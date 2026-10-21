"""
Did this person say they were going to be away?

Three faction rules turn on it - seven days offline, missing the chain
minimum, no hits in a war - and every one of them reads "without a notice".
The bot could not see #leave-notices at all, so management checked by hand
every single time, and a strike could be raised against somebody who had
posted perfectly properly a week earlier.

Deliberately NOT a date parser. Real notices look like "I'm away 29th Sept
to 3rd Oct", "On summer leave from 14-30 of August", "holiday 29jul-29aug" -
no format survives that, and a parser that is wrong a tenth of the time is
worse than none, because it would clear somebody who never posted or flag
somebody who did. All this does is answer "who posted, when, and what did
they say", and let a human read it.

Attribution is by author, so a manager replying in the channel counts as
that manager having posted a notice. That is harmless - management is not
who these alerts are about - and the alternative is guessing who a reply is
about, which is the same mistake as parsing the dates.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# How far back a notice still counts for. Longer than the longest rule it
# feeds (7 days offline), because somebody who posted three weeks ago saying
# "back in a month" is exactly the case this exists to catch.
NOTICE_WINDOW_DAYS = 60
# Enough of the message to recognise it; the rest is one click away.
SNIPPET = 90


def summarise(messages, now=None, window_days=NOTICE_WINDOW_DAYS):
    """
    Latest notice per person. Pure, so it can be tested without Discord.

    messages: [{'author_id': str, 'timestamp': epoch, 'content': str}]
    Returns {author_id: {'ts': float, 'text': str}}.

    Empty messages (an image or a sticker with no words) are skipped: a line
    saying 'posted a notice' with nothing to read is not evidence of
    anything.
    """
    now = (now or datetime.now(timezone.utc).timestamp())
    cutoff = now - window_days * 86400
    out = {}
    for m in messages or []:
        text = (m.get('content') or '').strip()
        if not text:
            continue
        ts = m.get('timestamp') or 0
        if ts < cutoff:
            continue
        author = str(m.get('author_id'))
        if author not in out or ts > out[author]['ts']:
            first = text.split('\n')[0].strip()
            if len(first) > SNIPPET:
                first = first[:SNIPPET - 1].rstrip() + '…'
            out[author] = {'ts': ts, 'text': first}
    return out


def note_for(discord_id, notices, now=None):
    """
    One line to hang off an alert, or None when they never posted.

    Says how long ago rather than a date, because the only question being
    asked is "is this recent enough to explain the silence".
    """
    if not discord_id or not notices:
        return None
    rec = notices.get(str(discord_id))
    if not rec:
        return None
    now = (now or datetime.now(timezone.utc).timestamp())
    days = max(0, (now - rec['ts']) / 86400)
    when = 'today' if days < 1 else (
        'yesterday' if days < 2 else f'{days:.0f} days ago')
    return f'\U0001f4dd notice {when}: "{rec["text"]}"'


async def fetch(bot, channel_id, limit=100):
    """
    Read the notice channel. Returns the summarise() dict, or None when it
    could not be read.

    None, not {}: an unreadable channel must not look like "nobody has
    posted a notice", which would turn every alert into an accusation.
    """
    if not bot or not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning('Notices: leave-notices channel not found')
        return None
    rows = []
    try:
        async for msg in channel.history(limit=limit):
            if msg.author and getattr(msg.author, 'bot', False):
                continue
            rows.append({
                'author_id': str(msg.author.id) if msg.author else '',
                'timestamp': msg.created_at.timestamp(),
                'content': msg.content or '',
            })
    except Exception as e:
        logger.error(f'Notices: could not read the channel: {e}')
        return None
    return summarise(rows)
