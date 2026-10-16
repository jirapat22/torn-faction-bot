"""
Parse "when" the way people actually type it.

Event creation used two rigid fields - a date as DD-MM-YYYY and a time as
HH:MM - and management said that was the hard part, not the command. So this
takes one free-text box instead and accepts what somebody would write in
chat: "saturday 8pm", "tomorrow 20:00", "in 3 days", "25-01-2026 14:30".

Everything is TCT, which is UTC. Torn has no other timezone and neither does
this, so nothing here guesses at local time.

The old explicit formats still parse, because the point is to stop REQUIRING
them, not to stop accepting them.
"""

import re
from datetime import datetime, timedelta, timezone

WEEKDAYS = {
    'monday': 0, 'mon': 0,
    'tuesday': 1, 'tue': 1, 'tues': 1,
    'wednesday': 2, 'wed': 2,
    'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
    'friday': 4, 'fri': 4,
    'saturday': 5, 'sat': 5,
    'sunday': 6, 'sun': 6,
}

_DATE_PATTERNS = (
    ('%d-%m-%Y', r'\d{1,2}-\d{1,2}-\d{4}'),
    ('%Y-%m-%d', r'\d{4}-\d{1,2}-\d{1,2}'),
    ('%d/%m/%Y', r'\d{1,2}/\d{1,2}/\d{4}'),
)


class WhenError(ValueError):
    """Raised with a message that is safe to show the person who typed it."""


def _parse_clock(text):
    """
    Pull a time of day out of `text`. Returns (hour, minute, leftover) or None.

    Handles 8pm, 8:30pm, 20:00 and 2000. The am/pm suffix is checked before
    the bare 24-hour forms, or "8pm" would read as hour 8.
    """
    m = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', text, re.I)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3).lower() == 'pm':
            hour += 12
        minute = int(m.group(2) or 0)
        if hour > 23 or minute > 59:
            raise WhenError(f'"{m.group(0)}" is not a real time')
        return hour, minute, (text[:m.start()] + text[m.end():]).strip()

    m = re.search(r'\b(\d{1,2}):(\d{2})\b', text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            raise WhenError(f'"{m.group(0)}" is not a real time')
        return hour, minute, (text[:m.start()] + text[m.end():]).strip()

    # 2000 / 0830, but never a 4-digit year, which a date pattern owns.
    m = re.search(r'(?<![\d/-])([01]\d|2[0-3])([0-5]\d)(?![\d/-])', text)
    if m:
        return int(m.group(1)), int(m.group(2)), (
            text[:m.start()] + text[m.end():]).strip()
    return None


def parse_when(text, now=None):
    """
    Turn free text into an aware UTC datetime.

    `now` is injectable so the behaviour is testable without waiting for a
    particular weekday to come round.

    Raises WhenError with something worth showing the user.
    """
    if not text or not text.strip():
        raise WhenError('Say when, for example "saturday 8pm" or "in 3 days".')

    now = now or datetime.now(timezone.utc)
    raw = text.strip()
    s = ' '.join(raw.lower().split())
    s = s.replace('@', ' ').replace(' at ', ' ').replace(' tct', ' ').replace(' utc', ' ')
    s = ' '.join(s.split())

    # ---- relative: "in 3 days", "in 90 minutes" ----
    m = re.fullmatch(r'in\s+(\d+)\s*(min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)', s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith('min'):
            delta = timedelta(minutes=n)
        elif unit.startswith(('h', 'hr')):
            delta = timedelta(hours=n)
        elif unit.startswith(('d', 'day')):
            delta = timedelta(days=n)
        else:
            delta = timedelta(weeks=n)
        if delta <= timedelta(0):
            raise WhenError('That is not in the future.')
        return (now + delta).replace(second=0, microsecond=0)

    clock = _parse_clock(s)
    hour, minute = (clock[0], clock[1]) if clock else (0, 0)
    rest = (clock[2] if clock else s).strip(' ,')

    # ---- explicit date, with or without a time ----
    for fmt, pattern in _DATE_PATTERNS:
        m = re.search(pattern, rest)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(0), fmt)
        except ValueError as exc:
            raise WhenError(f'"{m.group(0)}" is not a real date') from exc
        when = d.replace(hour=hour, minute=minute, tzinfo=timezone.utc)
        if not clock:
            raise WhenError('Add a time too, for example '
                            f'"{m.group(0)} 20:00".')
        return when

    # ---- today / tomorrow ----
    if rest in ('today', ''):
        if not clock:
            raise WhenError('Say when, for example "saturday 8pm" or '
                            '"in 3 days".')
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # A bare time already gone today means they mean tomorrow. Creating an
        # event in the past is never what anybody wanted.
        if when <= now:
            when += timedelta(days=1)
        return when

    if rest == 'tomorrow':
        when = (now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        return when

    # ---- a weekday name: the NEXT one, never today if it has passed ----
    for word in rest.replace(',', ' ').split():
        if word in WEEKDAYS:
            target = WEEKDAYS[word]
            ahead = (target - now.weekday()) % 7
            when = (now + timedelta(days=ahead)).replace(
                hour=hour, minute=minute, second=0, microsecond=0)
            if when <= now:
                when += timedelta(days=7)
            return when

    raise WhenError(
        f'Could not read "{raw}". Try "saturday 8pm", "tomorrow 20:00", '
        f'"in 3 days", or a date like "25-01-2026 14:30".')


def describe(when):
    """A short confirmation line: Discord renders both parts in local time."""
    unix = int(when.timestamp())
    return f'<t:{unix}:F> (<t:{unix}:R>) · {when:%d-%m-%Y %H:%M} TCT'
