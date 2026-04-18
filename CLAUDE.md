# RÈGLES ABSOLUES — BOT LUXALGO SMC (Capital secondaire test)

## ✅ AUTORISATION PERMANENTE

Claude est autorisé à :
- Accéder aux dashboards des 2 bots à chaque session
- Analyser les performances (equity, P&L, win rate, positions)
- Proposer et implémenter les modifications nécessaires pour améliorer la rentabilité
- Modifier le code, committer et pusher sans demande de confirmation supplémentaire

## ⛔ RÈGLES INTANGIBLES — NE JAMAIS ENFREINDRE

1. **WEBHOOK ONLY** — Les signaux viennent UNIQUEMENT de TradingView (LuxAlgo SMC BOS/CHoCH).
   - Aucun scanner interne. Jamais.

2. **CE BOT EST SÉPARÉ DU BOT PRINCIPAL** — Ne jamais mélanger les deux.
   - Compte Alpaca différent
   - Repo GitHub différent
   - Railway différent

3. **SELL = fermeture uniquement** — Pas de short. Le SELL ferme une position BUY existante via DELETE /positions/{symbol}.

## ✅ ARCHITECTURE VALIDÉE

- **Signaux** : TradingView LuxAlgo SMC → Webhook POST /webhook
- **Exécution** : Alpaca Paper Trading (compte LuxAlgo séparé)
- **Token** : luxalgo_secret_2026
- **Trailing SL** : monitor_loop actif
- **Filtre** : Session asiatique bloquée (23:00-08:00 UTC)
- **MM** : paramètres par symbole (BTCUSD/ETHUSD séparés)

## 🔑 COMPTES & URLS — NE PAS MÉLANGER

| Bot | Webhook | Dashboard | Token TradingView | Alpaca | Stratégie |
|-----|---------|-----------|-------------------|--------|-----------|
| SPLV3 principal | `alpaca-trading-bot-production-f693.up.railway.app/webhook` | `alpaca-trading-bot-production-f693.up.railway.app/dashboard` | `splv3_secret_2026` | Compte principal | SPLV3 + Elite EMAs + Elliott large caps 4/4 — MAX 3 pos — Half Kelly 12.5% |
| LuxAlgo SMC | `web-production-b2c39d.up.railway.app/webhook` | `web-production-b2c39d.up.railway.app/dashboard` | `luxalgo_secret_2026` | Compte secondaire | LuxAlgo BOS/CHoCH — trailing SL — paper trading |

- GitHub LuxAlgo : https://github.com/mbenelmalah-wq/https-github.com-mbenelmalah-wq-luxalgo-smc-bot
- Railway projet LuxAlgo : incredible-healing
