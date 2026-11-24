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
    r"\bgetting started\b|\bfirst steps?\b|\bnew (?:member|player)s? guide\b|\bhow do i (?:join|start|begin)\b|"
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


# "write me a poem about the faction" is a creative ask, not a fact lookup
# (live audit 25/11/2026: it got the faction card).
CREATIVE_RX = re.compile(r"\b(?:poem|song|rap|story|haiku|limerick|joke|write|draft|announce\w*|slogan|motto|"
                         r"chant|speech|ad|advert\w*|recruit(?:ment|ing)? (?:post|ad|message))\b", re.I)


def faction_question(question):
    q = question or ''
    return bool(FACTION_RX.search(q)) and not CREATIVE_RX.search(q)


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
        lead_line = f'👑 Leader: **{leader}**' + (f' · Co-leader: **{co}**' if co else '')
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
    if re.search(r'\bleader|co-?leader|boss|in charge|running\b', q):
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
    lines = [header] + [l for l in (lead_line, mgmt_line, age_line, rank_line, respect_line, size_line,
                                     chain_line, war_line) if l]
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
    if fetch is None:
        from utils import torn_api
        fetch = torn_api.fetch_faction_data
    try:
        data = await fetch('basic')
    except Exception:
        data = None
    return faction_card(data, question, guild)
