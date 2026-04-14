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
    sym = symbol.replace("USD", "/USD")
    url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/bars"
    r = requests.get(url,
        headers={"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET},
        params={"symbols": sym}, timeout=5)
    data = r.json()
    bar = data.get("bars", {}).get(sym)
    return float(bar["c"]) if bar else None

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

active_trails = {}

# ── Session filter ─────────────────────────────────────────────────────────────
def is_asian_session():
    h = datetime.now(timezone.utc).hour
    return h >= 23 or h < 8

# ── Webhook principal ─────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
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

    # Crypto : pas de short
    if side == "sell":
        positions = get_positions()
        has_pos = any(p.get("symbol") == symbol for p in (positions if isinstance(positions, list) else []))
        if not has_pos:
            log.info(f"SELL ignoré — pas de position {symbol}")
            return jsonify({"status": "no_position_to_sell"})

    # Déjà en position ?
    if symbol in active_trails and side == "buy":
        log.info(f"Déjà en position {symbol}")
        return jsonify({"status": "already_in_position"})

    # Paramètres MM par symbole
    mm_key = f"money_management_{symbol}"
    mm = config.get(mm_key, config["money_management_default"])

    capital = get_capital()
    mise    = half_kelly(capital, mm["default_win_rate"], mm["default_win_loss_ratio"])
    mm["mise"] = mise

    log.info(f"  {side.upper()} {symbol} | source={source} | signal={signal} | mise=${mise:.0f}")

    # Prix actuel
    prix = get_prix(symbol)
    if not prix:
        return jsonify({"error": "prix indisponible"}), 500

    # Ordre
    qty = round(mise / prix, 6)
    ordre = api_call("POST", "/orders", {
        "symbol": symbol, "qty": str(qty),
        "side": side, "type": "market", "time_in_force": "gtc"
    })

    if "error" in str(ordre).lower():
        log.error(f"Erreur ordre: {ordre}")
        return jsonify({"error": str(ordre)}), 500

    log.info(f"  Ordre placé: {ordre.get('id')}")

    # Trailing SL
    active_trails[symbol] = TrailSL(symbol, prix, side, mm)
    trades_history.append({
        "time": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol, "side": side, "source": source,
        "signal": signal, "entry": prix, "mise": round(mise, 2)
    })

    return jsonify({"status": "ok", "symbol": symbol, "side": side, "prix": prix, "mise": round(mise, 2)})

# ── Monitor trailing SL ────────────────────────────────────────────────────────
def monitor_loop():
    while True:
        time.sleep(10)
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

# ── Routes ─────────────────────────────────────────────────────────────────────
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
def home():
    eq = get_equity()
    return f"<h2>LuxAlgo SMC Bot</h2><p>Equity: ${eq:,.2f}</p><p>Trades: {len(trades_history)}</p>"

# ── Start ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import threading
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
