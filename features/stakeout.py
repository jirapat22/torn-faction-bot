"""
Stakeout Module
Track enemy player behavior over 24-48 hours to identify sleep patterns and optimal attack windows.

Features:
- Log online/offline/hospital/traveling status every 15 minutes
- Detect sleep patterns (consistent offline periods)
- Generate activity heatmap by hour
- Recommend best attack windows
- Estimate timezone based on sleep patterns
- Track multiple targets simultaneously

Commands (nested under /enemy):
- /enemy stakeout add <torn_id> [hours] - Start watching (default 24h, max 48h)
- /enemy stakeout list - Show active stakeouts
- /enemy stakeout report <torn_id> - Show behavior analysis
- /enemy stakeout stop <torn_id> - Stop watching
"""

import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from collections import defaultdict

from utils.helpers import load_json, save_json, create_embed, is_management, next_occurrence_of_hour
from utils.torn_api import fetch_player_data
from config import (
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL
)


import logging

logger = logging.getLogger(__name__)
# Status codes for logging
STATUS_ONLINE = 'online'
STATUS_OFFLINE = 'offline'
STATUS_IDLE = 'idle'
STATUS_HOSPITAL = 'hospital'
STATUS_TRAVELING = 'traveling'
STATUS_JAIL = 'jail'
STATUS_UNKNOWN = 'unknown'

# Max history records per target (48h at 15min intervals = 192, keep some buffer)
MAX_HISTORY_RECORDS = 250

# Timezone offsets for estimation (UTC offset in hours)
TIMEZONE_NAMES = {
    -12: 'UTC-12 (Baker Island)',
    -11: 'UTC-11 (Samoa)',
    -10: 'UTC-10 (Hawaii)',
    -9: 'UTC-9 (Alaska)',
    -8: 'UTC-8 (Pacific)',
    -7: 'UTC-7 (Mountain)',
    -6: 'UTC-6 (Central)',
    -5: 'UTC-5 (Eastern)',
    -4: 'UTC-4 (Atlantic)',
    -3: 'UTC-3 (Brazil)',
    -2: 'UTC-2',
    -1: 'UTC-1 (Azores)',
    0: 'UTC+0 (UK/Portugal)',
    1: 'UTC+1 (Central Europe)',
    2: 'UTC+2 (Eastern Europe)',
    3: 'UTC+3 (Moscow)',
    4: 'UTC+4 (Dubai)',
    5: 'UTC+5 (Pakistan)',
    5.5: 'UTC+5:30 (India)',
    6: 'UTC+6 (Bangladesh)',
    7: 'UTC+7 (Thailand)',
    8: 'UTC+8 (China/Singapore)',
    9: 'UTC+9 (Japan/Korea)',
    9.5: 'UTC+9:30 (Australia Central)',
    10: 'UTC+10 (Australia East)',
    11: 'UTC+11 (Solomon Islands)',
    12: 'UTC+12 (New Zealand)',
}


class StakeoutManager:
    """Manages stakeout tracking for enemy players"""

    def __init__(self, bot):
        self.bot = bot
        self.data_file = 'data/stakeouts.json'
        self.stakeouts = {}  # {torn_id: stakeout_config}
        self.history = {}    # {torn_id: [status_records]}

    async def load_data(self):
        """Load stakeout data from JSON"""
        data = load_json(self.data_file, default={'stakeouts': {}, 'history': {}})
        self.stakeouts = data.get('stakeouts', {})
        self.history = data.get('history', {})

        # Clean up expired stakeouts
        await self._cleanup_expired()

        logger.info(f'Loaded {len(self.stakeouts)} active stakeouts')

    async def save_data(self):
        """Save stakeout data to JSON"""
        save_json(self.data_file, {
            'stakeouts': self.stakeouts,
            'history': self.history
        })

    async def _cleanup_expired(self):
        """Remove stakeouts that have exceeded their duration
        and purge history for targets expired more than 7 days ago."""
        now = datetime.now(timezone.utc).timestamp()
        expired = []

        for torn_id, config in self.stakeouts.items():
            end_time = config.get('end_time', 0)
            if now >= end_time:
                expired.append(torn_id)

        for torn_id in expired:
            del self.stakeouts[torn_id]
            # Keep history for reports even after stakeout ends
            logger.info(f'Stakeout expired for {torn_id}')

        # Purge history for targets no longer active and older than 7 days
        stale_history = []
        seven_days = 7 * 86400
        for torn_id, records in self.history.items():
            if torn_id in self.stakeouts:
                continue  # Still active
            if not records:
                stale_history.append(torn_id)
                continue
            last_ts = records[-1].get('timestamp', 0)
            if now - last_ts > seven_days:
                stale_history.append(torn_id)

        for torn_id in stale_history:
            del self.history[torn_id]
            logger.info(f'Stakeout history purged for {torn_id}')

        if expired or stale_history:
            await self.save_data()

    async def add_stakeout(self, torn_id: str, duration_hours: int = 24, added_by: int = None) -> tuple:
        """
        Add a new stakeout target

        Args:
            torn_id: Torn player ID to watch
            duration_hours: How long to watch (max 48)
            added_by: Discord user ID who added this

        Returns:
            (success: bool, message: str, player_name: str or None)
        """
        torn_id = str(torn_id).strip()

        # Validate torn_id is numeric
        if not torn_id.isdigit():
            return False, f'Invalid Torn ID: must be a number (got: {torn_id})', None

        # Check if already being watched
        if torn_id in self.stakeouts:
            return False, 'This player is already being watched.', None

        # Validate duration
        duration_hours = min(max(duration_hours, 1), 48)

        # Fetch player info to validate ID and get name
        player_data = await self._fetch_player_status(torn_id)
        if not player_data:
            return False, 'Could not find player. Check the Torn ID and ensure TORN_PERSONAL_API_KEY is set correctly.', None

        player_name = player_data.get('name', 'Unknown')

        # Create stakeout config
        now = datetime.now(timezone.utc)
        self.stakeouts[torn_id] = {
            'torn_id': torn_id,
            'name': player_name,
            'start_time': now.timestamp(),
            'end_time': (now + timedelta(hours=duration_hours)).timestamp(),
            'duration_hours': duration_hours,
            'added_by': added_by,
            'check_count': 0
        }

        # Initialize history
        self.history[torn_id] = []

        # Do first status check immediately
        await self._record_status(torn_id, player_data)

        await self.save_data()
        logger.info(f'Stakeout added: {torn_id} ({player_name})')

        return True, f'Now watching **{player_name}** [{torn_id}] for {duration_hours} hours.', player_name

    async def remove_stakeout(self, torn_id: str) -> tuple:
        """
        Remove a stakeout target

        Returns:
            (success: bool, message: str)
        """
        torn_id = str(torn_id).strip()

        if torn_id not in self.stakeouts:
            return False, 'This player is not being watched.'

        name = self.stakeouts[torn_id].get('name', torn_id)
        del self.stakeouts[torn_id]
        # Keep history for potential report viewing

        await self.save_data()

        return True, f'Stopped watching **{name}** [{torn_id}].'

    async def extend_stakeout(self, torn_id: str, extra_hours: int) -> tuple:
        """
        Extend an existing stakeout duration

        Args:
            torn_id: Torn player ID
            extra_hours: Additional hours to add (1-48)

        Returns:
            (success: bool, message: str, new_end_time: float or None)
        """
        torn_id = str(torn_id).strip()

        if torn_id not in self.stakeouts:
            return False, 'This player is not being watched.', None

        # Validate hours
        extra_hours = min(max(extra_hours, 1), 48)

        config = self.stakeouts[torn_id]
        name = config.get('name', torn_id)
        old_end = config['end_time']
        now = datetime.now(timezone.utc).timestamp()

        # Extend from current end time (or now if already expired)
        base_time = max(old_end, now)
        new_end = base_time + (extra_hours * 3600)

        config['end_time'] = new_end
        config['duration_hours'] = config.get('duration_hours', 0) + extra_hours

        await self.save_data()

        remaining = (new_end - now) / 3600
        return True, f'Extended stakeout for **{name}** by {extra_hours} hours.\nTotal remaining: {remaining:.1f} hours.', new_end

    async def get_quick_status(self, torn_id: str) -> Optional[Dict]:
        """
        Get current status of a stakeout target

        Returns dict with current status info or None if not found/watching
        """
        torn_id = str(torn_id).strip()

        config = self.stakeouts.get(torn_id)
        if not config:
            # Check if we have history (stopped stakeout)
            if torn_id in self.history and self.history[torn_id]:
                return {'status': 'stopped', 'history_available': True}
            return None

        # Fetch current status
        player_data = await self._fetch_player_status(torn_id)
        if not player_data:
            return {'status': 'error', 'message': 'Could not fetch player data'}

        status, extra = self._determine_status(player_data)

        now = datetime.now(timezone.utc).timestamp()
        remaining = (config['end_time'] - now) / 3600
        record_count = len(self.history.get(torn_id, []))

        # Get last action info
        last_action = player_data.get('last_action', {})
        last_action_ts = last_action.get('timestamp', 0)
        mins_since_action = int((now - last_action_ts) / 60) if last_action_ts else None

        return {
            'status': 'active',
            'name': config.get('name', torn_id),
            'torn_id': torn_id,
            'current_status': status,
            'extra_info': extra,
            'remaining_hours': remaining,
            'record_count': record_count,
            'mins_since_action': mins_since_action,
            'level': player_data.get('level'),
            'faction': player_data.get('faction', {}).get('faction_name', 'None')
        }

    async def _fetch_player_status(self, torn_id: str) -> Optional[Dict]:
        """Fetch player status from Torn API"""
        # Try with travel selection first (gives more data)
        data = await fetch_player_data(torn_id, selections='profile,travel')
        if not data:
            # Fallback to just profile if travel selection fails
            logger.info(f'Stakeout: Retrying {torn_id} without travel selection')
            data = await fetch_player_data(torn_id, selections='profile')
        if not data:
            logger.error(f'Stakeout: Failed to fetch player {torn_id} - check TORN_PERSONAL_API_KEY')
        return data

    def _determine_status(self, player_data: Dict) -> tuple:
        """
        Determine player status from API data

        Returns:
            (status_code, extra_info)
        """
        if not player_data:
            return STATUS_UNKNOWN, None

        # Check if traveling
        travel = player_data.get('travel', {})
        if travel.get('time_left', 0) > 0:
            destination = travel.get('destination', 'Unknown')
            return STATUS_TRAVELING, destination

        # Check status
        status = player_data.get('status', {})
        state = status.get('state', '').lower()

        if state == 'hospital':
            until = status.get('until', 0)
            return STATUS_HOSPITAL, until
        elif state == 'jail':
            until = status.get('until', 0)
            return STATUS_JAIL, until
        elif state == 'traveling':
            return STATUS_TRAVELING, None

        # Check last action for online/offline/idle
        last_action = player_data.get('last_action', {})
        last_action_ts = last_action.get('timestamp', 0)
        last_action_status = last_action.get('status', 'Offline')

        now = datetime.now(timezone.utc).timestamp()
        minutes_since_action = (now - last_action_ts) / 60

        if last_action_status == 'Online':
            return STATUS_ONLINE, None
        elif last_action_status == 'Idle':
            return STATUS_IDLE, int(minutes_since_action)
        else:
            return STATUS_OFFLINE, int(minutes_since_action)

    async def _record_status(self, torn_id: str, player_data: Dict = None):
        """Record a status check for a stakeout target"""
        if torn_id not in self.stakeouts:
            return

        if player_data is None:
            player_data = await self._fetch_player_status(torn_id)

        status, extra = self._determine_status(player_data)

        now = datetime.now(timezone.utc)
        record = {
            'timestamp': now.timestamp(),
            'hour': now.hour,
            'weekday': now.weekday(),  # 0=Monday, 6=Sunday
            'status': status,
            'extra': extra,
            'attackable': status in [STATUS_ONLINE, STATUS_OFFLINE, STATUS_IDLE]  # Not hospital/jail/traveling
        }

        if torn_id not in self.history:
            self.history[torn_id] = []

        self.history[torn_id].append(record)

        # Prune oldest records if history exceeds max size
        if len(self.history[torn_id]) > MAX_HISTORY_RECORDS:
            self.history[torn_id] = self.history[torn_id][-MAX_HISTORY_RECORDS:]

        self.stakeouts[torn_id]['check_count'] = len(self.history[torn_id])

    async def check_all_stakeouts(self):
        """Check status of all active stakeouts (called by background task)"""
        await self._cleanup_expired()

        if not self.stakeouts:
            return

        for torn_id in list(self.stakeouts.keys()):
            await self._record_status(torn_id)

        await self.save_data()
        logger.info(f'Stakeout check: recorded status for {len(self.stakeouts)} targets')

    def get_active_stakeouts(self) -> List[Dict]:
        """Get list of active stakeout configs"""
        now = datetime.now(timezone.utc).timestamp()
        active = []

        for torn_id, config in self.stakeouts.items():
            remaining = config['end_time'] - now
            if remaining > 0:
                active.append({
                    **config,
                    'remaining_hours': remaining / 3600,
                    'record_count': len(self.history.get(torn_id, []))
                })

        return active

    def get_stakeout_count(self) -> int:
        """Get number of active stakeouts"""
        return len(self.stakeouts)

    def generate_report(self, torn_id: str) -> Optional[Dict]:
        """
        Generate behavior analysis report for a target

        Returns analysis dict or None if no data
        """
        torn_id = str(torn_id).strip()

        history = self.history.get(torn_id, [])
        if not history:
            return None

        config = self.stakeouts.get(torn_id, {})
        name = config.get('name', torn_id)

        # Hourly counts — only online vs offline matter for coordination decisions
        hourly_online = defaultdict(int)
        hourly_offline = defaultdict(int)
        hourly_total = defaultdict(int)

        # Weekday vs weekend patterns (0-4 = weekday, 5-6 = weekend)
        weekday_online = defaultdict(int)
        weekday_total = defaultdict(int)
        weekend_online = defaultdict(int)
        weekend_total = defaultdict(int)

        daily_online = defaultdict(int)
        daily_total = defaultdict(int)

        status_counts = defaultdict(int)

        for record in history:
            hour = record['hour']
            status = record['status']
            weekday = record.get('weekday', 0)

            hourly_total[hour] += 1
            status_counts[status] += 1

            # "Watching us" = online or idle. Everything else (offline, hospital,
            # jail, traveling) means they can't see/react to our chain.
            is_online = status in (STATUS_ONLINE, STATUS_IDLE)
            if is_online:
                hourly_online[hour] += 1
            else:
                hourly_offline[hour] += 1

            if weekday < 5:
                weekday_total[hour] += 1
                if is_online:
                    weekday_online[hour] += 1
            else:
                weekend_total[hour] += 1
                if is_online:
                    weekend_online[hour] += 1

            daily_total[weekday] += 1
            if is_online:
                daily_online[weekday] += 1

        # Smoothed per-hour online/offline probability (Laplace +1/+2)
        hour_stats = self._compute_hour_stats(hourly_online, hourly_offline, hourly_total)

        # Heatmap driven by smoothed online probability
        heatmap = []
        for hour in range(24):
            total = hourly_total.get(hour, 0)
            if total == 0:
                heatmap.append('·')
                continue
            ratio = hour_stats[hour]['online_p']
            if ratio >= 0.70:
                heatmap.append('█')
            elif ratio >= 0.45:
                heatmap.append('▓')
            elif ratio >= 0.20:
                heatmap.append('▒')
            else:
                heatmap.append('░')

        # Sleep / offline window — primary: streak-based; fallback: hourly search
        sleep_window = self._find_sleep_window_streaks(history)
        if not sleep_window:
            sleep_window = self._find_sleep_window(hour_stats)

        # Peak-online window — the hours they're most reliably watching
        peak_online_window = self._find_peak_online_window(hour_stats)

        # Secondary: ranked hour lists (kept for backward-compat callers)
        safe_windows = self._find_safe_hours(hour_stats)
        danger_windows = self._find_danger_hours(hour_stats)

        # Grouped contiguous blocks for display
        safe_blocks = self._group_contiguous(
            [h for h, _ in safe_windows if hour_stats[h]['offline_p'] >= 0.60]
        )
        danger_blocks = self._group_contiguous(
            [h for h, _ in danger_windows if hour_stats[h]['online_p'] >= 0.55]
        )

        estimated_tz = self._estimate_timezone(sleep_window)

        # Calculate overall stats
        total_checks = len(history)
        online_pct = (status_counts[STATUS_ONLINE] + status_counts[STATUS_IDLE]) / total_checks * 100 if total_checks > 0 else 0

        # Weekday vs weekend online rates
        weekday_checks = sum(weekday_total.values())
        weekend_checks = sum(weekend_total.values())
        weekday_online_pct = sum(weekday_online.values()) / weekday_checks * 100 if weekday_checks > 0 else 0
        weekend_online_pct = sum(weekend_online.values()) / weekend_checks * 100 if weekend_checks > 0 else 0

        # Most/least active days
        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        day_activity = []
        for day in range(7):
            total = daily_total.get(day, 0)
            if total >= 2:
                rate = daily_online.get(day, 0) / total * 100
                day_activity.append((day_names[day], rate, total))
        day_activity.sort(key=lambda x: x[1], reverse=True)

        # Coverage — how much of the 24h clock we've actually sampled
        hours_covered = sum(1 for h in range(24) if hourly_total.get(h, 0) > 0)

        # Time range
        first_check = datetime.fromtimestamp(history[0]['timestamp'], tz=timezone.utc)
        last_check = datetime.fromtimestamp(history[-1]['timestamp'], tz=timezone.utc)
        duration = last_check - first_check

        return {
            'torn_id': torn_id,
            'name': name,
            'total_checks': total_checks,
            'hours_covered': hours_covered,
            'duration_hours': duration.total_seconds() / 3600,
            'first_check': first_check,
            'last_check': last_check,
            'heatmap': heatmap,
            'sleep_window': sleep_window,
            'peak_online_window': peak_online_window,
            'safe_windows': safe_windows,
            'danger_windows': danger_windows,
            'safe_blocks': safe_blocks,
            'danger_blocks': danger_blocks,
            'estimated_tz': estimated_tz,
            'online_pct': online_pct,
            'weekday_online_pct': weekday_online_pct,
            'weekend_online_pct': weekend_online_pct,
            'day_activity': day_activity,
            'status_counts': dict(status_counts),
            'hourly_online': dict(hourly_online),
            'hourly_total': dict(hourly_total),
            'hour_stats': hour_stats,
        }

    @staticmethod
    def _compute_hour_stats(hourly_online: Dict, hourly_offline: Dict,
                            hourly_total: Dict) -> Dict[int, Dict]:
        """
        Per-hour smoothed probabilities using Laplace add-one smoothing.
        Returns {hour: {online_p, offline_p, samples}}.
        Unused hours get a neutral prior (0.5) so low-sample hours never rank as extreme.
        """
        stats = {}
        for hour in range(24):
            n = hourly_total.get(hour, 0)
            online = hourly_online.get(hour, 0)
            offline = hourly_offline.get(hour, 0)
            stats[hour] = {
                'online_p': (online + 1) / (n + 2),
                'offline_p': (offline + 1) / (n + 2),
                'samples': n,
            }
        return stats

    def _find_peak_online_window(self, hour_stats: Dict) -> Optional[tuple]:
        """
        Find the contiguous window of hours when the target is most reliably online
        (watching). Mirror of _find_sleep_window but for online_p.

        Returns (start_hour, end_hour, confidence) or None.
        """
        covered = sum(1 for h in range(24) if hour_stats[h]['samples'] > 0)
        if covered < 8:
            return None

        best = None  # (score, start, length, strength)
        for length in range(3, 9):  # 3–8h peak windows
            for start in range(24):
                window = [(start + k) % 24 for k in range(length)]
                outside = [h for h in range(24) if h not in window]
                inside_on = sum(hour_stats[h]['online_p'] for h in window) / length
                outside_on = sum(hour_stats[h]['online_p'] for h in outside) / max(len(outside), 1)
                inside_samples = sum(hour_stats[h]['samples'] for h in window)
                weight = min(1.0, inside_samples / (length * 5))
                score = (inside_on - outside_on) * weight
                if best is None or score > best[0]:
                    best = (score, start, length, inside_on - outside_on)

        if best is None or best[0] <= 0:
            return None
        _, start, length, strength = best
        end = (start + length) % 24
        confidence = max(0.0, min(1.0, strength))
        return (start, end, confidence)

    def _find_sleep_window_streaks(self, history: List[Dict]) -> Optional[tuple]:
        """
        Detect sleep window by clustering long offline streaks on the 24h circle.
        Returns (start_hour, end_hour, confidence) or None.

        Improvements over naive approach:
        - Streaks must be at least 3 hours (12 records at 15-min) to qualify as sleep.
        - Cluster by circular mean (not exact-hour bucketing), so 22:45 and 23:30 sleep
          starts aren't treated as unrelated.
        - Confidence reflects both the count of aligned streaks and how tight the
          cluster is (low circular variance = high confidence).
        """
        import math

        if len(history) < 12:
            return None

        # Collect offline streaks: each streak = (start_ts, start_hour_frac, duration_records)
        streaks = []
        curr_start = None
        curr_len = 0
        for rec in history:
            is_offline = rec['status'] == STATUS_OFFLINE
            if is_offline:
                if curr_start is None:
                    curr_start = rec
                curr_len += 1
            else:
                if curr_start is not None and curr_len >= 12:  # ≥3h
                    streaks.append(self._streak_record(curr_start, curr_len))
                curr_start = None
                curr_len = 0
        if curr_start is not None and curr_len >= 12:
            streaks.append(self._streak_record(curr_start, curr_len))

        if not streaks:
            return None

        # Circular mean of streak start hours (convert hours to angles)
        sin_sum = sum(math.sin(2 * math.pi * s['start_frac'] / 24) for s in streaks)
        cos_sum = sum(math.cos(2 * math.pi * s['start_frac'] / 24) for s in streaks)
        mean_angle = math.atan2(sin_sum, cos_sum)
        mean_start = (mean_angle * 24 / (2 * math.pi)) % 24

        # Circular variance R = sqrt(sin²+cos²)/n — 1.0 = perfectly aligned, 0 = random
        R = math.hypot(sin_sum, cos_sum) / len(streaks)

        # Keep streaks within ±2h of mean_start (circular distance)
        def circ_dist(a, b):
            d = abs(a - b) % 24
            return min(d, 24 - d)

        aligned = [s for s in streaks if circ_dist(s['start_frac'], mean_start) <= 2]
        if not aligned:
            return None

        avg_duration_h = sum(s['duration_mins'] for s in aligned) / len(aligned) / 60
        # Cap at 12h (longer than that is not a sleep window)
        avg_duration_h = min(avg_duration_h, 12)

        start_h = int(round(mean_start)) % 24
        end_h = int(round(mean_start + avg_duration_h)) % 24

        # Confidence: combines alignment (R) and sample count
        confidence = R * min(len(aligned) / 3, 1.0)
        return (start_h, end_h, confidence)

    @staticmethod
    def _streak_record(start_rec: Dict, records_count: int) -> Dict:
        """Turn a streak into {start_frac, duration_mins}.
        start_frac = hour + minute/60 for finer alignment than integer hours."""
        ts = start_rec['timestamp']
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        start_frac = dt.hour + dt.minute / 60
        return {
            'start_frac': start_frac,
            'duration_mins': records_count * 15,
        }

    def _find_sleep_window(self, hour_stats: Dict) -> Optional[tuple]:
        """
        Fallback sleep-window search using smoothed hourly probabilities.
        Sliding circular window of length 4-10h; score = mean(offline_p inside)
        − max(online_p outside). Tolerates single noisy hours.

        Returns (start_hour, end_hour, confidence) or None.
        """
        # Require at least half the hours to have any samples
        covered = sum(1 for h in range(24) if hour_stats[h]['samples'] > 0)
        if covered < 12:
            return None

        best = None  # (score, start, length, confidence)
        for length in range(4, 11):
            for start in range(24):
                window = [(start + k) % 24 for k in range(length)]
                outside = [h for h in range(24) if h not in window]
                inside_off = sum(hour_stats[h]['offline_p'] for h in window) / length
                outside_on = max((hour_stats[h]['online_p'] for h in outside), default=0.5)
                # Weight by sample count inside the window (logistic saturates at ~20 samples)
                inside_samples = sum(hour_stats[h]['samples'] for h in window)
                weight = min(1.0, inside_samples / (length * 5))
                score = (inside_off - outside_on) * weight
                if best is None or score > best[0]:
                    best = (score, start, length, inside_off - outside_on)

        if best is None or best[0] <= 0:
            return None

        _, start, length, confidence = best
        end = (start + length) % 24
        # Normalize confidence to [0,1]
        confidence = max(0.0, min(1.0, confidence))
        return (start, end, confidence)

    def _find_safe_hours(self, hour_stats: Dict) -> List[tuple]:
        """
        Rank hours by smoothed offline probability (target least likely to be watching).
        Returns list of (hour, offline_p) — top 6.
        """
        windows = [(h, hour_stats[h]['offline_p']) for h in range(24)
                   if hour_stats[h]['samples'] > 0]
        windows.sort(key=lambda x: x[1], reverse=True)
        return windows[:6]

    def _find_danger_hours(self, hour_stats: Dict) -> List[tuple]:
        """
        Rank hours by smoothed online probability (most active).
        Returns list of (hour, online_p) — top 6.
        """
        windows = [(h, hour_stats[h]['online_p']) for h in range(24)
                   if hour_stats[h]['samples'] > 0]
        windows.sort(key=lambda x: x[1], reverse=True)
        return windows[:6]

    @staticmethod
    def _group_contiguous(hours: List[int]) -> List[tuple]:
        """
        Group a list of hours into contiguous blocks on the 24h circle.
        Returns list of (start_hour, end_hour_exclusive) tuples, sorted by length desc.
        Handles midnight wrap: [22,23,0,1] -> [(22, 2)].
        """
        if not hours:
            return []
        present = sorted(set(h % 24 for h in hours))
        if len(present) == 24:
            return [(0, 0)]  # full day — represent as 0-0

        # Find gaps to determine where blocks start
        blocks = []
        start = present[0]
        prev = present[0]
        for h in present[1:]:
            if h == prev + 1:
                prev = h
                continue
            blocks.append((start, (prev + 1) % 24))
            start = h
            prev = h
        blocks.append((start, (prev + 1) % 24))

        # Merge wrap-around: if block ends at 24 (=0) and another starts at 0
        if len(blocks) >= 2 and blocks[0][0] == 0 and blocks[-1][1] == 0:
            first = blocks.pop(0)
            last = blocks.pop(-1)
            blocks.append((last[0], first[1]))

        def length(b):
            s, e = b
            return (e - s) % 24 or 24

        blocks.sort(key=length, reverse=True)
        return blocks

    def _estimate_timezone(self, sleep_window: Optional[tuple]) -> Optional[str]:
        """
        Estimate timezone from sleep window.

        Assumes mid-sleep = 03:30 local (empirically a better average than 04:00
        for gamers). Returns nearest half-hour offset label.
        """
        if not sleep_window:
            return None

        start_hour, end_hour, _ = sleep_window

        # Compute sleep length (handle wrap)
        if end_hour < start_hour:
            sleep_length = (24 - start_hour) + end_hour
        elif end_hour == start_hour:
            sleep_length = 8  # default when start==end
        else:
            sleep_length = end_hour - start_hour

        mid_sleep_utc = (start_hour + sleep_length / 2) % 24

        # Target local mid-sleep = 03:30
        offset = (3.5 - mid_sleep_utc)
        # Normalize to [-12, 12]
        while offset > 12:
            offset -= 24
        while offset < -12:
            offset += 24

        # Snap to half-hour grid
        offset = round(offset * 2) / 2
        # Prefer whole-hour label if available, else fall back
        label = TIMEZONE_NAMES.get(offset)
        if label:
            return label
        label = TIMEZONE_NAMES.get(int(offset))
        if label:
            return label
        sign = '+' if offset >= 0 else '-'
        mag = abs(offset)
        h = int(mag)
        m = int(round((mag - h) * 60))
        return f'UTC{sign}{h:02d}:{m:02d}'

    # Mapping from internal heatmap chars to Discord-native emoji blocks
    _EMOJI_MAP = {
        '█': '🟥',  # very active (≥70% online)
        '▓': '🟧',  # active     (≥45%)
        '▒': '🟨',  # sometimes  (≥20%)
        '░': '🟩',  # rarely     (<20%)
        '·': '⬛',  # no data
    }

    def format_heatmap_text(self, report: Dict) -> str:
        """
        Format the heatmap as Discord-native colour emoji.

        Returned as a markdown string (NOT a code block) so emoji render as glyphs.
        Hour labels are kept in a separate monospace line below.
        """
        heatmap = report['heatmap']
        emoji_row = ''.join(self._EMOJI_MAP.get(c, '⬛') for c in heatmap)

        # Hour scale: tick every 6 hours, in monospace so it lines up
        scale = '`00      06      12      18      23`'

        lines = [
            '**Activity Heatmap (TCT hours):**',
            emoji_row,
            scale,
            '🟥 Active ≥70% · 🟧 ≥45% · 🟨 ≥20% · 🟩 <20% · ⬛ No data',
        ]
        return '\n'.join(lines)


async def setup_stakeout_commands(bot, manager: StakeoutManager, enemy_group):
    """Register stakeout commands as an /enemy stakeout subgroup.

    Attaches to the passed enemy_group (from setup_enemy_commands); bot.py
    adds enemy_group to the tree once, after this runs.
    """

    stakeout_group = app_commands.Group(
        name='stakeout',
        description='Track enemy player behavior',
        parent=enemy_group
    )

    @stakeout_group.command(name='add', description='[Management] Start watching a player')
    @app_commands.describe(
        torn_id='Torn player ID to watch',
        hours='How long to watch (1-48 hours, default 24)'
    )
    async def stakeout_add(interaction: discord.Interaction, torn_id: str, hours: int = 24):
        """Add a player to stakeout"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Cap hours at valid range
        actual_hours = min(max(hours, 1), 48)

        success, message, name = await manager.add_stakeout(
            torn_id=torn_id,
            duration_hours=actual_hours,
            added_by=interaction.user.id
        )

        if success:
            embed = create_embed(
                title='Stakeout Started',
                description=message,
                color=EMBED_COLOR_SUCCESS
            )
            # Show duration with note if capped
            duration_note = f'{actual_hours} hours'
            if hours > 48:
                duration_note += ' (max 48h)'
            embed.add_field(
                name='Duration',
                value=duration_note,
                inline=True
            )
            embed.add_field(
                name='Checks',
                value='Every 15 minutes',
                inline=True
            )
            embed.add_field(
                name='Next Steps',
                value='Use `/enemy stakeout report` after a few hours to see patterns.',
                inline=False
            )
        else:
            embed = create_embed(
                title='Failed to Start Stakeout',
                description=message,
                color=EMBED_COLOR_WARNING
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @stakeout_group.command(name='stop', description='[Management] Stop watching a player')
    @app_commands.describe(torn_id='Torn player ID to stop watching')
    async def stakeout_stop(interaction: discord.Interaction, torn_id: str):
        """Stop a stakeout"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        success, message = await manager.remove_stakeout(torn_id)

        if success:
            embed = create_embed(
                title='Stakeout Stopped',
                description=message + '\n\nHistory preserved - you can still run `/enemy stakeout report` to see collected data.',
                color=EMBED_COLOR_SUCCESS
            )
        else:
            embed = create_embed(
                title='Not Found',
                description=message,
                color=EMBED_COLOR_WARNING
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @stakeout_group.command(name='extend', description='[Management] Extend a stakeout duration')
    @app_commands.describe(
        torn_id='Torn player ID to extend',
        hours='Additional hours to add (1-48)'
    )
    async def stakeout_extend(interaction: discord.Interaction, torn_id: str, hours: int = 24):
        """Extend a stakeout"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        success, message, new_end = await manager.extend_stakeout(torn_id, hours)

        if success:
            embed = create_embed(
                title='Stakeout Extended',
                description=message,
                color=EMBED_COLOR_SUCCESS
            )
            # Show new end time
            if new_end:
                embed.add_field(
                    name='New End Time',
                    value=f'<t:{int(new_end)}:F>',
                    inline=False
                )
        else:
            embed = create_embed(
                title='Extension Failed',
                description=message,
                color=EMBED_COLOR_WARNING
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @stakeout_group.command(name='status', description='Quick status check for a stakeout target')
    @app_commands.describe(torn_id='Torn player ID to check')
    async def stakeout_status(interaction: discord.Interaction, torn_id: str):
        """Quick status check"""
        await interaction.response.defer(ephemeral=True)

        result = await manager.get_quick_status(torn_id)

        if not result:
            embed = create_embed(
                title='Not Found',
                description=f'Player {torn_id} is not being watched and has no history.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if result.get('status') == 'stopped':
            embed = create_embed(
                title='Stakeout Stopped',
                description=f'Player {torn_id} is no longer being watched.\nUse `/enemy stakeout report {torn_id}` to view collected data.',
                color=EMBED_COLOR_INFO
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if result.get('status') == 'error':
            embed = create_embed(
                title='Error',
                description=result.get('message', 'Could not fetch status'),
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Build status embed
        current = result['current_status']
        extra = result.get('extra_info')

        # Status emoji and text
        status_display = {
            'online': '🟢 Online',
            'offline': '⚫ Offline',
            'idle': '🟡 Idle',
            'hospital': '🏥 Hospital',
            'jail': '🔒 Jail',
            'traveling': '✈️ Traveling'
        }.get(current, f'❓ {current}')

        # Add extra info if available
        if current == 'traveling' and extra:
            status_display += f' to {extra}'
        elif current == 'hospital' and extra:
            status_display += f' (until <t:{extra}:R>)'
        elif current == 'jail' and extra:
            status_display += f' (until <t:{extra}:R>)'
        elif current in ['offline', 'idle'] and extra:
            status_display += f' ({extra} mins)'

        embed = create_embed(
            title=f"Status: {result['name']}",
            description=f"[View Profile](https://www.torn.com/profiles.php?XID={result['torn_id']})",
            color=EMBED_COLOR_INFO
        )

        embed.add_field(name='Current Status', value=status_display, inline=True)
        embed.add_field(name='Level', value=str(result.get('level', '?')), inline=True)
        embed.add_field(name='Faction', value=result.get('faction', 'None'), inline=True)

        remaining = result['remaining_hours']
        if remaining <= 0:
            time_left = 'Expired'
        elif remaining > 1:
            time_left = f'{remaining:.1f} hours'
        else:
            time_left = f'{max(1, int(remaining * 60))} minutes'

        embed.add_field(name='Stakeout Remaining', value=time_left, inline=True)
        embed.add_field(name='Records Collected', value=str(result['record_count']), inline=True)

        if result.get('mins_since_action') is not None:
            embed.add_field(name='Last Action', value=f'{result["mins_since_action"]} mins ago', inline=True)

        embed.set_footer(text='Use /enemy stakeout report for full analysis')
        await interaction.followup.send(embed=embed, ephemeral=True)

    @stakeout_group.command(name='clear', description='[Management] Clear all stakeouts (use to fix corrupted data)')
    async def stakeout_clear(interaction: discord.Interaction):
        """Clear all stakeouts"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        count = len(manager.stakeouts)
        manager.stakeouts = {}
        manager.history = {}
        await manager.save_data()

        embed = create_embed(
            title='Stakeouts Cleared',
            description=f'Cleared {count} stakeout(s) and all history data.',
            color=EMBED_COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @stakeout_group.command(name='list', description='Show all active stakeouts')
    async def stakeout_list(interaction: discord.Interaction):
        """List active stakeouts"""
        stakeouts = manager.get_active_stakeouts()

        if not stakeouts:
            embed = create_embed(
                title='No Active Stakeouts',
                description='No players are currently being watched.\n\nUse `/enemy stakeout add <torn_id>` to start.',
                color=EMBED_COLOR_INFO
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        lines = []
        for s in stakeouts:
            remaining = s['remaining_hours']
            if remaining <= 0:
                hours_str = 'Expiring...'
            elif remaining > 1:
                hours_str = f'{remaining:.1f}h remaining'
            else:
                hours_str = f'{max(1, int(remaining * 60))}m remaining'
            lines.append(f"**{s['name']}** [{s['torn_id']}] — {s['record_count']} records | {hours_str}")

        description = 'Currently watching:\n\n' + '\n'.join(lines)
        # Chunk if exceeding embed description limit
        if len(description) > 4000:
            description = description[:4000] + '\n\n... (list truncated)'

        embed = create_embed(
            title=f'Active Stakeouts ({len(stakeouts)})',
            description=description,
            color=EMBED_COLOR_INFO
        )
        embed.set_footer(text='Use /enemy stakeout report <torn_id> to see behavior analysis')
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @stakeout_group.command(name='report', description='View behavior analysis for a watched player')
    @app_commands.describe(torn_id='Torn player ID to report on')
    async def stakeout_report(interaction: discord.Interaction, torn_id: str):
        """Generate and show behavior report"""
        await interaction.response.defer()

        report = manager.generate_report(torn_id)

        if not report:
            embed = create_embed(
                title='No Data Available',
                description=f'No stakeout data found for player {torn_id}.\n\nUse `/enemy stakeout add {torn_id}` to start watching.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.followup.send(embed=embed)
            return

        # Main embed
        embed = create_embed(
            title=f"Stakeout Report: {report['name']}",
            description=f"[View Profile](https://www.torn.com/profiles.php?XID={report['torn_id']})",
            color=EMBED_COLOR_INFO
        )

        # Coverage / overall online rate
        embed.add_field(
            name='Data Collected',
            value=f"{report['total_checks']} checks over {report['duration_hours']:.1f}h "
                  f"({report.get('hours_covered', 0)}/24h sampled)",
            inline=False
        )
        embed.add_field(
            name='Online Rate',
            value=f"{report['online_pct']:.0f}% overall",
            inline=True
        )

        weekday_pct = report.get('weekday_online_pct', 0)
        weekend_pct = report.get('weekend_online_pct', 0)
        if weekday_pct > 0 or weekend_pct > 0:
            embed.add_field(
                name='Weekday',
                value=f"{weekday_pct:.0f}% online",
                inline=True
            )
            embed.add_field(
                name='Weekend',
                value=f"{weekend_pct:.0f}% online",
                inline=True
            )

        # Day of week breakdown — most/least active
        day_activity = report.get('day_activity', [])
        if len(day_activity) >= 3:
            most_active = day_activity[0]
            least_active = day_activity[-1]
            embed.add_field(
                name='⚠️ Most Active Day',
                value=f"**{most_active[0]}** ({most_active[1]:.0f}% online)",
                inline=True
            )
            embed.add_field(
                name='✅ Least Active Day',
                value=f"**{least_active[0]}** ({least_active[1]:.0f}% online)",
                inline=True
            )

        # PRIMARY OUTPUT 1: peak-online window (avoid chaining here)
        peak = report.get('peak_online_window')
        if peak:
            s, e, conf = peak
            next_start = next_occurrence_of_hour(s)
            avoid_text = (
                f"🔴 **{s:02d}:00 – {e:02d}:00 TCT** "
                f"(your time: <t:{next_start}:t> next at <t:{next_start}:R>)\n"
                f"Don't chain or long-attack — target is most likely watching.\n"
                f"Confidence: {conf*100:.0f}%"
            )
        else:
            avoid_text = 'Not enough data yet — keep stakeout running.'
        embed.add_field(
            name='🚫 Avoid Window (they\'re watching)',
            value=avoid_text,
            inline=False
        )

        # PRIMARY OUTPUT 2: sleep window (green light for chains)
        if report['sleep_window']:
            s, e, conf = report['sleep_window']
            next_start = next_occurrence_of_hour(s)
            sleep_text = (
                f"🟢 **{s:02d}:00 – {e:02d}:00 TCT** "
                f"(your time: <t:{next_start}:t> next at <t:{next_start}:R>)\n"
                f"Safe to coordinate chains / long attacks — target is offline.\n"
                f"Confidence: {conf*100:.0f}%"
            )
        else:
            sleep_text = 'Not enough data yet — keep stakeout running.'
        embed.add_field(
            name='💤 Sleep Window (chain safely)',
            value=sleep_text,
            inline=False
        )

        # Timezone estimate
        if report['estimated_tz']:
            embed.add_field(
                name='Estimated Timezone',
                value=report['estimated_tz'],
                inline=True
            )

        # Heatmap as code block
        heatmap_text = manager.format_heatmap_text(report)

        first_unix = int(report['first_check'].timestamp())
        last_unix = int(report['last_check'].timestamp())
        embed.description = (embed.description or '') + (
            f"\n\n*Stakeout data: <t:{first_unix}:f> → <t:{last_unix}:f>*"
        )

        # Heatmap as plain markdown so colour emoji render (no code block).
        await interaction.followup.send(content=heatmap_text, embed=embed)

    @stakeout_group.command(name='test', description='[Management] Test API connectivity for stakeout')
    @app_commands.describe(torn_id='Optional: Test with a specific Torn player ID')
    async def stakeout_test(interaction: discord.Interaction, torn_id: str = '1'):
        """Test if personal API key is working"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        from config import TORN_PERSONAL_API_KEY

        # Check if key is configured
        if not TORN_PERSONAL_API_KEY:
            embed = create_embed(
                title='❌ API Key Missing',
                description='`TORN_PERSONAL_API_KEY` is not set in environment variables.\n\nPlease add your personal Torn API key to Railway.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Try to fetch the player with same selections as stakeout uses
        test_data = await fetch_player_data(torn_id, selections='profile,travel')

        if test_data:
            name = test_data.get('name', 'Unknown')
            level = test_data.get('level', '?')
            embed = create_embed(
                title='✅ API Connection Working',
                description=f"Successfully fetched player data.\n\n**Player:** {name} [{torn_id}]\n**Level:** {level}",
                color=EMBED_COLOR_SUCCESS
            )
        else:
            embed = create_embed(
                title='❌ Player Fetch Failed',
                description=f'Could not fetch player {torn_id}.\n\nPossible reasons:\n• Player ID does not exist\n• Player account is fedded/deleted\n• API rate limited\n\nCheck Railway logs for the actual error.',
                color=EMBED_COLOR_CRITICAL
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # Attached to enemy_group via parent=; bot.py adds the tree once.
    logger.info('Stakeout commands registered (/enemy stakeout add, stop, extend, status, list, report, clear, test)')
