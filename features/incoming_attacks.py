"""
Incoming attacks log: every hit ON one of our members, kept 90 days, so
"@ApexPunk who hit us?", "did stepbro hit us?" and "who hit Ikaros22?" can be
answered from the bot's own records.

Why a log of our own: Torn's faction attacks log only answers "the oldest 100
after a time", so a question about last month means paging through thousands
of attacks live, which is slow and burned the rate limit once already (the
"stepbro" search on 22/11/2026 hit Torn error 5). Saving them as they happen
costs one or two calls every 10 minutes instead.

One cursor does both jobs. It starts 30 days back (the one-time backfill) and
walks forward a page at a time, throttled, until it reaches now; after that
each tick just reads what is new. A failed page stops the tick WITHOUT moving
the cursor, so an outage is retried, never skipped (the None-vs-empty rule:
an unreadable page is not "nobody hit us").

Read side (pure functions below the class): counts and lists are worked out
in Python and the bot posts them itself, no AI wording, the same as notices.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

from utils.helpers import load_json, save_json, plural

logger = logging.getLogger(__name__)

DATA_FILE = 'data/incoming_attacks.json'
KEEP_DAYS = 90          # retention: older records are dropped on every save
BACKFILL_DAYS = 45      # how far back the first run starts (covers the 10/10/2026 "stepbro" hit)
PAGES_PER_TICK = 20     # cap per 10-minute tick once caught up
# While more than a day behind (the first backfill), a tick may read more:
# war days take ~14 pages a day, so 20 a tick needed ~3 hours for 30 days.
# 60 pages 3 s apart is ~20 calls a minute for 3 minutes, every 10 minutes.
BACKFILL_PAGES_PER_TICK = 60
PAGE_SLEEP_S = 3        # between pages: paging fast is what hit error 5
WINDOW_S = 86400        # each read asks for at most this span (see tick)
DAY = 86400
# Results that mean the defender actually lost something. "Lost"/"Stalemate"
# etc. mean they held, which is still a hit on us, just a failed one.
FAILED = {'lost', 'stalemate', 'escape', 'timeout', 'interrupted', 'assist'}


def _ts_text(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%d/%m/%Y %H:%M TCT')


def _date_text(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%d/%m/%Y')


def record_from(attack_id, a):
    """The fields we keep from one Torn v1 attack. A stealthed attack has a
    blank attacker: it is kept as 'Someone (stealthed)', never dropped, so the
    count of hits on us stays right."""
    name = str(a.get('attacker_name') or '').strip()
    stealthed = bool(a.get('stealthed')) or not name
    return {
        'id': str(attack_id),
        'ts': int(a.get('timestamp_started') or a.get('timestamp_ended') or 0),
        'attacker_id': None if stealthed else str(a.get('attacker_id') or '') or None,
        'attacker_name': 'Someone (stealthed)' if stealthed else name,
        'attacker_faction': str(a.get('attacker_faction') or '') or None,
        'attacker_faction_name': str(a.get('attacker_factionname') or '').strip() or None,
        'defender_id': str(a.get('defender_id') or ''),
        'defender_name': str(a.get('defender_name') or '').strip(),
        'result': str(a.get('result') or 'unknown'),
        'respect': a.get('respect_gain', a.get('respect')),
    }


class IncomingAttacks:
    def __init__(self, path=DATA_FILE, faction_id=None, fetch=None, now=time.time, sleep=asyncio.sleep):
        from config import TORN_FACTION_ID
        self.path = path
        self.faction_id = str(faction_id or TORN_FACTION_ID)
        self.fetch = fetch          # tests pass a fake; None = utils.torn_api
        self.now = now
        self.sleep = sleep
        self.data = load_json(path, default={}) or {}
        self.data.setdefault('attacks', {})
        # cursor: the timestamp the next page reads from (inclusive).
        # covered_from: the oldest time the log is complete from.
        self.data.setdefault('cursor', None)
        self.data.setdefault('covered_from', None)
        self.data.setdefault('last_ok', None)

    def save(self):
        cutoff = self.now() - KEEP_DAYS * DAY
        self.data['attacks'] = {k: v for k, v in self.data['attacks'].items() if v.get('ts', 0) >= cutoff}
        if self.data.get('covered_from') and self.data['covered_from'] < cutoff:
            self.data['covered_from'] = int(cutoff)
        save_json(self.path, self.data)

    async def _page(self, frm, to):
        if self.fetch is not None:
            return await self.fetch(frm, to)
        from utils import torn_api
        return await torn_api.fetch_faction_attacks(from_ts=frm, to_ts=to, sort='asc')

    async def tick(self, max_pages=None):
        """Read forward, at most `max_pages` pages. Returns how many new hits
        on us were saved, or None if Torn failed on the very first page.

        Two stretches can be pending. FORWARD: from `cursor` up to now. BACK:
        if BACKFILL_DAYS was raised after the log started (30 -> 45 on
        22/11/2026, so the 10/10 "stepbro" hit is in), the older days from
        `back_cursor` up to `covered_from` are read first, the same way."""
        now = int(self.now())
        start = now - BACKFILL_DAYS * DAY
        if self.data['cursor'] is None:
            self.data['cursor'] = start
            self.data['covered_from'] = start
        elif (self.data.get('covered_from') or start) > start + 3600 and not self.data.get('back_cursor'):
            self.data['back_cursor'] = self.data['back_start'] = start
            self.data['back_end'] = self.data['covered_from']
        if max_pages is None:
            behind = now - int(self.data['cursor'])
            max_pages = BACKFILL_PAGES_PER_TICK if behind > DAY or self.data.get('back_cursor') \
                else PAGES_PER_TICK
        added, pages = 0, 0
        while pages < max_pages:
            back = bool(self.data.get('back_cursor'))
            key, end = ('back_cursor', int(self.data['back_end'])) if back else ('cursor', now)
            if pages:
                await self.sleep(PAGE_SLEEP_S)
            frm = int(self.data[key])
            # A bounded window (at most a day): a short page then PROVES the
            # whole window was read. Unbounded, one short page mid-backfill
            # (live 22/11/2026: page 12 of the first run) read as "caught up"
            # 27 days early.
            to = min(frm + WINDOW_S, end)
            page = await self._page(frm, to)
            pages += 1
            if page is None:
                # Torn failed: keep the cursor where it is, so this stretch
                # is read again next tick instead of silently skipped.
                logger.warning(f'Incoming attacks: Torn failed reading from {frm}; will retry')
                self.save()
                return None if pages == 1 and not added else added
            self.data['last_ok'] = now
            for aid, a in page.items():
                if str(a.get('defender_faction')) != self.faction_id:
                    continue   # our own outgoing hits are in the same log
                if str(a.get('attacker_faction')) == self.faction_id:
                    continue   # one of us hitting one of us (friendly hosps): not an enemy hit
                if str(aid) in self.data['attacks']:
                    continue
                self.data['attacks'][str(aid)] = record_from(aid, a)
                added += 1
            stamps = [int(a.get('timestamp_started') or 0) for a in page.values()]
            last = max(stamps, default=frm)
            # `from` is inclusive, so the boundary second is re-read next
            # time and duplicates are dropped by id (skipping to last + 1
            # lost attacks sharing that second during a chain).
            if len(page) < 100:
                # The whole window is read.
                if to < end:
                    self.data[key] = to
                    continue
                if back:
                    # The older stretch is done: the log is complete from `start`.
                    self.data['covered_from'] = int(self.data.get('back_start') or start)
                    for k in ('back_cursor', 'back_end', 'back_start'):
                        self.data.pop(k, None)
                    continue
                # Read right up to now. Kept apart from the cursor, which
                # sits on the last attack: a quiet 3 hours is not "behind".
                self.data['cursor'] = max(last, frm)
                self.data['synced_to'] = now
                break
            self.data[key] = frm + 1 if last <= frm else last
        self.save()
        if added:
            logger.info(f'Incoming attacks: saved {added} new hit(s) on us ({pages} page(s))')
        return added

    def records(self):
        # Friendly hits saved before they were filtered out (22/11/2026).
        return [r for r in self.data['attacks'].values() if str(r.get('attacker_faction')) != self.faction_id]


# ─────────────────────────────────────────────────────────────────────────────
# Questions: "who hit us", "did <name> hit us", "who hit <member>"
# ─────────────────────────────────────────────────────────────────────────────

# "who hit/attacked/hospped/mugged us", "did X hit us", "who hit Ikaros22",
# "anyone attack us lately", "who's been hitting us". Torn slang counts:
# hosp/hospped, mug, retal'd... Not "how many hits did WE make" (outgoing).
_HIT = r"(?:hit|hits|hitting|attack\w*|hosp\w*|hospin|mug\w*|farm\w*|beat\w*|smack\w*|bust\w*|jump\w*|went for|been at)"
INCOMING_RX = re.compile(
    rf"\bwho(?:'s|s| has| have| is| was)?\s+(?:been\s+)?{_HIT}\s+(?:us|our|me)\b"
    rf"|\b(?:did|has|have|was|were)\s+[\w\- .'\[\]]{{1,40}}?\s+(?:been\s+)?{_HIT}\s+(?:us|our|me|any)"
    rf"|\b(?:any ?one|any ?body|anybody|someone|somebody)\s+(?:been\s+)?{_HIT}\s+(?:us|our|me)"
    rf"|\b(?:were|was|have|has) (?:we|i|us)\s+(?:been\s+)?hit\b|\b(?:we|i)\s+(?:got|get|been|were|was)\s+hit\b|\bget hit\b"
    rf"|\bhits? (?:on|against) (?:us|our)\b|\bincoming (?:hits?|attacks?)\b|\battacks? on (?:us|our)\b",
    re.I)
# Words that make it about OUR hits on them, or a game/rules question.
OUTGOING_RX = re.compile(r"\bwho (?:did )?(?:we|i)\b|\b(?:did|have|has) (?:i|we)\b(?! (?:get|got|been)\b)|\b(?:our|my) hits\b|\bhow (?:do|does|can)\b|\brules?\b", re.I)


# "who hit Ikaros22?": the word after the verb, checked against member names
# by the caller (ask_ai). "who hit the most" is about OUR hits, and "the"
# is nobody's name, so it never matches.
WHO_HIT_RX = re.compile(rf"\bwho(?:'s|s| has| have| is| was)?\s+(?:been\s+)?{_HIT}\s+@?([\w\-.]+)", re.I)


def is_incoming_question(question, member_names=()):
    """True for a question about hits ON us. `member_names`: our members'
    names (lower case), so "who hit Ikaros22" counts too."""
    q = question or ''
    if OUTGOING_RX.search(q):
        return False
    if INCOMING_RX.search(q):
        return True
    m = WHO_HIT_RX.search(q)
    return bool(m and m.group(1).lower() in member_names)


def _attacker_label(r):
    fac = r.get('attacker_faction_name')
    tid = r.get('attacker_id')
    base = f"{r.get('attacker_name')}" + (f' [{tid}]' if tid else '')
    return base + (f', {fac}' if fac else ', no faction' if tid else '')


def _line(r):
    res = r.get('result') or 'unknown'
    return f"- {_ts_text(r['ts'])}: {_attacker_label(r)} → {r.get('defender_name') or r.get('defender_id')} ({res})"


def _coverage(store, now):
    """(text, ok). ok False means the log can't answer at all yet."""
    if store is None or not store.data.get('covered_from'):
        return "I haven't collected incoming hits yet (the log is still starting up).", False
    frm = store.data['covered_from']
    note = f'Hits on us on record since {_date_text(frm)}'
    last_ok = store.data.get('last_ok')
    if not last_ok:
        return "I couldn't read Torn's attacks log yet, so I can't say who hit us.", False
    if _behind(store, now) > 2 * 3600:
        note += f' (still catching up: complete up to {_ts_text(_synced(store))})'
    return note, True


def _synced(store):
    """How far the log is complete: up to the last full read, or the cursor."""
    return max(store.data.get('synced_to') or 0, store.data.get('cursor') or 0)


def _behind(store, now):
    return now - _synced(store)


def find_attackers(records, query):
    """Attackers whose name contains `query` (any case), or whose Torn ID or
    faction name matches. "stepbro" finds Oh-No-StepBro."""
    q = (query or '').strip().lower()
    if not q:
        return []
    return [r for r in records if q in str(r.get('attacker_name') or '').lower()
            or q == str(r.get('attacker_id') or '')
            or (len(q) >= 4 and q in str(r.get('attacker_faction_name') or '').lower())]


_STOP = {'did', 'has', 'have', 'was', 'were', 'the', 'a', 'an', 'anyone', 'anybody', 'someone', 'somebody',
         'been', 'ever', 'recently', 'lately', 'from', 'player', 'guy', 'that', 'this', 'faction'}


def attacker_query(question):
    """The name in "did <name> hit us?", or ''. "did anyone hit us" names no one."""
    m = re.search(rf"\b(?:did|has|have|was|were)\s+([\w\- .'\[\]]{{1,40}}?)\s+(?:been\s+)?{_HIT}\s+(?:us|our|me|any)",
                  question or '', re.I)
    if not m:
        return ''
    words = [w for w in re.split(r'\s+', m.group(1).strip()) if w.lower() not in _STOP]
    return ' '.join(words).strip(" '.")


def _days_asked(question, default):
    q = (question or '').lower()
    m = re.search(r'\b(?:last|past)\s+(\d{1,2})\s+days?\b', q)
    if m:
        return max(1, min(KEEP_DAYS, int(m.group(1))))
    if re.search(r'\btoday\b|\b24 ?h', q):
        return 1
    if re.search(r'\b(?:this|last|past) week\b', q):
        return 7
    if re.search(r'\b(?:this|last|past) month\b', q):
        return 30
    if re.search(r'\bever\b|\ball\b', q):
        return KEEP_DAYS
    return default


def incoming_answer(question, store, member=None, now=None, show=12):
    """
    The bot's own answer to an incoming-hits question. `member` is
    (torn_id, name) for "who hit <member>", else None. Always text; never a
    confident "nobody" when the log can't tell.
    """
    now = now or time.time()
    cover, ok = _coverage(store, now)
    if not ok:
        return cover
    # Still reading the backfill: a "no hits" would be a guess.
    catching_up = _behind(store, now) > 2 * 3600
    if catching_up:
        unsure = ("Nothing found so far, but I'm still reading Torn's attacks log (done up to "
                  f"{_ts_text(_synced(store))}), so that is not the full picture yet. Ask again in a bit.")
    recs = store.records()
    who = attacker_query(question)
    if who:
        # "did stepbro hit us": every hit kept, not just the last week.
        hits = sorted(find_attackers(recs, who), key=lambda r: -r['ts'])
        if member:
            hits = [r for r in hits if r.get('defender_id') == str(member[0])]
        if not hits and catching_up:
            return unsure
        if not hits:
            return (f"No hits on us from anyone matching **{who}** in my records. {cover}.\n"
                    '-# Only names are matched, and stealthed attackers show no name.')
        names = sorted({_attacker_label(r) for r in hits})
        head = (f"Yes, **{who}** matches {plural(len(names), 'attacker')}: " + '; '.join(names[:5])
                + f". {plural(len(hits), 'hit')} on us on record:")
        lines = [_line(r) for r in hits[:show]]
        more = f'\n-# ...and {len(hits) - show} more.' if len(hits) > show else ''
        return f"{head}\n" + '\n'.join(lines) + more + f'\n-# {cover}.'
    days = _days_asked(question, 7 if not member else 30)
    cutoff = now - days * DAY
    hits = [r for r in recs if r['ts'] >= cutoff]
    if member:
        hits = [r for r in hits if r.get('defender_id') == str(member[0])]
    span = 'the last 24 hours' if days == 1 else f'the last {days} days'
    target = f'**{member[1]}**' if member else 'us'
    if not hits and catching_up:
        return unsure
    if not hits:
        return f'No hits on {target} in {span}, as far as my records go. {cover}.'
    hits.sort(key=lambda r: -r['ts'])
    by_attacker = {}
    for r in hits:
        by_attacker.setdefault(_attacker_label(r), []).append(r)
    ranked = sorted(by_attacker.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    out = [f"{plural(len(hits), 'hit')} on {target} in {span}, from {plural(len(by_attacker), 'attacker')}:"]
    for label, rs in ranked[:show]:
        victims = sorted({r.get('defender_name') or r.get('defender_id') for r in rs})
        out.append(f"- {label}: {plural(len(rs), 'hit')}"
                   + ('' if member else f" (on {', '.join(victims[:4])}{'...' if len(victims) > 4 else ''})")
                   + f", latest {_ts_text(rs[0]['ts'])}")
    if len(ranked) > show:
        out.append(f'-# ...and {plural(len(ranked) - show, "more attacker")}.')
    out.append(f'-# {cover}.')
    return '\n'.join(out)
