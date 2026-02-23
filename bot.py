"""
Torn Faction Discord Bot - Main Entry Point
Full featured bot with war tracking, chain tracking, verification, and more
"""

import sys
# Force unbuffered output for Railway logs
sys.stdout.reconfigure(line_buffering=True)

import discord
from discord.ext import commands, tasks
import asyncio
import os
import time
import traceback
from datetime import datetime, timezone

# Import our modules
from config import BOT_TOKEN, COMMAND_PREFIX
from features.inactivity import InactivityTracker
from features.chains import ChainTracker
from features.wars import WarTracker
from features.verification import VerificationSystem, setup_verification_commands
from features.roles import RoleSyncManager, setup_rolesync_commands
from features.enemies import EnemyWatchlist, setup_enemy_commands
from features.availability import AvailabilityManager, setup_availability_commands, restore_availability_views
from features.event_reminders import EventReminderManager
from features.stakeout import StakeoutManager, setup_stakeout_commands
from features.alliance import AllianceManager, setup_alliance_commands
from features.commands import setup_commands
from features.faction import setup_faction_commands
from utils.helpers import ensure_data_directories
from utils.torn_api import verify_api_key

# Set up intents (permissions for what the bot can see)
intents = discord.Intents.default()
intents.members = True  # Required to see member info
intents.guilds = True  # Required to see server info
intents.message_content = True  # Required by commands.Bot
intents.guild_scheduled_events = True  # Required to see scheduled events

# Create bot instance
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# Initialize trackers (set in on_ready)
inactivity_tracker = None
chain_tracker = None
war_tracker = None
verification_system = None
role_sync_manager = None
enemy_watchlist = None
availability_manager = None
event_reminder_manager = None
stakeout_manager = None
alliance_manager = None
_initialized = False  # Prevent re-initialization on reconnect


@bot.event
async def on_ready():
    """Called when the bot successfully connects to Discord"""
    global inactivity_tracker, chain_tracker, war_tracker, verification_system
    global role_sync_manager, enemy_watchlist, availability_manager
    global event_reminder_manager, stakeout_manager, alliance_manager, _initialized

    try:
        print(f'Bot logged in as {bot.user.name} (ID: {bot.user.id})')
        print(f'Connected to {len(bot.guilds)} server(s)')
        print('------')

        # Skip full initialization on reconnect to preserve in-memory state
        if _initialized:
            print('Reconnected - skipping re-initialization')
            return

        # Mark initialized early to prevent duplicate command registration on reconnect
        _initialized = True

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

        # Initialize chain tracker
        chain_tracker = ChainTracker(bot)
        await chain_tracker.load_chain_history()
        await chain_tracker.load_chain_session()

        # Initialize war tracker
        war_tracker = WarTracker(bot)
        await war_tracker.load_war_history()
        await war_tracker.load_war_member_stats()

        # Initialize verification system
        verification_system = VerificationSystem(bot)
        verification_system.inactivity_tracker = inactivity_tracker  # Sync both databases
        await verification_system.load_config()
        await setup_verification_commands(bot, verification_system)

        # Initialize role sync manager
        role_sync_manager = RoleSyncManager(bot)
        await role_sync_manager.load_config()
        await setup_rolesync_commands(bot, role_sync_manager)

        # Initialize enemy watchlist
        enemy_watchlist = EnemyWatchlist(bot)
        await enemy_watchlist.load_watchlist()
        await setup_enemy_commands(bot, enemy_watchlist)

        # Link enemy watchlist to war tracker for auto-clear
        war_tracker.enemy_watchlist = enemy_watchlist

        # Initialize availability polling system
        from config import VERIFIED_ROLE_NAME
        availability_manager = AvailabilityManager(bot)
        await availability_manager.load_data()
        await setup_availability_commands(bot, availability_manager, VERIFIED_ROLE_NAME)
        await restore_availability_views(bot, availability_manager, VERIFIED_ROLE_NAME)

        # Initialize event reminder manager (35-hour advance reminders)
        event_reminder_manager = EventReminderManager(bot)
        await event_reminder_manager.load_data()

        # Initialize stakeout manager (enemy behavior tracking)
        stakeout_manager = StakeoutManager(bot)
        await stakeout_manager.load_data()
        await setup_stakeout_commands(bot, stakeout_manager)

        # Initialize alliance manager (ally faction war tracking)
        alliance_manager = AllianceManager(bot)
        await alliance_manager.load_data()
        await setup_alliance_commands(bot, alliance_manager)

        # Initialize faction tools
        await setup_faction_commands(bot)

        # Register slash commands
        await setup_commands(
            bot, inactivity_tracker, chain_tracker, war_tracker, verification_system,
            event_reminder_manager=event_reminder_manager,
            role_sync_manager=role_sync_manager,
            enemy_watchlist=enemy_watchlist,
            stakeout_manager=stakeout_manager,
            alliance_manager=alliance_manager
        )

        # Sync slash commands with Discord (guild-specific for instant updates)
        from config import GUILD_ID
        try:
            guild = discord.Object(id=GUILD_ID)

            # Step 1: Copy commands to guild FIRST (before clearing)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f'Synced {len(synced)} slash command(s) to guild {GUILD_ID}')

            # Step 2: Clear global commands (removes duplicates from other servers)
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()  # Sync empty global
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

        if not role_sync_check.is_running():
            role_sync_check.start()

        if not enemy_status_check.is_running():
            enemy_status_check.start()

        if not event_reminder_check.is_running():
            event_reminder_check.start()

        if not stakeout_check.is_running():
            stakeout_check.start()

        if not alliance_war_check.is_running():
            alliance_war_check.start()

        if not poll_end_check.is_running():
            poll_end_check.start()

        print('Bot is ready!')

    except Exception as e:
        print(f'CRITICAL ERROR in on_ready: {e}')
        traceback.print_exc()


@tasks.loop(hours=24)
async def daily_inactivity_check():
    """Background task that runs every 24 hours to check for inactive members"""
    try:
        if inactivity_tracker:
            await inactivity_tracker.check_and_alert_inactive()
    except Exception as e:
        print(f'Error in daily inactivity check: {e}')


@daily_inactivity_check.before_loop
async def before_daily_check():
    """Wait until bot is ready before starting the daily check loop"""
    await bot.wait_until_ready()

    # Wait until next 00:00 TCT (UTC) to start the loop
    now = datetime.now(timezone.utc)
    # Seconds elapsed since midnight UTC today
    seconds_since_midnight = now.hour * 3600 + now.minute * 60 + now.second
    # Seconds until next midnight UTC
    seconds_until_target = 86400 - seconds_since_midnight

    print(f'Daily inactivity check will start in {seconds_until_target/3600:.1f} hours (at 00:00 TCT)')
    await asyncio.sleep(seconds_until_target)


@tasks.loop(minutes=1)
async def chain_update_check():
    """Background task that runs every minute to check chain updates"""
    try:
        if chain_tracker:
            await chain_tracker.check_chain_update()
    except Exception as e:
        print(f'Error in chain update check: {e}')


@chain_update_check.before_loop
async def before_chain_check():
    """Wait until bot is ready before starting the chain check loop"""
    await bot.wait_until_ready()
    print('Chain update checker started (checks every minute)')


@tasks.loop(minutes=1)
async def war_update_check():
    """Background task that runs every minute to check war updates"""
    try:
        if war_tracker:
            await war_tracker.check_war_update()
    except Exception as e:
        print(f'Error in war update check: {e}')


@war_update_check.before_loop
async def before_war_check():
    """Wait until bot is ready before starting the war check loop"""
    await bot.wait_until_ready()
    print('War update checker started (checks every minute)')


@tasks.loop(hours=1)
async def role_sync_check():
    """Background task that runs every hour to sync roles"""
    try:
        if role_sync_manager:
            await role_sync_manager.sync_all_members()
    except Exception as e:
        print(f'Error in role sync check: {e}')


@role_sync_check.before_loop
async def before_role_sync():
    """Wait until bot is ready before starting role sync"""
    await bot.wait_until_ready()
    print('Role sync checker started (checks every hour)')


@tasks.loop(minutes=15)
async def enemy_status_check():
    """Background task that runs every 15 minutes to check enemy status"""
    try:
        if enemy_watchlist and enemy_watchlist.get_enemy_count() > 0:
            await enemy_watchlist.check_all_enemies(post_updates=True)
    except Exception as e:
        print(f'Error in enemy status check: {e}')


@enemy_status_check.before_loop
async def before_enemy_check():
    """Wait until bot is ready before starting enemy check"""
    await bot.wait_until_ready()
    print('Enemy status checker started (checks every 15 minutes)')


@tasks.loop(hours=1)
async def event_reminder_check():
    """Background task that runs every hour to check for events starting in ~35 hours"""
    try:
        if event_reminder_manager:
            await event_reminder_manager.check_and_announce_events()
    except Exception as e:
        print(f'Error in event reminder check: {e}')


@event_reminder_check.before_loop
async def before_event_reminder_check():
    """Wait until bot is ready before starting event reminder check"""
    await bot.wait_until_ready()
    print('Event reminder checker started (checks every hour for 35h advance reminders)')


@tasks.loop(minutes=15)
async def stakeout_check():
    """Background task that runs every 15 minutes to check stakeout targets"""
    try:
        if stakeout_manager and stakeout_manager.get_stakeout_count() > 0:
            await stakeout_manager.check_all_stakeouts()
    except Exception as e:
        print(f'Error in stakeout check: {e}')


@stakeout_check.before_loop
async def before_stakeout_check():
    """Wait until bot is ready before starting stakeout check"""
    await bot.wait_until_ready()
    print('Stakeout checker started (checks every 15 minutes)')


@tasks.loop(minutes=5)
async def alliance_war_check():
    """Background task that runs every 5 minutes to check ally faction wars"""
    try:
        if alliance_manager and alliance_manager.get_ally_count() > 0:
            await alliance_manager.check_ally_wars()
    except Exception as e:
        print(f'Error in alliance war check: {e}')


@alliance_war_check.before_loop
async def before_alliance_war_check():
    """Wait until bot is ready before starting alliance war check"""
    await bot.wait_until_ready()
    print('Alliance war checker started (checks every 5 minutes)')


@tasks.loop(minutes=1)
async def poll_end_check():
    """Background task to check if any availability polls have ended"""
    try:
        if availability_manager:
            await availability_manager.check_poll_endings()
    except Exception as e:
        print(f'Error in poll end check: {e}')


@poll_end_check.before_loop
async def before_poll_end_check():
    """Wait until bot is ready before starting poll end check"""
    await bot.wait_until_ready()
    print('Poll end checker started (checks every minute)')


@bot.event
async def on_member_join(member):
    """Welcome new members when they join the server"""
    from config import WELCOME_CHANNEL_ID

    # Use plain text channel names (not <#ID> mentions) so unverified users can read them
    welcome_message = (
        f"🎉 Welcome to Ascended Punks, {member.mention}!\n\n"
        f"1️⃣ Run `/register` with your Torn ID in **#welcome-pit**.\n"
        f"2️⃣ Head to **#faction-edicts** and read the rules.\n"
        f"3️⃣ Once you're Verified, jump into **#punk-plaza** and say hi.\n\n"
        f"Please contact Ikaros22[3571142] if there is any issue with register process."
    )

    # Send to welcome channel
    if WELCOME_CHANNEL_ID:
        channel = bot.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            try:
                await channel.send(welcome_message)
                print(f'Welcomed new member: {member.name}')
                return
            except Exception as e:
                print(f'Failed to send welcome message to channel: {e}')
        else:
            print(f'Welcome channel {WELCOME_CHANNEL_ID} not found')

    # Fallback: DM the user only if channel send failed
    try:
        await member.send(welcome_message.replace(member.mention, member.name))
        print(f'Welcomed new member via DM (channel unavailable): {member.name}')
    except Exception as e:
        print(f'Failed to DM welcome message to {member.name}: {e}')


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
