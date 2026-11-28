"""
Where a member's @ApexPunk answer goes (Pat, 28/11/2026; SirAua: "make a
separate channel for bot commands so it doesn't spam the main talking
channel").

- The bot's HOME channel (found by name, `HOME_NAME`) answers everything.
- Topic channels answer their own topic: war questions in #war-room, chain
  questions in #chain, OC questions in #organised-crimes.
- #punk-plaza (and any other channel) keeps the fun: jokes, roasts, hi.
- Anything else is answered IN THE HOME CHANNEL (pinging the asker), with a
  short pointer left where they asked, so the chat stays clean but nobody
  misses their answer. Banter asked in a topic channel goes to #punk-plaza.
- Busy limit: after `BUSY_ANSWERS` bot answers in `BUSY_WINDOW_S` in one
  non-home channel, everything new there goes to the home channel.
- Management is never moved.

The decision (`route`) is pure; the AskAI handler does the posting.
"""

import re
import time

HOME_NAME = 'ask-apexpunk'        # the channel is "🤖-ask-apexpunk"; matched without the emoji
BUSY_ANSWERS = 5
BUSY_WINDOW_S = 600
POINTER_DELETE_S = 60             # the "answered in #..." line tidies itself away

# Channel name pieces -> the topic that channel is for.
CHANNEL_TOPICS = [
    ('war-room', 'war'),
    ('chain', 'chain'),
    ('organised-crime', 'oc'),
    ('organized-crime', 'oc'),
    ('punk-plaza', 'plaza'),
]

TOPIC_RX = {
    'war': re.compile(r'\b(?:wars?|rw|ranked|enem(?:y|ies)|score|winning|losing|lead|target score|'
                      r'flying|abroad|retals?|xan\w*)\b|\br we\b', re.I),
    'chain': re.compile(r'\bchain\w*\b|\bbonus hits?\b', re.I),
    'oc': re.compile(r'\bocs?\b|\borgani[sz]ed crimes?\b|\bcrimes?\b|\bscope\b', re.I),
}


def channel_topic(name, channel_id=None, home_id=None):
    """'home', 'war', 'chain', 'oc', 'plaza' or 'other' for a channel."""
    if home_id and channel_id == home_id:
        return 'home'
    n = (name or '').lower()
    if HOME_NAME in n:
        return 'home'
    for piece, topic in CHANNEL_TOPICS:
        if piece in n:
            return topic
    return 'other'


def question_topic(question, fun=False):
    """'fun' (bot-made banter), 'war', 'chain', 'oc' or 'stats' (anything else)."""
    if fun:
        return 'fun'
    for topic, rx in TOPIC_RX.items():
        if rx.search(question or ''):
            return topic
    return 'stats'


def route(where, topic, busy=False, management=False):
    """Where the answer goes: 'here', 'home' or 'plaza'."""
    if management or where == 'home':
        return 'here'
    if busy:
        return 'home'
    if where in ('war', 'chain', 'oc'):
        if topic == where:
            return 'here'
        return 'plaza' if topic == 'fun' else 'home'
    # #punk-plaza and every other channel: banter stays, the rest moves.
    return 'here' if topic == 'fun' else 'home'


class BusyMeter:
    """Bot answers per channel in the last BUSY_WINDOW_S (in memory)."""

    def __init__(self):
        self.times = {}

    def add(self, channel_id, now=None):
        self.times.setdefault(channel_id, []).append(now or time.time())

    def busy(self, channel_id, now=None):
        now = now or time.time()
        recent = [t for t in self.times.get(channel_id, []) if now - t < BUSY_WINDOW_S]
        self.times[channel_id] = recent
        return len(recent) >= BUSY_ANSWERS


def find_channel(guild, topic):
    """The guild's channel for a route target ('home' or 'plaza'), by name."""
    piece = HOME_NAME if topic == 'home' else 'punk-plaza'
    for ch in getattr(guild, 'text_channels', None) or getattr(guild, 'channels', []) or []:
        if piece in (getattr(ch, 'name', '') or '').lower():
            return ch
    return None


def pointer_text(target_mention, jump_url, topic, where):
    """The short line left where the member asked."""
    nudge = {'war': 'War stuff is welcome in here, the rest',
             'chain': 'Chain stuff is welcome in here, the rest',
             'oc': 'OC stuff is welcome in here, the rest'}.get(where, 'Stats and lookups')
    if target_mention and 'plaza' in target_mention.lower():
        return f'Banter lives in {target_mention} 😏 answered you there 👉 {jump_url}'
    return f'Answered in {target_mention} 👉 {jump_url}\n-# {nudge} go there to keep chat clean.'


# Repeats while waiting (Pat, 28/11/2026: spreadjam sent the same question
# three times on cooldown and got three ⏳). The first gets the normal nudge
# and ⏳; ONE repeat gets one of these; any more get silence.
PATIENCE_LINES = [
    "Still on it in a sec, no need to say it twice 😅",
    "Heard you the first time, punk 👂 hang tight.",
    "Patience, tiger 🐯 I'll take it once the timer's done.",
    "Same question, same answer, same wait ⏳ chill.",
    "Spamming me won't make me faster, trust me 😂",
    "I'm a bot, not a goldfish 🐠 I remember. Give it a minute.",
]


def normalise(text):
    return re.sub(r'[^a-z0-9 ]', '', (text or '').lower()).strip()
