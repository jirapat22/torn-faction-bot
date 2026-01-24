"""
Quick .env file generator for Torn Faction Bot
Helps you create .env file from your current config.py values
"""

import os

print("=== Torn Faction Bot - .env Generator ===\n")
print("This script will create a .env file using your current config.py values.\n")

# Check if config_local_backup.py exists (your original config)
if os.path.exists("config_local_backup.py"):
    print("Found config_local_backup.py - using your original hardcoded values\n")

    # Import the backup config
    import importlib.util
    spec = importlib.util.spec_from_file_location("config_backup", "config_local_backup.py")
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)

    # Create .env content
    env_content = f"""# Torn Faction Discord Bot - Environment Variables
# Generated from your config_local_backup.py

# ===== DISCORD BOT TOKEN =====
DISCORD_TOKEN={config.BOT_TOKEN}

# ===== DISCORD SERVER & CHANNEL IDS =====
GUILD_ID={config.GUILD_ID}
INACTIVITY_ALERTS_CHANNEL_ID={config.INACTIVITY_ALERTS_CHANNEL_ID}
ANNOUNCEMENTS_CHANNEL_ID={config.ANNOUNCEMENTS_CHANNEL_ID}
WAR_CHANNEL_ID={config.WAR_CHANNEL_ID}
WAR_MANAGEMENT_CHANNEL_ID={config.WAR_MANAGEMENT_CHANNEL_ID}
CHAINING_CHANNEL_ID={config.CHAINING_CHANNEL_ID}
HAPPY_JUMP_CHANNEL_ID={config.HAPPY_JUMP_CHANNEL_ID}

# ===== TORN API SETTINGS =====
TORN_API_KEY={config.TORN_API_KEY}
TORN_FACTION_ID={config.TORN_FACTION_ID}

# ===== MANAGEMENT ROLE IDS =====
MANAGEMENT_ROLE_IDS={','.join(map(str, config.MANAGEMENT_ROLE_IDS))}

# ===== BOT SETTINGS =====
COMMAND_PREFIX={config.COMMAND_PREFIX}
INACTIVITY_WARNING_DAYS={config.INACTIVITY_WARNING_DAYS}
INACTIVITY_CRITICAL_DAYS={config.INACTIVITY_CRITICAL_DAYS}
TORN_API_CACHE_SECONDS={config.TORN_API_CACHE_SECONDS}
TORN_API_MAX_REQUESTS_PER_MINUTE={config.TORN_API_MAX_REQUESTS_PER_MINUTE}
"""

    # Write .env file
    with open('.env', 'w') as f:
        f.write(env_content)

    print("✅ Created .env file with your values!")
    print("\nYou can now:")
    print("1. Test locally: python bot.py")
    print("2. Deploy to Railway (see RAILWAY_DEPLOYMENT_GUIDE.md)")

else:
    print("❌ Could not find config_local_backup.py")
    print("\nPlease run this first:")
    print("  copy config.py config_local_backup.py")
    print("\nThen run this script again.")
