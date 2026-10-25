"""
Event Reminders Module
Posts 35-hour advance reminders for Discord scheduled events to appropriate channels.

Why 35 hours and not a round number: four Xanax at a 6 to 8 hour
cooldown each is about 32 hours, plus the last cooldown clearing before
the event starts. A reminder 24 hours out is already too late for
anybody to turn up on a full stack.

Event routing:
- "Happy Jump" events → GENERAL_CHANNEL_ID (punk-plaza)
- "Chain" events → CHAINING_CHANNEL_ID
- "War" events → WAR_CHANNEL_ID
- Other events → ANNOUNCEMENTS_CHANNEL_ID
"""

import discord
from datetime import datetime, date, timezone, timedelta
from utils.helpers import load_json, save_json, create_embed
from utils.torn_api import fetch_user_messages
from features.event_ui import CreateEventPromptView
from config import (
    EMBED_COLOR_INFO,
    GENERAL_CHANNEL_ID,
    CHAINING_CHANNEL_ID,
    WAR_CHANNEL_ID,
    ANNOUNCEMENTS_CHANNEL_ID,
    EVENT_POSTING_CHANNEL_ID,
    GUILD_ID
)


import logging

logger = logging.getLogger(__name__)

# Torn's fixed/recurring annual events (not Discord Scheduled Events).
# (name, month, day) - day=None means "first Friday of month".
# Source: https://wiki.torn.com/wiki/Current_Events - update by hand if Torn changes dates.
# Sentinel for a date that has to be computed rather than looked up.
EASTER = 'easter'


def easter_sunday(year):
    """
    Gregorian Easter (Meeus/Jones/Butcher). Pure arithmetic, no table to go
    stale, correct for any year this bot will ever see.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1)


def resolve_calendar_date(year, month, day):
    """
    One implementation of "when is this event", shared by the reminders and
    the Events tab so they can never disagree about a date.

    day=None is the first Friday of that month. day=EASTER is Easter Sunday.
    """
    if day == EASTER:
        return easter_sunday(year)
    if day is not None:
        return date(year, month, day)
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7)


CALENDAR_EVENTS = [
    ('Awareness Week', 1, 15),
    ('Weekend Road Trip', 1, 31),
    ("Valentine's Day", 2, 13),
    ('Employee Appreciation Day', 3, None),
    ("St Patrick's Day", 3, 16),
    ('420 Day', 4, 19),
    ('Museum Day', 5, 17),
    ('World Blood Donor Day', 6, 13),
    ('World Population Day', 7, 10),
    ('World Tiger Day', 7, 28),
    ('International Beer Day', 8, None),
    # Sep 5 to 18 from 2026 onward, per the wiki calendar. Was missing
    # entirely, and the faction's own event guide calls it a big four.
    ('Elimination', 9, 5),
    ('Tourism Day', 9, 26),
    ('CaffeineCon {year}', 10, 14),
    ('Trick or Treat', 10, 25),
    ('World Diabetes Day', 11, 13),
    ('Torn Anniversary', 11, 15),
    ('Black Friday', 11, 29),
    ('Slash Wednesday', 12, 10),
    ('Christmas Town', 12, 19),
    # Easter moves with the lunar calendar, so it cannot be a fixed day.
    # EASTER resolves it per year. It was missing entirely before this, which
    # meant one of the faction's own "big four" never got a reminder.
    ('Easter Egg Hunt', 4, EASTER),
]

# Pinged with @everyone. Wars and chain events are always pinged separately:
# both carry a strike, so missing one has a consequence. These are the Torn
# events worth interrupting people for.
MAJOR_EVENTS = {'Trick or Treat', 'Christmas Town', 'Easter Egg Hunt'}

# Events where turning up on a full energy bar actually pays, so the Xanax
# line is worth saying. Christmas Town is collecting and Easter is searching
# pages; neither is helped by energy, and telling people to stack for them
# would be noise that makes the line easier to ignore where it matters.
STACKING_EVENTS = {'Trick or Treat'}


class EventReminderManager:
    """Manages 35-hour advance event reminders"""

    def __init__(self, bot):
        self.bot = bot
        self.data_file = 'data/event_reminders.json'
        self.announced_events = set()  # Discord scheduled event IDs that have been announced
        self.announced_calendar = set()  # 'calendar:{name}:{year}' keys already announced
        self.last_newsletter_id = 0  # highest Faction Newsletter message ID already posted

    async def load_data(self):
        """Load announced event IDs from JSON"""
        data = load_json(self.data_file, default={'announced': [], 'announced_calendar': [], 'last_newsletter_id': 0})
        self.announced_events = set(data.get('announced', []))
        self.announced_calendar = set(data.get('announced_calendar', []))
        self.last_newsletter_id = data.get('last_newsletter_id', 0)
        logger.info(f'Loaded {len(self.announced_events)} announced event IDs, '
                    f'{len(self.announced_calendar)} announced calendar events, '
                    f'last newsletter ID {self.last_newsletter_id}')

    async def save_data(self):
        """Save announced event IDs to JSON"""
        save_json(self.data_file, {
            'announced': list(self.announced_events),
            'announced_calendar': list(self.announced_calendar),
            'last_newsletter_id': self.last_newsletter_id,
        })

    def _resolve_calendar_date(self, year: int, month: int, day):
        """Delegates, so reminders and the Events tab share one rule."""
        return resolve_calendar_date(year, month, day)

    def _next_calendar_start(self, month: int, day, now: datetime) -> datetime:
        """This year's (or next year's, if already passed) noon-TCT start."""
        d = self._resolve_calendar_date(now.year, month, day)
        start = datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)
        if start < now - timedelta(hours=48):
            d = self._resolve_calendar_date(now.year + 1, month, day)
            start = datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)
        return start

    async def check_calendar_events(self):
        """Post a 35h advance reminder for Torn's fixed annual calendar events."""
        now = datetime.now(timezone.utc)

        for name, month, day in CALENDAR_EVENTS:
            start = self._next_calendar_start(month, day, now)
            hours_until = (start - now).total_seconds() / 3600
            key = f'calendar:{name}:{start.year}'

            if key in self.announced_calendar or not (33 <= hours_until <= 37):
                continue

            display_name = name.format(year=start.year)
            # The big ones interrupt people, in the channel where faction
            # wide notices already go. Everything else stays where it was.
            major = name in MAJOR_EVENTS
            channel = self.bot.get_channel(
                ANNOUNCEMENTS_CHANNEL_ID if major else GENERAL_CHANNEL_ID)
            if channel:
                embed = create_embed(
                    title='📅 Torn Event Reminder',
                    description=(
                        f"**{display_name}** starts <t:{int(start.timestamp())}:R> "
                        f"(<t:{int(start.timestamp())}:F>).\n"
                        f"_Exact start may shift a few hours. Torn staggers some events per-player._"
                    ),
                    color=EMBED_COLOR_INFO
                )
                content = '@everyone' if major else None
                if name in STACKING_EVENTS:
                    embed.add_field(
                        name='⏱️ Stack now',
                        value='Start stacking your Xanax so you go in full.',
                        inline=False)
                await channel.send(content=content, embed=embed)
                logger.info(f'Calendar event reminder posted for '
                            f'"{display_name}"{" (@everyone)" if major else ""}')
            else:
                logger.warning(f'Calendar event reminder: could not find channel (ID: {GENERAL_CHANNEL_ID})')

            self.announced_calendar.add(key)
            await self.save_data()

    async def check_newsletters(self):
        """
        Surface new in-game 'Faction Newsletter' mail to Discord. Torn's API
        only exposes title/sender/timestamp, not the body - members still
        need to check Torn mail for the actual details (dates, etc.).
        """
        messages = await fetch_user_messages()
        newsletter_ids = [int(mid) for mid, m in messages.items() if m.get('type') == 'Faction newsletter']
        if not newsletter_ids:
            return

        if self.last_newsletter_id == 0:
            # First run - baseline to newest so we don't dump months of history
            self.last_newsletter_id = max(newsletter_ids)
            await self.save_data()
            return

        new_ids = sorted(mid for mid in newsletter_ids if mid > self.last_newsletter_id)
        if not new_ids:
            return

        for mid in new_ids:
            m = messages[str(mid)]
            title = (m.get('title') or 'Faction Newsletter').strip()
            sender = m.get('name', 'Unknown')
            channel = self.bot.get_channel(EVENT_POSTING_CHANNEL_ID)
            if channel:
                embed = create_embed(
                    title='📰 Faction Newsletter',
                    description=(
                        f"**{sender}** sent: **{title}**\n"
                        f"Sent <t:{m['timestamp']}:R>. Check your Torn mail for full details.\n\n"
                        f"If this schedules something, hit **Create Event** below "
                        f"(or use `/createevent`) so Discord reminds everyone."
                    ),
                    color=EMBED_COLOR_INFO
                )
                await channel.send(embed=embed, view=CreateEventPromptView())
                logger.info(f'Newsletter nudge posted: "{title}"')

        self.last_newsletter_id = max(new_ids)
        await self.save_data()

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

        head = f"**{event_name}** starts in **{hours_text} hours**."
        if 'happy' in name_lower or 'jump' in name_lower:
            head += " Let's get stacking!!"
        elif 'chain' in name_lower:
            head += " Get ready to hit!"
        elif 'war' in name_lower:
            head += " Prepare for battle!"
        else:
            return head

        # Anything you turn up to with stacked energy gets the same line, and
        # this is WHY the window is 35 hours rather than a round 24 or 48:
        # four Xanax at a 6 to 8 hour cooldown each is about 32 hours, plus
        # the last cooldown clearing before the event. Reminding somebody 24
        # hours out is already too late to go in full.
        return (f"{head}\n\n⏱️ **Start stacking your Xanax now** "
                f"so you go in on a full bar.")

    async def check_and_announce_events(self):
        """
        Check all scheduled events and post reminders for those starting in ~35 hours.

        Called by background task.

        Returns:
            dict with 'checked' (total events), 'announced' (reminders posted this run),
            'upcoming' (list of (name, hours_until) for all scheduled events).
        """
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            logger.warning(f'Event reminders: Could not find guild (ID: {GUILD_ID})')
            return {'checked': 0, 'announced': 0, 'upcoming': []}

        now = datetime.now(timezone.utc)

        # Get all scheduled events
        try:
            events = await guild.fetch_scheduled_events()
        except Exception as e:
            logger.error(f'Event reminders: Failed to fetch events: {e}')
            return {'checked': 0, 'announced': 0, 'upcoming': []}

        checked = 0
        announced = 0
        upcoming = []

        for event in events:
            if event.status != discord.EventStatus.scheduled:
                continue

            time_until = event.start_time - now
            hours_until = time_until.total_seconds() / 3600
            checked += 1
            upcoming.append((event.name, hours_until))

            # Skip if already announced
            if event.id in self.announced_events:
                continue

            # Check if within the announcement window (33-37 hours)
            if 33 <= hours_until <= 37:
                await self._announce_event(event, hours_until)
                self.announced_events.add(event.id)
                await self.save_data()
                announced += 1

        # Clean up old event IDs (events that no longer exist)
        await self._cleanup_old_events(events)

        return {'checked': checked, 'announced': announced, 'upcoming': upcoming}

    async def _announce_event(self, event: discord.ScheduledEvent, hours_until: float):
        """Post the reminder message for an event."""
        channel_id = self._get_channel_for_event(event.name)
        channel = self.bot.get_channel(channel_id)

        if not channel:
            logger.warning(f'Event reminders: Could not find channel (ID: {channel_id}) for event "{event.name}"')
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

        # The raw event URL in the content is what makes Discord render the
        # event card with its Interested button. Without it the reminder is
        # just text, and a member who wants their own alert has to go and
        # find the event in a sidebar that only says "2 Events".
        link = f'https://discord.com/events/{event.guild_id}/{event.id}'
        try:
            await channel.send(
                content=f'{link}\n-# Click **Interested** above and Discord '
                        f'will remind you itself.',
                embed=embed)
            logger.info(f'Event reminder posted for "{event.name}" in channel {channel_id}')
        except Exception as e:
            logger.error(f'Event reminders: Failed to post reminder for "{event.name}": {e}')

    async def _cleanup_old_events(self, current_events: list):
        """Remove event IDs that no longer exist in Discord."""
        current_ids = {event.id for event in current_events}
        old_ids = self.announced_events - current_ids

        if old_ids:
            self.announced_events -= old_ids
            await self.save_data()
            logger.info(f'Event reminders: Cleaned up {len(old_ids)} old event IDs')
