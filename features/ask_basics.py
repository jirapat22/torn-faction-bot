"""
New member basics and faction facts, answered by the bot itself (Pat,
23/11/2026). No AI: everything here is quoted from the rulebook
(#faction-edicts, synced to data/faction_rules_live.md), read from the
server's real channel list, or read live from Torn's faction profile.
Nothing is invented: a fact the bot can't find is left out.

Open to members too. None of it is anyone's personal record.
"""

import re
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Channels: (name as typed, what it's for). Found by name in the server at
# answer time and turned into real links; one that doesn't exist is skipped.
# The "what it's for" text follows the rulebook's own wording.
# ---------------------------------------------------------------------------

GUIDE_CHANNELS = [
    ('faction-edicts', 'the rules. Read them and react ✅', ('rule', 'edict')),
    ('announcements', 'faction news, events and war calls', ('announce', 'news', 'event')),
    ('leave-notices', 'post here before you go away: when you leave and when you\'re back',
     ('leave', 'notice', 'away', 'holiday', 'absen', 'vacation', 'offline')),
    ('war-availability', 'answer the war poll and post your times', ('availab', 'poll', 'times')),
    ('war-room', 'war chat: call bonus hits, ask for a friendly hosp, get help scouting',
     ('war', 'bonus', 'hosp', 'scout', 'turtl')),
    ('chain', 'chain chat: call a bonus hit before you take one', ('chain',)),
    ('ask-a-punk', 'ask anything, veterans jump in', ('question', 'ask', 'help')),
    ('guides-hub', 'OC, war, chain, stacking and level 15 guides', ('guide', 'learn', 'oc', 'stack', 'tutorial')),
    ('commands-the-punk', 'my commands and how to ask me things', ('command', 'bot')),
    ('punk-plaza', 'general chat', ('chat', 'general', 'talk', 'hang')),
    ('suggestion', 'ideas for the rules or the faction', ('suggest', 'idea', 'feedback')),
]


def _bare(name):
    """'📝-leave-notices' -> 'leave-notices' (how people type it)."""
    return re.sub(r'^[^\w]+-?', '', name or '').lower()


def find_channels(guild):
    """{guide name: channel id} for the guide channels that exist."""
    found = {}
    chans = list(getattr(guild, 'channels', None) or [])
    for want, _what, _keys in GUIDE_CHANNELS:
        exact = [c for c in chans if _bare(c.name) == want]
        loose = [c for c in chans if want in c.name.lower()]
        hit = (exact or loose or [None])[0]
        if hit is not None:
            found[want] = hit.id
    return found


def channel_lines(guild, only=None):
    """'- <#id>: what it's for' for every guide channel that exists."""
    found = find_channels(guild)
    out = []
    for want, what, _keys in GUIDE_CHANNELS:
        if only and want not in only:
            continue
        if want in found:
            out.append(f'- <#{found[want]}>: {what}')
    return out


def linked(text, guild):
    """Rulebook text with '#📝-leave-notices' style names turned into links."""
    if guild is None:
        return text
    from features.rules_posts import names_to_links
    try:
        out, _unknown = names_to_links(text, {c.id: c.name for c in guild.channels})
        return out
    except Exception:
        return text


# ---------------------------------------------------------------------------
# Rulebook quotes
# ---------------------------------------------------------------------------

# Topic -> rulebook section titles (the part after ' > '), in order.
TOPICS = [
    ('oc', r"\bocs?\b|organi[sz]ed crimes?|\bcrimes?\b", ['Organized Crimes (OC)']),
    ('bonus', r"\bbonus(?: hits?)?\b", ['Bonus hits']),
    ('chain', r"\bchain(?:s|ing)?\b", ['Chains', 'Bonus hits']),
    ('turtle', r"\bturtl\w*|friendly hosp\w*", ['Turtling: don\'t bleed points', 'Friendly hosps']),
    ('war', r"\b(?:ranked )?wars?\b|\bwar weeks?\b", ['Availability', 'Prep', 'Fighting']),
    ('strike', r"\bstrikes?\b|\bremov\w+|\bkick\w*", ['How strikes work', 'What gets a strike']),
    ('leave', r"\bleave notices?\b|\bnotices?\b|\bgoing away\b|\babsen\w+|\bholiday\b|\bvacation\b|\binactiv\w+|"
              r"\bactivity\b", ['Going away', 'Daily baseline']),
    ('trial', r"\btrial\w*\b|\blevel 15\b", ['Trial period', 'New players (under level 15)']),
    ('join', r"\bjoin\w*\b", ['Joining']),
]

# "how do chains work HERE", "how does our faction do OCs", "what are our
# war rules": about the faction's own way, not Torn's game mechanics (those
# go to the wiki pages, features/torn_wiki.py).
HERE_RX = re.compile(
    r"\b(?:here|in (?:this|our|the) faction|for us|with us|in (?:ap|ascended punks)|at (?:ap|ascended punks))\b|"
    r"\bhow (?:do|does) (?:we|our faction|this faction|the faction|ap|ascended punks) (?:do|run|handle|work)\b|"
    r"\b(?:our|the faction'?s?) (?:rules? (?:on|for|about)|\w+ rules)\b",
    re.I)
HOW_RX = re.compile(r"\bhow (?:do|does|are|is)\b.*\b(?:work|works|done|run|handled)\b|\bexplain\b|\btell me about\b|"
                    r"\brules?\b|\bhow (?:do|does) (?:we|our faction|this faction|the faction) (?:do|run|handle)\b",
                    re.I)
# Live or records wording: "what's the war score in the faction" wants the
# score, not the rulebook.
LIVE_RX = re.compile(r"\b(?:score|now|rn|atm|current\w*|next|last|today|tonight|this week|winning|losing|lead\w*|"
                     r"status|who|how many|how much|my|me|mine|did|was|were|history|report)\b", re.I)


def rule_sections():
    from features.ask_ai import load_rule_sections
    return load_rule_sections()


def section(title_tail):
    for title, body in rule_sections():
        # Live titles can carry a guide link: "Organized Crimes (OC)  #🫆 ... Guide".
        if title.split(' > ')[-1].strip().lower().startswith(title_tail.lower()):
            return title, body
    return None


def quote_sections(tails, guild=None, limit=1500):
    """The named rulebook sections, quoted word for word, as one text."""
    out = []
    for tail in tails:
        found = section(tail)
        if not found:
            continue
        title, body = found
        body = re.sub(r'^-# .*$', '', body, flags=re.M).strip()   # Discord small-print lines
        head = re.sub(r'\s+#.*$', '', title.split(' > ')[-1]).strip()
        out.append(f'**{head}**\n{body}')
    text = linked('\n\n'.join(out), guild)
    if len(text) > limit:
        text = text[:limit].rsplit('\n', 1)[0] + '\n...'
    return text


def how_here_topic(question):
    """The rulebook topic a "how do X work here?" question is about, or None."""
    q = question or ''
    if not HERE_RX.search(q) or not HOW_RX.search(q):
        return None
    if LIVE_RX.search(q):
        return None
    for name, rx, tails in TOPICS:
        if re.search(rx, q, re.I):
            return name, tails
    return None


# ---------------------------------------------------------------------------
# New members
# ---------------------------------------------------------------------------

NEW_RX = re.compile(
    r"\b(?:i'?m|i am|im) (?:brand )?new\b|\bnew (?:here|to (?:the )?(?:faction|punks|ap|torn|this))\b|"
    r"\bjust joined\b|\bwhat (?:do|should) i do (?:first|now|next)\b|\bwhere (?:do|should) i (?:start|begin)\b|"
    r"\bgetting started\b|\bfirst steps?\b|\bnew (?:member|player)s? guide\b|\bhow do i (?:start|begin)\b|"
    r"\bonboarding\b|\bwhat do new (?:members|people|players) (?:do|need)\b", re.I)
VERIFY_RX = re.compile(
    r"\bhow (?:do|can|should) i (?:get )?(?:verif\w*|regist\w*|link (?:my )?(?:account|torn|discord))\b|"
    r"\bget(?:ting)? verified\b|\bverify me\b|\bhow (?:to|do you) (?:get )?(?:verif\w*|regist\w*)\b|"
    r"\bwhy am i not verified\b|\bhow do i get (?:the )?(?:role|punk role|member role)\b", re.I)
CHANNELS_RX = re.compile(
    r"\b(?:which|what) channels?\b|\bchannels? (?:matter|to (?:use|read|check|follow|watch)|guide|list)\b|"
    r"\bimportant channels\b|\bwhere (?:do|should|can) i (?:post|ask|put|talk|chat|find|say|tell)\b|"
    r"\bwhat'?s each channel for\b|\bwhich channel\b", re.I)
NOT_BASICS_RX = re.compile(r"\bhow many\b|\bwho (?:is|are|was|were|joined|left)\b|\bwhich members?\b", re.I)

VERIFY_TEXT = ("Type `/register` with your Torn ID (the number in your Torn profile link). I check it, set your "
               "nickname to your Torn name and give you the Ascended Punks role. 🤘")


def new_member_text(guild):
    parts = ["Welcome to the Punks! 🤘 Here's how to start:",
             f"**1. Get verified:** {VERIFY_TEXT}"]
    join = quote_sections(['Joining'], guild, limit=700)
    if join:
        parts.append('**2. From the rules:**\n' + join.split('\n', 1)[-1])
    trial = quote_sections(['Trial period'], guild, limit=500)
    if trial:
        parts.append(trial)
    chans = channel_lines(guild, only={'faction-edicts', 'leave-notices', 'war-availability', 'ask-a-punk',
                                       'guides-hub'}) if guild is not None else []
    if chans:
        parts.append('**Channels to know:**\n' + '\n'.join(chans))
    ask = find_channels(guild).get('ask-a-punk') if guild is not None else None
    parts.append(f'Stuck? Ask in <#{ask}> or @ me. 💪' if ask else 'Stuck? Ask management or @ me. 💪')
    parts.append(f'🎯 What we\'re about: {FACTION_GOALS}')
    return '\n\n'.join(parts)


def channels_text(question, guild):
    if guild is None:
        return None
    q = (question or '').lower()
    # "where do I post leave notices?": just that channel, if one fits.
    hits = [want for want, _what, keys in GUIDE_CHANNELS if any(k in q for k in keys)]
    if hits and re.search(r'\bwhere\b|\bwhich channel\b', q):
        lines = channel_lines(guild, only=set(hits[:2]))
        if lines:
            return 'That goes here 👇\n' + '\n'.join(lines)
    lines = channel_lines(guild)
    if not lines:
        return None
    return 'The channels that matter 📌\n' + '\n'.join(lines)


def basics_reply(question, guild=None):
    """New member / verify / channels / "how do X work here" answer, or None."""
    q = (question or '').strip()
    if not q or NOT_BASICS_RX.search(q):
        return None
    if VERIFY_RX.search(q):
        return VERIFY_TEXT
    if NEW_RX.search(q):
        return new_member_text(guild)
    topic = how_here_topic(q)
    if topic:
        _name, tails = topic
        text = quote_sections(tails, guild)
        if text:
            where = find_channels(guild).get('faction-edicts') if guild is not None else None
            more = f'\n\nFull rules: <#{where}>' if where else ''
            return f"Here's how we do it, straight from the rulebook 📜\n\n{text}{more}"
    if CHANNELS_RX.search(q):
        return channels_text(q, guild)
    return None


# ---------------------------------------------------------------------------
# Faction facts, live from Torn's faction profile (selections=basic).
# ---------------------------------------------------------------------------

FACTION_RX = re.compile(
    r"\b(?:tell me about|info (?:on|about)|what(?:'s| is)|whats) (?:the |our |this )?(?:faction|ascended punks)\b(?!\s+\w)|"
    r"\bfaction (?:info|stats|details|overview|profile|facts)\b|"
    r"\bwho(?:'s| is|s) (?:the |our )?(?:faction )?(?:leader|co-?leader|boss|in charge|running (?:the|this) faction)\b|"
    r"\bwho(?:'s| is| are|s) (?:the |our |in )?management\b|\bwho (?:are|r) the (?:mods|officers|admins|staff)\b|"
    r"\bhow old is (?:the |our |this )?faction\b|\bfaction(?:'s)? age\b|"
    r"\bwhen was (?:the |our |this )?faction (?:made|founded|created|started)\b|"
    r"\b(?:what(?:'s| is)|whats) (?:our|the) (?:faction )?(?:rank|respect|tag|best chain|capacity)\b|"
    r"\bfaction (?:rank|respect|tag|capacity)\b|\bhow much respect (?:do we|does (?:the|our) faction)\b|"
    r"\bwhat rank (?:are we|is (?:the|our) faction)\b|\bare we (?:recruiting|full|hiring)\b|\bis (?:the |our )?faction full\b|"
    r"\bhow many (?:spots|places|spaces) (?:are )?(?:left|open|free)\b|\bour best chain\b|"
    r"\bwhat'?s (?:our|the faction'?s?) (?:ranked )?war record\b|\bhow many (?:ranked )?wars? have we won\b",
    re.I)

RANK_DIVISIONS = {1: 'I', 2: 'II', 3: 'III'}

# Pat's own answers (25/11/2026) for what Torn and the rulebook can't say.
# Everything else on the card stays live from Torn, so nothing here goes
# stale: the age, the leader and how long they've been with us are read
# fresh every time.
FACTION_GOALS = ("We're on our way up: winning wars, growing players and keeping it fun. "
                 "Everyone pulls their weight, and nobody gets left behind. 📈")
FACTION_JOIN = 'Message any of management or the leader and they\'ll sort you out. 📩'
# Motto and tagline picked by Pat, 25/11/2026. No written history (Pat):
# the bot says so instead of inventing one.
FACTION_MOTTO = 'Punks up, enemies down.'
FACTION_TAGLINE = 'From the gutter, to the top.'
# "Hype it up" (Pat): the motto comes with a different line each time,
# mixed from three pools ("mix and match more", Pat 25/11/2026). Every part
# is a whole sentence, so any mix reads right: 18 x 18 x 16 = 5,184 combos.
MOTTO_OPEN = [
    "That's the one 🔥", "Hell yeah it is 🤘", "Burned into my circuits 🔥", "Say it with me 📣",
    "Fuck yes 😤", "Our whole religion, basically 🙏", "Straight from the gutter 🧱", "Read it twice 👀",
    "Loud and proud 📢", "Hand on heart 🫡", "No notes, it's perfect 💯", "Chills, every time 🥶",
    "Bow down 👑", "Carved in the hospital wall 🏥", "Every Punk knows it 🤘", "Bang, there it is 💥",
    "Say it like you mean it 😈", "Sacred words 📜",
]
MOTTO_MIDDLE = [
    f"{FACTION_TAGLINE} That's the whole story.", "We climb, they drop. Simple maths.",
    "Every chain hit is a step up the ladder.", "Rivals hear it and quietly check their medical cooldown.",
    f"We came {FACTION_TAGLINE.lower()[:-1]}, and we're not stopping halfway.",
    "Dead weight sinks, Punks rise.", "Nobody rides free on this crew.",
    "It's a promise and a threat at the same time.", "Somewhere an enemy just felt a chill.",
    "Every war we win makes it a little more true.", "The leaderboard is just the motto with numbers.",
    "It hits harder than a Kodachi to the face.", "It's printed on every hosp bill we send out.",
    f"{FACTION_TAGLINE} Bring snacks, it's a long climb.", "We don't kneel, we climb.",
    "Your mum chanted it at the last chain, true story.", "Enemies hear it right before the hospital music.",
    "It's not just words, it's a hit list with manners.",
]
MOTTO_CLOSE = [
    "Now go make the enemy feel it 💥", "Scream it before the next chain 📣", "Tattoo it, chant it, live it 🤘",
    "Write it on your knuckles and get to work 👊", "Print it, frame it, hit someone with the frame 🖼️",
    "If you're not shouting it, you're doing it wrong 😤", "Get out there and earn it 💪",
    "Up you go, Punk 🚀", "Pull your weight and pull your trigger 🔫", "See you at the top 🏔️",
    "Rival factions hate this one simple trick 😈", "Say it louder for the people in hosp 🔥",
    "Now hit something 🎯", "Climb, punk, climb 🧗", "Go be a problem for someone 😏",
    "Make it true again today ⚔️",
]


# "Are we Punks that Ascended, or did we ascend to punkdom?" (acid_leaks,
# launch day 26/11/2026; the AI answered "What's up?"). No official story
# exists (Pat), so the bot says so and gives its own take, mixed and matched.
NAME_RX = re.compile(
    r"\bascend\w*\b.{0,60}\bpunk|\bpunk\w*\b.{0,60}\bascend|"
    r"\bwhy (?:are we|is it|we'?re|r we) (?:called|named)\b|"
    r"\bwhat does (?:the |our |this )?(?:faction(?:'s)? )?name mean\b|"
    r"\b(?:meaning|origin|story) (?:of|behind) (?:the |our |this )?(?:faction(?:'s)? )?name\b|"
    r"\bwhere (?:does|did) (?:the |our )?(?:faction(?:'s)? )?name come from\b", re.I)
NAME_OPEN = [
    "No official story on file, so here's mine 📜", "Nobody wrote it down, so I'll make it legend 🧙",
    "Great question, deep lore incoming 🔥", "Officially? Unknown. Unofficially? 😏",
    "The archives are blank, so trust me on this 🗄️", "Gather round, punks 🪑",
]
NAME_MIDDLE = [
    "Both. We were born punks, and every war win is another floor up.",
    "We started in the gutter and ascended one hospital stay at a time.",
    "Ascended doesn't mean we got polite. It means we climbed higher and kept the attitude.",
    "The punk part is the attitude, the ascended part is the climb.",
    "Punks who ascend still punch like punks. That's the whole point.",
    "Some say we ascended to a higher plane. I say we ascended to a higher hosp count.",
    "We didn't ascend out of punkdom, we dragged punkdom up the ladder with us.",
    "Normal people ascend to heaven. We ascended to the top of the hit list.",
]
NAME_CLOSE = [
    f"{FACTION_TAGLINE} 🧗", f"**{FACTION_MOTTO}** 🔥", "Nobody gets carried up there 💪",
    "Now go earn the name 🤘", "Still climbing, by the way 🚀", "And we're not done ascending 😈",
]


def name_reply(rng=None):
    import random
    rng = rng or random
    return f'{rng.choice(NAME_OPEN)} {rng.choice(NAME_MIDDLE)} {rng.choice(NAME_CLOSE)}'


def motto_hype(rng=None):
    """'Fuck yes 😤 We climb, they drop. Now hit something 🎯': one line
    from each pool, so it is never the same few lines on rotation."""
    import random
    rng = rng or random
    return f'{rng.choice(MOTTO_OPEN)} {rng.choice(MOTTO_MIDDLE)} {rng.choice(MOTTO_CLOSE)}'


GOALS_RX = re.compile(
    r"\b(?:what(?:'s| is| are|re)|whats) (?:our|the) (?:faction(?:'s)? )?(?:goals?|aims?|mission|purpose|plans?)\b|"
    r"\bwhat(?:'s| is|s) (?:the |this |our )?faction (?:about|for)\b|\bfaction(?:'s)? (?:goals?|aims?|mission)\b|"
    r"\bwhat (?:are|r) we (?:aiming|going) for\b", re.I)
JOIN_RX = re.compile(
    r"\bhow (?:can|do|does|could|would|should) (?:my (?:friend|mate|buddy|bro|irl friend)s?|someone|somebody|"
    r"people|others|outsiders|they|a (?:friend|mate)|friends|new people|new players) (?:join|apply|get in)\b|"
    r"\bhow (?:to|do you|do i|can i) (?:join|apply (?:to|for)|get into) (?:the |our |this )?"
    r"(?:faction|punks|ascended punks)\b|\bcan (?:my (?:friend|mate)|someone|people) (?:join|apply)\b|"
    r"\bhow (?:do|does) (?:joining|recruiting|recruitment) work\b(?! here)|\bwho do i (?:message|talk to|ask) to join\b",
    re.I)
HISTORY_RX = re.compile(
    r"\bhow did (?:the |our |this )?(?:faction|punks) (?:start|begin|get started|come about)\b|"
    r"\b(?:faction|punks)(?:'s)? (?:history|story|origins?|backstory)\b|"
    r"\bwho (?:started|founded|made|created) (?:the |our |this )?(?:faction|punks)\b|"
    r"\b(?:history|story|origin) of (?:the |our |this )?(?:faction|punks)\b", re.I)
MOTTO_RX = re.compile(r"\b(?:what(?:'s| is)|whats) (?:our|the) (?:faction(?:'s)? )?(?:motto|slogan|catchphrase)\b|"
                      r"\bdo we (?:have|got) a (?:motto|slogan|catchphrase)\b", re.I)
MAKE_RX = re.compile(r"\b(?:write|make|come up with|create|invent|give us|think of|suggest)\b", re.I)


# "write me a poem about the faction" is a creative ask, not a fact lookup
# (live audit 25/11/2026: it got the faction card).
CREATIVE_RX = re.compile(r"\b(?:poem|song|rap|story|haiku|limerick|joke|write|draft|announce\w*|slogan|motto|"
                         r"chant|speech|ad|advert\w*|recruit(?:ment|ing)? (?:post|ad|message))\b", re.I)


def faction_question(question):
    q = question or ''
    # "what's our motto?" is a question; "write us a motto" is creative.
    if (MOTTO_RX.search(q) or NAME_RX.search(q)) and not MAKE_RX.search(q):
        return True
    if CREATIVE_RX.search(q):
        return False
    return bool(FACTION_RX.search(q) or GOALS_RX.search(q) or JOIN_RX.search(q) or HISTORY_RX.search(q))


def management_names(guild):
    """Display names of the people holding a management role, bots left out."""
    from utils.helpers import member_is_management
    names = []
    for m in list(getattr(guild, 'members', None) or []):
        if getattr(m, 'bot', False):
            continue
        try:
            if member_is_management(m):
                names.append(getattr(m, 'display_name', None) or m.name)
        except Exception:
            continue
    return sorted(set(names), key=str.lower)


def faction_card(data, question='', guild=None, now=None):
    """The faction facts Torn gave, worded by the bot. data=None: Torn down."""
    if not data:
        return "Torn isn't answering right now, so I can't pull the faction profile 🤷 Try again in a minute."
    now = now or datetime.now(timezone.utc)
    q = (question or '').lower()
    members = data.get('members') or {}
    name = data.get('name') or 'Ascended Punks'
    tag = data.get('tag')

    def who(pid):
        if not pid:
            return None
        m = members.get(str(pid)) or {}
        return m.get('name') or f'[{pid}]'

    leader, co = who(data.get('leader')), who(data.get('co-leader'))
    lines = []
    lead_line = None
    if leader:
        lead_line = f'👑 Leader: **{leader}**' + (f' · Co-leader: **{co}**' if co else ' · no co-leader')
    lead_days = (members.get(str(data.get('leader'))) or {}).get('days_in_faction')
    goal_line = f'🎯 Goal: {FACTION_GOALS}'
    motto_line = f'🔥 Motto: **{FACTION_MOTTO}** {FACTION_TAGLINE}'
    join_line = f'🚪 Want in? {FACTION_JOIN}'

    mgmt = management_names(guild) if guild is not None else []
    mgmt_line = f'🛡️ Management on Discord: {", ".join(mgmt)}' if mgmt else None
    age = data.get('age')
    age_line = None
    if isinstance(age, int) and age > 0:
        founded = (now - timedelta(days=age)).strftime('%d %b %Y')
        age_line = f'🎂 Founded {founded} ({age:,} days ago)'
    rank = data.get('rank') or {}
    rank_line = None
    if rank.get('name'):
        div = RANK_DIVISIONS.get(rank.get('division'), '')
        wins = rank.get('wins')
        rank_line = f'🏅 Rank: **{rank["name"]}{" " + div if div else ""}**' + \
                    (f' · {wins:,} ranked war wins' if isinstance(wins, int) else '')
    respect = data.get('respect')
    respect_line = f'⭐ Respect: {respect:,}' if isinstance(respect, int) else None
    cap = data.get('capacity')
    size_line = None
    if members:
        size_line = f'👥 Members: {len(members)}' + (f' / {cap}' if isinstance(cap, int) and cap else '')
        if isinstance(cap, int) and cap > len(members):
            size_line += f' ({cap - len(members)} open)'
        elif isinstance(cap, int) and cap and cap <= len(members):
            size_line += ' (full)'
    chain = data.get('best_chain')
    chain_line = f'⛓️ Best chain: {chain:,}' if isinstance(chain, int) and chain else None
    war_line = last_war_line(data)

    def only(*picked):
        got = [p for p in picked if p]
        return '\n'.join(got) if got else None

    focused = None
    if HISTORY_RX.search(q):
        # No written history (Pat), so the bot says so and gives what Torn
        # knows: when it was founded and how long the leader has been here.
        bits = ["No official origin story on file, it's before my time 🤷"]
        if age_line:
            bits.append(age_line)
        if leader:
            longest = isinstance(lead_days, int) and lead_days >= max(
                (m.get('days_in_faction') or 0 for m in members.values() if isinstance(m.get('days_in_faction'), int)),
                default=0)
            bits.append(f'👑 **{leader}** leads us' + (
                f' and has been here {lead_days:,} days' + (', longer than anyone' if longest else '')
                if isinstance(lead_days, int) and lead_days > 0 else ''))
        focused = '\n'.join(bits)
    elif MOTTO_RX.search(q):
        focused = f'**{FACTION_MOTTO}**\n{motto_hype()}'
    elif GOALS_RX.search(q):
        focused = goal_line
    elif JOIN_RX.search(q):
        focused = only(join_line, mgmt_line, size_line)
    elif re.search(r'\bco-?leader\b', q):
        focused = (f'👑 No co-leader right now. **{leader}** runs the show.' if leader and not co
                   else only(lead_line, mgmt_line))
    elif re.search(r'\bleader|boss|in charge|running\b', q):
        focused = only(lead_line, mgmt_line)
    elif re.search(r'\bmanagement|mods|officers|admins|staff\b', q):
        focused = only(mgmt_line, lead_line)
    elif re.search(r'\bold\b|\bage\b|founded|created|started|made\b', q):
        focused = only(age_line)
    elif re.search(r'\brank\b|war record|wars? have we won', q):
        focused = only(rank_line, war_line)
    elif re.search(r'\brespect\b', q):
        focused = only(respect_line)
    elif re.search(r'\bbest chain\b', q):
        focused = only(chain_line)
    elif re.search(r'\btag\b', q):
        focused = f'🏷️ Our tag is **{tag}**' if tag else None
    elif re.search(r'recruit|full|spots|places|spaces|capacity', q):
        focused = only(size_line)
        rec = quote_sections(['Recruiters'], guild, limit=600) if focused else ''
        if rec:
            focused += '\n\n' + rec
    if focused:
        return focused
    header = f'**{name}**' + (f' [{tag}]' if tag else '') + ' 🤘'
    lines = [header, motto_line]
    lines = lines + [l for l in (lead_line, mgmt_line, age_line, rank_line, respect_line, size_line,
                                     chain_line, war_line, goal_line, join_line) if l]
    return '\n'.join(lines)


def last_war_line(data):
    """'⚔️ Last ranked war: beat Phantom Force 5,377 to 2,731', from the
    ranked_wars Torn lists. Only a finished war with a winner counts."""
    ours = str(data.get('ID') or '')
    best = None
    for wid, w in (data.get('ranked_wars') or {}).items():
        war = (w or {}).get('war') or {}
        facs = (w or {}).get('factions') or {}
        if not war.get('end') or not war.get('winner') or ours not in facs or len(facs) != 2:
            continue
        if best is None or war['end'] > best[0]:
            best = (war['end'], w)
    if best is None:
        return None
    w = best[1]
    them = next(v for k, v in w['factions'].items() if k != ours)
    us = w['factions'][ours]
    won = str(w['war']['winner']) == ours
    try:
        score = f" {int(us['score']):,} to {int(them['score']):,}"
    except (KeyError, TypeError, ValueError):
        score = ''
    return f'⚔️ Last ranked war: {"beat" if won else "lost to"} {them.get("name", "the enemy")}{score}'


async def faction_reply(question, guild=None, fetch=None):
    """Faction facts for a faction question, or None. fetch: test hook."""
    if not faction_question(question):
        return None
    if NAME_RX.search(question or '') and not MOTTO_RX.search(question or ''):
        return name_reply()   # no Torn data needed, and Torn being down shouldn't block it
    if fetch is None:
        from utils import torn_api
        fetch = torn_api.fetch_faction_data
    try:
        data = await fetch('basic')
    except Exception:
        data = None
    return faction_card(data, question, guild)
