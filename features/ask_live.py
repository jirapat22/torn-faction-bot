"""
More things @ApexPunk can look up (Pat, 07/11/2026: "do all that").

features/ask_ai.py's FactBook answers from the bot's saved records. This adds
what the bot knows but the chat couldn't see:

  live_status   - the war, chain and chain event happening RIGHT NOW, and who
                  is flying or abroad (the same data as /war, /chain, flystatus)
  members_extra - leave notices (word for word), timezones, new-member
                  trials, the war availability poll
  faction_extra - war win/loss trends, the target board, the enemy
                  watchlist, an enemy faction's overview

Two design choices:

1. Reuse the slash commands' own embeds where they exist (format_war_embed,
   status_embed, progress_embed...) and turn them into plain text. The chat
   then says exactly what the command would, instead of a second copy of the
   logic that could drift from it. The number check grounds the AI's answer
   against this text like any other tool result.

2. Pat's rule: money, items, Xanax (use AND payouts) and battle stats stay out
   of chat answers. Any line in a tool result that mentions them is dropped
   here, before the AI ever sees it, so it can't repeat what it never got.
"""

import logging
import os
import re
import time
from datetime import datetime, timezone

from utils.helpers import load_json

logger = logging.getLogger(__name__)

# Lines that must never reach the AI (Pat's rule, 07/11/2026). "bs" is how
# Torn players write battle stats; FFScouter is where the estimates come from.
FORBIDDEN_LINE = re.compile(r'\bxan(ax)?s?\b|battle ?stats?|\bbs\b|ffscouter|\best\.?\s*stats?\b|'
                            r'\bnet ?worth\b|\bmoney\b|\bcash\b|'
                            # payouts are "check with management" (Pat, 08/11/2026)
                            r'\bpay ?-?outs?\b|reimburs\w*|\bvault\b|withdraw\w*', re.I)

NOTICE_DAYS = 60        # same window the strike alerts use (features/notices.py)
NOTICES_READ = 200      # messages read from #leave-notices per question
NOTICES_PER_PERSON = 3  # their latest few, word for word


# ─────────────────────────────────────────────────────────────────────────────
# Embeds -> plain text the AI can read
# ─────────────────────────────────────────────────────────────────────────────

def _tct(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime('%d/%m/%Y %H:%M TCT')


def _span(seconds):
    """3h 20m / 2d 4h / 15m. Whole units only, worked out here, not by the AI."""
    s = int(abs(seconds))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    if d:
        return f'{d}d {h}h'
    if h:
        return f'{h}h {m}m'
    return f'{m}m'


def _relative(ts, now):
    delta = int(ts) - int(now)
    return f'in {_span(delta)}' if delta >= 0 else f'{_span(delta)} ago'


def plain(text, now=None, names=None):
    """
    Discord markup -> words. <t:X:R> becomes "in 3h 20m" (worked out here, so
    the AI copies a countdown instead of computing one); other <t:X> become a
    TCT date. Mentions become the person's name when known. Forbidden lines
    (money, Xanax, battle stats) are dropped.
    """
    if not text:
        return ''
    now = now or time.time()
    names = names or {}

    def stamp(m):
        x, style = int(m.group(1)), (m.group(2) or 'f')
        return _relative(x, now) if style == 'R' else _tct(x)

    text = re.sub(r'<t:(\d+)(?::([a-zA-Z]))?>', stamp, text)
    text = re.sub(r'<@!?(\d+)>', lambda m: names.get(m.group(1), 'a member'), text)
    text = re.sub(r'<@&\d+>', 'a role', text)
    text = re.sub(r'<#\d+>', 'a channel', text)
    text = re.sub(r'<a?:(\w+):\d+>', '', text)                       # custom emoji
    text = re.sub(r'\[([^\]]+)\]\((?:https?://)[^)]+\)', r'\1', text)  # [label](link) -> label
    text = text.replace('```', '').replace('-# ', '')
    kept = [line for line in text.split('\n') if not FORBIDDEN_LINE.search(line)]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()


def embed_text(embed, now=None, names=None):
    """One embed (or a list of them) as plain text, title first."""
    if embed is None:
        return ''
    if isinstance(embed, (list, tuple)):
        return '\n\n'.join(embed_text(e, now, names) for e in embed)
    parts = [embed.title or '', embed.description or '']
    for f in embed.fields:
        parts.append(f'{f.name}:\n{f.value}')
    footer = getattr(embed.footer, 'text', None)
    if footer:
        parts.append(footer)
    return plain('\n'.join(p for p in parts if p), now, names)


# ─────────────────────────────────────────────────────────────────────────────
# Leave notices (pure helpers, tested without Discord)
# ─────────────────────────────────────────────────────────────────────────────

def group_notices(messages, now=None, days=NOTICE_DAYS, per_person=NOTICES_PER_PERSON):
    """
    {author_id: [{'ts', 'text'}, ...newest first]} from raw channel messages.
    Unlike features/notices.summarise (first line, 90 characters, for alert
    footers), this keeps the FULL text: Pat wants notices quoted word for
    word, and he reads the dates himself (no date parsing, on purpose).
    """
    now = now or time.time()
    out = {}
    for m in sorted(messages or [], key=lambda x: -(x.get('timestamp') or 0)):
        text = (m.get('content') or '').strip()
        ts = m.get('timestamp') or 0
        if not text or ts < now - days * 86400:
            continue
        rows = out.setdefault(str(m.get('author_id')), [])
        if len(rows) < per_person:
            rows.append({'ts': ts, 'text': text})
    return out


def notice_lines(grouped, label_for, only=None):
    """
    Lines like 'NOTICE by X, posted 03/11/2026 14:02 TCT: "away till Friday"'.
    `label_for(discord_id)` names the poster; `only` limits it to some ids.
    """
    lines = []
    for did, rows in grouped.items():
        if only is not None and did not in only:
            continue
        for r in rows:
            quoted = r['text'].replace('\n', ' / ')
            lines.append(f'NOTICE by {label_for(did)}, posted {_tct(r["ts"])}: "{quoted}"')
    return lines


def notices_answer(tool_text):
    """
    The reply to a leave-notice question, written by the bot (no AI words):
    each notice as a quote, everything else (e.g. "no notice from X") as is.
    """
    out = []
    for line in tool_text.split('\n'):
        m = re.match(r'NOTICE by (.+?), posted (.+? TCT): "(.*)"$', line)
        if m:
            out.append(f'📝 **{m.group(1)}**, posted {m.group(2)}:\n> {m.group(3)}')
        elif line.startswith('LEAVE NOTICES from'):
            continue
        elif line.strip():
            out.append(line.replace('NO DATA: ', '').split(' (The bot tells')[0])
    return '\n'.join(out).strip() or 'No leave notices found.'


# ─────────────────────────────────────────────────────────────────────────────
# The tools
# ─────────────────────────────────────────────────────────────────────────────

class LiveTools:
    """
    Built per question next to the FactBook. `live` holds the bot's running
    feature managers (war_tracker, chain_tracker, ...); any of them can be
    None, and then that tool says it isn't available instead of failing.
    """

    def __init__(self, facts, live=None, guild=None, data_dir='data'):
        self.facts = facts
        self.live = live
        self.guild = guild
        self.data_dir = data_dir
        self.now = facts.now

    def _m(self, name):
        return getattr(self.live, name, None) if self.live is not None else None

    def _names(self):
        """Discord user ID -> name. Linked members get their Torn name (what
        people ask about); others their Discord display name."""
        names = {str(m.id): m.display_name for m in getattr(self.guild, 'members', None) or []}
        torn = self.facts._known_names()
        for did, tid in (getattr(self.facts, 'discord_links', None) or {}).items():
            if str(tid) in torn:
                names[str(did)] = torn[str(tid)]
        return names

    def _found(self, text):
        self.facts.found_any = True
        return text

    # ---------- live_status ----------

    async def live_status(self, what):
        what = (what or '').lower()
        if what == 'war':
            return await self._war()
        if what == 'chain':
            return await self._chain()
        if what == 'chain_event':
            return await self._chain_event()
        if what == 'flights':
            return await self._flights()
        return 'Pick one of: war, chain, chain_event, flights.'

    async def _war(self):
        wt = self._m('war_tracker')
        if wt is None:
            return self.facts._notice('War tracking is not running on the bot right now.')
        info = await wt.get_current_war()
        out = []
        if info:
            out.append('WAR RIGHT NOW (same as /war):\n' + embed_text(await wt.format_war_embed(info), self.now,
                                                                      self._names()))
        upcoming = await wt.get_upcoming_war()
        if upcoming:
            out.append(f'Next ranked war: war {upcoming["war_id"]}, starts {_tct(upcoming["start"])} '
                       f'({_relative(upcoming["start"], self.now)}).')
        if not out:
            out.append('No ranked war is on right now and none is enlisted yet (or Torn did not answer).')
        return self._found('\n\n'.join(out))

    async def _chain(self):
        ct = self._m('chain_tracker')
        if ct is None:
            return self.facts._notice('Chain tracking is not running on the bot right now.')
        # Not ct.get_current_chain(): it returns None for BOTH "Torn didn't
        # answer" and "no chain running" (the None-vs-empty trap), and those
        # need different answers. Same call it makes, read here.
        from utils.torn_api import fetch_faction_data
        raw = await fetch_faction_data('chain')
        if not raw:
            return self.facts._notice("Torn isn't answering, so I can't see the chain right now.")
        chain = raw.get('chain') or {}
        if not chain.get('current'):
            cooldown = chain.get('cooldown') or 0
            return self._found('No chain is running right now.'
                               + (f' Chain cooldown ends {_relative(self.now + cooldown, self.now)}.'
                                  if cooldown else ''))
        return self._found('CHAIN RIGHT NOW (same as /chain):\n'
                           + embed_text(await ct.format_chain_embed(chain), self.now, self._names()))

    async def _chain_event(self):
        m = self._m('chain_event_manager')
        if m is None:
            return self.facts._notice('Chain events are not set up on the bot.')
        ev = m.current()
        if not ev:
            return self._found('No chain event has been run yet.')
        from features.chain_events import progress_embed
        totals, ok = await m.tally(ev, full=False)
        roster = await m.roster_for(ev)
        text = embed_text(progress_embed(ev, totals, roster, ok), self.now, self._names())
        status = {'running': 'running now', 'scheduled': 'scheduled, not started yet',
                  'ended': 'finished', 'cancelled': 'cancelled'}.get(ev.get('status'), ev.get('status'))
        head = (f'CHAIN EVENT "{ev.get("name")}" ({status}): {_tct(ev["start"])} to {_tct(ev["end"])}, '
                f'minimum {ev.get("minimum")} hits each.' + ('' if ok else ' Counts could NOT be fully read, '
                                                              'so totals may be short.'))
        return self._found(head + '\n' + text)

    async def _flights(self):
        ft = self._m('fly_tracker')
        if ft is None:
            return self.facts._notice('Flight tracking is not running on the bot right now.')
        return self._found('FLIGHTS RIGHT NOW (same as /war view:FlyStatus):\n'
                           + embed_text(await ft.status_embed(), self.now, self._names()))

    # ---------- members_extra ----------

    async def members_extra(self, what, name=''):
        what = (what or '').lower()
        if what == 'leave_notices':
            return await self._notices(name)
        if what == 'timezones':
            return self._timezones(name)
        if what == 'trials':
            return self._trials()
        if what == 'availability':
            return self._availability(name)
        return 'Pick one of: leave_notices, timezones, trials, availability.'

    def _who(self, name):
        """(torn_id, name) or (None, notice text already recorded)."""
        tid, real, _ = self.facts.resolve(name)
        if tid is None:
            return None, self.facts.member_report(name)   # records the "did you mean" notice
        return tid, real

    def _discord_ids_for(self, tid):
        return {d for d, t in self.facts.discord_links.items() if str(t) == str(tid)}

    def _label(self, did):
        tid = self.facts.discord_links.get(str(did))
        name = self._names().get(str(did))
        if tid:
            return f'{self.facts._name(tid)} [{tid}]'
        return name or 'someone not linked to the bot'

    async def _notices(self, name):
        from config import LEAVE_NOTICES_CHANNEL_ID
        bot = self._m('bot')
        channel = bot.get_channel(LEAVE_NOTICES_CHANNEL_ID) if bot and LEAVE_NOTICES_CHANNEL_ID else None
        if channel is None:
            return self.facts._notice("I can't see #leave-notices right now.")
        rows = []
        try:
            async for msg in channel.history(limit=NOTICES_READ):
                if msg.author and getattr(msg.author, 'bot', False):
                    continue
                rows.append({'author_id': str(msg.author.id), 'timestamp': msg.created_at.timestamp(),
                             'content': msg.content or ''})
        except Exception as e:   # None-vs-empty: unreadable is not "nobody posted"
            logger.warning(f'ask_live: could not read #leave-notices: {e}')
            return self.facts._notice("I couldn't read #leave-notices just now, so I can't say who posted.")
        grouped = group_notices(rows, now=self.now)
        only = None
        who = 'anyone'
        if (name or '').strip():
            tid, real = self._who(name)
            if tid is None:
                return real
            only = self._discord_ids_for(tid)
            who = real
            if not only:
                return self._found(f'{real} is not linked to a Discord account in the bot, so I can\'t tell '
                                   'which notices are theirs.')
        lines = notice_lines(grouped, self._label, only)
        if not lines:
            return self._found(f'No leave notice from {who} in #leave-notices in the last {NOTICE_DAYS} days.')
        return self._found(f'LEAVE NOTICES from {who}, last {NOTICE_DAYS} days, newest first, word for word:\n'
                           + '\n'.join(lines))

    def _timezones(self, name):
        from features.timezones import local_now, friendly_time
        store = load_json(os.path.join(self.data_dir, 'timezones.json'), default={}) or {}
        if not store:
            return self._found('Nobody has picked a timezone yet.')
        now = datetime.fromtimestamp(self.now, timezone.utc)
        only = None
        if (name or '').strip():
            tid, real = self._who(name)
            if tid is None:
                return real
            only = self._discord_ids_for(tid)
        lines = []
        for did, rec in store.items():
            if only is not None and did not in only:
                continue
            local = local_now(rec.get('zone', ''), now)
            when = f'{friendly_time(local)} {local:%A}' if local else 'unknown'
            lines.append(f'- {self._label(did)}: {rec.get("zone")}, local time now {when}')
        if not lines:
            return self._found('They have not picked a timezone on the timezone board.')
        return self._found(f'TIMEZONES (from the timezone board), TCT now {now:%H:%M}:\n' + '\n'.join(sorted(lines)))

    def _trials(self):
        from config import TRIAL_DAYS, TRIAL_TARGET_LEVEL
        roster = self.facts.roster
        if roster is None:
            return self.facts._notice("Torn isn't answering, so I can't see who is new right now.")
        rows = []
        for tid, m in roster.items():
            days = m.get('days_in_faction')
            if days is None or days > TRIAL_DAYS:
                continue
            level = m.get('level') or 0
            rows.append((days, f'- {self.facts._name(tid, m)} [{tid}]: level {level}, {days} days in the faction, '
                               f'{max(0, TRIAL_DAYS - days)} days of the trial left, '
                               + (f'already level {TRIAL_TARGET_LEVEL}+' if level >= TRIAL_TARGET_LEVEL
                                  else f'NOT yet level {TRIAL_TARGET_LEVEL}')))
        if not rows:
            return self._found(f'Nobody is on their {TRIAL_DAYS}-day trial right now.')
        rows.sort()
        return self._found(f'NEW MEMBERS ON TRIAL (reach level {TRIAL_TARGET_LEVEL} within {TRIAL_DAYS} days):\n'
                           + '\n'.join(r for _, r in rows))

    def _availability(self, date):
        m = self._m('availability_manager')
        if m is None:
            return self.facts._notice('Availability polls are not set up on the bot.')
        if not m.polls:
            return self._found('There are no availability polls on record.')
        pid = None
        d = re.sub(r'[/.]', '-', (date or '').strip())
        if d:
            pid = m.find_poll_by_date(d)
            if pid is None:
                return self._found(f'No availability poll covers {date}.')
        else:   # the newest open poll, else the newest one
            open_ = [p for p, v in m.polls.items() if not v.get('closed')]
            pick = open_ or list(m.polls)
            pid = max(pick, key=lambda p: (m.polls[p].get('blocks') or [{}])[0].get('start_unix', 0))
        poll, summary = m.polls[pid], m.get_summary(pid)
        names = self._names()
        data = m.data.get(pid, {}) or {}
        lines = [f'AVAILABILITY POLL {summary["start_date"]} to {summary["end_date"]}'
                 + (f' (war {poll["war_id"]})' if poll.get('war_id') else '')
                 + (', closed' if poll.get('closed') else
                    (f', closes {_relative(poll["poll_end_unix"], self.now)}' if poll.get('poll_end_unix') else ''))]
        for b in summary['blocks']:
            who = [names.get(str(u), 'a member') for u in data.get(str(b['id']), [])]
            lines.append(f'- {b["date"]} {b["start_time"]}-{b["end_time"]} TCT: {b["count"]} available'
                         + (f' ({", ".join(sorted(who))})' if who else ''))
        best = [b for b in summary['best_windows'] if b['count']]
        if best:
            lines.append('Best blocks (most people): ' + '; '.join(
                f'{b["date"]} {b["start_time"]}-{b["end_time"]} TCT with {b["count"]}' for b in best))
        missing = m.non_voters(pid, self.guild)
        lines.append(f'Did not answer ({len(missing)}): ' + (', '.join(missing) if missing else 'nobody'))
        return self._found('\n'.join(lines))

    # ---------- faction_extra ----------

    async def faction_extra(self, what, query=''):
        what = (what or '').lower()
        if what == 'war_trends':
            wt = self._m('war_tracker')
            if wt is None:
                return self.facts._notice('War tracking is not running on the bot right now.')
            return self._found('WAR TRENDS (same as /war view:Trends):\n'
                               + embed_text(await wt.format_war_trends_embed(), self.now))
        if what == 'targets':
            return self._targets()
        if what == 'enemy_watchlist':
            ew = self._m('enemy_watchlist')
            if ew is None:
                return self.facts._notice('The enemy watchlist is not running on the bot.')
            return self._found('ENEMY WATCHLIST:\n' + embed_text(await ew.format_watchlist_embed(), self.now))
        if what == 'enemy_faction':
            return await self._enemy_faction(query)
        return 'Pick one of: war_trends, targets, enemy_watchlist, enemy_faction.'

    def _targets(self):
        from features.targets import TARGET_DAYS
        tm = self._m('target_manager')
        targets = (tm.data.get('targets') if tm is not None else None)
        if targets is None:
            targets = (load_json(os.path.join(self.data_dir, 'targets.json'), default={}) or {}).get('targets', {})
        if not targets:
            return self._found('The target board is empty.')
        lines = []
        for tid, t in targets.items():
            until = ('permanent, until removed' if t.get('permanent') else
                     f'until {_tct(int(t.get("added_at", 0)) + TARGET_DAYS * 86400)}')
            lines.append(f'- {t.get("name", tid)} [{tid}] level {t.get("level", "unknown")}, added by '
                         f'{t.get("added_by_name", "someone")}, {until}'
                         + (f', reason: "{t["reason"]}"' if t.get('reason') else ''))
        return self._found('TARGET BOARD:\n' + '\n'.join(lines))

    async def _enemy_faction(self, query):
        """Overview of another faction: by ID, or the current/next war's opponent. No battle stats (Pat's rule)."""
        from config import TORN_FACTION_ID
        from utils.torn_api import fetch_any_faction_data
        from features.faction import _parse_rankedwars
        q = (query or '').strip()
        fid = None
        m = re.search(r'(?:ID=)?(\d{2,})', q)
        if m:
            fid = m.group(1)
        else:
            wt = self._m('war_tracker')
            war = None
            if wt is not None:
                war = await wt.get_current_war() or await wt.get_upcoming_war() or await wt.get_latest_war()
            generic = {'the', 'enemy', 'enemies', 'enemys', 'faction', 'them', 'their', 'they', 'opponent',
                       'opponents', 'current', 'war', 'wars', 'our', 'last', 'latest', 'next', 'upcoming',
                       'this', 'that', 'other', 'we', 'fought', 'fighting', 'vs', 'against', 'side', 'team'}
            name_part = ' '.join(w for w in re.findall(r"[\w'-]+", q.lower()) if w not in generic)
            for f_id, f in ((war or {}).get('data', {}).get('factions') or {}).items():
                if str(f_id) != str(TORN_FACTION_ID) and (not name_part
                                                          or name_part in str(f.get('name', '')).lower()):
                    fid = str(f_id)
            if fid is None and wt is not None and not name_part:
                # Torn's live list drops wars a month after they end, so with
                # no war on, fall back to the newest war the bot archived.
                past = [w for w in (getattr(wt, 'war_history', None) or {}).values() if w.get('enemy_faction_id')]
                if past:
                    fid = str(max(past, key=lambda w: w.get('start') or 0)['enemy_faction_id'])
            if fid is None:
                return self.facts._notice('Which faction? Give me its Torn faction ID (it\'s in the faction\'s '
                                          'profile link). I can only find the current war\'s enemy by name.')
        basic = await fetch_any_faction_data(fid, 'basic')
        if not basic:
            return self.facts._notice(f"I couldn't get faction {fid} from Torn. Check the ID.")
        wars = await fetch_any_faction_data(fid, 'rankedwars')
        record = _parse_rankedwars((wars or {}).get('rankedwars', {}), int(fid)) if wars else None
        rank = basic.get('rank') or {}
        rank = rank.get('name', 'unknown') if isinstance(rank, dict) else str(rank)
        members = basic.get('members') or {}
        lines = [f'FACTION {basic.get("name")} [{fid}]' + (f' tag {basic["tag"]}' if basic.get('tag') else ''),
                 f'- members: {len(members)}', f'- respect: {basic.get("respect", "unknown")}',
                 f'- best chain: {basic.get("best_chain", "unknown")}', f'- age: {basic.get("age", "unknown")} days',
                 f'- rank: {rank}',
                 '- last ranked wars, newest first: ' + (' '.join(record) if record else
                                                         ('none on record' if record == [] else 'unknown'))]
        if members:
            levels = sorted((m.get('level') or 0) for m in members.values())
            lines.append(f'- member levels: lowest {levels[0]}, highest {levels[-1]}, '
                         f'middle {levels[len(levels) // 2]}')
        return self._found('\n'.join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions shown to the AI
# ─────────────────────────────────────────────────────────────────────────────

LIVE_SPECS = [
    {'type': 'function', 'function': {
        'name': 'live_status',
        'description': 'What is happening RIGHT NOW: the current or next ranked war (score, time left), the '
                       'current chain (count, timeout), the chain event (each member\'s hits, who is short), or '
                       'flights (enemies and our members flying or abroad).',
        'parameters': {'type': 'object', 'properties': {
            'what': {'type': 'string', 'enum': ['war', 'chain', 'chain_event', 'flights']}},
            'required': ['what']}}},
    {'type': 'function', 'function': {
        'name': 'members_extra',
        'description': 'leave_notices: what members posted in #leave-notices (word for word; give name for one '
                       'member). timezones: where members are and their local time. trials: new members on their '
                       'trial (level 15 in 2 weeks). availability: the war availability poll (who is free when, '
                       'who did not answer; name = a date DD-MM-YYYY, or "" for the latest poll).',
        'parameters': {'type': 'object', 'properties': {
            'what': {'type': 'string', 'enum': ['leave_notices', 'timezones', 'trials', 'availability']},
            'name': {'type': 'string', 'description': 'Member name, or a date for availability, or ""'}},
            'required': ['what']}}},
    {'type': 'function', 'function': {
        'name': 'faction_extra',
        'description': 'war_trends: our win/loss record and top contributors across wars. targets: the target '
                       'board. enemy_watchlist: enemies being watched. enemy_faction: overview of another faction '
                       '(query = its faction ID, or "" for the current war\'s enemy).',
        'parameters': {'type': 'object', 'properties': {
            'what': {'type': 'string', 'enum': ['war_trends', 'targets', 'enemy_watchlist', 'enemy_faction']},
            'query': {'type': 'string'}},
            'required': ['what']}}},
]

LIVE_TOOL_NAMES = {s['function']['name'] for s in LIVE_SPECS}
