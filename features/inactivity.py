"""
Inactivity Tracking Module
Tracks ALL faction members via Torn API and sends alerts for inactive members

NEW BEHAVIOR:
- Checks ALL faction members from Torn API (not just registered Discord users)
- For registered members: Shows Discord @mention + Torn info
- For unregistered members: Shows Torn name only
- ALWAYS posts to #inactivity-alerts channel
- Registration is OPTIONAL (only needed for Discord mentions)
"""

import time
import discord
from datetime import datetime
from utils.helpers import load_json, save_json, create_embed, format_timestamp, get_days_since, get_current_timestamp
from utils.torn_api import fetch_faction_members, fetch_faction_crimes
from config import (
    INACTIVITY_ALERTS_CHANNEL_ID,
    INACTIVITY_WARNING_DAYS,
    INACTIVITY_CRITICAL_DAYS,
    OC_WARNING_DAYS,
    OC_CRITICAL_DAYS,
    OC_NEW_MEMBER_GRACE_DAYS,
    LEFT_FACTION_ALERT_DAYS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    EMBED_COLOR_SUCCESS,
    GUILD_ID,
    MANAGEMENT_PING_ROLE_ID
)


import logging

logger = logging.getLogger(__name__)

# Backoff schedule for the daily check's Torn fetch. Covers ~6 minutes of
# transient API trouble (blip, brief maintenance) before giving up until
# tomorrow — see check_and_alert_inactive for why this loop alone retries.
DAILY_FETCH_RETRY_DELAYS = [60, 120, 180]


class InactivityTracker:
    """Tracks member activity on Torn and sends inactivity alerts"""

    def __init__(self, bot):
        self.bot = bot
        self.user_mapping = {}  # {discord_id: torn_id}
        self.mapping_file = 'data/discord_to_torn.json'
        self.oc_data_file = 'data/oc_participation.json'
        self.last_oc_completion = {}  # {torn_id_str: float timestamp} — accumulated across runs
        self.left_faction_file = 'data/left_faction.json'
        self.left_faction = {}  # {torn_id_str: {first_gone, torn_name, discord_id}}

    async def load_user_mapping(self):
        """Load Discord to Torn ID mapping from JSON file"""
        self.user_mapping = load_json(self.mapping_file, default={})
        logger.info(f'Loaded Torn ID mappings for {len(self.user_mapping)} Discord users')

    async def load_oc_data(self):
        """Load accumulated OC participation data from JSON.
        If the file is from an old format (no version key), clears stale v1 data
        and starts fresh so v2 API data can accumulate cleanly.
        """
        raw = load_json(self.oc_data_file, default={})
        if raw.get('version') != 2:
            # Old format or first run — clear stale data and start fresh
            self.last_oc_completion = {}
            logger.info('OC data reset: clearing old data, will accumulate fresh v2 OC history')
            return
        data = raw.get('data', {})
        self.last_oc_completion = {str(k): float(v) for k, v in data.items() if v}
        logger.info(f'Loaded OC participation data for {len(self.last_oc_completion)} members')

    async def save_oc_data(self):
        """Save accumulated OC participation data to JSON"""
        save_json(self.oc_data_file, {'version': 2, 'data': self.last_oc_completion})

    async def load_left_faction_data(self):
        """Load left-faction tracking data from JSON."""
        raw = load_json(self.left_faction_file, default={})
        self.left_faction = {str(k): v for k, v in raw.items()}
        logger.info(f'Loaded left-faction data for {len(self.left_faction)} members')

    async def save_left_faction_data(self):
        """Save left-faction tracking data to JSON."""
        save_json(self.left_faction_file, self.left_faction)

    async def save_user_mapping(self):
        """Save Discord to Torn ID mapping to JSON file"""
        save_json(self.mapping_file, self.user_mapping)

    async def register_user(self, discord_id, torn_id):
        """
        Register a Discord user with their Torn ID

        Args:
            discord_id: Discord user ID (int or str)
            torn_id: Torn user ID (int or str)

        Returns:
            True if successful, False if failed
        """
        # Verify the Torn ID exists in faction
        members = await fetch_faction_members()

        if members is None:
            return False, "Could not connect to Torn API"

        if str(torn_id) not in members:
            return False, f"Torn ID {torn_id} not found in faction"

        # Store the mapping
        self.user_mapping[str(discord_id)] = str(torn_id)
        await self.save_user_mapping()

        torn_name = members[str(torn_id)].get('name', 'Unknown')
        return True, f"Linked to Torn user: {torn_name} [{torn_id}]"

    async def unregister_user(self, discord_id):
        """
        Unregister a Discord user (remove their Torn ID link)

        Args:
            discord_id: Discord user ID

        Returns:
            True if successful, False if user wasn't registered
        """
        discord_id_str = str(discord_id)

        if discord_id_str not in self.user_mapping:
            return False

        del self.user_mapping[discord_id_str]
        await self.save_user_mapping()
        return True

    def get_torn_id(self, discord_id):
        """
        Get Torn ID for a Discord user

        Args:
            discord_id: Discord user ID

        Returns:
            Torn ID (str) or None if not registered
        """
        return self.user_mapping.get(str(discord_id))

    def get_discord_id(self, torn_id):
        """
        Get Discord ID for a Torn user

        Args:
            torn_id: Torn user ID

        Returns:
            Discord ID (str) or None if not registered
        """
        for discord_id, tid in self.user_mapping.items():
            if tid == str(torn_id):
                return discord_id
        return None

    def _get_oc_status(self, torn_members, crimes):
        """
        Determine OC participation status for each member.

        Updates self.last_oc_completion with any new completions found in crimes.
        Only flags members who appear in accumulated OC data (skips unknowns to
        avoid false positives on first run or for members outside API history window).

        Returns:
            (oc_warning, oc_critical, data_updated) — lists and a bool flag.
        """
        now = time.time()
        data_updated = False

        # Build active_in_oc and update accumulated last_oc_completion
        # crimes is a normalized list from fetch_faction_crimes()
        active_in_oc = set()

        for crime in (crimes if isinstance(crimes, list) else []):
            if not isinstance(crime, dict):
                continue
            completed = crime.get('completed', False)
            completion_ts = crime.get('completion_ts', 0)
            for tid in crime.get('participants', []):
                tid = str(tid)
                if not completed:
                    active_in_oc.add(tid)
                elif completion_ts:
                    tc = float(completion_ts)
                    if tid not in self.last_oc_completion or tc > self.last_oc_completion[tid]:
                        self.last_oc_completion[tid] = tc
                        data_updated = True

        oc_warning = []
        oc_critical = []

        for torn_id, member in torn_members.items():
            tid = str(torn_id)
            days_in_faction = member.get('days_in_faction', 0)

            if days_in_faction < OC_NEW_MEMBER_GRACE_DAYS:
                continue

            if tid in active_in_oc:
                continue

            last_oc_ts = self.last_oc_completion.get(tid)
            if last_oc_ts is None:
                # No accumulated OC history — skip to avoid false positives
                # (data builds up over time with each daily run)
                continue

            days_since = (now - last_oc_ts) / 86400
            if days_since < OC_WARNING_DAYS:
                continue

            member_data = {
                'torn_name': member.get('name', f'ID:{tid}'),
                'torn_id': tid,
                'torn_level': member.get('level', '?'),
                'days': round(days_since, 1),
                'last_oc_ts': last_oc_ts,
            }

            if days_since >= OC_CRITICAL_DAYS:
                oc_critical.append(member_data)
            else:
                oc_warning.append(member_data)

        return oc_warning, oc_critical, data_updated

    def _get_left_faction_status(self, torn_members, guild_member_ids=None):
        """
        Detect verified Discord members who are no longer in the Torn faction.

        - First time detected as gone: records timestamp
        - Rejoined faction: removes from tracking
        - Gone >= LEFT_FACTION_ALERT_DAYS: added to alert list

        Returns:
            (alerts, data_updated)
            alerts: list of dicts with torn_id, torn_name, discord_id, days_gone
        """
        now = time.time()
        data_updated = False
        alerts = []

        current_torn_ids = set(str(tid) for tid in torn_members.keys())

        # Name lookup from verified_users (so we have names even after they leave)
        verified_users = load_json('data/verified_users.json', default={})
        torn_id_to_name = {}
        for d_id, data in verified_users.items():
            tid = str(data.get('torn_id', ''))
            name = data.get('torn_name', '')
            if tid and name:
                torn_id_to_name[tid] = name

        LEFT_FACTION_MAX_DAYS = 30     # Stop tracking after 30 days (clearly not returning)

        for discord_id, torn_id in self.user_mapping.items():
            tid = str(torn_id)

            if tid in current_torn_ids:
                # Back in faction — stop tracking
                if tid in self.left_faction:
                    del self.left_faction[tid]
                    data_updated = True
            else:
                # Not in faction

                # Stop tracking if they've left the Discord server
                if guild_member_ids is not None and discord_id not in guild_member_ids:
                    if tid in self.left_faction:
                        torn_name = self.left_faction[tid].get('torn_name', tid)
                        del self.left_faction[tid]
                        data_updated = True
                        logger.info(f'Left-faction: Removed {torn_name} — no longer in Discord server')
                        continue

                if tid not in self.left_faction:
                    torn_name = torn_id_to_name.get(tid, f'ID:{tid}')
                    self.left_faction[tid] = {
                        'first_gone': now,
                        'torn_name': torn_name,
                        'discord_id': discord_id,
                    }
                    data_updated = True

                entry = self.left_faction[tid]
                days_gone = (now - entry['first_gone']) / 86400

                # Auto-remove after 30 days — they're clearly not coming back
                if days_gone >= LEFT_FACTION_MAX_DAYS:
                    del self.left_faction[tid]
                    data_updated = True
                    logger.info(f'Left-faction: Auto-removed {entry["torn_name"]} after {days_gone:.0f}d')
                    continue

                if days_gone >= LEFT_FACTION_ALERT_DAYS:
                    alerts.append({
                        'torn_id': tid,
                        'torn_name': entry['torn_name'],
                        'discord_id': entry['discord_id'],
                        'days_gone': round(days_gone, 1),
                    })

        return alerts, data_updated

    async def check_and_alert_inactive(self):
        """
        Check for inactive members on Torn and send alerts
        NOW CHECKS ALL FACTION MEMBERS (registered or not)
        Called by the daily background task
        """
        logger.info(f'Running daily Torn inactivity check... ({datetime.now()})')

        # Get the alerts channel - ALWAYS use inactivity-alerts channel
        channel = self.bot.get_channel(INACTIVITY_ALERTS_CHANNEL_ID)

        if not channel:
            logger.error(f'ERROR: Could not find inactivity alerts channel (ID: {INACTIVITY_ALERTS_CHANNEL_ID})')
            logger.info('Please check INACTIVITY_ALERTS_CHANNEL_ID in config.py')
            return

        # Get the guild
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            logger.warning(f'Warning: Could not find guild (ID: {GUILD_ID})')
            # Still continue - we can check faction members without Discord

        # Fetch Torn faction member data and crimes in parallel
        import asyncio
        torn_members, crimes = await asyncio.gather(
            fetch_faction_members(),
            fetch_faction_crimes()
        )

        # This is the only 24h loop in the bot: every other background check
        # re-runs within 1-15 minutes, so a momentary Torn blip costs them one
        # tick, while a blip here costs a full day of accountability data.
        # Torn API failures aren't cached (only successes are), so an in-place
        # retry genuinely re-hits the API rather than replaying the failure.
        if not torn_members:
            for delay in DAILY_FETCH_RETRY_DELAYS:
                logger.warning(f'Inactivity check: Torn fetch failed, retrying in {delay}s')
                await asyncio.sleep(delay)
                torn_members, crimes = await asyncio.gather(
                    fetch_faction_members(),
                    fetch_faction_crimes()
                )
                if torn_members:
                    logger.info('Inactivity check: Torn fetch recovered on retry')
                    break

        if not torn_members:
            total_wait = sum(DAILY_FETCH_RETRY_DELAYS) // 60
            logger.error('Error: Could not fetch Torn faction data (all retries exhausted)')
            error_embed = create_embed(
                title='Inactivity Check Failed',
                description=f'Could not connect to Torn API to check member activity, after '
                            f'{len(DAILY_FETCH_RETRY_DELAYS)} retries over ~{total_wait} minutes. '
                            f'Will retry tomorrow.',
                color=EMBED_COLOR_CRITICAL
            )
            await channel.send(embed=error_embed)
            return

        if not crimes:
            crimes = []

        # Track inactive members
        warning_members = []  # 3+ days inactive
        critical_members = []  # 5+ days inactive

        # Check ALL faction members from Torn API
        logger.info(f'Checking {len(torn_members)} faction members...')
        for torn_id, torn_member in torn_members.items():
            # Get last action timestamp from Torn
            last_action_data = torn_member.get('last_action', {})
            last_action_timestamp = last_action_data.get('timestamp')

            if not last_action_timestamp:
                # No last action data, skip this member
                continue

            # Calculate days inactive
            days_inactive = get_days_since(last_action_timestamp)

            # Skip if member is active (less than 3 days)
            if days_inactive < INACTIVITY_WARNING_DAYS:
                continue

            # Get member info from Torn
            torn_name = torn_member.get('name', 'Unknown')
            torn_level = torn_member.get('level', '?')

            # Check if this Torn user is registered in Discord
            discord_id = self.get_discord_id(torn_id)
            discord_member = None

            if discord_id and guild:
                try:
                    discord_member = guild.get_member(int(discord_id))
                    if not discord_member:
                        discord_member = await guild.fetch_member(int(discord_id))
                except (discord.NotFound, discord.HTTPException, ValueError):
                    pass  # User not in Discord server or invalid ID

            # Build member data
            member_data = {
                'discord_member': discord_member,  # Will be None if not registered
                'torn_name': torn_name,
                'torn_id': torn_id,
                'torn_level': torn_level,
                'days': days_inactive,
                'last_seen': last_action_timestamp
            }

            # Check thresholds
            if days_inactive >= INACTIVITY_CRITICAL_DAYS:
                critical_members.append(member_data)
            elif days_inactive >= INACTIVITY_WARNING_DAYS:
                warning_members.append(member_data)

        # OC participation check
        oc_warning, oc_critical, oc_updated = self._get_oc_status(torn_members, crimes)
        if oc_updated:
            await self.save_oc_data()
        logger.info(f'OC check: {len(oc_warning)} warning, {len(oc_critical)} critical out of {len(torn_members)} total')

        # Left-faction check (verified Discord members no longer in Torn faction)
        guild_member_ids = {str(m.id) for m in guild.members} if guild else None
        left_alerts, left_updated = self._get_left_faction_status(torn_members, guild_member_ids)
        if left_updated:
            await self.save_left_faction_data()
        logger.info(f'Left-faction check: {len(left_alerts)} alert(s), tracking {len(self.left_faction)} members')

        # Always post daily summary
        total_inactive = len(warning_members) + len(critical_members)
        total_checked = len(torn_members)

        logger.info(f'Inactivity check: {total_inactive} inactive out of {total_checked} total')
        await self._send_inactivity_alert(channel, warning_members, critical_members, total_checked, oc_warning, oc_critical, left_alerts)

    async def test_run(self):
        """
        Run inactivity + OC checks silently — no channel posts.
        Returns a summary dict for ephemeral display via /test command.
        """
        import asyncio
        torn_members, crimes = await asyncio.gather(
            fetch_faction_members(),
            fetch_faction_crimes()
        )
        if not torn_members:
            return None
        if not crimes:
            crimes = []

        warning_members = []
        critical_members = []

        for torn_id, torn_member in torn_members.items():
            last_action_data = torn_member.get('last_action', {})
            last_action_timestamp = last_action_data.get('timestamp')
            if not last_action_timestamp:
                continue
            days_inactive = get_days_since(last_action_timestamp)
            if days_inactive < INACTIVITY_WARNING_DAYS:
                continue
            member_data = {
                'torn_name': torn_member.get('name', 'Unknown'),
                'torn_id': torn_id,
                'torn_level': torn_member.get('level', '?'),
                'days': days_inactive,
            }
            if days_inactive >= INACTIVITY_CRITICAL_DAYS:
                critical_members.append(member_data)
            else:
                warning_members.append(member_data)

        oc_warning, oc_critical, oc_updated = self._get_oc_status(torn_members, crimes)
        if oc_updated:
            await self.save_oc_data()

        guild = self.bot.get_guild(GUILD_ID)
        guild_member_ids = {str(m.id) for m in guild.members} if guild else None
        left_alerts, left_updated = self._get_left_faction_status(torn_members, guild_member_ids)
        if left_updated:
            await self.save_left_faction_data()

        return {
            'total': len(torn_members),
            'inactivity_critical': critical_members,
            'inactivity_warning': warning_members,
            'oc_critical': oc_critical,
            'oc_warning': oc_warning,
            'left_faction': left_alerts,
        }

    async def _send_inactivity_alert(self, channel, warning_members, critical_members, total_faction_members, oc_warning=None, oc_critical=None, left_faction=None):
        """
        Send inactivity alert embed to the channel
        ALWAYS posts to inactivity-alerts channel

        Args:
            channel: Discord channel to send to (always inactivity-alerts)
            warning_members: List of members inactive for 3+ days
            critical_members: List of members inactive for 5+ days
            total_faction_members: Total number of faction members checked
            oc_warning: List of members with no OC for 2+ days
            oc_critical: List of members with no OC for 4+ days
            left_faction: List of verified Discord members gone from faction 7+ days
        """
        if oc_warning is None:
            oc_warning = []
        if oc_critical is None:
            oc_critical = []
        if left_faction is None:
            left_faction = []

        has_inactivity = bool(warning_members or critical_members)
        has_oc_issues = bool(oc_warning or oc_critical)
        has_left = bool(left_faction)

        # Only ping management if there's something to report
        if MANAGEMENT_PING_ROLE_ID and (has_inactivity or has_oc_issues or has_left):
            await channel.send(f'<@&{MANAGEMENT_PING_ROLE_ID}>')

        # If everyone is active on both checks, post a positive summary
        if not warning_members and not critical_members and not oc_warning and not oc_critical and not left_faction:
            active_embed = create_embed(
                title='✅ Daily Inactivity Check',
                description=f'All **{total_faction_members}** faction members are active!\n\nNo inactivity, OC, or left-faction issues found.',
                color=EMBED_COLOR_SUCCESS
            )
            await channel.send(embed=active_embed)
            logger.info(f'Posted daily check - all {total_faction_members} members active')
            return

        # Send critical alerts
        if critical_members:
            lines = []
            for data in critical_members:
                discord_member = data['discord_member']
                torn_name = data['torn_name']
                torn_id = data['torn_id']
                torn_level = data['torn_level']
                days = data['days']
                last_seen = data['last_seen']
                discord_note = f' (Discord: {discord_member.display_name})' if discord_member else ''
                lines.append(
                    f'**{torn_name}** [Lvl {torn_level}]{discord_note}\n'
                    f'  {days} days inactive · Last action: {format_timestamp(last_seen, "R")}'
                )

            # Chunk into multiple embeds if needed (4096 char description limit)
            header = f'**{len(critical_members)} faction member(s)** have been inactive on Torn for {INACTIVITY_CRITICAL_DAYS}+ days:\n\n'
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n\n'
            if current:
                chunks.append(current)

            for i, chunk in enumerate(chunks):
                title = '🔴 Critical Inactivity Alert (Torn)' if i == 0 else '🔴 Critical (continued)'
                embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_CRITICAL)
                await channel.send(embed=embed)
            logger.info(f'Sent critical alert for {len(critical_members)} members')

        # Send warning alerts
        if warning_members:
            lines = []
            for data in warning_members:
                discord_member = data['discord_member']
                torn_name = data['torn_name']
                torn_id = data['torn_id']
                torn_level = data['torn_level']
                days = data['days']
                last_seen = data['last_seen']
                discord_note = f' (Discord: {discord_member.display_name})' if discord_member else ''
                lines.append(
                    f'**{torn_name}** [Lvl {torn_level}]{discord_note}\n'
                    f'  {days} days inactive · Last action: {format_timestamp(last_seen, "R")}'
                )

            header = f'**{len(warning_members)} faction member(s)** have been inactive on Torn for {INACTIVITY_WARNING_DAYS}+ days:\n\n'
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n\n'
            if current:
                chunks.append(current)

            for i, chunk in enumerate(chunks):
                title = '⚠️ Inactivity Warning (Torn)' if i == 0 else '⚠️ Warning (continued)'
                embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_WARNING)
                await channel.send(embed=embed)
            logger.info(f'Sent warning alert for {len(warning_members)} members')

        # ── OC Participation alerts ──────────────────────────────────────────

        if oc_critical:
            lines = []
            for data in oc_critical:
                last_ts = data['last_oc_ts']
                if last_ts:
                    last_str = format_timestamp(int(last_ts), 'R')
                else:
                    last_str = 'Never'
                lines.append(
                    f'**{data["torn_name"]}** [Lvl {data["torn_level"]}]\n'
                    f'  {data["days"]:.0f} days without OC · Last OC: {last_str}'
                )

            header = f'**{len(oc_critical)} member(s)** have not participated in an OC for {OC_CRITICAL_DAYS}+ days:\n\n'
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n\n'
            if current:
                chunks.append(current)

            for i, chunk in enumerate(chunks):
                title = '🔴 OC Participation Critical' if i == 0 else '🔴 OC Critical (continued)'
                embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_CRITICAL)
                await channel.send(embed=embed)
            logger.info(f'Sent OC critical alert for {len(oc_critical)} members')

        if oc_warning:
            lines = []
            for data in oc_warning:
                last_ts = data['last_oc_ts']
                if last_ts:
                    last_str = format_timestamp(int(last_ts), 'R')
                else:
                    last_str = 'Never'
                lines.append(
                    f'**{data["torn_name"]}** [Lvl {data["torn_level"]}]\n'
                    f'  {data["days"]:.0f} days without OC · Last OC: {last_str}'
                )

            header = f'**{len(oc_warning)} member(s)** have not participated in an OC for {OC_WARNING_DAYS}+ days:\n\n'
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n\n'
            if current:
                chunks.append(current)

            for i, chunk in enumerate(chunks):
                title = '⚠️ OC Participation Warning' if i == 0 else '⚠️ OC Warning (continued)'
                embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_WARNING)
                await channel.send(embed=embed)
            logger.info(f'Sent OC warning alert for {len(oc_warning)} members')

        # ── Left-faction alerts ──────────────────────────────────────────────
        if left_faction:
            guild = self.bot.get_guild(GUILD_ID)
            lines = []
            for data in left_faction:
                discord_id = data['discord_id']
                torn_name = data['torn_name']
                days_gone = data['days_gone']
                # Try to mention the Discord member
                discord_member = guild.get_member(int(discord_id)) if guild else None
                mention = discord_member.mention if discord_member else f'(ID:{discord_id})'
                lines.append(f'**{torn_name}** {mention} — {days_gone:.0f}d since leaving faction')

            header = f'**{len(left_faction)} verified Discord member(s)** have not returned to the faction in {LEFT_FACTION_ALERT_DAYS}+ days:\n\n'
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n'
            if current:
                chunks.append(current)

            for i, chunk in enumerate(chunks):
                title = '👋 Left Faction Alert' if i == 0 else '👋 Left Faction (continued)'
                embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_WARNING)
                await channel.send(embed=embed)
            logger.info(f'Sent left-faction alert for {len(left_faction)} members')

    def get_all_mappings(self):
        """
        Get all Discord to Torn ID mappings

        Returns:
            Dict of {discord_id: torn_id}
        """
        return self.user_mapping.copy()

    def get_registered_count(self):
        """Get count of registered users"""
        return len(self.user_mapping)
