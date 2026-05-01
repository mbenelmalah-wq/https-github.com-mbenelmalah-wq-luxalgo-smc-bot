from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import json, os

app = Flask(__name__)

TV_STATE_FILE = os.path.join(os.path.dirname(__file__), 'tv_state.json')

def load_tv_state():
    if os.path.exists(TV_STATE_FILE):
        with open(TV_STATE_FILE) as f:
            return json.load(f)
    return {}

def save_tv_state(state):
    with open(TV_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


# ─── Les 8 Piliers Belkhayate ──────────────────────────────────────────────────
# risk_polarity: +1 = actif Risk-On (monte en risk-on), -1 = refuge (monte en risk-off)
# reaction_order: ordre chronologique de réaction aux chocs (Ch.14 leçon trade #14)
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

# Ordre de réaction chronologique aux chocs (JPY réagit en premier, ES en dernier)
REACTION_CHAIN = ['JPY', 'ZN', 'GC', 'CL', 'HG', 'ES', 'BTC']


# ─── Indicateurs Belkhayate ────────────────────────────────────────────────────

def belkhayate_energy(closes, period=14):
    """
    Belkhayate Énergie — oscillateur momentum.
    Barres grises (>0) = pression acheteuse. Barres bleues (<0) = pression vendeuse.
    Barres grandes = forte énergie = signal potentiel. Barres petites = ne pas trader.
    """
    roc = closes.diff(period) / closes.shift(period) * 100
    return roc.ewm(span=5, adjust=False).mean()


def belkhayate_pivots(high, low, close):
    """
    Gamme musicale Belkhayate — 7 niveaux (Ch.12).
    FA = pivot central. Au-dessus de FA = premium (zone de vente).
    En dessous de FA = discount (zone d'achat).
    Rotation de polarité: un pivot cassé change de rôle (support ↔ résistance).

    Niveaux (par ordre croissant):
      DO  (vert foncé)  — Support extrême      — objectif achat maximum
      RE  (vert)        — Support fort          — objectif achat intermédiaire
      MI  (vert clair)  — Support modéré        — zone de prudence
      FA  (blanc/neutre)— Pivot central         — niveau clé de décision
      SOL (jaune)       — Résistance modérée    — zone de prudence
      LA  (orange)      — Résistance forte      — objectif vente intermédiaire
      SI  (rouge vif)   — Résistance extrême    — objectif vente maximum
    """
    pp = (high + low + close) / 3
    r  = high - low
    return {
        'SI':  round(float(pp + r * 1.000), 4),
        'LA':  round(float(pp + r * 0.618), 4),
        'SOL': round(float(pp + r * 0.250), 4),
        'FA':  round(float(pp),             4),
        'MI':  round(float(pp - r * 0.250), 4),
        'RE':  round(float(pp - r * 0.618), 4),
        'DO':  round(float(pp - r * 1.000), 4),
    }


def market_direction(closes, ema_fast=20, ema_slow=50):
    """Direction Belkhayate approximée: BULLISH si close > EMA20 > EMA50."""
    if len(closes) < ema_slow + 5:
        return 'NEUTRAL', 0
    e20  = closes.ewm(span=ema_fast).mean()
    e50  = closes.ewm(span=ema_slow).mean()
    c    = float(closes.iloc[-1])
    e20v = float(e20.iloc[-1])
    e50v = float(e50.iloc[-1])
    if c > e20v > e50v:
        return 'BULLISH', 1
    if c < e20v < e50v:
        return 'BEARISH', -1
    return 'NEUTRAL', 0


def individual_market_score(state, energy_val):
    """
    Score par marché: -3 à +3 (Ch.18 — Bloc 3).
    Combine direction et force de l'énergie.
    Exemple livre: Dollar +3, Yen +2, Obligations +2, Or +1, BTC -2...
    """
    abs_e = abs(energy_val)
    if state == 'BULLISH':
        if abs_e > 4: return 3
        if abs_e > 2: return 2
        return 1
    elif state == 'BEARISH':
        if abs_e > 4: return -3
        if abs_e > 2: return -2
        return -1
    return 0


def detect_divergence(closes, energy_series, window=10):
    """
    Détecte divergences haussières/baissières.
    Divergence baissière: prix fait higher high, énergie fait lower high.
    Divergence haussière: prix fait lower low, énergie fait higher low.
    """
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


# ─── Collecte des données ──────────────────────────────────────────────────────

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
            energy     = belkhayate_energy(closes)
            divergence = detect_divergence(closes, energy)
            pivots     = belkhayate_pivots(
                float(highs.tail(5).max()),
                float(lows.tail(5).min()),
                float(closes.iloc[-1])
            )
            cur_price  = float(closes.iloc[-1])
            prev_price = float(closes.iloc[-2])
            chg_pct    = (cur_price - prev_price) / prev_price * 100
            e_val      = float(energy.iloc[-1])
            e_history  = energy.tail(15).replace([np.inf, -np.inf], 0).fillna(0)
            msc        = individual_market_score(direction, e_val)

            # Position prix par rapport aux pivots Belkhayate
            fa_level = pivots.get('FA', cur_price)
            if cur_price > pivots.get('SOL', fa_level):
                pivot_zone = 'PREMIUM_FORT'
            elif cur_price > fa_level:
                pivot_zone = 'PREMIUM'
            elif cur_price > pivots.get('MI', fa_level):
                pivot_zone = 'DISCOUNT'
            else:
                pivot_zone = 'DISCOUNT_FORT'

            result = {
                'error':          False,
                'state':          direction,
                'dir_score':      dir_score,
                'energy':         round(e_val, 3),
                'energy_history': [round(x, 3) for x in e_history.tolist()],
                'price':          round(cur_price, 2),
                'change_pct':     round(chg_pct, 2),
                'divergence':     divergence,
                'pivots':         pivots,
                'pivot_zone':     pivot_zone,
                'market_score':   msc,
                'name':           info['name'],
                'role':           info['role'],
                'color':          info['color'],
                'risk_polarity':  info['risk_polarity'],
                'reaction_order': info['reaction_order'],
                'source':         'yfinance',
            }

            # Priorité aux données TradingView réelles
            tv = load_tv_state().get(sym)
            if tv:
                result['state']      = tv.get('direction', direction)
                result['energy']     = tv.get('energy', e_val)
                result['divergence'] = tv.get('divergence', divergence)
                result['pivot_pos']  = tv.get('pivot_pos', '')
                result['tv_time']    = tv.get('timestamp', '')
                result['source']     = 'tradingview'
                result['market_score'] = individual_market_score(result['state'], result['energy'])

            results[sym] = result

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


# ─── Analyse du régime ────────────────────────────────────────────────────────

def risk_regime(markets_data):
    """
    Régime de liquidité globale basé sur scores × polarité (Ch.18).
    Score pondéré = Σ(score_marché × risk_polarity) sur les 8 marchés.
    Plage théorique: -21 (Risk-OFF fort) à +21 (Risk-ON fort).
    """
    weighted = []
    for sym, d in markets_data.items():
        if d.get('error'):
            continue
        pol = MARKETS[sym]['risk_polarity']
        sc  = d.get('market_score', 0)
        weighted.append(sc * pol)

    if not weighted:
        return 'NO DATA', 0, 0

    total   = sum(weighted)
    aligned = len(weighted)

    if   total >= 10: regime = 'RISK-ON FORT'
    elif total >= 4:  regime = 'RISK-ON'
    elif total >= -3: regime = 'NEUTRE / DISSONANCE'
    elif total >= -9: regime = 'RISK-OFF'
    else:             regime = 'RISK-OFF FORT'

    return regime, total, aligned


def market_leader(markets_data):
    """Marché avec la plus forte énergie absolue = leader actuel de la pieuvre."""
    valid = [s for s, d in markets_data.items() if not d.get('error')]
    if not valid:
        return None
    return max(valid, key=lambda s: abs(markets_data[s].get('energy', 0)))


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
            # Actif risqué encore haussier = en retard
            if pol == +1 and state == 'BULLISH':
                laggards.append(sym)
            # Refuge encore baissier = en retard
            elif pol == -1 and state == 'BEARISH':
                laggards.append(sym)
        elif is_risk_on:
            if pol == +1 and state == 'BEARISH':
                laggards.append(sym)
            elif pol == -1 and state == 'BULLISH':
                laggards.append(sym)

    # Trier par ordre de réaction chronologique (le dernier à réagir est le plus intéressant)
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

    # Q1: Que fait le DXY ? — Soleil du système, fort ou faible ?
    dx_state  = dx.get('state', 'NEUTRAL')
    dx_score  = dx.get('market_score', 0)
    dx_chg    = dx.get('change_pct', 0)
    if dx_state == 'BULLISH':
        q1 = f'DXY FORT ({dx_chg:+.2f}%) — Pression Risk-OFF. Matières premières et actions sous tension.'
    elif dx_state == 'BEARISH':
        q1 = f'DXY FAIBLE ({dx_chg:+.2f}%) — Favorable Risk-ON. Or, BTC et actions avantagés.'
    else:
        q1 = f'DXY NEUTRE ({dx_chg:+.2f}%) — Pas de signal directionnel dominant. Attendre.'

    # Q2: Que font ZN et JPY ? — Refuges institutionnels, Risk-ON ou Risk-OFF ?
    zn_state  = zn.get('state', 'NEUTRAL')
    jpy_state = jpy.get('state', 'NEUTRAL')
    # USD/JPY BEARISH = Yen fort = carry trade se déboucle = Risk-OFF
    yen_fort = jpy_state == 'BEARISH'
    zn_bull  = zn_state == 'BULLISH'
    refuges_actifs = int(zn_bull) + int(yen_fort)
    if refuges_actifs == 2:
        q2 = (f'ZN HAUSSIER + Yen FORT (USD/JPY baissier) — '
              f'REFUGES ACTIFS. Risk-OFF naissant. Débouclement carry trade probable.')
    elif refuges_actifs == 1:
        signal = ('ZN HAUSSIER' if zn_bull else 'Yen FORT')
        q2 = f'{signal} — Signal mixte. Un seul refuge actif. Vigilance, pas encore confirmé.'
    else:
        q2 = (f'ZN {zn_state} + USD/JPY {jpy_state} — '
              f'Pas de pression sur les refuges. Contexte Risk-ON favorable.')

    # Q3: Qui est en retard ? — Le marché qu'on va trader aujourd'hui
    if laggards:
        target = laggards[0]
        tname  = MARKETS[target]['name']
        if 'RISK-OFF' in regime:
            q3 = (f'EN RETARD : {target} ({tname}) — Vente probable. '
                  f'Attendre setup Belkhayate sur 15min (pivot + énergie + direction).')
        else:
            q3 = (f'EN RETARD : {target} ({tname}) — Achat probable. '
                  f'Attendre setup Belkhayate sur 15min (pivot + énergie + direction).')
    elif leader:
        lname = MARKETS.get(leader, {}).get('name', leader)
        q3 = (f'Tous alignés avec le régime — Leader : {leader} ({lname}). '
              f'Attendre une configuration Trade Sniper #15.')
    else:
        q3 = 'Aucun marché clairement en retard — Patience. Aucun trade aujourd\'hui.'

    return [
        {'n': 1, 'q': 'Que fait le DXY ?',           'a': q1},
        {'n': 2, 'q': 'Que font ZN et JPY ?',         'a': q2},
        {'n': 3, 'q': 'Qui est en retard (à trader) ?','a': q3},
    ]


# ─── Les 15 Trades à Haute Probabilité ────────────────────────────────────────

TRADE_RR = {
    1: '1:3.0', 2: '1:3.9', 3: '1:3.8', 4: '1:4.3', 5: '1:3.5',
    6: '1:3.3', 7: '1:3.0', 8: '1:3.0', 9: '1:4.0', 10: '1:3.9',
    11: '1:3.3', 12: '1:4.3', 13: '1:3.8', 14: '1:4.7', 15: '1:4.7',
}

def active_patterns(markets_data, score, laggards):
    gc  = markets_data.get('GC',  {})
    cl  = markets_data.get('CL',  {})
    hg  = markets_data.get('HG',  {})
    es  = markets_data.get('ES',  {})
    zn  = markets_data.get('ZN',  {})
    btc = markets_data.get('BTC', {})
    jpy = markets_data.get('JPY', {})
    dx  = markets_data.get('DX',  {})

    def p(id_, name, market, bias, prob, desc):
        return {
            'id': id_, 'name': name, 'market': market,
            'bias': bias, 'prob': prob, 'rr': TRADE_RR.get(id_, '1:3'),
            'desc': desc,
        }

    patterns = []

    # ── TRADES RISK-OFF ────────────────────────────────────────────────────────

    # Trade 1 — Décalage Risk-Off Classique (ES SHORT 78%)
    # JPY fort (USD/JPY baissier) + ZN haussier → ES encore haussier = en retard
    if (jpy.get('state') == 'BEARISH' and zn.get('state') == 'BULLISH'
            and es.get('state') in ('BULLISH', 'NEUTRAL')):
        patterns.append(p(1, 'Décalage Risk-Off Classique', 'ES', 'SHORT', 78,
            'JPY fort + ZN haussier → ES en retard 4-8h. '
            'Attendre rejet sur SOL + Direction rouge.'))

    # Trade 4 — Cassure du Cuivre (HG SHORT 76%)
    # Doctor Copper casse FA à la baisse + ES encore haussier = divergence majeure
    if (hg.get('state') == 'BEARISH' and es.get('state') == 'BULLISH'
            and hg.get('energy', 0) < -1):
        patterns.append(p(4, 'Cassure du Cuivre — Doctor Copper', 'HG', 'SHORT', 76,
            'HG casse FA baissier + énergie vendeuse. ES diverge. '
            'Attendre retest FA en résistance.'))

    # Trade 5 — Pétrole en Surchauffe (CL SHORT 80%)
    # CL envolée + divergence baissière énergie + DXY remonte = sur-extension
    if (cl.get('divergence') == 'BEARISH_DIV' and cl.get('change_pct', 0) > 1.5
            and dx.get('state') == 'BULLISH'):
        patterns.append(p(5, 'Pétrole en Surchauffe', 'CL', 'SHORT', 80,
            'CL surchauffé + divergence énergie acheteuse + DXY remonte. '
            'Recul 4-6$ probable en 48h.'))

    # Trade 6 — L'Écart Yen-Carry Trade (JPY SHORT USD/JPY 85%)
    # USD/JPY BEARISH fort + refuges actifs = débouclement carry massif
    if (jpy.get('state') == 'BEARISH' and jpy.get('energy', 0) < -1
            and (gc.get('state') == 'BULLISH' or zn.get('state') == 'BULLISH')):
        patterns.append(p(6, "L'Écart Yen-Carry Trade", 'JPY', 'SHORT', 85,
            'USD/JPY baissier avec énergie vendeuse. Débouclement carry trade. '
            'Move de 150+ pips. Peut aussi shorter NQ/ES.'))

    # Trade 7 — Divergence Bonds vs Actions (ES SHORT 79%)
    # ZN monte MAIS ES tient encore = ZN a raison, ES finira par baisser
    if (zn.get('state') == 'BULLISH' and es.get('state') == 'BULLISH'):
        patterns.append(p(7, 'Divergence Bonds vs Actions', 'ES', 'SHORT', 79,
            'ZN monte (gravité du système) mais ES résiste. '
            'Fenêtre 2-6h. Obligations ont toujours raison vs actions.'))

    # Trade 8 — Bitcoin Capteur Précoce (ES SHORT 83%)
    # BTC casse en premier avec divergence baissière → ES va suivre 90-180min
    if (btc.get('state') == 'BEARISH' and es.get('state') == 'BULLISH'
            and btc.get('divergence') == 'BEARISH_DIV'):
        patterns.append(p(8, 'Bitcoin — Capteur Précoce', 'ES', 'SHORT', 83,
            'BTC casse support avec divergence. Capital spéculatif se retire. '
            'ES suit en 90-180min. Canari dans la mine.'))

    # Trade 12 — Faux Breakout des Actions (ES SHORT 79%)
    # ES breakout technique MAIS DXY monte = incohérence structurelle = piège
    if (es.get('state') == 'BULLISH' and dx.get('state') == 'BULLISH'
            and es.get('divergence') == 'BEARISH_DIV'):
        patterns.append(p(12, 'Faux Breakout des Actions', 'ES', 'SHORT', 79,
            'ES breakout mais DXY haussier = incohérence. '
            'Divergence énergie confirme. Vendre le retour sous SOL.'))

    # Trade 14 — L'Or qui Casse — Risk-Off Confirmé (GC LONG 84%)
    # JPY a réagi + ZN a réagi → OR encore en retard (60-120min de décalage)
    refuges_actifs = int(jpy.get('state') == 'BEARISH') + int(zn.get('state') == 'BULLISH')
    if refuges_actifs >= 2 and gc.get('state') != 'BULLISH':
        patterns.append(p(14, "L'Or qui Casse — Risk-Off Confirmé", 'GC', 'LONG', 84,
            'JPY et ZN ont réagi (ordre: JPY→ZN→OR). '
            'Or en retard 60-120min. Acheter pullback sur MI.'))

    # Trade 15 — Convergence Totale (ES SHORT 90%) — Le Trade Sniper Absolu
    # DXY ET JPY (yen) bougent SIMULTANÉMENT = stress systémique majeur
    dx_active  = dx.get('state') == 'BULLISH' and dx.get('energy', 0) > 1
    jpy_active = jpy.get('state') == 'BEARISH' and jpy.get('energy', 0) < -1
    if dx_active and jpy_active and es.get('state') in ('BULLISH', 'NEUTRAL'):
        patterns.append(p(15, 'Trade Sniper — Convergence Totale', 'ES', 'SHORT', 90,
            'DXY + JPY simultanés = stress systémique extrême. '
            'ES dernier à réagir. Move violent -1 à -1.5% en 24h.'))

    # ── TRADES RISK-ON ─────────────────────────────────────────────────────────

    # Trade 2 — Rebond sur Pivot RÉ en Risk-On (BTC LONG 74%)
    # DXY casse + BTC divergence haussière = liquidité renaissante
    if (btc.get('state') == 'BULLISH' and dx.get('state') == 'BEARISH'
            and btc.get('divergence') == 'BULLISH_DIV'):
        patterns.append(p(2, 'Rebond sur Pivot RÉ en Risk-On', 'BTC', 'LONG', 74,
            'DXY faible + BTC divergence haussière sur RÉ. '
            'BTC capte la liquidité renaissante en premier.'))

    # Trade 3 — L'Or qui Mène la Danse (GC LONG 82%)
    # Or monte AVEC forte énergie et est le leader actuel = stress systémique
    gc_is_leader = (gc.get('energy', 0) > 2
                    and abs(gc.get('energy', 0)) > abs(es.get('energy', 0)))
    if gc.get('state') == 'BULLISH' and gc_is_leader:
        patterns.append(p(3, "L'Or qui Mène la Danse", 'GC', 'LONG', 82,
            'Or leader avec forte énergie acheteuse. Change de rôle: suiveur→leader. '
            'Stress systémique réel. Retest FA → support.'))

    # Trade 9 — Dollar Faiblit — Or et BTC (GC LONG 77%)
    # DXY casse technique + BTC premier à réagir + OR encore en retard 60-120min
    if (dx.get('state') == 'BEARISH' and dx.get('energy', 0) < -1
            and btc.get('state') == 'BULLISH' and gc.get('state') != 'BULLISH'):
        patterns.append(p(9, 'Dollar Faiblit — Or et BTC', 'GC', 'LONG', 77,
            'DXY cassé. BTC a déjà réagi. Or en retard 60-120min. '
            'Acheter pullback sur MI dans contexte expansion liquidité.'))

    # Trade 10 — Effet Ressort sur la Bande (ES LONG 75%)
    # ES corrige vers bande Direction (temporairement NEUTRAL) + DXY baissier
    if (es.get('state') == 'NEUTRAL' and es.get('divergence') == 'BULLISH_DIV'
            and dx.get('state') == 'BEARISH'):
        patterns.append(p(10, 'Effet Ressort sur la Bande', 'ES', 'LONG', 75,
            'ES retourne sur bande Direction (points pas changés = pas de retournement). '
            'DXY baissier confirme. Stop serré sous bande.'))

    # Trade 11 — Cassure Verticale — Énergie Massive (CL LONG 81%)
    # CL énergie explosive 2-3x normale + cassure FA + pas de divergence baissière
    if (cl.get('state') == 'BULLISH' and cl.get('energy', 0) > 3
            and cl.get('divergence') != 'BEARISH_DIV'):
        patterns.append(p(11, 'Cassure Verticale — Énergie Massive', 'CL', 'LONG', 81,
            'CL énergie 2-3x normale. Cassure FA avec retest. '
            'Choc géopolitique. Attendre TOUJOURS le retest avant d\'entrer.'))

    # Trade 13 — Alignement Parfait — 5 Marchés (ES LONG 88%)
    # Quand 5 marchés parmi 8 s'alignent Risk-ON + ES pas encore réagi
    aligned_signals = sum([
        dx.get('state') == 'BEARISH',                               # DX baisse
        zn.get('state') == 'BULLISH',                               # ZN monte
        btc.get('state') == 'BULLISH',                              # BTC monte
        gc.get('state') == 'BULLISH',                               # Or monte
        cl.get('state') == 'BULLISH' or hg.get('state') == 'BULLISH',  # Matières premières
    ])
    if aligned_signals >= 4 and es.get('state') != 'BULLISH':
        patterns.append(p(13, 'Alignement Parfait — 5 Marchés Synchronisés', 'ES', 'LONG', 88,
            f'{aligned_signals}/5 signaux Risk-ON alignés. ES en consolidation sur MI. '
            f'Move dans 2-8h. Quand 5 marchés disent oui, ES finit toujours par suivre.'))

    return sorted(patterns, key=lambda x: x['prob'], reverse=True)


# ─── Routes Flask ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/webhook/belkhayate', methods=['POST'])
def webhook_belkhayate():
    """
    Reçoit les signaux des indicateurs Belkhayate depuis TradingView.

    Format JSON :
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
        'timestamp':  datetime.now().isoformat(),
    }
    save_tv_state(state)
    return jsonify({'ok': True, 'symbol': sym, 'direction': direction})


@app.route('/webhook/status', methods=['GET'])
def webhook_status():
    return jsonify(load_tv_state())


@app.route('/api/data')
def api_data():
    markets   = fetch_all_markets()
    regime, score, aligned = risk_regime(markets)
    leader    = market_leader(markets)
    laggards  = detect_laggards(markets, regime)
    patterns  = active_patterns(markets, score, laggards)
    questions = morning_questions(markets, leader, laggards, regime)

    scores_summary = {
        sym: {
            'score':    d.get('market_score', 0),
            'state':    d.get('state', 'NEUTRAL'),
            'polarity': MARKETS[sym]['risk_polarity'],
            'contrib':  d.get('market_score', 0) * MARKETS[sym]['risk_polarity'],
        }
        for sym, d in markets.items() if not d.get('error')
    }

    return jsonify({
        'markets':           markets,
        'regime':            regime,
        'score':             score,
        'aligned':           aligned,
        'leader':            leader,
        'laggards':          laggards,
        'patterns':          patterns,
        'morning_questions': questions,
        'scores_summary':    scores_summary,
        'reaction_chain':    REACTION_CHAIN,
        'market_definitions': {k: {'name': v['name'], 'role': v['role'], 'color': v['color']}
                                for k, v in MARKETS.items()},
        'timestamp': datetime.now().isoformat(),
    })


if __name__ == '__main__':
    port  = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug)
