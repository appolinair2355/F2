"""
Configuration du bot Telegram de prédiction Baccarat
Variables configurées pour Render.com
"""
import os

# ==================== CONFIGURATION OBLIGATOIRE ====================
# Ces valeurs sont configurées directement pour le déploiement Render.com

API_ID = 29177661
API_HASH = "a8639172fa8d35dbfd8ea46286d349ab"
BOT_TOKEN = "8131011456:AAGPWIFCfQoGuSlL-GcAw2s96rLbOn5I_c0"
ADMIN_ID = 1190237801

# IDs des canaux Telegram
SOURCE_CHANNEL_ID = -1002682552255      # Canal source Baccarat
PREDICTION_CHANNEL_ID = -1003853896752  # Canal de prédiction

# Port pour Render.com (obligatoire)
PORT = 10000

# ==================== CONFIGURATION PRÉDICTION ====================
# Offset pour la prédiction (défaut: 2) - N + a
PREDICTION_OFFSET = int(os.getenv('PREDICTION_OFFSET', '2'))

# ==================== MAPPING DES COULEURS ====================
SUIT_MAPPING = {
    '♠️': '❤️',
    '♠': '❤️',
    '❤️': '♠️',
    '❤': '♠️',
    '♥️': '♠️',
    '♥': '♠️',
    '♣️': '♦️',
    '♣': '♦️',
    '♦️': '♣️',
    '♦': '♣️'
}

ALL_SUITS = ['♠', '♥', '♦', '♣']

SUIT_DISPLAY = {
    '♠': '♠️',
    '♥': '❤️',
    '♦': '♦️',
    '♣': '♣️'
}

# Noms complets des couleurs pour l'affichage
SUIT_NAMES = {
    '♠': 'Pique',
    '♠️': 'Pique',
    '♥': 'Cœur',
    '❤️': 'Cœur',
    '♥️': 'Cœur',
    '♦': 'Carreau',
    '♦️': 'Carreau',
    '♣': 'Trèfle',
    '♣️': 'Trèfle'
}
