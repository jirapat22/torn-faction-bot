"""
One-off: fill in member stats for wars that were archived with NO members.

Wars 40834 (Kaos Korner) and 46089 (TGC) were saved with an empty member
list, so the @bot questions read as if nobody (Pat included) took part.

What can be recovered, and what can't:
- Hits MADE and score per member: exact, from Torn's v2 ranked war report,
  which Torn keeps for every war.
- Hits TAKEN, outside hits and friendly hosps: NOT recoverable. They come
  from the faction attacks log, which reaches back only ~2 months, and these
  wars are older. They are left out (unknown), never written as 0: a 0 would
  be a confident wrong answer (see "The None-vs-empty contract" in CLAUDE.md).
  The record is marked `incoming_unknown: True` so readers can say so.

Records are marked `reconstructed: True`. Run once, inside the container,
then restart the bot so WarTracker reloads the file (it keeps a copy in
memory and would otherwise write its old copy back at the next war end):

    docker compose exec apexpunk python scripts/backfill_empty_wars.py
"""
import asyncio
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import TORN_FACTION_ID                                   # noqa: E402
from utils.torn_api import fetch_faction_data_v2, close_session      # noqa: E402

STATS_FILE = 'data/war_member_stats.json'


async def main():
    with open(STATS_FILE, encoding='utf-8') as fh:
        store = json.load(fh)
    targets = [w for w in store.get('wars', []) if not w.get('members')]
    if not targets:
        print('no wars without members, nothing to do')
        return

    backup = f'{STATS_FILE}.pre-empty-backfill.{int(time.time())}'
    shutil.copy2(STATS_FILE, backup)
    print(f'backed up to {backup}')

    changed = 0
    for w in targets:
        wid = str(w.get('war_id'))
        data = await fetch_faction_data_v2(selections='rankedwarreport', path_id=wid)
        report = (data or {}).get('rankedwarreport') or {}
        ours = next((f for f in report.get('factions') or [] if str(f.get('id')) == str(TORN_FACTION_ID)), None)
        enemy = next((f for f in report.get('factions') or [] if str(f.get('id')) != str(TORN_FACTION_ID)), None)
        if not ours or not ours.get('members'):
            print(f'war {wid}: report unreadable or has no members, SKIPPED (left as it was)')
            continue
        w['members'] = {
            str(m['id']): {
                'name': m.get('name', f'ID:{m["id"]}'),
                'attacks': m.get('attacks', 0),
                'score': m.get('score', 0),
                # Unknown, so deliberately absent rather than 0.
                'defends': None,
                'outside_incoming': None,
            }
            for m in ours['members']
        }
        w['our_score'] = ours.get('score', w.get('our_score'))
        if enemy:
            w['enemy_score'] = enemy.get('score', w.get('enemy_score'))
            w['enemy_name'] = enemy.get('name', w.get('enemy_name'))
        winner = report.get('winner')
        if winner:
            w['result'] = 'victory' if str(winner) == str(TORN_FACTION_ID) else 'defeat'
        w['reconstructed'] = True
        w['incoming_unknown'] = True
        changed += 1
        print(f'war {wid}: {len(w["members"])} members, '
              f'{sum(m["attacks"] for m in w["members"].values())} hits made recovered')

    if changed:
        tmp = f'{STATS_FILE}.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(store, fh)
        os.replace(tmp, STATS_FILE)   # atomic, same as utils/helpers does
        print(f'wrote {STATS_FILE} ({changed} war(s) filled in)')
    await close_session()


asyncio.run(main())
