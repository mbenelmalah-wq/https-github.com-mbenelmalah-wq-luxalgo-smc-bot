# RÈGLES ABSOLUES — BOT LUXALGO SMC (Capital secondaire test)

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

| Bot | Railway URL | Token TradingView | Alpaca |
|-----|-------------|-------------------|--------|
| SPLV3 principal | `alpaca-trading-bot-production-f693.up.railway.app/webhook` | `splv3_secret_2026` | Compte principal |
| LuxAlgo SMC | `web-production-b2c39d.up.railway.app/webhook` | `luxalgo_secret_2026` | Compte secondaire |

- GitHub LuxAlgo : https://github.com/mbenelmalah-wq/https-github.com-mbenelmalah-wq-luxalgo-smc-bot
- Railway projet : incredible-healing
