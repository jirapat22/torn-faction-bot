"""
Bot Commands Module
Defines all slash commands for the bot
"""

import discord
from discord import app_commands
from datetime import datetime, timezone
from utils.helpers import create_embed, format_timestamp, format_timestamp_with_tct, get_days_since, is_management
from utils.torn_api import fetch_faction_members
from features.event_ui import EventTypeView
from config import (
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    ANNOUNCEMENTS_CHANNEL_ID,
    INACTIVITY_WARNING_DAYS,
    INACTIVITY_CRITICAL_DAYS,
    OC_WARNING_DAYS,
    OC_CRITICAL_DAYS,
    BOT_VERSION,
)


import logging

logger = logging.getLogger(__name__)


async def setup_commands(bot, inactivity_tracker, chain_tracker=None, war_tracker=None, verification_system=None,
                         event_reminder_manager=None, role_sync_manager=None,
                         enemy_watchlist=None, stakeout_manager=None, alliance_manager=None,
                         fly_tracker=None):
    """
    Register all slash commands with the bot

    Args:
        bot: Discord bot instance
        inactivity_tracker: InactivityTracker instance
        chain_tracker: ChainTracker instance (optional, for Phase 3)
        war_tracker: WarTracker instance (optional, for Phase 4)
        verification_system: VerificationSystem instance (for unregister sync)
    """

    @bot.tree.command(name='ping', description='Check if the bot is responding')
    async def ping(interaction: discord.Interaction):
        """Simple ping command to test bot responsiveness"""
        latency = round(bot.latency * 1000)  # Convert to milliseconds

        embed = create_embed(
            title='Pong!',
            description=f'Bot is online and responding.',
            color=EMBED_COLOR_SUCCESS,
            fields=[
                {'name': 'Latency', 'value': f'{latency}ms', 'inline': True},
                {'name': 'Status', 'value': 'Operational', 'inline': True}
            ]
        )

        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name='version', description='Show bot version')
    async def version(interaction: discord.Interaction):
        """Show bot version"""
        embed = create_embed(
            title='Bot Version',
            description='Ascended Punks Faction Bot',
            color=EMBED_COLOR_INFO,
            fields=[
                {'name': 'Version', 'value': BOT_VERSION, 'inline': True},
            ]
        )
        await interaction.response.send_message(embed=embed)

    # Note: /register command moved to features/verification.py for better verification system

    @bot.tree.command(name='unregister', description='Unlink your Discord account from your Torn ID')
    async def unregister(interaction: discord.Interaction):
        """Unregister Discord user (remove Torn ID link from all databases)"""
        discord_id = str(interaction.user.id)

        # Remove from both databases and remove verified role
        if verification_system:
            success = await verification_system.unregister_user(discord_id, guild=interaction.guild)
        else:
            success = await inactivity_tracker.unregister_user(interaction.user.id)

        if success:
            embed = create_embed(
                title='Unregistered',
                description='Your Torn ID has been unlinked and your verified role has been removed.\n'
                           'You can use `/register` again with the correct Torn ID.',
                color=EMBED_COLOR_SUCCESS
            )
        else:
            embed = create_embed(
                title='Not Registered',
                description='You don\'t have a Torn ID linked to your account.',
                color=EMBED_COLOR_INFO
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name='status', description='Show bot and inactivity tracking status')
    async def status(interaction: discord.Interaction):
        """Show current inactivity tracking statistics"""
        # Defer response since this might take a moment
        await interaction.response.defer()

        guild = interaction.guild

        if not guild:
            await interaction.followup.send('Error: Could not access server information')
            return

        # Count members
        total_members = len([m for m in guild.members if not m.bot])
        registered_members = inactivity_tracker.get_registered_count()
        unregistered_members = total_members - registered_members

        # Calculate uptime
        if hasattr(bot, 'start_time'):
            delta = datetime.now(timezone.utc) - bot.start_time
            uptime_str = str(delta).split('.')[0]  # Remove microseconds
        else:
            uptime_str = 'Unknown'

        # Build status message
        embed = create_embed(
            title='Bot Status',
            description='Torn faction inactivity tracking system',
            color=EMBED_COLOR_INFO,
            fields=[
                {'name': 'Version', 'value': BOT_VERSION, 'inline': True},
                {'name': 'Total Discord Members', 'value': str(total_members), 'inline': True},
                {'name': 'Registered Members', 'value': str(registered_members), 'inline': True},
                {'name': 'Unregistered Members', 'value': str(unregistered_members), 'inline': True},
                {'name': 'Uptime', 'value': uptime_str, 'inline': False},
                {'name': 'Tracking Method', 'value': 'Torn API (last_action)', 'inline': False},
                {'name': 'How to Register', 'value': 'Use `/register <torn_id>`', 'inline': False}
            ]
        )

        await interaction.followup.send(embed=embed)

    @bot.tree.command(name='help', description='Show bot commands and usage')
    async def help(interaction: discord.Interaction):
        """Show available commands and basic help"""
        embed = create_embed(
            title='Ascended Punks Bot - Commands',
            description='Use these slash commands to interact with the bot',
            color=EMBED_COLOR_INFO,
            fields=[
                {
                    'name': 'Member',
                    'value': (
                        '`/ping` - Check bot status\n'
                        '`/register <id>` - Link Torn account\n'
                        '`/unregister` - Unlink account\n'
                        '`/whois <user>` - Look up Torn ID\n'
                        '`/myactivity` - Check your activity\n'
                        '`/status` - Bot stats\n'
                        '`/version` - Show bot version'
                    ),
                    'inline': False,
                },
                {
                    'name': 'Chain & War',
                    'value': (
                        '`/chain` - Chain status / report / board\n'
                        '`/war` - War status / board / xan / history / trends'
                    ),
                    'inline': False,
                },
                {
                    'name': 'Intel (Management)',
                    'value': (
                        '`/faction compare|stats <id>` - Compare / analyse a faction\n'
                        '`/enemy watchlist` - War enemy watchlist\n'
                        '`/enemy stakeout` - Track enemy activity / behavior\n'
                        '`/alliance` - Ally faction war tracking'
                    ),
                    'inline': False,
                },
                {
                    'name': 'Management',
                    'value': (
                        '`/announce` - Post announcement\n'
                        '`/createevent` - Schedule event\n'
                        '`/verify` - Verification tools\n'
                        '`/rolesync` - Sync Torn positions to Discord roles\n'
                        '`/test <feature>` - Manually trigger a background check'
                    ),
                    'inline': False,
                },
            ]
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name='myactivity', description='Check your Torn activity status')
    async def myactivity(interaction: discord.Interaction):
        """Show the user's own activity information from Torn"""
        await interaction.response.defer(ephemeral=True)

        # Check if user is registered
        torn_id = inactivity_tracker.get_torn_id(interaction.user.id)

        if not torn_id:
            embed = create_embed(
                title='Not Registered',
                description='You haven\'t linked your Torn ID yet!',
                color=EMBED_COLOR_WARNING,
                fields=[
                    {'name': 'How to Register', 'value': 'Use `/register <torn_id>` to link your account', 'inline': False}
                ]
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Fetch Torn data
        members = await fetch_faction_members()

        if not members:
            embed = create_embed(
                title='API Error',
                description='Could not connect to Torn API. Please try again later.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Get user's Torn data
        torn_member = members.get(torn_id)

        if not torn_member:
            embed = create_embed(
                title='Error',
                description=f'Torn ID {torn_id} not found in faction. You may have left the faction.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Extract data
        torn_name = torn_member.get('name', 'Unknown')
        torn_level = torn_member.get('level', '?')
        last_action_data = torn_member.get('last_action', {})
        last_action_timestamp = last_action_data.get('timestamp')
        last_action_status = last_action_data.get('status', 'Unknown')

        if not last_action_timestamp:
            embed = create_embed(
                title='No Data',
                description='No activity data available for your Torn account.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Calculate days inactive
        days_inactive = get_days_since(last_action_timestamp)

        # Choose appropriate color and status
        if days_inactive >= 5:
            color = EMBED_COLOR_CRITICAL
            status = f'Critical - {days_inactive} days inactive'
        elif days_inactive >= 3:
            color = EMBED_COLOR_WARNING
            status = f'Warning - {days_inactive} days inactive'
        else:
            color = EMBED_COLOR_SUCCESS
            status = 'Active'

        embed = create_embed(
            title=f'Your Torn Activity: {torn_name}',
            description=f'Level {torn_level} - [View Profile](https://www.torn.com/profiles.php?XID={torn_id})',
            color=color,
            fields=[
                {'name': 'Status', 'value': status, 'inline': False},
                {'name': 'Days Since Last Action', 'value': str(days_inactive), 'inline': True},
                {'name': 'Last Action', 'value': format_timestamp(last_action_timestamp, 'R'), 'inline': True},
                {'name': 'Last Action Status', 'value': last_action_status, 'inline': False}
            ]
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # Note: /whois command moved to features/verification.py
    # Note: /registered command removed - use /verify stats instead

    # ===== CHAIN COMMAND (Phase 3) =====

    if chain_tracker:
        @bot.tree.command(name='chain', description='Chain status, report, or participation board')
        @app_commands.describe(view='What to show (default: current status)')
        @app_commands.choices(view=[
            app_commands.Choice(name='Status - Current chain info', value='status'),
            app_commands.Choice(name='Report - Last chain top contributors', value='report'),
            app_commands.Choice(name='Board - Full participation board (Management)', value='board'),
        ])
        async def chain(interaction: discord.Interaction, view: app_commands.Choice[str] = None):
            """Unified chain command with multiple views"""
            view_value = view.value if view else 'status'

            # Board view requires management
            if view_value == 'board' and not is_management(interaction):
                await interaction.response.send_message(
                    embed=create_embed(
                        title='Permission Denied',
                        description='You need Management permissions to view the chain board.',
                        color=EMBED_COLOR_CRITICAL
                    ),
                    ephemeral=True
                )
                return

            await interaction.response.defer()

            if view_value == 'report':
                report_data = await chain_tracker.get_chain_report()
                embed = await chain_tracker.format_chainreport_embed(report_data)
                await interaction.followup.send(embed=embed)

            elif view_value == 'board':
                aggregated = await chain_tracker.get_aggregated_chain_data()
                embeds = await chain_tracker.format_chainboard_embeds(aggregated)
                if isinstance(embeds, list):
                    for e in embeds:
                        await interaction.followup.send(embed=e)
                else:
                    await interaction.followup.send(embed=embeds)

            else:  # status (default)
                chain_data = await chain_tracker.get_current_chain()
                embed = await chain_tracker.format_chain_embed(chain_data)
                await interaction.followup.send(embed=embed)

    # ===== WAR COMMAND (Phase 4) =====

    if war_tracker:
        @bot.tree.command(name='war', description='War status, board, xan calc, or history')
        @app_commands.describe(
            view='What to show (default: current status)',
            member='For History view: check a specific member\'s war history'
        )
        @app_commands.choices(view=[
            app_commands.Choice(name='Status - Current war info', value='status'),
            app_commands.Choice(name='Board - War participation leaderboard (Management)', value='board'),
            app_commands.Choice(name='Xan - Xanax calculation (Management)', value='xan'),
            app_commands.Choice(name='History - War performance across wars (Management)', value='history'),
            app_commands.Choice(name='Trends - Faction W/L record and top contributors (Management)', value='trends'),
            app_commands.Choice(name='Fly - Toggle enemy flight tracking (Management)', value='fly'),
        ])
        async def war(interaction: discord.Interaction, view: app_commands.Choice[str] = None, member: discord.Member = None):
            """Unified war command with multiple views"""
            view_value = view.value if view else 'status'

            # Board, xan, history, trends, fly views require management
            if view_value in ('board', 'xan', 'history', 'trends', 'fly') and not is_management(interaction):
                await interaction.response.send_message(
                    embed=create_embed(
                        title='Permission Denied',
                        description='You need Management permissions for this view.',
                        color=EMBED_COLOR_CRITICAL
                    ),
                    ephemeral=True
                )
                return

            await interaction.response.defer()

            if view_value == 'board':
                war_info = await war_tracker.get_latest_war()
                embeds = await war_tracker.format_warboard_embed(war_info)
                if isinstance(embeds, list):
                    for embed in embeds:
                        await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send(embed=embeds)

            elif view_value == 'xan':
                war_info = await war_tracker.get_latest_war()
                embeds = await war_tracker.format_xan_embed(war_info)
                if isinstance(embeds, list):
                    for embed in embeds:
                        await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send(embed=embeds)

            elif view_value == 'history':
                if member:
                    torn_id = inactivity_tracker.get_torn_id(member.id) if inactivity_tracker else None
                    if not torn_id:
                        await interaction.followup.send(embed=create_embed(
                            title='Member Not Found',
                            description=f'{member.mention} is not registered. They need to `/register` first.',
                            color=EMBED_COLOR_WARNING
                        ))
                        return
                    embed = await war_tracker.format_warstats_member_embed(torn_id, member.display_name)
                else:
                    embed = await war_tracker.format_warstats_overview_embed()
                await interaction.followup.send(embed=embed)

            elif view_value == 'trends':
                embed = await war_tracker.format_war_trends_embed()
                await interaction.followup.send(embed=embed)

            elif view_value == 'fly':
                if not fly_tracker:
                    await interaction.followup.send(embed=create_embed(
                        title='Fly Tracking Unavailable',
                        description='Fly tracker is not initialized.',
                        color=EMBED_COLOR_WARNING
                    ))
                    return
                if fly_tracker.enabled:
                    embed = await fly_tracker.disable(reason=f'Toggled off by {interaction.user.display_name}')
                else:
                    _, embed = await fly_tracker.enable()
                await interaction.followup.send(embed=embed)

            else:  # status (default)
                war_info = await war_tracker.get_current_war()
                embed = await war_tracker.format_war_embed(war_info)
                await interaction.followup.send(embed=embed)

        logger.info('War command registered (/war with status/board/xan/history/trends/fly views)')
    else:
        logger.warning('war_tracker is None, war command NOT registered')

    # ===== MANAGEMENT COMMANDS (Phase 5) =====

    # Create Modal for announcement (allows multi-line input)
    class AnnouncementModal(discord.ui.Modal, title='Create Announcement'):
        def __init__(self, ping_everyone: bool):
            super().__init__()
            self.ping_everyone = ping_everyone

        announcement_text = discord.ui.TextInput(
            label='Announcement Message',
            style=discord.TextStyle.paragraph,
            placeholder='Type your announcement here...\n\nYou can use multiple lines!',
            required=True,
            max_length=2000
        )

        async def on_submit(self, interaction: discord.Interaction):
            # Get announcements channel
            channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
            if not channel:
                embed = create_embed(
                    title='Error',
                    description=f'Could not find announcements channel (ID: {ANNOUNCEMENTS_CHANNEL_ID})',
                    color=EMBED_COLOR_CRITICAL
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Create announcement embed
            announcement_embed = create_embed(
                title='📢 Faction Announcement',
                description=self.announcement_text.value,
                color=EMBED_COLOR_INFO
            )
            announcement_embed.set_footer(text=f'Posted by {interaction.user.display_name}')

            # Post announcement
            try:
                if self.ping_everyone:
                    await channel.send('@everyone', embed=announcement_embed)
                else:
                    await channel.send(embed=announcement_embed)

                # Confirm to user
                ping_text = ' (with @everyone ping)' if self.ping_everyone else ''
                confirm_embed = create_embed(
                    title='✅ Announcement Posted',
                    description=f'Your announcement has been posted to <#{ANNOUNCEMENTS_CHANNEL_ID}>{ping_text}',
                    color=EMBED_COLOR_SUCCESS
                )
                await interaction.response.send_message(embed=confirm_embed, ephemeral=True)

            except Exception as e:
                error_embed = create_embed(
                    title='Error',
                    description=f'Failed to post announcement: {str(e)}',
                    color=EMBED_COLOR_CRITICAL
                )
                await interaction.response.send_message(embed=error_embed, ephemeral=True)

    # View for selecting ping options
    class AnnouncementView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.select(
            placeholder='Ping @everyone?',
            options=[
                discord.SelectOption(label='No - Regular announcement', value='no', emoji='📢', default=True),
                discord.SelectOption(label='Yes - Ping @everyone', value='yes', emoji='🔔')
            ]
        )
        async def select_ping(self, interaction: discord.Interaction, select: discord.ui.Select):
            ping_everyone = select.values[0] == 'yes'
            await interaction.response.send_modal(AnnouncementModal(ping_everyone))

    @bot.tree.command(name='announce', description='[Management] Post an announcement to the announcements channel')
    async def announce(interaction: discord.Interaction):
        """Post an announcement to the announcements channel (Management only)"""
        # Check permissions
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Show selection for ping option
        embed = create_embed(
            title='Create Announcement',
            description='Select whether to ping @everyone:',
            color=EMBED_COLOR_INFO
        )
        await interaction.response.send_message(embed=embed, view=AnnouncementView(), ephemeral=True)

    @bot.tree.command(name='createevent', description='[Management] Create a Discord scheduled event')
    async def createevent(interaction: discord.Interaction):
        """Create a Discord scheduled event with TCT (UTC) time (Management only)"""
        # Check permissions
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Show event type selection
        embed = create_embed(
            title='Create Event',
            description='Select an event type:',
            color=EMBED_COLOR_INFO
        )
        await interaction.response.send_message(embed=embed, view=EventTypeView(), ephemeral=True)

    @bot.tree.command(name='test', description='[Management] Manually trigger a background feature check')
    @app_commands.describe(feature='Which feature to test')
    @app_commands.choices(feature=[
        app_commands.Choice(name='Inactivity + OC check', value='inactivity'),
        app_commands.Choice(name='Event reminders', value='events'),
        app_commands.Choice(name='Role sync', value='rolesync'),
        app_commands.Choice(name='Enemy watchlist', value='enemies'),
        app_commands.Choice(name='Stakeout check', value='stakeout'),
        app_commands.Choice(name='Alliance war check', value='alliance'),
    ])
    async def test_feature(interaction: discord.Interaction, feature: str):
        """Manually trigger a background feature for testing (Management only)"""
        if not is_management(interaction):
            await interaction.response.send_message(
                embed=create_embed('Permission Denied', 'Management only.', color=EMBED_COLOR_CRITICAL),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        label_map = {
            'inactivity': 'Inactivity + OC Check',
            'events':     'Event Reminders',
            'rolesync':   'Role Sync',
            'enemies':    'Enemy Watchlist',
            'stakeout':   'Stakeout Check',
            'alliance':   'Alliance War Check',
        }
        label = label_map.get(feature, feature)

        try:
            if feature == 'inactivity':
                summary = await inactivity_tracker.test_run()
                if not summary:
                    await interaction.followup.send(
                        embed=create_embed('❌ Inactivity Check Failed', 'Could not fetch data from Torn API.', color=EMBED_COLOR_CRITICAL),
                        ephemeral=True
                    )
                    return

                ic = summary['inactivity_critical']
                iw = summary['inactivity_warning']
                oc_c = summary['oc_critical']
                oc_w = summary['oc_warning']
                lf = summary.get('left_faction', [])
                total = summary['total']

                lines = []

                if ic:
                    lines.append('**Inactive (Critical):**')
                    for m in ic:
                        lines.append(f'• {m["torn_name"]} — {m["days"]}d')
                if iw:
                    if lines: lines.append('')
                    lines.append('**Inactive (Warning):**')
                    for m in iw:
                        lines.append(f'• {m["torn_name"]} — {m["days"]}d')
                if oc_c:
                    if lines: lines.append('')
                    lines.append('**No OC (Critical):**')
                    for m in oc_c:
                        lines.append(f'• {m["torn_name"]} — {m["days"]:.0f}d')
                if oc_w:
                    if lines: lines.append('')
                    lines.append('**No OC (Warning):**')
                    for m in oc_w:
                        lines.append(f'• {m["torn_name"]} — {m["days"]:.0f}d')
                if lf:
                    if lines: lines.append('')
                    lines.append('**Left Faction (7+ days):**')
                    for m in lf:
                        lines.append(f'• {m["torn_name"]} — {m["days_gone"]:.0f}d gone')

                if not lines:
                    lines.append(f'✅ All {total} members active. No inactivity, OC, or left-faction issues.')

                await interaction.followup.send(
                    embed=create_embed('📋 Inactivity + OC Check', '\n'.join(lines), color=EMBED_COLOR_INFO),
                    ephemeral=True
                )
                return

            elif feature == 'events':
                if event_reminder_manager:
                    result = await event_reminder_manager.check_and_announce_events()
                    checked = result.get('checked', 0)
                    announced = result.get('announced', 0)
                    upcoming = result.get('upcoming', [])
                    lines = [f'**Scheduled events found:** {checked}']
                    if announced:
                        lines.append(f'**Reminders posted this run:** {announced}')
                    else:
                        lines.append('No events in the 33–37h reminder window.')
                    if upcoming:
                        lines.append('\n**Upcoming events:**')
                        for name, hrs in sorted(upcoming, key=lambda x: x[1])[:5]:
                            lines.append(f'• {name} — in {hrs:.1f}h')
                    note = '\n'.join(lines)
                    color = EMBED_COLOR_SUCCESS
                else:
                    note = 'Event reminder manager not available.'
                    color = EMBED_COLOR_WARNING

            elif feature == 'rolesync':
                if role_sync_manager:
                    await role_sync_manager.sync_all_members()
                    note = 'Role sync complete. All members processed.'
                    color = EMBED_COLOR_SUCCESS
                else:
                    note = 'Role sync manager not available.'
                    color = EMBED_COLOR_WARNING

            elif feature == 'enemies':
                if enemy_watchlist:
                    count = enemy_watchlist.get_enemy_count()
                    if count > 0:
                        await enemy_watchlist.check_all_enemies(post_updates=True)
                        note = f'Checked **{count}** watched enemies. Any status changes posted to their channel.'
                    else:
                        note = 'No enemies on watchlist.'
                    color = EMBED_COLOR_SUCCESS
                else:
                    note = 'Enemy watchlist not available.'
                    color = EMBED_COLOR_WARNING

            elif feature == 'stakeout':
                if stakeout_manager:
                    count = stakeout_manager.get_stakeout_count()
                    if count > 0:
                        await stakeout_manager.check_all_stakeouts()
                        note = f'Checked **{count}** stakeout target(s). Any updates posted to their channel.'
                    else:
                        note = 'No stakeout targets active.'
                    color = EMBED_COLOR_SUCCESS
                else:
                    note = 'Stakeout manager not available.'
                    color = EMBED_COLOR_WARNING

            elif feature == 'alliance':
                if alliance_manager:
                    count = alliance_manager.get_ally_count()
                    if count > 0:
                        await alliance_manager.check_ally_wars()
                        note = f'Checked **{count}** allied faction(s). Any war updates posted to their channel.'
                    else:
                        note = 'No allied factions configured.'
                    color = EMBED_COLOR_SUCCESS
                else:
                    note = 'Alliance manager not available.'
                    color = EMBED_COLOR_WARNING

            else:
                note = 'Unknown feature.'
                color = EMBED_COLOR_WARNING

            label = label_map.get(feature, feature)
            await interaction.followup.send(
                embed=create_embed(f'✅ {label}', note, color=color),
                ephemeral=True
            )
        except Exception as e:
            label = label_map.get(feature, feature)
            await interaction.followup.send(
                embed=create_embed(f'❌ {label} Failed', str(e), color=EMBED_COLOR_CRITICAL),
                ephemeral=True
            )

    logger.info('Management commands registered (/announce, /createevent, /test)')
    logger.info('Slash commands registered')
