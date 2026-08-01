# 🔐 EscrowBot Telegram

Bot escrow sécurisé avec paiement crypto (OxaPay) et PayPal.

## 🚀 Déploiement sur Railway via GitHub

### Étape 1 — GitHub
1. Crée un compte sur [github.com](https://github.com) si pas déjà fait
2. Clique sur **New repository** → nomme-le `escrow-bot` → **Create**
3. Sur ton PC, ouvre CMD dans le dossier contenant les fichiers et tape :
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/TON_PSEUDO/escrow-bot.git
git push -u origin main
```

### Étape 2 — Railway
1. Va sur [railway.app](https://railway.app) → connecte-toi avec GitHub
2. Clique **New Project** → **Deploy from GitHub repo**
3. Sélectionne ton repo `escrow-bot`
4. Railway détecte automatiquement le bot ✅
5. Va dans **Variables** et ajoute si besoin :
   - `BOT_TOKEN` = ton token Telegram
   - `ADMIN_ID` = ton ID Telegram

### Commandes admin
| Commande | Description |
|----------|-------------|
| `/cryptook CODE` | Valider un paiement crypto manuellement |
| `/addscam @pseudo ID raison` | Ajouter à la scam list |
| `/removescam @pseudo` | Retirer de la scam list |

## 📁 Fichiers
- `escrow_bot.py` — Bot principal
- `requirements.txt` — Dépendances Python
- `Procfile` — Config Railway
- `railway.toml` — Config déploiement
- `.gitignore` — Fichiers ignorés par git
