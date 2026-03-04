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
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))  # welcome-pit channel
RULES_CHANNEL_ID = int(os.getenv("RULES_CHANNEL_ID", "0"))  # faction-edicts
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))  # punk-plaza

# ===== TORN API SETTINGS =====
# Get your Torn API key from: https://www.torn.com/preferences.php#tab=api
TORN_API_KEY = os.getenv("TORN_API_KEY")  # Faction API key for faction data
TORN_PERSONAL_API_KEY = os.getenv("TORN_PERSONAL_API_KEY")  # Personal API key for user lookups (stakeout)
TORN_FACTION_ID = int(os.getenv("TORN_FACTION_ID", "0"))

# ===== FFSCOUTER SETTINGS =====
# Get a free API key at: https://ffscouter.com
# Used for estimated battle stat lookups in /faction stats
FFSCOUTER_API_KEY = os.getenv("FFSCOUTER_API_KEY", "")

# ===== BOT SETTINGS =====
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

# Management role IDs (users with these roles can use management commands)
# Parse comma-separated role IDs from environment variable
MANAGEMENT_ROLE_IDS_STR = os.getenv("MANAGEMENT_ROLE_IDS", "")
if MANAGEMENT_ROLE_IDS_STR:
    MANAGEMENT_ROLE_IDS = [int(role_id.strip()) for role_id in MANAGEMENT_ROLE_IDS_STR.split(",") if role_id.strip()]
else:
    MANAGEMENT_ROLE_IDS = []

# Role IDs for pinging on specific events
MANAGEMENT_PING_ROLE_ID = int(os.getenv("MANAGEMENT_PING_ROLE_ID", "0"))  # Ping for inactivity/war

# Inactivity thresholds (in days)
INACTIVITY_WARNING_DAYS = int(os.getenv("INACTIVITY_WARNING_DAYS", "3"))
INACTIVITY_CRITICAL_DAYS = int(os.getenv("INACTIVITY_CRITICAL_DAYS", "5"))

# OC participation thresholds (in days since last completed/active OC)
OC_WARNING_DAYS = int(os.getenv("OC_WARNING_DAYS", "2"))
OC_CRITICAL_DAYS = int(os.getenv("OC_CRITICAL_DAYS", "4"))
OC_NEW_MEMBER_GRACE_DAYS = int(os.getenv("OC_NEW_MEMBER_GRACE_DAYS", "3"))

# Left-faction alert threshold (days before alerting about a verified member not in faction)
LEFT_FACTION_ALERT_DAYS = int(os.getenv("LEFT_FACTION_ALERT_DAYS", "7"))

# Embed color (Discord color codes)
EMBED_COLOR_INFO = 0x3498db     # Blue for info messages
EMBED_COLOR_WARNING = 0xf39c12  # Orange for warnings
EMBED_COLOR_CRITICAL = 0xe74c3c # Red for critical alerts
EMBED_COLOR_SUCCESS = 0x2ecc71  # Green for success messages

# ===== AVAILABILITY POLLING SETTINGS =====
# Role name required to participate in availability polls
VERIFIED_ROLE_NAME = os.getenv("VERIFIED_ROLE_NAME", "Ascended punks")

# Channel ID for availability polls (0 = use command channel)
AVAILABILITY_CHANNEL_ID = int(os.getenv("AVAILABILITY_CHANNEL_ID", "0"))

# ===== API RATE LIMITING =====
# Torn API rate limit settings
TORN_API_CACHE_SECONDS = int(os.getenv("TORN_API_CACHE_SECONDS", "30"))
TORN_API_MAX_REQUESTS_PER_MINUTE = int(os.getenv("TORN_API_MAX_REQUESTS_PER_MINUTE", "60"))
