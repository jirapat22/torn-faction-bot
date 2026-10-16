"""
Configuration file for Torn Faction Discord Bot
Railway deployment version - uses environment variables
"""

import os
from dotenv import load_dotenv

# Load .env file for local development (Railway ignores this)
load_dotenv()

# Bot version
BOT_VERSION = "2.5.0"

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
EVENT_POSTING_CHANNEL_ID = int(os.getenv("EVENT_POSTING_CHANNEL_ID") or 0)  # event-posting (blank = unset)
WAR_TRACKER_CHANNEL_ID = int(os.getenv("WAR_TRACKER_CHANNEL_ID", "1530733433721192498"))  # enemy flight alerts

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

# New-member trial. The guides promise "reach level 15 within 2 weeks" and
# "your first 2 weeks are a trial, at the end management reviews your
# progress" - one deadline, so one feature. The warning lands early enough
# that a member can still act on it.
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "14"))
TRIAL_TARGET_LEVEL = int(os.getenv("TRIAL_TARGET_LEVEL", "15"))
TRIAL_WARNING_DAYS = int(os.getenv("TRIAL_WARNING_DAYS", "10"))

# How long an issued strike counts against a member. The leader's concern was
# that "too many strikes might be difficult to monitor" - expiry is what keeps
# the active list short enough to act on, while the record itself is kept
# forever so history stays auditable.
STRIKE_EXPIRY_DAYS = int(os.getenv("STRIKE_EXPIRY_DAYS", "60"))

# The leader, pinged for Xanax reimbursements since that is their job alone.
# Unset falls back to the management role, so nothing is silently unaddressed.
LEADER_USER_ID = int(os.getenv("LEADER_USER_ID", "0"))

# The war guide post, linked when warning somebody about bleeding so the
# message carries the fix and not just the accusation.
WAR_GUIDE_CHANNEL_ID = int(os.getenv("WAR_GUIDE_CHANNEL_ID", "0"))

# Weekday to post the strike digest, 0 = Monday. Weekly rather than daily:
# strikes expire at 60 days and move slowly, so a daily post would mostly
# repeat itself and become wallpaper.
STRIKE_DIGEST_WEEKDAY = int(os.getenv("STRIKE_DIGEST_WEEKDAY", "0"))

# Hours before a war starts to check who has gone quiet. The rule is that
# members offline this long when we enlist get removed rather than struck,
# so this fires BEFORE the war instead of reporting it afterwards.
PREWAR_OFFLINE_HOURS = int(os.getenv("PREWAR_OFFLINE_HOURS", "24"))

# Left-faction alert threshold (days before alerting about a verified member not in faction)
# Grace period for someone who leaves the faction: they keep their verified
# role (and so their channel access) this many days before it is revoked, and
# management is alerted at the same moment rather than after the fact.
LEFT_FACTION_ALERT_DAYS = int(os.getenv("LEFT_FACTION_ALERT_DAYS", "3"))

# Embed color (Discord color codes)
EMBED_COLOR_INFO = 0x3498db     # Blue for info messages
EMBED_COLOR_WARNING = 0xf39c12  # Orange for warnings
EMBED_COLOR_CRITICAL = 0xe74c3c # Red for critical alerts
EMBED_COLOR_SUCCESS = 0x2ecc71  # Green for success messages

# Faction's verified-member role name (used for role sync, war-end ping,
# and availability polls)
VERIFIED_ROLE_NAME = os.getenv("VERIFIED_ROLE_NAME", "Ascended punks")

# Channel ID for availability polls (0 = use command channel)
AVAILABILITY_CHANNEL_ID = int(os.getenv("AVAILABILITY_CHANNEL_ID", "0"))

# ===== TURTLE ALERTS =====
# Tunable without a deploy, because the right value is a judgement call about
# noise. Defaults are measured against war 50338: 4-in-60 gave 12 alerts/day
# covering 16 members, where 2-in-30 would have given 128/day for 33 of 35.
TURTLE_HIT_THRESHOLD = int(os.getenv("TURTLE_HIT_THRESHOLD", "4"))
TURTLE_WINDOW_MINUTES = int(os.getenv("TURTLE_WINDOW_MINUTES", "60"))

# Continuous hours offline during a war before it is worth management's
# attention. 24 is measured, not picked: across war 50338 the members who
# actually fought still went quiet for up to 22.3 hours (median 11.3), so
# anything lower flags people who were contributing.
WAR_OFFLINE_STRIKE_HOURS = int(os.getenv("WAR_OFFLINE_STRIKE_HOURS", "24"))

# Where rule-break candidates go. Falls back to the inactivity channel when
# unset, so nothing is silently dropped if this is never configured.
STRIKES_CHANNEL_ID = int(os.getenv("STRIKES_CHANNEL_ID", "0"))

# ===== API RATE LIMITING =====
# Torn API rate limit settings
TORN_API_CACHE_SECONDS = int(os.getenv("TORN_API_CACHE_SECONDS", "30"))
TORN_API_MAX_REQUESTS_PER_MINUTE = int(os.getenv("TORN_API_MAX_REQUESTS_PER_MINUTE", "60"))
