"""
One-off: recover per-member hits-taken for wars archived before the fix.

Three wars were archived with `defends: 0` for every member, because Torn's
v2 ranked war report had dropped the `defends` field and nothing noticed. The
numbers themselves are still derivable from the faction attacks log, but only
while that log still reaches back - measured 2026-10-17 it reached 63 days,
so war 47427 is close to falling out permanently.

Incoming is classified by the WAR MODIFIER, never by attacker faction: a
stealth attack blanks attacker_name and attacker_faction in the defender's
log, and that was 395 of 931 war hits in war 50338. The modifier is also only
applied during the war itself, which is what makes a generous 6-day window
safe when no exact end time was stored.

Records are marked `reconstructed: True` so they are never mistaken for
live-captured data. Run once:

    railway ssh --service torn-faction-bot "python scripts/backfill_war_bleed.py"
"""
import asyncio
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from features.wars import WarTracker                      # noqa: E402
from utils.torn_api import fetch_faction_members, close_session  # noqa: E402

STATS_FILE = 'data/war_member_stats.json'
HISTORY_FILE = 'data/war_history.json'

# war_id -> (start, enemy_faction_id). Ends were never stored, so each window
# runs 6 days and relies on the war modifier to bound itself.
WARS = {
    '47427': (1786741200, '50426'),
    '48339': (1788040800, '55135'),
    '50338': (1791028800, '53421'),
}


async def main():
    if not os.path.exists(STATS_FILE):
        print('no war_member_stats.json, nothing to do')
        return

    backup = f'{STATS_FILE}.pre-backfill.{int(time.time())}'
    shutil.copy2(STATS_FILE, backup)
    print(f'backed up to {backup}')

    with open(STATS_FILE, encoding='utf-8') as fh:
        store = json.load(fh)
    wars = store.setdefault('wars', [])
    by_id = {str(w.get('war_id')): w for w in wars}

    history = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding='utf-8') as fh:
            history = json.load(fh)

    mgr = WarTracker(bot=None)
    roster = await fetch_faction_members() or {}
    changed = 0

    for war_id, (start, enemy) in WARS.items():
        rec = by_id.get(war_id)
        if rec and rec.get('reconstructed'):
            print(f'war {war_id}: already backfilled, skipping')
            continue

        stats = await mgr._get_war_attack_stats(enemy, start, start + 6 * 86400)
        if stats is None:
            print(f'war {war_id}: attacks log unreadable, SKIPPED '
                  f'(better to leave it empty than write a guess)')
            continue

        members = rec.get('members') if rec else None
        members = dict(members or {})
        touched = 0
        for tid, st in stats.items():
            if tid.startswith('_') or not isinstance(st, dict):
                continue
            taken = st.get('war_incoming', 0)
            outside = st.get('outside_incoming', 0)
            m = members.get(tid)
            if m is None:
                # No official per-member row survives for this war, so the
                # attack counts come from the log too and are approximate.
                m = {
                    'name': (roster.get(tid) or {}).get('name', f'ID:{tid}'),
                    'attacks': st.get('war_hits', 0),
                    'score': 0,
                    'hosps_given': st.get('hosps_given', 0),
                    'hosps_received': st.get('hosps_received', 0),
                    'attacks_from_log': True,
                }
                members[tid] = m
            # Only ever fill in a missing or zero value. An official figure
            # already recorded is better than anything derived here.
            if not m.get('defends'):
                m['defends'] = taken
                touched += 1
            if not m.get('outside_incoming'):
                m['outside_incoming'] = outside

        if rec is None:
            faction_data = (history.get(war_id) or {}).get('factions', {}) or {}
            enemy_name = 'Unknown'
            for fid, fd in faction_data.items():
                if str(fid) != str(enemy):
                    continue
                enemy_name = fd.get('name', 'Unknown')
            rec = {
                'war_id': war_id,
                'timestamp': start,
                'enemy_name': enemy_name,
                'our_score': 0,
                'enemy_score': 0,
                'result': 'unknown',
                'members': members,
                'friendly_hosps': [],
            }
            wars.append(rec)
        else:
            rec['members'] = members

        rec['reconstructed'] = True
        total = sum(m.get('defends') or 0 for m in members.values())
        print(f'war {war_id}: {len(members)} members, {touched} filled in, '
              f'{total} hits taken recovered')
        changed += 1

    if not changed:
        print('nothing changed')
        await close_session()
        return

    wars.sort(key=lambda w: w.get('timestamp', 0))
    tmp = f'{STATS_FILE}.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(store, fh)
    os.replace(tmp, STATS_FILE)   # atomic, same as utils/helpers does
    print(f'wrote {STATS_FILE} ({changed} war(s) updated)')
    await close_session()


asyncio.run(main())
