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


if __name__ == '__main__':
    test_all_modules_import()
    test_helper_signatures()
    test_torn_api_helpers()
    test_stakeout_algorithm()
    print('All smoke tests passed.')
