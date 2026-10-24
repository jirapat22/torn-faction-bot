"""
Smoke test: every production module imports cleanly with stub env vars.

Catches dead imports, broken syntax, removed symbols, and circular imports
without booting the bot. Run with: python -m pytest tests/  (or just `python tests/test_imports.py`).
"""

import importlib
import os
import sys
from pathlib import Path

# Stub required env vars so config.py loads without raising
os.environ.setdefault('DISCORD_TOKEN', 'test')
os.environ.setdefault('TORN_API_KEY', 'test')
os.environ.setdefault('GUILD_ID', '1')
os.environ.setdefault('TORN_FACTION_ID', '1')

# Ensure project root is on sys.path so imports resolve from anywhere
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


MODULES = [
    'config',
    'utils.helpers',
    'utils.torn_api',
    'features.commands',
    'features.availability',
    'features.chains',
    'features.enemies',
    'features.event_reminders',
    'features.event_ui',
    'features.faction',
    'features.flytrack',
    'features.inactivity',
    'features.roles',
    'features.stakeout',
    'features.verification',
    'features.wars',
]


def test_all_modules_import():
    """Every production module must import without errors."""
    failures = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:
            failures.append(f'{name}: {type(e).__name__}: {e}')
    assert not failures, 'Import failures:\n  ' + '\n  '.join(failures)


def test_helper_signatures():
    """Helpers exposed and callable. Catches silent renames."""
    from utils.helpers import (
        format_timestamp, format_timestamp_with_tct, format_time_dual,
        next_occurrence_of_hour, get_current_timestamp, get_days_since,
        is_management, create_embed, load_json, save_json, ensure_data_directories,
    )
    # Smoke-call the pure ones
    assert format_timestamp(1714000000, 'R').startswith('<t:')
    assert 'TCT' in format_time_dual(1714000000)
    assert 'TCT' in format_time_dual(1714000000, date=True)
    assert isinstance(next_occurrence_of_hour(0), int)
    assert isinstance(get_current_timestamp(), int)


def test_torn_api_helpers():
    """Public API of torn_api hasn't drifted."""
    from utils import torn_api
    for name in ('fetch_faction_data', 'fetch_faction_data_v2', 'fetch_faction_attacks',
                 'fetch_player_data', 'fetch_any_faction_data', 'fetch_ffscouter_stats',
                 'fetch_faction_crimes', 'verify_api_key', 'close_session'):
        assert hasattr(torn_api, name), f'Missing torn_api.{name}'


def test_stakeout_algorithm():
    """End-to-end: synthetic UTC+0 sleeper produces correct windows."""
    from features.stakeout import StakeoutManager, STATUS_ONLINE, STATUS_OFFLINE
    from datetime import datetime, timezone, timedelta

    mgr = StakeoutManager(bot=None)
    history = []
    start = datetime(2025, 4, 20, 0, 0, tzinfo=timezone.utc)
    for i in range(3 * 24 * 4):  # 3 days × 96 records/day
        t = start + timedelta(minutes=15 * i)
        h = t.hour
        if h >= 23 or h < 7:
            status = STATUS_OFFLINE
        elif 18 <= h <= 22:
            status = STATUS_ONLINE
        else:
            status = STATUS_OFFLINE if i % 3 == 0 else STATUS_ONLINE
        history.append({
            'timestamp': t.timestamp(),
            'hour': h,
            'weekday': t.weekday(),
            'status': status,
            'extra': None,
            'attackable': True,
        })

    mgr.history['1'] = history
    report = mgr.generate_report('1')
    assert report is not None
    sleep = report['sleep_window']
    peak = report['peak_online_window']
    assert sleep is not None and 22 <= sleep[0] <= 23 and 6 <= sleep[1] <= 8
    assert peak is not None and 17 <= peak[0] <= 19


def test_flytrack_logic():
    """Phase parsing, tier learning, and ETA math for enemy flight tracking."""
    from features.flytrack import (
        FlyTracker, parse_phase, STANDARD_FLIGHT_MINUTES,
        PHASE_HOME, PHASE_OUT, PHASE_ABROAD, PHASE_RETURNING,
    )

    # Status description → phase/destination. Confirmed live in production:
    # Torn's real wording is "Traveling from {origin} to {destination}", not
    # the "Traveling to X" this module originally assumed.
    assert parse_phase({'state': 'Traveling', 'description': 'Traveling from Torn to Mexico'}) == (PHASE_OUT, 'Mexico')
    assert parse_phase({'state': 'Traveling', 'description': 'Traveling from South Africa to Torn'}) == (PHASE_RETURNING, 'South Africa')
    assert parse_phase({'state': 'Traveling', 'description': 'Traveling from Torn to UAE'}) == (PHASE_OUT, 'UAE')
    # Legacy/alternate wordings kept as a secondary fallback attempt
    assert parse_phase({'state': 'Traveling', 'description': 'Returning to Torn from South Africa'}) == (PHASE_RETURNING, 'South Africa')
    assert parse_phase({'state': 'Traveling', 'description': 'Traveling to Torn'})[0] == PHASE_RETURNING
    assert parse_phase({'state': 'Abroad', 'description': 'In United Arab Emirates'}) == (PHASE_ABROAD, 'UAE')
    assert parse_phase({'state': 'Hospital', 'description': 'In a Swiss hospital for 2 mins'}) == (PHASE_ABROAD, 'Switzerland')
    assert parse_phase({'state': 'Hospital', 'description': 'In hospital for 5 mins'})[0] == PHASE_HOME
    assert parse_phase({'state': 'Okay', 'description': 'Okay'})[0] == PHASE_HOME

    # Tier learning: a 13-minute Mexico flight (26 std) is business class (x0.30 → 7.8)
    # vs WLT (x0.50 → 13.0) — should learn WLT
    mgr = FlyTracker(bot=None)
    mgr._learn_tier('42', 'Testy', 'Mexico', 13 * 60)
    assert mgr.tiers['42']['tier'] == 'wlt'

    # Unmeasured player falls back to the empirical median ratio across the
    # faction (0.669), not the theoretical airstrip bucket — measured against
    # 82 real flights, the buckets ran ~6% slow and always predicted late.
    from features.flytrack import DEFAULT_RATIO
    eta = mgr._eta_ts('999', 'Mexico', 1_000_000)
    assert eta == 1_000_000 + int(STANDARD_FLIGHT_MINUTES['Mexico'] * DEFAULT_RATIO * 60)

    # A measured player uses their own timed speed, not a rounded bucket
    eta_wlt = mgr._eta_ts('42', 'Mexico', 1_000_000)
    assert eta_wlt == 1_000_000 + int(STANDARD_FLIGHT_MINUTES['Mexico'] * 0.50 * 60)

    # Garbage measurement (fits no tier) must be rejected
    mgr._learn_tier('7', 'Glitchy', 'Mexico', 3 * 60)
    assert '7' not in mgr.tiers


def test_flytrack_estimate_leg():
    """
    _estimate_leg is the only ETA path (Torn's `travel` selection returns API
    error 7 "Incorrect ID-entity relation" for any ID but the key owner's own —
    confirmed live in production — so there is no exact-time path for enemies).
    """
    from features.flytrack import (FlyTracker, PHASE_OUT, PHASE_RETURNING,
                                   STANDARD_FLIGHT_MINUTES, DEFAULT_RATIO)

    mgr = FlyTracker(bot=None)
    now_ts = 1_000_000

    resolved = mgr._estimate_leg('1', PHASE_OUT, 'Mexico', now_ts)
    assert resolved['phase'] == PHASE_OUT
    assert resolved['dest'] == 'Mexico'
    assert resolved['eta'] == int(now_ts + STANDARD_FLIGHT_MINUTES['Mexico'] * DEFAULT_RATIO * 60)
    assert 'estimate' in resolved['note']

    # Returning with no destination in the text must inherit the last-known
    # abroad location from prev, not silently drop to "unknown"
    resolved = mgr._estimate_leg('2', PHASE_RETURNING, None, now_ts, prev={'dest': 'Switzerland'})
    assert resolved['phase'] == PHASE_RETURNING
    assert resolved['dest'] == 'Switzerland'
    assert resolved['eta'] == int(now_ts + STANDARD_FLIGHT_MINUTES['Switzerland'] * DEFAULT_RATIO * 60)

    # Genuinely unknown destination (no text match, no prior dest) — no ETA,
    # but the caller still gets a phase and a graceful "unknown" message
    resolved = mgr._estimate_leg('3', PHASE_OUT, None, now_ts)
    assert resolved['eta'] is None
    assert resolved['note'] == ''


def test_flytrack_group_formatting():
    """
    Group-line formatters must not depend on self.players, since status_embed()
    is a read-only peek that must never mutate the live tracker's persisted
    state (self.players is actively used by the running background loop).
    """
    from features.flytrack import FlyTracker, PHASE_OUT, PHASE_RETURNING

    mgr = FlyTracker(bot=None)
    # mgr.players deliberately left empty — formatters take fields directly
    assert mgr.players == {}

    line = mgr._format_group_line('Chaosito', PHASE_OUT, 'China', 1_700_000)
    assert 'Chaosito' in line and 'China' in line and '→' in line

    line = mgr._format_group_line('RodneyAKing', PHASE_RETURNING, 'South Africa', None)
    assert '←' in line and 'ETA unknown' in line

    line = mgr._format_abroad_line('Ferothen', 'China')
    assert line == '**Ferothen** · China'


def test_flytrack_status_reuses_persisted_eta():
    """
    Regression test: status_embed() must reuse the already leg_start-anchored
    eta from self.players for a leg check_flights() already caught, not
    recompute from "now". Recomputing pushes the ETA later every single time
    the peek is called (confirmed live: an alert said 6:31pm, the status
    peek 10 minutes later said 6:42pm for the same still-in-flight player).
    """
    import asyncio
    from features.flytrack import FlyTracker, PHASE_RETURNING

    mgr = FlyTracker(bot=None)
    mgr.enabled = True
    mgr.enemy_faction_name = 'TGC'

    # Simulate check_flights() having anchored this leg some time ago
    fixed_eta = 1_700_000
    mgr.players['99'] = {
        'name': 'BigGeecuz', 'phase': PHASE_RETURNING, 'dest': 'UAE',
        'eta': fixed_eta, 'leg_start': fixed_eta - 3600, 'leg_observed': True,
    }

    async def fake_members():
        return {'99': {'name': 'BigGeecuz',
                       'status': {'state': 'Traveling', 'description': 'Traveling from UAE to Torn'}}}
    mgr._fetch_enemy_members = fake_members

    embed = asyncio.run(mgr.status_embed())
    field = next(f for f in embed.fields if 'Returning' in f.name)
    assert f'<t:{fixed_eta}:t>' in field.value


def test_flytrack_fast_turnaround_detected():
    """
    Regression test: a fast turnaround (landed abroad and immediately started
    returning, missing the brief Abroad tick between two 60s polls entirely)
    must still be caught. The old coarse-category comparison collapsed
    PHASE_OUT and PHASE_RETURNING into the same 'traveling' bucket, so
    OUT -> RETURNING looked like "no change" and was silently skipped -
    no return alert, and self.players stayed stuck on stale outbound data.
    """
    import asyncio
    from features.flytrack import FlyTracker, PHASE_RETURNING

    mgr = FlyTracker(bot=None)
    mgr.enabled = True
    mgr.enemy_faction_id = '1'

    mgr.players['1'] = {
        'name': 'Testy', 'phase': 'out', 'dest': 'Mexico',
        'eta': 1_600_000, 'leg_start': 1_500_000, 'leg_observed': True,
    }

    posted = []

    async def fake_post(content=None, embed=None):
        if content:
            posted.append(content)
    mgr._post = fake_post

    async def fake_save():
        pass
    mgr.save_data = fake_save

    async def fake_members():
        return {'1': {'name': 'Testy',
                     'status': {'state': 'Traveling', 'description': 'Traveling from Mexico to Torn'}}}
    mgr._fetch_enemy_members = fake_members

    asyncio.run(mgr.check_flights())

    assert len(posted) == 1, 'return alert must fire even though the roster state stayed "Traveling" both ticks'
    assert 'Testy' in posted[0] and 'Mexico' in posted[0]
    assert mgr.players['1']['phase'] == PHASE_RETURNING


def test_register_rejects_torn_discord_mismatch():
    """
    Regression test for a real vulnerability: /register let anyone claim any
    Torn ID with zero ownership proof - just that the ID exists and isn't
    already claimed in this bot's own database. Torn's own Torn<->Discord
    link (set by the player through Torn itself, unspoofable from this side)
    must be checked: if it points to a DIFFERENT Discord account, reject.
    """
    import asyncio
    import features.verification as verification_mod
    import utils.torn_api as torn_api_mod

    async def fake_fetch_player_data(torn_id, *args, **kwargs):
        return {'player_id': torn_id, 'name': 'Victim', 'level': 10,
                'faction': {'faction_id': 0, 'faction_name': '', 'faction_tag': ''},
                'last_action': {'timestamp': 0}}
    verification_mod.fetch_player_data = fake_fetch_player_data

    async def fake_mismatch(torn_id):
        return '999999999999999999'  # a DIFFERENT discord id than the caller
    torn_api_mod.fetch_user_discord = fake_mismatch

    class FakeUser:
        id = 111111111111111111
    class FakeInteraction:
        user = FakeUser()
        guild = None

    mgr = verification_mod.VerificationSystem(bot=None)
    mgr.verified_users = {}
    mgr.config = {'require_faction': False, 'change_name': True, 'append_id': True, 'append_faction_tag': False}

    success, message = asyncio.run(mgr.register_user(FakeInteraction(), 12345))
    assert success is False
    assert 'linked to a different Discord account' in message
    assert '12345' not in mgr.verified_users.get(str(FakeUser.id), {})


def test_register_allows_unlinked_torn_id():
    """
    Self-registration must still work when the Torn ID has no Torn<->Discord
    link on file - there's no better ownership signal available, so this is
    an intentional, documented remaining gap, not something to break.
    """
    import asyncio
    import features.verification as verification_mod
    import utils.torn_api as torn_api_mod

    async def fake_fetch_player_data(torn_id, *args, **kwargs):
        return {'player_id': torn_id, 'name': 'Me', 'level': 5,
                'faction': {'faction_id': 0, 'faction_name': '', 'faction_tag': ''},
                'last_action': {'timestamp': 0}}
    verification_mod.fetch_player_data = fake_fetch_player_data

    async def fake_no_link(torn_id):
        return ''
    torn_api_mod.fetch_user_discord = fake_no_link

    class FakeUser:
        id = 222222222222222222
        async def edit(self, nick=None):
            pass
        async def add_roles(self, role):
            pass
    class FakeGuild:
        def get_role(self, role_id):
            return None
    class FakeInteraction:
        user = FakeUser()
        guild = FakeGuild()

    async def fake_save():
        pass

    mgr = verification_mod.VerificationSystem(bot=None)
    mgr.verified_users = {}
    mgr.config = {'require_faction': False, 'change_name': False, 'append_id': True,
                  'append_faction_tag': False, 'verified_role_id': None}
    mgr.save_verified_users = fake_save

    success, message = asyncio.run(mgr.register_user(FakeInteraction(), 54321))
    assert success is True
    assert str(FakeUser.id) in mgr.verified_users


def test_chain_milestone_picks_highest_on_jump():
    """
    Regression test: a chain that jumps past multiple milestones between two
    1-minute polls (plausible when several members hit at once) must announce
    the HIGHEST milestone reached, not the lowest. The old ascending-order
    loop with an early break reported "10 hits!" for a true jump to 120, and
    the skipped 25/50/100 milestones were never announced afterward since
    last_chain_count moves past them regardless.
    """
    import asyncio
    import features.chains as chains_mod
    from features.chains import ChainTracker

    tracker = ChainTracker(bot=None)
    tracker.was_chain_active = True
    tracker.last_chain_count = 8  # below all milestones
    tracker.last_chain_start = 0
    tracker.chain_end_checks = 0

    posted = []

    class FakeChannel:
        async def send(self, content=None, embed=None):
            posted.append(content)

    tracker.bot = type('FakeBot', (), {'get_channel': lambda self, cid: FakeChannel()})()

    async def fake_get_current_chain():
        return {'current': 120, 'start': 1000, 'timeout': 0, 'modifier': 1.0, 'max': 200}
    tracker.get_current_chain = fake_get_current_chain

    chains_mod.save_json = lambda *a, **kw: None  # avoid real disk writes

    async def fake_save_history():
        pass
    tracker.save_chain_history = fake_save_history

    asyncio.run(tracker.check_chain_update())

    assert len(posted) == 1
    assert '100 hits' in posted[0]
    assert tracker.last_chain_count == 120


def test_flytrack_autostop_trusts_war_tracker_not_raw_none():
    """
    Regression test for a real production incident: fly-tracking silently
    disabled itself during a confirmed-still-active war, hours before it
    actually ended, with no clear signal why. Root cause: the old auto-stop
    counted consecutive get_current_war() == None results on its own - a
    transient API hiccup can produce that, which WarTracker's own war-end
    detection already guards against (raw refetch + per-war end-timestamp
    check) before ever flipping was_war_active.
    """
    import asyncio
    from features.flytrack import FlyTracker, WAR_END_STRIKES

    # Case 1: transient hiccups while the war tracker's own confirmed state
    # says the same war is still active - must never disable, no matter how
    # many hiccups occur.
    class FlakyButActiveWarTracker:
        was_war_active = True
        current_war_id = '1'
        async def get_current_war(self):
            return None

    mgr = FlyTracker(bot=None, war_tracker=FlakyButActiveWarTracker())
    mgr.enabled = True
    mgr.enemy_faction_id = '1'
    mgr.war_id = '1'

    async def fake_members_empty():
        return {}
    mgr._fetch_enemy_members = fake_members_empty

    disabled = []
    async def fake_disable(reason='Toggled off'):
        disabled.append(reason)
    mgr.disable = fake_disable

    for _ in range(5):
        asyncio.run(mgr.check_flights())
    assert disabled == [], 'repeated API hiccups must not auto-disable a genuinely active war'
    assert mgr.enabled is True

    # Case 2: war tracker confirms the war really ended, consistently across
    # several ticks - must disable, but only once the strike threshold is met
    # (a single confirmation isn't enough - see Case 3 for why).
    class ConfirmedEndedWarTracker:
        was_war_active = False
        current_war_id = None
        async def get_current_war(self):
            return None

    mgr2 = FlyTracker(bot=None, war_tracker=ConfirmedEndedWarTracker())
    mgr2.enabled = True
    mgr2.enemy_faction_id = '1'
    mgr2.war_id = '1'

    async def fake_members_empty2():
        return {}
    mgr2._fetch_enemy_members = fake_members_empty2  # ticks before the threshold fall through to here

    disabled2 = []
    async def fake_disable2(reason='Toggled off'):
        disabled2.append(reason)
        mgr2.enabled = False

    async def fake_post2(content=None, embed=None):
        pass

    mgr2.disable = fake_disable2
    mgr2._post = fake_post2

    for i in range(WAR_END_STRIKES - 1):
        asyncio.run(mgr2.check_flights())
        assert disabled2 == [], f'must not disable before {WAR_END_STRIKES} confirmations (tick {i + 1})'
    asyncio.run(mgr2.check_flights())
    assert disabled2 == ['War ended']

    # Case 3: the actual restart-race this threshold exists for. was_war_active
    # and current_war_id always reset when WarTracker is freshly constructed
    # (process restart) and only recover after war_update_check's first
    # tick - which may not have run yet when fly_check's own first tick
    # fires. A war that's genuinely still active must survive a couple of
    # early reads of those not-yet-warmed-up values.
    class RestartingWarTracker:
        def __init__(self):
            self.was_war_active = False  # fresh process state, war_update_check hasn't run yet
            self.current_war_id = None
        async def get_current_war(self):
            return {'war_id': '1'}

    wt = RestartingWarTracker()
    mgr3 = FlyTracker(bot=None, war_tracker=wt)
    mgr3.enabled = True  # resumed from disk: was tracking before the restart
    mgr3.enemy_faction_id = '1'
    mgr3.war_id = '1'

    async def fake_members_empty3():
        return {}
    mgr3._fetch_enemy_members = fake_members_empty3

    disabled3 = []
    async def fake_disable3(reason='Toggled off'):
        disabled3.append(reason)
    mgr3.disable = fake_disable3

    # First tick(s) race ahead of war_update_check and see the stale reset
    # values - must not disable yet.
    asyncio.run(mgr3.check_flights())
    assert disabled3 == [], 'must tolerate the post-restart warm-up window'

    # war_update_check catches up and confirms the same war is still active.
    wt.was_war_active = True
    wt.current_war_id = '1'
    asyncio.run(mgr3.check_flights())
    assert disabled3 == [], 'must recover once the war tracker catches up, not stay committed to disabling'

    # Case 4: a DIFFERENT war is now active (new opponent) - self.enemy_faction_id
    # is fixed at enable() and never re-derived, so without this check flytrack
    # would keep silently polling the old, no-longer-relevant faction forever.
    class DifferentWarTracker:
        was_war_active = True
        current_war_id = '999'  # a new war, not the one flytrack is tracking
        async def get_current_war(self):
            return {'war_id': '999'}

    mgr4 = FlyTracker(bot=None, war_tracker=DifferentWarTracker())
    mgr4.enabled = True
    mgr4.enemy_faction_id = '1'
    mgr4.war_id = '1'  # still tracking the OLD war

    async def fake_members_empty4():
        return {}
    mgr4._fetch_enemy_members = fake_members_empty4

    disabled4 = []
    async def fake_disable4(reason='Toggled off'):
        disabled4.append(reason)
        mgr4.enabled = False

    async def fake_post4(content=None, embed=None):
        pass

    mgr4.disable = fake_disable4
    mgr4._post = fake_post4

    for _ in range(WAR_END_STRIKES):
        asyncio.run(mgr4.check_flights())
    assert disabled4 == ['A different war is now active. Re-toggle Fly tracking for it']


def test_activity_profile_chunk_detection():
    """Pure hour-probability-curve -> active-window chunking, including
    midnight wraparound and the 5-chunk cap."""
    from features.activity_profile import find_active_chunks, best_and_quiet_windows

    # Two separate daily bursts (e.g. a quick morning check-in, then evening play)
    probs = {h: 0.0 for h in range(24)}
    for h in (8, 9):
        probs[h] = 0.8
    for h in (18, 19, 20, 21, 22):
        probs[h] = 0.9
    chunks = find_active_chunks(probs, threshold=0.3, max_chunks=5)
    assert chunks == [(8, 10, 0.8), (18, 23, 0.9)]

    # A single window straddling midnight must not get split at the array boundary
    probs_wrap = {h: 0.0 for h in range(24)}
    for h in (22, 23, 0, 1):
        probs_wrap[h] = 0.7
    assert find_active_chunks(probs_wrap, threshold=0.3, max_chunks=5) == [(22, 2, 0.7)]

    # Nothing clears the threshold -> no pattern, not a false window
    assert find_active_chunks({h: 0.1 for h in range(24)}, threshold=0.3, max_chunks=5) == []

    # Always active -> one all-day window, not 24 tiny ones
    assert find_active_chunks({h: 0.9 for h in range(24)}, threshold=0.3, max_chunks=5) == [(0, 24, 0.9)]

    # 6 separate single-hour bursts, capped at 5 -> weakest dropped, rest kept in start order
    probs_many = {h: 0.0 for h in range(24)}
    strengths = {1: 0.9, 4: 0.8, 7: 0.7, 10: 0.6, 13: 0.5, 16: 0.4}
    for h, p in strengths.items():
        probs_many[h] = p
    capped = find_active_chunks(probs_many, threshold=0.3, max_chunks=5)
    assert len(capped) == 5
    assert (16, 17, 0.4) not in capped  # weakest of the 6 dropped
    assert [c[0] for c in capped] == sorted(c[0] for c in capped)  # sorted by start hour

    # Faction-wide best/quiet sliding window
    probs_cov = {h: 0.5 for h in range(24)}
    for h in (20, 21, 22):
        probs_cov[h] = 0.9
    for h in (3, 4, 5):
        probs_cov[h] = 0.1
    best, quiet = best_and_quiet_windows(probs_cov, window_hours=3)
    assert (best[0], best[1]) == (20, 23) and abs(best[2] - 0.9) < 1e-9
    assert (quiet[0], quiet[1]) == (3, 6) and abs(quiet[2] - 0.1) < 1e-9


def test_activity_profile_manager_queries():
    """Manager-level queries: warm-up gating, roster summary, and coverage
    aggregation correctly exclude members without enough tracked history."""
    from features.activity_profile import ActivityProfileManager, best_and_quiet_windows
    from datetime import datetime, timezone, timedelta

    mgr = ActivityProfileManager(bot=None)
    today = datetime.now(timezone.utc).date()

    alice_probs = {h: (0.8 if h in (8, 9) else (0.9 if 18 <= h <= 22 else 0.0)) for h in range(24)}
    days_list = [(today - timedelta(days=d)).strftime('%Y-%m-%d') for d in range(7) if d != 3]
    mgr.profiles['1'] = {
        'name': 'Alice',
        'first_seen': (today - timedelta(days=6)).strftime('%Y-%m-%d'),
        'hours': {str(h): [round(alice_probs[h] * 10), 10] for h in range(24)},
        'days': days_list,
        'last_updated': 0,
    }
    # Barely any samples yet -> must not be treated as ready
    mgr.profiles['2'] = {
        'name': 'Bob', 'first_seen': today.strftime('%Y-%m-%d'),
        'hours': {'12': [1, 2]}, 'days': [], 'last_updated': 0,
    }
    # Plenty of samples, but no hour ever clears the threshold -> scattered, no pattern
    cara_probs = {h: 0.1 for h in range(24)}
    mgr.profiles['3'] = {
        'name': 'Cara', 'first_seen': today.strftime('%Y-%m-%d'),
        'hours': {str(h): [1, 10] for h in range(24)}, 'days': [], 'last_updated': 0,
    }

    assert mgr.has_enough_data('1') is True
    assert mgr.has_enough_data('2') is False
    assert mgr.has_enough_data('3') is True

    assert mgr.get_active_windows('1') == [(8, 10, 0.8), (18, 23, 0.9)]
    assert mgr.get_active_windows('3') == []

    active, tracked = mgr.get_consistency('1')
    assert (active, tracked) == (6, 7)

    ready, warming_up = mgr.get_roster_summary()
    assert ready == [('1', 'Alice', (18, 23, 0.9))]  # Alice's strongest window
    assert warming_up == ['Bob', 'Cara']

    # Coverage must average only Alice + Cara (Bob excluded for insufficient data)
    expected_agg = {h: (alice_probs[h] + cara_probs[h]) / 2 for h in range(24)}
    expected_best, expected_quiet = best_and_quiet_windows(expected_agg)
    best, quiet, member_count = mgr.get_coverage()
    assert member_count == 2
    assert best == expected_best
    assert quiet == expected_quiet


def test_activity_profile_record_snapshot():
    """record_snapshot correctly classifies Online/Idle/recent-offline as
    active, ignores stale offline, and accumulates counts across calls."""
    import asyncio
    import time
    from datetime import datetime, timezone
    from features.activity_profile import ActivityProfileManager

    mgr = ActivityProfileManager(bot=None)

    async def fake_save():
        pass
    mgr.save_data = fake_save

    now = datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc)
    now_ts = now.timestamp()
    faction_members = {
        '100': {'name': 'Dan', 'last_action': {'status': 'Online', 'timestamp': now_ts}},
        '200': {'name': 'Eve', 'last_action': {'status': 'Offline', 'timestamp': now_ts - 30 * 60}},  # recent
        '300': {'name': 'Fay', 'last_action': {'status': 'Offline', 'timestamp': now_ts - 120 * 60}},  # stale
    }
    asyncio.run(mgr.record_snapshot(faction_members, now=now))

    assert mgr.profiles['100']['hours']['14'] == [1, 1]  # Online -> active
    assert mgr.profiles['200']['hours']['14'] == [1, 1]  # recently offline -> still active
    assert mgr.profiles['300']['hours']['14'] == [0, 1]  # stale offline -> inactive
    assert mgr.profiles['100']['days'] == ['2025-06-15']
    assert mgr.profiles['300']['days'] == []  # never counted active -> no day recorded

    # A second snapshot the same hour accumulates rather than overwrites
    asyncio.run(mgr.record_snapshot(faction_members, now=now))
    assert mgr.profiles['100']['hours']['14'] == [2, 2]


def test_activity_profile_decay():
    """
    Regression test for an audit finding: hour counters accumulated forever
    with no decay, so a member's activity from months ago carried the same
    weight as yesterday's — directly undermining the "notice a member's
    pattern shrinking" use case. Counters must now shrink day-over-day.
    """
    import asyncio
    from datetime import datetime, timezone
    from features.activity_profile import ActivityProfileManager, DECAY_FACTOR

    mgr = ActivityProfileManager(bot=None)

    async def fake_save():
        pass
    mgr.save_data = fake_save

    mgr.profiles['100'] = {
        'name': 'Dan', 'first_seen': '2025-01-01', 'last_decay_date': '2025-01-01',
        'last_seen_in_faction': '2025-01-01',
        'hours': {'10': [50, 100]}, 'days': [], 'last_updated': 0,
    }

    now = datetime(2025, 1, 11, 10, 0, tzinfo=timezone.utc)  # 10 days later, same hour
    faction_members = {'100': {'name': 'Dan', 'last_action': {'status': 'Offline', 'timestamp': 0}}}
    asyncio.run(mgr.record_snapshot(faction_members, now=now))

    active, total = mgr.profiles['100']['hours']['10']
    expected_total = round(100 * DECAY_FACTOR ** 10)
    expected_active = min(round(50 * DECAY_FACTOR ** 10), expected_total)
    # +1 new sample this tick (offline -> total grows, active doesn't)
    assert total == expected_total + 1
    assert active == expected_active
    assert total < 100 + 1  # actually shrank, not just kept accumulating raw
    assert mgr.profiles['100']['last_decay_date'] == '2025-01-11'

    # Same-day second call must not double-decay
    asyncio.run(mgr.record_snapshot(faction_members, now=now))
    active2, total2 = mgr.profiles['100']['hours']['10']
    assert total2 == total + 1  # only the new sample added, no extra decay


def test_activity_profile_decay_backfills_legacy_baseline():
    """
    Regression test for a bug in the decay fix itself: profiles written before
    decay existed have no 'last_decay_date'. The original fallback read that
    key defaulting to *today*, recomputed every call, so days_elapsed was
    permanently 0 and the early return never wrote a baseline back — leaving
    those profiles exempt from decay forever. A baseline must get stamped on
    first sight, so decay actually kicks in from the next day onward.
    """
    import asyncio
    from datetime import datetime, timezone
    from features.activity_profile import ActivityProfileManager, DECAY_FACTOR

    mgr = ActivityProfileManager(bot=None)

    async def fake_save():
        pass
    mgr.save_data = fake_save

    # Legacy shape: no 'last_decay_date' / 'last_seen_in_faction' keys at all
    mgr.profiles['100'] = {
        'name': 'Dan', 'first_seen': '2025-01-01',
        'hours': {'10': [50, 100]}, 'days': [], 'last_updated': 0,
    }

    day1 = datetime(2025, 5, 1, 10, 0, tzinfo=timezone.utc)
    faction_members = {'100': {'name': 'Dan', 'last_action': {'status': 'Offline', 'timestamp': 0}}}
    asyncio.run(mgr.record_snapshot(faction_members, now=day1))

    # First sight: no decay applied (unknown how stale), but a baseline is stamped
    assert mgr.profiles['100']['last_decay_date'] == '2025-05-01'
    assert mgr.profiles['100']['hours']['10'] == [50, 101]  # just the new sample

    # Next day, decay must actually apply — under the old code it never would
    day2 = datetime(2025, 5, 2, 10, 0, tzinfo=timezone.utc)
    asyncio.run(mgr.record_snapshot(faction_members, now=day2))
    active, total = mgr.profiles['100']['hours']['10']
    assert total == round(101 * DECAY_FACTOR) + 1
    assert active == min(round(50 * DECAY_FACTOR), round(101 * DECAY_FACTOR))
    assert mgr.profiles['100']['last_decay_date'] == '2025-05-02'


def test_activity_profile_pruning():
    """
    Regression test for an audit finding: profiles were never removed when a
    member left the faction, so Coverage/Activity would silently include
    ex-members forever. Absence past the grace period must prune; a shorter
    absence must not (guards against a single API hiccup wiping real history).
    """
    import asyncio
    from datetime import datetime, timezone, timedelta
    from features.activity_profile import ActivityProfileManager, PRUNE_AFTER_DAYS

    mgr = ActivityProfileManager(bot=None)

    async def fake_save():
        pass
    mgr.save_data = fake_save

    now = datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc)
    stale_date = (now.date() - timedelta(days=PRUNE_AFTER_DAYS + 1)).strftime('%Y-%m-%d')
    recent_date = (now.date() - timedelta(days=PRUNE_AFTER_DAYS - 1)).strftime('%Y-%m-%d')

    mgr.profiles['999'] = {
        'name': 'Gone', 'first_seen': '2024-01-01', 'last_decay_date': stale_date,
        'last_seen_in_faction': stale_date, 'hours': {'10': [5, 10]}, 'days': [], 'last_updated': 0,
    }
    mgr.profiles['888'] = {
        'name': 'Recent', 'first_seen': '2024-01-01', 'last_decay_date': recent_date,
        'last_seen_in_faction': recent_date, 'hours': {'10': [5, 10]}, 'days': [], 'last_updated': 0,
    }

    faction_members = {'1': {'name': 'Active', 'last_action': {'status': 'Online', 'timestamp': now.timestamp()}}}
    asyncio.run(mgr.record_snapshot(faction_members, now=now))

    assert '999' not in mgr.profiles  # absent long past the grace period -> pruned
    assert '888' in mgr.profiles      # still within the grace period -> kept
    assert '1' in mgr.profiles        # currently in the faction -> recorded normally


def test_inactivity_daily_check_retries_transient_api_failure():
    """
    Regression test for a real production failure: the daily inactivity check
    posted "Could not connect to Torn API ... Will retry tomorrow" after a
    single failed fetch. It's the only 24h loop in the bot — every other check
    re-runs within 1-15 minutes, so a momentary blip costs them one tick, but
    costs this one a full day of accountability data. It must retry in place.
    """
    import asyncio
    import features.inactivity as inactivity_mod
    from features.inactivity import InactivityTracker, DAILY_FETCH_RETRY_DELAYS

    class FakeChannel:
        def __init__(self):
            self.sent = []
        async def send(self, *args, **kwargs):
            self.sent.append(kwargs.get('embed'))

    class FakeBot:
        def __init__(self, channel):
            self._channel = channel
        def get_channel(self, _cid):
            return self._channel
        def get_guild(self, _gid):
            return None

    # Don't actually sleep through the backoff schedule during tests
    real_sleep = asyncio.sleep
    async def fast_sleep(_seconds):
        await real_sleep(0)
    asyncio.sleep = fast_sleep

    # Case 1: fetch fails once, then recovers -> must NOT post the failure embed
    calls = {'members': 0}
    import time as _time

    async def flaky_members():
        calls['members'] += 1
        if calls['members'] == 1:
            return None  # transient blip on the first attempt
        # Recovered. One currently-active member, so the downstream report
        # finds nothing to alert on and the check completes quietly.
        return {'1': {'name': 'Active', 'level': 10,
                      'last_action': {'timestamp': int(_time.time()), 'status': 'Online'}}}

    async def no_crimes():
        return []

    orig_members = inactivity_mod.fetch_faction_members
    orig_crimes = inactivity_mod.fetch_faction_crimes
    orig_save = inactivity_mod.save_json
    inactivity_mod.fetch_faction_members = flaky_members
    inactivity_mod.fetch_faction_crimes = no_crimes
    inactivity_mod.save_json = lambda *a, **k: None  # keep the test off disk

    try:
        channel = FakeChannel()
        tracker = InactivityTracker(FakeBot(channel))
        asyncio.run(tracker.check_and_alert_inactive())

        assert calls['members'] >= 2, 'must retry after a failed fetch, not give up immediately'
        failure_posts = [e for e in channel.sent
                         if e is not None and getattr(e, 'title', '') == 'Inactivity Check Failed']
        assert failure_posts == [], 'recovered on retry, so no failure embed should be posted'

        # Case 2: fetch fails every time -> exhausts retries, then reports failure
        calls['members'] = 0

        async def always_down():
            calls['members'] += 1
            return None

        inactivity_mod.fetch_faction_members = always_down
        channel2 = FakeChannel()
        tracker2 = InactivityTracker(FakeBot(channel2))
        asyncio.run(tracker2.check_and_alert_inactive())

        # 1 initial attempt + one per configured backoff delay
        assert calls['members'] == 1 + len(DAILY_FETCH_RETRY_DELAYS)
        failure_posts2 = [e for e in channel2.sent
                          if e is not None and getattr(e, 'title', '') == 'Inactivity Check Failed']
        assert len(failure_posts2) == 1, 'should still report failure once retries are exhausted'
    finally:
        inactivity_mod.fetch_faction_members = orig_members
        inactivity_mod.fetch_faction_crimes = orig_crimes
        inactivity_mod.save_json = orig_save
        asyncio.sleep = real_sleep


def test_oc_check_skipped_when_crimes_unavailable():
    """
    Regression test for a real production incident: the daily report flagged 17
    members as "no OC for 4+ days" plus 13 warnings — nearly the whole faction —
    while most of them were demonstrably mid-OC at that moment.

    Cause: fetch_faction_crimes() returned [] on API failure, indistinguishable
    from "no crimes exist". _get_oc_status then built an empty active_in_oc, so
    every member currently in an unexecuted crime lost their in-progress
    protection and got judged against a stale last-completion timestamp.
    An unreachable endpoint must skip the OC section, not invent absentees.
    """
    import time
    from features.inactivity import InactivityTracker

    tracker = InactivityTracker(bot=None)
    long_ago = time.time() - 30 * 86400
    # Two members, both with old completions, both currently mid-crime
    tracker.last_oc_completion = {'1': long_ago, '2': long_ago}
    members = {
        '1': {'name': 'InCrimeA', 'level': 20, 'days_in_faction': 100},
        '2': {'name': 'InCrimeB', 'level': 30, 'days_in_faction': 100},
    }
    active_crime = [{'completed': False, 'completion_ts': 0, 'participants': ['1', '2']}]

    # Normal case: crimes visible -> both protected by being mid-crime
    warn, crit, _, _slots = tracker._get_oc_status(members, active_crime)
    assert warn == [] and crit == [], 'members in an unexecuted crime must never be flagged'

    # Outage: crimes endpoint unreachable (None) -> skip, do NOT flag everyone
    warn_none, crit_none, updated_none, _slots = tracker._get_oc_status(members, None)
    assert warn_none == [] and crit_none == [], \
        'an unreachable crimes endpoint must skip the OC section, not flag every member'
    assert updated_none is False, 'nothing to persist when crimes data is unavailable'

    # The old failure mode, preserved as the contrast: [] genuinely means "no
    # crimes exist", so stale members legitimately get flagged. This is exactly
    # why an outage must not be collapsed into [].
    warn_empty, crit_empty, _, _slots = tracker._get_oc_status(members, [])
    assert len(crit_empty) == 2, 'with a real empty crime list, stale members do get flagged'


def test_fetch_faction_crimes_distinguishes_outage_from_empty():
    """fetch_faction_crimes must return None on total API failure, [] only when
    the API actually answered with no crimes."""
    import asyncio
    import utils.torn_api as api

    orig_v2 = api.fetch_faction_data_v2
    orig_v1 = api.fetch_faction_data
    try:
        async def down(*a, **k):
            return None
        api.fetch_faction_data_v2 = down
        api.fetch_faction_data = down
        assert asyncio.run(api.fetch_faction_crimes()) is None, \
            'both endpoints unreachable must report None, not an empty list'

        async def empty_v2(*a, **k):
            return {'crimes': []}
        async def empty_v1(*a, **k):
            return {'crimes': {}}
        api.fetch_faction_data_v2 = empty_v2
        api.fetch_faction_data = empty_v1
        assert asyncio.run(api.fetch_faction_crimes()) == [], \
            'API answered with no crimes -> empty list, not None'

        # Defensive: if Torn ever changes v2's shape, an unreadable response
        # must report an outage rather than degrade into "no crimes", which
        # would empty the in-progress set and flag every participating member.
        async def wrong_shape(*a, **k):
            return {'crimes': {'not': 'a list'}}
        api.fetch_faction_data_v2 = wrong_shape
        api.fetch_faction_data = empty_v1
        assert asyncio.run(api.fetch_faction_crimes()) is None, \
            'unreadable v2 shape must be reported as an outage, not an empty crime list'
    finally:
        api.fetch_faction_data_v2 = orig_v2
        api.fetch_faction_data = orig_v1


def test_stale_v1_crimes_fallback_rejected():
    """
    Re-audit finding: the previous fix only caught "both endpoints unreachable".
    But for an OC2 faction the v1 endpoint keeps answering 200 with frozen OC1
    history (measured live: newest record 585 days old, zero in progress). If v2
    failed and that got served as current, every crime would look completed, the
    in-progress set would be empty, and every member mid-OC would be flagged —
    today's bug again, but invisible, because the list comes back populated.
    """
    import asyncio
    import time
    import utils.torn_api as api

    orig_v2 = api.fetch_faction_data_v2
    orig_v1 = api.fetch_faction_data
    try:
        async def v2_down(*a, **k):
            return None
        api.fetch_faction_data_v2 = v2_down

        # v1 answers, but with ancient all-completed OC1 history
        ancient = time.time() - 585 * 86400
        async def v1_stale(*a, **k):
            return {'crimes': {'1': {'time_completed': ancient,
                                     'participants': [{'111': None}, {'222': None}]}}}
        api.fetch_faction_data = v1_stale
        assert asyncio.run(api.fetch_faction_crimes()) is None, \
            'stale v1 history must not stand in for live data — report the outage instead'

        # v1 with a crime actually in progress is live; trust it
        async def v1_live(*a, **k):
            return {'crimes': {'1': {'time_completed': 0,
                                     'participants': [{'111': None}]}}}
        api.fetch_faction_data = v1_live
        live = asyncio.run(api.fetch_faction_crimes())
        assert live is not None and len(live) == 1 and live[0]['completed'] is False, \
            'a v1 feed with an in-progress crime is live data and must be used'

        # v1 with a recent completion is also live
        async def v1_recent(*a, **k):
            return {'crimes': {'1': {'time_completed': time.time() - 3600,
                                     'participants': [{'111': None}]}}}
        api.fetch_faction_data = v1_recent
        recent = asyncio.run(api.fetch_faction_crimes())
        assert recent is not None and len(recent) == 1, 'recent v1 completion is live data'
    finally:
        api.fetch_faction_data_v2 = orig_v2
        api.fetch_faction_data = orig_v1


def test_retry_preserves_already_good_payload():
    """
    Re-audit finding: the retry loop now also fires when only the crimes fetch
    failed. It reassigned both payloads from each retry, so a retry where the
    member fetch blipped would overwrite good member data with None and abort
    the whole report — turning a partial success into a total failure.
    """
    import asyncio
    import features.inactivity as inactivity_mod
    from features.inactivity import InactivityTracker

    class FakeChannel:
        def __init__(self):
            self.sent = []
        async def send(self, *a, **k):
            self.sent.append(k.get('embed'))

    class FakeBot:
        def __init__(self, ch):
            self._ch = ch
        def get_channel(self, _):
            return self._ch
        def get_guild(self, _):
            return None

    real_sleep = asyncio.sleep
    async def fast_sleep(_s):
        await real_sleep(0)

    orig_members = inactivity_mod.fetch_faction_members
    orig_crimes = inactivity_mod.fetch_faction_crimes
    orig_save = inactivity_mod.save_json
    asyncio.sleep = fast_sleep
    inactivity_mod.save_json = lambda *a, **k: None

    state = {'n': 0}
    good_members = {'1': {'name': 'Someone', 'level': 5,
                          'last_action': {'timestamp': int(__import__('time').time())}}}

    async def members():
        # Succeeds first, then blips on every retry
        state['n'] += 1
        return good_members if state['n'] == 1 else None

    async def crimes_down():
        return None  # crimes never recovers -> retries all fire

    inactivity_mod.fetch_faction_members = members
    inactivity_mod.fetch_faction_crimes = crimes_down
    try:
        ch = FakeChannel()
        asyncio.run(InactivityTracker(FakeBot(ch)).check_and_alert_inactive())
        failures = [e for e in ch.sent
                    if e is not None and getattr(e, 'title', '') == 'Inactivity Check Failed']
        assert failures == [], \
            'good member data from the first attempt must survive a later failed retry'
        assert ch.sent, 'the report should still be posted using the member data we had'
    finally:
        inactivity_mod.fetch_faction_members = orig_members
        inactivity_mod.fetch_faction_crimes = orig_crimes
        inactivity_mod.save_json = orig_save
        asyncio.sleep = real_sleep


def test_left_faction_access_revoked_after_grace_period():
    """
    Someone who leaves the faction keeps their verified role (and so their
    channel access) for the grace period, then loses it — falling back to what
    @everyone can see. Revocation must be once-only, must not touch members
    still inside the grace window, and must not fire for people already back.
    """
    import asyncio
    import time
    from features.inactivity import InactivityTracker
    from config import LEFT_FACTION_ALERT_DAYS

    class FakeRole:
        id = 99
        name = 'Ascended punks'

    role = FakeRole()

    class FakeMember:
        def __init__(self):
            self.roles = [role]
            self.removed = []
        async def remove_roles(self, *roles, reason=None):
            self.removed.extend(roles)
            for r in roles:
                if r in self.roles:
                    self.roles.remove(r)

    member = FakeMember()

    class FakeGuild:
        roles = [role]
        def get_role(self, rid):
            return role if rid == role.id else None
        def get_member(self, _mid):
            return member

    class FakeVS:
        config = {'verified_role_id': 99}

    tracker = InactivityTracker(bot=None)
    tracker.verification_system = FakeVS()
    now = time.time()
    tracker.left_faction = {
        '1': {'first_gone': now - (LEFT_FACTION_ALERT_DAYS + 1) * 86400,
              'torn_name': 'Leaver', 'discord_id': '1000'},
    }
    alert = [{'torn_id': '1', 'torn_name': 'Leaver', 'discord_id': '1000',
              'days_gone': LEFT_FACTION_ALERT_DAYS + 1}]

    revoked = asyncio.run(tracker._revoke_left_faction_access(FakeGuild(), alert))
    assert revoked == ['Leaver'], 'past the grace period, access must be revoked'
    assert member.removed == [role]
    assert tracker.left_faction['1']['access_revoked'] is True

    # Running again must not re-attempt (already marked)
    revoked_again = asyncio.run(tracker._revoke_left_faction_access(FakeGuild(), alert))
    assert revoked_again == [], 'revocation is once-only, not retried every day'

    # Nobody past the grace period -> nothing revoked. _get_left_faction_status
    # only emits alerts past the threshold, so an empty alert list is the
    # in-grace case, and it must be a no-op.
    fresh = InactivityTracker(bot=None)
    fresh.verification_system = FakeVS()
    fresh.left_faction = {'2': {'first_gone': now, 'torn_name': 'JustLeft', 'discord_id': '2000'}}
    assert asyncio.run(fresh._revoke_left_faction_access(FakeGuild(), [])) == [], \
        'members still inside the grace period keep their access'

    # No guild (bot not in server / cache cold) -> safe no-op, never a crash
    assert asyncio.run(fresh._revoke_left_faction_access(None, alert)) == []


def test_flytrack_uses_measured_speed_not_rounded_bucket():
    """
    Regression test for a real report: "he landed like 5 minute earlier than
    what was said". MegaBates had been MEASURED at 143 min to South Africa, but
    the ETA was computed from the WLT bucket (0.50 x 297 = 148.5 min) — so the
    bot ignored its own measurement and predicted 5.5 min late. Across 82 stored
    flights every single prediction ran late (avg 8 min, worst 24) for the same
    reason: each bucket is ~6% slower than reality.
    """
    from features.flytrack import FlyTracker, STANDARD_FLIGHT_MINUTES, DEFAULT_RATIO

    mgr = FlyTracker(bot=None)
    sa = STANDARD_FLIGHT_MINUTES['South Africa']  # 297
    start = 1_000_000

    # Exactly the stored production record that produced the complaint
    mgr.tiers['mega'] = {'tier': 'wlt', 'name': 'MegaBates',
                         'observed_minutes': 143.0, 'dest': 'South Africa'}

    eta = mgr._eta_ts('mega', 'South Africa', start)
    bucket_eta = int(start + sa * 0.50 * 60)          # what the old code gave
    measured_eta = int(start + 143.0 * 60)            # what we actually timed

    assert eta == measured_eta, 'must use the measured 143 min, not the bucket'
    assert eta < bucket_eta, 'measured time is earlier than the bucket predicted'
    assert round((bucket_eta - eta) / 60, 1) == 5.5, 'the exact 5.5 min the member saw'

    # A legacy record with no ratios list still yields a measured ratio
    ratio, measured = mgr._ratio_for('mega')
    assert measured is True and abs(ratio - 143.0 / sa) < 1e-9

    # Never-seen player falls back to the empirical median, not a bucket
    ratio_unknown, measured_unknown = mgr._ratio_for('nobody')
    assert measured_unknown is False and ratio_unknown == DEFAULT_RATIO

    # Learning stores the real ratio, and the median damps a single noisy leg
    mgr._learn_tier('p2', 'Flyer', 'South Africa', 143 * 60)
    mgr._learn_tier('p2', 'Flyer', 'South Africa', 145 * 60)
    mgr._learn_tier('p2', 'Flyer', 'South Africa', 144 * 60)
    assert len(mgr.tiers['p2']['ratios']) == 3
    # Ratios persist rounded to 4dp, so compare at that precision
    assert abs(mgr._ratio_for('p2')[0] - (144.0 / sa)) < 1e-3, 'median of the samples'

    # Physically implausible timings are still rejected as glitches
    mgr._learn_tier('p3', 'Glitch', 'South Africa', 5 * 60)
    assert 'p3' not in mgr.tiers


def test_flytrack_eta_window_biases_early():
    """
    Landing times show as a window whose EARLY edge is the "be ready by" moment.
    The error is asymmetric: an enemy landing sooner than advertised gets people
    hit, landing later just costs a short wait. A measured player gets a tighter
    window than one we are only guessing at.
    """
    from features.flytrack import FlyTracker, POLL_LAG_SECONDS

    mgr = FlyTracker(bot=None)
    start = 1_000_000
    mgr.tiers['known'] = {'tier': 'wlt', 'name': 'Known',
                          'observed_minutes': 143.0, 'dest': 'South Africa'}

    eta_known = mgr._eta_ts('known', 'South Africa', start)
    early_known = mgr._eta_window('known', 'South Africa', eta_known)
    eta_guess = mgr._eta_ts('guess', 'South Africa', start)
    early_guess = mgr._eta_window('guess', 'South Africa', eta_guess)

    assert early_known < eta_known, 'the window opens before the expected landing'
    assert (eta_known - early_known) >= POLL_LAG_SECONDS, 'must cover the poll lag'
    # Guessed players get a wider window than measured ones
    assert (eta_guess - early_guess) > (eta_known - early_known)

    # No ETA (unknown destination) -> no window, and no crash
    assert mgr._eta_window('known', None, None) is None
    assert mgr._eta_window('known', 'Nowhere', 12345) is None

    # Rendering: the countdown tracks the EARLY edge, never the optimistic one
    text = mgr._eta_text(eta_known, early_known)
    assert f'<t:{early_known}:R>' in text and f'<t:{eta_known}:t>' in text
    # Without an early edge it degrades to a single time
    assert mgr._eta_text(eta_known, None) == f'<t:{eta_known}:t> (<t:{eta_known}:R>)'


def test_finished_war_stats_stop_at_the_war_end():
    """
    Measured on war 50338 after it ended: the attacks log reported 422 war hits
    against the official report's 421. The window had no upper bound, so a
    member hitting the former opponent days later still counted as a war hit.

    Left alone the gap widens, and /war view:Xan reimburses more Xanax the
    longer you wait to run it. An ONGOING war passes war_end=None and must
    stay unbounded.
    """
    import asyncio
    from features.wars import WarTracker
    import features.wars as wars_mod

    mgr = WarTracker(bot=None)
    seen = []

    async def record(from_ts=None, to_ts=None, sort=None):
        seen.append((from_ts, to_ts))
        return {}

    orig = wars_mod.fetch_faction_attacks
    wars_mod.fetch_faction_attacks = record
    try:
        asyncio.run(mgr._fetch_all_attacks_since(1791028800, 1791194409))
        assert seen[0] == (1791028800, 1791194409),             f'a finished war must bound the query at its end, got {seen[0]}'

        seen.clear()
        asyncio.run(mgr._fetch_all_attacks_since(1791028800))
        assert seen[0] == (1791028800, None),             f'an ongoing war must stay unbounded, got {seen[0]}'
    finally:
        wars_mod.fetch_faction_attacks = orig


def test_attacks_shape_change_is_not_read_as_no_attacks():
    """
    The outage door closed, so this checks the shape door. The paginator stops
    on an empty page, so if Torn turned `attacks` into a list — which it has
    already done once, to rankedwarreport's factions and members — degrading
    to {} would truncate the war log exactly as an outage used to.

    Torn really does send {"attacks": {}} for "none" (probed live), so an empty
    dict stays the legitimate empty answer.
    """
    import asyncio
    import utils.torn_api as torn_api_mod

    orig = torn_api_mod._make_api_request
    try:
        async def listed(*a, **k):
            return {'attacks': [{'attacker_id': 1}, {'attacker_id': 2}]}
        torn_api_mod._make_api_request = listed
        assert asyncio.run(torn_api_mod.fetch_faction_attacks()) is None,             'a populated list is a shape change, not "no attacks"'

        async def empty_dict(*a, **k):
            return {'attacks': {}}
        torn_api_mod._make_api_request = empty_dict
        assert asyncio.run(torn_api_mod.fetch_faction_attacks()) == {},             'what Torn actually sends for none must stay the empty answer'
    finally:
        torn_api_mod._make_api_request = orig


def test_fetch_faction_attacks_reports_outage_as_none():
    """
    The contract at the source. fetch_faction_attacks returned {} for both "the
    API failed" and "there are no attacks", and its only caller stops paging on
    an empty page - so an outage mid-pagination silently truncated the war log.

    Caught by mutation-testing the layer above: faking the fetch in those tests
    meant reverting this helper broke nothing, so it needs pinning here.
    """
    import asyncio
    import utils.torn_api as torn_api_mod

    orig = torn_api_mod._make_api_request
    try:
        async def failed(*a, **k):
            return None
        torn_api_mod._make_api_request = failed
        assert asyncio.run(torn_api_mod.fetch_faction_attacks()) is None,             'an outage must be None, never an empty dict'

        async def empty(*a, **k):
            return {'attacks': {}}
        torn_api_mod._make_api_request = empty
        assert asyncio.run(torn_api_mod.fetch_faction_attacks()) == {},             'a genuine empty answer stays an empty dict'

        async def wrong_shape(*a, **k):
            return {'attacks': []}
        torn_api_mod._make_api_request = wrong_shape
        assert asyncio.run(torn_api_mod.fetch_faction_attacks()) == {},             'an unexpected shape degrades to empty, not a crash'
    finally:
        torn_api_mod._make_api_request = orig


def test_attack_log_outage_is_not_a_quiet_war():
    """
    Re-audit find, and the most dangerous one yet because it corrupts the
    permanent record.

    fetch_faction_attacks returned {} on API failure, and the paginator stops
    when a page comes back empty. So one transient 504 on page 3 of 15 ended
    pagination early and the result looked like a complete, much quieter war.
    At war end those numbers are archived, where no later fix can reach them.

    An unreadable log must now surface as None all the way up, and the archive
    must store "unknown" rather than a zero that is indistinguishable from a
    member nobody touched.
    """
    import asyncio
    from features.wars import WarTracker
    import features.wars as wars_mod

    mgr = WarTracker(bot=None)

    # --- the paginator refuses to return a truncated log ---
    pages = {'n': 0}

    async def flaky(from_ts=None, to_ts=None, sort=None):
        pages['n'] += 1
        if pages['n'] == 1:
            return {str(i): {'timestamp_started': 1000 + i} for i in range(100)}
        return None  # every retry of page 2 fails

    orig_fetch = wars_mod.fetch_faction_attacks
    orig_sleep = asyncio.sleep
    wars_mod.fetch_faction_attacks = flaky

    async def no_sleep(_s):
        await orig_sleep(0)
    asyncio.sleep = no_sleep
    try:
        assert asyncio.run(mgr._fetch_all_attacks_since(1000)) is None, \
            'a partial log must report unknown, not pose as a complete one'
        assert asyncio.run(mgr._get_war_attack_stats('53421', 1000)) is None, \
            'the None must propagate, not degrade into empty stats'
    finally:
        wars_mod.fetch_faction_attacks = orig_fetch
        asyncio.sleep = orig_sleep


def test_attack_log_paging_does_not_skip_the_boundary_second():
    """
    `from` is inclusive, so stepping to latest_timestamp + 1 skipped every
    attack sharing that second with the page's last record - routine during a
    chain, where several land per second. Re-reading the boundary is free
    because attacks dedupe by id.
    """
    import asyncio
    from features.wars import WarTracker
    import features.wars as wars_mod

    mgr = WarTracker(bot=None)

    # 100 attacks ending at t=1050, plus two MORE also at t=1050 that only a
    # second page can reach.
    page1 = {str(i): {'timestamp_started': 1000 + min(i, 50)} for i in range(100)}
    tail = {'900': {'timestamp_started': 1050}, '901': {'timestamp_started': 1050}}

    seen_from = []

    async def paged(from_ts=None, to_ts=None, sort=None):
        seen_from.append(from_ts)
        if from_ts <= 1000:
            return page1
        return {**{k: v for k, v in page1.items() if v['timestamp_started'] >= from_ts}, **tail}

    orig = wars_mod.fetch_faction_attacks
    wars_mod.fetch_faction_attacks = paged
    try:
        all_attacks = asyncio.run(mgr._fetch_all_attacks_since(1000))
        assert all_attacks is not None
        assert 1050 in seen_from, f'must re-read the boundary second, saw {seen_from}'
        assert '900' in all_attacks and '901' in all_attacks, \
            'attacks sharing the boundary second were skipped'
        assert len(all_attacks) == 102, len(all_attacks)
    finally:
        wars_mod.fetch_faction_attacks = orig


def test_war_end_survives_an_unreadable_attack_log():
    """
    Introduced while adding the None contract above: the war-end handler still
    called attack_stats.get('_friendly_hosps'), which raises AttributeError on
    None and would take down the whole war-end post - reports, history and all.

    Also pins that the archive records unknown rather than zero.
    """
    import asyncio
    from features.wars import WarTracker
    import features.wars as wars_mod
    import utils.torn_api as torn_api_mod
    from config import TORN_FACTION_ID

    mgr = WarTracker(bot=None)
    mgr.war_member_stats = {'wars': []}
    saved = {}
    orig_save = wars_mod.save_json
    wars_mod.save_json = lambda path, data: saved.update(data=data)

    async def fake_members():
        return {'111': {'name': 'Tank'}}
    orig_members = torn_api_mod.fetch_faction_members
    torn_api_mod.fetch_faction_members = fake_members

    report_factions = {
        str(TORN_FACTION_ID): {'members': {'111': {'attacks': 79, 'score': 384.0}}},
    }
    try:
        # attack_stats=None is exactly what an unreadable log now yields
        asyncio.run(mgr._save_war_member_data(
            50338, report_factions, 1952, 5290, 'Them',
            friendly_hosps=[], attack_stats=None))

        rec = saved['data']['wars'][0]['members']['111']
        assert rec['defends'] is None, \
            'an unreadable log must archive unknown, not a permanent false zero'
        assert rec['attacks'] == 79, 'report-sourced data is still trustworthy'

        # ...and the history view shows that as "?" rather than 0
        mgr.war_member_stats = {'wars': [{
            'enemy_name': 'Them', 'result': 'defeat', 'timestamp': 1_790_000_000,
            'members': {'111': {'name': 'Tank', 'attacks': 79, 'defends': None,
                                'score': 384.0, 'hosps_given': 0, 'hosps_received': 0}},
        }]}
        embed = asyncio.run(mgr.format_warstats_member_embed('111', 'Tank'))
        body = (embed.description or '') + ''.join(f.value for f in embed.fields)
        assert 'Defends: ?' in body, body
    finally:
        wars_mod.save_json = orig_save
        torn_api_mod.fetch_faction_members = orig_members


def test_saved_war_history_records_real_hits_taken():
    """
    The audit's worst find, because it is the only one that is PERMANENT.

    _save_war_member_data wrote `defends` straight from the ranked war report,
    which v2 no longer carries - so every future war would have recorded "0
    hits taken" into war history. The display bugs could be fixed after the
    fact; this one could not, because the attacks log is only queryable for so
    long. Once a war is archived with zeros, the real numbers are gone.

    The stored key keeps the name `defends` so records written before this
    still render in the per-member history view.
    """
    import asyncio
    from features.wars import WarTracker
    import features.wars as wars_mod
    import utils.torn_api as torn_api_mod
    from config import TORN_FACTION_ID

    mgr = WarTracker(bot=None)
    mgr.war_member_stats = {'wars': []}

    saved = {}

    def fake_save(path, data):
        saved['data'] = data
    orig_save = wars_mod.save_json
    wars_mod.save_json = fake_save

    async def fake_members():
        return {'111': {'name': 'Tank'}}
    orig_members = torn_api_mod.fetch_faction_members
    torn_api_mod.fetch_faction_members = fake_members

    # v2 report shape: attacks and score, but no `defends` anywhere
    report_factions = {
        str(TORN_FACTION_ID): {'members': {'111': {'attacks': 79, 'score': 384.0}}},
    }
    attack_stats = {
        '111': {'total_incoming': 71, 'war_incoming': 68, 'outside_incoming': 3},
        '_friendly_hosps': [],
    }

    try:
        asyncio.run(mgr._save_war_member_data(
            50338, report_factions, 1952, 5290, 'The Naughty Kids',
            friendly_hosps=[], attack_stats=attack_stats))

        wars = saved['data']['wars'] if 'wars' in saved['data'] else saved['data']
        rec = None
        for w in (wars.values() if isinstance(wars, dict) else wars):
            if isinstance(w, dict) and '111' in (w.get('members') or {}):
                rec = w['members']['111']
                break
        assert rec is not None, f'no member record saved: {saved["data"]!r}'

        assert rec['defends'] == 68, \
            f'war history must archive real hits taken, got {rec["defends"]}'
        assert rec['outside_incoming'] == 3
        assert rec['attacks'] == 79, 'outgoing still comes from the report'
    finally:
        wars_mod.save_json = orig_save
        torn_api_mod.fetch_faction_members = orig_members


def test_incoming_split_does_not_dump_war_hits_into_outside():
    """
    War 50338's participation board read "War Incoming 0 / Outside Incoming
    1005" - every single hit taken filed as an outsider, in the middle of a
    ranked war lost 1952-5290.

    Cause: outside was derived as `total_incoming - defends`, and v2 dropped
    `defends`. .get('defends', 0) cannot tell a missing field from a real zero,
    so the subtraction handed the entire total to the outside bucket.

    The true split for that war was 931 war / 74 outside.
    """
    from features.wars import WarTracker

    split = WarTracker._incoming_split

    # v2 report: no `defends` at all -> trust the attacks log's own split
    war_inc, outside_inc = split({'attacks': 79}, {
        'total_incoming': 1005, 'war_incoming': 931, 'outside_incoming': 74})
    assert (war_inc, outside_inc) == (931, 74), \
        'war hits must not be dumped into the outside bucket'

    # v1-era cached report: `defends` is authoritative, outside is the remainder
    war_inc, outside_inc = split({'defends': 900}, {
        'total_incoming': 1005, 'war_incoming': 931, 'outside_incoming': 74})
    assert (war_inc, outside_inc) == (900, 105), 'a real defends value still wins'

    # A genuine zero is NOT the same as a missing field
    war_inc, outside_inc = split({'defends': 0}, {'total_incoming': 12})
    assert (war_inc, outside_inc) == (0, 12), 'defends=0 means truly never hit in war'

    # No log and no defends -> zeros, not a crash
    assert split({}, {}) == (0, 0)


def test_strike_candidates_feed_the_ledger_with_the_right_types():
    """
    Every tier the attacks log can settle has to reach the ledger as its OWN
    candidate, typed, so "how many strikes does X have" is answerable and so a
    dismissal can be remembered per thing rather than per war.

    The context is the war id on purpose: the same member being absent again
    next war is a NEW candidate, not a duplicate of the one dismissed last time.
    """
    import asyncio
    from features.wars import WarTracker, WAR_OFFLINE_STRIKE_HOURS
    import utils.torn_api as torn_api_mod
    from config import TORN_FACTION_ID

    got = []

    class FakeLedger:
        async def post_candidate(self, tid, name, stype, reason, context='', channel=None):
            got.append((str(tid), stype, context))
            return True

    class FakeChannel:
        async def send(self, **kw):
            pass

    class FakeBot:
        def get_channel(self, _):
            return FakeChannel()

    class FakeAvail:
        polls = {'p1': {'war_id': '777'}}
        data = {'p1': {'0': [555]}}        # discord 555 answered

    class FakeVS:
        verified_users = {'555': {'torn_id': 100}}

    mgr = WarTracker(bot=FakeBot())
    mgr.strike_manager = FakeLedger()
    mgr.availability_manager = FakeAvail()
    mgr.verification_system = FakeVS()
    over = (WAR_OFFLINE_STRIKE_HOURS + 6) * 3600
    mgr.war_offline = {'100': over, '200': over}

    async def fake_members():
        return {str(i): {'name': f'M{i}'} for i in (100, 200, 300, 400, 500)}
    orig = torn_api_mod.fetch_faction_members
    torn_api_mod.fetch_faction_members = fake_members

    attack_stats = {
        '400': {'mugs_faction': 2, 'mugs_given': 5},
        '_bonus_outside': [{'attacker_id': '500', 'chain': 25,
                            'defender_name': 'Rando', 'defender_faction': ''}],
        '_friendly_hosps': [],
    }
    report_factions = {str(TORN_FACTION_ID): {'members': {
        '100': {'attacks': 0}, '300': {'attacks': 0}, '400': {'attacks': 9}}}}

    try:
        asyncio.run(mgr.post_strike_candidates('777', attack_stats, report_factions))
    finally:
        torn_api_mod.fetch_faction_members = orig

    kinds = {(t, k) for t, k, _ in got}
    assert ('400', 'mug_faction') in kinds, kinds
    assert ('400', 'mug_opp') in kinds, kinds
    assert ('500', 'bonus') in kinds, kinds
    assert ('200', 'war_offline') in kinds, 'absent, no poll answer'
    assert ('100', 'war_offline') not in kinds, 'answering the poll is the notice'
    assert ('300', 'war_nohits') in kinds, 'zero attacks, no poll answer'
    assert ('100', 'war_nohits') not in kinds, 'they answered the poll'

    assert all(c == '777' for _t, _k, c in got), (
        f'every candidate must be scoped to the war: {got}')


def test_strike_candidates_still_post_without_a_ledger():
    """
    The ledger is wired in bot.py after the war tracker exists, and this repo
    has already shipped a bug where that order was wrong and a manager bound
    None. A war report is archived permanently, so losing it because the
    ledger was missing would be unrecoverable - it falls back to one embed
    and says so, instead of silently posting nothing.
    """
    import asyncio
    from features.wars import WarTracker
    import utils.torn_api as torn_api_mod
    from config import TORN_FACTION_ID

    sent = []

    class FakeChannel:
        async def send(self, **kw):
            sent.append(kw.get('embed'))

    class FakeBot:
        def get_channel(self, _):
            return FakeChannel()

    mgr = WarTracker(bot=FakeBot())      # NO strike_manager attribute
    mgr.war_offline = {}

    async def fake_members():
        return {str(500 + i): {'name': f'ballcrusher30{i:02d}XX'} for i in range(30)}
    orig = torn_api_mod.fetch_faction_members
    torn_api_mod.fetch_faction_members = fake_members
    try:
        asyncio.run(mgr.post_strike_candidates(
            '777',
            {'_bonus_outside': [{'attacker_id': str(500 + i), 'chain': 1000,
                                 'defender_name': 'SomeLongTargetName',
                                 'defender_faction': 'Kentucky Fried Criminals'}
                                for i in range(30)],
             '_friendly_hosps': []},
            {str(TORN_FACTION_ID): {'members': {}}}))
    finally:
        torn_api_mod.fetch_faction_members = orig

    assert len(sent) == 1, 'the report must still go out'
    assert len(sent[0].description) <= 4096, (
        f'embed description is {len(sent[0].description)}, Discord rejects over 4096')


def test_war_report_counts_hits_taken_from_attack_log():
    """
    War 50338's management report said "Total Times Hit: 0" for every member
    after a 1952-5290 defeat. Cause: it read the ranked war report's `defends`
    field, which Torn dropped when it retired v1 - so it was absent, and
    .get('defends', 0) turned an outage into a confident zero.

    The attacks log has the real numbers (1005 hits taken, 931 of them war),
    and cross-checks: its war-hit count matched the report's "Total Attacks
    Made: 421" exactly. The log is now the source, with `defends` kept only as
    a fallback for cached v1-era reports.
    """
    import asyncio
    from features.wars import WarTracker
    import utils.torn_api as torn_api_mod
    from config import TORN_FACTION_ID

    mgr = WarTracker(bot=None)
    members = {'111': {'name': 'Tank'}, '222': {'name': 'Glass'}}

    async def fake_members():
        return members
    orig = torn_api_mod.fetch_faction_members
    torn_api_mod.fetch_faction_members = fake_members

    final_data = {
        'war_id': 50338,
        'factions': {
            # v2 shape: `defends` simply isn't here any more
            str(TORN_FACTION_ID): {
                'name': 'Us', 'score': 1952,
                'members': {'111': {'attacks': 79, 'score': 384.0},
                            '222': {'attacks': 0, 'score': 0}},
            },
            '53421': {'name': 'The Naughty Kids', 'score': 5290, 'members': {}},
        },
    }

    try:
        # Without the log the old bug reproduces: everyone reads 0 hits taken
        res = asyncio.run(mgr.format_war_management_report(None, final_data=final_data))
        summary = next(f.value for f in res[0].fields if 'Summary' in f.name)
        assert 'Total Times Hit:** 0' in summary, 'no log -> nothing to report'

        # With the log, real counts appear
        attack_stats = {
            '111': {'war_incoming': 68, 'outside_incoming': 3},
            '222': {'war_incoming': 12, 'outside_incoming': 0},
            '_friendly_hosps': [],
        }
        res = asyncio.run(mgr.format_war_management_report(
            None, final_data=final_data, attack_stats=attack_stats))
        fields = {f.name: f.value for f in res[0].fields}
        summary = next(v for k, v in fields.items() if 'Summary' in k)

        assert 'Total Times Hit:** 80' in summary, summary
        assert 'outside the war' in summary, 'outside hits must not inflate the war number'
        assert 'Total Attacks Made:** 79' in summary, 'outgoing still comes from the report'

        detail = next(v for k, v in fields.items() if 'Member' in k)
        assert 'Hit: 68x' in detail, detail

        # The "most targeted" list was dead while every count was zero
        targeted = next((v for k, v in fields.items() if 'Targeted' in k), '')
        assert 'Tank' in targeted and '68' in targeted, targeted
    finally:
        torn_api_mod.fetch_faction_members = orig


def test_xan_counts_friendly_hosps():
    """
    Friendly hosps are reimbursed like any other hit: they cost the same energy
    and are thrown on instruction to put a teammate out of reach.

    The tricky part is double-counting. _get_war_attack_stats matches a friendly
    hosp FIRST and `continue`s, so it never lands in war_hits or outside/chain
    hits; the ranked war report likewise only counts attacks on the war
    opponent. This test pins both halves: the hosp is added exactly once, and a
    member who ONLY threw hosps still shows up (they used to be invisible,
    because the list is filtered on total > 0).
    """
    import asyncio
    from features.wars import WarTracker
    import utils.torn_api as torn_api_mod
    from config import TORN_FACTION_ID

    mgr = WarTracker(bot=None)

    members = {'111': {'name': 'Fighter'}, '222': {'name': 'Medic'}, '333': {'name': 'Idle'}}

    async def fake_members():
        return members
    orig_members = torn_api_mod.fetch_faction_members
    torn_api_mod.fetch_faction_members = fake_members

    async def no_report(war_id=None):
        return {}
    mgr._fetch_rankedwarreport = no_report

    async def fake_stats(enemy_faction_id, war_start, war_end=None):
        return {
            # 10 war hits, 2 chain-outside, 1 hosp -> 13
            '111': {'war_hits': 10, 'chain_outside_hits': 2, 'hosps_given': 1},
            # hosps ONLY - must still appear
            '222': {'war_hits': 0, 'chain_outside_hits': 0, 'hosps_given': 4},
            # nothing at all - must stay out
            '333': {'war_hits': 0, 'chain_outside_hits': 0, 'hosps_given': 0},
            '_friendly_hosps': [],
        }
    mgr._get_war_attack_stats = fake_stats

    try:
        war_info = {
            'war_id': 999,
            'is_active': True,
            'data': {
                'war': {'start': 1_000_000},
                'factions': {
                    str(TORN_FACTION_ID): {'name': 'Us', 'score': 100},
                    '53421': {'name': 'Them', 'score': 50},
                },
            },
        }
        result = asyncio.run(mgr.format_xan_embed(war_info))
        embed = result[0] if isinstance(result, list) else result

        fields = {f.name: f.value for f in embed.fields}
        assert fields['War Hits'] == '10'
        assert fields['Chain Outside'] == '2'
        assert fields['Friendly Hosps'] == '5', 'hosps are totalled across members'
        assert fields['Total'] == '17', 'war + chain + hosps, each counted once'

        table = next(v for k, v in fields.items() if k.startswith('Members'))
        assert 'Hsp' in table, 'the table needs its own hosps column'
        assert 'Medic' in table, 'a hosps-only member must not be filtered out'
        assert 'Idle' not in table, 'someone with no contribution stays off the list'

        # Fighter's row: 10 war, 2 chain, 1 hosp, 13 total - all on one line
        row = next(ln for ln in table.split('\n') if 'Fighter' in ln)
        assert row.split()[-4:] == ['10', '2', '1', '13'], row
    finally:
        torn_api_mod.fetch_faction_members = orig_members


def test_war_report_v2_shape_normalised():
    """
    Found in production logs: Torn retired `rankedwarreport` from API v1, which
    now answers "error 23: only available in API v2", so the per-member war
    report was silently coming back empty ("No data from API and no cache").

    v2 also changed shape — factions and members are LISTS where v1 used dicts
    keyed by id. Consumers here call .items() on factions and index members by
    user id, so the payload must be normalised at the boundary.
    """
    from features.wars import WarTracker

    # Exactly the shape the live v2 endpoint returns
    v2 = {
        'id': 50398, 'start': 1790938800, 'end': 0, 'winner': None, 'forfeit': False,
        'factions': [
            {'id': 45635, 'name': 'Ascended Punks', 'score': 1200, 'rank': 'Gold',
             'attacks': 300, 'rewards': {},
             'members': [
                 {'id': 3571142, 'name': 'Ikaros22', 'level': 67, 'attacks': 40, 'score': 210},
                 {'id': 2094609, 'name': 'Someone', 'level': 30, 'attacks': 12, 'score': 66},
             ]},
            {'id': 53421, 'name': 'The Naughty Kids', 'score': 900, 'rank': 'Gold',
             'attacks': 250, 'rewards': {}, 'members': []},
        ],
    }

    out = WarTracker._normalise_rankedwarreport(v2)
    factions = out['factions']
    assert isinstance(factions, dict), 'factions must be keyed by id for .items() consumers'
    assert set(factions) == {'45635', '53421'}, 'faction ids become string keys'

    members = factions['45635']['members']
    assert isinstance(members, dict), 'members must be keyed by user id'
    assert members['3571142']['name'] == 'Ikaros22'
    assert members['3571142']['attacks'] == 40
    # Fields consumers read survive; defends is absent in v2 and must default safely
    assert members['3571142'].get('defends', 0) == 0
    assert factions['45635']['score'] == 1200, 'faction-level fields are preserved'
    assert factions['53421']['members'] == {}, 'an empty member list becomes an empty dict'
    assert out['id'] == 50398, 'top-level report fields are preserved'

    # A v1-shaped payload (or a report cached before this change) passes through
    v1 = {'factions': {'45635': {'name': 'X', 'members': {'1': {'attacks': 2}}}}}
    assert WarTracker._normalise_rankedwarreport(v1) == v1

    # Junk must not raise
    assert WarTracker._normalise_rankedwarreport({}) == {}
    assert WarTracker._normalise_rankedwarreport(None) is None
    assert WarTracker._normalise_rankedwarreport(
        {'factions': [{'name': 'no id'}, 'garbage']})['factions'] == {}


def test_war_report_rejects_another_factions_war():
    """
    The v2 path id is a WAR id, not a faction id — and passing the wrong one
    does not error, it returns somebody else's war. Verified live: asking with
    faction id 45635 returned war 45635, a Nirvana vs Unicorn Apocalypse match
    this faction was never in. Importing that as our own war history would be
    worse than the broken state it replaced, so the report is checked for our
    faction before it is accepted.
    """
    import asyncio
    import features.wars as wars_mod
    from features.wars import WarTracker
    from config import TORN_FACTION_ID

    foreign = {'rankedwarreport': {
        'id': 45635,
        'factions': [
            {'id': 49837, 'name': 'Nirvana', 'score': 2565, 'members': []},
            {'id': 51559, 'name': 'Unicorn Apocalypse', 'score': 10867, 'members': []},
        ],
    }}
    ours = {'rankedwarreport': {
        'id': 47427,
        'factions': [
            {'id': int(TORN_FACTION_ID), 'name': 'Ascended Punks', 'score': 100,
             'members': [{'id': 1, 'name': 'A', 'attacks': 2, 'score': 5}]},
            {'id': 50426, 'name': 'Opponent', 'score': 90, 'members': []},
        ],
    }}

    tracker = WarTracker(bot=None)
    calls = []
    orig_fetch = wars_mod.fetch_faction_data_v2
    orig_save = wars_mod.save_json
    orig_load = wars_mod.load_json
    wars_mod.save_json = lambda *a, **k: None
    wars_mod.load_json = lambda *a, **k: {}          # no cache to fall back on
    try:
        async def fake_fetch(selections, path_id=None):
            calls.append(path_id)
            return foreign
        wars_mod.fetch_faction_data_v2 = fake_fetch
        assert asyncio.run(tracker._fetch_rankedwarreport(war_id=47427)) is None, \
            "a report that doesn't include our faction must be rejected"
        assert calls == [47427], 'the WAR id must be sent as the path id, not the faction id'

        # Without a war id we must not ask at all — asking with the faction id
        # is exactly what returned a stranger's war.
        calls.clear()
        assert asyncio.run(tracker._fetch_rankedwarreport(war_id=None)) is None
        assert calls == [], 'no war id means no API call'

        # Our own war is accepted and normalised
        async def fake_ours(selections, path_id=None):
            return ours
        wars_mod.fetch_faction_data_v2 = fake_ours
        got = asyncio.run(tracker._fetch_rankedwarreport(war_id=47427))
        assert got and str(TORN_FACTION_ID) in got['factions']
        assert got['factions'][str(TORN_FACTION_ID)]['members']['1']['attacks'] == 2
    finally:
        wars_mod.fetch_faction_data_v2 = orig_fetch
        wars_mod.save_json = orig_save
        wars_mod.load_json = orig_load


# ===== Follow warnings (enemy flying to a country we are sitting in) =====

def _fly_mgr(own_roster, enemy_roster, ff=None, ff_key=''):
    """Build a FlyTracker wired to fake rosters.

    Both the enemy roster and our own come from the same
    fetch_any_faction_data helper, so the fake dispatches on faction id.
    Returns (manager, posted_list, restore_callable).
    """
    import features.flytrack as fly_mod
    from features.flytrack import FlyTracker
    from config import TORN_FACTION_ID

    posted = []

    class FakeVS:
        # Torn 100 -> Discord 555; Torn 101 deliberately absent (unregistered)
        verified_users = {'555': {'torn_id': 100}}

    mgr = FlyTracker(bot=None, war_tracker=None)
    mgr.enabled = True
    mgr.enemy_faction_id = '999'
    mgr.verification_system = FakeVS()

    async def fake_post(content=None, embed=None):
        posted.append(content if content is not None else embed)
    mgr._post = fake_post

    async def fake_fetch(faction_id, selections='basic', **kw):
        if str(faction_id) == str(TORN_FACTION_ID):
            return own_roster if own_roster is None else {'members': own_roster}
        return {'members': enemy_roster}

    async def fake_ff(ids):
        return ff or {}

    orig = (fly_mod.fetch_any_faction_data, fly_mod.fetch_ffscouter_stats,
            fly_mod.save_json, fly_mod.FFSCOUTER_API_KEY)
    fly_mod.fetch_any_faction_data = fake_fetch
    fly_mod.fetch_ffscouter_stats = fake_ff
    fly_mod.save_json = lambda *a, **k: None
    fly_mod.FFSCOUTER_API_KEY = ff_key

    def restore():
        (fly_mod.fetch_any_faction_data, fly_mod.fetch_ffscouter_stats,
         fly_mod.save_json, fly_mod.FFSCOUTER_API_KEY) = orig

    return mgr, posted, restore


def _abroad(name, country):
    return {'name': name, 'status': {'state': 'Abroad', 'description': f'In {country}'}}


def _flying_to(name, country):
    return {'name': name, 'status': {'state': 'Traveling',
            'description': f'Traveling from Torn to {country}'}}


def _home(name):
    return {'name': name, 'status': {'state': 'Okay', 'description': 'Okay'}}


def test_follow_warning_pings_only_the_member_in_that_country():
    """
    The feature SirAua asked for: warn a member when an enemy takes off toward
    the country they are sitting in.

    Three things have to hold or it's either useless or spam:
      - only the member actually IN that country is pinged, by Discord mention
      - enemies departing in the same tick are ONE message, not one each
      - a departure toward an empty country produces nothing at all
    """
    import asyncio

    own = {
        '100': _abroad('OurGuy', 'Switzerland'),
        '101': _flying_to('MidAir', 'Switzerland'),  # not landed -> not "sitting"
    }
    enemy_home = {'200': _home('EnemyA'), '201': _home('EnemyB'), '202': _home('EnemyC')}
    mgr, posted, restore = _fly_mgr(own, enemy_home)
    try:
        # First tick baselines everyone silently
        asyncio.run(mgr.check_flights())
        assert posted == [], 'baseline tick must not alert'

        # Two enemies head for Switzerland (where OurGuy is), one for Mexico
        enemy_home['200'] = _flying_to('EnemyA', 'Switzerland')
        enemy_home['201'] = _flying_to('EnemyB', 'Switzerland')
        enemy_home['202'] = _flying_to('EnemyC', 'Mexico')
        asyncio.run(mgr.check_flights())

        follow = [p for p in posted if isinstance(p, str) and 'where you are' in p]
        assert len(follow) == 1, f'expected one grouped warning, got {len(follow)}'
        msg = follow[0]
        assert '<@555>' in msg, 'must ping the member via their Discord account'
        assert 'Switzerland' in msg
        assert 'EnemyA' in msg and 'EnemyB' in msg, 'both same-tick departures in one message'
        assert 'EnemyC' not in msg, 'Mexico has nobody of ours - no warning'
        assert 'MidAir' not in msg, 'still in the air, not sitting in Switzerland'
    finally:
        restore()


def test_follow_warning_tells_unregistered_members_how_to_fix_it():
    """
    An unregistered member (Kiwisupercar, live) can't be mentioned, so the
    warning is posted but notifies nobody. Rather than escalating to a role
    ping - which would route their alerts through someone else indefinitely -
    the message tells them how to get pinged themselves.

    Registered members must NOT see the hint; it would be noise on every alert.
    """
    import asyncio

    # Torn 100 is registered in the fake verification db, 101 is not
    own = {'100': _abroad('Registered', 'Switzerland'),
           '101': _abroad('Unregistered', 'Switzerland')}
    mgr, posted, restore = _fly_mgr(own, {})
    try:
        deps = [{'tid': '200', 'name': 'EnemyA', 'dest': 'Switzerland',
                 'eta': 1_000_500, 'early': 1_000_400}]
        msgs = asyncio.run(mgr._follow_alerts(deps, 1_000_000))
        assert len(msgs) == 2

        pinged = [m for m in msgs if '<@555>' in m]
        named = [m for m in msgs if 'not registered' in m]
        assert len(pinged) == 1 and len(named) == 1

        assert '/register' in named[0], 'tell them how to get pinged'
        assert named[0].startswith('\u26a0\ufe0f'), 'hint must not displace the warning'
        assert '/register' not in pinged[0], 'registered members do not need the hint'
    finally:
        restore()


def test_follow_warning_cooldown_suppresses_repeats():
    """
    Ikaros's objection was noise. Once someone has been told enemies are heading
    to Switzerland, saying it again minutes later adds nothing - they already
    know to watch that country.
    """
    import asyncio
    from features.flytrack import FOLLOW_ALERT_COOLDOWN_SECONDS

    own = {'100': _abroad('OurGuy', 'Switzerland')}
    mgr, posted, restore = _fly_mgr(own, {})
    try:
        deps = [{'tid': '200', 'name': 'EnemyA', 'dest': 'Switzerland',
                 'eta': 1_000_500, 'early': 1_000_400}]

        first = asyncio.run(mgr._follow_alerts(deps, 1_000_000))
        assert len(first) == 1, 'first warning must go out'

        again = asyncio.run(mgr._follow_alerts(deps, 1_000_060))
        assert again == [], 'inside the cooldown -> silent'

        later = asyncio.run(mgr._follow_alerts(
            deps, 1_000_000 + FOLLOW_ALERT_COOLDOWN_SECONDS + 1))
        assert len(later) == 1, 'past the cooldown it warns again'
    finally:
        restore()


def test_follow_warning_splits_stronger_from_weaker():
    """
    Enemies are banded against the stats of the member being warned, because
    "strong" has no absolute meaning - measured live, PUAD (~817k) was a real
    threat to Trizzy973 (516k) and irrelevant to TrevorLahey420 (7.5m) standing
    in the same country.

    Banding and the decision to send are deliberately separate: a trivial enemy
    rides along in a message a real threat already triggered (free context), but
    can never cause one on its own. A missing estimate fails OPEN.
    """
    import asyncio

    own = {'100': _abroad('OurGuy', 'Switzerland')}
    ff = {
        '100': {'bs_estimate': 10_000_000_000},  # our member
        '200': {'bs_estimate': 1_000_000_000},   # 0.1x - trivial
        '201': {'bs_estimate': 20_000_000_000},  # 2x    - stronger
        '203': {'bs_estimate': 6_000_000_000},   # 0.6x  - weaker, still worth knowing
        # 202 absent from FFScouter entirely -> unknown, must trigger
    }
    mgr, posted, restore = _fly_mgr(own, {}, ff=ff, ff_key='test-key')
    try:
        deps = [
            {'tid': '200', 'name': 'Weakling', 'dest': 'Switzerland', 'eta': 1_000_500, 'early': 1_000_400},
            {'tid': '201', 'name': 'Monster', 'dest': 'Switzerland', 'eta': 1_000_600, 'early': 1_000_500},
            {'tid': '202', 'name': 'Nameless', 'dest': 'Switzerland', 'eta': 1_000_700, 'early': 1_000_600},
            {'tid': '203', 'name': 'Middling', 'dest': 'Switzerland', 'eta': 1_000_800, 'early': 1_000_700},
        ]
        msgs = asyncio.run(mgr._follow_alerts(deps, 1_000_000))
        assert len(msgs) == 1
        msg = msgs[0]
        assert 'Stronger than you' in msg and 'Weaker than you' in msg, \
            'the faction asked for the two groups to be separated in the text'
        assert 'Unknown strength' in msg, 'no estimate fails open into its own band'
        assert '~20.0b' in msg, 'the estimate is shown so the member can judge'

        # Each enemy must land under the right heading
        strong_at = msg.index('Stronger than you')
        unknown_at = msg.index('Unknown strength')
        weak_at = msg.index('Weaker than you')
        assert strong_at < msg.index('Monster') < unknown_at, 'Monster is the strong one'
        assert unknown_at < msg.index('Nameless') < weak_at, 'no estimate -> Unknown'
        assert msg.index('Middling') > weak_at and msg.index('Weakling') > weak_at, \
            'both below-par enemies sit under Weaker'

        # The member must not be named twice: the mention already renders as
        # their nickname, which carries the Torn name in this server.
        assert '<@555>' in msg
        assert 'OurGuy' not in msg, 'mention alone identifies a registered member'
    finally:
        restore()


def test_follow_warning_not_triggered_by_weak_enemies_alone():
    """
    The send gate. Confirmed live: PUAD departing for Switzerland pinged
    Trizzy973 and Kiwisupercar but correctly stayed silent for TrevorLahey420,
    who outguns them ~9x. Without this, every member abroad hears about every
    takeoff regardless of whether it could hurt them - the noise objection that
    nearly killed the feature before it was built.
    """
    import asyncio

    own = {'100': _abroad('OurGuy', 'Switzerland')}
    ff = {
        '100': {'bs_estimate': 10_000_000_000},
        '200': {'bs_estimate': 1_000_000_000},  # 0.1x
        '201': {'bs_estimate': 500_000_000},    # 0.05x
    }
    mgr, posted, restore = _fly_mgr(own, {}, ff=ff, ff_key='test-key')
    try:
        deps = [
            {'tid': '200', 'name': 'Weakling', 'dest': 'Switzerland', 'eta': 1_000_500, 'early': 1_000_400},
            {'tid': '201', 'name': 'Tiny', 'dest': 'Switzerland', 'eta': 1_000_600, 'early': 1_000_500},
        ]
        assert asyncio.run(mgr._follow_alerts(deps, 1_000_000)) == [], \
            'nobody who could hurt this member departed - say nothing'
        assert mgr.follow_cooldowns == {}, \
            'a silent tick must not burn the cooldown and mask the next real threat'
    finally:
        restore()


def test_follow_warning_silent_when_own_roster_unavailable():
    """
    The None-vs-empty contract, applied to our own roster. A failed fetch must
    NOT read as "nobody is abroad" - that would quietly switch follow warnings
    off for as long as the API misbehaves, the same failure mode that once
    flagged the whole faction for missing OCs.

    Also: the cooldown must not be stamped on a tick we stayed silent, or the
    outage would go on suppressing the next real warning.
    """
    import asyncio

    mgr, posted, restore = _fly_mgr(None, {})  # own roster fetch returns None
    try:
        # The contract itself: a failed fetch must report None, NOT an empty
        # mapping. Both happen to stay silent here, but only None lets a future
        # caller tell "the API is down" apart from "nobody is abroad".
        assert asyncio.run(mgr._fetch_own_abroad()) is None

        deps = [{'tid': '200', 'name': 'EnemyA', 'dest': 'Switzerland',
                 'eta': 1_000_500, 'early': 1_000_400}]
        assert asyncio.run(mgr._follow_alerts(deps, 1_000_000)) == []
        assert mgr.follow_cooldowns == {}, 'an outage must not burn the cooldown'

        # A genuinely empty answer (200 OK, nobody abroad) is also silent, but
        # for the right reason - it is a real answer, not a failure.
        import features.flytrack as fly_mod
        async def empty_own(faction_id, selections='basic', **kw):
            from config import TORN_FACTION_ID
            if str(faction_id) == str(TORN_FACTION_ID):
                return {'members': {'100': _home('OurGuy')}}
            return {'members': {}}
        fly_mod.fetch_any_faction_data = empty_own
        assert asyncio.run(mgr._follow_alerts(deps, 1_000_000)) == []
    finally:
        restore()


def test_fly_status_shows_destination_hotspots():
    """
    The other half of the request: "a list where the most are flying to or
    sitting". Busiest country first, with sitting and inbound counted
    separately. Someone RETURNING from a country is in neither bucket - they
    are leaving it, not occupying it.
    """
    import asyncio

    enemy = {
        '200': _abroad('SatA', 'Switzerland'),
        '201': _abroad('SatB', 'Switzerland'),
        '202': _abroad('SatC', 'Switzerland'),
        '203': _flying_to('InA', 'Switzerland'),
        '204': _flying_to('InB', 'Switzerland'),
        '205': _flying_to('InC', 'Mexico'),
        '206': {'name': 'Homeward', 'status': {
            'state': 'Traveling', 'description': 'Traveling from Japan to Torn'}},
    }
    mgr, posted, restore = _fly_mgr({}, enemy)
    try:
        embed = asyncio.run(mgr.status_embed())
        hotspots = [f for f in embed.fields if 'Hotspots' in f.name]
        assert hotspots, 'the Fly view must show a destination tally'
        value = hotspots[0].value
        lines = value.split('\n')
        assert 'Switzerland' in lines[0] and '5' in lines[0], \
            f'busiest country first, got: {lines[0]!r}'
        assert '3 there' in lines[0] and '2 inbound' in lines[0]
        assert any('Mexico' in ln and '1 inbound' in ln for ln in lines)
        assert 'Japan' not in value, 'returning players do not occupy a country'
    finally:
        restore()


def test_war_poll_posts_once_two_days_out():
    """
    The rules make posting availability strike-able, so the poll must appear on
    its own - an obligation that depends on someone remembering to run /avail
    quietly stops being enforceable.

    Pins the three things that make it safe to run every minute: too-far-out
    does nothing, inside the window posts exactly one poll, and a second tick
    for the same war posts nothing more.
    """
    import asyncio
    from datetime import datetime, timezone
    import features.availability as av
    from features.availability import AvailabilityManager, WAR_POLL_LEAD_SECONDS

    now = int(datetime.now(timezone.utc).timestamp())

    class FakeMsg:
        id = 777

    class FakeChannel:
        id = 123
        def __init__(self):
            self.sent = []
        async def send(self, **kw):
            self.sent.append(kw)
            return FakeMsg()

    class FakeBot:
        def __init__(self, ch):
            self._ch = ch
        def get_channel(self, _):
            return self._ch

    class FakeWarTracker:
        def __init__(self, start):
            self.start = start
        async def get_upcoming_war(self):
            return {'war_id': '50999', 'start': self.start, 'data': {}}

    ch = FakeChannel()
    mgr = AvailabilityManager(FakeBot(ch))
    mgr.polls, mgr.data = {}, {}
    orig_save, orig_chan = av.save_json, av.AVAILABILITY_CHANNEL_ID
    av.save_json = lambda *a, **k: None
    av.AVAILABILITY_CHANNEL_ID = 123
    try:
        # Too far out -> silent
        asyncio.run(mgr.check_war_poll(FakeWarTracker(now + WAR_POLL_LEAD_SECONDS + 3600)))
        assert ch.sent == [], 'must not post days early'

        # Inside the window -> exactly one poll
        wt = FakeWarTracker(now + 36 * 3600)
        asyncio.run(mgr.check_war_poll(wt))
        assert len(ch.sent) == 1, f'expected one poll, got {len(ch.sent)}'
        assert len(mgr.polls) == 1
        poll = list(mgr.polls.values())[0]
        assert poll['war_id'] == '50999'
        from features.availability import WAR_POLL_CLOSE_LEAD_SECONDS
        assert poll['poll_end_unix'] == wt.start - WAR_POLL_CLOSE_LEAD_SECONDS, (
            'the poll closes an hour BEFORE the war: an answer typed as the '
            'war begins is not a plan anyone can act on')

        # Runs every minute - a second tick must not post again
        asyncio.run(mgr.check_war_poll(wt))
        assert len(ch.sent) == 1, 'posted twice for the same war'
    finally:
        av.save_json, av.AVAILABILITY_CHANNEL_ID = orig_save, orig_chan


def test_war_poll_window_is_24h_even_for_a_midnight_war():
    """
    create_poll() treats an end time of 00:00 as "end of that day" and adds the
    24h itself. A war starting at 00:00 TCT therefore produces end_time 00:00,
    and handing it the real next-day date as well would silently DOUBLE the
    window to 48h - twice the blocks, covering a day of war nobody asked about.
    """
    import asyncio
    from datetime import datetime, timezone
    import features.availability as av
    from features.availability import (AvailabilityManager, WAR_POLL_SPAN_HOURS,
                                       WAR_POLL_BLOCK_HOURS)

    class FakeMsg:
        id = 1

    class FakeChannel:
        id = 123
        async def send(self, **kw):
            return FakeMsg()

    class FakeBot:
        def get_channel(self, _):
            return FakeChannel()

    expected_blocks = WAR_POLL_SPAN_HOURS // WAR_POLL_BLOCK_HOURS
    orig_save = av.save_json
    av.save_json = lambda *a, **k: None
    try:
        # 00:00 TCT exactly - the case that used to double
        midnight = int(datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc).timestamp())
        # ...and a normal mid-afternoon start, as a control
        afternoon = int(datetime(2026, 11, 2, 16, 0, tzinfo=timezone.utc).timestamp())

        for label, start in (('midnight', midnight), ('afternoon', afternoon)):
            mgr = AvailabilityManager(FakeBot())
            mgr.polls, mgr.data = {}, {}
            asyncio.run(mgr._post_war_poll(FakeChannel(), '1', start, 'Verified'))
            blocks = list(mgr.polls.values())[0]['blocks']
            span = (blocks[-1]['end_unix'] - blocks[0]['start_unix']) / 3600
            assert blocks[0]['start_unix'] == start, f'{label}: poll must open at war start'
            assert span == WAR_POLL_SPAN_HOURS, f'{label}: window is {span}h, want {WAR_POLL_SPAN_HOURS}h'
            assert len(blocks) == expected_blocks, f'{label}: {len(blocks)} blocks'
    finally:
        av.save_json = orig_save


def test_fly_auto_starts_but_never_overrides_a_manual_toggle_off():
    """
    War END auto-stopped fly tracking, but START did nothing beyond setting a
    flag and a log line - so every war depended on somebody running
    /war view:Fly, and forgetting meant no flight alerts and no follow warnings
    for the whole war, silently.

    The hard part is not switching it on, it is not fighting the operator.
    disable() clears war_id, so war_id cannot be the "already handled" marker:
    a manual toggle-off would look like a fresh war a minute later and switch
    straight back on. A separate marker that disable() leaves alone fixes that.
    """
    import asyncio
    import features.flytrack as fly_mod
    from features.flytrack import FlyTracker

    class FakeWarTracker:
        was_war_active = True
        current_war_id = '50338'

    mgr = FlyTracker(bot=None, war_tracker=FakeWarTracker())
    enables = []

    async def fake_enable():
        enables.append(1)
        mgr.enabled = True
        mgr.war_id = '50338'
        return True, 'embed'

    async def fake_post(content=None, embed=None):
        pass

    mgr.enable = fake_enable
    mgr._post = fake_post
    orig_save = fly_mod.save_json
    fly_mod.save_json = lambda *a, **k: None
    try:
        # War starts -> tracking switches itself on
        asyncio.run(mgr.auto_start_for_new_war())
        assert enables == [1], 'fly tracking must start with the war'
        assert mgr.auto_started_war_id == '50338'

        # Same war, next tick -> nothing
        asyncio.run(mgr.auto_start_for_new_war())
        assert len(enables) == 1, 'must only fire once per war'

        # Operator turns it off mid-war. disable() wipes war_id, which is
        # exactly the state that used to look like a brand new war.
        mgr.enabled = False
        mgr.war_id = None
        asyncio.run(mgr.auto_start_for_new_war())
        assert len(enables) == 1, 'a deliberate toggle-off must be respected'

        # A genuinely different war does start it again
        mgr.war_tracker.current_war_id = '50999'
        asyncio.run(mgr.auto_start_for_new_war())
        assert len(enables) == 2, 'a new war must start tracking again'
    finally:
        fly_mod.save_json = orig_save


def test_fly_auto_start_retries_when_enable_fails():
    """
    At the moment a war flips live, the enemy roster can briefly lag behind it,
    so enable() can fail for a tick. Stamping the marker anyway would mean the
    war runs untracked and nothing ever tries again.
    """
    import asyncio
    import features.flytrack as fly_mod
    from features.flytrack import FlyTracker

    class FakeWarTracker:
        was_war_active = True
        current_war_id = '50338'

    mgr = FlyTracker(bot=None, war_tracker=FakeWarTracker())
    attempts = {'n': 0}

    async def flaky_enable():
        attempts['n'] += 1
        if attempts['n'] == 1:
            return False, 'no enemy faction yet'
        mgr.enabled = True
        mgr.war_id = '50338'
        return True, 'embed'

    async def fake_post(content=None, embed=None):
        pass

    mgr.enable = flaky_enable
    mgr._post = fake_post
    orig_save = fly_mod.save_json
    fly_mod.save_json = lambda *a, **k: None
    try:
        asyncio.run(mgr.auto_start_for_new_war())
        assert mgr.auto_started_war_id is None, 'a failed start must not be marked done'
        asyncio.run(mgr.auto_start_for_new_war())
        assert attempts['n'] == 2 and mgr.auto_started_war_id == '50338'
    finally:
        fly_mod.save_json = orig_save


def test_poll_roster_names_who_picked_each_block():
    """
    Counts tell you a block is thin but not who to chase, which is the point of
    running the poll at all. Empty blocks matter most, so truncation happens
    inside a block rather than by dropping blocks - a popular early block must
    never hide a later one with nobody in it.
    """
    from features.availability import AvailabilityManager

    class FakeMember:
        def __init__(self, name):
            self.display_name = name

    class FakeGuild:
        def __init__(self, members):
            self._m = members
        def get_member(self, uid):
            return self._m.get(int(uid))

    mgr = AvailabilityManager(bot=None)
    mgr.polls = {'p1': {'blocks': [
        {'id': 0, 'label': 'A'}, {'id': 1, 'label': 'B'}, {'id': 2, 'label': 'C'}]}}
    mgr.data = {'p1': {'0': [1, 2], '1': [], '2': [3]}}
    guild = FakeGuild({1: FakeMember('Ikaros22'), 2: FakeMember('SirAua'),
                       3: FakeMember('dakkkz')})

    out = mgr.build_roster('p1', guild)
    assert 'Ikaros22' in out and 'SirAua' in out, out
    assert '**A** (2)' in out
    assert '**B** (0): nobody yet' in out, 'an empty block is the signal, never hidden'
    assert '**C** (1): dakkkz' in out

    # Unknown member falls back to a mention rather than vanishing
    out2 = mgr.build_roster('p1', FakeGuild({}))
    assert '<@1>' in out2

    # A crowded block truncates itself, and later blocks still appear
    mgr.data['p1']['0'] = list(range(100, 140))
    big = mgr.build_roster('p1', FakeGuild({i: FakeMember(f'LongMemberName{i}') for i in range(100, 140)}))
    assert 'more' in big, 'a crowded block must summarise, not run away'
    assert '**C**' in big, 'later blocks must survive truncation'
    assert len(big) <= 1000


def test_no_open_slots_is_reported_but_is_not_an_excuse():
    """
    This check used to return early when every slot was filled, treating a full
    queue as management's fault rather than the member's.

    The faction rule is the other way round: a member with nothing to join is
    expected to TELL management so a crime gets created, not sit out. So the
    check still runs and still flags, and the shortage is handed back as
    context for the report instead of silencing it.

    An UNKNOWN slot count (-1, which the v1 fallback returns because OC1 has no
    slot model) is still not zero, and must not be reported as a shortage.
    """
    import time
    from features.inactivity import InactivityTracker

    mgr = InactivityTracker(bot=None)
    mgr.last_oc_completion = {'111': time.time() - 30 * 86400}
    members = {'111': {'name': 'Idle', 'days_in_faction': 90, 'level': 20}}

    # Slots open -> the member really could have joined, so flag them
    crimes = [{'completed': False, 'completion_ts': 0, 'participants': ['222'],
               'open_slots': 3, 'difficulty': 2}]
    warn, crit, _, _slots = mgr._get_oc_status(members, crimes)
    assert warn or crit, 'with a joinable slot this is a real miss'

    assert _slots is False, 'slots were open, so no shortage to report'

    # Every slot filled -> STILL flagged, and the shortage reported alongside
    crimes = [{'completed': False, 'completion_ts': 0, 'participants': ['222'],
               'open_slots': 0, 'difficulty': 2}]
    warn, crit, _, no_slots = mgr._get_oc_status(members, crimes)
    assert warn or crit, (
        'a full queue is not an excuse: the member is expected to ask '
        'management for a crime')
    assert no_slots is True, 'the shortage must still be reported as context'

    # Unknown (-1) is not zero: still runs, and is NOT a reported shortage
    crimes = [{'completed': False, 'completion_ts': 0, 'participants': ['222'],
               'open_slots': -1, 'difficulty': 0}]
    warn, crit, _, no_slots = mgr._get_oc_status(members, crimes)
    assert warn or crit, 'unknown slot counts must not silence the check'
    assert no_slots is False, 'unknown is not a known shortage'


def test_turtle_alert_fires_only_for_exposed_members():
    """
    The war guide's stated biggest problem: members farmed while offline. The
    threshold is measured against war 50338 (4 hits in 60 min = 12 alerts/day,
    16 members; 2-in-30 would have been 128/day and useless).

    Being ABROAD is not safety - landed abroad gives the attacker an extra
    x1.25, which the guide calls out as the worst case. Only hospital, jail or
    still being in the air counts as turtled.
    """
    import asyncio
    from features.wars import WarTracker, TURTLE_HIT_THRESHOLD
    import features.wars as wars_mod
    from config import TORN_FACTION_ID

    sent = []

    class FakeChannel:
        async def send(self, **kw):
            sent.append(kw.get('embed'))

    class FakeBot:
        def get_channel(self, _):
            return FakeChannel()

    mgr = WarTracker(bot=FakeBot())
    mgr.was_war_active = True
    mgr.turtle_cooldowns = {}

    our = str(TORN_FACTION_ID)
    now = 1_791_000_000

    def hits(defender, n):
        return {f'{defender}-{i}': {
            'attacker_faction': '53421', 'defender_faction': our,
            'defender_id': defender, 'result': 'Attacked',
            'timestamp_started': now - i * 60} for i in range(n)}

    attacks = {}
    attacks.update(hits('111', TURTLE_HIT_THRESHOLD))      # exposed in Torn
    attacks.update(hits('222', TURTLE_HIT_THRESHOLD))      # already hospitalised
    attacks.update(hits('333', TURTLE_HIT_THRESHOLD))      # landed abroad
    attacks.update(hits('444', TURTLE_HIT_THRESHOLD - 1))  # under the bar

    roster = {
        'members': {
            '111': {'name': 'Exposed', 'status': {'state': 'Okay'}},
            '222': {'name': 'Safe', 'status': {'state': 'Hospital'}},
            '333': {'name': 'Abroad', 'status': {'state': 'Abroad'}},
            '444': {'name': 'Barely', 'status': {'state': 'Okay'}},
        }
    }

    async def fake_attacks(from_ts=None, to_ts=None, sort=None):
        return attacks

    async def fake_basic(selections='basic'):
        return roster

    orig = (wars_mod.fetch_faction_attacks, wars_mod.fetch_faction_data, wars_mod.save_json)
    wars_mod.fetch_faction_attacks = fake_attacks
    wars_mod.fetch_faction_data = fake_basic
    wars_mod.save_json = lambda *a, **k: None
    try:
        asyncio.run(mgr.check_turtle_alerts())
        assert len(sent) == 1, f'expected one alert embed, got {len(sent)}'
        body = sent[0].description
        assert 'Exposed' in body, 'an exposed member being farmed must be warned'
        assert 'Abroad' in body, 'landed abroad is the worst case, not safety'
        assert 'Safe' not in body, 'already in hospital, nothing to nag about'
        assert 'Barely' not in body, 'below the measured threshold'

        # Runs every minute, so the cooldown must hold it
        sent.clear()
        asyncio.run(mgr.check_turtle_alerts())
        assert sent == [], 'cooldown must stop this repeating every tick'

        # An unreadable log must say nothing rather than guess
        sent.clear()
        mgr.turtle_cooldowns = {}
        async def dead(from_ts=None, to_ts=None, sort=None):
            return None
        wars_mod.fetch_faction_attacks = dead
        asyncio.run(mgr.check_turtle_alerts())
        assert sent == [], 'an outage is not evidence that nobody is being farmed'
    finally:
        (wars_mod.fetch_faction_attacks, wars_mod.fetch_faction_data,
         wars_mod.save_json) = orig


def test_only_recruiting_oc_slots_count_as_joinable():
    """
    "Unfilled" is not "joinable". Measured live, the faction had 16 empty slots
    on Recruiting crimes and 9 more on EXPIRED ones that nobody can ever join.
    Counting the expired 9 would keep the OC check running while there was
    genuinely nothing to join, which is the exact unfairness the check exists
    to avoid.
    """
    import asyncio
    import utils.torn_api as api

    payload = {'crimes': [
        {'status': 'Recruiting', 'executed_at': None, 'difficulty': 2,
         'slots': [{'user': {'id': 1}}, {'user': None}, {'user': None}]},
        {'status': 'Expired', 'executed_at': None, 'difficulty': 3,
         'slots': [{'user': None}, {'user': None}]},
        {'status': 'Planning', 'executed_at': None, 'difficulty': 4,
         'slots': [{'user': {'id': 2}}, {'user': {'id': 3}}]},
    ]}

    async def fake(selections='crimes', path_id=None):
        return payload
    orig = api.fetch_faction_data_v2
    api.fetch_faction_data_v2 = fake
    try:
        crimes = asyncio.run(api.fetch_faction_crimes())
        by_status = {c['status']: c for c in crimes}
        assert by_status['Recruiting']['open_slots'] == 2
        assert by_status['Expired']['open_slots'] == 0,             'an expired crime cannot be joined, so its empty slots are not open'
        assert by_status['Planning']['open_slots'] == 0, 'planning crimes are full'
        assert sum(c['open_slots'] for c in crimes) == 2
    finally:
        api.fetch_faction_data_v2 = orig


def test_war_report_separates_opponent_mugs_from_outside_mugs():
    """
    Mugging the opponent breaks the explicit war rule (Leave, not mug).
    Mugging a random outsider during the war is a different thing: it still
    costs 25% of the chain respect, but it is not the war rule. Reporting them
    as one number would either overstate the rule break or hide it.
    """
    import asyncio
    from features.wars import WarTracker
    import utils.torn_api as torn_api_mod
    from config import TORN_FACTION_ID

    mgr = WarTracker(bot=None)

    async def fake_members():
        return {'111': {'name': 'Mugger'}, '222': {'name': 'Clean'}}
    orig = torn_api_mod.fetch_faction_members
    torn_api_mod.fetch_faction_members = fake_members

    final_data = {
        'war_id': 1,
        'factions': {
            str(TORN_FACTION_ID): {'name': 'Us', 'score': 10,
                                   'members': {'111': {'attacks': 9, 'score': 5.0},
                                               '222': {'attacks': 4, 'score': 2.0}}},
            '999': {'name': 'Them', 'score': 5, 'members': {}},
        },
    }
    attack_stats = {
        '111': {'war_incoming': 1, 'outside_incoming': 0,
                'mugs_given': 3, 'mugs_outside': 7},
        '222': {'war_incoming': 0, 'outside_incoming': 0,
                'mugs_given': 0, 'mugs_outside': 0},
        '_friendly_hosps': [],
    }
    try:
        res = asyncio.run(mgr.format_war_management_report(
            None, final_data=final_data, attack_stats=attack_stats))
        fields = {f.name: f.value for f in res[0].fields}
        check = next(v for k, v in fields.items() if 'To check' in k)
        assert '3x on the opponent' in check, check
        assert '7x outside' in check, check
        assert 'Clean' not in check, 'nobody clean should appear'
    finally:
        torn_api_mod.fetch_faction_members = orig


def test_chain_break_warning_gates_on_size_and_cooldown():
    """
    The chain guide's one operational rule is "under 30 seconds and nobody's
    hit? You hit", and nothing told anyone - milestone posts are after the
    fact, so they can never save a chain.

    The 25+ gate is where the noise lives: measured over 100 chains in 147
    days, 10+ happens 0.68/day but 25+ only 0.19/day, so the gate alone filters
    out one-person chains without anyone having to flag an event first.
    """
    import asyncio
    from features.chains import (ChainTracker, CHAIN_ALERT_MIN_COUNT,
                                 CHAIN_ALERT_SECONDS_LEFT, CHAIN_ALERT_COOLDOWN)
    import features.chains as ch_mod

    sent = []

    class FakeChannel:
        async def send(self, content=None, embed=None):
            sent.append(content if content is not None else '')

    now = 1_791_000_000
    mgr = ChainTracker(bot=None)
    orig_now = ch_mod.get_current_timestamp
    ch_mod.get_current_timestamp = lambda: now

    def run(count, secs_left):
        chain = {'current': count, 'timeout': now + secs_left, 'start': 1}
        # exercise just the warning block via the real attribute it guards on
        timeout_sane = 0 < secs_left <= 300
        if (timeout_sane and count >= CHAIN_ALERT_MIN_COUNT
                and secs_left <= CHAIN_ALERT_SECONDS_LEFT
                and now - mgr._last_chain_warning >= CHAIN_ALERT_COOLDOWN):
            mgr._last_chain_warning = now
            asyncio.run(FakeChannel().send(content=f'chain {count} {secs_left}s'))

    try:
        run(10, 20)
        assert sent == [], 'a 10-chain is one person, not an event'

        run(CHAIN_ALERT_MIN_COUNT, 20)
        assert len(sent) == 1, 'a 25+ chain about to die must be called'

        # Runs every minute; the cooldown has to hold it
        run(CHAIN_ALERT_MIN_COUNT + 5, 15)
        assert len(sent) == 1, 'cooldown must stop it repeating every tick'

        now += CHAIN_ALERT_COOLDOWN + 1
        ch_mod.get_current_timestamp = lambda: now
        run(CHAIN_ALERT_MIN_COUNT + 5, 15)
        assert len(sent) == 2, 'past the cooldown it may warn again'

        # A healthy timer says nothing
        sent.clear()
        mgr._last_chain_warning = 0
        run(100, 240)
        assert sent == [], 'four minutes left is not an emergency'
    finally:
        ch_mod.get_current_timestamp = orig_now


def test_bonus_hit_on_a_non_enemy_is_flagged_but_only_when_it_really_was_one():
    """
    "A bonus hit on an outside target during a war = 1 strike" (war guide).

    This is an accusation, so the discrimination matters more than the catch.
    Measured against 2,827 live attacks before writing it:
      - chain position is on every record, and only SUCCESSFUL hits carry one
        (every Lost/Stalemate/Escape/Interrupted/Timeout record had chain=0)
      - modifiers.chain_bonus is the ordinary multiplier (1.0-1.74 observed)
        EXCEPT on a bonus hit, where it is the flat bonus itself (10/20/40/80)
      - one Arrested record carried chain_bonus=10 with chain=0, which is why
        the chain position gates the check and the modifier only corroborates
    """
    import asyncio
    from features.wars import WarTracker
    import features.wars as wars_mod
    from config import TORN_FACTION_ID

    # The suite pins TORN_FACTION_ID to '1' (see top of this file), so "ours"
    # has to come from config, not a literal.
    OURS, ENEMY = str(TORN_FACTION_ID), '53421'

    def hit(aid, chain, bonus, deffac, result='Attacked', defid='999'):
        a = {
            'attacker_id': aid, 'attacker_faction': OURS,
            'attacker_name': f'M{aid}',
            'defender_id': defid, 'defender_faction': deffac,
            'defender_name': f'T{defid}', 'defender_factionname': 'Somebody',
            'result': result, 'timestamp_started': 1000, 'timestamp_ended': 1001,
            'chain': chain,
        }
        if bonus is not None:
            a['modifiers'] = {'chain_bonus': bonus, 'war': 1}
        return a

    attacks = {
        # --- should be flagged ---
        '1': hit('100', 25, 20, 0),          # bonus on a no-faction random
        '2': hit('101', 10, 10, '99999'),    # bonus on some other faction
        '3': hit('102', 50, 40, OURS),       # bonus burned on a faction mate
        '4': hit('103', 100, None, 0),       # modifier field gone -> still flag
        # --- should NOT be flagged ---
        '5': hit('200', 25, 20, ENEMY),      # bonus on the enemy: correct play
        '6': hit('201', 24, 1.4, 0),         # not a milestone position
        '7': hit('202', 50, 1.5, 0),         # milestone position, NOT the bonus
        '8': hit('203', 10, 10, 0, result='Lost'),   # failed attack
        '9': hit('204', 0, 10, 0),           # the Arrested-shaped anomaly
    }

    mgr = WarTracker(bot=None)

    async def fake_fetch(from_ts=None, to_ts=None, sort=None):
        return attacks if (from_ts or 0) <= 1000 else {}

    async def fake_faction_data(selection, *a, **k):
        return {'chains': {}}  # no chain windows needed for this check

    orig_fetch = wars_mod.fetch_faction_attacks
    orig_data = wars_mod.fetch_faction_data
    wars_mod.fetch_faction_attacks = fake_fetch
    wars_mod.fetch_faction_data = fake_faction_data
    try:
        stats = asyncio.run(mgr._get_war_attack_stats(ENEMY, 1000, 2000))
    finally:
        wars_mod.fetch_faction_attacks = orig_fetch
        wars_mod.fetch_faction_data = orig_data

    assert stats is not None, 'readable log must not report unknown'

    flagged = {b['attacker_id'] for b in stats['_bonus_outside']}
    assert flagged == {'100', '101', '102', '103'}, (
        f'wrong set flagged: {sorted(flagged)}')

    for uid in ('200', '201', '202', '203', '204'):
        assert stats.get(uid, {}).get('bonus_outside', 0) == 0, (
            f'{uid} must not be flagged')

    # the record has to carry what makes it checkable
    rec = next(b for b in stats['_bonus_outside'] if b['attacker_id'] == '100')
    assert rec['chain'] == 25 and rec['defender_name'] == 'T999', rec

    # the event log must not leak into per-member iteration
    assert isinstance(stats['_bonus_outside'], list)
    assert all(k.startswith('_') or isinstance(v, dict)
               for k, v in stats.items())


def test_management_ping_lands_in_the_channel_that_got_the_alert():
    """
    Regression, seen in production on 15/10/2026.

    Routing OC criticals to the strikes channel left the @Management ping
    behind in the daily-check channel, so management was pinged into a channel
    with nothing in it while the actual alert sat somewhere else. A ping with
    no content is worse than no ping: it trains people to ignore the next one.

    The ping now follows the content, and the nudge tier stays put because a
    2-day reminder is not strike material.
    """
    import asyncio
    from features.inactivity import InactivityTracker

    class FakeChannel:
        def __init__(self, cid):
            self.id = cid
            self.posts = []

        async def send(self, content=None, embed=None, **kw):
            self.posts.append(('ping' if content else 'embed',
                               embed.title if embed else content))

    daily = FakeChannel(111)
    strikes = FakeChannel(222)

    class FakeBot:
        def get_channel(self, cid):
            return strikes if cid == 222 else daily

        def get_guild(self, _):
            return None

    import features.inactivity as inact_mod
    orig_sid = inact_mod.STRIKES_CHANNEL_ID
    orig_role = inact_mod.MANAGEMENT_PING_ROLE_ID
    inact_mod.STRIKES_CHANNEL_ID = 222
    inact_mod.MANAGEMENT_PING_ROLE_ID = 999

    mgr = InactivityTracker(bot=FakeBot())
    crit = [{'torn_name': 'Druid1999', 'torn_id': '4460611', 'torn_level': 15,
             'days': 15.0, 'last_oc_ts': 1790000000.0}]
    try:
        # ONLY an OC critical: the exact shape that produced the bare ping.
        asyncio.run(mgr._send_inactivity_alert(
            daily, [], [], 35, oc_warning=[], oc_critical=crit, left_faction=[]))

        assert not any(k == 'ping' for k, _ in daily.posts), (
            f'bare ping left in the daily channel: {daily.posts}')
        assert any(k == 'ping' for k, _ in strikes.posts), (
            f'the strikes channel got the alert but no ping: {strikes.posts}')
        assert any(k == 'embed' for k, _ in strikes.posts), 'alert must be there'

        # And a nudge-only run stays in the daily channel, pinged there.
        daily.posts.clear()
        strikes.posts.clear()
        asyncio.run(mgr._send_inactivity_alert(
            daily, [], [], 35, oc_warning=crit, oc_critical=[], left_faction=[]))
        assert any(k == 'ping' for k, _ in daily.posts), 'nudge must ping here'
        assert any(k == 'embed' for k, _ in daily.posts), 'nudge stays here'
        assert not strikes.posts, (
            f'a 2-day nudge is not strike material: {strikes.posts}')
    finally:
        inact_mod.STRIKES_CHANNEL_ID = orig_sid
        inact_mod.MANAGEMENT_PING_ROLE_ID = orig_role


def test_expired_crime_does_not_count_as_participating():
    """
    Torn reports an Expired crime with completed=False, exactly like a live
    one. It is dead: it timed out without ever running, so anyone still
    sitting in it is not participating in anything.

    Without the status check those members read as busy and get skipped by the
    OC check entirely - and they are precisely the people worth flagging, since
    the crime they joined never happened. Same shape as the Expired-slots bug,
    where unfillable slots read as joinable.

    Verified against the live payload: Expired crimes really do carry
    completed=False, while Successful and Failure carry True.
    """
    import time
    from features.inactivity import InactivityTracker

    now = time.time()
    old = now - 20 * 86400          # last real OC, well past any threshold

    crimes = [
        # dead, but shaped like a live one
        {'status': 'Expired', 'completed': False, 'completion_ts': 0,
         'participants': ['100'], 'open_slots': 0, 'difficulty': 3},
        # genuinely live
        {'status': 'Planning', 'completed': False, 'completion_ts': 0,
         'participants': ['200'], 'open_slots': 0, 'difficulty': 3},
        # finished, and it is what builds the history
        {'status': 'Successful', 'completed': True, 'completion_ts': old,
         'participants': ['100', '200'], 'open_slots': 0, 'difficulty': 3},
        # something joinable must exist, or the check skips everything
        {'status': 'Recruiting', 'completed': False, 'completion_ts': 0,
         'participants': [], 'open_slots': 2, 'difficulty': 3},
    ]
    members = {
        '100': {'name': 'StuckInExpired', 'level': 10, 'days_in_faction': 40},
        '200': {'name': 'ActuallyPlanning', 'level': 10, 'days_in_faction': 40},
    }

    mgr = InactivityTracker(bot=None)
    warn, crit, _, _slots = mgr._get_oc_status(members, crimes)

    flagged = {m['torn_name'] for m in (warn + crit)}
    assert 'StuckInExpired' in flagged, (
        'a member sitting in an Expired crime is not participating and must '
        f'still be checked; got {flagged}')
    assert 'ActuallyPlanning' not in flagged, (
        'a member in a live Planning crime is genuinely busy')


def test_member_with_no_oc_history_is_clocked_from_their_join_date():
    """
    A member who has NEVER completed an OC used to be skipped forever: the
    check needed a last-completion timestamp and there was none, and that
    never expires. Measured 15/10/2026, two members were invisible this way,
    one of them 51 days in the faction.

    The clock now falls back to time in the faction MINUS the grace days,
    because a brand new member cannot do anything in the faction yet. Counting
    starts the day the grace runs out, not the day they joined - so the grace
    is a real head start, not just a reporting delay.
    """
    import time
    from features.inactivity import InactivityTracker
    from config import (OC_NEW_MEMBER_GRACE_DAYS, OC_WARNING_DAYS,
                        OC_CRITICAL_DAYS)

    g = OC_NEW_MEMBER_GRACE_DAYS
    now = time.time()

    # Something joinable has to exist or the whole check is skipped by design.
    crimes = [{'status': 'Recruiting', 'completed': False, 'completion_ts': 0,
               'participants': [], 'open_slots': 3, 'difficulty': 3}]

    def member(days_in_faction):
        return {'name': f'N{days_in_faction}', 'level': 7,
                'days_in_faction': days_in_faction}

    mgr = InactivityTracker(bot=None)

    # Nobody here has any OC history at all.
    members = {
        '1': member(g - 1),                       # still inside the grace
        '2': member(g),                           # grace just ended: 0 days
        '3': member(g + OC_WARNING_DAYS),         # first day of the nudge
        '4': member(g + OC_CRITICAL_DAYS),        # first day of strike range
        '5': member(g + 48),                      # the BunnyBull case
    }
    warn, crit, _, _slots = mgr._get_oc_status(members, crimes)
    warned = {m['torn_name'] for m in warn}
    critted = {m['torn_name'] for m in crit}

    assert f'N{g - 1}' not in warned | critted, 'inside the grace, untouched'
    assert f'N{g}' not in warned | critted, (
        'the grace must be a head start: day 0 of counting, not day 3')
    assert f'N{g + OC_WARNING_DAYS}' in warned, 'should be nudged'
    assert f'N{g + OC_CRITICAL_DAYS}' in critted, 'should be strike territory'
    assert f'N{g + 48}' in critted, 'long-standing member who never joined one'

    # The grace really is subtracted, not just used as a skip.
    rec = next(m for m in crit if m['torn_name'] == f'N{g + 48}')
    assert rec['days'] == 48, f"expected 48 days of eligibility, got {rec['days']}"
    assert rec['never_joined'] is True, 'must be marked so the embed says so'
    assert rec['last_oc_ts'] is None, 'there is genuinely no last OC'

    # A member WITH history is still clocked from that history, not join date.
    mgr.last_oc_completion = {'9': now - 1 * 86400}
    warn2, crit2, _, _slots = mgr._get_oc_status({'9': member(500)}, crimes)
    assert not warn2 and not crit2, (
        'one day since a real OC is fine, however long they have been here')


def test_revive_scan_never_turns_a_failed_lookup_into_an_accusation():
    """
    This message names people, so a failed lookup must not become a verdict
    either way. Three distinct "no answer" shapes have to stay separate from
    a real one:

      - the roster itself is unreadable  -> None, post nothing at all
      - one member's profile call fails  -> unknown, not listed
      - `revivable` missing from a 200   -> unknown, not "off"

    The third is the shape that cost the most before: a field that simply
    disappears from a successful payload, where no guard fires and the .get()
    default silently becomes the answer, exactly as `defends` did.
    """
    import asyncio
    from features.wars import WarTracker
    import utils.torn_api as api

    mgr = WarTracker(bot=None)

    roster = {'1': {'name': 'ReviveOn'}, '2': {'name': 'ReviveOff'},
              '3': {'name': 'FetchFails'}, '4': {'name': 'FieldGone'}}
    profiles = {
        '1': {'revivable': 1},
        '2': {'revivable': 0},
        '3': None,            # the call failed
        '4': {'level': 10},   # 200 OK, but the field is gone
    }

    async def fake_members():
        return roster

    async def fake_player(tid, selections='profile', **kw):
        return profiles[str(tid)]

    o_m, o_p = api.fetch_faction_members, api.fetch_player_data
    api.fetch_faction_members, api.fetch_player_data = fake_members, fake_player
    try:
        r = asyncio.run(mgr.scan_revive_status())
        assert [n for n, _ in r['on']] == ['ReviveOn'], r['on']
        assert r['off'] == 1, 'only the member we actually read as off'
        assert r['unknown'] == 2, (
            f"a failed call and a vanished field are both unknown, got {r['unknown']}")

        # --- roster unreadable: None, never an empty all-clear ---
        async def no_roster():
            return None
        api.fetch_faction_members = no_roster
        assert asyncio.run(mgr.scan_revive_status()) is None, (
            'an unreadable roster must not render as "nobody has revives on"')
    finally:
        api.fetch_faction_members, api.fetch_player_data = o_m, o_p


def test_revive_check_posts_once_per_checkpoint_and_retries_on_failure():
    """
    Two things a scheduled pre-war post must not do: fire twice, or quietly
    give up.

    Every checkpoint already passed is marked on the first post, not just the
    one that fired. There is only one checkpoint today (6h), so this cannot
    bite right now - it is here so that adding a second one back does not
    quietly produce two messages a minute apart.
    And if the roster was unreadable the checkpoint stays open, so the check
    retries next tick instead of being silently consumed.
    """
    import asyncio
    from features.wars import WarTracker, REVIVE_CHECK_CHECKPOINTS
    import features.wars as wars_mod

    posts = []

    class FakeChannel:
        async def send(self, **kw):
            posts.append(kw.get('embed'))

    class FakeBot:
        def get_channel(self, _):
            return FakeChannel()

    now = 1_800_000_000
    mgr = WarTracker(bot=FakeBot())
    saved = []

    async def fake_save():
        saved.append(dict(mgr.revive_checks))
    mgr.save_revive_checks = fake_save

    # war starts inside the TIGHTEST checkpoint, so both have been passed
    start = now + min(REVIVE_CHECK_CHECKPOINTS) - 60

    async def upcoming():
        return {'war_id': '999', 'start': start, 'data': {}}
    mgr.get_upcoming_war = upcoming

    calls = {'n': 0}

    async def ok(channel, label):
        calls['n'] += 1
        return True

    orig_ts = wars_mod.get_current_timestamp
    wars_mod.get_current_timestamp = lambda: now
    try:
        mgr.post_revive_check = ok
        asyncio.run(mgr.check_revive_status())
        assert calls['n'] == 1, f'posted {calls["n"]} times, expected 1'
        assert set(mgr.revive_checks['999']) == set(REVIVE_CHECK_CHECKPOINTS), (
            'every checkpoint already passed must be marked on the first post, '
            f"got {mgr.revive_checks['999']}")

        # a second tick does nothing
        asyncio.run(mgr.check_revive_status())
        assert calls['n'] == 1, 'must not post again for the same war'

        # --- an unreadable roster leaves the checkpoint open ---
        mgr2 = WarTracker(bot=FakeBot())
        mgr2.save_revive_checks = fake_save
        mgr2.get_upcoming_war = upcoming

        async def failed(channel, label):
            return False
        mgr2.post_revive_check = failed
        asyncio.run(mgr2.check_revive_status())
        assert '999' not in mgr2.revive_checks, (
            'a failed post must not consume the checkpoint')
    finally:
        wars_mod.get_current_timestamp = orig_ts


def test_trial_check_fires_once_per_member_and_resets_on_rejoin():
    """
    The guides make two promises that are one deadline: "reach level 15 within
    2 weeks" and "your first 2 weeks are a trial, at the end management reviews
    your progress".

    This runs DAILY, so latching is the whole feature. Without it a member who
    missed the deadline is re-accused every morning for as long as they stay,
    which is how an alert channel becomes background noise.

    days_in_faction resets when someone leaves and rejoins, and a second trial
    that is silently never checked is worse than no check at all.
    """
    from features.inactivity import InactivityTracker
    from config import TRIAL_DAYS, TRIAL_TARGET_LEVEL, TRIAL_WARNING_DAYS

    mgr = InactivityTracker(bot=None)
    low, ok = TRIAL_TARGET_LEVEL - 1, TRIAL_TARGET_LEVEL

    def roster(days, level):
        return {'7': {'name': 'Newbie', 'days_in_faction': days, 'level': level}}

    # --- too early: nothing at all ---
    w, f, p, _ = mgr._get_trial_status(roster(TRIAL_WARNING_DAYS - 1, low))
    assert not (w or f or p), 'before the warning day there is nothing to say'

    # --- warning day, still under level ---
    w, f, p, upd = mgr._get_trial_status(roster(TRIAL_WARNING_DAYS, low))
    assert len(w) == 1 and not f and not p, (w, f, p)
    assert w[0]['days_left'] == TRIAL_DAYS - TRIAL_WARNING_DAYS
    assert upd, 'the latch has to be persisted'

    # same day again, and the next day: silence
    w, f, p, _ = mgr._get_trial_status(roster(TRIAL_WARNING_DAYS, low))
    assert not w, 'must not warn twice'
    w, f, p, _ = mgr._get_trial_status(roster(TRIAL_WARNING_DAYS + 1, low))
    assert not w, 'must not warn again the next day either'

    # --- deadline, still under level: failed, once ---
    w, f, p, _ = mgr._get_trial_status(roster(TRIAL_DAYS, low))
    assert len(f) == 1 and not p, (w, f, p)
    assert f[0]['torn_level'] == low
    w, f, p, _ = mgr._get_trial_status(roster(TRIAL_DAYS + 1, low))
    assert not f, 'a missed deadline must not be re-accused daily'

    # --- a REJOIN resets the trial ---
    w, f, p, _ = mgr._get_trial_status(roster(1, low))       # fresh clock
    assert not (w or f or p), 'day 1 of a new trial says nothing yet'
    w, f, p, _ = mgr._get_trial_status(roster(TRIAL_WARNING_DAYS, low))
    assert len(w) == 1, 'the second trial must be checked, not silently skipped'

    # --- someone who made it: review due, not a failure ---
    mgr2 = InactivityTracker(bot=None)
    w, f, p, _ = mgr2._get_trial_status(roster(TRIAL_DAYS, ok))
    assert len(p) == 1 and not f, (w, f, p)

    # --- long-past members are pruned, so the file cannot grow forever ---
    mgr3 = InactivityTracker(bot=None)
    mgr3.trial_state = {'999': {'warned': True, 'reviewed': True}}
    w, f, p, upd = mgr3._get_trial_status(roster(TRIAL_DAYS + 30, ok))
    assert not (w or f or p), 'well past the review there is nothing to post'
    assert mgr3.trial_state == {}, f'stale entries must be pruned: {mgr3.trial_state}'
    assert upd


def test_strike_ledger_remembers_dismissals_and_expires_strikes():
    """
    The ledger exists because detection without a record left "how many
    strikes does X have?" answerable only by scrolling a channel.

    Two behaviours carry the whole thing:

    DISMISS MEMORY. The daily checks re-detect the same thing every morning.
    Without remembering a no, management would be asked the same question
    forever, which is exactly the noise that made the old alerts unreadable.

    EXPIRY ON READ. The leader asked for strikes to stop piling up, but a row
    is never deleted - expiry is computed when reading, so an aged-out strike
    stops counting while the history stays auditable.
    """
    import time
    from features.strikes import StrikeManager
    from config import STRIKE_EXPIRY_DAYS

    sm = StrikeManager(bot=None)

    # a fresh candidate is open
    assert sm.is_open('oc', '111', '') is True

    # ...and is not, once it is pending a decision
    sm.pending[sm.key_for('oc', '111', '')] = {'torn_id': '111'}
    assert sm.is_open('oc', '111', '') is False, 'must not post twice'

    # dismissing it keeps it closed for good
    del sm.pending[sm.key_for('oc', '111', '')]
    sm.dismissed[sm.key_for('oc', '111', '')] = time.time()
    assert sm.is_open('oc', '111', '') is False, (
        'a dismissed candidate must not be raised again tomorrow')

    # a DIFFERENT type for the same member is still open
    assert sm.is_open('trial', '111', '') is True

    # --- war context keeps candidates per war ---
    sm2 = StrikeManager(bot=None)
    sm2.dismissed[sm2.key_for('war_offline', '222', '100')] = time.time()
    assert sm2.is_open('war_offline', '222', '100') is False, 'same war, settled'
    assert sm2.is_open('war_offline', '222', '101') is True, (
        'being absent again NEXT war is a new candidate, not a duplicate')

    # --- an issued strike also closes the candidate ---
    sm3 = StrikeManager(bot=None)
    rec = sm3.add('333', 'Repeat', 'oc', 'no OC')
    rec['context'] = ''
    assert len(sm3.active('333')) == 1
    assert sm3.is_open('oc', '333', '') is False, 'already struck for this'

    # --- expiry is on READ: the row survives, the count does not ---
    rec['issued_at'] = time.time() - (STRIKE_EXPIRY_DAYS + 1) * 86400
    assert sm3.active('333') == [], 'an expired strike must stop counting'
    assert len(sm3.history('333')) == 1, (
        'but the record itself must remain, so history stays auditable')
    assert sm3.is_open('oc', '333', '') is True, (
        'once it expires the same rule can be raised again')

    # --- removing a strike takes it out of the active count ---
    sm4 = StrikeManager(bot=None)
    r = sm4.add('444', 'Wrongly', 'bonus', 'mistake')
    assert len(sm4.active('444')) == 1
    assert sm4.remove(r['id'], 'management') is not None
    assert sm4.active('444') == [], 'removed strikes do not count'
    assert sm4.remove(r['id'], 'management') is None, 'cannot remove twice'

    # --- counts() drives the /strikes all view ---
    sm5 = StrikeManager(bot=None)
    sm5.add('1', 'Alpha', 'oc', 'x')
    sm5.add('1', 'Alpha', 'trial', 'y')
    sm5.add('2', 'Beta', 'oc', 'z')
    counts = sm5.counts()
    assert counts['1'][1] == 2 and counts['2'][1] == 1, counts


def test_strike_notify_choices_are_exclusive_and_tracked():
    """
    A candidate offers three mutually exclusive choices and only one happens.

    The one that needs a guard is "I'll tell them": it records a strike that
    NOBODY HAS DELIVERED, which is indistinguishable from a member who was
    never informed unless it is tracked separately. That is what
    awaiting_notice() is for, and why the message keeps a "Mark as told"
    button instead of resolving outright.
    """
    from features.strikes import StrikeManager, dm_text, STRIKE_TYPES

    sm = StrikeManager(bot=None)

    # --- bot notifies: nothing is left outstanding ---
    a = sm.add('1', 'Alpha', 'oc', 'no OC', issued_by='Mgr', notified='bot')
    assert sm.awaiting_notice() == [], 'the bot already told them'

    # --- management notifies: it stays outstanding until marked ---
    b = sm.add('2', 'Beta', 'trial', 'missed 15', issued_by='Mgr',
               notified='pending')
    out = sm.awaiting_notice()
    assert [s['id'] for s in out] == [b['id']], out
    assert len(sm.active('2')) == 1, 'it counts as a strike either way'

    assert sm.mark_told(b['id'], 'Mgr2') is not None
    assert sm.awaiting_notice() == [], 'marked, so no longer outstanding'
    assert sm.get(b['id'])['notified_by'] == 'Mgr2', 'who told them is recorded'
    assert sm.mark_told(b['id'], 'Mgr2') is None, 'cannot mark twice'

    # a bot-notified strike was never outstanding, so it cannot be marked
    assert sm.mark_told(a['id'], 'Mgr2') is None

    # --- an expired strike drops out of the outstanding list too ---
    from config import STRIKE_EXPIRY_DAYS
    import time
    c = sm.add('3', 'Gamma', 'oc', 'x', notified='pending')
    assert len(sm.awaiting_notice()) == 1
    c['issued_at'] = time.time() - (STRIKE_EXPIRY_DAYS + 1) * 86400
    assert sm.awaiting_notice() == [], (
        'chasing someone over a strike that no longer counts is pointless')

    # --- the DM text is built once, so the channel echo cannot drift ---
    t = dm_text('bonus', 'took chain 25 on a random')
    assert STRIKE_TYPES['bonus'] in t and 'took chain 25' in t
    assert str(STRIKE_EXPIRY_DAYS) in t, 'the member is told when it expires'

    # --- dismissal still records nothing in the ledger ---
    sm2 = StrikeManager(bot=None)
    sm2.dismissed[sm2.key_for('oc', '9', '')] = time.time()
    assert sm2.active('9') == [], 'a dismissal is not a strike'
    assert sm2.is_open('oc', '9', '') is False


def test_war_insight_counts_categories_and_never_invents_hits_taken():
    """
    The board insight leads every line with a count, because "9 of 34 made no
    hits" survives being skim-read and a list of names does not.

    The guard that matters: hits taken come from the attacks log, and when
    that could not be read in full the bleed line must say UNKNOWN rather
    than report everyone as untouched. Reporting a quiet war because the log
    was unreachable is the oldest bug in this repo.
    """
    from features.wars import WarTracker
    from config import TORN_FACTION_ID

    mgr = WarTracker(bot=None)
    mgr.war_member_stats = {'wars': [{
        'war_id': '100', 'enemy_name': 'Old Foes', 'result': 'defeat',
        'members': {'1': {'attacks': 5}, '2': {'attacks': 0}, '3': {'attacks': 0}},
    }]}

    report = {str(TORN_FACTION_ID): {'members': {
        '1': {'attacks': 40, 'score': 300},   # carried it
        '2': {'attacks': 2, 'score': 10},     # token effort, farmed
        '3': {'attacks': 0, 'score': 0},      # nothing, answered the poll
        '4': {'attacks': 0, 'score': 0},      # nothing, said nothing
    }}}
    stats = {
        '1': {'total_incoming': 2, 'name': 'Carrier'},
        '2': {'total_incoming': 30, 'name': 'Bled'},
        '3': {'total_incoming': 0, 'name': 'Excused'},
        '4': {'total_incoming': 1, 'name': 'Silent'},
    }

    txt = mgr._war_insight_text(report, stats, responders={'3'})
    assert txt, 'should produce an insight'

    assert 'Carrier' in txt and '1 of 4' not in txt
    assert '2 of 4 made at least one hit' in txt, txt

    # the bleed line names the member who took far more than they gave
    assert 'Bled' in txt and '30 taken vs 2 made' in txt, txt

    # counts, and the poll exemption kept separate from real silence
    assert '2 of 4 made no hits' in txt, txt
    assert 'Silent' in txt, 'no hits and no poll answer must be named'
    assert 'Excused' not in txt.split('Did nothing')[1], (
        'answering the poll is the notice, so they are counted not named')

    # comparison against the previous war
    assert 'war #100' in txt and 'Old Foes' in txt, txt

    # --- the log was unreadable: say so, never report zero ---
    txt2 = mgr._war_insight_text(report, None, responders=set())
    assert 'unknown' in txt2.lower(), txt2
    assert 'could not be read' in txt2, (
        'an unreadable attacks log must be stated, not shown as nobody '
        f'being hit: {txt2}')
    assert 'taken vs' not in txt2, 'no bleed figures can be claimed'

    # --- no member data at all ---
    assert mgr._war_insight_text({}, stats) is None


def test_bleeding_warns_before_it_strikes_and_resets_on_a_clean_war():
    """
    Measured across wars 47427, 48339 and 50338: five members took more than
    they landed in EVERY war they played, together 591 of 2,364 hits taken.
    Meanwhile EyeUhhn went 86 -> 11 -> 6 across the same wars.

    That contrast is the whole design. One bad war is not a signal, so nothing
    happens. The first REPEAT gets a DM with the fix in it, because bleeding
    is usually not knowing how to turtle rather than not caring. Only a
    further war of it becomes a strike candidate.

    A clean war clears the record completely: somebody who fixed it must not
    stay one bad war away from a strike forever.
    """
    import asyncio
    from features.wars import WarTracker
    from config import TORN_FACTION_ID

    candidates, warnings = [], []

    class FakeLedger:
        def discord_id_for(self, tid):
            return None
        async def post_candidate(self, tid, name, stype, reason, context='', channel=None):
            candidates.append((str(tid), stype, context))
            return True

    mgr = WarTracker(bot=None)
    mgr.strike_manager = FakeLedger()

    async def fake_warn(tid, name, detail):
        warnings.append(str(tid))
    mgr._warn_bleeder = fake_warn

    def war(war_id, made, taken):
        report = {str(TORN_FACTION_ID): {'members': {'1': {'attacks': made}}}}
        stats = {'1': {'total_incoming': taken, 'name': 'Bleeder'}}
        asyncio.run(mgr.check_bleeding(war_id, report, stats))

    # war 1: bled, but once is not a pattern
    war('100', made=2, taken=30)
    assert warnings == [] and candidates == [], (warnings, candidates)
    assert mgr.bleed_streaks['1']['streak'] == 1

    # war 2: bled again -> warned, still not a strike
    war('101', made=1, taken=25)
    assert warnings == ['1'], warnings
    assert candidates == [], 'a warning is not a strike'
    assert mgr.bleed_streaks['1']['warned'] is True

    # war 3: bled after the warning -> candidate
    war('102', made=0, taken=40)
    assert warnings == ['1'], 'must not warn the same person twice'
    assert len(candidates) == 1, candidates
    assert candidates[0][0] == '1' and candidates[0][2] == '102', candidates

    # war 4: clean war -> the record is wiped, not just paused
    war('103', made=20, taken=3)
    assert '1' not in mgr.bleed_streaks, (
        'fixing it must clear the record, not leave them one war from a strike')

    # and the next bad war starts over at "not a pattern"
    war('104', made=0, taken=10)
    assert len(candidates) == 1, 'back to square one, no new candidate'
    assert warnings == ['1']

    # --- an unreadable log must not EXONERATE anyone either ---
    # With no stats every member reads as 0 hits taken, which looks like a
    # clean war and would silently wipe every streak on the books. That is
    # the real damage an unreadable log does here, not a false accusation.
    mgr2 = WarTracker(bot=None)
    mgr2.strike_manager = FakeLedger()
    mgr2.bleed_streaks = {'9': {'streak': 2, 'warned': True, 'last_war': '199'}}
    asyncio.run(mgr2.check_bleeding(
        '200', {str(TORN_FACTION_ID): {'members': {'9': {'attacks': 0}}}}, None))
    assert mgr2.bleed_streaks.get('9', {}).get('streak') == 2, (
        'an unreadable log must leave the record untouched, not read as a '
        f'clean war: {mgr2.bleed_streaks}')


def test_parse_when_accepts_what_people_actually_type():
    """
    Management said the date and time fields were the hard part of making an
    event, not the command. So one free-text box has to accept what somebody
    would write in chat, and still accept the old rigid formats, because the
    point is to stop REQUIRING them.

    Everything is TCT, which is UTC. Nothing here guesses at local time.
    """
    from datetime import datetime, timezone, timedelta
    from utils.when import parse_when, WhenError

    # A Wednesday, 10:00 UTC, so "saturday" and "already gone today" are both
    # testable without waiting for a particular day.
    now = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    assert now.weekday() == 2

    def p(text):
        return parse_when(text, now=now)

    # --- relative ---
    assert p('in 3 days') == now + timedelta(days=3)
    assert p('in 2 hours') == now + timedelta(hours=2)
    assert p('in 90 minutes') == now + timedelta(minutes=90)

    # --- weekday names pick the NEXT one ---
    sat = p('saturday 8pm')
    assert (sat.weekday(), sat.hour, sat.minute) == (5, 20, 0), sat
    assert sat.day == 17, 'the coming Saturday, not one in the past'
    assert p('sat 20:00') == sat, 'abbreviations and 24h agree'
    assert p('Saturday 8:00pm') == sat, 'case and minutes agree'

    # today's weekday, with the time already gone -> next week
    wed = p('wednesday 09:00')
    assert wed.weekday() == 2 and wed.day == 21, (
        f'09:00 Wednesday has passed, so it means next week: {wed}')

    # --- today / tomorrow ---
    assert p('tomorrow 20:00') == datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
    assert p('today 23:00') == datetime(2026, 10, 14, 23, 0, tzinfo=timezone.utc)

    # a bare time already gone today means tomorrow: an event in the past is
    # never what anybody wanted
    assert p('9am') == datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    assert p('8pm') == datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)

    # --- the old rigid formats still work ---
    assert p('25-01-2027 14:30') == datetime(2027, 1, 25, 14, 30, tzinfo=timezone.utc)
    assert p('2027-01-25 14:30') == datetime(2027, 1, 25, 14, 30, tzinfo=timezone.utc)

    # --- noise people type without thinking ---
    assert p('saturday at 8pm TCT') == sat
    assert p('  SATURDAY   8PM  ') == sat
    assert p('saturday @ 8pm') == sat

    # --- 2000-style and the 4-digit-year trap ---
    assert p('tomorrow 2000') == datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
    assert p('25-01-2027 2030') == datetime(2027, 1, 25, 20, 30, tzinfo=timezone.utc), (
        'the year must not be eaten as a time')

    # --- things that must be refused, not guessed ---
    for bad in ('', '   ', 'soonish', 'next month', 'saturday 25:00',
                '32-01-2027 10:00'):
        try:
            p(bad)
            raise AssertionError(f'{bad!r} should not have parsed')
        except WhenError:
            pass

    # a date with no time is refused rather than silently set to midnight
    try:
        p('25-01-2027')
        raise AssertionError('a date with no time should be refused')
    except WhenError as e:
        assert '20:00' in str(e), 'the error should show what to type'


def test_cleanup_only_ever_deletes_the_bots_own_noise():
    """
    Auto-delete is the one feature here that destroys data, so the guards
    matter more than the behaviour.

    Two rules, and the difference between them is the whole risk:

    - Everywhere, it may remove the bot's own messages and Discord's join
      notices, and a member's words are safe at any age.
    - In the welcome pit ONLY, members' messages go too, because the channel
      is onboarding traffic that is worthless a month later.

    A pinned message survives either rule, in every channel - that is the
    escape hatch for anything in the welcome pit worth keeping. And the
    human sweep must never reach a channel that would be cited in a strike
    dispute, which is what the HUMAN_SWEEP_CHANNELS assertion below is for.
    """
    import asyncio
    import discord
    from features.cleanup import CleanupManager
    from config import (WELCOME_CHANNEL_ID, WAR_TRACKER_CHANNEL_ID,
                        STRIKES_CHANNEL_ID, WAR_MANAGEMENT_CHANNEL_ID,
                        INACTIVITY_ALERTS_CHANNEL_ID)

    class U:
        def __init__(self, uid):
            self.id = uid

    class Msg:
        def __init__(self, author_id, mtype=discord.MessageType.default,
                     pinned=False):
            self.author = U(author_id)
            self.type = mtype
            self.pinned = pinned
            self.deleted = False

        async def delete(self):
            self.deleted = True

    class FakeBot:
        user = U(999)

    mgr = CleanupManager(bot=FakeBot())

    bot_msg = Msg(999)
    human_msg = Msg(123)
    join_msg = Msg(123, mtype=discord.MessageType.new_member)
    pinned_bot = Msg(999, pinned=True)
    pinned_join = Msg(123, mtype=discord.MessageType.new_member, pinned=True)

    assert mgr._deletable(bot_msg) is True, 'the bot cleans up after itself'
    assert mgr._deletable(join_msg) is True, 'a join notice is noise'
    assert mgr._deletable(human_msg) is False, (
        "outside the welcome pit a member's own words are never deleted")
    assert mgr._deletable(pinned_bot) is False, 'pinned beats any age rule'
    assert mgr._deletable(pinned_join) is False

    # --- the welcome pit sweeps people's messages too, pins excepted ---
    pinned_human = Msg(123, pinned=True)
    assert mgr._deletable(human_msg, sweep_humans=True) is True, (
        'the welcome pit is onboarding traffic and goes in full')
    assert mgr._deletable(pinned_human, sweep_humans=True) is False, (
        'a pin is the escape hatch and must hold even there')

    # --- and that sweep is scoped to exactly one channel ---
    from features.cleanup import HUMAN_SWEEP_CHANNELS
    assert HUMAN_SWEEP_CHANNELS <= {WELCOME_CHANNEL_ID}, (
        f'only the welcome pit may sweep human messages: '
        f'{HUMAN_SWEEP_CHANNELS}')
    assert WAR_TRACKER_CHANNEL_ID not in HUMAN_SWEEP_CHANNELS
    for protected in (STRIKES_CHANNEL_ID, WAR_MANAGEMENT_CHANNEL_ID,
                      INACTIVITY_ALERTS_CHANNEL_ID):
        if protected:
            assert protected not in HUMAN_SWEEP_CHANNELS, (
                f'{protected} holds evidence - never sweep human messages')

    # --- the swept list is the narrow one, and nothing citable is in it ---
    ids = {cid for cid, _label in mgr._targets()}
    assert ids <= {WELCOME_CHANNEL_ID, WAR_TRACKER_CHANNEL_ID}, ids
    for protected in (STRIKES_CHANNEL_ID, WAR_MANAGEMENT_CHANNEL_ID,
                      INACTIVITY_ALERTS_CHANNEL_ID):
        if protected:
            assert protected not in ids, (
                f'{protected} holds evidence and must never be swept')

    # --- no bot, no deletions ---
    assert asyncio.run(CleanupManager(bot=None).sweep()) == {}


def test_cleanup_sweeps_once_a_week_and_reports_what_it_took():
    """
    Deleting is the one thing here that destroys data, so it happens on a
    schedule it cannot overrun and it never happens silently.

    The week stamp, not "7 days since last run", is what stops a restart
    sweeping twice - and a week the bot was down is skipped rather than
    running late and deleting a double batch.
    """
    import asyncio
    import discord
    from datetime import datetime, timezone
    import features.cleanup as cleanup_mod
    from features.cleanup import CleanupManager

    saved = {}
    cleanup_mod.load_json = lambda path, default=None: dict(saved) or (default or {})
    cleanup_mod.save_json = lambda path, data: saved.update(data)

    sent = []

    class U:
        def __init__(self, uid):
            self.id = uid

    class Msg:
        def __init__(self, author_id, mtype=discord.MessageType.default, pinned=False):
            self.author, self.type, self.pinned = U(author_id), mtype, pinned
            self.gone = False
        async def delete(self):
            self.gone = True

    class Hist:
        def __init__(self, msgs):
            self.msgs = msgs
        def __call__(self, **kw):
            return self
        def __aiter__(self):
            self._it = iter(self.msgs)
            return self
        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class Chan:
        name = 'swept'
        def __init__(self, msgs):
            self.history = Hist(msgs)
        async def send(self, **kw):
            sent.append(kw.get('embed'))

    # 3 bot posts, 1 join notice, 2 human messages, 1 pinned bot post
    msgs = ([Msg(999) for _ in range(3)]
            + [Msg(5, mtype=discord.MessageType.new_member)]
            + [Msg(5), Msg(6)]
            + [Msg(999, pinned=True)])
    chan = Chan(msgs)

    class FakeBot:
        user = U(999)
        def get_channel(self, _cid):
            return chan

    mgr = CleanupManager(bot=FakeBot())
    mgr._targets = lambda: [(1, 'welcome')]
    cleanup_mod.CLEANUP_REPORT_CHANNEL_ID = 1   # unset means no report at all

    res = asyncio.run(mgr.sweep(force=True))
    stat = res['welcome']
    assert stat['deleted'] == 4, stat          # 3 bot + 1 join
    assert stat['bot'] == 3 and stat['joins'] == 1, stat
    assert stat['kept'] == 3, f'2 humans + 1 pinned must survive: {stat}'
    assert sum(1 for m in msgs if m.gone) == 4
    assert not any(m.gone for m in msgs if m.pinned or m.author.id in (5, 6)
                   and m.type is discord.MessageType.default)

    # --- it reported, and the report says what was KEPT too ---
    assert len(sent) == 1, 'a delete must never be silent'
    body = sent[0].description
    assert '4' in body and 'kept 3' in body, body
    assert 'strikes' in body, 'the report states what is never swept'

    # --- same week: refuses to run again ---
    now = datetime.now(timezone.utc)
    year, week, weekday = now.isocalendar()
    cleanup_mod.CLEANUP_WEEKDAY = weekday - 1     # make today the sweep day
    assert mgr._due() is False, (
        'already swept this week, so it must not run again')

    # --- a different weekday is never due, even unswept ---
    saved.clear()
    cleanup_mod.CLEANUP_WEEKDAY = (weekday - 1 + 3) % 7
    assert mgr._due() is False, 'only sweeps on its own weekday'
    assert mgr._due(force=True) is True, 'force is for running it by hand'


def test_discord_roster_classifies_every_way_the_two_lists_drift():
    """
    Every alert the bot sends assumes the Torn roster and the Discord server
    match. They drift four distinct ways, and each costs the member silently,
    so each has to be named separately rather than lumped into "mismatch".

    The one worth being careful about is somebody who DID register and whose
    Discord account has since left the server: they are unreachable exactly
    like someone who never registered, but they are a different conversation,
    so the detail says which.
    """
    from features.discord_roster import classify, build_embed

    roster = {
        '1': {'name': 'Present'},     # registered, in the server: fine
        '2': {'name': 'NoDiscord'},   # never registered, not in the server
        '3': {'name': 'LeftServer'},  # registered, Discord account gone
    }
    verified = {
        '100': {'torn_id': 1, 'torn_name': 'Present'},
        '300': {'torn_id': 3, 'torn_name': 'LeftServer'},
        '400': {'torn_id': 9, 'torn_name': 'Quitter'},   # in server, off roster
        '500': {'torn_id': 8, 'torn_name': 'Ghost'},     # neither: dead record
    }
    present = {'100', '400', '700', '999'}   # 700 unregistered, 999 is a bot
    bots = {'999'}

    r = classify(roster, verified, present, bots)

    names = lambda key: [n for n, _d in r[key]]
    assert names('not_in_discord') == ['NoDiscord', 'LeftServer'] or \
           sorted(names('not_in_discord')) == ['LeftServer', 'NoDiscord'], r
    assert 'Present' not in names('not_in_discord'), 'they are reachable'

    # the two unreachable cases are told apart in the detail
    detail = dict(r['not_in_discord'])
    assert 'never registered' in detail['NoDiscord']
    assert 'left the server' in detail['LeftServer'], detail

    assert names('unregistered') == ['<@700>'], r['unregistered']
    assert '<@999>' not in names('unregistered'), 'a bot never registers'
    assert '<@100>' not in names('unregistered'), 'they did register'

    assert names('left_faction') == ['<@400>'], r['left_faction']
    assert names('stale_link') == ['Ghost'], r['stale_link']

    # --- everything agreeing says so, rather than printing empty sections ---
    clean = classify({'1': {'name': 'Present'}},
                     {'100': {'torn_id': 1, 'torn_name': 'Present'}},
                     {'100'}, set())
    assert all(not v for v in clean.values()), clean
    e = build_embed(clean, 1, 1)
    assert 'agree' in e.title.lower(), e.title

    # --- a mismatch embed counts them and names the sections ---
    e2 = build_embed(r, len(roster), 3)
    assert '5' in e2.description, 'total mismatches stated'
    for fragment in ('not reachable', 'never registered', 'not on the faction'):
        assert fragment in e2.description, fragment


def test_role_sync_only_touches_roles_it_manages():
    """
    roles.py is the highest blast radius untested code in the repo: it adds
    and removes real people's Discord roles, and the audit found it had no
    coverage at all.

    The invariant that matters is containment. sync_member_role may only ever
    remove a role that is in the CONFIGURED position map - a member's Verified
    role, their colour role, anything a human gave them by hand must survive a
    sync untouched. Get that wrong and a sync strips the server.
    """
    import asyncio
    from features.roles import RoleSyncManager

    class Role:
        def __init__(self, rid, name):
            self.id, self.name = rid, name
        def __repr__(self):
            return f'<{self.name}>'
        def __eq__(self, other):
            return isinstance(other, Role) and other.id == self.id
        def __hash__(self):
            return hash(self.id)

    LEADER, MEMBER = Role(10, 'Leader'), Role(11, 'Member')
    VERIFIED, COLOUR = Role(99, 'Verified'), Role(98, 'Pink')

    class Guild:
        roles = [LEADER, MEMBER, VERIFIED, COLOUR]
        def get_role(self, rid):
            return next((r for r in self.roles if r.id == rid), None)

    class Member:
        name = 'Someone'
        def __init__(self, roles):
            self.roles = list(roles)
            self.added, self.removed = [], []
        async def add_roles(self, *roles, reason=None):
            self.added.extend(roles)
            self.roles.extend(roles)
        async def remove_roles(self, *roles, reason=None):
            self.removed.extend(roles)
            self.roles = [r for r in self.roles if r not in roles]

    mgr = RoleSyncManager(bot=None)
    # Positions are keyed lowercase, which get_roles_for_position relies on.
    mgr.position_roles = {'leader': [10], 'member': [11]}
    managed = mgr.get_all_position_role_ids()
    assert managed == {10, 11}, (
        f'the managed set must be exactly the configured roles: {managed}')

    # --- promoted: gains Leader, loses Member, keeps everything else ---
    m = Member([MEMBER, VERIFIED, COLOUR])
    changed = asyncio.run(mgr.sync_member_role(Guild(), m, 'Leader'))
    assert changed is True
    assert LEADER in m.added, m.added
    assert MEMBER in m.removed, m.removed
    assert VERIFIED not in m.removed and COLOUR not in m.removed, (
        f'a sync must never strip roles it does not manage: {m.removed}')
    assert VERIFIED in m.roles and COLOUR in m.roles

    # --- already correct: no API calls at all ---
    m2 = Member([LEADER, VERIFIED])
    assert asyncio.run(mgr.sync_member_role(Guild(), m2, 'Leader')) is False
    assert not m2.added and not m2.removed, 'a no-op must not call Discord'

    # --- unknown position: managed roles go, unmanaged ones stay ---
    m3 = Member([LEADER, VERIFIED, COLOUR])
    asyncio.run(mgr.sync_member_role(Guild(), m3, 'NotAPosition'))
    assert LEADER in m3.removed, 'no mapping means no managed role'
    assert VERIFIED in m3.roles and COLOUR in m3.roles, (
        f'unmanaged roles survive an unknown position: {m3.roles}')


def test_the_bot_never_ticks_its_own_alerts():
    """
    The bot used to pre-add the tick to nudge alerts, as a one-click
    affordance. It was wrong: the count read "1" before anybody had touched
    it, so every alert looked already handled, and a real tick made it 2.

    A reaction is only worth anything if every one of them came from a
    person, so the bot adds none and the message says what the tick is for.
    """
    import asyncio
    from features.inactivity import InactivityTracker

    class Msg:
        def __init__(self):
            self.reactions = []
        async def add_reaction(self, emoji):
            self.reactions.append(emoji)

    sent = Msg()

    class Chan:
        async def send(self, **kw):
            return sent

    mgr = InactivityTracker(bot=None)
    out = asyncio.run(mgr._send_handled(Chan(), object()))
    assert out is sent
    assert sent.reactions == [], (
        f'the bot must not tick its own alert: {sent.reactions}')

    # a send failure returns None rather than raising into the daily check
    class Dead:
        async def send(self, **kw):
            raise RuntimeError('channel gone')
    assert asyncio.run(mgr._send_handled(Dead(), object())) is None


def test_strikes_never_announce_that_there_are_none():
    """
    A clean record is not news. Two places used to say so anyway: a candidate
    opened with "0 active strike(s) already", which is noise on what is
    usually a first offence and buries the line when somebody genuinely IS a
    repeat; and the weekly digest posted "nobody holds an active strike",
    which is a message never worth opening.

    Answering a direct question is different - /strikes member on someone
    clean still says so, because somebody asked.
    """
    import asyncio
    from features.strikes import StrikeManager

    sent = []

    class Chan:
        async def send(self, **kw):
            sent.append(kw.get('embed'))

    class FakeBot:
        def get_channel(self, _):
            return Chan()

    mgr = StrikeManager(bot=FakeBot())
    saved = []
    async def fake_save():
        saved.append(1)
    mgr.save = fake_save

    # --- first offence: no history line at all ---
    asyncio.run(mgr.post_candidate('1', 'Clean', 'oc', 'no OC this week'))
    assert len(sent) == 1
    body = sent[0].description
    assert 'active strike' not in body, (
        f'a clean member must not be told they have 0 strikes: {body}')
    assert 'Pick one' in body, 'the actual instruction survives'

    # --- a repeat offender DOES get the count ---
    mgr.add('2', 'Repeat', 'bonus', 'earlier')
    sent.clear()
    asyncio.run(mgr.post_candidate('2', 'Repeat', 'oc', 'again'))
    assert '1 active strike(s) already' in sent[0].description, sent[0].description

    # --- an empty week posts nothing, but is still stamped ---
    quiet = StrikeManager(bot=FakeBot())
    quiet.save = fake_save
    sent.clear()
    posted = asyncio.run(quiet.post_weekly_digest(force=True))
    assert posted is False and sent == [], (
        f'an empty digest must stay silent: {sent}')
    assert '_digest' in quiet.dismissed, (
        'the week is still stamped, or it retries all day')

    # --- a week with strikes still posts ---
    busy = StrikeManager(bot=FakeBot())
    busy.save = fake_save
    busy.add('3', 'Somebody', 'oc', 'missed')
    sent.clear()
    assert asyncio.run(busy.post_weekly_digest(force=True)) is True
    assert len(sent) == 1 and 'Somebody' in sent[0].description


def test_strike_dm_states_the_published_ladder():
    """
    #faction-edicts publishes a three-step ladder: strike 1 is a warning,
    2 is the final warning, 3 goes to a removal vote. The DM has to say which
    step the member is on, or the ladder only exists for management.

    Two things are guarded here:

    - The position comes from the ledger AFTER the strike is recorded, so a
      first offence reads 1 and not 0. Off by one here tells somebody they
      are on a final warning when they are not.
    - Past 3 it must not fall off the end into silence. A fourth strike is
      still at the removal-vote step, not an unlabelled one.
    """
    from features.strikes import (StrikeManager, dm_text, STRIKE_TYPES,
                                  STRIKE_LADDER_LAST)

    sm = StrikeManager(bot=None)

    # First offence, through the real send path. send_dm returns the text it
    # built even when there is nobody to send it to, which is what makes the
    # count testable without a Discord connection.
    import asyncio
    sm.add('7', 'Seven', 'oc', 'no OC', issued_by='Mgr')
    sent, t1 = asyncio.run(sm.send_dm('7', 'oc', 'no OC'))
    assert sent is False, 'unregistered member: nothing to send to'
    assert 'strike 1 of 3' in t1, t1
    assert 'final warning' not in t1, 'a first offence is not a final warning'

    sm.add('7', 'Seven', 'chain', 'missed the minimum', issued_by='Mgr')
    t2 = dm_text('chain', 'missed the minimum', total=len(sm.active('7')))
    assert 'strike 2 of 3' in t2 and 'final warning' in t2, t2

    sm.add('7', 'Seven', 'bonus', 'bonus on a random', issued_by='Mgr')
    t3 = dm_text('bonus', 'bonus on a random', total=len(sm.active('7')))
    assert STRIKE_LADDER_LAST in t3, t3

    # a fourth still names the step rather than going quiet
    sm.add('7', 'Seven', 'conduct', 'again', issued_by='Mgr')
    t4 = dm_text('conduct', 'again', total=len(sm.active('7')))
    assert STRIKE_LADDER_LAST in t4, t4

    # an expired strike must not inflate the position of a later one
    import time
    from config import STRIKE_EXPIRY_DAYS
    sm2 = StrikeManager(bot=None)
    old = sm2.add('8', 'Eight', 'oc', 'long ago')
    old['issued_at'] = time.time() - (STRIKE_EXPIRY_DAYS + 1) * 86400
    sm2.add('8', 'Eight', 'oc', 'now')
    t = dm_text('oc', 'now', total=len(sm2.active('8')))
    assert 'strike 1 of 3' in t, (
        'a strike that aged out cannot push somebody up the ladder: ' + t)

    # the appeal route matches the rules, which say to appeal to the leader
    assert 'leader' in t.lower(), t
    assert STRIKE_TYPES['oc'] in t


def test_manual_poll_posts_where_the_chaser_looks_for_it():
    """
    A manual /avail setup used to post the poll in whatever channel it was
    typed in. Discord blocks /avail in the availability channel, so that was
    never the availability channel - while post_poll_chasers always posts
    there, saying "X of Y have not answered the poll above". The poll and the
    chase have to be in the same channel or the chase points at nothing.

    The fallback is the part worth guarding: if the availability channel is
    unset or unreachable the poll must still go somewhere. A poll in the
    wrong channel can be deleted and redone; a poll that was never posted
    loses the war's availability entirely.
    """
    import features.availability as av

    class Channel:
        def __init__(self, cid, name):
            self.id, self.name = cid, name

    AVAIL = Channel(999, 'war-availability')
    TYPED_IN = Channel(111, 'management')

    class Client:
        def __init__(self, found):
            self.found = found
        def get_channel(self, cid):
            return self.found if cid == av.AVAILABILITY_CHANNEL_ID else None

    original = av.AVAILABILITY_CHANNEL_ID
    try:
        # --- configured and reachable: the poll goes to the poll channel ---
        av.AVAILABILITY_CHANNEL_ID = 999
        assert av.poll_post_target(Client(AVAIL), TYPED_IN) is AVAIL

        # --- configured but the bot cannot see it: post it anyway ---
        assert av.poll_post_target(Client(None), TYPED_IN) is TYPED_IN, (
            'an unreachable channel must not swallow the poll')

        # --- not configured at all: post where it was asked for ---
        av.AVAILABILITY_CHANNEL_ID = 0
        assert av.poll_post_target(Client(AVAIL), TYPED_IN) is TYPED_IN

        # --- no client at all cannot raise ---
        assert av.poll_post_target(None, TYPED_IN) is TYPED_IN
    finally:
        av.AVAILABILITY_CHANNEL_ID = original


def test_member_command_board_lists_only_member_actions():
    """
    The board in #commands-the-punk is read by the whole faction, so every
    line on it has to be something a plain member can actually run. The
    failure is quiet and annoying: a member reads the board, types the
    command and gets "Management only", which teaches them to stop trying.

    The gating lives in the command handlers, not here, so this test reads
    the real gate expressions out of the source and checks the board against
    them. Rename or re-gate an action and this goes red instead of the board
    quietly lying.
    """
    import io as _io
    import os
    import re
    from features import member_commands as mc
    import features.commands as cmds
    import features.availability as avail

    # --- what the board claims a member can run ---
    listed = {}
    for _title, rows in mc.SECTIONS:
        for cmd, what, chans in rows:
            m = re.match(r'/(\w+)(?:\s+\w+:([^\s]+))?', cmd)
            assert m, f'unparseable board entry: {cmd}'
            listed.setdefault(m.group(1), set()).add((m.group(2) or '').lower())
            assert what, f'{cmd} has no description'

    src = _io.open(cmds.__file__, encoding='utf-8').read()

    # --- /war: the gate is a tuple of management-only views ---
    gate = re.search(r"view_value in \(([^)]*)\) and not is_management", src)
    assert gate, 'could not find the /war management gate - has it moved?'
    war_gated = {x.strip().strip("'\"") for x in gate.group(1).split(',')}
    assert 'board' in war_gated and 'xan' in war_gated, war_gated
    for action in listed.get('war', ()):
        assert action not in war_gated, (
            f'/war view:{action} is management-gated but on the member board')

    # --- /chain: single gated view ---
    chain_gate = re.search(r"view_value == '(\w+)' and not is_management", src)
    assert chain_gate, 'could not find the /chain management gate'
    assert chain_gate.group(1) not in listed.get('chain', ()), (
        f'/chain view:{chain_gate.group(1)} is management-only')

    # --- /avail: these four check is_management in their own branches ---
    avail_src = _io.open(avail.__file__, encoding='utf-8').read()
    for action in ('setup', 'delete', 'coverage', 'activity'):
        assert f"'{action}'" in avail_src, f'{action} vanished from /avail'
        assert action not in listed.get('avail', ()), (
            f'/avail action:{action} is management-only but listed')

    # --- wholly management commands must not appear at all ---
    for name in ('faction', 'strikes', 'verify', 'rolesync', 'announce',
                 'createevent', 'test'):
        assert name not in listed, f'/{name} is management-only but listed'

    # --- and it never sends a member to a channel they cannot see ---
    # Naming a management channel is worse than naming none: the member
    # cannot even open it to find out why the command failed.
    from config import (WAR_MANAGEMENT_CHANNEL_ID, STRIKES_CHANNEL_ID,
                        INACTIVITY_ALERTS_CHANNEL_ID,
                        CLEANUP_REPORT_CHANNEL_ID)
    named = set()
    for _title, rows in mc.SECTIONS:
        for _cmd, _what, chans in rows:
            named.update(c for c in chans if c)
    for hidden in (WAR_MANAGEMENT_CHANNEL_ID, STRIKES_CHANNEL_ID,
                   INACTIVITY_ALERTS_CHANNEL_ID, CLEANUP_REPORT_CHANNEL_ID):
        if hidden:
            assert hidden not in named, (
                f'{hidden} is management-only - members cannot open it')

    # --- every listed command still exists somewhere in the codebase ---
    feature_dir = os.path.dirname(cmds.__file__)
    blob = ''
    for fn in os.listdir(feature_dir):
        if fn.endswith('.py'):
            blob += _io.open(os.path.join(feature_dir, fn),
                             encoding='utf-8').read()
    for name in listed:
        assert f"name='{name}'" in blob, (
            f'/{name} is on the board but no command defines it any more')


def test_member_command_board_renders_without_unset_channels():
    """
    Channel ids come from config, and an unset one is 0. Rendering that would
    print a dead <#0> mention - so an entry whose channels are all unset has
    to fall back to saying "any channel" rather than pointing nowhere.
    """
    from features import member_commands as mc

    assert mc._where([0, 0]) == 'any channel', 'unset ids must not render'
    assert mc._where([]) == 'any channel'
    assert mc._where([0, 123]) == '<#123>', 'the live one survives'
    assert mc._where([123, 456]) == '<#123> or <#456>'

    e = mc.build_embed([('Test', [('/ping', 'checks the bot', [0])])])
    assert '<#0>' not in e.description, e.description
    assert 'any channel' in e.description

    # the real board fits Discord's description limit
    full = mc.build_embed()
    assert len(full.description) <= 4096, len(full.description)
    assert '/register' in full.description


def test_prune_refuses_to_act_on_a_roster_it_could_not_read():
    """
    /verify prune deletes the records that link a person to their Torn
    identity, so the dangerous case is not the one it deletes - it is the one
    it would delete if the roster fetch failed.

    An unreadable roster makes EVERY record look like it points off the
    roster, which would wipe the whole verification database and un-verify
    the entire faction. This is the None-vs-empty contract at its most
    expensive, so prunable() must return nothing at all rather than
    everything.
    """
    from features.discord_roster import prunable

    roster = {'111': {'name': 'Stayed'}, '222': {'name': 'AlsoStayed'}}
    verified = {
        '900': {'torn_id': '111', 'torn_name': 'Stayed'},        # on roster
        '901': {'torn_id': '333', 'torn_name': 'LeftButHere'},   # in server
        '902': {'torn_id': '444', 'torn_name': 'GoneEntirely'},   # dead
        '903': {'torn_id': None, 'torn_name': 'Broken'},          # no id
    }
    in_server = {'900', '901'}

    # --- the outage cases: nothing is prunable, whatever else is true ---
    for unreadable in (None, {}, []):
        assert prunable(unreadable, verified, in_server) == [], (
            'a roster that could not be read must prune NOTHING')

    # --- the normal case: only the record with nobody behind it ---
    dead = prunable(roster, verified, in_server)
    assert [d[0] for d in dead] == ['902'], dead
    assert dead[0][1] == 'GoneEntirely'

    # --- somebody off the roster but still in the server is NOT dead ---
    # They are a real person in the server; removing their record would
    # silently un-verify them.
    assert '901' not in [d[0] for d in dead], (
        'a member still in the server keeps their record'
    )

    # --- a record with no torn_id is left alone rather than guessed at ---
    assert '903' not in [d[0] for d in dead]

    # --- nothing verified at all cannot crash ---
    assert prunable(roster, {}, in_server) == []
    assert prunable(roster, None, None) == []

    # --- an empty server (no members fetched) does not make everyone dead ---
    # It does widen the set, which is correct: the roster is what proves
    # faction membership, and these two are off it.
    wide = prunable(roster, verified, set())
    assert sorted(d[0] for d in wide) == ['901', '902'], wide


def test_config_check_tells_the_difference_between_unset_and_deleted():
    """
    Every posting feature in this bot guards itself: get_channel() returns
    None, a warning goes to the log, the function returns. That is correct
    and it is also why a deleted channel is invisible - no error, no alert,
    the feature just stops. Strike candidates stop appearing and nobody
    knows until a strike was never raised.

    Two distinctions this has to get right:

    - An id of 0 is a CHOICE (nothing configured), an id that resolves to
      nothing is a FAULT (it pointed at something that got deleted). Lumping
      them together means either nagging about features nobody wanted or
      staying quiet about a broken one.
    - When everything resolves it must build NO embed at all. A config check
      that posts "all fine" on every restart is one more thing nobody reads.
    """
    from features.config_check import audit, build_embed

    rows = [
        ('GOOD_CHANNEL', 'channel', 111, 'alerts', True),
        ('DELETED_CHANNEL', 'channel', 222, 'strike candidates', True),
        ('UNSET_REQUIRED', 'channel', 0, 'the war board', True),
        ('UNSET_OPTIONAL', 'channel', 0, 'event posts', False),
        ('GOOD_ROLE', 'role', 333, 'the management ping', True),
        ('DELETED_ROLE', 'role', 444, 'who counts as management', True),
    ]
    live = {('channel', 111), ('role', 333)}

    def resolve(kind, value):
        return object() if (kind, value) in live else None

    broken, unset = audit(rows, resolve)
    assert [b[0] for b in broken] == ['DELETED_CHANNEL', 'DELETED_ROLE'], broken
    assert [u[0] for u in unset] == ['UNSET_REQUIRED'], (
        'an optional id left at 0 is a choice, not a problem: ' + str(unset))

    e = build_embed(broken, unset)
    assert e is not None
    assert 'strike candidates' in e.description, (
        'the report has to say what stops working, not just the var name')
    assert 'DELETED_ROLE' in e.description
    assert 'UNSET_OPTIONAL' not in e.description

    # --- everything healthy: nothing to say ---
    ok_broken, ok_unset = audit(
        [('GOOD_CHANNEL', 'channel', 111, 'alerts', True)], resolve)
    assert (ok_broken, ok_unset) == ([], [])
    assert build_embed(ok_broken, ok_unset) is None, (
        'a clean check must post nothing at all'
    )

    # --- a non-numeric id is a fault, not a crash ---
    junk, _ = audit([('TYPO', 'channel', 'not-a-number', 'alerts', True)],
                    lambda k, v: None)
    assert [j[0] for j in junk] == ['TYPO']

    # --- the real config is wired up, not an empty list ---
    from features.config_check import entries
    real = entries()
    names = {r[0] for r in real}
    assert 'STRIKES_CHANNEL_ID' in names and 'AVAILABILITY_CHANNEL_ID' in names
    assert all(len(r) == 5 for r in real), 'every row needs a purpose and a flag'


def test_rolesync_never_hands_out_administrator_by_torn_position():
    """
    /rolesync setrole is one line away from a disaster: map a Torn position
    to an Administrator role and whoever holds that position in Torn - now or
    after any future promotion - can delete the server. Map it to a
    management role and a whole squad can issue and dismiss strikes.

    Administrator is refused outright because no Torn position should ever
    confer it. Management is allowed but has to be asked for twice.
    """
    from features.roles import privilege_level

    ADMIN = 1 << 3
    MGMT_IDS = [111, 222]

    # Administrator is the refusal, whatever the id is
    assert privilege_level(ADMIN, 999, MGMT_IDS) == 'admin'
    assert privilege_level(ADMIN | (1 << 11), 111, MGMT_IDS) == 'admin', (
        'admin outranks the management check')

    # a management role by id, without admin
    assert privilege_level(1 << 11, 111, MGMT_IDS) == 'management'
    assert privilege_level(0, '222', MGMT_IDS) == 'management', (
        'ids arrive as strings from config and must still match')

    # an ordinary team role is free to map - this is the normal case
    assert privilege_level(1 << 11, 777, MGMT_IDS) is None
    assert privilege_level(0, 777, []) is None
    assert privilege_level(0, 777, None) is None

    # junk permissions must not crash or silently read as safe-by-accident
    assert privilege_level(None, 111, MGMT_IDS) == 'management'
    assert privilege_level('not-a-number', 777, MGMT_IDS) is None


def test_lobby_nag_waits_a_day_paces_itself_and_gives_up():
    """
    This feature pings real people in the one channel they can see, so every
    guard here is about NOT being a machine gun.

    - Nothing on day one. Somebody who joined an hour ago is reading the
      welcome message; a bot interrupting them is worse than useless.
    - Then once every two days, never on consecutive runs. The daily loop
      calls this every single day, so without the cadence check a member
      would be pinged every 24h until they gave up on the server.
    - It stops after MAX_NAGS. If three reminders did not work, a human has
      to ask them in Torn, and the bot pestering forever only trains people
      to mute the channel.
    - Registering or leaving removes them from the state file, so nothing
      accumulates and a rejoin does not resume mid-nag.
    """
    from features.lobby import (classify_lobby, NAG_AFTER_HOURS,
                                NAG_EVERY_HOURS, MAX_NAGS,
                                ESCALATE_AFTER_DAYS)

    HOUR, DAY = 3600, 86400
    now = 1_000_000_000

    def member(mid, hours_ago):
        return {'id': mid, 'name': f'M{mid}', 'joined_at': now - hours_ago * HOUR}

    fresh = member('1', 2)                       # joined 2 hours ago
    day_old = member('2', NAG_AFTER_HOURS + 1)   # just past the quiet period
    stubborn = member('3', ESCALATE_AFTER_DAYS * 24 + 5)

    # --- day one is quiet ---
    nag, esc, state = classify_lobby([fresh], set(), now=now, state={})
    assert nag == [] and esc == [], 'a brand new member must not be pinged'
    assert state == {}, 'and must not even be tracked yet'

    # --- past a day: one nag, recorded ---
    nag, esc, state = classify_lobby([day_old], set(), now=now, state={})
    assert [m['id'] for m in nag] == ['2'], nag
    assert state['2']['nags'] == 1 and state['2']['last'] == now

    # --- the very next day: silence, because the loop runs daily ---
    nag2, _esc, state2 = classify_lobby([day_old], set(), now=now + DAY,
                                        state=state)
    assert nag2 == [], 'nagging every single day is how a channel gets muted'
    assert state2['2']['nags'] == 1, 'and the count must not creep up'

    # --- two days later: nagged again ---
    nag3, _esc, state3 = classify_lobby([day_old], set(),
                                        now=now + NAG_EVERY_HOURS * HOUR,
                                        state=state)
    assert [m['id'] for m in nag3] == ['2']
    assert state3['2']['nags'] == 2

    # --- it gives up after MAX_NAGS ---
    spent = {'2': {'nags': MAX_NAGS, 'last': 0}}
    nag4, _esc, _s = classify_lobby([day_old], set(), now=now, state=spent)
    assert nag4 == [], f'must stop after {MAX_NAGS} attempts'

    # --- escalation kicks in at three days, and keeps reporting ---
    _nag, esc5, _s = classify_lobby([stubborn], set(), now=now, state=spent)
    assert [m['id'] for m in esc5] == ['3'], esc5

    # --- registering removes them entirely ---
    nag6, esc6, state6 = classify_lobby([day_old, stubborn], {'2', '3'},
                                        now=now, state=state3)
    assert nag6 == [] and esc6 == [], 'a registered member is not in the lobby'
    assert state6 == {}, 'and is dropped from the state file'

    # --- leaving the server drops them too ---
    _n, _e, state7 = classify_lobby([], set(), now=now, state=state3)
    assert state7 == {}, 'state must not grow forever'

    # --- no join date: tracked by nobody, pestered by nobody ---
    nodate = {'id': '9', 'name': 'M9', 'joined_at': 0}
    nag8, esc8, state8 = classify_lobby([nodate], set(), now=now, state={})
    assert nag8 == [] and esc8 == [] and state8 == {}

    # --- the message names them and offers both routes ---
    from features.lobby import nag_text
    t = nag_text(['2', '3'])
    assert '<@2>' in t and '<@3>' in t, 'the nag has to actually ping them'
    assert '/register' in t and 'link' in t.lower()
    assert 'API key' in t, 'welcome-pit is where people paste keys'


def test_welcome_board_stays_pinned_and_avoids_dead_channel_links():
    """
    This post is the only instruction an UNVERIFIED member can read, so two
    things have to hold.

    The pin, because the welcome pit is swept weekly INCLUDING members' own
    messages - and pinned is the only exemption. An unpinned board is a board
    that gets deleted in thirty days, leaving newcomers with nothing.

    And no <#id> channel mentions. An unverified member cannot see any of
    those channels, so a mention renders as a dead link for exactly the
    audience this post exists for - plain text names instead.
    """
    import asyncio
    from features.welcome_board import build_embed, refresh

    e = build_embed()
    assert '<#' not in e.description, (
        'channel mentions are dead links for unverified members: '
        + e.description)
    assert '/register' in e.description, 'the fallback route has to be there'
    assert 'API key' in e.description
    assert len(e.description) <= 4096

    class Msg:
        def __init__(self, author_id, embeds=(), pinned=False):
            self.author = type('U', (), {'id': author_id})()
            self.embeds = list(embeds)
            self.pinned = pinned
            self.deleted = False
        async def pin(self):
            self.pinned = True
        async def unpin(self):
            self.pinned = False
        async def delete(self):
            self.deleted = True

    class Channel:
        def __init__(self, msgs=None):
            self.msgs = list(msgs or [])
            self.sent = []
        def history(self, limit=100):
            msgs = self.msgs
            class It:
                def __aiter__(self_):
                    self_._i = iter(msgs)
                    return self_
                async def __anext__(self_):
                    try:
                        return next(self_._i)
                    except StopIteration:
                        raise StopAsyncIteration
            return It()
        async def send(self, embed=None):
            m = Msg(7, [embed])
            self.sent.append(m)
            self.msgs.insert(0, m)
            return m

    class Bot:
        user = type('U', (), {'id': 7})()
        def __init__(self, channel):
            self._c = channel
        def get_channel(self, cid):
            return self._c

    # --- empty channel: posts AND pins ---
    ch = Channel()
    assert asyncio.run(refresh(Bot(ch), channel_id=1)) == 'posted'
    assert len(ch.sent) == 1
    assert ch.sent[0].pinned is True, (
        'an unpinned board is deleted by the weekly sweep')

    # --- already correct: no repost, no duplicate ---
    ch2 = Channel([Msg(7, [build_embed()], pinned=True)])
    assert asyncio.run(refresh(Bot(ch2), channel_id=1)) == 'unchanged'
    assert ch2.sent == [], 'must not post a second copy'

    # --- correct text but somebody unpinned it: re-pin, still no repost ---
    unpinned = Msg(7, [build_embed()], pinned=False)
    ch3 = Channel([unpinned])
    assert asyncio.run(refresh(Bot(ch3), channel_id=1)) == 'unchanged'
    assert unpinned.pinned is True, 'an unpinned board must be re-pinned'
    assert ch3.sent == []

    # --- stale text: new one posted and pinned, old one removed ---
    stale_embed = build_embed()
    stale_embed.description = 'old wording'
    stale = Msg(7, [stale_embed], pinned=True)
    ch4 = Channel([stale])
    assert asyncio.run(refresh(Bot(ch4), channel_id=1)) == 'posted'
    assert ch4.sent[0].pinned is True
    assert stale.deleted is True and stale.pinned is False

    # --- no channel configured: does nothing rather than crashing ---
    class NoChannel(Bot):
        def get_channel(self, cid):
            return None
    assert asyncio.run(refresh(NoChannel(None), channel_id=1)) is None
    assert asyncio.run(refresh(None, channel_id=1)) is None


def test_stakeout_suggestions_rank_by_danger_to_us_not_in_the_abstract():
    """
    "Strong" has no absolute meaning - measured on a live war, an enemy at
    817k was a real threat to one of ours at 516k and irrelevant to another
    at 7.5m standing in the same country. So the suggestions are banded
    against OUR roster, and the bands come from the same threat_bands() the
    war board uses, because a watch list and a board that disagree about the
    same enemy is worse than either being wrong alone.

    Two things must hold:
    - soft targets are never suggested. Knowing when somebody you can already
      beat wakes up is not worth an API call on a timer.
    - no enemy roster means no suggestions, rather than an empty list read as
      "nobody here is dangerous".
    """
    from features.stakeout_suggest import pick_targets, build_embed

    own = {f'o{i}': {'name': f'Ours{i}', 'level': 30} for i in range(5)}
    ff = {f'o{i}': {'bs_estimate': v} for i, v in
          enumerate([1_000_000, 900_000, 800_000, 200_000, 100_000])}
    # our avg 600k, top-3 avg 900k

    enemy = {
        '1': {'name': 'Monster', 'level': 60},
        '2': {'name': 'Solid', 'level': 50},
        '3': {'name': 'Weakling', 'level': 10},
        '4': {'name': 'Biggest', 'level': 55},
    }
    ff.update({
        '1': {'bs_estimate': 950_000},    # above top-3 -> AVOID
        '2': {'bs_estimate': 700_000},    # above avg   -> CAUTION
        '3': {'bs_estimate': 50_000},     # below avg   -> soft, skipped
        '4': {'bs_estimate': 2_000_000},  # way above   -> AVOID, worst
    })

    picks = pick_targets(enemy, own, ff, has_ff=True)
    names = [p[1] for p in picks]
    assert 'Weakling' not in names, f'soft targets must not be watched: {names}'
    assert names[0] == 'Biggest', f'worst first: {names}'
    assert set(names) == {'Biggest', 'Monster', 'Solid'}, names
    assert picks[0][4] == 'AVOID' and picks[-1][4] == 'CAUTION'

    # --- the limit is respected, keeping the worst ---
    many = {str(i): {'name': f'E{i}', 'level': 99} for i in range(20)}
    ffm = {str(i): {'bs_estimate': 1_000_000 + i} for i in range(20)}
    ffm.update({f'o{i}': ff[f'o{i}'] for i in range(5)})
    capped = pick_targets(many, own, ffm, has_ff=True, limit=3)
    assert len(capped) == 3
    assert [p[1] for p in capped] == ['E19', 'E18', 'E17'], capped

    # --- no enemy roster: say nothing ---
    assert pick_targets({}, own, ff, has_ff=True) == []
    assert pick_targets(None, own, ff, has_ff=True) == []

    # --- no FFScouter: falls back to level rather than giving up ---
    own_lvl = {f'o{i}': {'name': f'Ours{i}', 'level': lv}
               for i, lv in enumerate([80, 70, 60, 20, 10])}
    picks_lvl = pick_targets(enemy, own_lvl, {}, has_ff=False)
    assert picks_lvl, 'a missing stat source must not empty the list'
    assert 'Weakling' not in [p[1] for p in picks_lvl], (
        'the level fallback still has to exclude the obviously weak')

    # --- the embed hands over runnable commands, and is silent when empty ---
    e = build_embed(picks, 'Enemy Faction', has_ff=True)
    assert '/enemy stakeout add torn_id:4' in e.description, e.description
    assert 'Biggest' in e.description
    assert build_embed([], 'Enemy Faction', has_ff=True) is None

    # --- and says so when it is guessing from levels ---
    e2 = build_embed(picks_lvl, 'Enemy Faction', has_ff=False)
    assert 'level' in e2.description.lower()


def test_xan_report_marks_who_actually_qualifies():
    """
    The rules reimburse Xanax at ten hits, friendly hosps counting toward the
    ten. The report printed totals and left the counting to the reader, which
    is how somebody gets told they missed it by one when they did not.

    The boundary is the test: exactly ten qualifies, nine does not.
    """
    from features.wars import XAN_REIMBURSE_MIN_HITS

    assert XAN_REIMBURSE_MIN_HITS == 10, 'the rules say ten'

    def qualifies(war, chain, hosp):
        return (war + chain + hosp) >= XAN_REIMBURSE_MIN_HITS

    assert qualifies(10, 0, 0) is True, 'exactly ten is in'
    assert qualifies(9, 0, 0) is False
    assert qualifies(5, 0, 5) is True, (
        'friendly hosps count toward the ten - they cost the same energy and '
        'were thrown on instruction')
    assert qualifies(0, 10, 0) is True, 'chain hits count too'
    assert qualifies(3, 3, 3) is False


def test_poll_closes_at_the_deadline_the_rules_actually_state():
    """
    #faction-edicts says answer at least 24 hours before the war. The poll
    used to stay open until an hour before, which made the written deadline
    fiction - somebody could answer 17 hours late, still count as answered,
    and answering is exactly what exempts them from the absence strike.

    The ordering is the real guard: the chase has to land while the poll is
    still OPEN. Chase after the close and it is pure noise, telling people to
    click something that no longer takes clicks.
    """
    from features.availability import (WAR_POLL_CLOSE_LEAD_SECONDS,
                                       WAR_POLL_LEAD_SECONDS)
    from features.wars import POLL_CHASE_CHECKPOINTS, REVIVE_CHECK_CHECKPOINTS

    assert WAR_POLL_CLOSE_LEAD_SECONDS == 24 * 3600, (
        'the rules say 24 hours, so the poll closes at 24 hours')

    chase = min(POLL_CHASE_CHECKPOINTS)
    assert chase > WAR_POLL_CLOSE_LEAD_SECONDS, (
        f'the chase ({chase}s before) must come BEFORE the poll closes '
        f'({WAR_POLL_CLOSE_LEAD_SECONDS}s before), or it chases a closed poll')

    assert WAR_POLL_LEAD_SECONDS > chase, (
        'the poll has to exist before anybody is chased about it')

    # revives stay late on purpose: it is a thing to do just before fighting,
    # not something to action a day out and forget
    assert min(REVIVE_CHECK_CHECKPOINTS) < WAR_POLL_CLOSE_LEAD_SECONDS

    # and the window to answer is a real one, not minutes
    window = (WAR_POLL_LEAD_SECONDS - WAR_POLL_CLOSE_LEAD_SECONDS) / 3600
    assert window >= 12, f'only {window}h to answer is not a poll'


def test_weekly_activity_check_runs_on_its_day_and_only_once():
    """
    The quiet-member list moved off the war countdown. Six hours before a war
    it was interesting and useless - you cannot act on it without pulling
    somebody out of a war already enlisted for. On a Monday it is actionable.

    Same week-stamp guard as the other weekly jobs: a restart must not post
    it twice, and a week the bot was down is skipped rather than arriving
    late on a Thursday.
    """
    import asyncio
    import datetime as dt
    import features.wars as wars_mod
    from features.wars import WarTracker

    saved = {}
    orig_load, orig_save = wars_mod.load_json, wars_mod.save_json

    calls = []

    class FakeChannel:
        async def send(self, *a, **k):
            calls.append('sent')

    tracker = WarTracker(bot=None)
    tracker.bot = type('B', (), {'get_channel': lambda self, cid: FakeChannel()})()

    async def fake_prewar(channel, label):
        calls.append(f'posted:{label}')
        return True
    tracker.post_prewar_offline = fake_prewar

    import utils.helpers as helpers_mod
    real_load, real_save = helpers_mod.load_json, helpers_mod.save_json
    helpers_mod.load_json = lambda path, default=None: dict(saved) or (default or {})
    helpers_mod.save_json = lambda path, data: saved.update(data)
    try:
        today = dt.datetime.now(dt.timezone.utc).isocalendar()[2] - 1

        # --- not its day: silent ---
        from config import PREWAR_REPORT_WEEKDAY
        import config as config_mod
        config_mod.PREWAR_REPORT_WEEKDAY = (today + 3) % 7
        assert asyncio.run(tracker.post_weekly_activity_check()) is False
        assert calls == [], 'must not post on the wrong day'

        # --- its day: posts once ---
        config_mod.PREWAR_REPORT_WEEKDAY = today
        assert asyncio.run(tracker.post_weekly_activity_check()) is True
        assert any(c.startswith('posted:') for c in calls)
        assert 'last_week' in saved

        # --- same week again (a restart): silent ---
        before = len(calls)
        assert asyncio.run(tracker.post_weekly_activity_check()) is False
        assert len(calls) == before, 'a restart must not post it twice'

        # --- force ignores both guards, for testing ---
        assert asyncio.run(tracker.post_weekly_activity_check(force=True)) is True

        # --- an unreadable roster leaves the week open to retry ---
        saved.clear()
        async def failing(channel, label):
            return False
        tracker.post_prewar_offline = failing
        assert asyncio.run(tracker.post_weekly_activity_check(force=True)) is False
        assert 'last_week' not in saved, (
            'a failed read must not stamp the week as done')
    finally:
        helpers_mod.load_json, helpers_mod.save_json = real_load, real_save
        config_mod.PREWAR_REPORT_WEEKDAY = PREWAR_REPORT_WEEKDAY


def test_management_is_never_pinged_into_a_channel_with_nothing_in_it():
    """
    Seen in production on 21/10/2026: @Management was pinged in the strikes
    channel and nothing followed it.

    The cause is the gap between "somebody is OC-critical" and "something
    gets posted about it". The content is a strike CANDIDATE, and
    post_candidate deliberately posts nothing for a member who already has an
    open candidate or a live strike for the same thing - which is correct,
    re-raising them every morning would bury the ones needing a decision. But
    the ping fired on the critical list being non-empty, so two already-struck
    members produced a ping and no message.

    A ping with nothing under it is worse than no ping: it trains people to
    ignore the mention that exists to make them look.
    """
    import asyncio
    import features.inactivity as inactivity_mod
    from features.inactivity import InactivityTracker

    # Pings are off in the test environment (no role id configured), which
    # would make every assertion below pass for the wrong reason.
    real_ping = inactivity_mod.MANAGEMENT_PING_ROLE_ID
    inactivity_mod.MANAGEMENT_PING_ROLE_ID = 424242

    sends = {'alerts': [], 'strikes': []}

    class FakeChannel:
        def __init__(self, bucket, cid):
            self.bucket, self.id = bucket, cid
        async def send(self, content=None, embed=None):
            sends[self.bucket].append(content if content else
                                      f'EMBED:{embed.title}')
            return None

    alerts = FakeChannel('alerts', 1)
    strikes = FakeChannel('strikes', 2)

    class FakeStrikes:
        """Nobody is raisable: everyone already holds a strike."""
        def __init__(self, open_ids=()):
            self.open_ids = set(open_ids)
            self.posted = []
        def is_open(self, stype, torn_id, context=''):
            return str(torn_id) in self.open_ids
        async def post_candidate(self, torn_id, name, stype, reason,
                                 context='', channel=None):
            self.posted.append(str(torn_id))
            return True

    tracker = InactivityTracker(bot=None)
    tracker.bot = type('B', (), {'get_channel': lambda self, cid: strikes})()

    oc_critical = [
        {'torn_id': '111', 'torn_name': 'AlreadyStruck', 'torn_level': 20,
         'days': 6.0, 'last_oc_ts': None, 'never_joined': False},
        {'torn_id': '222', 'torn_name': 'AlsoStruck', 'torn_level': 30,
         'days': 20.0, 'last_oc_ts': None, 'never_joined': False},
    ]

    # --- nobody raisable: no ping anywhere, nothing posted ---
    tracker.strike_manager = FakeStrikes(open_ids=())
    asyncio.run(tracker._send_inactivity_alert(
        alerts, [], [], 35, oc_warning=[], oc_critical=oc_critical,
        left_faction=[]))
    pings = [m for m in sends['strikes'] if m and '<@&' in str(m)]
    assert pings == [], (
        f'pinged with nothing to show: {sends["strikes"]}')
    assert tracker.strike_manager.posted == []

    # --- one of them IS raisable: ping, and only that one is raised ---
    sends['alerts'].clear(); sends['strikes'].clear()
    tracker.strike_manager = FakeStrikes(open_ids={'222'})
    asyncio.run(tracker._send_inactivity_alert(
        alerts, [], [], 35, oc_warning=[], oc_critical=oc_critical,
        left_faction=[]))
    assert tracker.strike_manager.posted == ['222'], (
        f'only the raisable one: {tracker.strike_manager.posted}')

    # --- and an ordinary inactivity alert still pings, because it always
    #     posts its embed ---
    sends['alerts'].clear(); sends['strikes'].clear()
    tracker.strike_manager = FakeStrikes(open_ids=())
    warning = [{'torn_name': 'Quiet', 'torn_id': '333', 'torn_level': 10,
                'days': 4, 'last_seen': 1700000000, 'discord_member': None}]
    asyncio.run(tracker._send_inactivity_alert(
        alerts, warning, [], 35, oc_warning=[], oc_critical=[],
        left_faction=[]))
    try:
        assert any('<@&' in str(m) for m in sends['alerts']), (
            'a real alert must still ping')
        assert any(str(m).startswith('EMBED:') for m in sends['alerts']), (
            'and the content has to follow it')
    finally:
        inactivity_mod.MANAGEMENT_PING_ROLE_ID = real_ping


def test_roster_check_does_not_print_a_mention_it_knows_is_dead():
    """
    The roster check reports accounts that have LEFT the server. Rendering
    those as <@id> mentions means Discord cannot resolve them, so the line
    reads "registered as <@764919505817436200>, but that account has left" -
    raw text in the middle of the sentence, in the one line whose whole point
    is that the account is gone.
    """
    from features.discord_roster import classify

    roster = {'1': {'name': 'Gone'}}
    verified = {'999': {'torn_id': '1', 'torn_name': 'Gone'}}
    result = classify(roster, verified, guild_member_ids=set())

    detail = result['not_in_discord'][0][1]
    assert '<@' not in detail, f'dead mention rendered raw: {detail}'
    assert '999' in detail, 'the id is still worth showing, as plain text'

    # the still-here case is unaffected: that mention resolves fine
    present = classify(roster, verified, guild_member_ids={'999'})
    assert present['not_in_discord'] == []


def test_timezone_board_survives_daylight_saving():
    """
    The reason the store holds an IANA zone and not a UTC offset.

    An offset is right for about five months at a time. The moment NZ or the
    UK moves its clocks, a stored "+13" is an hour wrong, the board says
    somebody is asleep when they are up, and nobody notices until a chain is
    called at the wrong hour. zoneinfo recomputes it from the zone, so the
    same stored value is correct in both halves of the year.

    Checked at two real instants six months apart - NZ and the UK are in
    opposite daylight-saving states, which is the case most likely to be got
    wrong.
    """
    from datetime import datetime, timezone
    from features.timezones import group_by_region, local_now, friendly_time

    store = {
        '1': {'zone': 'Pacific/Auckland', 'name': 'Ikaros22'},
        '2': {'zone': 'Europe/London', 'name': 'GDawng'},
    }

    # --- January: NZ on summer time (+13), UK on GMT (+0) ---
    jan = datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc)
    by_zone = {g['zone']: g for g in group_by_region(store, now=jan)}
    assert by_zone['Pacific/Auckland']['offset_hours'] == 13, by_zone
    assert by_zone['Europe/London']['offset_hours'] == 0, by_zone

    # --- July: NZ back to +12, UK on BST (+1) ---
    jul = datetime(2027, 7, 15, 12, 0, tzinfo=timezone.utc)
    by_zone = {g['zone']: g for g in group_by_region(store, now=jul)}
    assert by_zone['Pacific/Auckland']['offset_hours'] == 12, (
        'NZ left daylight saving and the board must follow')
    assert by_zone['Europe/London']['offset_hours'] == 1, (
        'the UK entered BST and the board must follow')

    # --- east to west, so the board reads like the sun moving ---
    order = [g['zone'] for g in group_by_region(store, now=jan)]
    assert order == ['Pacific/Auckland', 'Europe/London'], order

    # --- a junk zone is dropped, not crashed on ---
    assert local_now('Mars/Olympus') is None
    assert group_by_region({'9': {'zone': 'Mars/Olympus'}}, now=jan) == []

    # --- and nobody set: empty, not an error ---
    assert group_by_region({}, now=jan) == []
    assert group_by_region(None, now=jan) == []

    # --- 12-hour clock, written by hand because strftime for it is
    #     platform-specific ---
    assert friendly_time(datetime(2027, 1, 1, 0, 5)) == '12:05 am'
    assert friendly_time(datetime(2027, 1, 1, 12, 0)) == '12:00 pm'
    assert friendly_time(datetime(2027, 1, 1, 13, 7)) == '1:07 pm'


def test_timezone_picker_fits_discord_and_the_clocks_match():
    """
    Two menus because Discord allows 25 options in one, and the world does
    not fit. Every option's value has to be a zone the library can actually
    resolve, or picking it saves something the board then silently drops.
    """
    from zoneinfo import ZoneInfo
    from features.timezones import (REGIONS_WEST, REGIONS_EAST, ALL_REGIONS,
                                    clock_emoji, sparkline)

    assert len(REGIONS_WEST) <= 25 and len(REGIONS_EAST) <= 25, 'Discord cap'
    assert len(ALL_REGIONS) > 25, 'one menu would not have been enough'

    zones = [z for _e, _l, z in ALL_REGIONS]
    assert len(zones) == len(set(zones)), 'a duplicate zone means two rows'
    for zone in zones:
        ZoneInfo(zone)      # raises if the zone is not real

    # the clock face has to match the hour, or the board lies in a way that
    # looks deliberate
    assert clock_emoji(3, 0) == '\U0001f552', 'three o clock'
    assert clock_emoji(15, 0) == '\U0001f552', '3pm is the same face'
    assert clock_emoji(12, 0) == '\U0001f55b', 'twelve'
    assert clock_emoji(3, 30) == '\U0001f55e', 'half past three'
    assert clock_emoji(3, 50) == '\U0001f553', 'ten to four rounds to four'

    # sparkline is relative to the busiest hour, so the day has a shape
    flat = sparkline({h: 0.5 for h in range(24)})
    assert len(flat) == 24 and len(set(flat)) == 1
    peaked = sparkline({h: (1.0 if h == 14 else 0.1) for h in range(24)})
    assert peaked[14] == '█' and peaked[0] == '▁', peaked
    assert sparkline({}) == '' and sparkline(None) == ''


def test_notices_never_turn_an_unreadable_channel_into_an_accusation():
    """
    Three faction rules say "without a notice", so this decides whether an
    absence is an offence. The dangerous failure is not missing a notice -
    it is the bot implying somebody never posted one.

    So an unreadable channel returns None, which renders no line at all,
    rather than {} which would read as "checked, nobody gave notice". Same
    None-vs-empty contract as the rest of this repo, in the one place where
    getting it wrong gets a member struck for something they did correctly.

    It is also deliberately not a date parser. Real notices read "I'm away
    29th Sept to 3rd Oct" and "holiday 29jul-29aug"; a parser that is wrong
    one time in ten would clear someone who never posted, or flag someone who
    did. It only answers who posted, when, and what they said.
    """
    from features.notices import summarise, note_for, NOTICE_WINDOW_DAYS

    DAY = 86400
    now = 1_800_000_000

    msgs = [
        # Older one FIRST on purpose: if this test fed them newest-first,
        # "keep the latest" and "keep the first seen" would be the same
        # answer and the comparison would never be exercised.
        {'author_id': '1', 'timestamp': now - 9 * DAY,
         'content': 'older notice from the same person'},
        {'author_id': '1', 'timestamp': now - 2 * DAY,
         'content': "I'm away 29th Sept to 3rd Oct, may have no signal"},
        {'author_id': '2', 'timestamp': now - 40 * DAY,
         'content': 'On summer leave from 14-30 of August'},
        {'author_id': '3', 'timestamp': now - (NOTICE_WINDOW_DAYS + 5) * DAY,
         'content': 'ancient, outside the window'},
        {'author_id': '4', 'timestamp': now - DAY, 'content': '   '},
    ]

    out = summarise(msgs, now=now)

    assert out['1']['text'].startswith("I'm away 29th"), (
        f'the LATEST notice wins, not the first seen: {out["1"]}')
    assert '2' in out, 'a month-old notice still counts - people go away for months'
    assert '3' not in out, 'past the window it stops counting'
    assert '4' not in out, (
        'an empty message is not evidence of anything, so it must not '
        'produce a "posted a notice" line')

    # --- the line that hangs off an alert ---
    line = note_for('1', out, now=now)
    assert 'notice 2 days ago' in line and '29th Sept' in line, line
    assert note_for('99', out, now=now) is None, 'no notice, no line'

    # --- THE guard: an unreadable channel reports None, never {} ---
    # {} would read as "checked, nobody gave notice", and the Monday list
    # words itself differently for the two cases - "none of them posted"
    # versus "could not be read, check by hand". Getting this backwards
    # strikes somebody for an absence they declared properly.
    import asyncio
    from features.notices import fetch

    class NoChannelBot:
        user = None
        def get_channel(self, cid):
            return None

    assert asyncio.run(fetch(NoChannelBot(), 123)) is None, (
        'a missing channel must be None, not an empty dict')
    assert asyncio.run(fetch(None, 123)) is None
    assert asyncio.run(fetch(NoChannelBot(), 0)) is None

    class ExplodingChannel:
        def history(self, limit=100):
            class It:
                def __aiter__(self_):
                    return self_
                async def __anext__(self_):
                    raise RuntimeError('discord said no')
            return It()

    class BrokenBot:
        user = None
        def get_channel(self, cid):
            return ExplodingChannel()

    assert asyncio.run(fetch(BrokenBot(), 123)) is None, (
        'a channel that fails mid-read is unreadable, not empty')

    assert note_for('1', None, now=now) is None

    # --- today and yesterday read as words, because the question is
    #     "recent enough to explain this?" ---
    fresh = summarise([{'author_id': '7', 'timestamp': now - 3600,
                        'content': 'off for a bit'}], now=now)
    assert 'today' in note_for('7', fresh, now=now)

    # --- a long notice is cut, not dumped into the embed whole ---
    long_msg = summarise([{'author_id': '8', 'timestamp': now,
                           'content': 'x' * 400}], now=now)
    assert len(long_msg['8']['text']) < 120, 'must be a snippet'

    # --- only the first line, so a rambling notice stays one line ---
    multi = summarise([{'author_id': '9', 'timestamp': now,
                        'content': 'Away next week\nsecond line\nthird'}],
                      now=now)
    assert multi['9']['text'] == 'Away next week', multi

    # --- nothing at all, rather than an exception ---
    assert summarise([], now=now) == {}
    assert summarise(None, now=now) == {}


def test_timezone_board_awake_means_their_clock_not_their_last_click():
    """
    The board's whole purpose is "call chains when people are awake", so the
    awake count has to come from the clock where they are.

    It used to mean "acted on Torn in the last 15 minutes", which read
    "0 of 5 awake" live while three of those five were mid-afternoon. With a
    small faction and a 15-minute window that number is almost always zero,
    so the headline looked broken and told nobody anything.

    Active-on-Torn is still worth showing - it is just a different question,
    and these two must not be allowed to collapse back into one number.
    """
    from features.timezones import (group_by_region, build_embed, is_waking,
                                    WAKING_START_HOUR, WAKING_END_HOUR)
    from datetime import datetime, timezone

    # 04:00 UTC. Auckland +13 = 17:00 (awake), Los Angeles -7 = 21:00 (awake),
    # Manila +8 = 12:00 (awake), London +1 = 05:00 (asleep).
    now = datetime(2026, 10, 24, 4, 0, tzinfo=timezone.utc)
    store = {
        '1': {'zone': 'Pacific/Auckland', 'name': 'Ikaros22'},
        '2': {'zone': 'America/Los_Angeles', 'name': 'EyeUhhn'},
        '3': {'zone': 'Asia/Manila', 'name': 'dakkkz'},
        '4': {'zone': 'Europe/London', 'name': 'NightOwl'},
    }

    # Nobody has touched Torn recently - the exact case that read 0 of 5.
    groups = group_by_region(store, active_ids=(), now=now)
    awake = sum(g['awake'] for g in groups)
    assert awake == 3, (
        f'3 of these 4 are at a civil hour, got {awake}. An empty '
        f'active list must not drag the awake count to zero.')

    by_label = {g['label']: g for g in groups}
    london = [g for g in groups if g['zone'] == 'Europe/London'][0]
    assert london['awake'] == 0, '05:00 is not a waking hour'

    # Active is counted separately and does NOT change awake.
    groups2 = group_by_region(store, active_ids=['4'], now=now)
    assert sum(g['awake'] for g in groups2) == 3, (
        'someone being on Torn at 5am does not make the other zones awake')
    london2 = [g for g in groups2 if g['zone'] == 'Europe/London'][0]
    assert london2['active'] == 1 and london2['awake'] == 0, (
        'awake and active are different questions and must stay separate')

    # The headline must not say "awake" about the Torn-activity number.
    embed = build_embed(groups2, active_names=['NightOwl'])
    text = embed.description or ''
    assert '3' in text.split('of')[0], f'awake total missing from: {text!r}'
    assert 'waking hours' in text, (
        f'the headline must say what it measures: {text!r}')

    # Boundaries, so a tweak to the window cannot silently invert them.
    class _L:
        def __init__(self, h): self.hour = h
    assert is_waking(_L(WAKING_START_HOUR)) is True
    assert is_waking(_L(WAKING_START_HOUR - 1)) is False
    assert is_waking(_L(WAKING_END_HOUR - 1)) is True
    assert is_waking(_L(WAKING_END_HOUR)) is False


def test_chain_event_never_calls_an_unreadable_log_a_no_show():
    """
    This feature decides whether somebody turned up to a chain, and the
    answer becomes a strike. So the failure that matters is not miscounting -
    it is a partial or failed read of the attacks log looking like "they made
    zero hits", which is indistinguishable from not turning up at all.

    Same None-vs-empty contract as the rest of the repo, in the place where
    getting it wrong punishes somebody who did nothing wrong.
    """
    from features.chain_events import (chain_hits_by_member, shortfall,
                                       merge_totals, met_minimum, day_of,
                                       clamp_window, MAX_EVENT_DAYS)

    OURS, THEIRS = '45635', '99999'

    # chain > 0 means the hit advanced a chain (verified in wars.py over
    # 2,827 real attacks). chain 0 is a loss, an escape, or a hit outside any
    # chain - none of which count toward a chain minimum.
    attacks = {
        '1': {'attacker_id': '100', 'attacker_faction': OURS, 'chain': 4},
        '2': {'attacker_id': '100', 'attacker_faction': OURS, 'chain': 5},
        '3': {'attacker_id': '200', 'attacker_faction': OURS, 'chain': 6},
        '4': {'attacker_id': '200', 'attacker_faction': OURS, 'chain': 0},
        '5': {'attacker_id': '300', 'attacker_faction': THEIRS, 'chain': 9},
        '6': 'not a dict at all',
    }
    out = chain_hits_by_member(attacks, OURS)
    assert out == {'100': 2, '200': 1}, out

    # --- THE guard: unreadable log is None, never {} ---
    assert chain_hits_by_member(None, OURS) is None, (
        'an unreadable attacks log must be None, not an empty tally - {} '
        'would put the whole faction on the shortfall list')
    assert chain_hits_by_member({}, OURS) == {}, (
        'a genuinely empty log is a real answer and stays empty')

    # A failed top-up must not advance anything or wipe the running totals.
    base = {'100': 7}
    merged, ok = merge_totals(base, None)
    assert merged == base and ok is False, (merged, ok)
    merged, ok = merge_totals(base, {'100': 3, '200': 1})
    assert merged == {'100': 10, '200': 1} and ok is True, merged

    # --- shortfall ---
    roster = {'100': 'Ikaros22', '200': 'dakkkz', '400': 'NeverShowed'}
    short = shortfall({'100': 12, '200': 3}, roster, 10)
    names = [m['name'] for m in short]
    assert names == ['NeverShowed', 'dakkkz'], (
        f'worst first, and somebody absent from the log is 0 not missing: {names}')
    assert short[0]['hits'] == 0
    assert met_minimum({'100': 12, '200': 3}, roster, 10) == 1

    assert shortfall(None, roster, 10) is None, (
        'unreadable totals must not produce a shortfall list at all')
    assert met_minimum(None, roster, 10) is None

    # --- day counting, 1-indexed and clamped ---
    ev = {'start': 1_000_000, 'end': 1_000_000 + 5 * 86400}
    assert day_of(ev, now=1_000_000 + 10) == (1, 5), 'first hour is day 1'
    assert day_of(ev, now=1_000_000 + 2 * 86400 + 60) == (3, 5)
    assert day_of(ev, now=1_000_000 + 99 * 86400) == (5, 5), 'clamped to the end'

    # --- a duration typo must not create a two-month strike window ---
    start, end, notes = clamp_window(5_000_000, 24 * 400, now=5_000_000)
    assert (end - start) == MAX_EVENT_DAYS * 86400, (end - start)
    assert notes, 'a capped duration must be reported, not applied silently'

    # Backdating a few hours is normal; a week back is a typo.
    now = 5_000_000
    start, end, notes = clamp_window(now - 3 * 3600, 24, now=now)
    assert start == now - 3 * 3600 and not notes, 'a 3h backdate is fine'
    start, end, notes = clamp_window(now - 400 * 3600, 24, now=now)
    assert start > now - 400 * 3600 and notes, 'a 400h backdate must be pulled in'


if __name__ == '__main__':
    test_all_modules_import()
    test_helper_signatures()
    test_torn_api_helpers()
    test_stakeout_algorithm()
    test_flytrack_logic()
    test_flytrack_uses_measured_speed_not_rounded_bucket()
    test_flytrack_eta_window_biases_early()
    test_flytrack_estimate_leg()
    test_flytrack_group_formatting()
    test_flytrack_status_reuses_persisted_eta()
    test_flytrack_fast_turnaround_detected()
    test_register_rejects_torn_discord_mismatch()
    test_register_allows_unlinked_torn_id()
    test_flytrack_autostop_trusts_war_tracker_not_raw_none()
    test_chain_milestone_picks_highest_on_jump()
    test_war_report_v2_shape_normalised()
    test_war_report_rejects_another_factions_war()
    test_xan_counts_friendly_hosps()
    test_incoming_split_does_not_dump_war_hits_into_outside()
    test_saved_war_history_records_real_hits_taken()
    test_fetch_faction_attacks_reports_outage_as_none()
    test_finished_war_stats_stop_at_the_war_end()
    test_attacks_shape_change_is_not_read_as_no_attacks()
    test_attack_log_outage_is_not_a_quiet_war()
    test_attack_log_paging_does_not_skip_the_boundary_second()
    test_war_end_survives_an_unreadable_attack_log()
    test_war_poll_posts_once_two_days_out()
    test_war_poll_window_is_24h_even_for_a_midnight_war()
    test_fly_auto_starts_but_never_overrides_a_manual_toggle_off()
    test_fly_auto_start_retries_when_enable_fails()
    test_poll_roster_names_who_picked_each_block()
    test_no_open_slots_is_reported_but_is_not_an_excuse()
    test_turtle_alert_fires_only_for_exposed_members()
    test_only_recruiting_oc_slots_count_as_joinable()
    test_war_report_separates_opponent_mugs_from_outside_mugs()
    test_chain_break_warning_gates_on_size_and_cooldown()
    test_strike_candidates_feed_the_ledger_with_the_right_types()
    test_bonus_hit_on_a_non_enemy_is_flagged_but_only_when_it_really_was_one()
    test_strike_candidates_still_post_without_a_ledger()
    test_management_ping_lands_in_the_channel_that_got_the_alert()
    test_expired_crime_does_not_count_as_participating()
    test_member_with_no_oc_history_is_clocked_from_their_join_date()
    test_trial_check_fires_once_per_member_and_resets_on_rejoin()
    test_strike_ledger_remembers_dismissals_and_expires_strikes()
    test_strike_notify_choices_are_exclusive_and_tracked()
    test_war_insight_counts_categories_and_never_invents_hits_taken()
    test_bleeding_warns_before_it_strikes_and_resets_on_a_clean_war()
    test_parse_when_accepts_what_people_actually_type()
    test_cleanup_only_ever_deletes_the_bots_own_noise()
    test_cleanup_sweeps_once_a_week_and_reports_what_it_took()
    test_discord_roster_classifies_every_way_the_two_lists_drift()
    test_role_sync_only_touches_roles_it_manages()
    test_the_bot_never_ticks_its_own_alerts()
    test_strikes_never_announce_that_there_are_none()
    test_revive_scan_never_turns_a_failed_lookup_into_an_accusation()
    test_revive_check_posts_once_per_checkpoint_and_retries_on_failure()
    test_war_report_counts_hits_taken_from_attack_log()
    test_activity_profile_chunk_detection()
    test_activity_profile_manager_queries()
    test_activity_profile_record_snapshot()
    test_activity_profile_decay()
    test_activity_profile_decay_backfills_legacy_baseline()
    test_activity_profile_pruning()
    test_inactivity_daily_check_retries_transient_api_failure()
    test_oc_check_skipped_when_crimes_unavailable()
    test_fetch_faction_crimes_distinguishes_outage_from_empty()
    test_stale_v1_crimes_fallback_rejected()
    test_retry_preserves_already_good_payload()
    test_left_faction_access_revoked_after_grace_period()
    test_follow_warning_pings_only_the_member_in_that_country()
    test_follow_warning_cooldown_suppresses_repeats()
    test_follow_warning_tells_unregistered_members_how_to_fix_it()
    test_follow_warning_splits_stronger_from_weaker()
    test_follow_warning_not_triggered_by_weak_enemies_alone()
    test_follow_warning_silent_when_own_roster_unavailable()
    test_fly_status_shows_destination_hotspots()
    test_strike_dm_states_the_published_ladder()
    test_manual_poll_posts_where_the_chaser_looks_for_it()
    test_member_command_board_lists_only_member_actions()
    test_member_command_board_renders_without_unset_channels()
    test_prune_refuses_to_act_on_a_roster_it_could_not_read()
    test_config_check_tells_the_difference_between_unset_and_deleted()
    test_rolesync_never_hands_out_administrator_by_torn_position()
    test_lobby_nag_waits_a_day_paces_itself_and_gives_up()
    test_welcome_board_stays_pinned_and_avoids_dead_channel_links()
    test_stakeout_suggestions_rank_by_danger_to_us_not_in_the_abstract()
    test_xan_report_marks_who_actually_qualifies()
    test_poll_closes_at_the_deadline_the_rules_actually_state()
    test_weekly_activity_check_runs_on_its_day_and_only_once()
    test_management_is_never_pinged_into_a_channel_with_nothing_in_it()
    test_roster_check_does_not_print_a_mention_it_knows_is_dead()
    test_timezone_board_survives_daylight_saving()
    test_timezone_picker_fits_discord_and_the_clocks_match()
    test_notices_never_turn_an_unreadable_channel_into_an_accusation()
    test_timezone_board_awake_means_their_clock_not_their_last_click()
    test_chain_event_never_calls_an_unreadable_log_a_no_show()
    print('All smoke tests passed.')
