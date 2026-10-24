"""
Activity Profile Module
Tracks when faction members are typically online, built from data already
fetched hourly for role sync (last_action / status) - no extra API calls.

Not a live-tracking system: it accumulates an hour-of-day probability curve
per member over many days ("what fraction of hourly snapshots at 18:00 UTC
found this person active?"), then reports typical active windows for one
member, or faction-wide best/quiet windows for scheduling.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
from utils.helpers import load_json, save_json

import logging

logger = logging.getLogger(__name__)

DATA_FILE = 'data/activity_profile.json'

ACTIVE_STATUSES = {'Online', 'Idle'}
# Snapshots are taken roughly hourly - treat "last action within ~65 minutes"
# as still active-ish, to cover drift in when the hourly tick actually fires.
RECENT_ACTION_WINDOW_SECONDS = 65 * 60

MIN_SAMPLES_FOR_PROFILE = 48  # ~2 days of hourly snapshots before showing anything
DAILY_HISTORY_DAYS = 14       # rolling window of "was active this day" dates kept
CHUNK_THRESHOLD = 0.30        # hour counts as "typically active" at >=30% of samples
MAX_CHUNKS = 5                # cap on distinct active windows shown per member
COVERAGE_WINDOW_HOURS = 3     # width of the sliding window for best/quiet faction-wide

# Applied once per elapsed calendar day per member, so recent weeks outweigh
# months-old samples instead of every sample carrying equal weight forever
# (~34-day half-life at 0.98/day).
DECAY_FACTOR = 0.98
# Drop a member's profile once they've been absent from the faction fetch for
# this many days straight - avoids stale ex-members quietly skewing Coverage
# and cluttering the roster view forever. A grace period (not "first miss")
# so one API hiccup doesn't wipe real history.
PRUNE_AFTER_DAYS = 7


def _hour_probabilities(profile: dict) -> Optional[dict]:
    """{hour:int -> probability:float} for one profile dict, or None if empty."""
    hours = profile.get('hours') if profile else None
    if not hours:
        return None
    probs = {}
    for h in range(24):
        active, total = hours.get(str(h), [0, 0])
        probs[h] = (active / total) if total > 0 else 0.0
    return probs


def _total_samples(profile: dict) -> int:
    if not profile:
        return 0
    return sum(total for _active, total in profile.get('hours', {}).values())


def find_active_chunks(probs: dict, threshold: float = CHUNK_THRESHOLD,
                        max_chunks: int = MAX_CHUNKS) -> list:
    """
    Walk a 24-hour probability curve and return contiguous runs of hours
    where probability >= threshold, as a list of (start_hour, end_hour, avg_prob)
    tuples. Handles wraparound across midnight. If more than max_chunks are
    found, keeps the strongest (highest avg_prob) ones, re-sorted by start hour.
    Returns [] if no hour clears the threshold, or a single (0, 24, avg) chunk
    if every hour does.
    """
    active_hours = [probs.get(h, 0.0) >= threshold for h in range(24)]

    if not any(active_hours):
        return []
    if all(active_hours):
        return [(0, 24, sum(probs.values()) / 24)]

    # Rotate so we start right at a False->True boundary, so runs never get
    # split by the array's arbitrary 0-index cut point.
    start_idx = next(i for i in range(24) if active_hours[i] and not active_hours[i - 1])
    rotated = active_hours[start_idx:] + active_hours[:start_idx]

    chunks = []
    i = 0
    while i < 24:
        if rotated[i]:
            j = i
            while j < 24 and rotated[j]:
                j += 1
            hours_in_chunk = [(start_idx + k) % 24 for k in range(i, j)]
            avg_prob = sum(probs[h] for h in hours_in_chunk) / len(hours_in_chunk)
            real_start = (start_idx + i) % 24
            real_end = (start_idx + j) % 24
            chunks.append((real_start, real_end, avg_prob))
            i = j
        else:
            i += 1

    if len(chunks) > max_chunks:
        chunks = sorted(chunks, key=lambda c: c[2], reverse=True)[:max_chunks]
    chunks.sort(key=lambda c: c[0])
    return chunks


def best_and_quiet_windows(probs: dict, window_hours: int = COVERAGE_WINDOW_HOURS):
    """
    Slide a window_hours-wide window around the 24h circle and return
    ((best_start, best_end, best_avg), (quiet_start, quiet_end, quiet_avg))
    - the highest- and lowest-average-probability windows.
    """
    best = None
    quiet = None
    for start in range(24):
        hours = [(start + i) % 24 for i in range(window_hours)]
        avg = sum(probs.get(h, 0.0) for h in hours) / window_hours
        end = (start + window_hours) % 24
        if best is None or avg > best[2]:
            best = (start, end, avg)
        if quiet is None or avg < quiet[2]:
            quiet = (start, end, avg)
    return best, quiet


def format_hour(h: int) -> str:
    return f'{h % 24:02d}:00'


def format_window(start: int, end: int) -> str:
    return f'{format_hour(start)}–{format_hour(end)}'


class ActivityProfileManager:
    """Accumulates and queries per-member activity profiles."""

    def __init__(self, bot):
        self.bot = bot
        self.profiles = {}  # torn_id -> {'name', 'first_seen', 'hours', 'days', 'last_updated'}

    async def load_data(self):
        self.profiles = load_json(DATA_FILE, default={})

    async def save_data(self):
        save_json(DATA_FILE, self.profiles)

    async def record_snapshot(self, faction_members: dict, now: datetime = None):
        """
        Called once per hour with an already-fetched faction/basic members
        payload (torn_id -> member data including name/status/last_action).

        Args:
            now: Override for the current UTC time (tests only) - defaults
                 to the real current time.
        """
        if not faction_members:
            return

        now = now or datetime.now(timezone.utc)
        hour_key = str(now.hour)
        date_key = now.strftime('%Y-%m-%d')
        now_ts = now.timestamp()

        for torn_id, data in faction_members.items():
            name = data.get('name', torn_id)
            last_action = data.get('last_action') or {}
            status_desc = last_action.get('status', 'Offline')
            last_action_ts = last_action.get('timestamp', 0)
            recently_active = bool(last_action_ts) and (now_ts - last_action_ts) <= RECENT_ACTION_WINDOW_SECONDS
            is_active = status_desc in ACTIVE_STATUSES or recently_active

            profile = self.profiles.setdefault(torn_id, {
                'name': name, 'first_seen': date_key, 'hours': {}, 'days': [],
                'last_updated': 0, 'last_decay_date': date_key, 'last_seen_in_faction': date_key,
            })
            profile['name'] = name
            # Every torn_id here is in the current faction fetch, so it's seen today.
            profile['last_seen_in_faction'] = date_key

            self._apply_decay(profile, now.date(), date_key)

            hours = profile['hours']
            active_count, total_count = hours.get(hour_key, [0, 0])
            total_count += 1
            if is_active:
                active_count += 1
            hours[hour_key] = [active_count, total_count]

            if is_active:
                days = profile['days']
                if date_key not in days:
                    days.append(date_key)
                    days.sort()
                    if len(days) > DAILY_HISTORY_DAYS:
                        profile['days'] = days[-DAILY_HISTORY_DAYS:]

            profile['last_updated'] = now_ts

        self._prune_stale_profiles(now.date())

        await self.save_data()
        logger.info(f'Activity profile: recorded snapshot for {len(faction_members)} members')

    @staticmethod
    def _apply_decay(profile: dict, today: 'datetime.date', date_key: str):
        """
        Shrink a profile's hour counters once per elapsed calendar day (compounding
        across any gap), so old samples gradually matter less than recent ones.
        No-op if already decayed today.
        """
        last_decay_str = profile.get('last_decay_date')
        if last_decay_str is None:
            # Legacy profile with no baseline (predates this method). Stamp one
            # now rather than leaving it unset - defaulting the read to "today"
            # on every call would make days_elapsed permanently 0, exempting
            # this profile from decay forever.
            profile['last_decay_date'] = date_key
            return

        last_decay_date = datetime.strptime(last_decay_str, '%Y-%m-%d').date()
        days_elapsed = (today - last_decay_date).days
        if days_elapsed < 1:
            return

        factor = DECAY_FACTOR ** days_elapsed
        hours = profile['hours']
        for hk, (active, total) in list(hours.items()):
            new_total = round(total * factor)
            new_active = min(round(active * factor), new_total)
            hours[hk] = [new_active, new_total]
        profile['last_decay_date'] = date_key

    def _prune_stale_profiles(self, today: 'datetime.date'):
        """Drop profiles for members absent from the faction fetch for PRUNE_AFTER_DAYS+."""
        stale_ids = []
        for torn_id, profile in self.profiles.items():
            last_seen_str = profile.get('last_seen_in_faction')
            if not last_seen_str:
                continue  # no staleness info yet (legacy entry) - leave it alone
            last_seen_date = datetime.strptime(last_seen_str, '%Y-%m-%d').date()
            if (today - last_seen_date).days > PRUNE_AFTER_DAYS:
                stale_ids.append(torn_id)

        for torn_id in stale_ids:
            name = self.profiles[torn_id].get('name', torn_id)
            logger.info(f'Activity profile: pruning {name} ({torn_id}) - absent from faction {PRUNE_AFTER_DAYS}+ days')
            del self.profiles[torn_id]

    def has_enough_data(self, torn_id: str) -> bool:
        return _total_samples(self.profiles.get(torn_id)) >= MIN_SAMPLES_FOR_PROFILE

    def get_active_windows(self, torn_id: str):
        """List of (start_hour, end_hour, avg_prob) for this member, or None if no data yet."""
        profile = self.profiles.get(torn_id)
        probs = _hour_probabilities(profile)
        if probs is None:
            return None
        return find_active_chunks(probs)

    def get_consistency(self, torn_id: str):
        """(days_active, days_tracked) over the last 7 calendar days."""
        profile = self.profiles.get(torn_id)
        if not profile or not profile.get('first_seen'):
            return None
        first_seen_date = datetime.strptime(profile['first_seen'], '%Y-%m-%d').date()
        today = datetime.now(timezone.utc).date()
        days_set = set(profile.get('days', []))

        tracked = 0
        active = 0
        for delta in range(7):
            d = today - timedelta(days=delta)
            if d < first_seen_date:
                continue
            tracked += 1
            if d.strftime('%Y-%m-%d') in days_set:
                active += 1
        return (active, tracked)

    def get_roster_summary(self):
        """
        List of (torn_id, name, primary_window) for every member with enough
        data, sorted by the start hour of their strongest active window, plus
        a separate list of names still warming up.
        """
        ready = []
        warming_up = []
        for torn_id, profile in self.profiles.items():
            name = profile.get('name', torn_id)
            if not self.has_enough_data(torn_id):
                warming_up.append(name)
                continue
            chunks = self.get_active_windows(torn_id)
            if not chunks:
                warming_up.append(name)
                continue
            primary = max(chunks, key=lambda c: c[2])
            ready.append((torn_id, name, primary))

        ready.sort(key=lambda r: r[2][0])
        return ready, warming_up

    def hourly_curve(self):
        """
        {hour: 0..1} averaged over every member with enough data, or None.

        Pulled out of get_coverage so the timezone board can draw the shape
        of the day without re-implementing the aggregation and drifting from
        the windows get_coverage reports.
        """
        ready = []
        for torn_id in self.profiles:
            if not self.has_enough_data(torn_id):
                continue
            probs = _hour_probabilities(self.profiles[torn_id])
            if probs is not None:
                ready.append(probs)
        if not ready:
            return None
        return {h: sum(p[h] for p in ready) / len(ready) for h in range(24)}

    def get_coverage(self):
        """
        Faction-wide (best_window, quiet_window, member_count) using every
        member with enough data, or None if nobody has enough data yet.
        best/quiet windows are (start_hour, end_hour, avg_prob) tuples.
        """
        ready_probs = []
        for torn_id in self.profiles:
            if not self.has_enough_data(torn_id):
                continue
            probs = _hour_probabilities(self.profiles[torn_id])
            if probs is not None:
                ready_probs.append(probs)

        if not ready_probs:
            return None

        agg = {h: sum(p[h] for p in ready_probs) / len(ready_probs)
               for h in range(24)}
        best, quiet = best_and_quiet_windows(agg)
        return best, quiet, len(ready_probs)
