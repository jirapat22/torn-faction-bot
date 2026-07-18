"""
Shared event-creation UI.

The event-type picker and its modals live here (not inside commands.py) so
the background newsletter task can reuse them: the newsletter nudge carries a
persistent "Create Event" button that opens the same picker /createevent uses.

Also auto-announces a created event to the channel matching its type.
"""

import discord
from datetime import datetime, timezone, timedelta
from utils.helpers import create_embed, is_management
from config import (
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_CRITICAL,
    HAPPY_JUMP_CHANNEL_ID,
    CHAINING_CHANNEL_ID,
    WAR_CHANNEL_ID,
    GENERAL_CHANNEL_ID,
    ANNOUNCEMENTS_CHANNEL_ID,
    EVENT_POSTING_CHANNEL_ID,
)

import logging

logger = logging.getLogger(__name__)

_DATE_FORMATS = ('%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d')


def _parse_event_datetime(date_str: str, time_str: str) -> datetime:
    """Parse a date+time pair, accepting DD-MM-YYYY, YYYY-MM-DD, or slash variants.

    Returns a UTC-aware datetime; raises ValueError if no format matches.
    """
    last_err = None
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(f'{date_str} {time_str}', f'{fmt} %H:%M')
            return dt.replace(tzinfo=timezone.utc)
        except ValueError as e:
            last_err = e
    raise ValueError(f'Could not parse date/time. Accepted formats: '
                     f'DD-MM-YYYY, YYYY-MM-DD, DD/MM/YYYY, YYYY/MM/DD. '
                     f'Last error: {last_err}')


def _announce_channel_id(event_name: str) -> int:
    """Route an event announcement to the channel matching its type,
    falling back to the event-posting channel (then announcements)."""
    n = event_name.lower()
    fallback = EVENT_POSTING_CHANNEL_ID or ANNOUNCEMENTS_CHANNEL_ID
    if 'happy' in n or 'jump' in n:
        # Happy jumps live in punk-plaza (GENERAL); dedicated channel wins if set
        return HAPPY_JUMP_CHANNEL_ID or GENERAL_CHANNEL_ID or fallback
    if 'chain' in n:
        return CHAINING_CHANNEL_ID or fallback
    if 'war' in n:
        return WAR_CHANNEL_ID or fallback
    return fallback


async def _announce_event(interaction, name, start_unix, duration, manual_end):
    """Post a public announcement of a newly-created event to its type channel."""
    channel_id = _announce_channel_id(name)
    channel = interaction.client.get_channel(channel_id) if channel_id else None
    if not channel:
        logger.warning(f'Event announce: no channel for "{name}" (id {channel_id})')
        return
    duration_line = 'Until manually ended' if manual_end else f'{duration} hour(s)'
    embed = create_embed(
        title=f'📅 Event Scheduled: {name}',
        description=(
            f'**When:** <t:{start_unix}:F> (<t:{start_unix}:R>)\n'
            f'**Duration:** {duration_line}\n'
            f'Created by {interaction.user.mention}'
        ),
        color=EMBED_COLOR_INFO
    )
    try:
        await channel.send(embed=embed)
        logger.info(f'Announced event "{name}" to channel {channel_id}')
    except Exception as e:
        logger.error(f'Event announce failed for "{name}": {e}')


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
        label='Date (DD-MM-YYYY or YYYY-MM-DD)',
        placeholder='E.g., 25-01-2026 or 2026-01-25',
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
            name = self.default_name
            date = self.event_date.value
            time = self.event_time.value
            duration = self.default_duration

            description = self.event_description.value.strip() if self.event_description.value.strip() else self.default_desc

            event_start = _parse_event_datetime(date, time)
            event_end = event_start + timedelta(hours=duration)

            event_description = f'{description}\n\n⏰ Time: {date} {time} TCT (UTC)'

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

            start_unix = int(event_start.timestamp())
            time_line = f'🕐 Time: <t:{start_unix}:f> ({time} TCT)'
            if self.manual_end:
                confirm_desc = (f'**{name}**\n\n'
                               f'📅 Date: {date}\n'
                               f'{time_line}\n'
                               f'⏱️ Duration: Until manually ended\n\n'
                               f'⚠️ Right-click event → End Event when war completes\n\n'
                               f'Event ID: {event.id}')
            else:
                confirm_desc = (f'**{name}**\n\n'
                               f'📅 Date: {date}\n'
                               f'{time_line}\n'
                               f'⏱️ Duration: {duration} hour(s)\n\n'
                               f'Event ID: {event.id}')

            confirm_embed = create_embed(
                title='✅ Event Created',
                description=confirm_desc,
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=confirm_embed, ephemeral=True)

            await _announce_event(interaction, name, start_unix, duration, self.manual_end)

        except ValueError as e:
            error_embed = create_embed(
                title='Invalid Format',
                description='Please use one of:\n'
                           '• Date: DD-MM-YYYY (25-01-2026) or YYYY-MM-DD (2026-01-25)\n'
                           '• Time: HH:MM (14:30) — TCT/UTC\n\n'
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
        label='Date (DD-MM-YYYY or YYYY-MM-DD)',
        placeholder='E.g., 25-01-2026 or 2026-01-25',
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
            name = self.event_name.value
            date = self.event_date.value
            time = self.event_time.value
            duration = int(self.event_duration.value) if self.event_duration.value else self.default_duration
            description = self.event_desc.value

            event_start = _parse_event_datetime(date, time)
            event_end = event_start + timedelta(hours=duration)

            if description:
                event_description = f'{description}\n\n⏰ Time: {date} {time} TCT (UTC)'
            elif self.default_desc:
                event_description = f'{self.default_desc}\n\n⏰ Time: {date} {time} TCT (UTC)'
            else:
                event_description = f'⏰ Time: {date} {time} TCT (UTC)'

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

            start_unix = int(event_start.timestamp())
            time_line = f'🕐 Time: <t:{start_unix}:f> ({time} TCT)'
            if self.manual_end:
                confirm_desc = (f'**{name}**\n\n'
                               f'📅 Date: {date}\n'
                               f'{time_line}\n'
                               f'⏱️ Duration: {duration} hour(s) (placeholder)\n\n'
                               f'⚠️ **Manual End Event**\n'
                               f'Right-click event → End Event when complete\n\n'
                               f'Event ID: {event.id}')
            else:
                confirm_desc = (f'**{name}**\n\n'
                               f'📅 Date: {date}\n'
                               f'{time_line}\n'
                               f'⏱️ Duration: {duration} hour(s)\n\n'
                               f'Event ID: {event.id}')

            confirm_embed = create_embed(
                title='✅ Event Created',
                description=confirm_desc,
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=confirm_embed, ephemeral=True)

            await _announce_event(interaction, name, start_unix, duration, self.manual_end)

        except ValueError as e:
            error_embed = create_embed(
                title='Invalid Format',
                description='Please use one of:\n'
                           '• Date: DD-MM-YYYY (25-01-2026) or YYYY-MM-DD (2026-01-25)\n'
                           '• Time: HH:MM (14:30) — TCT/UTC\n\n'
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

        if event_type in ['war', 'chain', 'happyjump']:
            await interaction.response.send_modal(CommonEventModal(event_type))
        elif event_type == 'custom_manual':
            await interaction.response.send_modal(CustomEventModal(manual_end=True))
        else:  # custom
            await interaction.response.send_modal(CustomEventModal(manual_end=False))


class CreateEventPromptView(discord.ui.View):
    """Persistent view: a 'Create Event' button attached to the newsletter
    nudge. Timeout=None + a fixed custom_id so it survives bot restarts
    (bot.py registers it once via bot.add_view)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Create Event', emoji='📅',
                       style=discord.ButtonStyle.primary,
                       custom_id='newsletter_create_event')
    async def create_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Gate to management, same as the /createevent command
        if not is_management(interaction):
            await interaction.response.send_message(
                'You do not have permission to create events.', ephemeral=True)
            return
        embed = create_embed(
            title='Create Event',
            description='Select an event type:',
            color=EMBED_COLOR_INFO
        )
        await interaction.response.send_message(embed=embed, view=EventTypeView(), ephemeral=True)
