from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
import json, os, time, sys

_TZ = ZoneInfo(os.environ.get('TZ', 'Africa/Casablanca'))
def now_local():
    return datetime.now(_TZ)

# Import stratégie personnalisée
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from trading.strategy import get_setup, confirm_entry
except ImportError:
    try:
        from trading.tradingstrategy import get_setup
        def confirm_entry(setup, convergence_score, current_price, resistance_level):
            return {'contexte': False, 'signal': False, 'timing': False, 'can_trade': False, 'verdict': 'Module manquant'}
    except ImportError:
        def get_setup(scores): return 'NO_TRADE'
        def confirm_entry(setup, convergence_score, current_price, resistance_level):
            return {'can_trade': False, 'verdict': 'Module manquant'}

# Charge .env si présent (développement local)
def _load_env_file():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(env_path):
        return
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env_file()

app = Flask(__name__)

# ─── Cache analytics (évite de re-télécharger toutes les 5s) ─────────────────
_analytics_cache = {'data': None, 'ts': 0}
ANALYTICS_TTL = 600  # 10 minutes

# Répertoire persistant : /data sur Railway (Volume), dossier local sinon
_DATA_DIR = os.environ.get('DATA_DIR',
            os.path.join(os.path.dirname(os.path.abspath(__file__))))
os.makedirs(_DATA_DIR, exist_ok=True)

TV_STATE_FILE        = os.path.join(_DATA_DIR, 'tv_state.json')
VISION_HISTORY_FILE  = os.path.join(_DATA_DIR, 'vision_history.json')
SEASONAX_FILE        = os.path.join(_DATA_DIR, 'seasonax_data.json')
TRADE_JOURNAL_FILE   = os.path.join(_DATA_DIR, 'trade_journal.json')

def load_tv_state():
    if os.path.exists(TV_STATE_FILE):
        with open(TV_STATE_FILE) as f:
            return json.load(f)
    return {}

def save_tv_state(state):
    with open(TV_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


# ─── Vision History ───────────────────────────────────────────────────────────

def load_vision_history():
    if os.path.exists(VISION_HISTORY_FILE):
        try:
            with open(VISION_HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_vision_history(entry):
    history = load_vision_history()
    history.insert(0, entry)
    history = history[:50]
    with open(VISION_HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)


# ─── Telegram alerts ─────────────────────────────────────────────────────────

_last_alert_ts = 0

def send_telegram_alert(message):
    token   = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return False
    try:
        import urllib.request
        url  = f'https://api.telegram.org/bot{token}/sendMessage'
        data = json.dumps({'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'}).encode()
        req  = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


# ─── Seasonax ─────────────────────────────────────────────────────────────────

def load_seasonax_data():
    if os.path.exists(SEASONAX_FILE):
        try:
            with open(SEASONAX_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_seasonax_data(data):
    with open(SEASONAX_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ─── Trade Journal ────────────────────────────────────────────────────────────

def load_trade_journal():
    if os.path.exists(TRADE_JOURNAL_FILE):
        try:
            with open(TRADE_JOURNAL_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_trade_journal(trades):
    with open(TRADE_JOURNAL_FILE, 'w') as f:
        json.dump(trades, f, indent=2)

MARKETS = {
    'GC':  {'name': 'Or',           'role': 'Détecteur de stress monétaire', 'ticker': 'GC=F',     'risk_polarity': -1, 'color': '#FFD700', 'reaction_order': 3},
    'CL':  {'name': 'Pétrole',      'role': 'Moteur inflationniste',         'ticker': 'CL=F',     'risk_polarity': +1, 'color': '#FF6B35', 'reaction_order': 4},
    'HG':  {'name': 'Cuivre',       'role': 'Doctor Copper',                 'ticker': 'HG=F',     'risk_polarity': +1, 'color': '#CD7F32', 'reaction_order': 4},
    'ES':  {'name': 'S&P 500',      'role': 'Miroir de la liquidité',        'ticker': 'ES=F',     'risk_polarity': +1, 'color': '#4CAF50', 'reaction_order': 5},
    'ZN':  {'name': 'Obligations',  'role': "Chef d'orchestre",              'ticker': 'ZN=F',     'risk_polarity': -1, 'color': '#2196F3', 'reaction_order': 2},
    'BTC': {'name': 'Bitcoin',      'role': 'Capteur de liquidité extrême',  'ticker': 'BTC-USD',  'risk_polarity': +1, 'color': '#FF9800', 'reaction_order': 5},
    'JPY': {'name': 'Yen (USD/JPY)','role': 'Détonateur caché',              'ticker': 'USDJPY=X', 'risk_polarity': +1, 'color': '#9C27B0', 'reaction_order': 1},
    'DX':  {'name': 'Dollar DX',    'role': 'Centre de gravité',             'ticker': 'DX-Y.NYB', 'risk_polarity': -1, 'color': '#00BCD4', 'reaction_order': 0},
}

# Chronologie institutionnelle de propagation du signal (Ch.14)
CHRONOLOGY = ['JPY', 'ZN', 'GC', 'CL', 'HG', 'ES']


def belkhayate_energy(closes, period=14):
    """Barres grises (>0) = pression acheteuse. Barres bleues (<0) = pression vendeuse."""
    roc = closes.diff(period) / closes.shift(period) * 100
    return roc.ewm(span=5, adjust=False).mean()


def belkhayate_pivots(high, low, close):
    """
    Gamme musicale Belkhayate — 7 niveaux (Ch.12) :
    DO (support extrême) → RÉ → MI → FA (pivot) → SOL → LA → SI (résistance extrême)
    """
    pp = (high + low + close) / 3
    r = high - low
    return {
        'SI':  round(float(pp + r * 1.0),   4),
        'LA':  round(float(pp + r * 0.618), 4),
        'SOL': round(float(pp + r * 0.25),  4),
        'FA':  round(float(pp),              4),
        'MI':  round(float(pp - r * 0.25),  4),
        'RE':  round(float(pp - r * 0.618), 4),
        'DO':  round(float(pp - r * 1.0),   4),
    }


def compute_market_score(direction, energy_val):
    """Score composite -3 à +3 par marché (Ch.18 Belkhayate IA)."""
    dir_score = {'BULLISH': 2, 'NEUTRAL': 0, 'BEARISH': -2}.get(direction, 0)
    energy_score = 1 if energy_val > 0.8 else (-1 if energy_val < -0.8 else 0)
    return max(-3, min(3, dir_score + energy_score))


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

            e_val = float(energy.iloc[-1])
            e_history = energy.tail(15).replace([np.inf, -np.inf], 0).fillna(0)
            mkt_score = compute_market_score(direction, e_val)

            # Position prix par rapport aux pivots (zone discount / premium)
            fa_level = pivots.get('FA', cur_price)
            if cur_price > pivots.get('SOL', fa_level):
                pivot_zone = 'PREMIUM_FORT'
            elif cur_price > fa_level:
                pivot_zone = 'PREMIUM'
            elif cur_price > pivots.get('MI', fa_level):
                pivot_zone = 'DISCOUNT'
            else:
                pivot_zone = 'DISCOUNT_FORT'

            yf_result = {
                'error':          False,
                'state':          direction,
                'dir_score':      dir_score,
                'energy':         round(e_val, 3),
                'energy_history': [round(x, 3) for x in e_history.tolist()],
                'market_score':   mkt_score,
                'price':          round(cur_price, 2),
                'change_pct':     round(chg_pct, 2),
                'divergence':     divergence,
                'pivots':         pivots,
                'pivot_zone':     pivot_zone,
                'name':           info['name'],
                'role':           info['role'],
                'color':          info['color'],
                'risk_polarity':  info['risk_polarity'],
                'reaction_order': info['reaction_order'],
                'source':         'yfinance',
            }

            tv = load_tv_state().get(sym)
            if tv:
                yf_result['state']        = tv.get('direction', direction)
                yf_result['energy']       = tv.get('energy', e_val)
                yf_result['divergence']   = tv.get('divergence', divergence)
                yf_result['pivot_pos']    = tv.get('pivot_pos', '')
                yf_result['tv_time']      = tv.get('timestamp', '')
                yf_result['source']       = tv.get('source', 'tradingview')
                yf_result['confidence']   = tv.get('confidence')
                yf_result['market_score'] = compute_market_score(
                    yf_result['state'], yf_result['energy'])

            results[sym] = yf_result

        except Exception as exc:
            results[sym] = {
                'error': True, 'state': 'NEUTRAL', 'energy': 0, 'market_score': 0,
                'price': 0, 'change_pct': 0, 'divergence': 'NONE', 'pivots': {},
                'pivot_zone': 'NEUTRAL',
                'name': info['name'], 'role': info['role'],
                'color': info['color'], 'risk_polarity': info['risk_polarity'],
                'reaction_order': info['reaction_order'],
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
    return max(
        (s for s, d in markets_data.items() if not d.get('error')),
        key=lambda s: abs(markets_data[s].get('energy', 0)),
        default=None
    )


def chronology_status(markets_data):
    """
    Chronologie institutionnelle de propagation (Ch.14) :
    Yen → Obligations → Or → Pétrole/Cuivre → Actions
    Retourne le statut de chaque marché dans la chaîne.
    """
    chain = []
    for sym in CHRONOLOGY:
        d = markets_data.get(sym, {})
        if d.get('error'):
            status = 'unknown'
        elif sym == 'JPY':
            # USD/JPY BEARISH = yen qui monte = risk-off = "activé"
            status = 'active' if d.get('state') == 'BEARISH' else 'waiting'
        elif sym in ('ZN', 'GC'):
            status = 'active' if d.get('state') == 'BULLISH' else 'waiting'
        elif sym in ('CL', 'HG'):
            status = 'active' if d.get('state') == 'BEARISH' else 'waiting'
        elif sym == 'ES':
            # ES BEARISH = le signal a finalement atteint les actions
            status = 'active' if d.get('state') == 'BEARISH' else 'lagging'
        else:
            status = 'waiting'
        chain.append({'sym': sym, 'name': MARKETS[sym]['name'], 'status': status, 'color': MARKETS[sym]['color']})
    return chain


def detect_laggards(markets_data, regime):
    """
    Détecte les marchés en retard (Ch.18 — Logique de détection du retard).
    Si 5+ marchés signalent Risk-OFF mais qu'un marché ne réagit pas encore = en retard.
    C'est lui qu'on trade (Étape 2 du Protocole Sniper: trader le suiveur, pas le leader).
    """
    laggards = []
    is_risk_off = 'RISK-OFF' in regime
    is_risk_on  = 'RISK-ON'  in regime and 'RISK-OFF' not in regime

    for sym, d in markets_data.items():
        if d.get('error'):
            continue
        pol   = MARKETS[sym]['risk_polarity']
        state = d.get('state', 'NEUTRAL')
        if is_risk_off:
            if pol == +1 and state == 'BULLISH':
                laggards.append(sym)
            elif pol == -1 and state == 'BEARISH':
                laggards.append(sym)
        elif is_risk_on:
            if pol == +1 and state == 'BEARISH':
                laggards.append(sym)
            elif pol == -1 and state == 'BULLISH':
                laggards.append(sym)

    laggards.sort(key=lambda s: MARKETS[s]['reaction_order'], reverse=True)
    return laggards


def morning_questions(markets_data, leader, laggards, regime):
    """
    Les 3 Questions du Matin — Protocole Sniper Étape 1 (Ch.11 + Ch.14).
    Ces 3 questions prennent 2 minutes et définissent toute la journée.
    """
    dx  = markets_data.get('DX', {})
    zn  = markets_data.get('ZN', {})
    jpy = markets_data.get('JPY', {})

    dx_state  = dx.get('state', 'NEUTRAL')
    dx_chg    = dx.get('change_pct', 0)
    if dx_state == 'BULLISH':
        q1 = f'DXY FORT ({dx_chg:+.2f}%) — Pression Risk-OFF. Matières premières et actions sous tension.'
    elif dx_state == 'BEARISH':
        q1 = f'DXY FAIBLE ({dx_chg:+.2f}%) — Favorable Risk-ON. Or, BTC et actions avantagés.'
    else:
        q1 = f'DXY NEUTRE ({dx_chg:+.2f}%) — Pas de signal directionnel dominant. Attendre.'

    zn_state  = zn.get('state', 'NEUTRAL')
    yen_fort  = jpy.get('state', 'NEUTRAL') == 'BEARISH'
    zn_bull   = zn_state == 'BULLISH'
    refuges   = int(zn_bull) + int(yen_fort)
    if refuges == 2:
        q2 = 'ZN HAUSSIER + Yen FORT (USD/JPY baissier) — REFUGES ACTIFS. Risk-OFF naissant. Débouclement carry trade probable.'
    elif refuges == 1:
        signal = 'ZN HAUSSIER' if zn_bull else 'Yen FORT'
        q2 = f'{signal} — Signal mixte. Un seul refuge actif. Vigilance, pas encore confirmé.'
    else:
        q2 = f'ZN {zn_state} + USD/JPY {jpy.get("state","NEUTRAL")} — Pas de pression refuges. Contexte Risk-ON favorable.'

    if laggards:
        target = laggards[0]
        tname  = MARKETS[target]['name']
        if 'RISK-OFF' in regime:
            q3 = f'EN RETARD : {target} ({tname}) — Vente probable. Attendre setup Belkhayate 15min (pivot + énergie + direction).'
        else:
            q3 = f'EN RETARD : {target} ({tname}) — Achat probable. Attendre setup Belkhayate 15min (pivot + énergie + direction).'
    elif leader:
        lname = MARKETS.get(leader, {}).get('name', leader)
        q3 = f'Tous alignés avec le régime — Leader: {leader} ({lname}). Attendre Trade Sniper #15.'
    else:
        q3 = "Aucun marché clairement en retard — Patience. Aucun trade aujourd'hui."

    return [
        {'n': 1, 'q': 'Que fait le DXY ?',            'a': q1},
        {'n': 2, 'q': 'Que font ZN et JPY ?',          'a': q2},
        {'n': 3, 'q': 'Qui est en retard (à trader) ?', 'a': q3},
    ]


# R/R ratios officiels du livre Belkhayate (Ch.15-IV)
TRADE_RR = {
    1: '1:3.0', 2: '1:3.9', 3: '1:3.8', 4: '1:4.3', 5: '1:3.5',
    6: '1:3.3', 7: '1:3.0', 8: '1:3.0', 9: '1:4.0', 10: '1:3.9',
    11: '1:3.3', 12: '1:4.3', 13: '1:3.8', 14: '1:4.7', 15: '1:4.7',
}


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

    # Trade 1 (78%) — Décalage Risk-Off Classique
    # GC + ZN haussiers, ES pas encore réagi = short ES avant la cascade
    if gc.get('state') == 'BULLISH' and zn.get('state') == 'BULLISH' and \
       es.get('state') in ('BULLISH', 'NEUTRAL'):
        patterns.append({'id': 1, 'name': 'Décalage Risk-Off Classique', 'market': 'ES',
                         'bias': 'SHORT', 'prob': 78,
                         'desc': 'GC+ZN haussiers — ES en retard sur Risk-Off naissant (4-8h de fenêtre)'})

    # Trade 2 (74%) — Rebond sur Pivot RÉ en Risk-On
    # Dollar faiblit, liquidité revient, BTC premier à réagir sur support
    if dx.get('state') == 'BEARISH' and btc.get('state') == 'BULLISH' and \
       btc.get('energy', 0) > 0:
        patterns.append({'id': 2, 'name': 'Rebond sur Pivot RÉ en Risk-On', 'market': 'BTC',
                         'bias': 'LONG', 'prob': 74,
                         'desc': 'DX baissier + BTC capteur liquidité actif — rebond pivot support'})

    # Trade 3 (82%) — L'Or qui Mène la Danse
    # Or monte EN MEME TEMPS que le dollar = stress systémique rare et puissant
    if gc.get('state') == 'BULLISH' and dx.get('state') == 'BULLISH' and \
       gc.get('energy', 0) > 0:
        patterns.append({'id': 3, 'name': "L'Or qui Mène la Danse", 'market': 'GC',
                         'bias': 'LONG', 'prob': 82,
                         'desc': 'Or ET Dollar haussiers simultanément — stress systémique, signal rare et puissant'})

    # Trade 4 (76%) — La Cassure du Cuivre — Doctor Copper Parle
    # HG casse à la baisse, ES tient encore = divergence fondamentale précoce
    if hg.get('state') == 'BEARISH' and es.get('state') in ('BULLISH', 'NEUTRAL'):
        patterns.append({'id': 4, 'name': 'La Cassure du Cuivre — Doctor Copper Parle', 'market': 'HG',
                         'bias': 'SHORT', 'prob': 76,
                         'desc': 'HG baissier, ES encore haussier — divergence économique fondamentale'})

    # Trade 5 (80%) — Le Pétrole en Surchauffe
    # CL surchauffé (+2%+), divergence baissière énergie, DX stable/haussier = retournement imminent
    if cl.get('divergence') == 'BEARISH_DIV' and cl.get('change_pct', 0) > 2 and \
       dx.get('state') in ('BULLISH', 'NEUTRAL'):
        patterns.append({'id': 5, 'name': 'Le Pétrole en Surchauffe', 'market': 'CL',
                         'bias': 'SHORT', 'prob': 80,
                         'desc': 'CL surchauffé + divergence Énergie baissière + DX stable = recul 4-6$ probable'})

    # Trade 6 (85%) — L'Écart Yen-Carry Trade
    # USD/JPY commence à descendre (yen se renforce) = débouclement carry trade violent
    if jpy.get('state') == 'BEARISH' and jpy.get('energy', 0) < 0 and \
       es.get('state') in ('BULLISH', 'NEUTRAL'):
        patterns.append({'id': 6, 'name': "L'Écart Yen-Carry Trade", 'market': 'JPY',
                         'bias': 'SHORT', 'prob': 85,
                         'desc': 'USD/JPY baissier + énergie vendeuse — débouclement carry trade, train en marche'})

    # Trade 7 (79%) — La Divergence Bonds vs Actions
    # ZN monte fortement (capital fuit vers sécurité), ES tient encore par inertie
    if zn.get('state') == 'BULLISH' and es.get('state') == 'BULLISH' and \
       zn.get('energy', 0) > 0 and gc.get('state') != 'BULLISH':
        patterns.append({'id': 7, 'name': 'La Divergence Bonds vs Actions', 'market': 'ES',
                         'bias': 'SHORT', 'prob': 79,
                         'desc': 'ZN monte (capital vers sécurité) + ES haussier par inertie — ES va corriger'})

    # Trade 8 (83%) — Le Bitcoin — Capteur Précoce
    # BTC casse avant ES = capital spéculatif se retire = ES va suivre dans 90-180 min
    if btc.get('state') == 'BEARISH' and es.get('state') in ('BULLISH', 'NEUTRAL') and \
       btc.get('divergence') == 'BEARISH_DIV':
        patterns.append({'id': 8, 'name': 'Le Bitcoin — Capteur Précoce', 'market': 'ES',
                         'bias': 'SHORT', 'prob': 83,
                         'desc': 'BTC casse avant ES — capital spéculatif se retire, ES suit dans 90-180 min'})

    # Trade 9 (77%) — Le Dollar Faiblit — Or et Bitcoin Ensemble
    # DX baisse + GC + BTC = tous captent la liquidité qui revient = expansion confirmée
    if dx.get('state') == 'BEARISH' and gc.get('state') == 'BULLISH' and \
       btc.get('state') == 'BULLISH':
        patterns.append({'id': 9, 'name': 'Le Dollar Faiblit — Or et Bitcoin Ensemble', 'market': 'GC',
                         'bias': 'LONG', 'prob': 77,
                         'desc': 'DX baissier + GC + BTC haussiers — expansion de liquidité, or en retard sur BTC'})

    # Trade 10 (75%) — L'Effet Ressort sur la Bande
    # ES en tendance haussière qui pullback (énergie divergence haussière) dans contexte Risk-On
    if es.get('state') == 'BULLISH' and es.get('divergence') == 'BULLISH_DIV' and \
       es.get('change_pct', 0) < 0 and score >= 1:
        patterns.append({'id': 10, 'name': "L'Effet Ressort sur la Bande", 'market': 'ES',
                         'bias': 'LONG', 'prob': 75,
                         'desc': 'ES pullback sur bande Direction verte — rechargement avant reprise Risk-On'})

    # Trade 11 (81%) — La Cassure Verticale — Énergie Massive
    # CL cassure haussière avec énergie explosive = choc géopolitique, move de 4-8%
    if cl.get('state') == 'BULLISH' and cl.get('energy', 0) > 1.5:
        patterns.append({'id': 11, 'name': 'La Cassure Verticale — Énergie Massive', 'market': 'CL',
                         'bias': 'LONG', 'prob': 81,
                         'desc': 'CL cassure haussière avec énergie acheteuse explosive — choc géopolitique probable'})

    # Trade 12 (79%) — Le Faux Breakout des Actions
    # ES casse à la hausse MAIS DX monte = incohérence structurelle = piège
    if es.get('state') == 'BULLISH' and es.get('change_pct', 0) > 0.5 and \
       dx.get('state') == 'BULLISH' and dx.get('energy', 0) > 0:
        patterns.append({'id': 12, 'name': 'Le Faux Breakout des Actions', 'market': 'ES',
                         'bias': 'SHORT', 'prob': 79,
                         'desc': 'ES breakout apparent MAIS DX haussier — incohérence structurelle = piège classique'})

    # Trade 13 (88%) — L'Alignement Parfait — 5 Marchés Synchronisés
    # 5+ marchés sur 8 alignés dans le sens Risk-On
    risk_on_aligned = sum(1 for s, d in markets_data.items() if not d.get('error') and (
        (MARKETS[s]['risk_polarity'] == 1  and d.get('state') == 'BULLISH') or
        (MARKETS[s]['risk_polarity'] == -1 and d.get('state') == 'BEARISH')
    ))
    if risk_on_aligned >= 5:
        patterns.append({'id': 13, 'name': "L'Alignement Parfait — 5 Marchés Synchronisés", 'market': 'ES',
                         'bias': 'LONG', 'prob': 88,
                         'desc': f'{risk_on_aligned}/8 marchés alignés Risk-On — configuration rare, taille de position majorée'})

    # Trade 14 (84%) — L'Or qui Casse — Risk-Off Confirmé
    # Yen monte (1er), obligations suivent, or encore décalé = acheter l'or dans le retard
    if jpy.get('state') == 'BEARISH' and zn.get('state') == 'BULLISH' and \
       gc.get('state') in ('NEUTRAL', 'BULLISH') and es.get('state') == 'BEARISH':
        patterns.append({'id': 14, 'name': "L'Or qui Casse — Risk-Off Confirmé", 'market': 'GC',
                         'bias': 'LONG', 'prob': 84,
                         'desc': 'JPY+ZN Risk-Off confirmé — or décalé = acheter le retard (Yen→Bonds→Or)'})

    # Trade 15 (90%) — Le Trade Sniper — Convergence Totale
    # DX haussier ET USD/JPY baissier simultanément = stress systémique majeur, ES dernier à réagir
    if dx.get('state') == 'BULLISH' and jpy.get('state') == 'BEARISH' and \
       gc.get('state') == 'BULLISH' and zn.get('state') == 'BULLISH' and \
       es.get('state') in ('BULLISH', 'NEUTRAL'):
        patterns.append({'id': 15, 'name': 'Le Trade Sniper — Convergence Totale', 'market': 'ES',
                         'bias': 'SHORT', 'prob': 90,
                         'desc': 'DX+JPY+GC+ZN tous alignés Risk-Off — ES dernier à réagir = SHORT sniper absolu'})

    # Ajoute R/R ratio du livre à chaque pattern
    for pat in patterns:
        pat['rr'] = TRADE_RR.get(pat['id'], '1:3')

    return sorted(patterns, key=lambda x: x['prob'], reverse=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — MATHÉMATIQUES INTERMARKET (Appel à contribution scientifique)
# ═══════════════════════════════════════════════════════════════════════════════

_seasonal_cache = {'closes': None, 'ts': 0}
SEASONAL_TTL = 3600  # 1h — données long-terme stables


def _download_returns(period='4mo'):
    """Télécharge les rendements journaliers des 8 marchés en une seule passe."""
    closes = {}
    for sym, info in MARKETS.items():
        try:
            hist = yf.Ticker(info['ticker']).history(period=period, interval='1d')
            if not hist.empty and len(hist) >= 40:
                closes[sym] = hist['Close'].dropna()
        except Exception:
            pass
    if len(closes) < 2:
        return pd.DataFrame()
    df = pd.DataFrame(closes)
    df = df.dropna()
    return df.pct_change().dropna()


def _download_closes_long(period='3y'):
    """Closes long-terme (3 ans) pour la saisonnalité — cache 1h."""
    global _seasonal_cache
    if _seasonal_cache['closes'] is not None and time.time() - _seasonal_cache['ts'] < SEASONAL_TTL:
        return _seasonal_cache['closes']
    closes = {}
    for sym, info in MARKETS.items():
        try:
            hist = yf.Ticker(info['ticker']).history(period=period, interval='1d')
            if not hist.empty:
                closes[sym] = hist['Close'].dropna()
        except Exception:
            pass
    df = pd.DataFrame(closes).dropna(how='all') if closes else pd.DataFrame()
    _seasonal_cache = {'closes': df, 'ts': time.time()}
    return df


# ── 1. Matrice de corrélation rolling 30j ─────────────────────────────────────

def rolling_correlation_matrix(returns_df, window=30):
    """
    Matrice de corrélation rolling sur `window` jours.
    Retourne : corrélation courante + corrélation historique (90j) + delta.
    Axe 1 du document scientifique : matrices de corrélation évolutives.
    """
    if returns_df.empty or len(returns_df) < window + 5:
        return {}

    syms = [s for s in MARKETS if s in returns_df.columns]
    recent = returns_df.tail(window)
    hist   = returns_df  # toute la période disponible

    corr_now  = recent[syms].corr()
    corr_hist = hist[syms].corr()

    matrix = {}
    for s1 in syms:
        matrix[s1] = {}
        for s2 in syms:
            c_now  = float(corr_now.loc[s1, s2])
            c_hist = float(corr_hist.loc[s1, s2])
            matrix[s1][s2] = {
                'current':    round(c_now,  3),
                'historical': round(c_hist, 3),
                'delta':      round(c_now - c_hist, 3),
            }
    return matrix


# ── 2. Détection des décalages temporels (cross-correlation) ──────────────────

def detect_temporal_lags(returns_df, max_lag=5):
    """
    Identifie le décalage temporel (en jours) entre marchés leaders et suiveurs.
    Axe 4 du document : identification des marchés leaders et retardataires.

    Pour chaque paire (leader, suiveur), cherche le lag ∈ [-max_lag, max_lag]
    qui maximise la corrélation absolue.
    Lag > 0 : leader précède le suiveur de `lag` jours.
    Lag < 0 : le suiveur précède le leader (inattendu = signal).
    """
    if returns_df.empty or len(returns_df) < max_lag * 3:
        return []

    # Paires institutionnellement pertinentes (Ch.14 du livre)
    PAIRS = [
        ('JPY', 'ES',  'Yen → Actions',       -1),   # corrélation attendue négative
        ('JPY', 'BTC', 'Yen → Bitcoin',        -1),
        ('JPY', 'GC',  'Yen → Or',             -1),
        ('ZN',  'ES',  'Obligations → Actions',-1),
        ('ZN',  'GC',  'Obligations → Or',     +1),
        ('BTC', 'ES',  'Bitcoin → Actions',    +1),
        ('DX',  'GC',  'Dollar → Or',          -1),
        ('DX',  'BTC', 'Dollar → Bitcoin',     -1),
        ('GC',  'ES',  'Or → Actions',         -1),
    ]

    results = []
    a_vals = returns_df
    n = len(a_vals)

    for leader, follower, name, expected_sign in PAIRS:
        if leader not in a_vals.columns or follower not in a_vals.columns:
            continue

        a = a_vals[leader].values
        b = a_vals[follower].values

        best_lag, best_corr = 0, float(np.corrcoef(a, b)[0, 1])

        for lag in range(-max_lag, max_lag + 1):
            if lag == 0:
                corr = float(np.corrcoef(a, b)[0, 1])
            elif lag > 0:
                corr = float(np.corrcoef(a[:-lag], b[lag:])[0, 1])
            else:
                corr = float(np.corrcoef(a[abs(lag):], b[:-abs(lag)])[0, 1])

            if np.isnan(corr):
                continue
            if abs(corr) > abs(best_corr):
                best_corr = corr
                best_lag  = lag

        # Interprétation
        if best_lag > 0:
            interpretation = f'{leader} précède {follower} de {best_lag}j'
        elif best_lag < 0:
            interpretation = f'{follower} précède {leader} de {abs(best_lag)}j (inattendu)'
        else:
            interpretation = 'Simultanés'

        surprise = (best_corr * expected_sign < 0) and abs(best_corr) > 0.25

        results.append({
            'leader':         leader,
            'follower':       follower,
            'name':           name,
            'lag_days':       best_lag,
            'correlation':    round(best_corr, 3),
            'expected_sign':  expected_sign,
            'surprise':       surprise,
            'interpretation': interpretation,
        })

    return sorted(results, key=lambda x: abs(x['correlation']), reverse=True)


# ── 3. Z-score de désynchronisation statistique ───────────────────────────────

def zscore_desync(returns_df, window=30, min_hist=60):
    """
    Pour chaque paire clé, compare la corrélation actuelle (30j) à sa
    distribution historique → Z-score de désynchronisation.
    Z > 2 = anomalie forte. Z entre 1.5 et 2 = alerte.
    Axe 7 du document : détection des anomalies et divergences.
    """
    if returns_df.empty or len(returns_df) < min_hist:
        return []

    KEY_PAIRS = [
        ('JPY', 'ES',  'Yen / S&P 500',     -1),
        ('ZN',  'ES',  'Bonds / S&P 500',   -1),
        ('GC',  'DX',  'Or / Dollar',       -1),
        ('BTC', 'ES',  'Bitcoin / S&P 500', +1),
        ('CL',  'HG',  'Pétrole / Cuivre',  +1),
        ('GC',  'ZN',  'Or / Obligations',  +1),
        ('JPY', 'GC',  'Yen / Or',          -1),
        ('DX',  'BTC', 'Dollar / Bitcoin',  -1),
    ]

    anomalies = []

    for s1, s2, name, expected_sign in KEY_PAIRS:
        if s1 not in returns_df.columns or s2 not in returns_df.columns:
            continue

        # Corrélations rolling pour construire la distribution historique
        hist_corrs = []
        for i in range(len(returns_df) - window):
            sub = returns_df.iloc[i: i + window][[s1, s2]].dropna()
            if len(sub) >= window:
                c = float(sub[s1].corr(sub[s2]))
                if not np.isnan(c):
                    hist_corrs.append(c)

        if len(hist_corrs) < 5:
            continue

        current_corr = float(returns_df.tail(window)[[s1, s2]].dropna()[s1].corr(
            returns_df.tail(window)[[s1, s2]].dropna()[s2]))

        if np.isnan(current_corr):
            continue

        mean_c = float(np.mean(hist_corrs))
        std_c  = float(np.std(hist_corrs))

        if std_c < 0.001:
            continue

        z = (current_corr - mean_c) / std_c

        # Inversion de signe = désynchronisation qualitative
        inverted = (current_corr * expected_sign < -0.1) and abs(current_corr) > 0.2

        level = 'NORMAL'
        if abs(z) > 2.5 or inverted:
            level = 'ANOMALIE'
        elif abs(z) > 1.5:
            level = 'ALERTE'

        if level != 'NORMAL':
            anomalies.append({
                'pair':         f'{s1}/{s2}',
                'name':         name,
                'current_corr': round(current_corr, 3),
                'mean_corr':    round(mean_c, 3),
                'z_score':      round(z, 2),
                'inverted':     inverted,
                'level':        level,
                'signal': (
                    f'Corrélation inversée ({current_corr:+.2f} vs attendu {"+" if expected_sign>0 else "-"})'
                    if inverted else
                    f'Z={z:+.1f} — écart {"fort" if abs(z)>2.5 else "modéré"} à la normale ({mean_c:+.2f})'
                ),
            })

    return sorted(anomalies, key=lambda x: abs(x['z_score']), reverse=True)


# ── Axe 2 — Saisonnalité multi-échelle ───────────────────────────────────────

def seasonal_bias(df_closes):
    """
    Axe 2 : biais saisonnier mensuel et hebdomadaire.
    Utilise 3 ans d'historique pour des statistiques significatives.
    Retourne pour chaque marché :
      - monthly_bias : rendement moyen du mois courant (historique)
      - weekly_bias  : rendement moyen du jour de semaine courant
      - signal       : FAVORABLE / DÉFAVORABLE / NEUTRE
      - monthly_all  : dict {1..12: avg_return%} pour visualisation calendrier
    """
    if df_closes is None or df_closes.empty:
        return {}

    MONTHS_FR = {1:'Jan',2:'Fév',3:'Mar',4:'Avr',5:'Mai',6:'Jun',
                 7:'Jul',8:'Aoû',9:'Sep',10:'Oct',11:'Nov',12:'Déc'}
    DAYS_FR   = {0:'Lun',1:'Mar',2:'Mer',3:'Jeu',4:'Ven',5:'Sam',6:'Dim'}

    now = now_local()
    cm, cw = now.month, now.weekday()
    result = {}

    seasonax_data = load_seasonax_data()

    for sym in MARKETS:
        if sym not in df_closes.columns:
            continue
        s = df_closes[sym].dropna()
        ret = s.pct_change().dropna() * 100  # en %
        df = ret.to_frame('r')
        df.index = pd.to_datetime(df.index)
        df['month']   = df.index.month
        df['weekday'] = df.index.weekday

        monthly  = df.groupby('month')['r'].mean()
        weekly   = df.groupby('weekday')['r'].mean()

        mb = float(monthly.get(cm, 0))
        wb = float(weekly.get(cw, 0))

        monthly_all = {int(m): round(float(v), 3) for m, v in monthly.items()}
        weekly_all  = {int(d): round(float(v), 3) for d, v in weekly.items()}

        # Override avec Seasonax si disponible
        seasonax_override = False
        if sym in seasonax_data:
            sx = seasonax_data[sym]
            if 'monthly_bias' in sx:
                mb = float(sx['monthly_bias'])
                seasonax_override = True
            if 'weekly_bias' in sx:
                wb = float(sx['weekly_bias'])
                seasonax_override = True

        combined = round((mb + wb) / 2, 3)

        result[sym] = {
            'monthly_bias':      round(mb, 3),
            'weekly_bias':       round(wb, 3),
            'combined_bias':     combined,
            'month_name':        MONTHS_FR.get(cm, ''),
            'day_name':          DAYS_FR.get(cw, ''),
            'monthly_all':       monthly_all,
            'weekly_all':        weekly_all,
            'signal': ('FAVORABLE' if combined > 0.05 else
                       'DÉFAVORABLE' if combined < -0.05 else 'NEUTRE'),
            'n_years':           round(len(s) / 252, 1),
            'seasonax_override': seasonax_override,
        }
    return result


# ── Axe 3 — Régimes de marché avancés (clustering dynamique) ─────────────────

def advanced_regime_detection(returns_df):
    """
    Axe 3 : classification du régime par score de flux pondéré + percentile historique.
    Approche : score intermarket journalier (pondéré par polarity) → distribution historique
    → classification en 5 régimes selon percentile et volatilité récente.
    Aucune dépendance externe requise.
    """
    if returns_df.empty or len(returns_df) < 40:
        return {}

    syms = [s for s in MARKETS if s in returns_df.columns]

    def risk_score_row(row):
        return sum(MARKETS[s]['risk_polarity'] * row[s] for s in syms if s in row)

    daily_scores = returns_df[syms].apply(risk_score_row, axis=1)
    roll20 = daily_scores.rolling(20)
    score_series = roll20.mean().dropna()
    vol_series   = roll20.std().dropna()

    if score_series.empty:
        return {}

    cur_score = float(score_series.iloc[-1])
    cur_vol   = float(vol_series.iloc[-1]) if not vol_series.empty else 0

    score_pct = float(np.mean(score_series.values < cur_score)) * 100
    vol_pct   = float(np.mean(vol_series.values < cur_vol))   * 100 if not vol_series.empty else 50

    # 5 régimes
    if score_pct >= 75 and vol_pct < 65:
        regime, color, desc = 'RISK-ON FORT',        '#22c55e', 'Momentum achats massifs — rester long'
        conf = min(95, int(score_pct))
    elif score_pct >= 55:
        regime, color, desc = 'RISK-ON MODÉRÉ',      '#86efac', 'Biais haussier — bons points d\'entrée long'
        conf = int(score_pct * 0.85)
    elif score_pct <= 25 and vol_pct >= 70:
        regime, color, desc = 'CRISE / TRANSITION',  '#f97316', 'Volatilité élevée + flux risk-off — prudence'
        conf = min(95, int(vol_pct))
    elif score_pct <= 30:
        regime, color, desc = 'RISK-OFF',             '#ef4444', 'Flux refuges — privilégier shorts risqués'
        conf = int((100 - score_pct) * 0.85)
    else:
        regime, color, desc = 'NEUTRE / DISSONANCE', '#eab308', 'Régime ambigu — attendre signal clair'
        conf = 50

    # Score brut (annualisé approximatif pour lisibilité)
    score_display = round(cur_score * 252, 2)

    return {
        'regime':                regime,
        'confidence':            conf,
        'color':                 color,
        'description':           desc,
        'score_annualized':      score_display,
        'score_percentile':      round(score_pct, 1),
        'volatility_percentile': round(vol_pct, 1),
        'n_observations':        len(score_series),
    }


# ── Axe 5 — Causalité de Granger ─────────────────────────────────────────────

def granger_causality_analysis(returns_df, max_lag=3):
    """
    Axe 5 : test de causalité de Granger entre 8 paires institutionnelles.
    Nécessite statsmodels. Retourne une liste vide avec message si absent.
    p < 0.05 → causalité significative. p < 0.01 → forte.
    """
    try:
        from statsmodels.tsa.stattools import grangercausalitytests
    except ImportError:
        return {'available': False, 'message': 'pip install statsmodels', 'results': []}

    if returns_df.empty or len(returns_df) < max_lag + 20:
        return {'available': True, 'results': []}

    PAIRS = [
        ('JPY', 'ES',  'Yen → S&P 500'),
        ('JPY', 'BTC', 'Yen → Bitcoin'),
        ('BTC', 'ES',  'Bitcoin → S&P 500'),
        ('ZN',  'ES',  'Obligations → S&P 500'),
        ('DX',  'GC',  'Dollar → Or'),
        ('GC',  'ES',  'Or → S&P 500'),
        ('CL',  'ES',  'Pétrole → S&P 500'),
        ('HG',  'ES',  'Cuivre → S&P 500'),
    ]

    results = []
    for leader, follower, name in PAIRS:
        if leader not in returns_df.columns or follower not in returns_df.columns:
            continue
        data = returns_df[[follower, leader]].dropna()
        if len(data) < max_lag + 15:
            continue
        try:
            test = grangercausalitytests(data, maxlag=max_lag, verbose=False)
            # Meilleur lag = p-value minimale
            best_lag = min(test, key=lambda l: test[l][0]['ssr_ftest'][1])
            p_val  = float(test[best_lag][0]['ssr_ftest'][1])
            f_stat = float(test[best_lag][0]['ssr_ftest'][0])
            results.append({
                'leader':      leader,
                'follower':    follower,
                'name':        name,
                'best_lag':    best_lag,
                'p_value':     round(p_val, 4),
                'f_stat':      round(f_stat, 2),
                'significant': p_val < 0.05,
                'strong':      p_val < 0.01,
                'verdict':     ('FORTE' if p_val < 0.01 else
                                'SIGNIFICATIVE' if p_val < 0.05 else 'NON SIGN.'),
            })
        except Exception:
            continue

    results.sort(key=lambda x: x['p_value'])
    return {'available': True, 'results': results, 'max_lag': max_lag}


# ── Axe 6 — Score intermarket-saisonnier combiné ─────────────────────────────

def seasonal_adjusted_scores(markets_data, seasonal_data):
    """
    Axe 6 : fusionne le score intermarket (-3/+3) avec le biais saisonnier.
    Score ajusté = score_intermarket × (1 + α × biais_saisonnier)
    α = 0.4 (impact saisonnier max ±40% du score de base).
    """
    if not seasonal_data:
        return {}
    result = {}
    for sym, d in markets_data.items():
        if d.get('error'):
            continue
        base = d.get('market_score', 0)
        seas = seasonal_data.get(sym, {})
        cb   = seas.get('combined_bias', 0)
        # cb est en % de rendement journalier moyen → normalise sur [-1,1]
        bias_factor = max(-0.4, min(0.4, cb * 15))
        adjusted    = max(-3, min(3, round(base * (1 + bias_factor), 2)))
        result[sym] = {
            'base_score':     base,
            'seasonal_bias':  round(cb, 3),
            'bias_pct':       round(bias_factor * 100, 1),
            'adjusted_score': adjusted,
            'delta':          round(adjusted - base, 2),
            'signal':         seas.get('signal', 'NEUTRE'),
        }
    return result


# ── Axe 8 — Espérance mathématique dynamique ─────────────────────────────────

def dynamic_probability_factors(regime_data, zscore_anomalies, temporal_lags):
    """
    Axe 8 : calcule des facteurs d'ajustement des probabilités des 15 trades.
    Retourne un dict de facteurs applicables côté client ou serveur :
      - regime_adj      : ±pp selon clarté du régime
      - anomaly_adj     : −pp par anomalie sévère (corrélations cassées = moins fiable)
      - lag_boosts      : +pp si le bon leader a déjà bougé en avance sur son suiveur
      - global_confidence : 0-100, confiance globale du système intermarket
    """
    # Facteur régime
    regime_label = regime_data.get('regime', 'NEUTRE') if regime_data else 'NEUTRE'
    regime_conf  = regime_data.get('confidence', 50) if regime_data else 50
    regime_adj = {
        'RISK-ON FORT':       +5,
        'RISK-ON MODÉRÉ':     +2,
        'NEUTRE / DISSONANCE':-8,
        'RISK-OFF':           +2,
        'CRISE / TRANSITION': -12,
    }.get(regime_label, 0)

    # Facteur anomalies de corrélation
    severe = sum(1 for a in zscore_anomalies if a.get('level') == 'ANOMALIE')
    alerts = sum(1 for a in zscore_anomalies if a.get('level') == 'ALERTE')
    anomaly_adj = -(severe * 4 + alerts * 1)

    # Facteur décalages temporels : quels leaders ont avancé ?
    lag_boosts = {}
    for l in temporal_lags:
        if l.get('lag_days', 0) > 0 and not l.get('surprise', False):
            # Leader en avance confirmé → boost sur les trades ciblant le suiveur
            lag_boosts[l['follower']] = lag_boosts.get(l['follower'], 0) + 2

    # Confiance globale (0-100)
    base_conf = regime_conf
    anomaly_penalty = min(40, severe * 10 + alerts * 4)
    global_confidence = max(0, min(100, base_conf - anomaly_penalty))

    return {
        'regime_adj':         regime_adj,
        'anomaly_adj':        anomaly_adj,
        'lag_boosts':         lag_boosts,
        'global_confidence':  global_confidence,
        'regime_label':       regime_label,
        'n_severe_anomalies': severe,
        'n_alerts':           alerts,
        'note': (
            'Système en forte cohérence — probabilités fiables' if global_confidence >= 70 else
            'Cohérence partielle — réduire taille de position' if global_confidence >= 45 else
            'Désynchronisation détectée — attendre la normalisation'
        ),
    }


# ── Score de convergence global 0-100 ────────────────────────────────────────

def convergence_score(markets_data, regime_score, patterns, dynamic_factors, seasonality, vision_history):
    """
    Score global de convergence 0-100 basé sur 6 composantes.
    0-40  = ATTENDRE · 40-70 = SURVEILLER · 70-100 = OPPORTUNITÉ
    """
    score = 0

    # 1. Régime clair +20 / NEUTRE 0 / DISSONANCE -10
    regime_label = dynamic_factors.get('regime_label', 'NEUTRE') if dynamic_factors else 'NEUTRE'
    if regime_label in ('RISK-ON FORT', 'RISK-OFF FORT'):
        score += 20
    elif regime_label in ('RISK-ON MODÉRÉ', 'RISK-OFF'):
        score += 10
    elif regime_label == 'NEUTRE / DISSONANCE':
        score -= 10

    # 2. Nb marchés alignés / 8 × 15 pts
    aligned = sum(1 for s, d in markets_data.items() if not d.get('error') and (
        (MARKETS[s]['risk_polarity'] == 1  and d.get('state') == 'BULLISH') or
        (MARKETS[s]['risk_polarity'] == -1 and d.get('state') == 'BEARISH')
    ))
    score += int(aligned / 8 * 15)

    # 3. Meilleur pattern actif prob >= 80% → +20 pts
    if patterns:
        best_prob = max((p.get('prob', 0) for p in patterns), default=0)
        if best_prob >= 80:
            score += 20
        elif best_prob >= 70:
            score += 10

    # 4. Confiance dynamique × 0.25 → max 25 pts
    if dynamic_factors:
        gc = dynamic_factors.get('global_confidence', 0)
        score += int(gc * 0.25)

    # 5. Dernière analyse Vision
    if vision_history:
        last = vision_history[0]
        conf = last.get('analyse', {}).get('confiance', 0)
        if conf >= 85:
            score += 20
        elif conf >= 70:
            score += 10

    # 6. Biais saisonnier favorable
    if seasonality:
        favorable = sum(1 for s in seasonality.values() if s.get('signal') == 'FAVORABLE')
        if favorable >= 4:
            score += 5

    return max(0, min(100, score))


# ── Endpoint analytics (avec cache 10 min) ────────────────────────────────────

@app.route('/api/analytics')
def api_analytics():
    global _analytics_cache
    now = time.time()
    if _analytics_cache['data'] and now - _analytics_cache['ts'] < ANALYTICS_TTL:
        return jsonify(_analytics_cache['data'])

    # Téléchargements
    returns     = _download_returns(period='4mo')
    closes_long = _download_closes_long(period='3y')

    # Phase 2 — Axes 1/4/7 (existants)
    corr_matrix   = rolling_correlation_matrix(returns, window=30)
    lags          = detect_temporal_lags(returns, max_lag=5)
    anomalies     = zscore_desync(returns, window=30)

    # Phase 2 — Axes 2/3/5/6/8 (nouveaux)
    seasonality   = seasonal_bias(closes_long)
    regime_adv    = advanced_regime_detection(returns)
    granger       = granger_causality_analysis(returns, max_lag=3)

    # Axe 6 — score saisonnier (nécessite les données marchés actuelles)
    try:
        live_markets = fetch_all_markets()
    except Exception:
        live_markets = {}
    seas_scores   = seasonal_adjusted_scores(live_markets, seasonality)

    # Axe 8 — facteurs d'espérance dynamique
    dyn_factors   = dynamic_probability_factors(regime_adv, anomalies, lags)

    result = {
        # Axes 1/4/7
        'correlation_matrix': corr_matrix,
        'temporal_lags':      lags,
        'zscore_anomalies':   anomalies,
        # Axe 2
        'seasonality':        seasonality,
        # Axe 3
        'advanced_regime':    regime_adv,
        # Axe 5
        'granger':            granger,
        # Axe 6
        'seasonal_scores':    seas_scores,
        # Axe 8
        'dynamic_factors':    dyn_factors,
        # Meta
        'symbols':            list(MARKETS.keys()),
        'timestamp':          now_local().isoformat(),
        'cache_ttl_min':      ANALYTICS_TTL // 60,
    }

    _analytics_cache = {'data': result, 'ts': now}
    return jsonify(result)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/webhook/belkhayate', methods=['POST'])
def webhook_belkhayate():
    """
    Format JSON attendu :
    {
      "symbol":     "GC",
      "direction":  "BULLISH" | "BEARISH" | "NEUTRAL",
      "energy":     2.5,
      "divergence": "BEARISH_DIV" | "BULLISH_DIV" | "NONE",
      "pivot_pos":  "above_FA" | "below_MI" | "",
      "price":      2350.5
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
        'timestamp':  now_local().isoformat(),
    }
    save_tv_state(state)
    return jsonify({'ok': True, 'symbol': sym, 'direction': direction})


@app.route('/webhook/status', methods=['GET'])
def webhook_status():
    return jsonify(load_tv_state())


@app.route('/api/data')
def api_data():
    global _last_alert_ts

    markets  = fetch_all_markets()
    regime, score, aligned = risk_regime(markets)
    leader   = market_leader(markets)
    patterns = active_patterns(markets, score)
    chrono   = chronology_status(markets)

    # Alerte Telegram si pattern >= 85% et cooldown 30 min
    now_ts = time.time()
    if patterns and (now_ts - _last_alert_ts) > 1800:
        best = patterns[0]
        if best.get('prob', 0) >= 85:
            msg = (
                f'<b>🎯 Belkhayate — Pattern détecté</b>\n'
                f'Trade #{best["id"]} — {best["name"]}\n'
                f'Marché: <b>{best["market"]}</b> · Biais: <b>{best["bias"]}</b> · Prob: <b>{best["prob"]}%</b>\n'
                f'{best["desc"]}'
            )
            if send_telegram_alert(msg):
                _last_alert_ts = now_ts

    # Score de convergence
    vision_hist   = load_vision_history()
    conv_score    = convergence_score(markets, score, patterns, None, None, vision_hist)

    # Seasonax badges
    seasonax_data = load_seasonax_data()
    seasonax_symbols = list(seasonax_data.keys())

    # Marchés en retard + Questions du Matin (Protocole Sniper)
    laggards  = detect_laggards(markets, regime)
    questions = morning_questions(markets, leader, laggards, regime)

    # Scores par marché avec contribution pondérée (Ch.18)
    scores_summary = {
        sym: {
            'score':    d.get('market_score', 0),
            'state':    d.get('state', 'NEUTRAL'),
            'polarity': MARKETS[sym]['risk_polarity'],
            'contrib':  d.get('market_score', 0) * MARKETS[sym]['risk_polarity'],
        }
        for sym, d in markets.items() if not d.get('error')
    }

    # Stratégie personnalisée
    scores_dict = {sym: d.get('market_score', 0) for sym, d in markets.items()}
    setup = get_setup(scores_dict)

    # Confirmation timing (prix ES vs pivot FA comme résistance)
    es_data = markets.get('ES', {})
    es_price = es_data.get('price', 0)
    es_fa    = es_data.get('pivots', {}).get('FA', es_price)
    entry_check = confirm_entry(setup, conv_score, es_price, es_fa)

    return jsonify({
        'markets':            markets,
        'regime':             regime,
        'score':              score,
        'aligned':            aligned,
        'leader':             leader,
        'laggards':           laggards,
        'patterns':           patterns,
        'morning_questions':  questions,
        'scores_summary':     scores_summary,
        'chronology':         chrono,
        'reaction_chain':     CHRONOLOGY,
        'convergence_score':  conv_score,
        'seasonax_symbols':   seasonax_symbols,
        'setup':              setup,
        'entry_check':        entry_check,
        'market_definitions': {k: {'name': v['name'], 'role': v['role'], 'color': v['color']}
                               for k, v in MARKETS.items()},
        'timestamp':          now_local().isoformat(),
    })


# ── Vision — Analyse capture d'écran Belkhayate (Claude Vision) ──────────────

VISION_PROMPT = """Tu es un expert Belkhayate — analyste intermarket institutionnel.
Analyse cette capture d'écran TradingView avec les indicateurs Belkhayate.

ÉTAPE 1 — Lis chaque graphique :
1. Direction Belkhayate (bande colorée sous les bougies) :
   - Verte = BULLISH | Rouge = BEARISH | Absente/grise = NEUTRAL

2. Belkhayate Énergie (histogramme en bas) :
   - Barres grises/blanches = énergie acheteuse → valeur positive (ex: +2.5)
   - Barres bleues = énergie vendeuse → valeur négative (ex: -1.8)
   - Estime la valeur de la dernière barre sur une échelle ±5

3. Divergences :
   - Prix nouveau HIGH + énergie LOW = BEARISH_DIV
   - Prix nouveau LOW + énergie HIGH = BULLISH_DIV | Sinon = NONE

4. Pivots Belkhayate (lignes horizontales pointillées) :
   - SI=rouge, LA=orange, SOL=jaune, FA=gris, MI=vert clair, RE=vert, DO=vert foncé
   - Position prix : above_SI, above_LA, above_SOL, at_FA, below_MI, below_RE, below_DO

5. Symbole : GC=Or, CL=Pétrole, HG=Cuivre, ES=S&P500, ZN=Obligations, BTC=Bitcoin, JPY=USDJPY, DX=Dollar

ÉTAPE 2 — Synthèse intermarket :
En te basant sur tous les marchés visibles, détermine :
- Le régime global (RISK-ON / RISK-OFF / NEUTRE / DISSONANCE)
- Le marché leader (celui qui montre le signal le plus fort et précoce)
- L'action recommandée avec justification Belkhayate

IMPORTANT : Réponds UNIQUEMENT en JSON valide :
{
  "markets": [
    {"symbol":"GC","direction":"BULLISH","energy":2.5,"divergence":"NONE","pivot_pos":"above_FA","confidence":85}
  ],
  "analyse": {
    "regime": "RISK-OFF",
    "leader": "GC",
    "action": "LONG GC",
    "direction_trade": "LONG",
    "marche_cible": "GC",
    "confiance": 78,
    "justification": "L'Or est haussier avec forte énergie au-dessus du pivot FA. ZN confirme risk-off. ES encore neutre = retardataire. Chronologie Belkhayate respectée : GC mène, ES suit.",
    "niveaux": "Entrée au-dessus de FA, SL sous MI, TP vers LA"
  }
}

Si tu ne peux pas identifier un marché clairement, ne l'inclus pas."""


def analyze_screenshot_with_vision(image_base64, media_type='image/png'):
    import anthropic, re as _re
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    print(f"DEBUG api_key present: {bool(api_key)}, len={len(api_key)}")
    if not api_key:
        raise ValueError('ANTHROPIC_API_KEY non configuré')

    client = anthropic.Anthropic(api_key=api_key)
    print("DEBUG: client created, sending request...")
    response = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=2048,
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image', 'source': {
                    'type': 'base64', 'media_type': media_type, 'data': image_base64,
                }},
                {'type': 'text', 'text': VISION_PROMPT},
            ]
        }]
    )

    text = response.content[0].text.strip()
    # Extrait le bloc JSON même si du texte parasite l'entoure
    m = _re.search(r'\{.*\}', text, _re.DOTALL)
    if not m:
        raise ValueError(f'Vision: réponse non parseable: {text[:200]}')
    raw = m.group()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Nettoie les caractères de contrôle et retente
        raw_clean = _re.sub(r'[\x00-\x1f\x7f]', ' ', raw)
        # Tronque les strings trop longues qui peuvent casser le JSON
        raw_clean = _re.sub(r'"([^"]{500})[^"]*"', r'"\1..."', raw_clean)
        try:
            return json.loads(raw_clean)
        except json.JSONDecodeError as e:
            raise ValueError(f'Vision JSON invalide: {e} — {raw[:300]}')


@app.route('/api/vision/analyze', methods=['POST'])
def api_vision_analyze():
    import base64
    try:
        if request.content_type and 'multipart' in request.content_type:
            f = request.files.get('image')
            if not f:
                return jsonify({'error': 'Champ image manquant'}), 400
            img_b64 = base64.b64encode(f.read()).decode()
            media_type = f.content_type or 'image/png'
        else:
            body = request.get_json(silent=True) or {}
            img_b64 = body.get('image_base64', '')
            media_type = body.get('media_type', 'image/png')
            if not img_b64:
                return jsonify({'error': 'image_base64 manquant'}), 400

        vision_result = analyze_screenshot_with_vision(img_b64, media_type)
        state = load_tv_state()
        updated = []

        for m in vision_result.get('markets', []):
            sym = str(m.get('symbol', '')).upper()
            if sym not in MARKETS:
                continue
            direction = str(m.get('direction', 'NEUTRAL')).upper()
            if direction not in ('BULLISH', 'BEARISH', 'NEUTRAL'):
                direction = 'NEUTRAL'
            state[sym] = {
                'direction':  direction,
                'energy':     float(m.get('energy', 0)),
                'divergence': m.get('divergence', 'NONE'),
                'pivot_pos':  m.get('pivot_pos', ''),
                'price':      0,
                'confidence': int(m.get('confidence', 0)),
                'timestamp':  now_local().isoformat(),
                'source':     'vision',
            }
            updated.append(sym)

        save_tv_state(state)

        # Sauvegarde dans l'historique Vision
        analyse = vision_result.get('analyse', {})
        save_vision_history({
            'timestamp': now_local().isoformat(),
            'markets':   updated,
            'analyse':   analyse,
        })

        # Alerte Telegram si confiance >= 70%
        if analyse.get('confiance', 0) >= 70:
            conf = analyse.get('confiance', 0)
            action = analyse.get('action', '')
            regime_v = analyse.get('regime', '')
            justif = analyse.get('justification', '')
            niveaux = analyse.get('niveaux', '')
            msg = (
                f'<b>📷 Vision Belkhayate — {"🔥" if conf >= 85 else ""}Analyse</b>\n'
                f'Action: <b>{action}</b> · Confiance: <b>{conf}%</b>\n'
                f'Régime: {regime_v}\n'
                f'{justif}\n'
                f'<i>📍 {niveaux}</i>'
            )
            send_telegram_alert(msg)

        return jsonify({
            'ok':      True,
            'updated': updated,
            'markets': vision_result.get('markets', []),
            'analyse': analyse,
        })

    except ImportError:
        return jsonify({'error': 'pip install anthropic requis'}), 500
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'type': type(e).__name__, 'detail': traceback.format_exc()}), 500


# ── Vision History ────────────────────────────────────────────────────────────

@app.route('/api/vision/history', methods=['GET'])
def api_vision_history():
    return jsonify(load_vision_history())


@app.route('/api/vision/history/export', methods=['GET'])
def api_vision_history_export():
    from flask import send_file
    import io
    history = load_vision_history()
    data = json.dumps(history, indent=2, ensure_ascii=False).encode('utf-8')
    buf = io.BytesIO(data)
    buf.seek(0)
    filename = f"vision_history_{now_local().strftime('%Y%m%d_%H%M%S')}.json"
    return send_file(buf, mimetype='application/json',
                     as_attachment=True, download_name=filename)


@app.route('/api/vision/history/import', methods=['POST'])
def api_vision_history_import():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Aucun fichier reçu'}), 400
        f = request.files['file']
        imported = json.loads(f.read().decode('utf-8'))
        if not isinstance(imported, list):
            return jsonify({'error': 'Format invalide — tableau JSON attendu'}), 400

        existing = load_vision_history()
        # Fusion : on déduplique par timestamp
        existing_ts = {e.get('timestamp') for e in existing}
        new_entries = [e for e in imported if e.get('timestamp') not in existing_ts]
        merged = new_entries + existing
        merged.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        merged = merged[:50]

        with open(VISION_HISTORY_FILE, 'w') as fout:
            json.dump(merged, fout, indent=2, ensure_ascii=False)

        return jsonify({'ok': True, 'added': len(new_entries), 'total': len(merged)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Seasonax ──────────────────────────────────────────────────────────────────

@app.route('/api/seasonax', methods=['GET'])
def api_seasonax_get():
    return jsonify(load_seasonax_data())

@app.route('/api/seasonax', methods=['POST'])
def api_seasonax_post():
    body = request.get_json(silent=True) or {}
    sym = str(body.get('symbol', '')).upper()
    if sym not in MARKETS:
        return jsonify({'error': f'Symbole inconnu: {sym}'}), 400
    data = load_seasonax_data()
    data[sym] = {
        'monthly_bias': float(body.get('monthly_bias', 0)),
        'weekly_bias':  float(body.get('weekly_bias', 0)),
        'source':       body.get('source', 'seasonax'),
        'updated_at':   now_local().isoformat(),
    }
    save_seasonax_data(data)
    return jsonify({'ok': True, 'symbol': sym})


# ── Trade Journal ─────────────────────────────────────────────────────────────

# ── Calcul Kelly ─────────────────────────────────────────────────────────────

def kelly_position_size(probability, entry, sl, tp, capital=10000, fraction=0.5):
    """
    Half-Kelly position sizing.
    probability : 0-100
    entry/sl/tp : prix
    capital     : capital total (défaut $10,000)
    fraction    : 0.5 = Half-Kelly (recommandé pour réduire la volatilité)
    Retourne dict avec kelly%, montant$, nb_unités
    """
    if entry <= 0 or sl <= 0 or tp <= 0:
        return None
    p = probability / 100
    q = 1 - p
    risk_per_unit  = abs(entry - sl)
    reward_per_unit = abs(tp - entry)
    if risk_per_unit == 0:
        return None
    b = reward_per_unit / risk_per_unit  # ratio R:R
    kelly_full = (p * b - q) / b
    kelly_frac = kelly_full * fraction
    kelly_frac = max(0, min(0.25, kelly_frac))  # clamp 0-25% du capital
    montant = round(capital * kelly_frac, 2)
    units   = round(montant / entry, 4) if entry > 0 else 0
    return {
        'kelly_pct':    round(kelly_frac * 100, 1),
        'montant_usd':  montant,
        'units':        units,
        'rr_ratio':     round(b, 2),
        'capital':      capital,
        'fraction':     fraction,
        'note': 'Half-Kelly recommandé — risque modéré',
    }


@app.route('/api/kelly', methods=['POST'])
def api_kelly():
    """Calcule Kelly pour un trade donné."""
    body = request.get_json(silent=True) or {}
    result = kelly_position_size(
        probability = float(body.get('probability', 70)),
        entry       = float(body.get('entry', 0)),
        sl          = float(body.get('sl', 0)),
        tp          = float(body.get('tp', 0)),
        capital     = float(body.get('capital', 10000)),
        fraction    = float(body.get('fraction', 0.5)),
    )
    if result is None:
        return jsonify({'error': 'Données insuffisantes (entry/sl/tp requis)'}), 400
    return jsonify(result)


@app.route('/api/trades', methods=['GET'])
def api_trades_get():
    return jsonify(load_trade_journal())

@app.route('/api/trades', methods=['POST'])
def api_trades_post():
    body = request.get_json(silent=True) or {}
    trades = load_trade_journal()
    trade_id = str(int(time.time() * 1000))
    entry = float(body.get('entry_price', 0))
    sl    = float(body.get('sl', 0))
    tp    = float(body.get('tp', 0))
    prob  = float(body.get('probability', 70))
    cap   = float(body.get('capital', 10000))
    kelly = kelly_position_size(prob, entry, sl, tp, cap)
    trade = {
        'id':          trade_id,
        'symbol':      str(body.get('symbol', '')).upper(),
        'direction':   str(body.get('direction', 'L')).upper(),
        'entry_price': entry,
        'sl':          sl,
        'tp':          tp,
        'probability': prob,
        'capital':     cap,
        'kelly':       kelly,
        'pattern':     body.get('pattern', ''),
        'notes':       body.get('notes', ''),
        'opened_at':   now_local().isoformat(),
        'status':      'OPEN',
        'exit_price':  None,
        'pnl':         None,
        'closed_at':   None,
    }
    trades.insert(0, trade)
    save_trade_journal(trades)
    return jsonify({'ok': True, 'trade': trade})

@app.route('/api/trades/<trade_id>/close', methods=['POST'])
def api_trades_close(trade_id):
    body = request.get_json(silent=True) or {}
    exit_price = float(body.get('exit_price', 0))
    trades = load_trade_journal()
    for t in trades:
        if t['id'] == trade_id:
            entry = t.get('entry_price', 0)
            direction = t.get('direction', 'L')
            if entry and exit_price:
                if direction == 'L':
                    pnl_pct = (exit_price - entry) / entry * 100
                else:
                    pnl_pct = (entry - exit_price) / entry * 100
                t['pnl'] = round(pnl_pct, 3)
            t['exit_price'] = exit_price
            t['status']     = 'CLOSED'
            t['closed_at']  = now_local().isoformat()
            save_trade_journal(trades)
            return jsonify({'ok': True, 'trade': t})
    return jsonify({'error': 'Trade non trouvé'}), 404


# ── Vision Multi-captures (2 images) ─────────────────────────────────────────

@app.route('/api/vision/analyze_multi', methods=['POST'])
def api_vision_analyze_multi():
    import anthropic, re as _re
    try:
        body = request.get_json(silent=True) or {}
        images      = body.get('images', [])
        media_types = body.get('media_types', [])

        if not images or len(images) < 1:
            return jsonify({'error': 'Au moins une image requise'}), 400

        # Pad media_types si manquant
        while len(media_types) < len(images):
            media_types.append('image/png')

        api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY non configuré'}), 500

        client = anthropic.Anthropic(api_key=api_key)

        # Construire le contenu avec toutes les images
        content = []
        for i, (img_b64, mime) in enumerate(zip(images, media_types)):
            content.append({'type': 'text', 'text': f'Image {i+1} (marchés {i*4+1}-{min((i+1)*4, 8)}) :'})
            content.append({'type': 'image', 'source': {
                'type': 'base64', 'media_type': mime, 'data': img_b64,
            }})
        content.append({'type': 'text', 'text': VISION_PROMPT})

        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=3000,
            messages=[{'role': 'user', 'content': content}]
        )

        text = response.content[0].text.strip()
        m = _re.search(r'\{.*\}', text, _re.DOTALL)
        if not m:
            return jsonify({'error': f'Vision: réponse non parseable: {text[:200]}'}), 400

        raw = m.group()
        try:
            vision_result = json.loads(raw)
        except json.JSONDecodeError:
            raw_clean = _re.sub(r'[\x00-\x1f\x7f]', ' ', raw)
            raw_clean = _re.sub(r'"([^"]{500})[^"]*"', r'"\1..."', raw_clean)
            vision_result = json.loads(raw_clean)

        # Met à jour tv_state comme pour analyze simple
        state   = load_tv_state()
        updated = []
        for mkt in vision_result.get('markets', []):
            sym = str(mkt.get('symbol', '')).upper()
            if sym not in MARKETS:
                continue
            direction = str(mkt.get('direction', 'NEUTRAL')).upper()
            if direction not in ('BULLISH', 'BEARISH', 'NEUTRAL'):
                direction = 'NEUTRAL'
            state[sym] = {
                'direction':  direction,
                'energy':     float(mkt.get('energy', 0)),
                'divergence': mkt.get('divergence', 'NONE'),
                'pivot_pos':  mkt.get('pivot_pos', ''),
                'price':      0,
                'confidence': int(mkt.get('confidence', 0)),
                'timestamp':  now_local().isoformat(),
                'source':     'vision_multi',
            }
            updated.append(sym)
        save_tv_state(state)

        # Historique + Telegram
        analyse = vision_result.get('analyse', {})
        save_vision_history({
            'timestamp': now_local().isoformat(),
            'markets':   updated,
            'analyse':   analyse,
        })
        if analyse.get('confiance', 0) >= 70:
            msg = (
                f'<b>📷 Vision Multi — Analyse</b>\n'
                f'Action: <b>{analyse.get("action","")}</b> · Confiance: <b>{analyse.get("confiance",0)}%</b>\n'
                f'Régime: {analyse.get("regime","")}\n'
                f'{analyse.get("justification","")}\n'
                f'<i>📍 {analyse.get("niveaux","")}</i>'
            )
            send_telegram_alert(msg)

        return jsonify({
            'ok':      True,
            'updated': updated,
            'markets': vision_result.get('markets', []),
            'analyse': analyse,
        })

    except ImportError:
        return jsonify({'error': 'pip install anthropic requis'}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'type': type(e).__name__, 'detail': traceback.format_exc()}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug)
