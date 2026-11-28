"""
Where a member's @ApexPunk answer goes (Pat, 28/11/2026; SirAua: "make a
separate channel for bot commands so it doesn't spam the main talking
channel").

- The bot's HOME channel (found by name, `HOME_NAME`) answers everything.
- Jokes and chat (bot-made banter) are answered where they were asked.
- Anything else is answered IN THE HOME CHANNEL: their message is forwarded
  there and the answer replies to it with a ping, and a short pointer is left
  where they asked. Pat, 28/11/2026: one simple rule (topic channels and a
  busy limit were tried the same day and dropped as too messy).
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
    """Where the answer goes: 'here' or 'home'. Pat, 28/11/2026, after a
    day of topic-channel and busy-limit rules ("getting a little messy"):
    ONE rule. Jokes and chat stay where they were asked; everything else is
    answered in the home channel. Management is never moved. `busy` is
    kept in the signature for old callers and ignored."""
    if management or where == 'home' or topic == 'fun':
        return 'here'
    return 'home'


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
    return f'Answered in {target_mention} 👉 {jump_url}\n-# Questions go there to keep chat clean.'


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
