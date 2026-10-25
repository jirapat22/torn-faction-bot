"""
Fill Discord's own Events tab, so "what is coming up" has an answer.

The bot already posted a 35 hour reminder for each event, but a reminder is
a single message that scrolls away. Anyone asking "what is on this week" had
nowhere to look. Discord has a built in Events tab for exactly this, members
already know it, and marking Interested gets them their own notification
without the bot pinging anybody.

It manages ONLY the events it created, tracked by key in its own state file.
An event somebody made by hand is never edited or deleted, because the tab is
shared and silently removing a person's event is worse than a missing one.

The reminders still run. event_reminders.py reads scheduled events and posts
35 hours out, routed by name, so anything created here is announced by that
with no extra wiring: the board is where you look, the reminder is what
reaches you.
"""

import logging
from datetime import datetime, date, timedelta, timezone

from utils.helpers import load_json, save_json
from config import (
    CHAINING_CHANNEL_ID,
    WAR_CHANNEL_ID,
    GENERAL_CHANNEL_ID,
)

logger = logging.getLogger(__name__)

STATE_FILE = 'data/calendar_sync.json'

# How far ahead to fill the tab. Far enough to plan a chain or a trip around,
# short enough that everything on it is still worth reading.
HORIZON_DAYS = 14

# Torn staggers most events per player between 10:00 and 16:00 TCT, so there
# is no single true start. The earliest is used, and the description says so,
# because a tab that looks precise about an approximate thing is worse than
# one that admits it.
TORN_EVENT_START_HOUR = 10

# Most Torn events run 48 hours. These do not.
TORN_EVENT_DAYS = {
    'Trick or Treat': 7,
    'Christmas Town': 14,
    'Elimination': 14,
}
DEFAULT_TORN_EVENT_DAYS = 2


def resolve_date(year, month, day):
    """
    Delegates to the reminders module so there is exactly one answer to
    "when is this event". Two copies of this rule would eventually disagree,
    and then the Events tab and the reminder would name different days for
    the same thing.
    """
    from features.event_reminders import resolve_calendar_date
    return resolve_calendar_date(year, month, day)


def torn_event_window(name, month, day, now):
    """(start, end) for this year's occurrence, or next year's if passed."""
    d = resolve_date(now.year, month, day)
    start = datetime(d.year, d.month, d.day, TORN_EVENT_START_HOUR, 0,
                     tzinfo=timezone.utc)
    days = TORN_EVENT_DAYS.get(name, DEFAULT_TORN_EVENT_DAYS)
    if start + timedelta(days=days) < now:
        d = resolve_date(now.year + 1, month, day)
        start = datetime(d.year, d.month, d.day, TORN_EVENT_START_HOUR, 0,
                         tzinfo=timezone.utc)
    return start, start + timedelta(days=days)


def desired(now, calendar_events, chain_event=None, upcoming_war=None,
            horizon_days=HORIZON_DAYS):
    """
    What the Events tab should contain. Pure, so the whole decision is
    testable without Discord.

    Returns {key: {'name', 'start', 'end', 'description'}} with epoch
    seconds. Keys are stable across runs so the same event is recognised
    rather than recreated: recreating would drop everybody's Interested mark
    every time the bot restarted.
    """
    out = {}
    horizon = now + timedelta(days=horizon_days)

    for name, month, day in (calendar_events or []):
        start, end = torn_event_window(name, month, day, now)
        # No "already finished" check here on purpose: torn_event_window
        # rolls an occurrence that has ended forward to next year under
        # exactly that condition, so the window it returns always ends in the
        # future. A second check would be dead code pretending to be a guard.
        if start > horizon:
            continue
        label = name.format(year=start.year)
        out[f'torn:{name}:{start.year}'] = {
            'name': f'\U0001f3ae {label}',
            'start': int(start.timestamp()),
            'end': int(end.timestamp()),
            'description': (
                'Torn event. Start and end are approximate: Torn lets each '
                'player choose their own start between 10:00 and 16:00 TCT, '
                'so check the Torn calendar for yours.'),
        }

    if chain_event and chain_event.get('status') in ('running', 'scheduled'):
        start = int(chain_event['start'])
        if start <= int(horizon.timestamp()):
            mn = chain_event.get('minimum')
            target = chain_event.get('target')
            desc = (f"Minimum {mn} chain hits each for the whole window. "
                    f"Type /chain and pick Event to see your own total.")
            if target:
                desc += f" Faction goal: a chain of {target}."
            out[f"chain:{chain_event['id']}"] = {
                'name': f"⛓️ {chain_event['name']}",
                'start': start,
                'end': int(chain_event['end']),
                'description': desc,
            }

    if upcoming_war and upcoming_war.get('start'):
        start = int(upcoming_war['start'])
        if start <= int(horizon.timestamp()):
            # Wars have no published end, so the tab shows a 24 hour block
            # rather than claiming to know when it finishes.
            out[f"war:{upcoming_war['war_id']}"] = {
                'name': '⚔️ Ranked war',
                'start': start,
                'end': start + 86400,
                'description': ('Ranked war starts. Answer the availability '
                                'poll before it closes, 24 hours ahead.'),
            }

    return out


def channel_for(key):
    """Where a newly created event gets announced, by key prefix."""
    if key.startswith('chain:'):
        return CHAINING_CHANNEL_ID
    if key.startswith('war:'):
        return WAR_CHANNEL_ID
    return GENERAL_CHANNEL_ID


def changed(existing, want):
    """Does a tracked event need editing? Compared on what we set."""
    return (existing.get('name') != want['name']
            or int(existing.get('start', 0)) != int(want['start'])
            or int(existing.get('end', 0)) != int(want['end'])
            or existing.get('description') != want['description'])


async def sync(bot, guild, chain_event_manager=None, war_tracker=None,
               state_file=STATE_FILE):
    """
    Reconcile the Events tab with what should be on it.

    Returns a small summary dict, or None when it could not run. Each source
    is wrapped on its own: a war lookup failing must not stop Torn's annual
    events from appearing.
    """
    if not guild:
        return None

    from features.event_reminders import CALENDAR_EVENTS
    now = datetime.now(timezone.utc)

    chain_event = None
    if chain_event_manager:
        try:
            chain_event = (chain_event_manager.active()
                           or chain_event_manager.pending())
        except Exception as e:
            logger.error(f'Calendar sync: chain event lookup failed: {e}')

    war = None
    if war_tracker:
        try:
            war = await war_tracker.get_upcoming_war()
        except Exception as e:
            logger.error(f'Calendar sync: upcoming war lookup failed: {e}')

    want = desired(now, CALENDAR_EVENTS, chain_event, war)
    state = load_json(state_file, default={}) or {}

    try:
        live = {str(e.id): e for e in await guild.fetch_scheduled_events()}
    except Exception as e:
        logger.error(f'Calendar sync: could not read scheduled events: {e}')
        return None

    import discord
    created = updated = removed = 0
    new_state = {}

    for key, w in want.items():
        tracked = state.get(key)
        event = live.get(str(tracked.get('id'))) if tracked else None
        if event is None:
            try:
                event = await guild.create_scheduled_event(
                    name=w['name'][:100],
                    description=w['description'][:1000],
                    start_time=datetime.fromtimestamp(w['start'], timezone.utc),
                    end_time=datetime.fromtimestamp(w['end'], timezone.utc),
                    entity_type=discord.EntityType.external,
                    location='Torn',
                    privacy_level=discord.PrivacyLevel.guild_only,
                    reason='Faction calendar')
                created += 1
                # Say it once, with the link. Creating an event is otherwise
                # completely silent, and the 35 hour reminder only fires in a
                # 33 to 37 hour window, so anything set up the night before
                # would never be announced at all.
                try:
                    ch = bot.get_channel(channel_for(key))
                    if ch:
                        link = (f'https://discord.com/events/'
                                f'{guild.id}/{event.id}')
                        await ch.send(
                            f"\U0001f4c5 **{w['name']}** is on the calendar."
                            + '\n' + link + '\n'
                            + '-# Click **Interested** and Discord will '
                              'remind you when it starts.')
                except Exception as e:
                    logger.error(f'Calendar sync: created but could not '
                                 f'announce "{w["name"]}": {e}')
            except Exception as e:
                logger.error(f'Calendar sync: could not create "{w["name"]}": {e}')
                continue
        elif changed(tracked, w):
            try:
                await event.edit(
                    name=w['name'][:100],
                    description=w['description'][:1000],
                    start_time=datetime.fromtimestamp(w['start'], timezone.utc),
                    end_time=datetime.fromtimestamp(w['end'], timezone.utc),
                    reason='Faction calendar')
                updated += 1
            except Exception as e:
                logger.error(f'Calendar sync: could not edit "{w["name"]}": {e}')
        new_state[key] = {'id': str(event.id), 'name': w['name'],
                          'start': w['start'], 'end': w['end'],
                          'description': w['description']}

    # Anything we created that is no longer wanted, such as a cancelled chain
    # event. Only ever our own keys; a human's event is never touched.
    for key, tracked in state.items():
        if key in want:
            continue
        event = live.get(str(tracked.get('id')))
        if event is None:
            continue
        try:
            await event.delete(reason='Faction calendar: no longer scheduled')
            removed += 1
        except Exception as e:
            logger.error(f'Calendar sync: could not remove "{key}": {e}')
            new_state[key] = tracked

    if new_state != state:
        save_json(state_file, new_state)
    if created or updated or removed:
        logger.info(f'Calendar sync: {created} created, {updated} updated, '
                    f'{removed} removed')
    return {'created': created, 'updated': updated, 'removed': removed,
            'tracked': len(new_state)}

def event_link(guild_id, key, state_file=STATE_FILE):
    """
    The Discord event URL for one of our keys, or None.

    Posting this raw URL is what makes Discord render the event card with
    its Interested button, which is the only way a member gets their own
    reminder without the bot pinging everybody.
    """
    if not guild_id:
        return None
    state = load_json(state_file, default={}) or {}
    tracked = state.get(key)
    if not tracked or not tracked.get('id'):
        return None
    return f"https://discord.com/events/{guild_id}/{tracked['id']}"
