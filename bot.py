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
from config import (BOT_TOKEN, COMMAND_PREFIX, GUILD_ID, TORN_API_KEY, TORN_FACTION_ID,
                    VERIFIED_ROLE_NAME)
from features.inactivity import InactivityTracker
from features.chains import ChainTracker
from features.wars import WarTracker
from features.verification import VerificationSystem, setup_verification_commands
from features.roles import RoleSyncManager, setup_rolesync_commands
from features.activity_profile import ActivityProfileManager
from features.enemies import EnemyWatchlist, setup_enemy_commands
from features.availability import AvailabilityManager, setup_availability_commands, restore_availability_views
from features.event_reminders import EventReminderManager
from features.stakeout import StakeoutManager, setup_stakeout_commands
from features.flytrack import FlyTracker
from features.commands import setup_commands
from features.faction import setup_faction_commands
from utils.helpers import ensure_data_directories
from utils.torn_api import verify_api_key, close_session

import logging
from logging.handlers import RotatingFileHandler

# Configure root logger: timestamped, levelled output to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-7s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout,
)
# Quieten noisy discord.py internals
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _add_file_log():
    """
    Also write the log to data/logs/bot.log. On the Mac, data/ is a mounted
    folder, so this survives deploys; Docker's own log does not, because every
    deploy rebuilds the container and its log goes with it. That left a
    health check able to see only the 2 minutes since the last deploy.

    Rotates at 2 MB keeping 5 old files (~3 weeks, ~12 MB at most), and leaves
    out the once-a-minute health check requests, which were most of the noise.
    """
    try:
        os.makedirs('data/logs', exist_ok=True)
        handler = RotatingFileHandler('data/logs/bot.log', maxBytes=2_000_000,
                                      backupCount=5, encoding='utf-8')
        handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-7s [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'))
        handler.addFilter(lambda record: record.name != 'aiohttp.access')
        logging.getLogger().addHandler(handler)
    except OSError as e:
        # A log file is a convenience; never the reason the bot won't start.
        logger.warning(f'File log disabled: {e}')


_add_file_log()

# Set up intents (permissions for what the bot can see)
intents = discord.Intents.default()
intents.members = True  # Required to see member info
intents.guilds = True  # Required to see server info
intents.guild_scheduled_events = True  # Required to see scheduled events

# Create bot instance
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


# Clean shutdown: release the shared aiohttp session before closing the bot.
_original_close = bot.close


async def _bot_close():
    try:
        if _healthcheck_runner is not None:
            await _healthcheck_runner.cleanup()
        await close_session()
    finally:
        await _original_close()


bot.close = _bot_close

# Initialize trackers (set in on_ready)
inactivity_tracker = None
chain_tracker = None
war_tracker = None
verification_system = None
strike_manager = None
role_sync_manager = None
activity_profile_manager = None
enemy_watchlist = None
availability_manager = None
event_reminder_manager = None
stakeout_manager = None
fly_tracker = None
chain_event_manager = None
target_manager = None
_initialized = False  # Prevent re-initialization on reconnect


_healthcheck_runner = None


async def _start_healthcheck_server():
    """
    Spin up a tiny aiohttp server on $PORT for Railway's healthcheck probe.
    No-op if PORT isn't set (local development) or already running.
    """
    global _healthcheck_runner
    if _healthcheck_runner is not None:
        return
    port_str = os.getenv('PORT')
    if not port_str:
        return  # local run - skip
    try:
        port = int(port_str)
    except ValueError:
        logger.warning(f'PORT env var is not numeric: {port_str!r}; skipping healthcheck')
        return

    from aiohttp import web

    async def handle(request):
        # Healthy = bot logged in AND every background loop still running
        loops = [
            daily_inactivity_check, chain_update_check, war_update_check,
            role_sync_check, enemy_status_check, event_reminder_check,
            stakeout_check, fly_check, poll_end_check,
            retal_check, target_board_check, timezone_board_check,
        ]
        all_running = bot.is_ready() and all(loop.is_running() for loop in loops)
        body = {
            'status': 'healthy' if all_running else 'degraded',
            'discord_ready': bot.is_ready(),
            'loops_running': sum(1 for loop in loops if loop.is_running()),
            'loops_total': len(loops),
        }
        return web.json_response(body, status=200 if all_running else 503)

    app = web.Application()
    app.router.add_get('/', handle)
    app.router.add_get('/health', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    _healthcheck_runner = runner
    logger.info(f'Healthcheck server listening on 0.0.0.0:{port}')


@bot.event
async def on_ready():
    """Called when the bot successfully connects to Discord"""
    global inactivity_tracker, chain_tracker, war_tracker, verification_system
    global role_sync_manager, activity_profile_manager, enemy_watchlist, availability_manager
    global event_reminder_manager, stakeout_manager, fly_tracker, _initialized
    global strike_manager, chain_event_manager, target_manager

    try:
        logger.info(f'Bot logged in as {bot.user.name} (ID: {bot.user.id})')
        logger.info(f'Connected to {len(bot.guilds)} server(s)')
        logger.info('------')

        # Skip full initialization on reconnect to preserve in-memory state
        if _initialized:
            logger.info('Reconnected - skipping re-initialization')
            return

        # Mark initialized early so a mid-init crash doesn't cause duplicate
        # command registration on a subsequent reconnect. A crash here
        # requires a full process restart (Railway handles that).
        _initialized = True
        bot.start_time = datetime.now(timezone.utc)

        # Ensure data directories exist
        ensure_data_directories()

        # Register persistent views so their buttons survive restarts
        # (the newsletter nudge's "Create Event" button)
        from features.event_ui import CreateEventPromptView
        bot.add_view(CreateEventPromptView())

        # The timezone picker's two dropdowns. Fixed custom_ids, so one
        # registration here keeps every previously posted menu working.
        from features.timezones import TimezonePicker
        bot.add_view(TimezonePicker())

        # Verify Torn API key
        logger.info('Verifying Torn API key...')
        api_valid = await verify_api_key()
        if api_valid:
            logger.info('Torn API key verified successfully!')
        else:
            logger.warning('Torn API key is invalid or faction ID is wrong!')
            logger.info('Please check TORN_API_KEY and TORN_FACTION_ID in config.py')

        # Initialize inactivity tracker
        inactivity_tracker = InactivityTracker(bot)
        await inactivity_tracker.load_user_mapping()
        await inactivity_tracker.load_oc_data()
        await inactivity_tracker.load_trial_data()
        await inactivity_tracker.load_left_faction_data()

        # Strike ledger. Constructed early because the detections below hand
        # it candidates, and its buttons have to be re-registered on startup.
        from features.strikes import (StrikeManager, StrikeActionView,
                                      StrikeDeliveredView)
        strike_manager = StrikeManager(bot)
        await strike_manager.load()
        # Re-register a view per OPEN candidate, and per strike still awaiting
        # someone to deliver it. discord.py matches a persistent view by exact
        # custom_id, and ours carry the candidate key or strike id, so a
        # generic view would leave every pre-restart button dead.
        for _key in list(strike_manager.pending):
            bot.add_view(StrikeActionView(strike_manager, _key))
        _awaiting = strike_manager.awaiting_notice()
        for _s in _awaiting:
            bot.add_view(StrikeDeliveredView(strike_manager, _s['id']))
        logger.info(f'Strikes: restored {len(strike_manager.pending)} candidate '
                    f'view(s), {len(_awaiting)} awaiting notice')

        # Initialize chain tracker
        chain_tracker = ChainTracker(bot)
        await chain_tracker.load_chain_history()
        await chain_tracker.load_chain_session()

        # Initialize war tracker
        war_tracker = WarTracker(bot)
        await war_tracker.load_war_history()
        await war_tracker.load_war_member_stats()
        await war_tracker.load_pending_wars()

        # Chain events: a named window that many chains fall inside, so a
        # 5-day Halloween event is one per-member total instead of a dozen
        # separate chains nobody can add up. Takes war_tracker for its
        # hardened attacks paginator rather than growing a second one.
        from features.chain_events import ChainEventManager
        from config import TORN_FACTION_ID
        chain_event_manager = ChainEventManager(bot, war_tracker,
                                                TORN_FACTION_ID)
        # The shortfall list files candidates through the ledger; without it
        # the button says so rather than silently doing nothing.
        chain_event_manager.strike_manager = strike_manager

        # Initialize verification system
        verification_system = VerificationSystem(bot)
        verification_system.inactivity_tracker = inactivity_tracker  # Sync both databases
        # Reverse link: the daily left-faction check needs the configured
        # verified role id to revoke access once the grace period expires.
        inactivity_tracker.verification_system = verification_system
        # The strike ledger needs verification to map a Torn id to a Discord
        # user for the DM; the detections need the ledger to post candidates.
        strike_manager.verification_system = verification_system
        inactivity_tracker.strike_manager = strike_manager
        # Turns the Discord user running /chain into a Torn id, so the
        # member view can show them their own rank.
        chain_event_manager.verification_system = verification_system
        await verification_system.load_config()
        await setup_verification_commands(bot, verification_system)

        # Initialize role sync manager
        role_sync_manager = RoleSyncManager(bot)
        await role_sync_manager.load_config()
        await setup_rolesync_commands(bot, role_sync_manager)

        # Initialize activity profile manager (tracks typical online hours per
        # member, fed by the same hourly faction fetch role sync already makes)
        activity_profile_manager = ActivityProfileManager(bot)
        await activity_profile_manager.load_data()

        # Initialize enemy watchlist - builds the /enemy command group
        # (stakeout attaches to it below; added to the tree once, after)
        enemy_watchlist = EnemyWatchlist(bot)
        await enemy_watchlist.load_watchlist()
        enemy_group = await setup_enemy_commands(bot, enemy_watchlist)

        # Link enemy watchlist to war tracker for auto-clear
        war_tracker.enemy_watchlist = enemy_watchlist
        # Needed to @-mention a member who is being farmed during a war
        war_tracker.verification_system = verification_system
        war_tracker.strike_manager = strike_manager
        await war_tracker.load_turtle_alerts()
        await war_tracker.load_revive_checks()
        await war_tracker.load_bleed_streaks()

        # Initialize availability polling system
        from config import VERIFIED_ROLE_NAME
        availability_manager = AvailabilityManager(bot)
        await availability_manager.load_data()
        await setup_availability_commands(bot, availability_manager, VERIFIED_ROLE_NAME,
                                           activity_profile_manager=activity_profile_manager)
        await restore_availability_views(bot, availability_manager, VERIFIED_ROLE_NAME)
        # Assigned here, not up with the other war_tracker wiring: that runs
        # before AvailabilityManager exists, so it would have bound None and
        # every absent member would have looked like they never answered.
        war_tracker.availability_manager = availability_manager

        # Initialize event reminder manager (35-hour advance reminders)
        event_reminder_manager = EventReminderManager(bot)
        await event_reminder_manager.load_data()

        # Initialize stakeout manager (enemy behavior tracking) -
        # nests as /enemy stakeout under the group built above
        stakeout_manager = StakeoutManager(bot)
        await stakeout_manager.load_data()
        await setup_stakeout_commands(bot, stakeout_manager, enemy_group)

        # Register the fully-assembled /enemy group (watchlist + stakeout)
        bot.tree.add_command(enemy_group)

        # Initialize fly tracker (enemy flight tracking during ranked war)
        fly_tracker = FlyTracker(bot, war_tracker=war_tracker)
        # Needed to turn a Torn id into a Discord mention when warning a member
        # that an enemy is flying to the country they're sitting in.
        fly_tracker.verification_system = verification_system
        await fly_tracker.load_data()

        # Target board and retals. Needs verification for the verified role
        # (who may add targets, and who can see the channel it creates).
        from features.targets import TargetManager, RetalView, setup_target_commands
        target_manager = TargetManager(bot)
        target_manager.verification_system = verification_system
        await target_manager.load()
        # One fixed custom_id, so this single registration keeps the
        # "Add to targets" button working on every retal posted before a restart.
        bot.add_view(RetalView(target_manager))
        await setup_target_commands(bot, target_manager)

        # Initialize faction tools
        await setup_faction_commands(bot)

        # Register slash commands
        await setup_commands(
            bot, inactivity_tracker, chain_tracker, war_tracker, verification_system,
            event_reminder_manager=event_reminder_manager,
            role_sync_manager=role_sync_manager,
            enemy_watchlist=enemy_watchlist,
            stakeout_manager=stakeout_manager,
            fly_tracker=fly_tracker,
            strike_manager=strike_manager,
            chain_event_manager=chain_event_manager
        )

        # Sync slash commands with Discord (guild-specific for instant updates)
        from config import GUILD_ID
        try:
            guild = discord.Object(id=GUILD_ID)

            # Step 1: Copy commands to guild FIRST (before clearing)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logger.info(f'Synced {len(synced)} slash command(s) to guild {GUILD_ID}')

            # Step 2: Clear global commands (removes duplicates from other servers)
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()  # Sync empty global
            logger.info('Global commands cleared - no more duplicates')
        except Exception as e:
            logger.error(f'Failed to sync commands: {e}')

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

        if not fly_check.is_running():
            fly_check.start()

        if not poll_end_check.is_running():
            poll_end_check.start()

        if not retal_check.is_running():
            retal_check.start()

        if not target_board_check.is_running():
            target_board_check.start()

        if not timezone_board_check.is_running():
            timezone_board_check.start()

        if not loop_watchdog.is_running():
            loop_watchdog.start()

        # Healthcheck HTTP endpoint for Railway (only starts if PORT is set,
        # so local runs aren't affected). Reports 200 if Discord is connected
        # AND every background loop is running; 503 otherwise.
        await _start_healthcheck_server()

        # Does every configured id still point at something real? Posted to
        # management rather than logged, because the failure it catches is
        # one that produces no error anywhere a human looks.
        try:
            from features.config_check import report as config_report
            outcome = await config_report(bot, bot.get_guild(GUILD_ID))
            if outcome and outcome != 'ok':
                logger.info(f'Config check: {outcome}')
        except Exception as e:
            logger.error(f'Config check failed: {e}')

        # Refresh the member command board. Runs last because it is the one
        # piece of startup that is purely cosmetic - it must never be the
        # reason the bot fails to come up, so it is both wrapped and silent
        # when the channel is not configured.
        try:
            from features.member_commands import refresh as refresh_commands
            result = await refresh_commands(bot)
            if result:
                logger.info(f'Member command board: {result}')

            # The pinned "how to get verified" post. Same pattern, different
            # audience: this one is the only instruction an UNVERIFIED member
            # can read, since the welcome pit is the only channel they see.
            from features.welcome_board import refresh as refresh_welcome
            result = await refresh_welcome(bot)
            if result:
                logger.info(f'Welcome board: {result}')

            from features.timezones import post_picker, refresh_board
            result = await post_picker(bot)
            if result:
                logger.info(f'Timezone picker: {result}')
            result = await refresh_board(bot, profile=activity_profile_manager)
            if result:
                logger.info(f'Timezone board: {result}')

            # Retal messages left over from before the restart, which the
            # store lost track of, would otherwise never be deleted.
            removed = await target_manager.sweep_stray_retals()
            if removed:
                logger.info(f'Targets: removed {removed} stray retal message(s)')
        except Exception as e:
            logger.error(f'Startup boards failed: {e}')

        logger.info('Bot is ready!')

    except Exception as e:
        logger.error(f'CRITICAL ERROR in on_ready: {e}')
        traceback.print_exc()


@tasks.loop(hours=24)
async def daily_inactivity_check():
    """Background task that runs every 24 hours to check for inactive members"""
    try:
        if inactivity_tracker:
            await inactivity_tracker.check_and_alert_inactive()
        # Weekly strike digest. Rides the daily loop and decides for itself
        # whether this is the day, so there is no second loop to keep alive.
        if strike_manager:
            await strike_manager.post_weekly_digest()

        # The Monday "who is quiet before we enlist" report. Rides the daily
        # loop and picks its own day, like the other weekly jobs.
        try:
            if war_tracker:
                await war_tracker.post_weekly_activity_check()
        except Exception as e:
            logger.error(f'Weekly activity check failed: {e}')

        # Torn-vs-Discord roster drift, weekly. Decides its own day, so
        # there is no extra loop to keep alive.
        try:
            from features.discord_roster import post_weekly_roster_check
            from config import ROSTER_CHECK_WEEKDAY, INACTIVITY_ALERTS_CHANNEL_ID
            await post_weekly_roster_check(
                bot, bot.get_guild(GUILD_ID), verification_system,
                INACTIVITY_ALERTS_CHANNEL_ID, ROSTER_CHECK_WEEKDAY)
        except Exception as e:
            logger.error(f'Weekly roster check failed: {e}')

        # Re-post the member command board if somebody deleted it. Cheap:
        # refresh() reads the channel and returns 'unchanged' unless the
        # board is actually missing or out of date.
        try:
            from features.member_commands import refresh as refresh_commands
            await refresh_commands(bot)
            from features.welcome_board import refresh as refresh_welcome
            await refresh_welcome(bot)
        except Exception as e:
            logger.error(f'Board refresh failed: {e}')

        # Channel cleanup. Rides the daily loop but sweeps WEEKLY - it
        # checks the weekday itself, so there is no second loop to keep
        # alive. Narrow by design: welcome pit and the war-tracker feed
        # only, never a channel you would cite in a dispute.
        try:
            from features.cleanup import CleanupManager
            swept = await CleanupManager(bot).sweep()
            if any(swept.values()):
                logger.info(f'Cleanup: {swept}')
        except Exception as e:
            logger.error(f'Cleanup failed: {e}')

        # Auto-verify using Torn's official Torn<->Discord account link, which the
        # player establishes through Torn itself and so proves ownership. A Discord
        # nickname does NOT - it's user-set, so anyone could claim another member's
        # Torn ID. Nickname matching stays manual/human-reviewed (/verify bulksync).
        # Unlinked members keep using /register as normal.
        if verification_system:
            guild = bot.get_guild(GUILD_ID)
            if guild:
                sync_result = await verification_system.autoverify_linked_members(guild)
                if sync_result:
                    logger.info(f"Daily auto-verify: {sync_result['verified']} verified via Torn link "
                                f"({sync_result['checked']} checked, "
                                f"{sync_result['unlinked']} not linked to Discord)")

                # Anyone still unregistered after auto-verify has not linked
                # their Torn account either, so the bot cannot find them on
                # its own - it has to ask them.
                try:
                    from features.lobby import run as lobby_run
                    await lobby_run(bot, guild, verification_system)
                except Exception as e:
                    logger.error(f'Lobby check failed: {e}')

                # Forum posts archive themselves after a week of quiet, and
                # an archived guide renders as "# unknown" everywhere the
                # index links to it. Pinning cannot hold them open: a forum
                # allows exactly one pinned post.
                try:
                    from features.guides import keep_unarchived
                    from config import GUIDES_HUB_CHANNEL_ID
                    n = await keep_unarchived(bot, GUIDES_HUB_CHANNEL_ID)
                    if n:
                        logger.info(f'Guides: un-archived {n} guide(s)')
                except Exception as e:
                    logger.error(f'Guides check failed: {e}')

                role_result = await verification_system.fixrole_members(guild)
                if role_result:
                    logger.info(f"Daily auto-verify: {role_result['fixed']} role(s) fixed, "
                                f"{role_result['failed']} failed")
    except Exception as e:
        logger.error(f'Error in daily inactivity check: {e}')


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

    logger.info(f'Daily inactivity check will start in {seconds_until_target/3600:.1f} hours (at 00:00 TCT)')
    await asyncio.sleep(seconds_until_target)


@tasks.loop(minutes=1)
async def chain_update_check():
    """Background task that runs every minute to check chain updates"""
    try:
        if chain_tracker:
            await chain_tracker.check_chain_update()
    except Exception as e:
        logger.error(f'Error in chain update check: {e}')

    # A scheduled chain event begins here. Checked every minute so a 16:00
    # start is announced at 16:00 and not up to an hour late. A start the bot
    # slept through still fires, and still counts from its scheduled time -
    # the totals are derived from the window, not from when we woke up.
    try:
        if chain_event_manager:
            from features.chain_events import announce_text
            from config import CHAINING_CHANNEL_ID
            for ev in chain_event_manager.due():
                chain_event_manager.begin(ev)
                channel = bot.get_channel(CHAINING_CHANNEL_ID)
                if channel:
                    text = announce_text(ev)
                    # The Events tab only says "2 Events" in a sidebar, so
                    # the announcement carries the event's own link. Posting
                    # the raw URL is what renders the card with Interested,
                    # which is how a member gets their own reminder.
                    try:
                        from features.calendar_sync import event_link
                        from config import GUILD_ID as _g
                        link = event_link(_g, f"chain:{ev['id']}")
                        if link:
                            text += (
                                '\n\n' + link + '\n-# Click **Interested** '
                                'and Discord will remind you itself.')
                    except Exception as e:
                        logger.debug(f'Chain event link lookup failed: {e}')
                    await channel.send(text)
                else:
                    logger.warning('Chain event: chaining channel not found, '
                                   'started without announcing')
    except Exception as e:
        logger.error(f'Error starting scheduled chain event: {e}')


@chain_update_check.before_loop
async def before_chain_check():
    """Wait until bot is ready before starting the chain check loop"""
    await bot.wait_until_ready()
    logger.info('Chain update checker started (checks every minute)')


@tasks.loop(minutes=1)
async def war_update_check():
    """Background task that runs every minute to check war updates"""
    try:
        if war_tracker:
            await war_tracker.check_war_update()
        # Piggybacks on the same cached rankedwars fetch war_tracker just made,
        # so asking every minute costs no extra API call. It posts at most once
        # per war, roughly two days out.
        if availability_manager and war_tracker:
            await availability_manager.check_war_poll(war_tracker, VERIFIED_ROLE_NAME)
        # War end already auto-stops fly tracking; this is the missing other
        # half. Runs here rather than in the fly loop, because that one only
        # ticks while tracking is already on.
        if fly_tracker and war_tracker:
            await fly_tracker.auto_start_for_new_war()
        # Farming check. Reads one page of the attacks log, so it is cheap
        # enough to run on the same minute tick as everything else here.
        if war_tracker:
            await war_tracker.check_turtle_alerts()
        # Pre-war revive check. Reuses the same cached rankedwars fetch, so
        # quiet ticks cost nothing; the per-member profile calls only happen on
        # the tick that actually posts, at most twice per war.
        if war_tracker:
            await war_tracker.check_revive_status()
    except Exception as e:
        logger.error(f'Error in war update check: {e}')


@war_update_check.before_loop
async def before_war_check():
    """Wait until bot is ready before starting the war check loop"""
    await bot.wait_until_ready()
    logger.info('War update checker started (checks every minute)')


@tasks.loop(hours=1)
async def role_sync_check():
    """
    Background task that runs every hour to sync roles, and to record an
    activity-profile snapshot from the same faction fetch (avoids a second
    Torn API call for the activity profile feature).
    """
    try:
        faction_members = None
        if (role_sync_manager and role_sync_manager.config.get('enabled', True)
                and role_sync_manager.position_roles) or activity_profile_manager:
            from utils.torn_api import fetch_faction_members
            faction_members = await fetch_faction_members()

        if role_sync_manager:
            await role_sync_manager.sync_all_members(faction_members=faction_members)

        # Discord's own Events tab, so "what is coming up" has an answer that
        # does not scroll away. Hourly rather than daily: an event scheduled
        # now should show up within the hour, not at the next midnight.
        try:
            from features.calendar_sync import sync as calendar_sync
            from config import GUILD_ID as _gid
            await calendar_sync(bot, bot.get_guild(_gid),
                                chain_event_manager, war_tracker)
        except Exception as e:
            logger.error(f'Calendar sync failed: {e}')
        if activity_profile_manager and faction_members:
            await activity_profile_manager.record_snapshot(faction_members)
        # The timezone board rides this tick too: the roster it needs for
        # "who is awake right now" was just fetched, so keeping the clocks
        # current costs no extra API call. Editing one embed, not posting.
        try:
            from features.timezones import refresh_board
            await refresh_board(bot, roster=faction_members,
                                profile=activity_profile_manager)
        except Exception as e:
            logger.error(f'Timezone board refresh failed: {e}')
    except Exception as e:
        logger.error(f'Error in role sync check: {e}')


@role_sync_check.before_loop
async def before_role_sync():
    """Wait until bot is ready before starting role sync"""
    await bot.wait_until_ready()
    logger.info('Role sync checker started (checks every hour)')


@tasks.loop(minutes=15)
async def enemy_status_check():
    """Background task that runs every 15 minutes to check enemy status"""
    try:
        if enemy_watchlist and enemy_watchlist.get_enemy_count() > 0:
            await enemy_watchlist.check_all_enemies(post_updates=True)
    except Exception as e:
        logger.error(f'Error in enemy status check: {e}')


@enemy_status_check.before_loop
async def before_enemy_check():
    """Wait until bot is ready before starting enemy check"""
    await bot.wait_until_ready()
    logger.info('Enemy status checker started (checks every 15 minutes)')


@tasks.loop(hours=1)
async def event_reminder_check():
    """Background task that runs every hour to check for events starting in ~35 hours"""
    try:
        if event_reminder_manager:
            await event_reminder_manager.check_and_announce_events()
            await event_reminder_manager.check_calendar_events()
            await event_reminder_manager.check_newsletters()
    except Exception as e:
        logger.error(f'Error in event reminder check: {e}')


@event_reminder_check.before_loop
async def before_event_reminder_check():
    """Wait until bot is ready before starting event reminder check"""
    await bot.wait_until_ready()
    logger.info('Event reminder checker started (checks every hour for 35h advance reminders)')


@tasks.loop(minutes=15)
async def stakeout_check():
    """Background task that runs every 15 minutes to check stakeout targets"""
    try:
        if stakeout_manager and stakeout_manager.get_stakeout_count() > 0:
            await stakeout_manager.check_all_stakeouts()
    except Exception as e:
        logger.error(f'Error in stakeout check: {e}')


@stakeout_check.before_loop
async def before_stakeout_check():
    """Wait until bot is ready before starting stakeout check"""
    await bot.wait_until_ready()
    logger.info('Stakeout checker started (checks every 15 minutes)')


@tasks.loop(seconds=60)
async def fly_check():
    """Background task that runs every 60 seconds to track enemy flights during war"""
    try:
        if fly_tracker and fly_tracker.enabled:
            await fly_tracker.check_flights()
    except Exception as e:
        logger.error(f'Error in fly check: {e}')


@fly_check.before_loop
async def before_fly_check():
    """Wait until bot is ready before starting fly check"""
    await bot.wait_until_ready()
    logger.info('Fly checker started (checks every 60 seconds while fly tracking is on)')


@tasks.loop(minutes=1)
async def poll_end_check():
    """Background task to check if any availability polls have ended"""
    try:
        if availability_manager:
            await availability_manager.check_poll_endings()
    except Exception as e:
        logger.error(f'Error in poll end check: {e}')


@poll_end_check.before_loop
async def before_poll_end_check():
    """Wait until bot is ready before starting poll end check"""
    await bot.wait_until_ready()
    logger.info('Poll end checker started (checks every minute)')


@tasks.loop(seconds=30)
async def retal_check():
    """Post, update and delete retal messages. 30s because the retal window is
    only 5 minutes, and Torn refreshes the attack log about that often anyway."""
    try:
        if target_manager:
            await target_manager.retal_tick()
    except Exception as e:
        logger.error(f'Error in retal check: {e}')


@retal_check.before_loop
async def before_retal_check():
    await bot.wait_until_ready()
    logger.info('Retal checker started (checks every 30 seconds)')


@tasks.loop(minutes=2)
async def target_board_check():
    """Refresh the pinned target board: statuses, stats, 7-day expiry."""
    try:
        if target_manager:
            await target_manager.refresh_board()
    except Exception as e:
        logger.error(f'Error in target board check: {e}')


@target_board_check.before_loop
async def before_target_board_check():
    await bot.wait_until_ready()
    logger.info('Target board started (refreshes every 2 minutes)')


@tasks.loop(minutes=5)
async def timezone_board_check():
    """Keep the timezone board's clocks current. It shows exact times
    ("6:44 pm"), and redrawn only hourly they were up to 59 minutes behind,
    which is the very "pinged at 4am" mistake the board exists to prevent.
    One cached roster lookup per redraw."""
    try:
        from features.timezones import refresh_board
        await refresh_board(bot, profile=activity_profile_manager)
    except Exception as e:
        logger.error(f'Error in timezone board check: {e}')


@timezone_board_check.before_loop
async def before_timezone_board_check():
    await bot.wait_until_ready()
    logger.info('Timezone board started (refreshes every 5 minutes)')


@tasks.loop(minutes=5)
async def loop_watchdog():
    """Restart any background task loop that has stopped unexpectedly."""
    loops = {
        'daily_inactivity_check': daily_inactivity_check,
        'chain_update_check': chain_update_check,
        'war_update_check': war_update_check,
        'role_sync_check': role_sync_check,
        'enemy_status_check': enemy_status_check,
        'event_reminder_check': event_reminder_check,
        'stakeout_check': stakeout_check,
        'fly_check': fly_check,
        'poll_end_check': poll_end_check,
        'retal_check': retal_check,
        'target_board_check': target_board_check,
        'timezone_board_check': timezone_board_check,
    }
    for name, loop in loops.items():
        if not loop.is_running():
            try:
                loop.start()
                logger.info(f'Watchdog: restarted dead loop {name}')
            except RuntimeError as e:
                logger.warning(f'Watchdog: could not restart {name}: {e}')


@loop_watchdog.before_loop
async def before_loop_watchdog():
    await bot.wait_until_ready()
    logger.info('Loop watchdog started (checks every 5 minutes)')


@bot.event
async def on_member_join(member):
    """Welcome new members when they join the server"""
    from config import WELCOME_CHANNEL_ID

    # Use plain text channel names (not <#ID> mentions) so unverified users can read them
    # Plain channel names, not <#ID> mentions: an unverified member cannot
    # see those channels, and Discord renders a mention they cannot resolve
    # as a dead "#unknown-channel".
    #
    # Step 1 leads with the Torn link rather than /register because it is the
    # one that cannot go wrong - no ID to mistype, no impersonating somebody
    # else, and it proves ownership from Torn's side. /register stays as the
    # fallback for anyone who has not linked.
    welcome_message = (
        f"🎉 Welcome to Ascended Punks, {member.mention}!\n\n"
        f"1️⃣ **Get verified** in **#welcome-pit**, either way:\n"
        f" • Link your Torn account to Discord in Torn, and the bot "
        f"picks you up automatically overnight.\n"
        f" • Or run `/register` with your Torn ID (the number in your "
        f"Torn profile URL).\n"
        f"2️⃣ Head to **#faction-edicts** and read the rules.\n"
        f"3️⃣ Once you're Verified, jump into **#punk-plaza** and say hi.\n\n"
        f"⚠️ Never paste an API key in Discord, here or anywhere else. "
        f"The bot never asks for one.\n"
        f"Please contact Ikaros22[3571142] if there is any issue with register process."
    )

    # Send to welcome channel
    if WELCOME_CHANNEL_ID:
        channel = bot.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            try:
                await channel.send(welcome_message)
                logger.info(f'Welcomed new member: {member.name}')
                return
            except Exception as e:
                logger.error(f'Failed to send welcome message to channel: {e}')
        else:
            logger.info(f'Welcome channel {WELCOME_CHANNEL_ID} not found')

    # Fallback: DM the user only if channel send failed
    try:
        await member.send(welcome_message.replace(member.mention, member.name))
        logger.info(f'Welcomed new member via DM (channel unavailable): {member.name}')
    except Exception as e:
        logger.error(f'Failed to DM welcome message to {member.name}: {e}')


@bot.event
async def on_error(event, *args, **kwargs):
    """Global error handler"""
    logger.error(f'Error in {event}:')
    import traceback
    traceback.print_exc()


def _validate_config():
    """Check required environment variables are present and non-zero.

    Returns a list of human-readable errors (empty if the config is valid).
    """
    errors = []
    if not BOT_TOKEN:
        errors.append('DISCORD_TOKEN is not set')
    if not TORN_API_KEY:
        errors.append('TORN_API_KEY is not set')
    if not GUILD_ID:
        errors.append('GUILD_ID is not set (or is 0)')
    if not TORN_FACTION_ID:
        errors.append('TORN_FACTION_ID is not set (or is 0)')
    return errors


# Run the bot
if __name__ == '__main__':
    config_errors = _validate_config()
    if config_errors:
        logger.error('ERROR: Configuration is incomplete:')
        for e in config_errors:
            logger.info(f'  - {e}')
        logger.info('Check your environment variables (.env or Railway config).')
        sys.exit(1)

    try:
        bot.run(BOT_TOKEN)
    except discord.LoginFailure:
        logger.error('ERROR: Invalid bot token!')
        logger.info('Please check your DISCORD_TOKEN env variable.')
    except Exception as e:
        logger.error(f'ERROR: Failed to start bot: {e}')
