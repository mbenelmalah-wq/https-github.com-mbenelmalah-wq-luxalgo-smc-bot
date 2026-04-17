from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import json, os

app = Flask(__name__)

# ─── Stockage des signaux TradingView reçus par webhook ───────────────────────
TV_STATE_FILE = os.path.join(os.path.dirname(__file__), 'tv_state.json')

def load_tv_state():
    if os.path.exists(TV_STATE_FILE):
        with open(TV_STATE_FILE) as f:
            return json.load(f)
    return {}

def save_tv_state(state):
    with open(TV_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

MARKETS = {
    'GC':  {'name': 'Or',           'role': 'Détecteur de stress monétaire', 'ticker': 'GC=F',    'risk_polarity': -1, 'color': '#FFD700'},
    'CL':  {'name': 'Pétrole',      'role': 'Moteur inflationniste',         'ticker': 'CL=F',    'risk_polarity': +1, 'color': '#FF6B35'},
    'HG':  {'name': 'Cuivre',       'role': 'Doctor Copper',                 'ticker': 'HG=F',    'risk_polarity': +1, 'color': '#CD7F32'},
    'ES':  {'name': 'S&P 500',      'role': 'Miroir de la liquidité',        'ticker': 'ES=F',    'risk_polarity': +1, 'color': '#4CAF50'},
    'ZN':  {'name': 'Obligations',  'role': "Chef d'orchestre",              'ticker': 'ZN=F',    'risk_polarity': -1, 'color': '#2196F3'},
    'BTC': {'name': 'Bitcoin',      'role': 'Capteur de liquidité extrême',  'ticker': 'BTC-USD', 'risk_polarity': +1, 'color': '#FF9800'},
    'JPY': {'name': 'Yen JPY',      'role': 'Détonateur caché',              'ticker': 'USDJPY=X','risk_polarity': +1, 'color': '#9C27B0'},
    'DX':  {'name': 'Dollar DX',    'role': 'Centre de gravité',             'ticker': 'DX-Y.NYB','risk_polarity': -1, 'color': '#00BCD4'},
}


def belkhayate_energy(closes, period=14):
    """
    Approximation Belkhayate Énergie.
    Barres grises (>0) = pression acheteuse.
    Barres bleues (<0) = pression vendeuse.
    """
    roc = closes.diff(period) / closes.shift(period) * 100
    return roc.ewm(span=5, adjust=False).mean()


def belkhayate_pivots(high, low, close):
    """Niveaux Belkhayate : RE, MI, FA, LA, SI (approximation pivot hebdo)."""
    pp = (high + low + close) / 3
    r = high - low
    return {
        'SI': round(float(pp + r * 0.618), 4),
        'LA': round(float(pp + r * 0.25), 4),
        'FA': round(float(pp), 4),
        'MI': round(float(pp - r * 0.25), 4),
        'RE': round(float(pp - r * 0.618), 4),
    }


def market_direction(closes, ema_fast=20, ema_slow=50):
    """BULLISH si close > EMA20 > EMA50, BEARISH si inverse, sinon NEUTRAL."""
    if len(closes) < ema_slow + 5:
        return 'NEUTRAL', 0

    e20 = closes.ewm(span=ema_fast).mean()
    e50 = closes.ewm(span=ema_slow).mean()

    c = float(closes.iloc[-1])
    e20v = float(e20.iloc[-1])
    e50v = float(e50.iloc[-1])

    if c > e20v > e50v:
        return 'BULLISH', 1
    if c < e20v < e50v:
        return 'BEARISH', -1
    return 'NEUTRAL', 0


def detect_divergence(closes, energy_series, window=10):
    """Détecte divergence haussière ou baissière sur les n dernières barres."""
    if len(closes) < window or len(energy_series) < window:
        return 'NONE'

    p = closes.tail(window)
    e = energy_series.tail(window)

    price_up  = float(p.iloc[-1]) > float(p.iloc[0])
    energy_up = float(e.iloc[-1]) > float(e.iloc[0])

    if price_up and not energy_up:
        return 'BEARISH_DIV'
    if not price_up and energy_up:
        return 'BULLISH_DIV'
    return 'NONE'


def fetch_all_markets():
    results = {}
    for sym, info in MARKETS.items():
        try:
            hist = yf.Ticker(info['ticker']).history(period='3mo', interval='1d')
            if hist.empty or len(hist) < 20:
                raise ValueError('Données insuffisantes')

            closes = hist['Close'].dropna()
            highs  = hist['High'].dropna()
            lows   = hist['Low'].dropna()

            direction, dir_score = market_direction(closes)
            energy = belkhayate_energy(closes)
            divergence = detect_divergence(closes, energy)
            pivots = belkhayate_pivots(
                float(highs.tail(5).max()),
                float(lows.tail(5).min()),
                float(closes.iloc[-1])
            )

            cur_price  = float(closes.iloc[-1])
            prev_price = float(closes.iloc[-2])
            chg_pct    = (cur_price - prev_price) / prev_price * 100

            e_history = energy.tail(15).replace([np.inf, -np.inf], 0).fillna(0)

            yf_result = {
                'error':          False,
                'state':          direction,
                'dir_score':      dir_score,
                'energy':         round(float(energy.iloc[-1]), 3),
                'energy_history': [round(x, 3) for x in e_history.tolist()],
                'price':          round(cur_price, 2),
                'change_pct':     round(chg_pct, 2),
                'divergence':     divergence,
                'pivots':         pivots,
                'name':           info['name'],
                'role':           info['role'],
                'color':          info['color'],
                'risk_polarity':  info['risk_polarity'],
                'source':         'yfinance',
            }

            # Écrase avec les vraies données TradingView si disponibles
            tv = load_tv_state().get(sym)
            if tv:
                yf_result['state']      = tv.get('direction', direction)
                yf_result['energy']     = tv.get('energy', yf_result['energy'])
                yf_result['divergence'] = tv.get('divergence', divergence)
                yf_result['pivot_pos']  = tv.get('pivot_pos', '')
                yf_result['tv_time']    = tv.get('timestamp', '')
                yf_result['source']     = 'tradingview'

            results[sym] = yf_result

        except Exception as exc:
            results[sym] = {
                'error': True, 'state': 'NEUTRAL', 'energy': 0,
                'price': 0, 'change_pct': 0, 'divergence': 'NONE',
                'name': info['name'], 'role': info['role'],
                'color': info['color'], 'risk_polarity': info['risk_polarity'],
                'error_msg': str(exc),
            }
    return results


def risk_regime(markets_data):
    score = 0
    aligned = 0
    for sym, d in markets_data.items():
        if d.get('error'):
            continue
        pol = MARKETS[sym]['risk_polarity']
        st  = d['state']
        if st == 'BULLISH':
            score += pol
            aligned += 1
        elif st == 'BEARISH':
            score -= pol
            aligned += 1

    if aligned == 0:
        return 'NO DATA', 0, 0

    if   score >= 5:  regime = 'RISK-ON FORT'
    elif score >= 2:  regime = 'RISK-ON'
    elif score >= -1: regime = 'NEUTRE / DISSONANCE'
    elif score >= -4: regime = 'RISK-OFF'
    else:             regime = 'RISK-OFF FORT'

    return regime, score, aligned


def market_leader(markets_data):
    """Marché avec la plus forte énergie absolue = leader."""
    return max(
        (s for s, d in markets_data.items() if not d.get('error')),
        key=lambda s: abs(markets_data[s].get('energy', 0)),
        default=None
    )


def active_patterns(markets_data, score):
    gc  = markets_data.get('GC', {})
    cl  = markets_data.get('CL', {})
    hg  = markets_data.get('HG', {})
    es  = markets_data.get('ES', {})
    zn  = markets_data.get('ZN', {})
    btc = markets_data.get('BTC', {})
    jpy = markets_data.get('JPY', {})
    dx  = markets_data.get('DX', {})

    patterns = []

    # Trade 1 — Décalage Risk-Off Classique
    if gc.get('state') == 'BULLISH' and zn.get('state') == 'BULLISH' and es.get('state') == 'BEARISH':
        patterns.append({'id': 1, 'name': 'Décalage Risk-Off Classique', 'market': 'ES',
                         'bias': 'SHORT', 'prob': 78, 'desc': 'Or + Obligations haussiers, ES baissier'})

    # Trade 2 — Rebond Risk-On BTC
    if btc.get('state') == 'BULLISH' and es.get('state') == 'BULLISH' and btc.get('energy', 0) > 0:
        patterns.append({'id': 2, 'name': 'Rebond Risk-On', 'market': 'BTC',
                         'bias': 'LONG', 'prob': 75, 'desc': 'BTC et ES alignés haussiers'})

    # Trade 3 — L'Or qui Mène la Danse
    if gc.get('state') == 'BULLISH' and dx.get('state') == 'BEARISH' and gc.get('energy', 0) > 0:
        patterns.append({'id': 3, 'name': "L'Or qui Mène la Danse", 'market': 'GC',
                         'bias': 'LONG', 'prob': 80, 'desc': 'Dollar faible + Or en impulsion'})

    # Trade 4 — Cassure du Cuivre
    if hg.get('divergence') in ('BEARISH_DIV', 'BULLISH_DIV'):
        bias = 'SHORT' if hg['divergence'] == 'BEARISH_DIV' else 'LONG'
        patterns.append({'id': 4, 'name': 'Cassure du Cuivre', 'market': 'HG',
                         'bias': bias, 'prob': 72, 'desc': f'Divergence {hg["divergence"]} sur HG'})

    # Trade 5 — Pétrole en Surchauffe
    if cl.get('divergence') == 'BEARISH_DIV' and cl.get('change_pct', 0) > 2:
        patterns.append({'id': 5, 'name': 'Pétrole en Surchauffe', 'market': 'CL',
                         'bias': 'SHORT', 'prob': 76, 'desc': 'CL surchauffé + divergence baissière Énergie'})

    # Trade 7 — Divergence Bonds vs Actions
    if zn.get('state') == 'BULLISH' and es.get('state') == 'BULLISH':
        patterns.append({'id': 7, 'name': 'Divergence Bonds vs Actions', 'market': 'ES',
                         'bias': 'WATCH', 'prob': 70, 'desc': 'ZN et ES haussiers simultanément = tension'})

    # Trade 8 — Bitcoin Capteur Précoce (ES SHORT)
    if btc.get('state') == 'BEARISH' and es.get('state') == 'BULLISH' and btc.get('divergence') == 'BEARISH_DIV':
        patterns.append({'id': 8, 'name': 'Bitcoin — Capteur Précoce', 'market': 'ES',
                         'bias': 'SHORT', 'prob': 83, 'desc': 'BTC casse avant ES — Risk-On qui se brise'})

    # Trade 9 — Dollar Faiblit
    if dx.get('state') == 'BEARISH' and gc.get('state') == 'BULLISH' and btc.get('state') == 'BULLISH':
        patterns.append({'id': 9, 'name': 'Dollar Faiblit', 'market': 'GC',
                         'bias': 'LONG', 'prob': 79, 'desc': 'DX baissier confirme Or et BTC haussiers'})

    # Trade 13 — Alignement Parfait
    risk_on_bull = sum(1 for s, d in markets_data.items()
                       if MARKETS[s]['risk_polarity'] == 1 and d.get('state') == 'BULLISH')
    if risk_on_bull >= 4:
        patterns.append({'id': 13, 'name': 'Alignement Parfait', 'market': 'ES',
                         'bias': 'LONG', 'prob': 88, 'desc': f'{risk_on_bull}/4 marchés Risk-On alignés haussiers'})

    # Trade 14 — Risk-Off Confirmé
    refuges_bull = sum(1 for s in ('GC', 'ZN', 'JPY') if markets_data.get(s, {}).get('state') == 'BULLISH')
    if refuges_bull >= 2 and es.get('state') == 'BEARISH':
        patterns.append({'id': 14, 'name': "Risk-Off Confirmé", 'market': 'GC',
                         'bias': 'LONG', 'prob': 82, 'desc': 'Refuges alignés + ES baissier = Risk-Off confirmé'})

    # Trade 15 — Convergence Totale
    valid = [s for s, d in markets_data.items() if not d.get('error')]
    all_aligned = all(
        (markets_data[s]['state'] == 'BULLISH' and MARKETS[s]['risk_polarity'] == 1) or
        (markets_data[s]['state'] == 'BEARISH' and MARKETS[s]['risk_polarity'] == -1)
        for s in valid
    )
    if all_aligned and len(valid) >= 6:
        bias = 'LONG' if score >= 0 else 'SHORT'
        patterns.append({'id': 15, 'name': 'Trade Sniper — Convergence Totale', 'market': 'ES',
                         'bias': bias, 'prob': 93, 'desc': 'Tous les marchés alignés — Signal maximum'})

    return sorted(patterns, key=lambda x: x['prob'], reverse=True)


@app.route('/')
def index():
    return render_template('index.html')


# ─── WEBHOOK TradingView ───────────────────────────────────────────────────────
@app.route('/webhook/belkhayate', methods=['POST'])
def webhook_belkhayate():
    """
    Reçoit les signaux des indicateurs Belkhayate depuis TradingView.

    Format JSON attendu :
    {
      "symbol":     "GC",
      "direction":  "BULLISH" | "BEARISH" | "NEUTRAL",
      "energy":     2.5,           (optionnel — valeur Belkhayate Énergie)
      "divergence": "BEARISH_DIV" | "BULLISH_DIV" | "NONE",  (optionnel)
      "pivot_pos":  "above_FA" | "below_MI" | "",  (optionnel)
      "price":      2350.5          (optionnel)
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON invalide'}), 400

    sym = str(data.get('symbol', '')).upper()
    if sym not in MARKETS:
        return jsonify({'error': f'Symbole inconnu: {sym}'}), 400

    direction = str(data.get('direction', 'NEUTRAL')).upper()
    if direction not in ('BULLISH', 'BEARISH', 'NEUTRAL'):
        return jsonify({'error': 'direction invalide'}), 400

    state = load_tv_state()
    state[sym] = {
        'direction':  direction,
        'energy':     float(data.get('energy', 0)),
        'divergence': data.get('divergence', 'NONE'),
        'pivot_pos':  data.get('pivot_pos', ''),
        'price':      float(data.get('price', 0)),
        'timestamp':  datetime.now().isoformat(),
    }
    save_tv_state(state)

    return jsonify({'ok': True, 'symbol': sym, 'direction': direction})


@app.route('/webhook/status', methods=['GET'])
def webhook_status():
    """Affiche les derniers signaux TradingView reçus."""
    return jsonify(load_tv_state())


@app.route('/api/data')
def api_data():
    markets = fetch_all_markets()
    regime, score, aligned = risk_regime(markets)
    leader = market_leader(markets)
    patterns = active_patterns(markets, score)

    return jsonify({
        'markets':  markets,
        'regime':   regime,
        'score':    score,
        'aligned':  aligned,
        'leader':   leader,
        'patterns': patterns,
        'market_definitions': {k: {'name': v['name'], 'role': v['role'], 'color': v['color']}
                               for k, v in MARKETS.items()},
        'timestamp': datetime.now().isoformat(),
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug)
