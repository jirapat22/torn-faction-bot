"""
Chain history: every chain's per-member hits, kept for a year.

Why this exists: the live chain board (features/chains.py) keeps per-member
chain hits for only 24 hours, so "how active is X?" could only ever see the
last day of chaining. This keeps a slim record of every chain instead, so the
@bot questions (features/ask_ai.py) can answer "hit in 9 of 12 chains in the
last 14 days".

How it fills itself, in one loop (every 5 minutes):
  1. Ask Torn for the newest page of finished chains (1 call). Anything not
     saved yet joins the queue.
  2. While the backfill is not finished, also read one OLDER page (1 call),
     walking back BACKFILL_DAYS. That is how the last 2 months get filled in.
  3. Fetch up to REPORTS_PER_TICK chain reports from the queue, newest first.
So new chains are saved within minutes and the backfill trickles in gently
(~180 reports an hour) instead of hammering Torn.

Chains are keyed by Torn's real chain ID. The old 24h store keys them by
LENGTH ("chain 10"), so two 10-hit chains in a day counted as one there.

None-vs-empty: a failed list or report fetch changes nothing and is retried
next tick. A report that fails MAX_ATTEMPTS times is dropped from the queue
and listed as missing, so a gap is visible rather than silently zero.
"""

import logging
import time

from utils import torn_api
from utils.helpers import load_json, save_json
from config import TORN_FACTION_ID

logger = logging.getLogger(__name__)

DATA_FILE = 'data/chain_log.json'
RETENTION_DAYS = 365      # ~0.6 KB a chain, ~10 chains a day: about 2 MB a year
BACKFILL_DAYS = 60        # how far back the one-off backfill reaches
REPORTS_PER_TICK = 15     # chain reports fetched per 5-minute tick
MAX_ATTEMPTS = 5
DAY = 86400


def slim_report(report):
    """
    The parts of a v2 chain report worth keeping, about 0.6 KB:
    hits = {torn_id: [attacks, respect]} for everyone who hit, and
    present = torn IDs who were in the faction but made no hits.
    """
    details = report.get('details') or {}
    hits = {}
    for a in report.get('attackers') or []:
        attacks = (a.get('attacks') or {}).get('total', 0) or 0
        respect = (a.get('respect') or {}).get('total', 0) or 0
        hits[str(a.get('id'))] = [int(attacks), round(float(respect), 2)]
    return {
        'start': int(report.get('start') or 0),
        'end': int(report.get('end') or 0),
        'length': int(details.get('chain') or 0),
        'respect': float(details.get('respect') or 0),
        'hits': hits,
        'present': [str(x) for x in report.get('non_attackers') or []],
    }


class ChainLog:
    def __init__(self, path=DATA_FILE):
        self.path = path
        data = load_json(path, default={}) or {}
        self.chains = data.get('chains', {})      # chain_id -> slim report
        self.queue = data.get('queue', {})        # chain_id -> {'end': ts, 'attempts': n}
        self.missing = data.get('missing', {})    # chain_id -> end ts (report never readable)
        # Oldest chain end seen while walking back. None = backfill not started.
        self.backfill_cursor = data.get('backfill_cursor')
        self.backfill_done = data.get('backfill_done', False)

    def save(self):
        save_json(self.path, {
            'chains': self.chains, 'queue': self.queue, 'missing': self.missing,
            'backfill_cursor': self.backfill_cursor, 'backfill_done': self.backfill_done,
        })

    def _prune(self, now):
        cutoff = now - RETENTION_DAYS * DAY
        for store in (self.chains, self.missing):
            for cid in [c for c, v in store.items() if (v.get('end', 0) if isinstance(v, dict) else v) < cutoff]:
                del store[cid]

    def _enqueue(self, chains, now):
        """Queue chains from a list page that aren't saved yet. Returns the oldest end seen."""
        oldest = None
        for c in chains or []:
            cid = str(c.get('id'))
            end = int(c.get('end') or c.get('start') or 0)
            oldest = end if oldest is None else min(oldest, end)
            if end < now - RETENTION_DAYS * DAY:
                continue
            if cid in self.chains or cid in self.queue or cid in self.missing:
                continue
            self.queue[cid] = {'end': end, 'attempts': 0}
        return oldest

    async def _list_page(self, to=None):
        """One page (up to 100) of finished chains, newest first. None if Torn failed."""
        params = {'selections': 'chains', 'limit': 100, 'sort': 'desc'}
        if to:
            params['to'] = int(to)
        data = await torn_api._make_api_request(f'v2/faction/{TORN_FACTION_ID}', params)
        if not data:
            return None
        return data.get('chains') or []

    async def tick(self, now=None):
        """One round: see new chains, walk the backfill back a page, fetch some reports."""
        now = now or time.time()
        changed = False

        newest = await self._list_page()
        if newest is not None:
            before = len(self.queue)
            oldest = self._enqueue(newest, now)
            changed |= len(self.queue) != before
            if self.backfill_cursor is None and oldest is not None:
                self.backfill_cursor = oldest
                changed = True

        if not self.backfill_done and self.backfill_cursor is not None:
            if self.backfill_cursor <= now - BACKFILL_DAYS * DAY:
                self.backfill_done = True
                changed = True
                logger.info('Chain log: backfill reached its limit')
            else:
                page = await self._list_page(to=self.backfill_cursor - 1)
                if page is not None:
                    if not page:
                        self.backfill_done = True      # Torn has nothing older
                    else:
                        oldest = self._enqueue(page, now)
                        self.backfill_cursor = min(self.backfill_cursor, oldest)
                    changed = True

        # Newest first, so recent chains are answerable soonest.
        for cid in sorted(self.queue, key=lambda c: -self.queue[c]['end'])[:REPORTS_PER_TICK]:
            data = await torn_api.fetch_faction_data_v2(selections='chainreport', path_id=cid)
            report = (data or {}).get('chainreport')
            if report and report.get('start'):
                self.chains[cid] = slim_report(report)
                del self.queue[cid]
            else:
                self.queue[cid]['attempts'] += 1
                if self.queue[cid]['attempts'] >= MAX_ATTEMPTS:
                    logger.warning(f'Chain log: report for chain {cid} unreadable {MAX_ATTEMPTS} times, giving up')
                    self.missing[cid] = self.queue.pop(cid)['end']
            changed = True

        self._prune(now)
        if changed:
            self.save()
            logger.info(f'Chain log: {len(self.chains)} chains saved, {len(self.queue)} queued, '
                        f'backfill {"done" if self.backfill_done else "in progress"}')


def member_chain_summary(chains, tid, now, days):
    """
    (chains run, chains they were in, chains they hit in, total hits, last hit ts)
    over the last `days`. "Were in" = listed as hitter or as present non-hitter.
    """
    tid = str(tid)
    cutoff = now - days * DAY
    run = were_in = hit_in = hits = 0
    last_hit = None
    for c in chains.values():
        if c.get('end', 0) < cutoff:
            continue
        run += 1
        if tid in c.get('hits', {}):
            n = c['hits'][tid][0]
            were_in += 1
            if n:
                hit_in += 1
                hits += n
                last_hit = max(last_hit or 0, c['end'])
        elif tid in c.get('present', []):
            were_in += 1
    return run, were_in, hit_in, hits, last_hit
