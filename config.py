"""
Configuration file for Torn Faction Discord Bot
Railway deployment version - uses environment variables
"""

import os
from dotenv import load_dotenv

# Load .env file for local development (Railway ignores this)
load_dotenv()

# ===== DISCORD BOT TOKEN =====
# Get this from: https://discord.com/developers/applications
BOT_TOKEN = os.getenv("DISCORD_TOKEN")

# ===== DISCORD SERVER & CHANNEL IDS =====
# How to get IDs:
# 1. Enable Developer Mode: Discord Settings → Advanced → Developer Mode (ON)
# 2. Right-click on server/channel → Copy ID

# Your Discord server ID
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# Channel IDs
INACTIVITY_ALERTS_CHANNEL_ID = int(os.getenv("INACTIVITY_ALERTS_CHANNEL_ID", "0"))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv("ANNOUNCEMENTS_CHANNEL_ID", "0"))
WAR_CHANNEL_ID = int(os.getenv("WAR_CHANNEL_ID", "0"))
WAR_MANAGEMENT_CHANNEL_ID = int(os.getenv("WAR_MANAGEMENT_CHANNEL_ID", "0"))
CHAINING_CHANNEL_ID = int(os.getenv("CHAINING_CHANNEL_ID", "0"))
HAPPY_JUMP_CHANNEL_ID = int(os.getenv("HAPPY_JUMP_CHANNEL_ID", "0"))

# ===== TORN API SETTINGS =====
# Get your Torn API key from: https://www.torn.com/preferences.php#tab=api
TORN_API_KEY = os.getenv("TORN_API_KEY")
TORN_FACTION_ID = int(os.getenv("TORN_FACTION_ID", "0"))

# ===== BOT SETTINGS =====
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

# Management role IDs (users with these roles can use management commands)
# Parse comma-separated role IDs from environment variable
MANAGEMENT_ROLE_IDS_STR = os.getenv("MANAGEMENT_ROLE_IDS", "")
if MANAGEMENT_ROLE_IDS_STR:
    MANAGEMENT_ROLE_IDS = [int(role_id.strip()) for role_id in MANAGEMENT_ROLE_IDS_STR.split(",") if role_id.strip()]
else:
    MANAGEMENT_ROLE_IDS = []

# Inactivity thresholds (in days)
INACTIVITY_WARNING_DAYS = int(os.getenv("INACTIVITY_WARNING_DAYS", "3"))
INACTIVITY_CRITICAL_DAYS = int(os.getenv("INACTIVITY_CRITICAL_DAYS", "5"))

# Embed color (Discord color codes)
EMBED_COLOR_INFO = 0x3498db     # Blue for info messages
EMBED_COLOR_WARNING = 0xf39c12  # Orange for warnings
EMBED_COLOR_CRITICAL = 0xe74c3c # Red for critical alerts
EMBED_COLOR_SUCCESS = 0x2ecc71  # Green for success messages

# ===== API RATE LIMITING =====
# Torn API rate limit settings
TORN_API_CACHE_SECONDS = int(os.getenv("TORN_API_CACHE_SECONDS", "30"))
TORN_API_MAX_REQUESTS_PER_MINUTE = int(os.getenv("TORN_API_MAX_REQUESTS_PER_MINUTE", "60"))
