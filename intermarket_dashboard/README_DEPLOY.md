# Intermarket Dashboard — Méthode Belkhayate

## Lancer en local

```bash
cd intermarket_dashboard
pip install -r requirements.txt
python app.py
```
Ouvrir : http://localhost:5001

## Déployer sur Railway (projet séparé)

1. Créer un nouveau projet Railway
2. Connecter ce dossier (ou pusher en repo séparé)
3. Ajouter variable : `PORT=5001`
4. Start command : `python app.py`

## Données

- Source : Yahoo Finance (yfinance) — données journalières, légèrement différées
- Mise à jour automatique : toutes les 5 minutes dans le dashboard
- Les futures (GC, CL, HG, ES, ZN) peuvent parfois avoir des données manquantes en dehors des heures de marché
