"""
Regression tests for logic that has failed silently before.

Both bugs guarded here produced no error - they just quietly did nothing,
which is why they survived so long. Keep these cheap and dependency-free.
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# --- OC crimes parsing -------------------------------------------------------
# Torn's v2 faction/crimes uses executed_at + slots[].user.id. The original code
# looked for status == 'completed' and 'initiated_at', which never matched, so
# no completions were ever recorded and nobody was flagged for OC inactivity.

def _v2_crimes_payload():
    return {'crimes': [
        {   # concluded crime - has executed_at, participants under slots[].user.id
            'id': 1, 'name': 'Snow Blind', 'status': 'Successful',
            'executed_at': 1784005629, 'ready_at': 1784000000, 'planning_at': None,
            'slots': [
                {'position': 'Hustler', 'user': {'id': 3525640, 'outcome': 'Successful'}},
                {'position': 'Muscle', 'user': {'id': 3198618, 'outcome': 'Injured'}},
            ],
        },
        {   # still recruiting - no executed_at, empty slot
            'id': 2, 'name': 'Mob Mentality', 'status': 'Recruiting',
            'executed_at': None,
            'slots': [{'position': 'Looter', 'user': None}],
        },
    ]}


def test_oc_crimes_parse_completed_and_participants(monkeypatch):
    import utils.torn_api as api

    async def fake_v2(selections='crimes'):
        return _v2_crimes_payload()

    monkeypatch.setattr(api, 'fetch_faction_data_v2', fake_v2)
    result = asyncio.run(api.fetch_faction_crimes())

    done = [c for c in result if c['completed']]
    assert len(done) == 1, f'expected 1 completed crime, got {len(done)}'
    assert done[0]['completion_ts'] == 1784005629.0
    # participants must come from slots[].user.id, not the absent 'user_id'
    assert done[0]['participants'] == ['3525640', '3198618'], done[0]['participants']

    active = [c for c in result if not c['completed']]
    assert len(active) == 1
    assert active[0]['participants'] == []  # unfilled slot contributes nobody


# --- Unattended verification -------------------------------------------------
# A Discord nickname is user-set, so it is NOT proof of Torn account ownership.
# Only Torn's official Discord link may verify anyone unattended.

class _FakeMember:
    def __init__(self, mid):
        self.id = mid
        self.bot = False
        self.nick = None
        self.display_name = f'user{mid}'


class _FakeGuild:
    def __init__(self, member_ids):
        self._members = {m: _FakeMember(m) for m in member_ids}
        self.members = list(self._members.values())

    def get_member(self, mid):
        return self._members.get(mid)


def test_autoverify_only_accepts_torn_linked_accounts(monkeypatch, tmp_path):
    from features.verification import VerificationSystem
    import features.verification as vmod

    roster = {
        '111': {'name': 'LinkedGuy'},    # linked, and present in the server
        '222': {'name': 'UnlinkedGuy'},  # no Torn->Discord link at all
        '333': {'name': 'ElsewhereGuy'}, # linked, but not in this server
    }
    links = {'111': '900000000000000001', '222': '', '333': '900000000000000009'}

    async def fake_roster():
        return roster

    async def fake_link(tid):
        return links[str(tid)]

    monkeypatch.setattr('utils.torn_api.fetch_faction_members', fake_roster)
    monkeypatch.setattr('utils.torn_api.fetch_user_discord', fake_link)

    vs = VerificationSystem(bot=None)
    vs.verified_users_file = str(tmp_path / 'verified.json')
    vs.verified_users = {}
    vs.inactivity_tracker = None

    guild = _FakeGuild([900000000000000001])  # only the linked member is present
    result = asyncio.run(vs.autoverify_linked_members(guild))

    assert result['verified'] == 1, result
    assert '900000000000000001' in vs.verified_users
    assert vs.verified_users['900000000000000001']['torn_id'] == 111
    assert vs.verified_users['900000000000000001']['verified_by'] == 'torn-discord-link'
    # unlinked member must never be auto-verified
    assert result['unlinked'] == 1
    assert all(d['torn_id'] != 222 for d in vs.verified_users.values())
    # linked but absent from the server must not be verified either
    assert '900000000000000009' not in vs.verified_users


def test_bulksync_is_not_callable_unattended_with_nickname_trust():
    """bulksync matches on user-controlled nicknames, so it must stay manual.

    Guards against re-introducing an unattended caller: the daily cron must not
    reference it.
    """
    bot_src = (ROOT / 'bot.py').read_text(encoding='utf-8')
    assert 'bulksync_members' not in bot_src, (
        'bot.py must not call bulksync_members - nickname matching is not identity '
        'proof and must not run unattended'
    )
