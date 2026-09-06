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
    'utils.timestamps',
    'utils.torn_api',
    'features.commands',
    'features.alliance',
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

    # Default assumption is airstrip; ETA = start + std * 0.70
    eta = mgr._eta_ts('999', 'Mexico', 1_000_000)
    assert eta == 1_000_000 + int(STANDARD_FLIGHT_MINUTES['Mexico'] * 0.70 * 60)

    # Learned tier overrides the default
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
    from features.flytrack import FlyTracker, PHASE_OUT, PHASE_RETURNING, STANDARD_FLIGHT_MINUTES

    mgr = FlyTracker(bot=None)
    now_ts = 1_000_000

    resolved = mgr._estimate_leg('1', PHASE_OUT, 'Mexico', now_ts)
    assert resolved['phase'] == PHASE_OUT
    assert resolved['dest'] == 'Mexico'
    assert resolved['eta'] == int(now_ts + STANDARD_FLIGHT_MINUTES['Mexico'] * 0.70 * 60)
    assert 'estimate' in resolved['note']

    # Returning with no destination in the text must inherit the last-known
    # abroad location from prev, not silently drop to "unknown"
    resolved = mgr._estimate_leg('2', PHASE_RETURNING, None, now_ts, prev={'dest': 'Switzerland'})
    assert resolved['phase'] == PHASE_RETURNING
    assert resolved['dest'] == 'Switzerland'
    assert resolved['eta'] == int(now_ts + STANDARD_FLIGHT_MINUTES['Switzerland'] * 0.70 * 60)

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
    assert line == '**Ferothen** — China'


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


def test_alliance_announces_old_war_end_on_new_war():
    """
    Regression test: if an ally faction's war ends and a NEW war starts
    before the tracker's next check (no detected gap), the OLD war's end
    (win/loss) must still get announced - not silently overwritten by
    active_wars[faction_id] getting replaced with the new war's data.
    """
    import asyncio
    from features.alliance import AllianceManager

    mgr = AllianceManager(bot=None)
    mgr.active_wars = {'100': {'war_id': 'old_war', 'enemy_name': 'OldEnemy', 'enemy_id': '1',
                               'start_time': 0, 'factions': []}}
    mgr.announced_wars = set()

    posted = []

    class FakeChannel:
        async def send(self, content=None, embed=None):
            posted.append(embed.title if embed else content)

    new_war_item = {'start': 100, 'end': 0, 'factions': [{'id': '100'}, {'id': '2', 'name': 'NewEnemy'}]}

    asyncio.run(mgr._process_war_v2(FakeChannel(), '100', 'AllyName', 'new_war', new_war_item))

    assert len(posted) == 2, 'must post both the old war ending and the new war starting'
    assert 'Ended' in posted[0]
    assert mgr.active_wars['100']['war_id'] == 'new_war'


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
    assert disabled4 == ['A different war is now active — re-toggle Fly tracking for it']


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


if __name__ == '__main__':
    test_all_modules_import()
    test_helper_signatures()
    test_torn_api_helpers()
    test_stakeout_algorithm()
    test_flytrack_logic()
    test_flytrack_estimate_leg()
    test_flytrack_group_formatting()
    test_flytrack_status_reuses_persisted_eta()
    test_flytrack_fast_turnaround_detected()
    test_register_rejects_torn_discord_mismatch()
    test_register_allows_unlinked_torn_id()
    test_flytrack_autostop_trusts_war_tracker_not_raw_none()
    test_chain_milestone_picks_highest_on_jump()
    test_alliance_announces_old_war_end_on_new_war()
    test_activity_profile_chunk_detection()
    test_activity_profile_manager_queries()
    test_activity_profile_record_snapshot()
    test_activity_profile_decay()
    test_activity_profile_decay_backfills_legacy_baseline()
    test_activity_profile_pruning()
    test_inactivity_daily_check_retries_transient_api_failure()
    print('All smoke tests passed.')
