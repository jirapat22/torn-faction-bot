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
    ('Ranked war', 'A weekly faction event. A war lasts a week at most. We enlist first; Torn matchmakes '
                   'enlisted factions every Tuesday. We alternate war weeks and training weeks.'),
    ('Chain', 'A weekly faction event.'),
    ('Happy jump', 'A weekly faction event that lasts 24 hours.'),
]


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
    now = now or time.time()
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
    """events_text for just these events, or '' when there are none."""
    if not names:
        return ''
    parts = []
    for n in names:
        t = events_text(n, path=path, now=now, limit=1)
        parts += [l for l in t.split('\n')[1:] if l.startswith('- ') and l not in parts]
    return 'EVENT FACTS for this announcement:\n' + '\n'.join(parts) if parts else ''


# Short detail lines the bot adds under each event in an announcement, and the
# words that show the AI already said it (so it isn't said twice).
FACTION_DETAILS = {
    'Ranked war': ('A war lasts a week at most. Torn matchmakes enlisted factions every Tuesday.', 'tuesday'),
    'Happy jump': ('Lasts 24 hours.', '24 hours'),
}
_LINE_CUES = {
    'Ranked war': re.compile(r'\b(wars?|enlist\w*)\b', re.I),
    'Happy jump': re.compile(r'happy\s*jump', re.I),
    'Chain': re.compile(r'\bchain', re.I),
}


def _torn_detail(title, path):
    saved = load_json(path, default={}) or {}
    for e in (saved.get('events') or []) + (saved.get('competitions') or []):
        if e.get('title') == title:
            desc = (e.get('description') or '').strip().rstrip('.')
            if not desc:
                return None
            note = ' Pick your start time in the Torn calendar.' if e in (saved.get('events') or []) else ''
            # Long descriptions (Christmas Town, Easter) are cut to their first sentence.
            first = re.split(r'(?<=[.!])\s', desc)[0].rstrip('.')
            return (f'Torn: {first}.{note}', first.split()[0].lower() if first else '')
    return None


def enrich_draft(text, names, path=CALENDAR_FILE):
    """
    Add each event's real details under its line in a draft. Done by the bot,
    not the AI: given the facts and told to include them, the AI left them out.
    A detail is skipped when the draft already says it.
    """
    lines = text.split('\n')
    for name in names:
        detail = FACTION_DETAILS.get(name) or _torn_detail(name, path)
        if not detail:
            continue
        line_text, already = detail
        if already and already in text.lower():
            continue
        cue = _LINE_CUES.get(name) or re.compile(
            '|'.join(re.escape(w) for w in re.findall(r'[A-Za-z]{4,}', name)
                     if w.lower() not in _GENERIC_TITLE_WORDS) or re.escape(name), re.I)
        for i, l in enumerate(lines):
            if cue.search(l):
                lines.insert(i + 1, f'- {line_text}')
                break
    return '\n'.join(lines)
