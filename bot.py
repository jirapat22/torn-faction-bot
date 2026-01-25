"""
Torn Faction Discord Bot - Main Entry Point
Phase 3: Chain Tracking + Inactivity Tracking
"""

import discord
from discord.ext import commands, tasks
import asyncio
import os
from datetime import datetime

# Import our modules
from config import BOT_TOKEN, COMMAND_PREFIX
from features.inactivity import InactivityTracker
from features.chains import ChainTracker
from features.wars import WarTracker
from features.verification import VerificationSystem, setup_verification_commands
from features.commands import setup_commands
from utils.helpers import ensure_data_directories
from utils.torn_api import verify_api_key

# Set up intents (permissions for what the bot can see)
intents = discord.Intents.default()
intents.members = True  # Required to see member info
intents.guilds = True  # Required to see server info
# Note: We don't need message_content anymore - using Torn API instead!

# Create bot instance
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# Initialize trackers
inactivity_tracker = None
chain_tracker = None
war_tracker = None
verification_system = None


@bot.event
async def on_ready():
    """Called when the bot successfully connects to Discord"""
    global inactivity_tracker, chain_tracker, war_tracker, verification_system

    print(f'Bot logged in as {bot.user.name} (ID: {bot.user.id})')
    print(f'Connected to {len(bot.guilds)} server(s)')
    print('------')

    # Ensure data directories exist
    ensure_data_directories()

    # Verify Torn API key
    print('Verifying Torn API key...')
    api_valid = await verify_api_key()
    if api_valid:
        print('Torn API key verified successfully!')
    else:
        print('WARNING: Torn API key is invalid or faction ID is wrong!')
        print('Please check TORN_API_KEY and TORN_FACTION_ID in config.py')

    # Initialize inactivity tracker
    inactivity_tracker = InactivityTracker(bot)
    await inactivity_tracker.load_user_mapping()

    # Initialize chain tracker (Phase 3)
    chain_tracker = ChainTracker(bot)
    await chain_tracker.load_chain_history()

    # Initialize war tracker (Phase 4)
    war_tracker = WarTracker(bot)
    await war_tracker.load_war_history()

    # Initialize verification system
    verification_system = VerificationSystem(bot)
    await verification_system.load_config()
    await setup_verification_commands(bot, verification_system)

    # Register slash commands
    await setup_commands(bot, inactivity_tracker, chain_tracker, war_tracker)

    # Sync slash commands with Discord (guild-specific for instant updates)
    from config import GUILD_ID
    try:
        guild = discord.Object(id=GUILD_ID)

        # Clear global commands first (removes duplicates)
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()  # Sync empty global commands

        # Now sync to guild only
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f'Synced {len(synced)} slash command(s) to guild {GUILD_ID}')
        print('Global commands cleared - no more duplicates')
    except Exception as e:
        print(f'Failed to sync commands: {e}')

    # Start background tasks
    if not daily_inactivity_check.is_running():
        daily_inactivity_check.start()

    if not chain_update_check.is_running():
        chain_update_check.start()

    if not war_update_check.is_running():
        war_update_check.start()

    print('Bot is ready!')


# No longer tracking Discord messages - using Torn API instead!


@tasks.loop(hours=24)
async def daily_inactivity_check():
    """Background task that runs every 24 hours to check for inactive members"""
    if inactivity_tracker:
        await inactivity_tracker.check_and_alert_inactive()


@daily_inactivity_check.before_loop
async def before_daily_check():
    """Wait until bot is ready before starting the daily check loop"""
    await bot.wait_until_ready()

    # Wait until next 12:00 PM to start the loop
    now = datetime.now()
    target_hour = 12   # 12 PM (noon)

    # Calculate seconds until next 12 PM
    if now.hour >= target_hour:
        # If it's past 12 PM today, wait until 12 PM tomorrow
        seconds_until_target = ((24 - now.hour + target_hour) * 3600
                               - now.minute * 60
                               - now.second)
    else:
        # If it's before 12 PM today, wait until 12 PM today
        seconds_until_target = ((target_hour - now.hour) * 3600
                               - now.minute * 60
                               - now.second)

    print(f'Daily inactivity check will start in {seconds_until_target/3600:.1f} hours (at 12:00 PM)')
    await asyncio.sleep(seconds_until_target)


@tasks.loop(minutes=1)
async def chain_update_check():
    """Background task that runs every minute to check chain updates"""
    if chain_tracker:
        await chain_tracker.check_chain_update()


@chain_update_check.before_loop
async def before_chain_check():
    """Wait until bot is ready before starting the chain check loop"""
    await bot.wait_until_ready()
    print('Chain update checker started (checks every minute)')


@tasks.loop(minutes=1)
async def war_update_check():
    """Background task that runs every minute to check war updates"""
    if war_tracker:
        await war_tracker.check_war_update()


@war_update_check.before_loop
async def before_war_check():
    """Wait until bot is ready before starting the war check loop"""
    await bot.wait_until_ready()
    print('War update checker started (checks every minute)')


@bot.event
async def on_error(event, *args, **kwargs):
    """Global error handler"""
    print(f'Error in {event}:')
    import traceback
    traceback.print_exc()


# Run the bot
if __name__ == '__main__':
    if not BOT_TOKEN:
        print('ERROR: Bot token not found in config.py!')
        print('Please add your bot token to config.py')
    else:
        try:
            bot.run(BOT_TOKEN)
        except discord.LoginFailure:
            print('ERROR: Invalid bot token!')
            print('Please check your bot token in config.py')
        except Exception as e:
            print(f'ERROR: Failed to start bot: {e}')
