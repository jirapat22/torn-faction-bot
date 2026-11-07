"""
What the bot knows about events, for "@ApexPunk" answers and announcements.

Two kinds, both FACTS rather than AI memory (an announcement once called
World Diabetes Day a charity day: "Join the cause and show support"):

1. Torn's calendar events, from Torn's own API (v2 torn/calendar): exact
   start/end in TCT and Torn's one-line effect, e.g. World Diabetes Day =
   "Candy effects are tripled". Saved to data/torn_calendar.json and
   refreshed once a day; if Torn can't be reached the saved copy is used.
   (The Torn wiki refuses automated reads, the API does not.)
2. The faction's own weekly events, as Pat described them (07/11/2026):
   ranked war (a week at most; Torn matchmakes enlisted factions every
   Tuesday), chain, and happy jump (24 hours). Edit FACTION_EVENTS to change.
"""

import logging
import os
import re
import time
from datetime import datetime, timezone

from utils import torn_api
from utils.helpers import load_json, save_json

logger = logging.getLogger(__name__)

CALENDAR_FILE = 'data/torn_calendar.json'
REFRESH_EVERY_S = 24 * 3600
DAY = 86400

# How Torn's calendar works for players (Pat, 07/11/2026): each player picks
# their own start time in the calendar (seen: 10:00 to 16:00 TCT in 15-minute
# steps; Pat isn't sure every event uses that window), and the event then ends
# at that same time of day after its normal length (a one-day event picked at
# 16:00 runs 16:00 to 16:00 the next day).
PICK_START_NOTE = ('Players pick their own start time in the Torn calendar (10:00 to 16:00 TCT for at least some '
                   'events); it then ends at that same time of day after its normal length.')

# Pat's own descriptions. Only facts he gave: no durations or rules made up.
FACTION_EVENTS = [
    ('Ranked war', 'A weekly faction event. Enlistment starts on Tuesday (Torn matchmakes enlisted factions '
                   'every Tuesday) and we war on the weekend. A war lasts a week at most. We alternate war weeks '
                   'and training weeks.'),
    ('Chain', 'A weekly faction event. Normally runs 24 hours.'),
    ('Happy jump', 'A weekly faction event that lasts 24 hours.'),
]

# Channels the detail lines link to (IDs from the rulebook posts).
WAR_AVAILABILITY = '<#1559499530385235978>'
HAPPY_JUMP_GUIDE = '<#1560218041751437405>'
WAR_GUIDE = '<#1559868505044815892>'

# Emoji per event for announcement headings (Pat's pick: event emojis).
EVENT_EMOJI = {
    'Ranked war': '⚔️', 'Chain': '⛓️', 'Happy jump': '🦘', 'World Diabetes Day': '🍭',
    'Christmas town': '🎄', 'Trick or Treat': '🎃', 'Easter Egg Hunt': '🥚', "Valentine's Day": '💘',
    'Black Friday': '🛍️', 'Slash Wednesday': '🏥', 'Torn Anniversary': '🎂', 'Elimination': '🏆',
    "St Patrick's Day": '🍀', 'Weekend Road Trip': '🏎️', 'Tourism Day': '✈️', 'World Blood Donor Day': '🩸',
}


async def refresh(path=CALENDAR_FILE, force=False, now=None):
    """Fetch Torn's calendar if the saved copy is over a day old. Never wipes the
    saved copy on a failed fetch (None-vs-empty, as everywhere in this repo)."""
    now = now or time.time()
    saved = load_json(path, default={}) or {}
    if not force and now - saved.get('fetched_at', 0) < REFRESH_EVERY_S:
        return False
    data = await torn_api._make_api_request('v2/torn', {'selections': 'calendar'})
    cal = (data or {}).get('calendar')
    if not cal or not (cal.get('events') or cal.get('competitions')):
        logger.warning('Torn calendar: could not fetch, keeping the saved copy')
        return False
    save_json(path, {'fetched_at': now, 'events': cal.get('events') or [],
                     'competitions': cal.get('competitions') or []})
    logger.info(f'Torn calendar: saved {len(cal.get("events") or [])} events, '
                f'{len(cal.get("competitions") or [])} competitions')
    return True


def _fmt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%A %d/%m/%Y %H:%M') if ts else 'unknown'


def _duration(start, end):
    if not start or not end:
        return 'unknown length'
    secs = end - start + 60          # Torn ends at 23:59, so add the last minute back
    days = secs / DAY
    if days >= 1.5:
        return f'{round(days)} days'
    return f'{round(secs / 3600)} hours'


def _matches(words, title):
    """Whole-word match, so "war" doesn't match "Awareness" and "worlds" still
    matches "World"."""
    tokens = re.findall(r'[a-z0-9]+', (title or '').lower())
    for w in words:
        for t in tokens:
            if t == w or (len(w) >= 4 and len(t) >= 4 and (t.startswith(w) or w.startswith(t))):
                return True
    return False


def events_text(query='', path=CALENDAR_FILE, now=None, limit=8):
    """
    Plain-English event facts for the AI. A query matches event names (and
    "next"/"upcoming"/empty lists what's coming up). Faction events are
    included when the query mentions them, or always for an empty query.
    """
    now = time.time() if now is None else now      # now=0 means "every event, past ones too"
    saved = load_json(path, default={}) or {}
    torn = [dict(e, kind='Torn event') for e in saved.get('events') or []] + \
           [dict(e, kind='Torn competition') for e in saved.get('competitions') or []]
    q = (query or '').lower().strip()
    words = [w for w in re.findall(r'[a-z0-9]+', q) if w not in {'the', 'day', 'event', 'events', 'when', 'is',
                                                              'what', 'does', 'next', 'upcoming', 'torn', 'of'}]
    out = []
    if not torn:
        out.append("Torn's calendar is not saved yet, so official event dates are unknown right now.")
    else:
        if words:
            picked = [e for e in torn if _matches(words, e.get('title'))]
        else:
            picked = [e for e in torn if (e.get('end') or 0) >= now]
        picked.sort(key=lambda e: e.get('start') or 0)
        for e in picked[:limit]:
            state = ('on now' if (e.get('start') or 0) <= now <= (e.get('end') or 0)
                     else 'finished' if (e.get('end') or 0) < now else 'upcoming')
            out.append(f'- {e.get("title")} ({e["kind"]}, {state}): {_fmt(e.get("start"))} to '
                       f'{_fmt(e.get("end"))} TCT, {_duration(e.get("start"), e.get("end"))}. '
                       f'Effect, in Torn\'s words: "{(e.get("description") or "not stated").strip()}"'
                       + (f' {PICK_START_NOTE}' if e['kind'] == 'Torn event' else ''))
        if torn and not picked and words:
            out.append(f'No Torn event matching "{query}" in this year\'s calendar.')
    faction = [f for f in FACTION_EVENTS if not words or _matches(words, f[0])]
    for name, desc in faction:
        out.append(f'- {name} (faction event): {desc}')
    return 'EVENTS (official Torn calendar, plus our faction\'s own weekly events):\n' + '\n'.join(out)


# Words that say a faction weekly event is in the text.
_FACTION_CUES = {
    'Ranked war': re.compile(r'\b(wars?|enlist\w*|ranked)\b', re.I),
    'Chain': re.compile(r'\bchain(s|ing)?\b', re.I),
    'Happy jump': re.compile(r'\bhappy\s*jump\w*\b', re.I),
}
_GENERIC_TITLE_WORDS = {'day', 'days', 'week', 'the', 'of', 'torn', 'town', 'awareness'}


def detect_events(text, path=CALENDAR_FILE):
    """
    Names of the events a piece of text talks about: a Torn event counts when
    every distinctive word of its title is there ("worlds diabetes day" ->
    World Diabetes Day; "next two weeks" does NOT pull in Awareness Week).
    """
    tokens = re.findall(r'[a-z0-9]+', (text or '').lower())
    def has(word):
        return any(t == word or (len(word) >= 4 and len(t) >= 4 and (t.startswith(word) or word.startswith(t)))
                   for t in tokens)
    saved = load_json(path, default={}) or {}
    found = []
    for e in (saved.get('events') or []) + (saved.get('competitions') or []):
        title = e.get('title') or ''
        words = [w for w in re.findall(r'[a-z0-9]+', title.lower())
                 if w not in _GENERIC_TITLE_WORDS and not w.isdigit()]
        if words and all(has(w) for w in words) and title not in found:
            found.append(title)
    for name, cue in _FACTION_CUES.items():
        if cue.search(text or ''):
            found.append(name)
    return found


def facts_for(names, path=CALENDAR_FILE, now=None):
    """
    The fact lines for exactly these events, or '' when there are none.
    Torn events are matched by their EXACT title: a word match on "World
    Diabetes Day" once picked World Blood Donor Day (the first "World ..."
    event of the year) and put its effect into Pat's announcement.
    """
    if not names:
        return ''
    parts = []
    full = events_text('', path=path, now=0, limit=100).split('\n')[1:]   # every event, past or not
    for n in names:
        line = next((l for l in full if l.startswith(f'- {n} (')), None)
        if line and line not in parts:
            parts.append(line)
    return 'EVENT FACTS for this announcement:\n' + '\n'.join(parts) if parts else ''


# Detail lines the bot adds under each event in an announcement (Pat's picks,
# 07/11/2026). Each has a pattern that shows the draft already says it, so a
# point is never made twice.
FACTION_DETAILS = {
    # Pat trimmed these (07/11/2026): war keeps only revives/turtle and
    # stack/scout/guide; the rest merged into fewer lines to keep posts short.
    'Ranked war': [
        ('Revives OFF for the whole war. Going offline? Turtle first.', r'revive'),
        (f'Stack for the start and scout the enemy with FFScouter. War guide: {WAR_GUIDE}', r'scout|war guide'),
    ],
    'Chain': [
        ('Normally runs 24 hours. Minimum 10 hits (5 if you are under level 15).', r'minimum|10 hits'),
        (f"Stack your energy before it starts. Can't make it? Post in {WAR_AVAILABILITY} 24 hours before.",
         r"can.?t make it|\bstack"),
    ],
    'Happy jump': [
        (f'Lasts 24 hours. Happy Jumping Guide: {HAPPY_JUMP_GUIDE}', r'jumping guide'),
    ],
}
_LINE_CUES = {
    'Ranked war': re.compile(r'\b(wars?|enlist\w*)\b', re.I),
    'Happy jump': re.compile(r'happy\s*jump', re.I),
    'Chain': re.compile(r'\bchain', re.I),
}


def _torn_detail(title, path):
    """[(line, already-said pattern)] for a Torn event: its effect in Torn's
    words, and the start-time pick for calendar events."""
    saved = load_json(path, default={}) or {}
    for e in (saved.get('events') or []) + (saved.get('competitions') or []):
        if e.get('title') == title:
            desc = (e.get('description') or '').strip().rstrip('.')
            out = []
            pick = ' Pick your start time in the Torn calendar.' if e in (saved.get('events') or []) else ''
            if desc:
                # Long descriptions (Christmas Town, Easter) are cut to their first sentence.
                first = re.split(r'(?<=[.!])\s', desc)[0].rstrip('.')
                out.append((f'Torn: {first}.{pick}', re.escape(first.split()[0]) if first else r'$^'))
            elif pick:
                out.append((pick.strip(), r'start time'))
            return out
    return []


def enrich_draft(text, names, path=CALENDAR_FILE):
    """
    Add each event's real details under its line in a draft. Done by the bot,
    not the AI: given the facts and told to include them, the AI left them out.
    A detail is skipped when the draft already says it.
    """
    lines = text.split('\n')
    for name in names:
        details = FACTION_DETAILS.get(name) or _torn_detail(name, path)
        cue = _LINE_CUES.get(name) or re.compile(
            '|'.join(re.escape(w) for w in re.findall(r'[A-Za-z]{4,}', name)
                     if w.lower() not in _GENERIC_TITLE_WORDS) or re.escape(name), re.I)
        # Only under an event's own HEADING (Pat, 07/11/2026): a passing "the
        # chain is after the war" in a happy-jump post gets no chain/war bullets.
        at = next((i for i, l in enumerate(lines) if l.lstrip().startswith(('**', '#')) and cue.search(l)), None)
        if at is None:
            continue
        # Insert after the event's heading and any bullets already under it.
        end = at + 1
        while end < len(lines) and lines[end].lstrip().startswith(('-', '•')):
            end += 1
        block = '\n'.join(lines[at:end])
        new = [f'- {line}' for line, already in details if not re.search(already, block, re.I)]
        lines[end:end] = new
    return '\n'.join(lines)
