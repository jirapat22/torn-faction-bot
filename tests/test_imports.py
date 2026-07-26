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


if __name__ == '__main__':
    test_all_modules_import()
    test_helper_signatures()
    test_torn_api_helpers()
    test_stakeout_algorithm()
    test_flytrack_logic()
    test_flytrack_estimate_leg()
    test_flytrack_group_formatting()
    print('All smoke tests passed.')
