"""
Bot Commands Module
Defines all slash commands for the bot
"""

import discord
from discord import app_commands
from datetime import datetime, timezone
from utils.helpers import create_embed, format_timestamp, format_timestamp_with_tct, get_days_since, is_management
from utils.torn_api import fetch_faction_members
from config import (
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    ANNOUNCEMENTS_CHANNEL_ID
)


async def setup_commands(bot, inactivity_tracker, chain_tracker=None, war_tracker=None, verification_system=None):
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

        # Build status message
        embed = create_embed(
            title='Bot Status',
            description='Torn faction inactivity tracking system',
            color=EMBED_COLOR_INFO,
            fields=[
                {'name': 'Total Discord Members', 'value': str(total_members), 'inline': True},
                {'name': 'Registered Members', 'value': str(registered_members), 'inline': True},
                {'name': 'Unregistered Members', 'value': str(unregistered_members), 'inline': True},
                {'name': 'Tracking Method', 'value': 'Torn API (last_action)', 'inline': False},
                {'name': 'How to Register', 'value': 'Use `/register <torn_id>`', 'inline': False}
            ]
        )

        await interaction.followup.send(embed=embed)

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
        ])
        async def war(interaction: discord.Interaction, view: app_commands.Choice[str] = None, member: discord.Member = None):
            """Unified war command with multiple views"""
            view_value = view.value if view else 'status'

            # Board, xan, and history views require management
            if view_value in ('board', 'xan', 'history') and not is_management(interaction):
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

            else:  # status (default)
                war_info = await war_tracker.get_current_war()
                embed = await war_tracker.format_war_embed(war_info)
                await interaction.followup.send(embed=embed)

        print('War command registered (/war with status/board/xan/history views)')
    else:
        print('WARNING: war_tracker is None, war command NOT registered')

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

    # Modal for common events (War, Chain, Happy Jump) - only date, time, and optional description
    class CommonEventModal(discord.ui.Modal):
        def __init__(self, event_type: str):
            if event_type == 'war':
                super().__init__(title='Create War Event')
                self.default_duration = 168  # 7 days placeholder - until it ends
                self.default_desc = '⚔️ War event - Manually end when war completes'
                self.manual_end = True
                self.default_name = 'Faction War'
            elif event_type == 'chain':
                super().__init__(title='Create Chain Event')
                self.default_duration = 24
                self.default_desc = "Let's build a massive chain! Updates in #chaining"
                self.manual_end = False
                self.default_name = 'Chain Event'
            elif event_type == 'happyjump':
                super().__init__(title='Create Happy Jump Event')
                self.default_duration = 24
                self.default_desc = 'Energy refill time! Use your energy before the jump'
                self.manual_end = False
                self.default_name = 'Happy Jump'

            self.event_type = event_type

        event_date = discord.ui.TextInput(
            label='Date (DD-MM-YYYY format)',
            placeholder='E.g., 25-01-2026',
            required=True,
            max_length=10
        )

        event_time = discord.ui.TextInput(
            label='Time (HH:MM in TCT/UTC)',
            placeholder='E.g., 14:30',
            required=True,
            max_length=5
        )

        event_description = discord.ui.TextInput(
            label='Description (Optional)',
            style=discord.TextStyle.paragraph,
            placeholder='Leave blank to use default description',
            required=False,
            max_length=500
        )

        async def on_submit(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            try:
                # Get values - use default name, user provides date/time/optional description
                name = self.default_name
                date = self.event_date.value
                time = self.event_time.value
                duration = self.default_duration

                # Use user description if provided, otherwise use default
                description = self.event_description.value.strip() if self.event_description.value.strip() else self.default_desc

                # Parse date and time (TCT = UTC) - DD-MM-YYYY format
                datetime_str = f'{date} {time}'
                event_start = datetime.strptime(datetime_str, '%d-%m-%Y %H:%M')
                event_start = event_start.replace(tzinfo=timezone.utc)

                # Calculate end time
                from datetime import timedelta
                event_end = event_start + timedelta(hours=duration)

                # Create event description
                event_description = f'{description}\n\n⏰ Time: {date} {time} TCT (UTC)'

                # Create the scheduled event
                guild = interaction.guild
                event = await guild.create_scheduled_event(
                    name=name,
                    description=event_description,
                    start_time=event_start,
                    end_time=event_end,
                    privacy_level=discord.PrivacyLevel.guild_only,
                    entity_type=discord.EntityType.external,
                    location='Torn City'
                )

                # Confirm to user
                if self.manual_end:
                    confirm_desc = (f'**{name}**\n\n'
                                   f'📅 Date: {date}\n'
                                   f'🕐 Time: {time} TCT (UTC)\n'
                                   f'⏱️ Duration: Until manually ended\n\n'
                                   f'⚠️ Right-click event → End Event when war completes\n\n'
                                   f'Event ID: {event.id}')
                else:
                    confirm_desc = (f'**{name}**\n\n'
                                   f'📅 Date: {date}\n'
                                   f'🕐 Time: {time} TCT (UTC)\n'
                                   f'⏱️ Duration: {duration} hour(s)\n\n'
                                   f'Event ID: {event.id}')

                confirm_embed = create_embed(
                    title='✅ Event Created',
                    description=confirm_desc,
                    color=EMBED_COLOR_SUCCESS
                )
                await interaction.followup.send(embed=confirm_embed, ephemeral=True)

            except ValueError as e:
                error_embed = create_embed(
                    title='Invalid Format',
                    description='Please use the correct format:\n'
                               '• Date: DD-MM-YYYY (e.g., 25-01-2026)\n'
                               '• Time: HH:MM (e.g., 14:30)\n\n'
                               f'Error: {str(e)}',
                    color=EMBED_COLOR_CRITICAL
                )
                await interaction.followup.send(embed=error_embed, ephemeral=True)

            except Exception as e:
                error_embed = create_embed(
                    title='Error',
                    description=f'Failed to create event: {str(e)}',
                    color=EMBED_COLOR_CRITICAL
                )
                await interaction.followup.send(embed=error_embed, ephemeral=True)

    # Modal for custom events - all fields
    class CustomEventModal(discord.ui.Modal):
        def __init__(self, manual_end: bool = False):
            if manual_end:
                super().__init__(title='Create Custom Event (Manual End)')
                self.manual_end = True
                self.default_duration = 168
                self.default_desc = 'Manually end this event when complete'
            else:
                super().__init__(title='Create Custom Event')
                self.manual_end = False
                self.default_duration = 2
                self.default_desc = ''

        event_name = discord.ui.TextInput(
            label='Event Name',
            placeholder='E.g., "Special Event"',
            required=True,
            max_length=100
        )

        event_date = discord.ui.TextInput(
            label='Date (DD-MM-YYYY format)',
            placeholder='E.g., 25-01-2026',
            required=True,
            max_length=10
        )

        event_time = discord.ui.TextInput(
            label='Time (HH:MM in TCT/UTC)',
            placeholder='E.g., 14:30',
            required=True,
            max_length=5
        )

        event_duration = discord.ui.TextInput(
            label='Duration (hours)',
            placeholder='E.g., 24',
            required=False,
            max_length=3
        )

        event_desc = discord.ui.TextInput(
            label='Description (optional)',
            style=discord.TextStyle.paragraph,
            placeholder='Additional event details...',
            required=False,
            max_length=500
        )

        async def on_submit(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            try:
                # Get values
                name = self.event_name.value
                date = self.event_date.value
                time = self.event_time.value
                duration = int(self.event_duration.value) if self.event_duration.value else self.default_duration
                description = self.event_desc.value

                # Parse date and time (TCT = UTC) - DD-MM-YYYY format
                datetime_str = f'{date} {time}'
                event_start = datetime.strptime(datetime_str, '%d-%m-%Y %H:%M')
                event_start = event_start.replace(tzinfo=timezone.utc)

                # Calculate end time
                from datetime import timedelta
                event_end = event_start + timedelta(hours=duration)

                # Create event description
                if description:
                    event_description = f'{description}\n\n⏰ Time: {date} {time} TCT (UTC)'
                elif self.default_desc:
                    event_description = f'{self.default_desc}\n\n⏰ Time: {date} {time} TCT (UTC)'
                else:
                    event_description = f'⏰ Time: {date} {time} TCT (UTC)'

                # Create the scheduled event
                guild = interaction.guild
                event = await guild.create_scheduled_event(
                    name=name,
                    description=event_description,
                    start_time=event_start,
                    end_time=event_end,
                    privacy_level=discord.PrivacyLevel.guild_only,
                    entity_type=discord.EntityType.external,
                    location='Torn City'
                )

                # Confirm to user
                if self.manual_end:
                    confirm_desc = (f'**{name}**\n\n'
                                   f'📅 Date: {date}\n'
                                   f'🕐 Time: {time} TCT (UTC)\n'
                                   f'⏱️ Duration: {duration} hour(s) (placeholder)\n\n'
                                   f'⚠️ **Manual End Event**\n'
                                   f'Right-click event → End Event when complete\n\n'
                                   f'Event ID: {event.id}')
                else:
                    confirm_desc = (f'**{name}**\n\n'
                                   f'📅 Date: {date}\n'
                                   f'🕐 Time: {time} TCT (UTC)\n'
                                   f'⏱️ Duration: {duration} hour(s)\n\n'
                                   f'Event ID: {event.id}')

                confirm_embed = create_embed(
                    title='✅ Event Created',
                    description=confirm_desc,
                    color=EMBED_COLOR_SUCCESS
                )
                await interaction.followup.send(embed=confirm_embed, ephemeral=True)

            except ValueError as e:
                error_embed = create_embed(
                    title='Invalid Format',
                    description='Please use the correct format:\n'
                               '• Date: DD-MM-YYYY (e.g., 25-01-2026)\n'
                               '• Time: HH:MM (e.g., 14:30)\n\n'
                               f'Error: {str(e)}',
                    color=EMBED_COLOR_CRITICAL
                )
                await interaction.followup.send(embed=error_embed, ephemeral=True)

            except Exception as e:
                error_embed = create_embed(
                    title='Error',
                    description=f'Failed to create event: {str(e)}',
                    color=EMBED_COLOR_CRITICAL
                )
                await interaction.followup.send(embed=error_embed, ephemeral=True)

    # View for selecting event type
    class EventTypeView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.select(
            placeholder='Select event type...',
            options=[
                discord.SelectOption(label='Faction War', value='war', emoji='⚔️', description='Until it ends - manually end when complete'),
                discord.SelectOption(label='Chain Event', value='chain', emoji='🔗', description='24 hour chain event'),
                discord.SelectOption(label='Happy Jump', value='happyjump', emoji='⚡', description='24 hour happy jump'),
                discord.SelectOption(label='Custom (Fixed duration)', value='custom', emoji='✏️', description='Custom event with set duration'),
                discord.SelectOption(label='Custom (Manual end)', value='custom_manual', emoji='📝', description='Custom event - manual end')
            ]
        )
        async def select_event_type(self, interaction: discord.Interaction, select: discord.ui.Select):
            event_type = select.values[0]

            # Use CommonEventModal for war, chain, happyjump
            if event_type in ['war', 'chain', 'happyjump']:
                await interaction.response.send_modal(CommonEventModal(event_type))
            # Use CustomEventModal for custom events
            elif event_type == 'custom_manual':
                await interaction.response.send_modal(CustomEventModal(manual_end=True))
            else:  # custom
                await interaction.response.send_modal(CustomEventModal(manual_end=False))

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

    @bot.tree.command(name='test_inactivity', description='[Management] Manually trigger inactivity check')
    async def test_inactivity(interaction: discord.Interaction):
        """Manually run the inactivity check (Management only)"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await inactivity_tracker.check_and_alert_inactive()
            embed = create_embed(
                title='Inactivity Check Complete',
                description='Manual inactivity check has been run. Check #inactivity-alerts for results.',
                color=EMBED_COLOR_SUCCESS
            )
        except Exception as e:
            embed = create_embed(
                title='Error',
                description=f'Inactivity check failed: {str(e)}',
                color=EMBED_COLOR_CRITICAL
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    print('Management commands registered (/announce, /createevent, /test_inactivity)')
    print('Slash commands registered')
