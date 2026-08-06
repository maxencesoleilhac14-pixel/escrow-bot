"""
🔐 EscrowBot — Version complète v3
Fixes : auto-validation OxaPay instantanée via webhook IPN,
        polling backup toutes les 15s, panel admin avec commandes,
        refund sans casser la session, 3 langues.
"""

import logging, uuid, aiohttp, os, asyncio, hmac, hashlib, json
from datetime import datetime
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# ──────────────────────────────────────────────
#  CONFIG — lit depuis variables d'environnement Railway
#  ou valeurs par défaut si lancé en local
# ──────────────────────────────────────────────
BOT_TOKEN        = os.environ.get("BOT_TOKEN")
ADMIN_ID         = int(os.environ.get("ADMIN_ID", "8955159788"))
FEES_PCT         = 0.10
OXAPAY_KEY       = os.environ.get("OXAPAY_KEY")
OXAPAY_IPN_SECRET= os.environ.get("OXAPAY_IPN_SECRET", "")   # optionnel — vérif signature
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")
if not OXAPAY_KEY:
    raise RuntimeError("OXAPAY_KEY environment variable is required")
OXAPAY_API       = "https://api.oxapay.com/merchants/request"
OXAPAY_INQ       = "https://api.oxapay.com/merchants/inquiry"
PAYPAL_URL       = "https://www.paypal.me/SkyOress"
SERVER_LINK      = "https://t.me/+ALtFJrhrEYM5NDRk"
WEBHOOK_PORT     = int(os.environ.get("PORT", 8080))   # Railway injecte PORT automatiquement

# ──────────────────────────────────────────────
#  ÉTAT GLOBAL
# ──────────────────────────────────────────────
bot_online    : bool = True
all_users     : set  = set()
user_lang     : dict = {}
sessions      : dict = {}
user_sessions : dict = {}
scam_list     : list = []
user_first_free: set = set()   # users ayant déjà utilisé leur 1ère session offerte
_app          = None   # référence globale à l'Application Telegram (pour le webhook IPN)

STATE_FILE = os.environ.get("STATE_FILE", "bot_state.json")

def save_state():
    """Sauvegarde tout l'état (sessions, users, scam list...) pour survivre aux redémarrages."""
    try:
        payload = {
            "sessions":        sessions,
            "user_sessions":   user_sessions,
            "user_lang":       user_lang,
            "all_users":       list(all_users),
            "scam_list":       scam_list,
            "user_first_free": list(user_first_free),
            "bot_online":      bot_online,
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"save_state error: {e}")

def load_state():
    """Restaure l'état au démarrage (après redémarrage Railway par ex)."""
    global bot_online
    try:
        if not os.path.exists(STATE_FILE):
            return
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        sessions.update(d.get("sessions", {}))
        user_sessions.update(d.get("user_sessions", {}))
        user_lang.update(d.get("user_lang", {}))
        all_users.update(d.get("all_users", []))
        scam_list.extend(d.get("scam_list", []))
        user_first_free.update(d.get("user_first_free", []))
        bot_online = d.get("bot_online", True)
        logger.info(f"État restauré : {len(sessions)} sessions, {len(all_users)} users")
    except Exception as e:
        logger.error(f"load_state error: {e}")

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  TRADUCTIONS — 3 langues complètes
# ──────────────────────────────────────────────
LANG = {
"fr": {
"choose_lang":      "🌍 *Choisis ta langue :*",
"welcome":          "👋 *Salut {name} !*\n\n🔐 *BIENVENUE SUR ESCROWBOT*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⭐ *L'intermédiaire de confiance*\n_Protège chaque transaction de A à Z._\n\n✅ *Argent bloqué* jusqu'à livraison\n🔍 *Livraison vérifiée* par l'admin\n💸 *Frais fixes : 10%* seulement\n⚡ *Rapide, sûr, discret*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n👇 *Que veux-tu faire ?*",
"bot_offline":      "🔴 *EscrowBot est hors ligne.*\n\nAucun admin disponible.\n⏳ _Reviens plus tard !_",
"btn_create":       "🛒  Créer une session  ·  Acheteur",
"btn_join":         "🔑  Rejoindre une session  ·  Vendeur",
"btn_sessions":     "📋  Mes sessions",
"btn_howto":        "ℹ️  Comment ça marche ?",
"btn_server":       "💬  Rejoindre le serveur",
"btn_scamlist":     "🚫  Scam List",
"btn_admin":        "👑  Panel Admin",
"btn_back":         "⬅️  Retour au menu",
"howto":            "ℹ️ *Comment fonctionne EscrowBot ?*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n1️⃣ L'acheteur crée une session\n2️⃣ Il partage le code au vendeur\n3️⃣ Le vendeur rejoint\n4️⃣ L'acheteur définit les conditions\n5️⃣ L'acheteur paie via lien sécurisé\n6️⃣ L'admin valide le paiement ✅\n7️⃣ Le vendeur dépose la livraison\n8️⃣ L'admin vérifie et valide 🔍\n9️⃣ Le vendeur reçoit son argent 💰\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💸 Frais : *10%* à la charge de l'acheteur\n🔒 Aucun argent ne part sans validation admin !",
"scamlist_empty":   "🚫 *Scam List*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n✅ Aucun scammeur répertorié.",
"scamlist_title":   "🚫 *Scam List — Personnes à éviter*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ _Ces personnes ont été signalées._\n\n",
"step1":            "🛒 *Créer une session — Étape 1/5*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nQuel est le nom/description de l'article ?",
"step2":            "📬 *Étape 2/5 — Type de livraison*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Article : *{item}*\n\nQuel type de livraison attends-tu ?",
"step3":            "💵 *Étape 3/5 — Prix*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Livraison : *{dtype}*\n\nÀ quel montant le vendeur doit-il être payé ?\n💡 _Ex: 50 ou 49.99_\n⚠️ _10% de frais ajoutés automatiquement_",
"step4":            "💳 *Étape 4/5 — Paiement*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n💰 *Récapitulatif :*\n  💵 Prix vendeur  → `{seller} €`\n  📊 {fee_label}   → `{fees} €`\n  ━━━━━━━━━━━━━━━━━\n  💳 *Total        → `{total} €`*\n\nQuelle méthode de paiement ?",
"step5":            "📋 *Étape 5/5 — Conditions*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚖️ Définis les conditions de la transaction.\nElles seront inscrites sur le *contrat de session*.\n\n✍️ Écris tes conditions ou clique *Ignorer*.\n💡 _Conditions convenues en amont avec le vendeur en privé._",
"btn_skip_cond":    "⏩  Ignorer les conditions",
"btn_exchange":     "🔁  Échange de tech — frais 5€",
"free_ready":       "🔔 *Le vendeur a rejoint ta session !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🆓 *Ta session est OFFERTE — aucun paiement requis !*\n🧑‍💼 *Vendeur :* {seller}\n\n📦 Le vendeur va envoyer la livraison directement.",
"tx_complete_seller":"🎉 *Échange terminé !*\nMerci d'avoir utilisé *EscrowBot* ! 🙌",
"exchange_deposit": "📦 *Échange — Dépose ta tech !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nEnvoie ta tech (fichier, lien, image ou texte).\n⏳ On attend la tech des 2 côtés, puis l'admin valide l'échange.",
"exchange_deposited":"📤 *Ta tech est déposée !*\n⏳ En attente de la tech de l'autre côté…",
"exchange_ready":   "🔁 *Échange prêt !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nLes 2 tech sont déposées.\n🔍 L'admin valide l'échange.",
"session_created":  "🎉 *Session créée !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{summary}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n📤 *Code à partager au vendeur :*\n\n```\n{code}\n```\n⏳ _En attente du vendeur…_",
"join_prompt":      "🔑 *Rejoindre une session*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nEntre le code de session :",
"code_not_found":   "❌ *Code introuvable !*\nVérifie le code et réessaie.",
"code_used":        "⛔ *Code déjà utilisé !*",
"joined_ok":        "✅ *Tu as rejoint la session !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{summary}\n⏳ En attente du paiement acheteur…",
"pay_crypto_msg":   "🔔 *Le vendeur a rejoint ta session !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🧑‍💼 *Vendeur :* {seller}\n💰 *Montant : `{total} €`*\n🪙 *Méthode : Crypto*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ *FRAIS RÉSEAU :* Le paiement crypto implique des frais de réseau (gas fees) non inclus dans le montant. Ces frais sont à ta charge et dépendent de la blockchain. Le montant reçu par le vendeur peut donc être légèrement inférieur.\n\n🔗 Clique sur le bouton pour payer.\n\n🚨 *Ne quitte PAS la page de paiement avant confirmation du bot ou de la blockchain. Même l'admin n'a pas accès aux fonds avant validation. Reste sur la page jusqu'au retour du bot.*",
"pay_paypal_msg":   "🔔 *Le vendeur a rejoint ta session !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🧑‍💼 *Vendeur :* {seller}\n💰 *Montant : `{total} €`*\n💳 *Méthode : PayPal*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n1️⃣ Clique sur le bouton pour payer\n2️⃣ ⚠️ *IMPORTANT :* Paiement en *AMIS / PROCHES (Friends & Family)* **OBLIGATOIREMENT**\n   🚫 Jamais en *Marchandises / Biens*\n3️⃣ Reviens ici et envoie un *screenshot* de la confirmation\n⚠️ _Sans preuve = transaction non validée._",
"pay_link_error":   "⚠️ Erreur génération lien. Contacte l'admin.",
"crypto_pending":   "⏳ *Paiement en cours de vérification…*\n\nLe bot vérifie automatiquement la blockchain.\n🔔 Tu seras notifié dès confirmation.",
"send_proof":       "📸 Envoie un *screenshot* de ta confirmation PayPal.",
"proof_sent":       "📤 *Preuve envoyée à l'admin !*\n⏳ En attente de validation…",
"payment_ok_buyer": "✅ *Paiement validé !*\n🔔 Le vendeur va maintenant déposer ta livraison.",
"payment_ok_seller":"💰 *Paiement confirmé !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📦 *Article :* {item}\n📬 *Type :* {dtype}\n\n➡️ Envoie ta livraison ici.\n📎 _Fichier, lien, image ou texte_",
"payment_rejected": "❌ *Paiement non reçu.*\nRéessaie et renvoie un screenshot clair.",
"delivery_sent":    "📤 *Livraison envoyée à l'admin !*\n🔍 En cours de vérification…",
"delivery_ok_buyer":"✅ *Ta livraison est arrivée !*\n📦 *{item}*",
"tx_complete_buyer":"🎉 *Transaction terminée !*\nMerci d'avoir utilisé *EscrowBot* ! 🙌",
"delivery_rejected":"❌ *Livraison rejetée.*\n🚫 Contenu non conforme.\n➡️ Renvoie la bonne livraison.",
"withdraw_crypto":  "🪙 *Retrait Crypto*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nIndique ton adresse de réception :\n• USDT TRC20 / ERC20\n• BTC / ETH / SOL…\n📋 _Copie-colle ton adresse_",
"withdraw_paypal":  "💳 *Retrait PayPal*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nIndique ton PayPal.me ou email PayPal :",
"withdraw_ok":      "✅ *Adresse enregistrée !*\n💰 `{amount} €`\n📲 {method}\n📋 `{address}`\n⏳ L'admin traite ton retrait.",
"payout_sent":      "💰 *Paiement envoyé !*\n✅ *{amount} €* sur ton compte.\nMerci d'avoir utilisé *EscrowBot* ! 🙌",
"refund_prompt":    "💸 *Demande de remboursement*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ Pour raisons légales (anti-escroquerie/anti-blanchiment), le remboursement sera effectué *uniquement sur le compte ayant servi au paiement*.\n\n❓ *Confirmes-tu ?*",
"refund_crypto_addr":"🪙 *Remboursement Crypto*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nIndique ton adresse crypto pour recevoir le remboursement.\n⚠️ *Même devise que ton paiement initial.*\n📋 _Copie-colle ton adresse_",
"btn_refund_yes":   "✅  Oui, demander le remboursement",
"btn_refund_no":    "❌  Non, annuler",
"refund_sent":      "📨 *Demande envoyée à l'admin.*\n⏳ Remboursement sur ton compte d'origine.",
"refund_done":      "💸 *Remboursement en cours !*\nTu recevras la somme sur ton compte de paiement. 🙏",
"no_sessions":      "😕 Tu n'as pas encore de sessions.",
"invalid_amount":   "❌ Montant invalide. Ex: `50` ou `49.99`",
"announce_off":     "🔴 *EscrowBot — Hors ligne*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nL'admin n'est plus disponible.\n⏳ _Transactions suspendues. Reviens bientôt !_",
"announce_on":      "🟢 *EscrowBot — De retour en ligne !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Un admin est disponible.\n👇 Tu peux reprendre tes échanges !",
"announce_prompt":  "📢 *Annonce*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nÉcris le message à envoyer à tous les utilisateurs :",
"announce_sent":    "✅ *Annonce envoyée à {count} utilisateurs.*",
},
"en": {
"choose_lang":      "🌍 *Choose your language:*",
"welcome":          "👋 *Hello {name}!*\n\n🔐 *WELCOME TO ESCROWBOT*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⭐ *Your trusted middleman*\n_Every transaction protected from A to Z._\n\n✅ *Funds held* until delivery\n🔍 *Delivery verified* by admin\n💸 *Flat fee: 10%* only\n⚡ *Fast, safe, discreet*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n👇 *What do you want to do?*",
"bot_offline":      "🔴 *EscrowBot is offline.*\nNo admin available.\n⏳ _Come back later!_",
"btn_create":       "🛒  Create a session  ·  Buyer",
"btn_join":         "🔑  Join a session  ·  Seller",
"btn_sessions":     "📋  My sessions",
"btn_howto":        "ℹ️  How does it work?",
"btn_server":       "💬  Join the server",
"btn_scamlist":     "🚫  Scam List",
"btn_admin":        "👑  Admin Panel",
"btn_back":         "⬅️  Back to menu",
"howto":            "ℹ️ *How does EscrowBot work?*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n1️⃣ Buyer creates a session\n2️⃣ Shares code with seller\n3️⃣ Seller joins\n4️⃣ Buyer sets conditions\n5️⃣ Buyer pays via secure link\n6️⃣ Admin validates payment ✅\n7️⃣ Seller delivers\n8️⃣ Admin checks & validates 🔍\n9️⃣ Seller receives payment 💰\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💸 Fee: *10%* charged to buyer\n🔒 No money moves without admin approval!",
"scamlist_empty":   "🚫 *Scam List*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ No scammers listed.",
"scamlist_title":   "🚫 *Scam List — People to avoid*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ _These people have been reported._\n\n",
"step1":            "🛒 *Create a session — Step 1/5*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nWhat is the item name/description?",
"step2":            "📬 *Step 2/5 — Delivery type*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Item: *{item}*\nWhat delivery type do you expect?",
"step3":            "💵 *Step 3/5 — Price*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Delivery: *{dtype}*\nHow much should the seller be paid?\n💡 _Ex: 50 or 49.99_\n⚠️ _10% fee added automatically_",
"step4":            "💳 *Step 4/5 — Payment*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💰 *Summary:*\n  💵 Seller price → `{seller} €`\n  📊 {fee_label}    → `{fees} €`\n  ━━━━━━━━━━━━━━━━━\n  💳 *Total       → `{total} €`*\nPayment method?",
"step5":            "📋 *Step 5/5 — Conditions*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚖️ Set the conditions for this transaction.\nThey'll be written on the *session contract*.\n✍️ Write your conditions or click *Skip.*\n💡 _Conditions must be agreed upon in advance in private._",
"btn_skip_cond":    "⏩  Skip conditions",
"btn_exchange":     "🔁  Tech exchange — €5 fee",
"free_ready":       "🔔 *Seller joined your session!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🆓 *Your session is FREE — no payment required!*\n🧑‍💼 *Seller:* {seller}\n\n📦 The seller will send the delivery directly.",
"tx_complete_seller":"🎉 *Exchange complete!*\nThank you for using *EscrowBot*! 🙌",
"exchange_deposit": "📦 *Exchange — Deposit your tech!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nSend your tech (file, link, image or text).\n⏳ Waiting for both tech items, then admin validates.",
"exchange_deposited":"📤 *Your tech is deposited!*\n⏳ Waiting for the other side…",
"exchange_ready":   "🔁 *Exchange ready!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nBoth tech items are deposited.\n🔍 Admin validates the exchange.",
"session_created":  "🎉 *Session created!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{summary}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📤 *Code for the seller:*\n\n```\n{code}\n```\n⏳ _Waiting for seller…_",
"join_prompt":      "🔑 *Join a session*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nEnter the session code:",
"code_not_found":   "❌ *Code not found!*\nCheck and try again.",
"code_used":        "⛔ *Code already used!*",
"joined_ok":        "✅ *You joined the session!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{summary}\n⏳ Waiting for buyer's payment…",
"pay_crypto_msg":   "🔔 *Seller joined your session!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🧑‍💼 *Seller:* {seller}\n💰 *Amount: `{total} €`*\n🪙 *Method: Crypto*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ *NETWORK FEES:* Crypto payments include network fees (gas fees) not included in the amount. These are at your expense and depend on the blockchain.\n\n🔗 Click the button to pay.\n\n🚨 *Do NOT leave the payment page until the bot or blockchain confirms. Even the admin has no access to funds before validation.*",
"pay_paypal_msg":   "🔔 *Seller joined your session!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🧑‍💼 *Seller:* {seller}\n💰 *Amount: `{total} €`*\n💳 *Method: PayPal*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n1️⃣ Click the button to pay\n2️⃣ ⚠️ *IMPORTANT:* Pay with *FRIENDS & FAMILY* — **MANDATORY**\n   🚫 Never as *Goods & Services*\n3️⃣ Come back and send a *screenshot*\n⚠️ _Without proof = not validated._",
"pay_link_error":   "⚠️ Payment link error. Contact admin.",
"crypto_pending":   "⏳ *Payment being verified…*\nBot is automatically checking the blockchain.\n🔔 You'll be notified once confirmed.",
"send_proof":       "📸 Send a *screenshot* of your PayPal confirmation.",
"proof_sent":       "📤 *Proof sent to admin!*\n⏳ Waiting for validation…",
"payment_ok_buyer": "✅ *Payment validated!*\n🔔 The seller will now deliver.",
"payment_ok_seller":"💰 *Payment confirmed!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📦 *Item:* {item}\n📬 *Type:* {dtype}\n\n➡️ Send your delivery here.\n📎 _File, link, image or text_",
"payment_rejected": "❌ *Payment not received.*\nRetry and send a clear screenshot.",
"delivery_sent":    "📤 *Delivery sent to admin!*\n🔍 Under review…",
"delivery_ok_buyer":"✅ *Your delivery arrived!*\n📦 *{item}*",
"tx_complete_buyer":"🎉 *Transaction complete!*\nThank you for using *EscrowBot*! 🙌",
"delivery_rejected":"❌ *Delivery rejected.*\n🚫 Content doesn't match.\n➡️ Send the correct delivery.",
"withdraw_crypto":  "🪙 *Crypto Withdrawal*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nEnter your receiving address:\n• USDT TRC20 / ERC20\n• BTC / ETH / SOL…",
"withdraw_paypal":  "💳 *PayPal Withdrawal*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nEnter your PayPal.me or email:",
"withdraw_ok":      "✅ *Address registered!*\n💰 `{amount} €`\n📲 {method}\n📋 `{address}`\n⏳ Admin will process shortly.",
"payout_sent":      "💰 *Payment sent!*\n✅ *{amount} €* to your account.\nThank you for using *EscrowBot*! 🙌",
"refund_prompt":    "💸 *Refund Request*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ For legal reasons (anti-fraud/AML), refund will be sent *only to the original payment account*.\n\n❓ *Confirm?*",
"refund_crypto_addr":"🪙 *Crypto Refund*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nEnter your crypto address for the refund.\n⚠️ *Same currency as your original payment.*",
"btn_refund_yes":   "✅  Yes, request refund",
"btn_refund_no":    "❌  No, cancel",
"refund_sent":      "📨 *Request sent to admin.*\n⏳ Refund to your original account.",
"refund_done":      "💸 *Refund in progress!*\nYou'll receive the amount on your payment account. 🙏",
"no_sessions":      "😕 You don't have any sessions.",
"invalid_amount":   "❌ Invalid amount. Ex: `50` or `49.99`",
"announce_off":     "🔴 *EscrowBot — Offline*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nAdmin unavailable.\n⏳ _Transactions suspended. Come back soon!_",
"announce_on":      "🟢 *EscrowBot — Back online!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Admin is available.\n👇 You can trade safely!",
"announce_prompt":  "📢 *Announcement*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nWrite the message to send to all users:",
"announce_sent":    "✅ *Announcement sent to {count} users.*",
},
"ru": {
"choose_lang":      "🌍 *Выберите язык:*",
"welcome":          "👋 *Привет, {name}!*\n\n🔐 *ДОБРО ПОЖАЛОВАТЬ В ESCROWBOT*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⭐ *Надёжный посредник*\n_Каждая сделка защищена от начала до конца._\n\n✅ *Средства заморожены* до поставки\n🔍 *Поставка проверяется* администратором\n💸 *Фиксированная комиссия: 10%*\n⚡ *Быстро, безопасно, конфиденциально*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n👇 *Что вы хотите сделать?*",
"bot_offline":      "🔴 *EscrowBot не в сети.*\nАдминистратор недоступен.\n⏳ _Вернитесь позже!_",
"btn_create":       "🛒  Создать сессию  ·  Покупатель",
"btn_join":         "🔑  Присоединиться  ·  Продавец",
"btn_sessions":     "📋  Мои сессии",
"btn_howto":        "ℹ️  Как это работает?",
"btn_server":       "💬  Присоединиться к серверу",
"btn_scamlist":     "🚫  Список скамеров",
"btn_admin":        "👑  Панель администратора",
"btn_back":         "⬅️  Назад в меню",
"howto":            "ℹ️ *Как работает EscrowBot?*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n1️⃣ Покупатель создаёт сессию\n2️⃣ Передаёт код продавцу\n3️⃣ Продавец присоединяется\n4️⃣ Покупатель задаёт условия\n5️⃣ Оплата по защищённой ссылке\n6️⃣ Администратор подтверждает ✅\n7️⃣ Продавец отправляет товар\n8️⃣ Администратор проверяет 🔍\n9️⃣ Продавец получает оплату 💰\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💸 Комиссия: *10%*\n🔒 Деньги не переводятся без одобрения!",
"scamlist_empty":   "🚫 *Список скамеров*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Список пуст.",
"scamlist_title":   "🚫 *Список скамеров*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ _Эти люди замечены в мошенничестве._\n\n",
"step1":            "🛒 *Создать сессию — Шаг 1/5*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nКакой товар вы покупаете?",
"step2":            "📬 *Шаг 2/5 — Тип доставки*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Товар: *{item}*\nКакой тип доставки ожидаете?",
"step3":            "💵 *Шаг 3/5 — Цена*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Доставка: *{dtype}*\nСколько получит продавец?\n💡 _Пример: 50 или 49.99_\n⚠️ _Комиссия 10% добавится автоматически_",
"step4":            "💳 *Шаг 4/5 — Оплата*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💰 *Итого:*\n  💵 Продавец  → `{seller} €`\n  📊 {fee_label}  → `{fees} €`\n  ━━━━━━━━━━━━━━━━━\n  💳 *Итого    → `{total} €`*\nСпособ оплаты?",
"step5":            "📋 *Шаг 5/5 — Условия*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚖️ Укажите условия сделки.\nОни будут записаны в *контракт сессии*.\n✍️ Напишите условия или нажмите *Пропустить.*\n💡 _Условия согласовываются заранее._",
"btn_skip_cond":    "⏩  Пропустить условия",
"btn_exchange":     "🔁  Обмен техникой — 5€",
"free_ready":       "🔔 *Продавец присоединился!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🆓 *Ваша сессия БЕСПЛАТНА — оплата не требуется!*\n🧑‍💼 *Продавец:* {seller}\n\n📦 Продавец отправит товар напрямую.",
"tx_complete_seller":"🎉 *Обмен завершён!*\nСпасибо за использование *EscrowBot*! 🙌",
"exchange_deposit": "📦 *Обмен — Отправьте свою технику!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nОтправьте технику (файл, ссылку, фото или текст).\n⏳ Ждём технику с обеих сторон, затем админ подтвердит.",
"exchange_deposited":"📤 *Ваша техника отправлена!*\n⏳ Ожидание техники с другой стороны…",
"exchange_ready":   "🔁 *Обмен готов!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nТехника получена с обеих сторон.\n🔍 Админ подтверждает обмен.",
"session_created":  "🎉 *Сессия создана!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{summary}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📤 *Код для продавца:*\n\n```\n{code}\n```\n⏳ _Ожидание продавца…_",
"join_prompt":      "🔑 *Присоединиться к сессии*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nВведите код сессии:",
"code_not_found":   "❌ *Код не найден!*\nПроверьте и попробуйте снова.",
"code_used":        "⛔ *Код уже используется!*",
"joined_ok":        "✅ *Вы вошли в сессию!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{summary}\n⏳ Ожидание оплаты…",
"pay_crypto_msg":   "🔔 *Продавец присоединился!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🧑‍💼 *Продавец:* {seller}\n💰 *Сумма: `{total} €`*\n🪙 *Метод: Крипто*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ *КОМИССИЯ СЕТИ:* Криптоплатежи включают сетевые комиссии (gas fees), не включённые в сумму. Эти расходы несёт покупатель.\n\n🔗 Нажмите кнопку для оплаты.\n\n🚨 *Не покидайте страницу до подтверждения ботом или блокчейном.*",
"pay_paypal_msg":   "🔔 *Продавец присоединился!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🧑‍💼 *Продавец:* {seller}\n💰 *Сумма: `{total} €`*\n💳 *Метод: PayPal*\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n1️⃣ Нажмите кнопку для оплаты\n2️⃣ ⚠️ *ВАЖНО:* Оплата строго *ДРУЗЬЯМ И РОДСТВЕННИКАМ (Friends & Family)* — **ОБЯЗАТЕЛЬНО**\n   🚫 Никогда не *Товары и услуги*\n3️⃣ Отправьте скриншот подтверждения\n⚠️ _Без доказательства — не подтверждено._",
"pay_link_error":   "⚠️ Ошибка генерации ссылки. Обратитесь к администратору.",
"crypto_pending":   "⏳ *Проверка платежа…*\nБот автоматически проверяет блокчейн.\n🔔 Уведомление после подтверждения.",
"send_proof":       "📸 Отправьте скриншот подтверждения PayPal.",
"proof_sent":       "📤 *Доказательство отправлено!*\n⏳ Ожидание подтверждения…",
"payment_ok_buyer": "✅ *Оплата подтверждена!*\n🔔 Продавец скоро отправит товар.",
"payment_ok_seller":"💰 *Оплата подтверждена!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📦 *Товар:* {item}\n📬 *Тип:* {dtype}\n\n➡️ Отправьте товар сюда.\n📎 _Файл, ссылка, фото или текст_",
"payment_rejected": "❌ *Оплата не получена.*\nПовторите и отправьте чёткий скриншот.",
"delivery_sent":    "📤 *Товар отправлен на проверку!*\n🔍 Проверяется…",
"delivery_ok_buyer":"✅ *Ваш товар получен!*\n📦 *{item}*",
"tx_complete_buyer":"🎉 *Сделка завершена!*\nСпасибо за использование *EscrowBot*! 🙌",
"delivery_rejected":"❌ *Товар отклонён.*\n🚫 Содержимое не соответствует.\n➡️ Отправьте правильный товар.",
"withdraw_crypto":  "🪙 *Вывод в крипто*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nУкажите адрес получения:\n• USDT TRC20 / ERC20\n• BTC / ETH / SOL…",
"withdraw_paypal":  "💳 *Вывод через PayPal*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nУкажите PayPal.me или email:",
"withdraw_ok":      "✅ *Адрес зарегистрирован!*\n💰 `{amount} €`\n📲 {method}\n📋 `{address}`\n⏳ Обрабатывается.",
"payout_sent":      "💰 *Платёж отправлен!*\n✅ *{amount} €* на ваш счёт. 🙌",
"refund_prompt":    "💸 *Запрос на возврат*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ По юридическим причинам (AML) возврат только на исходный счёт.\n\n❓ *Подтвердить?*",
"refund_crypto_addr":"🪙 *Возврат Крипто*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nУкажите адрес для возврата.\n⚠️ *Та же валюта, что при оплате.*",
"btn_refund_yes":   "✅  Да, запросить возврат",
"btn_refund_no":    "❌  Нет, отмена",
"refund_sent":      "📨 *Запрос отправлен администратору.*\n⏳ Возврат на исходный счёт.",
"refund_done":      "💸 *Возврат обрабатывается!* 🙏",
"no_sessions":      "😕 У вас нет сессий.",
"invalid_amount":   "❌ Неверная сумма. Пример: `50`",
"announce_off":     "🔴 *EscrowBot — Не в сети*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nАдминистратор недоступен.\n⏳ _Сделки приостановлены._",
"announce_on":      "🟢 *EscrowBot — Снова в сети!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Администратор доступен.",
"announce_prompt":  "📢 *Объявление*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\nНапишите сообщение для всех пользователей:",
"announce_sent":    "✅ *Объявление отправлено {count} пользователям.*",
},
}

def t(uid: int, key: str, **kw) -> str:
    lang = user_lang.get(uid, "fr")
    text = LANG.get(lang, LANG["fr"]).get(key, LANG["fr"].get(key, key))
    return text.format(**kw) if kw else text

# ──────────────────────────────────────────────
#  ÉTATS
# ──────────────────────────────────────────────
(CHOOSE_LANG, BUY_ITEM_NAME, BUY_DELIVERY_TYPE,
 BUY_PRICE, BUY_PAYMENT_METHOD, BUY_CONDITIONS,
 SELLER_ENTER_CODE, REFUND_CRYPTO_ADDR,
 ADMIN_ANNOUNCE) = range(9)

# ══════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════

def gen_code(username: str) -> str:
    tag   = (username or "USER")[:8].upper()
    short = str(uuid.uuid4())[:6].upper()
    return f"{tag}-{short}"

def fmt(v: float) -> str:
    return f"{v:.2f}"

def esc(s) -> str:
    """Échappe les caractères Markdown pour éviter les erreurs de parse (boutons morts)."""
    return str(s).replace("_", r"\_").replace("*", r"\*").replace("[", r"\[").replace("`", r"\`")

def compute_fee(uid: int, sp: float, is_exchange: bool) -> tuple[float, str]:
    """Retourne (frais, label). 1ère session de chaque user = offerte."""
    if uid not in user_first_free:
        user_first_free.add(uid)
        return 0.0, "OFFERT 🎁"
    if is_exchange:
        return 5.0, "Frais fixes (5€)"
    return sp * FEES_PCT, "Frais (10%)"

def session_summary(s: dict) -> str:
    de = {"Lien URL":"🔗","PDF":"📄","Image":"🖼️","Texte / Code":"📝"}.get(s.get("delivery_type",""),"📦")
    pe = "🪙" if s.get("payment_method")=="Crypto" else "💳"
    cond = s.get("conditions","")
    cl   = f"\n📋 *Conditions :* _{esc(cond)}_" if cond else ""
    return (
        f"╔══════════════════════════╗\n"
        f"        📋 *SESSION ESCROW*\n"
        f"╚══════════════════════════╝\n\n"
        f"🔑 *Code :* `{s['code']}`\n"
        f"🛒 *Article :* {esc(s['item_name'])}\n"
        f"{de} *Livraison :* {s['delivery_type']}\n"
        f"💵 *Prix vendeur :* `{fmt(s['seller_price'])} €`\n"
        f"💰 *Total (frais inclus) :* `{fmt(s['total_price'])} €`\n"
        f"💸 *Frais :* {esc(s.get('fee_label',''))}\n"
        f"{pe} *Paiement :* {s['payment_method']}\n"
        f"👤 *Acheteur :* {esc(s.get('buyer_name','?'))} | ID: `{s.get('buyer_id','?')}`\n"
        f"🧑‍💼 *Vendeur :* {esc(s.get('seller_name','⏳'))} | ID: `{s.get('seller_id','—')}`\n"
        f"📊 *Statut :* {s['status']}\n"
        f"🕒 *Créée le :* {s.get('created_at','?')[:10]}"
        f"{cl}\n"
    )

def main_kb(uid: int) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(t(uid,"btn_create"),   callback_data="create_session")],
        [InlineKeyboardButton(t(uid,"btn_join"),     callback_data="join_session")],
        [InlineKeyboardButton(t(uid,"btn_sessions"), callback_data="my_sessions")],
        [InlineKeyboardButton(t(uid,"btn_howto"),    callback_data="how_it_works")],
        [
            InlineKeyboardButton(t(uid,"btn_server"),   url=SERVER_LINK),
            InlineKeyboardButton(t(uid,"btn_scamlist"), callback_data="scam_list"),
        ],
    ]
    if uid == ADMIN_ID:
        kb.append([InlineKeyboardButton(t(uid,"btn_admin"), callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def payment_kb(uid: int, code: str, pay_link: str, method: str, total: str) -> InlineKeyboardMarkup:
    """Clavier de paiement réutilisable."""
    if method == "Crypto":
        kb = [[InlineKeyboardButton(f"🪙  Payer {total} € en Crypto", url=pay_link)]]
    else:
        kb = [[InlineKeyboardButton(f"💳  Payer {total} € via PayPal", url=pay_link)]]
    kb.append([InlineKeyboardButton("💸  Demande de remboursement", callback_data=f"refund_ask_{code}")])
    return InlineKeyboardMarkup(kb)

async def generate_oxapay_link(amount: float, order_id: str) -> tuple[str | None, str | None]:
    """Retourne (payLink, trackId) ou (None, None)."""
    payload = {
        "merchant":    OXAPAY_KEY,
        "amount":      amount,
        "currency":    "EUR",
        "lifeTime":    60,
        "orderId":     order_id,
        "description": f"EscrowBot — {order_id}",
    }
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(OXAPAY_API, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
                if data.get("result") == 100:
                    return data.get("payLink"), str(data.get("trackId",""))
    except Exception as e:
        logger.error(f"OxaPay create error: {e}")
    return None, None

async def check_oxapay_payment(track_id: str) -> bool:
    """Retourne True si le paiement est confirmé (tous statuts valides OxaPay)."""
    payload = {"merchant": OXAPAY_KEY, "trackId": track_id}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(OXAPAY_INQ, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                status = data.get("status", "")
                # Statuts OxaPay confirmant un paiement reçu
                return data.get("result") == 100 and status in (
                    "Paid", "Completed", "confirmed", "Complete", "paid"
                )
    except Exception as e:
        logger.error(f"OxaPay inquiry error: {e}")
    return False

async def _payment_validated(s: dict, bot):
    """Après paiement validé : gère vente (1 livraison) et échange (2 tech)."""
    buid = s["buyer_id"]; suid = s["seller_id"]
    if s.get("is_exchange"):
        s["status"] = "⏳ En attente des livraisons"
        await bot.send_message(chat_id=suid, text=t(suid,"exchange_deposit"), parse_mode="Markdown")
        await bot.send_message(chat_id=buid, text=t(buid,"exchange_deposit"), parse_mode="Markdown")
    else:
        s["status"] = "⏳ En attente de la livraison vendeur"
        await bot.send_message(chat_id=buid, text=t(buid,"payment_ok_buyer"), parse_mode="Markdown")
        await bot.send_message(chat_id=suid,
            text=t(suid, "payment_ok_seller", item=esc(s["item_name"]), dtype=s["delivery_type"]),
            parse_mode="Markdown")
    await bot.send_message(chat_id=ADMIN_ID,
        text=f"✅ *Paiement validé !*\n`{s['code']}`\n\n{session_summary(s)}",
        parse_mode="Markdown")
    save_state()

def store_delivery(msg):
    if msg.document: return {"type":"document","file_id":msg.document.file_id}
    if msg.photo:    return {"type":"photo","file_id":msg.photo[-1].file_id}
    if msg.text:     return {"type":"text","content":msg.text}
    return None

async def _send_delivery_to(bot, chat_id: int, di: dict, cap: str):
    if di.get("type")=="document":
        await bot.send_document(chat_id=chat_id,document=di["file_id"],caption=cap,parse_mode="Markdown")
    elif di.get("type")=="photo":
        await bot.send_photo(chat_id=chat_id,photo=di["file_id"],caption=cap,parse_mode="Markdown")
    elif di.get("type")=="text":
        await bot.send_message(chat_id=chat_id,text=f"{cap}\n\n📝 *Contenu :*\n\n{esc(di['content'])}",parse_mode="Markdown")

async def _validate_crypto_payment(code: str, bot):
    """Valide un paiement crypto et notifie toutes les parties."""
    s = sessions.get(code)
    if not s or s["status"] != "⏳ En attente du paiement acheteur":
        return
    if s.get("payment_method") != "Crypto":
        return

    await _payment_validated(s, bot)
    logger.info(f"OxaPay payment validated: {code}")

async def oxapay_ipn_handler(request: web.Request) -> web.Response:
    """
    Webhook IPN OxaPay — appelé INSTANTANÉMENT par OxaPay dès qu'un paiement arrive.
    OxaPay envoie un POST JSON avec trackId et status.
    URL à configurer dans ton dashboard OxaPay : https://TON-PROJET.railway.app/oxapay-ipn
    """
    try:
        body = await request.read()

        # Vérification signature HMAC si secret configuré
        if OXAPAY_IPN_SECRET:
            sig = request.headers.get("hmac", "")
            expected = hmac.new(
                OXAPAY_IPN_SECRET.encode(), body, hashlib.sha512
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                logger.warning("OxaPay IPN: signature invalide")
                return web.Response(status=403, text="Forbidden")

        data   = json.loads(body)
        status = data.get("status", "")
        order_id = data.get("orderId", "")   # = le code de session
        track_id = str(data.get("trackId", ""))

        logger.info(f"OxaPay IPN reçu — orderId={order_id} status={status} trackId={track_id}")

        if status in ("Paid", "Completed", "Complete", "confirmed", "paid") and _app:
            # Cherche par orderId (= code session) ou trackId
            s = sessions.get(order_id)
            if not s and track_id:
                s = next((x for x in sessions.values() if x.get("track_id") == track_id), None)
                if s:
                    order_id = s["code"]

            if s:
                await _validate_crypto_payment(order_id, _app.bot)

    except Exception as e:
        logger.error(f"OxaPay IPN error: {e}")

    # OxaPay attend toujours un 200
    return web.Response(status=200, text="OK")

async def oxapay_polling(context: ContextTypes.DEFAULT_TYPE):
    """
    Job backup toutes les 15s — vérifie les paiements crypto en attente.
    Sert de filet de sécurité si l'IPN webhook n'a pas été déclenché.
    """
    for code, s in list(sessions.items()):
        if s["status"] != "⏳ En attente du paiement acheteur": continue
        if s.get("payment_method") != "Crypto": continue
        track_id = s.get("track_id")
        if not track_id: continue
        paid = await check_oxapay_payment(track_id)
        if paid:
            await _validate_crypto_payment(code, context.bot)

# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in all_users:
        all_users.add(uid)
        save_state()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇫🇷  Français", callback_data="lang_fr")],
        [InlineKeyboardButton("🇬🇧  English",  callback_data="lang_en")],
        [InlineKeyboardButton("🇷🇺  Русский",  callback_data="lang_ru")],
    ])
    await update.message.reply_text("🌍 Choisis ta langue / Choose your language / Выберите язык :", reply_markup=kb)
    return CHOOSE_LANG

async def set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid  = q.from_user.id; name = q.from_user.first_name or ""
    user_lang[uid] = q.data.replace("lang_","")
    save_state()
    if not bot_online and uid != ADMIN_ID:
        await q.edit_message_text(t(uid,"bot_offline"), parse_mode="Markdown")
        return ConversationHandler.END
    await q.edit_message_text(t(uid,"welcome",name=esc(name)), parse_mode="Markdown", reply_markup=main_kb(uid))
    return ConversationHandler.END

# ══════════════════════════════════════════════
#  SCAM LIST
# ══════════════════════════════════════════════

async def show_scam_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    kb  = InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"btn_back"), callback_data="back_start")]])
    if not scam_list:
        await q.edit_message_text(t(uid,"scamlist_empty"), parse_mode="Markdown", reply_markup=kb); return
    txt = t(uid,"scamlist_title")
    for i, e in enumerate(scam_list, 1):
        txt += f"*{i}.* 👤 {esc(e['username'])} | ID: `{esc(e['id'])}`\n   📅 {esc(e['date'])}\n   ❗ _{esc(e['reason'])}_\n\n"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)

async def cmd_addscam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage: `/addscam @pseudo ID raison`", parse_mode="Markdown"); return
    scam_list.append({"username":args[0],"id":args[1],"reason":" ".join(args[2:]),"date":datetime.now().strftime("%d/%m/%Y")})
    save_state()
    await update.message.reply_text(f"✅ *{args[0]}* ajouté à la Scam List.", parse_mode="Markdown")

async def cmd_removescam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage: `/removescam @pseudo`", parse_mode="Markdown"); return
    before = len(scam_list)
    scam_list[:] = [e for e in scam_list if e["username"] != context.args[0]]
    save_state()
    msg = f"✅ *{context.args[0]}* retiré." if len(scam_list) < before else f"❌ *{context.args[0]}* non trouvé."
    await update.message.reply_text(msg, parse_mode="Markdown")

# ══════════════════════════════════════════════
#  COMMENT ÇA MARCHE
# ══════════════════════════════════════════════

async def how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = q.from_user.id
    await q.edit_message_text(t(uid,"howto"), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"btn_back"), callback_data="back_start")]]))

# ══════════════════════════════════════════════
#  CRÉER SESSION
# ══════════════════════════════════════════════

async def create_session_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = q.from_user.id
    if not bot_online and uid != ADMIN_ID:
        await q.edit_message_text(t(uid,"bot_offline"), parse_mode="Markdown")
        return ConversationHandler.END
    await q.edit_message_text(t(uid,"step1"), parse_mode="Markdown")
    return BUY_ITEM_NAME

async def buy_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data["item_name"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗  Lien URL",             callback_data="del_url")],
        [InlineKeyboardButton("📄  Fichier PDF",          callback_data="del_pdf")],
        [InlineKeyboardButton("🖼️  Image / Photo",        callback_data="del_image")],
        [InlineKeyboardButton("📝  Texte / Code / Accès", callback_data="del_text")],
        [InlineKeyboardButton(t(uid,"btn_exchange"),      callback_data="mode_exchange")],
    ])
    await update.message.reply_text(t(uid,"step2",item=esc(context.user_data["item_name"])),
        parse_mode="Markdown", reply_markup=kb)
    return BUY_DELIVERY_TYPE

async def buy_delivery_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = q.from_user.id
    context.user_data["delivery_type"] = {"del_url":"Lien URL","del_pdf":"PDF","del_image":"Image","del_text":"Texte / Code"}[q.data]
    await q.edit_message_text(t(uid,"step3",dtype=context.user_data["delivery_type"]), parse_mode="Markdown")
    return BUY_PRICE

async def buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        sp = float(update.message.text.replace(",",".").strip())
        if sp <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text(t(uid,"invalid_amount"), parse_mode="Markdown")
        return BUY_PRICE
    fee, fee_label = compute_fee(uid, sp, False)
    total = sp + fee
    context.user_data["seller_price"] = sp
    context.user_data["total_price"]  = total
    context.user_data["fee_label"]    = fee_label
    context.user_data["is_exchange"]  = False
    if total <= 0:
        context.user_data["payment_method"] = "Gratuit"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"btn_skip_cond"), callback_data="skip_conditions")]])
        await update.message.reply_text(t(uid,"step5"), parse_mode="Markdown", reply_markup=kb)
        return BUY_CONDITIONS
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🪙  Crypto (Multi-devises)", callback_data="pay_crypto")],
        [InlineKeyboardButton("💳  PayPal",                 callback_data="pay_paypal")],
    ])
    await update.message.reply_text(
        t(uid,"step4",seller=fmt(sp),fees=fmt(fee),total=fmt(total),fee_label=fee_label),
        parse_mode="Markdown", reply_markup=kb)
    return BUY_PAYMENT_METHOD

async def buy_exchange_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = q.from_user.id
    fee, fee_label = compute_fee(uid, 0.0, True)
    total = fee
    context.user_data["delivery_type"] = "Échange de tech"
    context.user_data["seller_price"]  = 0.0
    context.user_data["total_price"]   = total
    context.user_data["fee_label"]     = fee_label
    context.user_data["is_exchange"]   = True
    if total <= 0:
        context.user_data["payment_method"] = "Gratuit"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"btn_skip_cond"), callback_data="skip_conditions")]])
        await q.edit_message_text(t(uid,"step5"), parse_mode="Markdown", reply_markup=kb)
        return BUY_CONDITIONS
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🪙  Crypto (Multi-devises)", callback_data="pay_crypto")],
        [InlineKeyboardButton("💳  PayPal",                 callback_data="pay_paypal")],
    ])
    await q.edit_message_text(
        t(uid,"step4",seller=fmt(0.0),fees=fmt(fee),total=fmt(total),fee_label=fee_label),
        parse_mode="Markdown", reply_markup=kb)
    return BUY_PAYMENT_METHOD

async def buy_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = q.from_user.id
    context.user_data["payment_method"] = "Crypto" if q.data=="pay_crypto" else "PayPal"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"btn_skip_cond"), callback_data="skip_conditions")]])
    await q.edit_message_text(t(uid,"step5"), parse_mode="Markdown", reply_markup=kb)
    return BUY_CONDITIONS

async def buy_conditions_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; user = update.effective_user
    context.user_data["conditions"] = update.message.text.strip()
    await finalize_session(uid, user, context, update.message.reply_text)
    return ConversationHandler.END

async def buy_conditions_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = q.from_user.id; user = q.from_user
    context.user_data["conditions"] = ""
    await finalize_session(uid, user, context, q.edit_message_text)
    return ConversationHandler.END

async def finalize_session(uid, user, context, reply_fn):
    ud    = context.user_data
    code  = gen_code(user.username or str(uid))
    uname = f"@{user.username}" if user.username else user.first_name
    s = {
        "code":           code,
        "item_name":      ud["item_name"],
        "delivery_type":  ud["delivery_type"],
        "seller_price":   ud["seller_price"],
        "total_price":    ud["total_price"],
        "payment_method": ud.get("payment_method", "Gratuit"),
        "is_exchange":   ud.get("is_exchange", False),
        "fee_label":     ud.get("fee_label", "Frais (10%)"),
        "conditions":     ud.get("conditions",""),
        "buyer_id":       uid,
        "buyer_name":     uname,
        "seller_id":      None,
        "seller_name":    None,
        "status":         "⏳ En attente du vendeur",
        "delivery_file":  None,
        "payment_proof":  None,
        "pay_link":       None,
        "track_id":       None,
        "created_at":     datetime.now().isoformat(),
    }
    sessions[code]     = s
    user_sessions[uid] = code
    save_state()
    await reply_fn(t(uid,"session_created",summary=session_summary(s),code=code), parse_mode="Markdown")
    await context.bot.send_message(chat_id=ADMIN_ID,
        text=f"🆕 *Nouvelle session !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{session_summary(s)}",
        parse_mode="Markdown")

# ══════════════════════════════════════════════
#  REJOINDRE SESSION
# ══════════════════════════════════════════════

async def join_session_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = q.from_user.id
    if not bot_online and uid != ADMIN_ID:
        await q.edit_message_text(t(uid,"bot_offline"), parse_mode="Markdown")
        return ConversationHandler.END
    await q.edit_message_text(t(uid,"join_prompt"), parse_mode="Markdown")
    return SELLER_ENTER_CODE

async def seller_enter_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id; user = update.effective_user
    code = update.message.text.strip().upper()
    s    = sessions.get(code)
    if not s:
        await update.message.reply_text(t(uid,"code_not_found"), parse_mode="Markdown")
        return SELLER_ENTER_CODE
    if s["seller_id"] is not None:
        await update.message.reply_text(t(uid,"code_used"), parse_mode="Markdown")
        return ConversationHandler.END

    s["seller_id"]     = uid
    s["seller_name"]   = f"@{user.username}" if user.username else user.first_name
    s["status"]        = "⏳ En attente du paiement acheteur"
    user_sessions[uid] = code

    await update.message.reply_text(t(uid,"joined_ok",summary=session_summary(s)), parse_mode="Markdown")

    buid  = s["buyer_id"]

    # Session offerte (1ère session gratuite) → pas de paiement requis
    if s["total_price"] <= 0:
        if s.get("is_exchange"):
            await _payment_validated(s, context.bot)
        else:
            s["status"] = "⏳ En attente de la livraison vendeur"
            await context.bot.send_message(chat_id=buid, text=t(buid,"free_ready",seller=esc(s["seller_name"])), parse_mode="Markdown")
            await context.bot.send_message(chat_id=s["seller_id"],
                text=t(s["seller_id"],"payment_ok_seller",item=esc(s["item_name"]),dtype=s["delivery_type"]),
                parse_mode="Markdown")
            await context.bot.send_message(chat_id=ADMIN_ID,
                text=f"👥 *Vendeur rejoint — session OFFERTE !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{session_summary(s)}",
                parse_mode="Markdown")
        save_state()
        return ConversationHandler.END

    total = fmt(s["total_price"])

    if s["payment_method"] == "Crypto":
        pay_link, track_id = await generate_oxapay_link(s["total_price"], code)
        if pay_link:
            s["pay_link"]  = pay_link
            s["track_id"]  = track_id
            pay_txt = t(buid,"pay_crypto_msg",seller=esc(s["seller_name"]),total=total)
            kb = payment_kb(buid, code, pay_link, "Crypto", total)
        else:
            pay_txt = t(buid,"pay_link_error")
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("💸  Remboursement", callback_data=f"refund_ask_{code}")]])
    else:
        pay_link = f"{PAYPAL_URL}/{total}"
        s["pay_link"] = pay_link
        pay_txt = t(buid,"pay_paypal_msg",seller=esc(s["seller_name"]),total=total)
        kb = payment_kb(buid, code, pay_link, "PayPal", total)

    await context.bot.send_message(chat_id=buid, text=pay_txt, parse_mode="Markdown", reply_markup=kb)
    await context.bot.send_message(chat_id=ADMIN_ID,
        text=f"👥 *Vendeur rejoint !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{session_summary(s)}",
        parse_mode="Markdown")
    save_state()
    return ConversationHandler.END

# ══════════════════════════════════════════════
#  REMBOURSEMENT — FIX: NON = retour au paiement
# ══════════════════════════════════════════════

async def refund_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid  = q.from_user.id
    code = q.data.replace("refund_ask_","")
    context.user_data["refund_code"] = code
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid,"btn_refund_yes"), callback_data=f"refund_confirm_{code}")],
        [InlineKeyboardButton(t(uid,"btn_refund_no"),  callback_data=f"back_to_pay_{code}")],
    ])
    await q.edit_message_text(t(uid,"refund_prompt"), parse_mode="Markdown", reply_markup=kb)

async def back_to_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retour à la page de paiement sans casser la session."""
    q = update.callback_query; await q.answer()
    uid  = q.from_user.id
    code = q.data.replace("back_to_pay_","")
    s    = sessions.get(code)
    if not s:
        await q.edit_message_text(t(uid,"bot_offline"), parse_mode="Markdown"); return
    total    = fmt(s["total_price"])
    pay_link = s.get("pay_link","")
    method   = s.get("payment_method","")
    if method == "Crypto":
        pay_txt = t(uid,"pay_crypto_msg",seller=esc(s["seller_name"]),total=total)
    else:
        pay_txt = t(uid,"pay_paypal_msg",seller=esc(s["seller_name"]),total=total)
    kb = payment_kb(uid, code, pay_link, method, total)
    await q.edit_message_text(pay_txt, parse_mode="Markdown", reply_markup=kb)

async def refund_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid  = q.from_user.id
    code = q.data.replace("refund_confirm_","")
    s    = sessions.get(code)
    if not s:
        await q.edit_message_text("❌ Session introuvable."); return

    # Si crypto → demander l'adresse de remboursement
    if s.get("payment_method") == "Crypto":
        context.user_data["refund_code"] = code
        await q.edit_message_text(t(uid,"refund_crypto_addr"), parse_mode="Markdown")
        return  # On attend l'adresse via route_message → handle_refund_crypto_addr

    # PayPal → envoyer directement à l'admin avec la preuve
    await _send_refund_to_admin(uid, code, s, None, context)
    await q.edit_message_text(t(uid,"refund_sent"), parse_mode="Markdown")

async def handle_refund_crypto_addr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reçoit l'adresse crypto de remboursement."""
    uid     = update.effective_user.id
    code    = context.user_data.get("refund_code")
    address = update.message.text.strip()
    s       = sessions.get(code)
    if not s:
        await update.message.reply_text("❌ Session introuvable."); return

    await _send_refund_to_admin(uid, code, s, address, context)
    await update.message.reply_text(t(uid,"refund_sent"), parse_mode="Markdown")

async def _send_refund_to_admin(uid, code, s, crypto_addr, context):
    proof     = s.get("payment_proof")
    addr_line = f"\n🪙 *Adresse remboursement :* `{esc(crypto_addr)}`" if crypto_addr else ""
    admin_txt = (
        f"💸 *DEMANDE DE REMBOURSEMENT !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{session_summary(s)}\n"
        f"👤 {esc(s['buyer_name'])} | ID: `{s['buyer_id']}`\n"
        f"💳 Méthode : {s['payment_method']}"
        f"{addr_line}\n\n"
        f"⚠️ _Rembourser uniquement sur le compte d'origine._"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅  J'ai remboursé !", callback_data=f"admin_refunded_{code}_{uid}")]])
    if proof:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=proof, caption=admin_txt, parse_mode="Markdown", reply_markup=kb)
    else:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_txt, parse_mode="Markdown", reply_markup=kb)
    # Supprimer la session
    sessions.pop(code, None)
    user_sessions.pop(uid, None)
    if s.get("seller_id") and user_sessions.get(s["seller_id"]) == code:
        user_sessions.pop(s["seller_id"], None)
    save_state()

async def admin_refunded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌",show_alert=True); return
    await q.answer()
    parts     = q.data.replace("admin_refunded_","").rsplit("_",1)
    buyer_uid = int(parts[-1])
    await context.bot.send_message(chat_id=buyer_uid, text=t(buyer_uid,"refund_done"), parse_mode="Markdown")
    await q.edit_message_text("✅ *Remboursement confirmé.*", parse_mode="Markdown")

# ══════════════════════════════════════════════
#  PREUVE PAYPAL
# ══════════════════════════════════════════════

async def handle_buyer_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    code = user_sessions.get(uid)
    if not code: return
    s = sessions.get(code)
    if not s or s["buyer_id"] != uid: return
    if s["status"] != "⏳ En attente du paiement acheteur": return
    if s.get("payment_method") != "PayPal": return

    if not update.message.photo:
        await update.message.reply_text(t(uid,"send_proof"), parse_mode="Markdown"); return

    fid = update.message.photo[-1].file_id
    s["payment_proof"] = fid
    s["status"]        = "🔍 Preuve de paiement en attente"
    await update.message.forward(chat_id=ADMIN_ID)
    await context.bot.send_message(chat_id=ADMIN_ID,
        text=f"💳 *Preuve PayPal !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{session_summary(s)}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅  Paiement reçu !", callback_data=f"admin_pay_ok_{code}"),
            InlineKeyboardButton("❌  Pas reçu",        callback_data=f"admin_pay_rej_{code}"),
        ]]))
    await update.message.reply_text(t(uid,"proof_sent"), parse_mode="Markdown")

# ══════════════════════════════════════════════
#  ADMIN PAIEMENT
# ══════════════════════════════════════════════

async def admin_pay_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌",show_alert=True); return
    await q.answer()
    code = q.data.replace("admin_pay_ok_","")
    s    = sessions.get(code)
    if not s: await q.edit_message_text("❌ Session introuvable."); return
    await _payment_validated(s, context.bot)
    await q.edit_message_text(f"✅ Paiement validé — `{code}`.", parse_mode="Markdown")

async def admin_pay_rej(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌",show_alert=True); return
    await q.answer()
    code = q.data.replace("admin_pay_rej_","")
    s    = sessions.get(code)
    if not s: await q.edit_message_text("❌ Session introuvable."); return
    s["status"] = "⏳ En attente du paiement acheteur"
    await context.bot.send_message(chat_id=s["buyer_id"], text=t(s["buyer_id"],"payment_rejected"), parse_mode="Markdown")
    save_state()
    await q.edit_message_text(f"❌ Paiement rejeté — `{code}`.", parse_mode="Markdown")

async def cmd_cryptook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage : /cryptook CODE"); return
    code = context.args[0].upper(); s = sessions.get(code)
    if not s: await update.message.reply_text("❌ Session introuvable."); return
    await _payment_validated(s, context.bot)
    await update.message.reply_text(f"✅ Crypto validé — `{code}`.", parse_mode="Markdown")

# ══════════════════════════════════════════════
#  LIVRAISON
# ══════════════════════════════════════════════

async def seller_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    code = user_sessions.get(uid)
    if not code: return
    s = sessions.get(code)
    if not s or s["seller_id"] != uid: return
    if s["status"] != "⏳ En attente de la livraison vendeur": return

    msg = update.message
    di  = store_delivery(msg)
    if not di: return

    s["delivery_file"] = di
    s["status"]        = "🔍 En attente validation livraison"
    save_state()
    await msg.forward(chat_id=ADMIN_ID)
    await context.bot.send_message(chat_id=ADMIN_ID,
        text=f"📦 *Livraison reçue !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{session_summary(s)}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅  Valider", callback_data=f"admin_approve_{code}"),
            InlineKeyboardButton("❌  Rejeter", callback_data=f"admin_reject_{code}"),
        ]]))
    await msg.reply_text(t(uid,"delivery_sent"), parse_mode="Markdown")

# ══════════════════════════════════════════════
#  ÉCHANGE — DÉPÔT DES 2 TECH
# ══════════════════════════════════════════════

async def handle_exchange_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    code = user_sessions.get(uid)
    if not code: return
    s = sessions.get(code)
    if not s: return

    msg = update.message
    di  = store_delivery(msg)
    if not di: return

    key = "buyer_delivery" if uid == s["buyer_id"] else "seller_delivery"
    if s.get(key):
        await msg.reply_text("⚠️ Ta tech est déjà déposée.")
        return

    s[key] = di
    save_state()
    await msg.forward(chat_id=ADMIN_ID)
    await msg.reply_text(t(uid,"exchange_deposited"), parse_mode="Markdown")

    if s.get("buyer_delivery") and s.get("seller_delivery"):
        s["status"] = "🔍 Échange en attente de validation"
        save_state()
        await context.bot.send_message(chat_id=ADMIN_ID,
            text=f"🔁 *Échange — les 2 tech sont déposées !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{session_summary(s)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅  Valider l'échange", callback_data=f"admin_ex_ok_{code}"),
                InlineKeyboardButton("❌  Rejeter",           callback_data=f"admin_ex_rej_{code}"),
            ]]))
        await context.bot.send_message(chat_id=s["buyer_id"],  text=t(s["buyer_id"],"exchange_ready"), parse_mode="Markdown")
        await context.bot.send_message(chat_id=s["seller_id"], text=t(s["seller_id"],"exchange_ready"), parse_mode="Markdown")

# ══════════════════════════════════════════════
#  ADMIN LIVRAISON
# ══════════════════════════════════════════════

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌",show_alert=True); return
    await q.answer()
    code = q.data.replace("admin_approve_",""); s = sessions.get(code)
    if not s: await q.edit_message_text("❌ Session introuvable."); return

    s["status"] = "⏳ En attente adresse retrait vendeur"
    buid = s["buyer_id"]; di = s.get("delivery_file",{}); cap = t(buid,"delivery_ok_buyer",item=esc(s["item_name"]))
    await _send_delivery_to(context.bot, buid, di, cap)

    await context.bot.send_message(chat_id=buid, text=t(buid,"tx_complete_buyer"), parse_mode="Markdown")

    suid = s["seller_id"]
    if s.get("seller_price", 0) > 0:
        wp   = t(suid,"withdraw_crypto") if s["payment_method"]=="Crypto" else t(suid,"withdraw_paypal")
        await context.bot.send_message(chat_id=suid,
            text=f"🎉 *Livraison validée !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n💵 Tu vas recevoir *{fmt(s['seller_price'])} €*\n\n{wp}",
            parse_mode="Markdown")
    else:
        # Échange → pas de retrait, session clôturée
        s["status"] = "✅ Transaction complète"
        await context.bot.send_message(chat_id=suid, text=t(suid,"tx_complete_seller"), parse_mode="Markdown")
    save_state()
    await q.edit_message_text(f"✅ Livraison validée — `{code}`.", parse_mode="Markdown")

async def admin_ex_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌",show_alert=True); return
    await q.answer()
    code = q.data.replace("admin_ex_ok_",""); s = sessions.get(code)
    if not s: await q.edit_message_text("❌ Session introuvable."); return

    buid = s["buyer_id"]; suid = s["seller_id"]
    sdi  = s.get("seller_delivery",{}); bdi = s.get("buyer_delivery",{})

    # La tech du vendeur part à l'acheteur, celle de l'acheteur part au vendeur
    await _send_delivery_to(context.bot, buid, sdi, t(buid,"delivery_ok_buyer",item=esc(s["item_name"])))
    await context.bot.send_message(chat_id=buid, text=t(buid,"tx_complete_buyer"), parse_mode="Markdown")
    await _send_delivery_to(context.bot, suid, bdi, t(suid,"delivery_ok_buyer",item=esc(s["item_name"])))
    await context.bot.send_message(chat_id=suid, text=t(suid,"tx_complete_seller"), parse_mode="Markdown")

    s["status"] = "✅ Transaction complète"
    save_state()
    await q.edit_message_text(f"✅ Échange validé — `{code}`.", parse_mode="Markdown")

async def admin_ex_rej(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌",show_alert=True); return
    await q.answer()
    code = q.data.replace("admin_ex_rej_",""); s = sessions.get(code)
    if not s: await q.edit_message_text("❌ Session introuvable."); return
    s["status"] = "⏳ En attente des livraisons"
    s["buyer_delivery"]  = None
    s["seller_delivery"] = None
    save_state()
    await context.bot.send_message(chat_id=s["buyer_id"],  text=t(s["buyer_id"],"delivery_rejected"), parse_mode="Markdown")
    await context.bot.send_message(chat_id=s["seller_id"], text=t(s["seller_id"],"delivery_rejected"), parse_mode="Markdown")
    await q.edit_message_text(f"❌ Échange rejeté — `{code}`.", parse_mode="Markdown")

async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌",show_alert=True); return
    await q.answer()
    code = q.data.replace("admin_reject_",""); s = sessions.get(code)
    if not s: await q.edit_message_text("❌ Session introuvable."); return
    s["status"] = "⏳ En attente de la livraison vendeur"
    await context.bot.send_message(chat_id=s["seller_id"],text=t(s["seller_id"],"delivery_rejected"),parse_mode="Markdown")
    save_state()
    await q.edit_message_text(f"❌ Livraison rejetée — `{code}`.", parse_mode="Markdown")

# ══════════════════════════════════════════════
#  RETRAIT VENDEUR
# ══════════════════════════════════════════════

async def seller_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    code = user_sessions.get(uid)
    if not code: return
    s = sessions.get(code)
    if not s or s["seller_id"] != uid: return
    if s["status"] != "⏳ En attente adresse retrait vendeur": return

    address = update.message.text.strip()
    s["withdraw_address"] = address
    s["status"]           = "⏳ Retrait en cours"
    save_state()
    ml = "🪙 Crypto" if s["payment_method"]=="Crypto" else "💳 PayPal"

    await update.message.reply_text(t(uid,"withdraw_ok",amount=fmt(s["seller_price"]),method=ml,address=esc(address)),parse_mode="Markdown")
    await context.bot.send_message(chat_id=ADMIN_ID,
        text=(f"💸 *Retrait !*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
              f"🔑 `{code}`\n🧑‍💼 {esc(s['seller_name'])} | ID: `{s['seller_id']}`\n"
              f"📲 {ml}\n📋 `{esc(address)}`\n💰 *{fmt(s['seller_price'])} €*"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅  J'ai envoyé !", callback_data=f"admin_paid_{code}")]]))

async def admin_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌",show_alert=True); return
    await q.answer()
    code = q.data.replace("admin_paid_",""); s = sessions.get(code)
    if not s: await q.edit_message_text("❌ Session introuvable."); return
    s["status"] = "✅ Transaction complète"
    suid = s["seller_id"]
    await context.bot.send_message(chat_id=suid,text=t(suid,"payout_sent",amount=fmt(s["seller_price"])),parse_mode="Markdown")
    save_state()
    await q.edit_message_text(f"✅ Session `{code}` clôturée.", parse_mode="Markdown")

# ══════════════════════════════════════════════
#  MES SESSIONS
# ══════════════════════════════════════════════

async def my_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = q.from_user.id
    us = [s for s in sessions.values() if s["buyer_id"]==uid or s["seller_id"]==uid]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"btn_back"),callback_data="back_start")]])
    if not us:
        await q.edit_message_text(t(uid,"no_sessions"),parse_mode="Markdown",reply_markup=kb); return
    txt = "📋 *Tes sessions :*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for s in us[-5:]:
        role = "🛒" if s["buyer_id"]==uid else "🧑‍💼"
        txt += f"{role} `{s['code']}` — {s['item_name']}\n{s['status']}\n\n"
    await q.edit_message_text(txt,parse_mode="Markdown",reply_markup=kb)

# ══════════════════════════════════════════════
#  PANEL ADMIN + ANNONCE
# ══════════════════════════════════════════════

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌",show_alert=True); return
    await q.answer()
    total     = len(sessions)
    completed = sum(1 for s in sessions.values() if "complète" in s["status"])
    pending   = sum(1 for s in sessions.values() if "attente" in s["status"].lower())
    revenue   = sum(s["total_price"]-s["seller_price"] for s in sessions.values() if "complète" in s["status"])
    scams     = len(scam_list)
    sl        = "🟢 EN LIGNE" if bot_online else "🔴 HORS LIGNE"
    btn_tog   = "🔴  Hors ligne" if bot_online else "🟢  En ligne"
    await q.edit_message_text(
        f"👑 *Panel Admin — EscrowBot*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 *Sessions totales :* `{total}`\n"
        f"✅ *Complétées :* `{completed}`\n"
        f"⏳ *En attente :* `{pending}`\n"
        f"💰 *Revenus (frais) :* `{fmt(revenue)} €`\n"
        f"🚫 *Scam List :* `{scams}`\n\n"
        f"📡 *Statut bot :* {sl}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *Commandes disponibles :*\n"
        f"`/cryptook CODE` — Valider un paiement crypto manuellement\n"
        f"`/addscam @pseudo ID raison` — Ajouter à la Scam List\n"
        f"`/removescam @pseudo` — Retirer de la Scam List",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(btn_tog,                callback_data="admin_toggle")],
            [InlineKeyboardButton("📢  Annonce",          callback_data="admin_announce")],
            [InlineKeyboardButton(t(ADMIN_ID,"btn_back"), callback_data="back_start")],
        ])
    )

async def toggle_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_online
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌", show_alert=True); return
    await q.answer()
    bot_online = not bot_online
    save_state()
    msg_key = "announce_on" if bot_online else "announce_off"
    for uid in list(all_users):
        try:
            await context.bot.send_message(chat_id=uid, text=t(uid, msg_key), parse_mode="Markdown")
        except Exception:
            pass
    sl      = "🟢 EN LIGNE" if bot_online else "🔴 HORS LIGNE"
    btn_tog = "🔴  Hors ligne" if bot_online else "🟢  En ligne"
    await q.edit_message_text(
        f"👑 *Panel Admin*\n\n📡 *Statut bot :* {sl}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(btn_tog,            callback_data="admin_toggle")],
            [InlineKeyboardButton("📢  Annonce",      callback_data="admin_announce")],
            [InlineKeyboardButton(t(ADMIN_ID,"btn_back"), callback_data="back_start")],
        ])
    )

async def admin_announce_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("❌", show_alert=True); return
    await q.answer()
    await q.edit_message_text(t(ADMIN_ID, "announce_prompt"), parse_mode="Markdown")
    return ADMIN_ANNOUNCE

async def admin_announce_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    text  = update.message.text.strip()
    count = 0
    for uid in list(all_users):
        try:
            await context.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
            count += 1
        except Exception:
            pass
    await update.message.reply_text(t(ADMIN_ID, "announce_sent", count=count), parse_mode="Markdown")
    return ConversationHandler.END

# ══════════════════════════════════════════════
#  ROUTE MESSAGES LIBRES
# ══════════════════════════════════════════════

async def route_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatche les messages texte/photo/document selon l'état de la session."""
    uid  = update.effective_user.id
    code = user_sessions.get(uid)
    s    = sessions.get(code) if code else None

    # Adresse crypto de remboursement
    if context.user_data.get("refund_code") and update.message.text:
        await handle_refund_crypto_addr(update, context)
        context.user_data.pop("refund_code", None)
        return

    if not s:
        return

    status = s.get("status", "")

    # Retrait vendeur (adresse)
    if s["seller_id"] == uid and status == "⏳ En attente adresse retrait vendeur":
        await seller_withdraw(update, context); return

    # Preuve PayPal acheteur
    if s["buyer_id"] == uid and status == "⏳ En attente du paiement acheteur":
        await handle_buyer_proof(update, context); return

    # Livraison vendeur
    if s["seller_id"] == uid and status == "⏳ En attente de la livraison vendeur":
        await seller_delivery(update, context); return

    # Échange — dépôt des 2 tech (acheteur ET vendeur)
    if s.get("is_exchange") and status == "⏳ En attente des livraisons":
        if uid in (s["buyer_id"], s["seller_id"]):
            await handle_exchange_delivery(update, context)
        return

async def back_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid  = q.from_user.id
    name = q.from_user.first_name or ""
    if not bot_online and uid != ADMIN_ID:
        await q.edit_message_text(t(uid,"bot_offline"), parse_mode="Markdown"); return
    await q.edit_message_text(t(uid,"welcome",name=name), parse_mode="Markdown", reply_markup=main_kb(uid))

# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════

def main():
    global _app
    load_state()
    app = Application.builder().token(BOT_TOKEN).build()
    _app = app

    # ConversationHandler principal (création session + langue)
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(create_session_start, pattern="^create_session$"),
            CallbackQueryHandler(join_session_start,   pattern="^join_session$"),
            CallbackQueryHandler(admin_announce_start, pattern="^admin_announce$"),
        ],
        states={
            CHOOSE_LANG:        [CallbackQueryHandler(set_lang, pattern="^lang_")],
            BUY_ITEM_NAME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_item_name)],
            BUY_DELIVERY_TYPE:  [
                CallbackQueryHandler(buy_delivery_type, pattern="^del_"),
                CallbackQueryHandler(buy_exchange_mode, pattern="^mode_exchange$"),
            ],
            BUY_PRICE:          [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_price)],
            BUY_PAYMENT_METHOD: [CallbackQueryHandler(buy_payment_method, pattern="^pay_")],
            BUY_CONDITIONS:     [
                MessageHandler(filters.TEXT & ~filters.COMMAND, buy_conditions_text),
                CallbackQueryHandler(buy_conditions_skip, pattern="^skip_conditions$"),
            ],
            SELLER_ENTER_CODE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, seller_enter_code)],
            ADMIN_ANNOUNCE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_announce_send)],
        },
        fallbacks=[CallbackQueryHandler(back_start, pattern="^back_start$")],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )

    app.add_handler(conv)

    # Callbacks boutons
    app.add_handler(CallbackQueryHandler(back_start,       pattern="^back_start$"))
    app.add_handler(CallbackQueryHandler(show_scam_list,   pattern="^scam_list$"))
    app.add_handler(CallbackQueryHandler(how_it_works,     pattern="^how_it_works$"))
    app.add_handler(CallbackQueryHandler(my_sessions,      pattern="^my_sessions$"))
    app.add_handler(CallbackQueryHandler(admin_panel,      pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(toggle_online,    pattern="^admin_toggle$"))
    app.add_handler(CallbackQueryHandler(refund_ask,       pattern="^refund_ask_"))
    app.add_handler(CallbackQueryHandler(back_to_pay,      pattern="^back_to_pay_"))
    app.add_handler(CallbackQueryHandler(refund_confirm,   pattern="^refund_confirm_"))
    app.add_handler(CallbackQueryHandler(admin_refunded,   pattern="^admin_refunded_"))
    app.add_handler(CallbackQueryHandler(admin_pay_ok,     pattern="^admin_pay_ok_"))
    app.add_handler(CallbackQueryHandler(admin_pay_rej,    pattern="^admin_pay_rej_"))
    app.add_handler(CallbackQueryHandler(admin_approve,    pattern="^admin_approve_"))
    app.add_handler(CallbackQueryHandler(admin_reject,     pattern="^admin_reject_"))
    app.add_handler(CallbackQueryHandler(admin_paid,       pattern="^admin_paid_"))
    app.add_handler(CallbackQueryHandler(admin_ex_ok,      pattern="^admin_ex_ok_"))
    app.add_handler(CallbackQueryHandler(admin_ex_rej,     pattern="^admin_ex_rej_"))

    # Commandes admin
    app.add_handler(CommandHandler("cryptook",   cmd_cryptook))
    app.add_handler(CommandHandler("addscam",    cmd_addscam))
    app.add_handler(CommandHandler("removescam", cmd_removescam))

    # Messages libres (livraison, preuve, retrait, remboursement crypto)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
        route_message
    ))

    # Job polling OxaPay backup toutes les 15s
    app.job_queue.run_repeating(oxapay_polling, interval=15, first=10)

    logger.info("✅ EscrowBot démarré.")

    # ── Serveur IPN OxaPay (aiohttp) + Bot Telegram en parallèle ──
    async def run_all():
        # Serveur HTTP pour recevoir les webhooks OxaPay instantanément
        web_app = web.Application()
        web_app.router.add_post("/oxapay-ipn", oxapay_ipn_handler)
        web_app.router.add_get("/health", lambda r: web.Response(text="OK"))
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
        await site.start()
        logger.info(f"🌐 Serveur IPN démarré sur port {WEBHOOK_PORT} — route POST /oxapay-ipn")

        # Démarrage du bot Telegram en polling
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("🤖 Bot Telegram en polling...")

        # Tourne indéfiniment
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            await runner.cleanup()

    asyncio.run(run_all())

if __name__ == "__main__":
    main()
