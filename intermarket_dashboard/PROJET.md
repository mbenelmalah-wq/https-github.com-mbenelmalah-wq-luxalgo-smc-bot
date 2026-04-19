# PROJET INTERMARKET MASTERY — BELKHAYATE
## Contexte rapide pour Claude

---

## QUI
- **Trader** : Mostafa Belkhayate (World AI Trading Championship Winner, Springbox AI)
- **Méthode** : Intermarket Mastery 2026 — Édition Institutionnelle
- **Plateforme de référence** : ortogonex.com (dashboard inter-corrélé)

---

## CE QUI EST DÉJÀ CONSTRUIT

### Dashboard Flask — `intermarket_dashboard/`
- **URL locale** : http://localhost:5001
- **Lancement** : `cd intermarket_dashboard && python app.py`
- **Fichiers** :
  - `app.py` — backend Flask + logique marché
  - `templates/index.html` — UI dark Octogone
  - `tv_state.json` — signaux TradingView reçus (créé automatiquement)

### Fonctionnalités actuelles (Phase 1)
- [x] Octogone visuel des 8 marchés
- [x] Régime Risk-On/Off (score -8 à +8)
- [x] Tête du marché (leader par énergie max)
- [x] Belkhayate Énergie approximée (ROC + EMA)
- [x] Pivots Belkhayate approximés (SI, LA, FA, MI, RE)
- [x] Détection divergences haussières/baissières
- [x] 9/15 patterns de trades auto-détectés
- [x] Webhook TradingView `/webhook/belkhayate`
- [x] Données hybrides : TradingView (priorité) + yfinance (fallback)
- [x] Indicateur source : ● TV (vert) vs ● ~ (gris)

---

## LES 8 PILIERS (marchés)

| Symbole | Nom | Rôle Belkhayate | Ticker Yahoo | Polarity |
|---------|-----|-----------------|--------------|----------|
| GC | Or | Détecteur stress monétaire | GC=F | -1 (Risk-Off si haussier) |
| CL | Pétrole | Moteur inflationniste | CL=F | +1 (Risk-On si haussier) |
| HG | Cuivre | Doctor Copper | HG=F | +1 |
| ES | S&P 500 | Miroir liquidité | ES=F | +1 |
| ZN | Obligations 10Y | Chef d'orchestre | ZN=F | -1 |
| BTC | Bitcoin | Capteur liquidité extrême | BTC-USD | +1 |
| JPY | Yen (6J) | Détonateur caché | USDJPY=X | +1 (USD/JPY) |
| DX | Dollar Index | Centre de gravité | DX-Y.NYB | -1 |

**Centre du système** : LIQUIDITÉ GLOBALE

---

## LES 3 INSTRUMENTS BELKHAYATE (Ch. 12)

1. **Belkhayate Énergie** — Oscillateur momentum
   - Barres grises = énergie acheteuse (pression haussière)
   - Barres bleues = énergie vendeuse (pression baissière)
   - Barres petites = peu d'énergie — ne pas trader
   - Barres grandes = forte énergie — signal potentiel
   - Divergence baissière = prix fait higher high, énergie fait lower high
   - Sur TradingView : indicateur "BELKHAYATE ÉNERGIE"

2. **Pivots Belkhayate** — Niveaux magnétiques
   - Niveaux : SI (résistance extrême), LA, FA (pivot central), MI, RE (support extrême)
   - Principe : rotation de polarité (FA cassé = change de rôle support/résistance)
   - Sur TradingView : indicateur "B-pivot"

3. **Direction Belkhayate** — Confirmation tendance
   - Rouge = baissier dès la 3e bougie de rejet

---

## STRUCTURE D'UN TRADE (3 niveaux)

```
NIVEAU 1 — CONTEXTE INTERMARKET
  → Régime (Risk-On / Risk-Off / Dissonance)
  → Leader identifié
  → Marché retardataire ciblé
  → Probabilité %

NIVEAU 2 — ÉNERGIE BELKHAYATE
  → Divergence confirmée
  → Taille des barres (forte/faible énergie)

NIVEAU 3 — PIVOT + DIRECTION
  → Prix sur niveau clé (FA, MI, RE...)
  → Direction rouge/verte confirmée
```

---

## LES 15 TRADES HAUTE PROBABILITÉ

| # | Nom | Marché | Biais | Prob |
|---|-----|--------|-------|------|
| 1 | Décalage Risk-Off Classique | ES | SHORT | 78% |
| 2 | Rebond sur Pivot Risk-On | BTC | LONG | 75% |
| 3 | L'Or qui Mène la Danse | GC | LONG | 80% |
| 4 | Cassure du Cuivre | HG | L/S | 72% |
| 5 | Pétrole en Surchauffe | CL | SHORT | 76% |
| 6 | L'Écart Yen-Carry Trade | JPY | L/S | ~74% |
| 7 | Divergence Bonds vs Actions | ES | WATCH | 70% |
| 8 | Bitcoin — Capteur Précoce | ES | SHORT | 83% |
| 9 | Dollar Faiblit — Or et BTC | GC | LONG | 79% |
| 10 | Effet Ressort sur la Bande | NQ | L/S | ~74% |
| 11 | Cassure Verticale | CL | L/S | ~73% |
| 12 | Faux Breakout des Actions | ES | L/S | ~75% |
| 13 | Alignement Parfait | ES | LONG | 88% |
| 14 | Risk-Off Confirmé | GC | LONG | 82% |
| 15 | Trade Sniper — Convergence Totale | ES | L/S | 93% |

---

## DOCUMENT CLÉ — APPEL SCIENTIFIQUE BELKHAYATE

**Titre** : Modélisation mathématique des relations inter-marchés et de leur saisonnalité dynamique

**7 axes mathématiques à implémenter** :

1. **Corrélations évolutives** — matrices rolling, graphes pondérés
2. **Saisonnalité multi-échelle** — cycles mensuel / hebdo / intraday / macro
3. **Régimes de marché** — HMM (Hidden Markov Model), regime switching, clustering
4. **Leaders & retardataires** — Granger Causality, décalages temporels
5. **Causalité & propagation** — VAR / VECM
6. **Score synthétique intermarket-saisonnier** — indicateur global combiné
7. **Anomalies & divergences** — désynchronisation statistique

---

## ROADMAP DES PHASES

### Phase 1 — TERMINÉE ✅
Dashboard de base avec approximations

### Phase 2 — À FAIRE 🔄
- [ ] Matrice de corrélation rolling (30j/60j) affichée dans le dashboard
- [ ] Granger Causality pour identification vraie du leader
- [ ] HMM pour régime Risk-On/Off (remplace la règle EMA simple)
- [ ] Décalages temporels exploitables entre marchés

### Phase 3 — PLANIFIÉE 📋
- [ ] Biais saisonnier par marché (mensuel/hebdomadaire)
- [ ] Score intermarket-saisonnier combiné
- [ ] Calendrier des périodes à haute prédictivité

### Phase 4 — VISION 🎯
- [ ] Intelligence artificielle intermarket (modèle ML)
- [ ] Intégration TradingView complète (tous les indicateurs Belkhayate)
- [ ] Déploiement Railway avec URL publique

---

## DÉPLOIEMENT

| Environnement | URL | Status |
|---------------|-----|--------|
| Local PC | http://localhost:5001 | ✅ Actif |
| Railway (dashboard) | À déployer | 🔄 En attente |

**Branche GitHub** : `claude/belkhayate-platform-explanation-AY50w`

### Lancer en local (Windows PowerShell)
```powershell
cd https-github.com-mbenelmalah-wq-luxalgo-smc-bot\intermarket_dashboard
python app.py
# → http://localhost:5001
```

---

## WEBHOOK TRADINGVIEW

**Endpoint** : `POST /webhook/belkhayate`

**Format JSON alerte TradingView** :
```json
{
  "symbol": "GC",
  "direction": "BULLISH",
  "energy": {{plot_0}},
  "price": {{close}},
  "divergence": "NONE"
}
```

**2 alertes par marché** :
- Condition "Énergie croise vers le haut" → direction: "BULLISH"
- Condition "Énergie croise vers le bas" → direction: "BEARISH"
- Déclenchement : "À la clôture de la bougie" (PAS "Une fois seulement")

---

## POUR REPRENDRE RAPIDEMENT

1. Lire ce fichier `PROJET.md`
2. Lire `app.py` pour voir l'état du code
3. Phase en cours : déploiement Railway + Phase 2 mathématique
