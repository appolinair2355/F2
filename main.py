import os
import asyncio
import re
import logging
import sys
from datetime import datetime, timezone
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from aiohttp import web
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, PORT,
    SUIT_MAPPING, ALL_SUITS, SUIT_DISPLAY, SUIT_NAMES,
    PREDICTION_OFFSET
)

# ==================== CONFIGURATION LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ==================== VÉRIFICATIONS ====================
if not API_ID or API_ID == 0:
    logger.error("❌ API_ID manquant")
    exit(1)
if not API_HASH:
    logger.error("❌ API_HASH manquant")
    exit(1)
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN manquant")
    exit(1)

logger.info(f"🚀 Démarrage Bot Prédiction Baccarat v2.0")
logger.info(f"📡 Configuration: SOURCE={SOURCE_CHANNEL_ID}, PREDICTION={PREDICTION_CHANNEL_ID}, PORT={PORT}")

# ==================== INITIALISATION CLIENT ====================
session_string = os.getenv('TELEGRAM_SESSION', '')
client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

# ==================== VARIABLES GLOBALES ====================
pending_predictions = {}      # Prédictions en attente
processed_messages = set()    # Messages déjà traités
last_transferred_game = None  # Dernier jeu transféré
current_game_number = 0       # Numéro de jeu actuel
source_channel_ok = False     # Statut canal source
prediction_channel_ok = False # Statut canal prédiction
transfer_enabled = True       # Transfert activé par défaut

# ==================== FONCTIONS UTILITAIRES ====================

def extract_game_number(message: str):
    """Extrait le numéro de jeu du format #N430"""
    match = re.search(r"#N\s*(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def extract_parentheses_groups(message: str):
    """Extrait les groupes entre parenthèses"""
    return re.findall(r"\(([^)]*)\)", message)

def normalize_suits(group_str: str) -> str:
    """Normalise les emojis de couleurs"""
    normalized = group_str.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    return normalized

def count_cards_by_suit(group_str: str) -> dict:
    """Compte les cartes par couleur dans un groupe"""
    normalized = normalize_suits(group_str)
    counts = {}
    for suit in ALL_SUITS:
        count = normalized.count(suit)
        if count > 0:
            counts[suit] = count
    return counts

def find_duplicate_suit(second_group: str) -> str:
    """
    Nouvelle règle: Vérifie si le 2ème groupe a 2 cartes de même couleur.
    Retourne la couleur si trouvée, None sinon.
    """
    suit_counts = count_cards_by_suit(second_group)

    for suit, count in suit_counts.items():
        if count >= 2:
            return suit
    return None

def has_suit_in_group(group_str: str, target_suit: str) -> bool:
    """Vérifie si une couleur est présente dans un groupe"""
    normalized = normalize_suits(group_str)
    target_normalized = normalize_suits(target_suit)
    for suit in ALL_SUITS:
        if suit in target_normalized and suit in normalized:
            return True
    return False

def get_suit_display(suit: str) -> str:
    """Retourne l'emoji de la couleur"""
    return SUIT_DISPLAY.get(suit, suit)

def get_suit_name(suit: str) -> str:
    """Retourne le nom complet de la couleur"""
    return SUIT_NAMES.get(suit, suit)

def is_message_finalized(message: str) -> bool:
    """Vérifie si un message est finalisé (pour la vérification)"""
    if '⏰' in message:
        return False
    return '✅' in message or '🔰' in message

def format_prediction_message(game_number: int, suit: str, status: str = "⏳⏳") -> str:
    """Formate le message de prédiction avec le nouveau format emoji"""
    suit_display = get_suit_display(suit)
    suit_name = get_suit_name(suit)

    return f"""🎰 PRÉDICTION #{game_number}
🎯 Couleur: {suit_display} {suit_name}
📊 Statut: {status}"""

def format_status_message(status_code: str) -> str:
    """Convertit le code statut en texte formaté"""
    if status_code == '✅0️⃣':
        return "✅0️⃣ GAGNÉ"
    elif status_code == '✅1️⃣':
        return "✅1️⃣ GAGNÉ"
    elif status_code == '✅2️⃣':
        return "✅2️⃣ GAGNÉ"
    elif status_code == '❌':
        return "❌ PERDU"
    return status_code

# ==================== FONCTIONS PRINCIPALES ====================

async def send_prediction_to_channel(target_game: int, suit: str, base_game: int):
    """Envoie une prédiction au canal de prédiction"""
    try:
        prediction_msg = format_prediction_message(target_game, suit, "⏳⏳")
        msg_id = 0

        if PREDICTION_CHANNEL_ID and prediction_channel_ok:
            try:
                pred_msg = await client.send_message(PREDICTION_CHANNEL_ID, prediction_msg)
                msg_id = pred_msg.id
                logger.info(f"✅ Prédiction envoyée: Jeu #{target_game} - {get_suit_display(suit)} {get_suit_name(suit)}")
            except Exception as e:
                logger.error(f"❌ Erreur envoi prédiction: {e}")
        else:
            logger.warning(f"⚠️ Canal prédiction non accessible")

        # Stocker la prédiction
        pending_predictions[target_game] = {
            'message_id': msg_id,
            'suit': suit,
            'base_game': base_game,
            'status': '⏳⏳',
            'check_count': 0,
            'created_at': datetime.now().isoformat()
        }

        logger.info(f"🎯 Prédiction active: #{target_game} - {get_suit_display(suit)} (basé sur #{base_game})")
        return msg_id

    except Exception as e:
        logger.error(f"❌ Erreur création prédiction: {e}")
        return None

async def update_prediction_status(game_number: int, new_status: str):
    """Met à jour le statut d'une prédiction existante"""
    try:
        if game_number not in pending_predictions:
            return False

        pred = pending_predictions[game_number]
        message_id = pred['message_id']
        suit = pred['suit']

        status_text = format_status_message(new_status)
        updated_msg = format_prediction_message(game_number, suit, status_text)

        # Mettre à jour le message dans le canal
        if PREDICTION_CHANNEL_ID and message_id > 0 and prediction_channel_ok:
            try:
                await client.edit_message(PREDICTION_CHANNEL_ID, message_id, updated_msg)
                logger.info(f"✅ Statut mis à jour: #{game_number} → {status_text}")
            except Exception as e:
                logger.error(f"❌ Erreur mise à jour message: {e}")

        pred['status'] = new_status

        # Supprimer si terminé
        if new_status in ['✅0️⃣', '✅1️⃣', '✅2️⃣', '❌']:
            del pending_predictions[game_number]
            logger.info(f"🗑️ Prédiction #{game_number} terminée et supprimée")

        return True

    except Exception as e:
        logger.error(f"❌ Erreur mise à jour statut: {e}")
        return False

async def check_prediction_result(game_number: int, first_group: str, second_group: str):
    """
    Vérifie le résultat des prédictions pour un jeu finalisé.
    Cherche la couleur prédite dans les deux groupes.
    """
    # Vérifier si ce jeu a une prédiction active
    if game_number in pending_predictions:
        pred = pending_predictions[game_number]
        target_suit = pred['suit']

        # Vérifier dans les deux groupes
        found_in_first = has_suit_in_group(first_group, target_suit)
        found_in_second = has_suit_in_group(second_group, target_suit)

        if found_in_first or found_in_second:
            await update_prediction_status(game_number, '✅0️⃣')
            logger.info(f"🎉 PRÉDICTION #{game_number} GAGNÉE (trouvée au numéro)")
            return True
        else:
            pred['check_count'] = 1
            logger.info(f"⏳ Prédiction #{game_number}: non trouvée, attente +1")

    # Vérifier les jeux précédents (N-1 et N-2)
    for offset in [1, 2]:
        prev_game = game_number - offset
        if prev_game in pending_predictions:
            pred = pending_predictions[prev_game]
            check_count = pred.get('check_count', 0)

            if check_count >= offset - 1:
                target_suit = pred['suit']

                found_in_first = has_suit_in_group(first_group, target_suit)
                found_in_second = has_suit_in_group(second_group, target_suit)

                if found_in_first or found_in_second:
                    status_code = f'✅{offset}️⃣'
                    await update_prediction_status(prev_game, status_code)
                    logger.info(f"🎉 PRÉDICTION #{prev_game} GAGNÉE au +{offset}")
                    return True
                elif offset == 2:
                    # Échec définitif après 3 tentatives
                    await update_prediction_status(prev_game, '❌')
                    logger.info(f"💔 PRÉDICTION #{prev_game} PERDUE")
                    return False
                else:
                    pred['check_count'] = offset
                    logger.info(f"⏳ Prédiction #{prev_game}: pas trouvé au +{offset}")

    return None

async def process_new_message(message_text: str, chat_id: int, is_finalized: bool = False):
    """
    Traite un message du canal source.
    is_finalized=False → Création de prédiction (immédiat)
    is_finalized=True → Vérification des prédictions
    """
    global last_transferred_game, current_game_number

    try:
        game_number = extract_game_number(message_text)
        if game_number is None:
            return

        current_game_number = game_number

        # Éviter les doublons
        message_hash = f"{game_number}_{message_text[:50]}"
        if message_hash in processed_messages:
            return
        processed_messages.add(message_hash)
        if len(processed_messages) > 200:
            processed_messages.clear()

        # Extraire les groupes
        groups = extract_parentheses_groups(message_text)
        if len(groups) < 2:
            logger.warning(f"⚠️ Jeu #{game_number}: moins de 2 groupes trouvés")
            return

        first_group = groups[0]
        second_group = groups[1]

        logger.info(f"📩 Jeu #{game_number} | G1: {first_group} | G2: {second_group} | Finalisé: {is_finalized}")

        # === MODE FINALISÉ : Vérification ===
        if is_finalized:
            logger.info(f"✅ Vérification prédiction pour jeu finalisé #{game_number}")

            # Transfert à l'admin si activé
            if transfer_enabled and ADMIN_ID and last_transferred_game != game_number:
                try:
                    transfer_msg = f"📨 **Message finalisé:**\n\n{message_text}"
                    await client.send_message(ADMIN_ID, transfer_msg)
                    last_transferred_game = game_number
                except Exception as e:
                    logger.error(f"❌ Erreur transfert: {e}")

            # Vérifier les résultats
            await check_prediction_result(game_number, first_group, second_group)
            return

        # === MODE NOUVEAU MESSAGE : Création prédiction ===
        # Nouvelle règle: 2 cartes identiques dans le 2ème groupe
        duplicate_suit = find_duplicate_suit(second_group)

        if duplicate_suit:
            target_game = game_number + PREDICTION_OFFSET

            # Vérifier si pas déjà en cours
            if target_game not in pending_predictions:
                await send_prediction_to_channel(target_game, duplicate_suit, game_number)
                logger.info(f"🔮 NOUVELLE PRÉDICTION: #{target_game} (basé sur #{game_number}, doublon {get_suit_display(duplicate_suit)} dans G2)")
            else:
                logger.info(f"ℹ️ Prédiction #{target_game} déjà existante")
        else:
            logger.info(f"ℹ️ Jeu #{game_number}: pas de doublon dans G2, pas de prédiction")

    except Exception as e:
        logger.error(f"❌ Erreur traitement message: {e}")
        import traceback
        logger.error(traceback.format_exc())

# ==================== HANDLERS TÉLÉGRAM ====================

@client.on(events.NewMessage())
async def handle_message(event):
    """Gestion des nouveaux messages"""
    try:
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id

        # Correction pour les canaux
        if chat_id > 0 and hasattr(chat, 'broadcast') and chat.broadcast:
            chat_id = -1000000000000 - chat_id

        if chat_id == SOURCE_CHANNEL_ID:
            message_text = event.message.message
            logger.debug(f"Message reçu: {message_text[:80]}...")

            # Traitement immédiat pour créer les prédictions
            # (ne pas attendre la finalisation)
            await process_new_message(message_text, chat_id, is_finalized=False)

    except Exception as e:
        logger.error(f"❌ Erreur handle_message: {e}")

@client.on(events.MessageEdited())
async def handle_edited_message(event):
    """Gestion des messages édités (finalisation)"""
    try:
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id

        if chat_id > 0 and hasattr(chat, 'broadcast') and chat.broadcast:
            chat_id = -1000000000000 - chat_id

        if chat_id == SOURCE_CHANNEL_ID:
            message_text = event.message.message

            # Si le message devient finalisé, vérifier les prédictions
            if is_message_finalized(message_text):
                logger.info(f"📝 Message finalisé détecté (édition)")
                await process_new_message(message_text, chat_id, is_finalized=True)

    except Exception as e:
        logger.error(f"❌ Erreur handle_edited: {e}")

# ==================== COMMANDES ADMIN ====================

@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    if event.is_group or event.is_channel:
        return

    await event.respond(f"""🤖 **Bot Prédiction Baccarat v2.0**

🎯 **Règle:** Prédiction quand 2 cartes identiques dans le 2ème groupe
📏 **Offset:** N + {PREDICTION_OFFSET}

**Commandes:**
• `/status` - Voir les prédictions actives
• `/setoffset <n>` - Changer l'offset (admin)
• `/transfert` - Activer le transfert
• `/stoptransfert` - Désactiver le transfert
• `/checkchannels` - Vérifier les canaux
• `/debug` - Informations système
• `/help` - Aide complète""")

@client.on(events.NewMessage(pattern='/setoffset'))
async def cmd_setoffset(event):
    if event.is_group or event.is_channel:
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("⛔ Réservé à l'admin")
        return

    try:
        text = event.message.message
        parts = text.split()
        if len(parts) < 2:
            await event.respond("❌ Usage: `/setoffset <nombre>`\nEx: `/setoffset 3`")
            return

        new_offset = int(parts[1])
        if new_offset < 1 or new_offset > 10:
            await event.respond("❌ L'offset doit être entre 1 et 10")
            return

        global PREDICTION_OFFSET
        import config
        config.PREDICTION_OFFSET = new_offset

        await event.respond(f"✅ Offset modifié: **{new_offset}**\nProchaines prédictions: N+{new_offset}")
        logger.info(f"📏 Offset modifié par admin: {new_offset}")

    except ValueError:
        await event.respond("❌ Nombre invalide")
    except Exception as e:
        await event.respond(f"❌ Erreur: {str(e)}")

@client.on(events.NewMessage(pattern='/status'))
async def cmd_status(event):
    if event.is_group or event.is_channel:
        return

    status_msg = f"📊 **État du Bot**\n\n"
    status_msg += f"🎮 Jeu actuel: #{current_game_number}\n"
    status_msg += f"📏 Offset: N+{PREDICTION_OFFSET}\n\n"

    if pending_predictions:
        status_msg += f"**🔮 Prédictions actives ({len(pending_predictions)}):**\n"
        for game_num, pred in sorted(pending_predictions.items()):
            distance = game_num - current_game_number
            suit_display = get_suit_display(pred['suit'])
            suit_name = get_suit_name(pred['suit'])
            status_msg += f"• #{game_num}: {suit_display} {suit_name} ({pred['status']})\n"
    else:
        status_msg += "**🔮 Aucune prédiction active**\n"

    await event.respond(status_msg)

@client.on(events.NewMessage(pattern='/debug'))
async def cmd_debug(event):
    if event.is_group or event.is_channel:
        return

    debug_msg = f"""🔍 **Debug Info:**

**Config:**
• Source: {SOURCE_CHANNEL_ID}
• Prédiction: {PREDICTION_CHANNEL_ID}
• Admin: {ADMIN_ID}
• Offset: {PREDICTION_OFFSET}

**Statut:**
• Source OK: {'✅' if source_channel_ok else '❌'}
• Prédiction OK: {'✅' if prediction_channel_ok else '❌'}
• Jeu actuel: #{current_game_number}
• Prédictions: {len(pending_predictions)}

**Version:** 2.0 (Render.com)
"""
    await event.respond(debug_msg)

@client.on(events.NewMessage(pattern='/checkchannels'))
async def cmd_checkchannels(event):
    global source_channel_ok, prediction_channel_ok

    if event.is_group or event.is_channel:
        return

    await event.respond("🔍 Vérification des canaux...")
    result_msg = "📡 **Résultat:**\n\n"

    # Vérifier canal source
    try:
        source_entity = await client.get_entity(SOURCE_CHANNEL_ID)
        source_channel_ok = True
        result_msg += f"✅ **Source:** {getattr(source_entity, 'title', 'N/A')}\n"
    except Exception as e:
        source_channel_ok = False
        result_msg += f"❌ **Source:** {str(e)[:50]}\n"

    # Vérifier canal prédiction
    try:
        pred_entity = await client.get_entity(PREDICTION_CHANNEL_ID)
        try:
            test_msg = await client.send_message(PREDICTION_CHANNEL_ID, "🔍 Test...")
            await client.delete_messages(PREDICTION_CHANNEL_ID, test_msg.id)
            prediction_channel_ok = True
            result_msg += f"✅ **Prédiction:** {getattr(pred_entity, 'title', 'N/A')}\n"
        except:
            result_msg += f"⚠️ **Prédiction:** Lecture seule\n"
    except Exception as e:
        result_msg += f"❌ **Prédiction:** {str(e)[:50]}\n"

    await event.respond(result_msg)

@client.on(events.NewMessage(pattern='/transfert'))
async def cmd_transfert(event):
    if event.is_group or event.is_channel:
        return
    global transfer_enabled
    transfer_enabled = True
    await event.respond("✅ Transfert activé")

@client.on(events.NewMessage(pattern='/stoptransfert'))
async def cmd_stop_transfert(event):
    if event.is_group or event.is_channel:
        return
    global transfer_enabled
    transfer_enabled = False
    await event.respond("⛔ Transfert désactivé")

@client.on(events.NewMessage(pattern='/help'))
async def cmd_help(event):
    if event.is_group or event.is_channel:
        return

    await event.respond(f"""📖 **Aide Bot Prédiction v2.0**

**🎯 Règle de prédiction:**
Quand le **2ème groupe** contient **2 cartes de même couleur**:
→ Prédiction pour le jeu **N + {PREDICTION_OFFSET}**

**Exemple:**
```
#N430. ✅4(10♦️5♠️9♠️) - 0(10♥️J♥️K♦️) #T4
```
2ème groupe: (10♥️J♥️K♦️) → 2×❤️
→ Prédiction #{430 + PREDICTION_OFFSET}: ❤️ Cœur

**⚡ Fonctionnement:**
1. Détection immédiate (pas d'attente finalisation)
2. Vérification uniquement sur messages finalisés
3. Statuts: ✅0️⃣ ✅1️⃣ ✅2️⃣ ou ❌

**Commandes:**
• `/start` - Démarrer
• `/status` - Voir les prédictions
• `/setoffset <n>` - Changer offset (admin)
• `/transfert` - Activer transfert
• `/stoptransfert` - Désactiver
• `/checkchannels` - Vérifier canaux
• `/debug` - Infos système""")

# ==================== SERVEUR WEB (RENDER.COM) ====================

async def index(request):
    """Page d'accueil"""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Prédiction Baccarat v2.0</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }}
            h1 {{ color: #2c3e50; }}
            .status {{ background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 20px 0; }}
            .ok {{ color: #27ae60; }}
            .warning {{ color: #e74c3c; }}
        </style>
    </head>
    <body>
        <h1>🎯 Bot Prédiction Baccarat v2.0</h1>
        <div class="status">
            <h3>📊 Statut</h3>
            <p><strong>Jeu actuel:</strong> #{current_game_number}</p>
            <p><strong>Prédictions actives:</strong> {len(pending_predictions)}</p>
            <p><strong>Offset:</strong> N+{PREDICTION_OFFSET}</p>
            <p><strong>Canal Source:</strong> <span class="{'ok' if source_channel_ok else 'warning'}">{'✅ OK' if source_channel_ok else '❌ Erreur'}</span></p>
            <p><strong>Canal Prédiction:</strong> <span class="{'ok' if prediction_channel_ok else 'warning'}">{'✅ OK' if prediction_channel_ok else '❌ Erreur'}</span></p>
        </div>
        <p><em>Déployé sur Render.com</em></p>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html', status=200)

async def health_check(request):
    """Health check pour Render.com"""
    return web.Response(text="OK", status=200)

async def status_api(request):
    """API JSON pour le statut"""
    return web.json_response({
        "status": "running",
        "version": "2.0",
        "current_game": current_game_number,
        "pending_predictions": len(pending_predictions),
        "prediction_offset": PREDICTION_OFFSET,
        "source_channel_ok": source_channel_ok,
        "prediction_channel_ok": prediction_channel_ok,
        "timestamp": datetime.now().isoformat()
    })

async def start_web_server():
    """Démarre le serveur web sur le port 10000"""
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/health', health_check)
    app.router.add_get('/status', status_api)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Serveur web démarré sur port {PORT}")

# ==================== DÉMARRAGE ====================

async def start_bot():
    """Démarre le bot Telegram"""
    global source_channel_ok, prediction_channel_ok

    try:
        logger.info("🔌 Connexion à Telegram...")
        await client.start(bot_token=BOT_TOKEN)

        me = await client.get_me()
        logger.info(f"🤖 Bot connecté: @{me.username}")

        # Sauvegarder la session
        session = client.session.save()
        if session:
            logger.info(f"🔑 Session: {session[:50]}...")
            logger.info("💡 Sauvegardez cette session dans TELEGRAM_SESSION pour les redémarrages")

        # Vérifier les canaux
        logger.info("🔍 Vérification des canaux...")

        try:
            source = await client.get_entity(SOURCE_CHANNEL_ID)
            source_channel_ok = True
            logger.info(f"✅ Canal source: {getattr(source, 'title', 'N/A')}")
        except Exception as e:
            logger.error(f"❌ Canal source inaccessible: {e}")

        try:
            pred = await client.get_entity(PREDICTION_CHANNEL_ID)
            # Test d'écriture
            test = await client.send_message(PREDICTION_CHANNEL_ID, "🤖 Bot v2.0 démarré!")
            await client.delete_messages(PREDICTION_CHANNEL_ID, test.id)
            prediction_channel_ok = True
            logger.info(f"✅ Canal prédiction: {getattr(pred, 'title', 'N/A')}")
        except Exception as e:
            logger.error(f"❌ Canal prédiction inaccessible: {e}")

        logger.info(f"📋 Règle active: 2 cartes identiques dans G2 → Prédiction N+{PREDICTION_OFFSET}")
        return True

    except Exception as e:
        logger.error(f"❌ Erreur démarrage bot: {e}")
        return False

async def main():
    """Fonction principale"""
    try:
        # Démarrer le serveur web d'abord (Render.com requirement)
        await start_web_server()

        # Démarrer le bot
        success = await start_bot()
        if not success:
            logger.error("Arrêt du programme")
            return

        logger.info("✅ Bot complètement opérationnel!")
        await client.run_until_disconnected()

    except Exception as e:
        logger.error(f"❌ Erreur fatale: {e}")
    finally:
        await client.disconnect()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot arrêté par l'utilisateur")
    except Exception as e:
        logger.error(f"💥 Erreur critique: {e}")
