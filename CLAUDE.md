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

## 🔑 COMPTES

- Alpaca Paper : compte LuxAlgo (séparé du principal)
- Railway : web-production-b2c39d.up.railway.app
- GitHub : https://github.com/mbenelmalah-wq/https-github.com-mbenelmalah-wq-luxalgo-smc-bot
