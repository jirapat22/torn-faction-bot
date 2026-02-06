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

Commands:
- /stakeout add <torn_id> [hours] - Start watching (default 24h, max 48h)
- /stakeout list - Show active stakeouts
- /stakeout report <torn_id> - Show behavior analysis
- /stakeout stop <torn_id> - Stop watching
"""

import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from collections import defaultdict

from utils.helpers import load_json, save_json, create_embed
from utils.torn_api import fetch_player_data
from config import (
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    MANAGEMENT_ROLE_IDS
)


# Status codes for logging
STATUS_ONLINE = 'online'
STATUS_OFFLINE = 'offline'
STATUS_IDLE = 'idle'
STATUS_HOSPITAL = 'hospital'
STATUS_TRAVELING = 'traveling'
STATUS_JAIL = 'jail'
STATUS_UNKNOWN = 'unknown'

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

        print(f'Loaded {len(self.stakeouts)} active stakeouts')

    async def save_data(self):
        """Save stakeout data to JSON"""
        save_json(self.data_file, {
            'stakeouts': self.stakeouts,
            'history': self.history
        })

    async def _cleanup_expired(self):
        """Remove stakeouts that have exceeded their duration"""
        now = datetime.now(timezone.utc).timestamp()
        expired = []

        for torn_id, config in self.stakeouts.items():
            end_time = config.get('end_time', 0)
            if now >= end_time:
                expired.append(torn_id)

        for torn_id in expired:
            del self.stakeouts[torn_id]
            # Keep history for reports even after stakeout ends
            print(f'Stakeout expired for {torn_id}')

        if expired:
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
        print(f'Stakeout added: {torn_id} ({player_name})')

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
            print(f'Stakeout: Retrying {torn_id} without travel selection')
            data = await fetch_player_data(torn_id, selections='profile')
        if not data:
            print(f'Stakeout: Failed to fetch player {torn_id} - check TORN_PERSONAL_API_KEY')
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
            'status': status,
            'extra': extra
        }

        if torn_id not in self.history:
            self.history[torn_id] = []

        self.history[torn_id].append(record)
        self.stakeouts[torn_id]['check_count'] = len(self.history[torn_id])

    async def check_all_stakeouts(self):
        """Check status of all active stakeouts (called by background task)"""
        await self._cleanup_expired()

        if not self.stakeouts:
            return

        for torn_id in list(self.stakeouts.keys()):
            await self._record_status(torn_id)

        await self.save_data()
        print(f'Stakeout check: recorded status for {len(self.stakeouts)} targets')

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

        # Calculate hourly activity counts
        hourly_online = defaultdict(int)
        hourly_offline = defaultdict(int)
        hourly_hospital = defaultdict(int)
        hourly_total = defaultdict(int)

        status_counts = defaultdict(int)

        for record in history:
            hour = record['hour']
            status = record['status']

            hourly_total[hour] += 1
            status_counts[status] += 1

            if status in [STATUS_ONLINE, STATUS_IDLE]:
                hourly_online[hour] += 1
            elif status == STATUS_OFFLINE:
                hourly_offline[hour] += 1
            elif status == STATUS_HOSPITAL:
                hourly_hospital[hour] += 1

        # Generate heatmap (24 hours)
        heatmap = []
        for hour in range(24):
            total = hourly_total.get(hour, 0)
            online = hourly_online.get(hour, 0)

            if total == 0:
                heatmap.append('·')  # No data
            else:
                ratio = online / total
                if ratio >= 0.75:
                    heatmap.append('█')  # Very active
                elif ratio >= 0.5:
                    heatmap.append('▓')  # Active
                elif ratio >= 0.25:
                    heatmap.append('▒')  # Sometimes active
                else:
                    heatmap.append('░')  # Rarely active

        # Find sleep window (longest consecutive low-activity period)
        sleep_window = self._find_sleep_window(hourly_online, hourly_total)

        # Find best attack windows (hours with lowest online ratio)
        attack_windows = self._find_attack_windows(hourly_online, hourly_total)

        # Estimate timezone
        estimated_tz = self._estimate_timezone(sleep_window)

        # Calculate overall stats
        total_checks = len(history)
        online_pct = (status_counts[STATUS_ONLINE] + status_counts[STATUS_IDLE]) / total_checks * 100 if total_checks > 0 else 0
        hospital_pct = status_counts[STATUS_HOSPITAL] / total_checks * 100 if total_checks > 0 else 0

        # Time range
        first_check = datetime.fromtimestamp(history[0]['timestamp'], tz=timezone.utc)
        last_check = datetime.fromtimestamp(history[-1]['timestamp'], tz=timezone.utc)
        duration = last_check - first_check

        return {
            'torn_id': torn_id,
            'name': name,
            'total_checks': total_checks,
            'duration_hours': duration.total_seconds() / 3600,
            'first_check': first_check,
            'last_check': last_check,
            'heatmap': heatmap,
            'sleep_window': sleep_window,
            'attack_windows': attack_windows,
            'estimated_tz': estimated_tz,
            'online_pct': online_pct,
            'hospital_pct': hospital_pct,
            'status_counts': dict(status_counts),
            'hourly_online': dict(hourly_online),
            'hourly_total': dict(hourly_total)
        }

    def _find_sleep_window(self, hourly_online: Dict, hourly_total: Dict) -> Optional[tuple]:
        """
        Find likely sleep window (consecutive hours with low activity)

        Returns (start_hour, end_hour, confidence) or None
        """
        # Calculate offline ratio for each hour
        ratios = {}
        for hour in range(24):
            total = hourly_total.get(hour, 0)
            online = hourly_online.get(hour, 0)
            if total >= 2:  # Need at least 2 data points
                ratios[hour] = 1 - (online / total)  # Offline ratio

        if len(ratios) < 12:  # Need at least half a day of data
            return None

        # Find longest stretch of hours with >60% offline
        best_start = None
        best_length = 0
        best_confidence = 0

        for start in range(24):
            length = 0
            total_confidence = 0

            for offset in range(24):
                hour = (start + offset) % 24
                if hour in ratios and ratios[hour] >= 0.6:
                    length += 1
                    total_confidence += ratios[hour]
                else:
                    break

            if length >= 4 and length > best_length:  # At least 4 hours
                best_start = start
                best_length = length
                best_confidence = total_confidence / length if length > 0 else 0

        if best_start is not None:
            end_hour = (best_start + best_length) % 24
            return (best_start, end_hour, best_confidence)

        return None

    def _find_attack_windows(self, hourly_online: Dict, hourly_total: Dict) -> List[tuple]:
        """
        Find best attack windows (hours with lowest online ratio)

        Returns list of (hour, offline_ratio) tuples, top 3
        """
        windows = []

        for hour in range(24):
            total = hourly_total.get(hour, 0)
            online = hourly_online.get(hour, 0)

            if total >= 2:
                offline_ratio = 1 - (online / total)
                windows.append((hour, offline_ratio))

        # Sort by offline ratio descending
        windows.sort(key=lambda x: x[1], reverse=True)

        return windows[:5]  # Top 5 best hours

    def _estimate_timezone(self, sleep_window: Optional[tuple]) -> Optional[str]:
        """
        Estimate timezone based on sleep window

        Assumes typical sleep is 00:00-08:00 local time
        """
        if not sleep_window:
            return None

        start_hour, end_hour, _ = sleep_window

        # Assume mid-sleep is around 04:00 local time
        if end_hour < start_hour:
            # Sleep crosses midnight
            sleep_length = (24 - start_hour) + end_hour
        else:
            sleep_length = end_hour - start_hour

        mid_sleep_utc = (start_hour + sleep_length // 2) % 24

        # If mid-sleep is at hour X UTC, and we assume it's 04:00 local...
        # local = utc + offset
        # 4 = mid_sleep_utc + offset
        # offset = 4 - mid_sleep_utc
        offset = 4 - mid_sleep_utc
        if offset > 12:
            offset -= 24
        elif offset < -12:
            offset += 24

        return TIMEZONE_NAMES.get(offset, f'UTC{offset:+d}')

    def format_heatmap_text(self, report: Dict) -> str:
        """Format heatmap as ASCII art"""
        heatmap = report['heatmap']

        lines = []
        lines.append('Activity Heatmap (TCT/UTC hours)')
        lines.append('█=Active ▓=Often ▒=Sometimes ░=Rarely ·=No data')
        lines.append('')

        # Hour labels (0-23)
        hour_labels_1 = ''.join([str(h // 10) if h >= 10 else ' ' for h in range(24)])
        hour_labels_2 = ''.join([str(h % 10) for h in range(24)])

        lines.append(f'Hour: {hour_labels_1}')
        lines.append(f'      {hour_labels_2}')
        lines.append(f'      {"".join(heatmap)}')

        return '\n'.join(lines)


async def setup_stakeout_commands(bot, manager: StakeoutManager):
    """Register stakeout slash commands"""

    def is_management(interaction: discord.Interaction) -> bool:
        """Check if user has management permissions"""
        if interaction.user.guild_permissions.manage_guild:
            return True
        if MANAGEMENT_ROLE_IDS:
            user_role_ids = [role.id for role in interaction.user.roles]
            if any(role_id in MANAGEMENT_ROLE_IDS for role_id in user_role_ids):
                return True
        return False

    stakeout_group = app_commands.Group(name='stakeout', description='Track enemy player behavior')

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
                value='Use `/stakeout report` after a few hours to see patterns.',
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
                description=message + '\n\nHistory preserved - you can still run `/stakeout report` to see collected data.',
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
                description=f'Player {torn_id} is no longer being watched.\nUse `/stakeout report {torn_id}` to view collected data.',
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
        if remaining > 1:
            time_left = f'{remaining:.1f} hours'
        else:
            time_left = f'{int(remaining * 60)} minutes'

        embed.add_field(name='Stakeout Remaining', value=time_left, inline=True)
        embed.add_field(name='Records Collected', value=str(result['record_count']), inline=True)

        if result.get('mins_since_action') is not None:
            embed.add_field(name='Last Action', value=f'{result["mins_since_action"]} mins ago', inline=True)

        embed.set_footer(text='Use /stakeout report for full analysis')
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
                description='No players are currently being watched.\n\nUse `/stakeout add <torn_id>` to start.',
                color=EMBED_COLOR_INFO
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = create_embed(
            title=f'Active Stakeouts ({len(stakeouts)})',
            description='Currently watching:',
            color=EMBED_COLOR_INFO
        )

        for s in stakeouts:
            remaining = s['remaining_hours']
            hours_str = f'{remaining:.1f}h remaining' if remaining > 1 else f'{int(remaining * 60)}m remaining'

            embed.add_field(
                name=f"{s['name']} [{s['torn_id']}]",
                value=f"Records: {s['record_count']} | {hours_str}",
                inline=False
            )

        embed.set_footer(text='Use /stakeout report <torn_id> to see behavior analysis')
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
                description=f'No stakeout data found for player {torn_id}.\n\nUse `/stakeout add {torn_id}` to start watching.',
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

        # Stats
        embed.add_field(
            name='Data Collected',
            value=f"{report['total_checks']} checks over {report['duration_hours']:.1f}h",
            inline=True
        )
        embed.add_field(
            name='Online Rate',
            value=f"{report['online_pct']:.0f}%",
            inline=True
        )
        embed.add_field(
            name='Hospital Rate',
            value=f"{report['hospital_pct']:.0f}%",
            inline=True
        )

        # Sleep window
        if report['sleep_window']:
            start, end, confidence = report['sleep_window']
            sleep_text = f"**{start:02d}:00 - {end:02d}:00 TCT** ({confidence*100:.0f}% confidence)"
        else:
            sleep_text = 'Not enough data yet'

        embed.add_field(
            name='Likely Sleep Window',
            value=sleep_text,
            inline=False
        )

        # Best attack times
        if report['attack_windows']:
            attack_text = '\n'.join([
                f"**{hour:02d}:00 TCT** - {ratio*100:.0f}% offline"
                for hour, ratio in report['attack_windows'][:3]
            ])
        else:
            attack_text = 'Not enough data yet'

        embed.add_field(
            name='Best Attack Windows',
            value=attack_text,
            inline=False
        )

        # Timezone estimate
        if report['estimated_tz']:
            embed.add_field(
                name='Estimated Timezone',
                value=report['estimated_tz'],
                inline=False
            )

        # Heatmap as code block
        heatmap_text = manager.format_heatmap_text(report)

        embed.set_footer(text=f"Data from {report['first_check'].strftime('%d-%m %H:%M')} to {report['last_check'].strftime('%d-%m %H:%M')} TCT")

        await interaction.followup.send(content=f'```\n{heatmap_text}\n```', embed=embed)

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

    # Add group to bot
    bot.tree.add_command(stakeout_group)
    print('Stakeout commands registered (/stakeout add, stop, extend, status, list, report, clear, test)')
