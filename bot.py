"""
LuxAlgo SMC Bot — Paper Trading Alpaca
Nouveau projet séparé — NE PAS MODIFIER le bot principal
"""

import os, json, time, logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import requests

# ── Config ────────────────────────────────────────────────────────────────────
with open("config.json") as f:
    config = json.load(f)

API_KEY    = config["alpaca"]["api_key"]
API_SECRET = config["alpaca"]["api_secret"]
BASE_URL   = config["alpaca"]["endpoint"]
TOKEN      = config["webhook_token"]

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type":        "application/json"
}

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

trades_history = []

def load_history_from_alpaca():
    """Charge l'historique depuis Alpaca au démarrage — persistant après redéploiement."""
    try:
        orders = api_call("GET", "/orders?status=closed&limit=100&direction=desc")
        if not isinstance(orders, list):
            return
        count = 0
        for o in orders:
            if o.get("filled_at") and o.get("side") == "buy":
                raw_sym = o.get("symbol", "").replace("/", "")
                if not raw_sym.endswith("USD"):
                    raw_sym = raw_sym + "USD"
                entry = float(o.get("filled_avg_price") or 0)
                qty   = float(o.get("filled_qty") or 0)
                trades_history.append({
                    "time":   o.get("filled_at", "")[:16].replace("T", " "),
                    "symbol": raw_sym,
                    "side":   "buy",
                    "source": "ALPACA_HISTORY",
                    "signal": "",
                    "entry":  entry,
                    "mise":   round(entry * qty, 2)
                })
                count += 1
        log.info(f"Historique chargé : {count} trades depuis Alpaca")
    except Exception as e:
        log.warning(f"load_history: {e}")

# ── Helpers API Alpaca ─────────────────────────────────────────────────────────
def api_call(method, path, payload=None):
    url = BASE_URL + path
    try:
        r = requests.request(method, url, headers=HEADERS, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_capital():
    acc = api_call("GET", "/account")
    return float(acc.get("cash", 100000))

def get_equity():
    acc = api_call("GET", "/account")
    return float(acc.get("equity", 100000))

def get_positions():
    return api_call("GET", "/positions")

def get_prix(symbol):
    cg_ids = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
              "AVAX": "avalanche-2", "XRP": "ripple", "ADA": "cardano"}
    coin = symbol[:3].upper()
    # Source 1 : CoinGecko (public, sans auth, toujours disponible)
    try:
        cg_id = cg_ids.get(coin)
        if cg_id:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                price = r.json().get(cg_id, {}).get("usd")
                if price:
                    log.info(f"  Prix {symbol} via CoinGecko: ${price}")
                    return float(price)
    except Exception as e:
        log.warning(f"CoinGecko échoué: {e}")
    # Source 2 : Binance public (fallback)
    try:
        pair = coin + "USDT"
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={pair}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            price = float(r.json()["price"])
            log.info(f"  Prix {symbol} via Binance: ${price}")
            return price
    except Exception as e:
        log.warning(f"Binance échoué: {e}")
    log.error(f"Impossible d'obtenir le prix pour {symbol}")
    return None

def half_kelly(capital, p, b):
    q = 1 - p
    f = (b * p - q) / b
    hk = max(0, f * 0.5)
    return min(hk * capital, capital * 0.20)  # max 20% par trade

def normalize_symbol(raw):
    mapping = {"BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD", "SOLUSD": "SOLUSD",
               "BTC/USD": "BTCUSD", "ETH/USD": "ETHUSD", "SOL/USD": "SOLUSD",
               "BTCUSDT": "BTCUSD", "ETHUSDT": "ETHUSD"}
    return mapping.get(raw.upper().replace("-", ""), raw.upper().replace("/", ""))

# ── Trailing SL ───────────────────────────────────────────────────────────────
class TrailSL:
    def __init__(self, symbol, entry, side, mm):
        self.symbol  = symbol
        self.entry   = entry
        self.side    = side
        self.tp      = entry * (1 + mm["take_profit_pct"])
        self.sl      = entry * (1 - mm["trailing_sl_pct"])
        self.palier  = entry * (1 + mm["profit_trigger_pct"])
        self.step    = mm["trail_step_pct"]
        self.be_lock = False
        self.time_in = datetime.now(timezone.utc)
        self.mise    = mm.get("mise", 0)

    def update(self, prix):
        """Retourne 'TP', 'SL', ou None"""
        if prix >= self.tp:
            return "TP"
        if prix <= self.sl:
            return "SL"
        if prix >= self.palier:
            if not self.be_lock:
                self.sl = self.entry
                self.be_lock = True
            else:
                new_sl = prix * (1 - self.step)
                if new_sl > self.sl:
                    self.sl = new_sl
            self.palier = prix * (1 + self.step)
        return None

active_trails  = {}
cooldown_last  = {}   # { symbol: datetime }
COOLDOWN_MIN   = 5    # minutes minimum entre deux trades sur le même symbole

# ── Session filter ─────────────────────────────────────────────────────────────
def is_asian_session():
    h = datetime.now(timezone.utc).hour
    return h >= 23 or h < 8

# ── Filtre EMA 114 Belkhayate (1min Binance) ───────────────────────────────────
_ema114_cache = {}  # symbol → {"ema": float, "slope": float, "ts": timestamp}
EMA114_TTL    = 60  # secondes

def get_ema114(symbol):
    """
    Calcule EMA 114 sur les 200 dernières bougies 1min Binance.
    Retourne (ema, slope) :
      slope > seuil  → tendance haussière → BUY autorisé
      slope < -seuil → tendance baissière → SELL autorisé
      |slope| < seuil → aplati → pas de trade
    """
    import time as _time
    cached = _ema114_cache.get(symbol)
    if cached and _time.time() - cached["ts"] < EMA114_TTL:
        return cached["ema"], cached["slope"]

    coin = symbol[:3].upper()
    pair = coin + "USDT"
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit=200"
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return None, None
        candles = r.json()
        closes  = [float(c[4]) for c in candles]

        # Calcul EMA 114
        period = 114
        k      = 2 / (period + 1)
        ema    = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)

        # Pente = différence EMA sur les 5 dernières bougies (normalisée)
        ema_prev = sum(closes[:period]) / period
        for price in closes[period:-5]:
            ema_prev = price * k + ema_prev * (1 - k)
        slope = (ema - ema_prev) / ema * 100  # en % du prix

        _ema114_cache[symbol] = {"ema": ema, "slope": slope, "ts": _time.time()}
        log.info(f"  EMA114 {symbol}: {ema:.2f} | slope={slope:.4f}%")
        return ema, slope

    except Exception as e:
        log.warning(f"EMA114 erreur {symbol}: {e}")
        return None, None

SLOPE_THRESHOLD = 0.003  # % — en dessous = aplati, pas de trade

# ── Webhook principal ─────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
  try:
    data = request.get_json(silent=True) or {}
    log.info(f"Signal reçu: {data}")

    # Auth
    if data.get("token") != TOKEN:
        return jsonify({"error": "token invalide"}), 401

    symbol = normalize_symbol(data.get("symbol", "BTCUSD"))
    side   = data.get("side", "").lower()
    source = data.get("source", "LUXALGO").upper()
    signal = data.get("signal", "").upper()  # BOS_BULL, BOS_BEAR, OB_BULL, OB_BEAR

    if side not in ("buy", "sell"):
        return jsonify({"error": "side invalide"}), 400

    # Filtre session asiatique
    if config["sessions"]["block_asian_session"] and is_asian_session():
        log.info("Session asiatique — signal ignoré")
        return jsonify({"status": "asian_session_blocked"})

    # Filtre EMA 114 Belkhayate (1min)
    ema114, slope = get_ema114(symbol)
    if ema114 is not None:
        if abs(slope) < SLOPE_THRESHOLD:
            log.info(f"  EMA114 aplatie ({slope:.4f}%) — signal ignoré")
            return jsonify({"status": "ema114_flat", "slope": slope})
        if side == "buy" and slope < 0:
            log.info(f"  EMA114 baissière ({slope:.4f}%) — BUY bloqué")
            return jsonify({"status": "ema114_bearish", "slope": slope})

    # SELL ignoré — sorties gérées uniquement par le Trailing SL
    if side == "sell":
        log.info(f"SELL ignoré ({source}/{signal}) — trailing SL gère les sorties")
        return jsonify({"status": "sell_ignored_trailing_sl_manages_exit"})

    # BUY — cooldown ?
    last = cooldown_last.get(symbol)
    if last:
        elapsed = (datetime.now(timezone.utc) - last.replace(tzinfo=timezone.utc) if last.tzinfo is None else datetime.now(timezone.utc) - last).total_seconds() / 60
        if elapsed < COOLDOWN_MIN:
            log.info(f"Cooldown {symbol} : {elapsed:.1f}/{COOLDOWN_MIN} min — ignoré")
            return jsonify({"status": "cooldown", "minutes_restantes": round(COOLDOWN_MIN - elapsed, 1)})

    # BUY — déjà en position ?
    if symbol in active_trails:
        log.info(f"Déjà en position {symbol}")
        return jsonify({"status": "already_in_position"})

    # Paramètres MM par symbole
    mm_key = f"money_management_{symbol}"
    mm = config.get(mm_key, config["money_management_default"])

    capital = get_capital()
    mise    = half_kelly(capital, mm["default_win_rate"], mm["default_win_loss_ratio"])
    mm["mise"] = mise

    log.info(f"  BUY {symbol} | source={source} | signal={signal} | mise=${mise:.0f}")

    # Prix actuel
    prix = get_prix(symbol)
    if not prix:
        return jsonify({"error": "prix indisponible"}), 500

    # Ordre BUY — Alpaca crypto exige format "BTC/USD"
    alpaca_sym = symbol[:3] + "/" + symbol[3:]  # BTCUSD → BTC/USD
    qty = round(mise / prix, 6)
    ordre = api_call("POST", "/orders", {
        "symbol": alpaca_sym, "qty": str(qty),
        "side": "buy", "type": "market", "time_in_force": "gtc"
    })

    if ordre.get("status") in ("rejected", "canceled") or "error" in str(ordre).lower():
        log.error(f"Erreur ordre: {ordre}")
        return jsonify({"error": str(ordre)}), 500

    log.info(f"  Ordre placé: {ordre.get('id')}")

    # Trailing SL
    active_trails[symbol] = TrailSL(symbol, prix, "buy", mm)
    trades_history.append({
        "time": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol, "side": "buy", "source": source,
        "signal": signal, "entry": prix, "mise": round(mise, 2)
    })

    return jsonify({"status": "ok", "symbol": symbol, "side": "buy", "prix": prix, "mise": round(mise, 2)})
  except Exception as e:
    log.error(f"Webhook erreur: {e}", exc_info=True)
    return jsonify({"error": str(e)}), 500

# ── Monitor trailing SL ────────────────────────────────────────────────────────
def monitor_loop():
    while True:
        time.sleep(5)
        to_close = []
        for sym, trail in list(active_trails.items()):
            prix = get_prix(sym)
            if not prix:
                continue
            result = trail.update(prix)
            if result:
                log.info(f"  SORTIE {sym} [{result}] @ {prix:.2f}")
                # Fermer position
                api_call("DELETE", f"/positions/{sym}")
                pnl = (prix - trail.entry) / trail.entry * trail.mise
                for t in reversed(trades_history):
                    if t["symbol"] == sym and "exit" not in t:
                        t["exit"] = prix
                        t["reason"] = result
                        t["pnl"] = round(pnl, 2)
                        break
                to_close.append(sym)
        for sym in to_close:
            active_trails.pop(sym, None)
            cooldown_last[sym] = datetime.now(timezone.utc)  # bloque nouveau BUY 5 min

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/recover")
def recover():
    """Reconstruit les trails manquants depuis les positions Alpaca ouvertes."""
    try:
        pos_list = api_call("GET", "/positions")
        if not isinstance(pos_list, list):
            return jsonify({"error": "impossible de lire les positions"}), 500
        recovered = []
        for p in pos_list:
            sym   = p["symbol"].replace("/", "")
            if sym in active_trails:
                continue
            entry = float(p["avg_entry_price"])
            mm_key = f"money_management_{sym}"
            mm = config.get(mm_key, config["money_management_default"])
            mm["mise"] = abs(float(p["market_value"]))
            active_trails[sym] = TrailSL(sym, entry, "buy", mm)
            recovered.append({"symbol": sym, "entry": entry,
                               "sl": round(active_trails[sym].sl, 2),
                               "tp": round(active_trails[sym].tp, 2)})
            log.info(f"  RECOVER {sym} entry={entry:.2f} SL={active_trails[sym].sl:.2f} TP={active_trails[sym].tp:.2f}")
        return jsonify({"status": "ok", "recovered": recovered})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/status")
def status():
    return jsonify({
        "status":    "running",
        "equity":    get_equity(),
        "capital":   get_capital(),
        "positions": list(active_trails.keys()),
        "trails":    {s: {"entry": t.entry, "sl": round(t.sl, 2), "tp": round(t.tp, 2)}
                      for s, t in active_trails.items()},
        "time":      datetime.now(timezone.utc).isoformat()
    })

@app.route("/history")
def history():
    return jsonify(trades_history)

@app.route("/")
@app.route("/dashboard")
def dashboard():
    eq      = get_equity()
    cap     = get_capital()
    trades  = trades_history
    wins    = [t for t in trades if t.get("pnl", 0) > 0]
    losses  = [t for t in trades if t.get("pnl", 0) < 0]
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    wr      = round(len(wins)/len(trades)*100, 1) if trades else 0
    pnl_color = "#00e676" if total_pnl >= 0 else "#ff5252"
    eq_color  = "#00e676" if eq >= 100000 else "#ff5252"

    positions_html = ""
    for sym, t in active_trails.items():
        prix_now = get_prix(sym) or t.entry
        pnl_live = round((prix_now - t.entry) / t.entry * 100, 3)
        pc = "#00e676" if pnl_live >= 0 else "#ff5252"
        positions_html += f"""
        <div class="card pos-card">
            <div class="pos-sym">{sym}</div>
            <div class="pos-grid">
                <span>Entrée</span><span>${t.entry:,.2f}</span>
                <span>Prix actuel</span><span>${prix_now:,.2f}</span>
                <span>SL</span><span style="color:#ff5252">${t.sl:,.2f}</span>
                <span>TP</span><span style="color:#00e676">${t.tp:,.2f}</span>
                <span>P&L live</span><span style="color:{pc}">{'+' if pnl_live>=0 else ''}{pnl_live}%</span>
            </div>
        </div>"""

    trades_html = ""
    for t in reversed(trades[-20:]):
        pnl = t.get("pnl")
        if pnl is not None:
            pc = "#00e676" if pnl >= 0 else "#ff5252"
            pnl_str = f'<span style="color:{pc}">{"+$" if pnl>=0 else "-$"}{abs(pnl):.2f}</span>'
        else:
            pnl_str = '<span style="color:#aaa">En cours</span>'
        reason = t.get("reason", "...")
        sig    = t.get("signal", "")
        trades_html += f"""
        <tr>
            <td>{t['time'][11:16]}</td>
            <td>{t['symbol']}</td>
            <td style="color:{'#00e676' if t['side']=='buy' else '#ff5252'}">{t['side'].upper()}</td>
            <td><small>{sig}</small></td>
            <td>${t['entry']:,.1f}</td>
            <td>{pnl_str}</td>
            <td><small>{reason}</small></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LuxAlgo SMC Bot</title>
<meta http-equiv="refresh" content="15">
<style>
  * {{ box-sizing:border-box; margin:0; padding:0 }}
  body {{ background:#0d1117; color:#e6edf3; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; padding:12px }}
  h1 {{ font-size:1.2rem; color:#7c3aed; margin-bottom:14px; text-align:center }}
  .badge {{ display:inline-block; background:#7c3aed22; color:#a78bfa; border:1px solid #7c3aed55; border-radius:20px; padding:2px 10px; font-size:.75rem; margin-left:8px }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:14px }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:14px }}
  .card .label {{ color:#8b949e; font-size:.75rem; margin-bottom:4px }}
  .card .value {{ font-size:1.3rem; font-weight:700 }}
  .pos-card {{ margin-bottom:10px }}
  .pos-sym {{ font-size:1rem; font-weight:700; color:#a78bfa; margin-bottom:8px }}
  .pos-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:4px 12px; font-size:.85rem }}
  .pos-grid span:nth-child(odd) {{ color:#8b949e }}
  h2 {{ font-size:.95rem; color:#8b949e; margin:14px 0 8px; border-bottom:1px solid #21262d; padding-bottom:6px }}
  table {{ width:100%; border-collapse:collapse; font-size:.8rem }}
  th {{ color:#8b949e; text-align:left; padding:6px 4px; border-bottom:1px solid #21262d }}
  td {{ padding:6px 4px; border-bottom:1px solid #21262d11 }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; background:#00e676; margin-right:6px; animation:pulse 2s infinite }}
  @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
</style>
</head>
<body>
<h1><span class="dot"></span>LuxAlgo SMC Bot <span class="badge">PAPER</span></h1>

<div class="grid">
  <div class="card">
    <div class="label">Equity</div>
    <div class="value" style="color:{eq_color}">${eq:,.0f}</div>
  </div>
  <div class="card">
    <div class="label">Capital libre</div>
    <div class="value">${cap:,.0f}</div>
  </div>
  <div class="card">
    <div class="label">P&L total</div>
    <div class="value" style="color:{pnl_color}">{'+' if total_pnl>=0 else ''}${total_pnl:,.2f}</div>
  </div>
  <div class="card">
    <div class="label">Win Rate</div>
    <div class="value" style="color:{'#00e676' if wr>=50 else '#ff9800'}">{wr}%</div>
  </div>
  <div class="card">
    <div class="label">Trades</div>
    <div class="value">{len(trades)}</div>
  </div>
  <div class="card">
    <div class="label">Positions</div>
    <div class="value" style="color:#a78bfa">{len(active_trails)}</div>
  </div>
</div>

{'<h2>Positions ouvertes</h2>' + positions_html if active_trails else '<div class="card" style="text-align:center;color:#8b949e;padding:20px">Aucune position ouverte</div>'}

<h2>Derniers trades ({len(trades)})</h2>
<div style="overflow-x:auto">
<table>
  <tr><th>Heure</th><th>Sym</th><th>Side</th><th>Signal</th><th>Entrée</th><th>P&L</th><th>Raison</th></tr>
  {trades_html if trades_html else '<tr><td colspan="7" style="text-align:center;color:#8b949e;padding:20px">En attente des premiers signaux LuxAlgo...</td></tr>'}
</table>
</div>
<p style="text-align:center;color:#8b949e;font-size:.7rem;margin-top:12px">Refresh auto 15s</p>
</body></html>"""

# ── Start ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    load_history_from_alpaca()
    import threading
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
