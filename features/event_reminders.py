"""
Event Reminders Module
Posts 30-hour advance reminders for Discord scheduled events to appropriate channels.

Event routing:
- "Happy Jump" events → GENERAL_CHANNEL_ID (punk-plaza)
- "Chain" events → CHAINING_CHANNEL_ID
- "War" events → WAR_CHANNEL_ID
- Other events → ANNOUNCEMENTS_CHANNEL_ID
"""

import discord
from datetime import datetime, timezone, timedelta
from utils.helpers import load_json, save_json, create_embed
from config import (
    EMBED_COLOR_INFO,
    GENERAL_CHANNEL_ID,
    CHAINING_CHANNEL_ID,
    WAR_CHANNEL_ID,
    ANNOUNCEMENTS_CHANNEL_ID,
    GUILD_ID
)


class EventReminderManager:
    """Manages 30-hour advance event reminders"""

    def __init__(self, bot):
        self.bot = bot
        self.data_file = 'data/event_reminders.json'
        self.announced_events = set()  # Event IDs that have been announced

    async def load_data(self):
        """Load announced event IDs from JSON"""
        data = load_json(self.data_file, default={'announced': []})
        self.announced_events = set(data.get('announced', []))
        print(f'Loaded {len(self.announced_events)} announced event IDs')

    async def save_data(self):
        """Save announced event IDs to JSON"""
        save_json(self.data_file, {'announced': list(self.announced_events)})

    def _get_channel_for_event(self, event_name: str) -> int:
        """
        Determine which channel to post the reminder based on event name.

        Returns channel ID based on event type keywords.
        """
        name_lower = event_name.lower()

        if 'happy' in name_lower or 'jump' in name_lower:
            return GENERAL_CHANNEL_ID  # punk-plaza
        elif 'chain' in name_lower:
            return CHAINING_CHANNEL_ID
        elif 'war' in name_lower:
            return WAR_CHANNEL_ID
        else:
            return ANNOUNCEMENTS_CHANNEL_ID

    def _get_reminder_message(self, event_name: str, hours_until: float) -> str:
        """
        Get the reminder message based on event type.

        Returns a motivational message for the event.
        """
        name_lower = event_name.lower()
        hours_text = f"{int(hours_until)}" if hours_until == int(hours_until) else f"{hours_until:.1f}"

        if 'happy' in name_lower or 'jump' in name_lower:
            return f"**{event_name}** starts in **{hours_text} hours**. Let's get stacking!!"
        elif 'chain' in name_lower:
            return f"**{event_name}** starts in **{hours_text} hours**. Get ready to hit!"
        elif 'war' in name_lower:
            return f"**{event_name}** starts in **{hours_text} hours**. Prepare for battle!"
        else:
            return f"**{event_name}** starts in **{hours_text} hours**!"

    async def check_and_announce_events(self):
        """
        Check all scheduled events and post reminders for those starting in ~30 hours.

        Called by background task.
        """
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            print(f'Event reminders: Could not find guild (ID: {GUILD_ID})')
            return

        now = datetime.now(timezone.utc)

        # Get all scheduled events
        try:
            events = await guild.fetch_scheduled_events()
        except Exception as e:
            print(f'Event reminders: Failed to fetch events: {e}')
            return

        for event in events:
            # Skip if already announced
            if event.id in self.announced_events:
                continue

            # Skip if event has already started or ended
            if event.status != discord.EventStatus.scheduled:
                continue

            # Calculate hours until event starts
            time_until = event.start_time - now
            hours_until = time_until.total_seconds() / 3600

            # Check if within the announcement window (28-32 hours)
            # This gives a 4-hour window to catch events even if check runs late
            if 28 <= hours_until <= 32:
                await self._announce_event(event, hours_until)
                self.announced_events.add(event.id)
                await self.save_data()

        # Clean up old event IDs (events that no longer exist)
        await self._cleanup_old_events(events)

    async def _announce_event(self, event: discord.ScheduledEvent, hours_until: float):
        """Post the reminder message for an event."""
        channel_id = self._get_channel_for_event(event.name)
        channel = self.bot.get_channel(channel_id)

        if not channel:
            print(f'Event reminders: Could not find channel (ID: {channel_id}) for event "{event.name}"')
            return

        # Build the message
        message = self._get_reminder_message(event.name, hours_until)

        # Create embed
        embed = create_embed(
            title='⏰ Event Reminder',
            description=message,
            color=EMBED_COLOR_INFO
        )

        # Add event details
        embed.add_field(
            name='Start Time',
            value=f"<t:{int(event.start_time.timestamp())}:F>\n(<t:{int(event.start_time.timestamp())}:R>)",
            inline=True
        )

        if event.description:
            # Truncate description if too long
            desc = event.description[:200] + '...' if len(event.description) > 200 else event.description
            embed.add_field(
                name='Details',
                value=desc,
                inline=False
            )

        try:
            await channel.send(embed=embed)
            print(f'Event reminder posted for "{event.name}" in channel {channel_id}')
        except Exception as e:
            print(f'Event reminders: Failed to post reminder for "{event.name}": {e}')

    async def _cleanup_old_events(self, current_events: list):
        """Remove event IDs that no longer exist in Discord."""
        current_ids = {event.id for event in current_events}
        old_ids = self.announced_events - current_ids

        if old_ids:
            self.announced_events -= old_ids
            await self.save_data()
            print(f'Event reminders: Cleaned up {len(old_ids)} old event IDs')
