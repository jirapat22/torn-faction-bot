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
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    EMBED_COLOR_SUCCESS,
    GUILD_ID,
    MANAGEMENT_PING_ROLE_ID
)


class InactivityTracker:
    """Tracks member activity on Torn and sends inactivity alerts"""

    def __init__(self, bot):
        self.bot = bot
        self.user_mapping = {}  # {discord_id: torn_id}
        self.mapping_file = 'data/discord_to_torn.json'

    async def load_user_mapping(self):
        """Load Discord to Torn ID mapping from JSON file"""
        self.user_mapping = load_json(self.mapping_file, default={})
        print(f'Loaded Torn ID mappings for {len(self.user_mapping)} Discord users')

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

        A member is considered active if they are currently assigned to an
        in-progress OC (time_completed == 0). Otherwise their last completion
        timestamp is used to calculate days since last OC.

        Members in the faction fewer than OC_NEW_MEMBER_GRACE_DAYS are skipped.

        Returns:
            (oc_warning, oc_critical) — each a list of member dicts.
        """
        now = time.time()

        # Build: last completed OC timestamp per torn_id (active/in-progress OCs are ignored)
        last_completion = {}  # {torn_id_str: float timestamp}

        for crime_data in crimes.values():
            time_completed = crime_data.get('time_completed', 0)
            if not time_completed:
                continue  # skip active/in-progress crimes

            participants = crime_data.get('participants', [])

            # participants can be a list of {torn_id: {description}} dicts
            # or occasionally a plain dict {torn_id: {description}}
            if isinstance(participants, dict):
                participants = [{tid: pdata} for tid, pdata in participants.items()]

            for participant_obj in participants:
                if not isinstance(participant_obj, dict):
                    continue
                for torn_id, _pdata in participant_obj.items():
                    tid = str(torn_id)
                    tc = float(time_completed)
                    if tid not in last_completion or tc > last_completion[tid]:
                        last_completion[tid] = tc

        oc_warning = []
        oc_critical = []

        for torn_id, member in torn_members.items():
            tid = str(torn_id)
            days_in_faction = member.get('days_in_faction', 0)

            # Grace period for new members
            if days_in_faction < OC_NEW_MEMBER_GRACE_DAYS:
                continue

            last_oc_ts = last_completion.get(tid)
            if last_oc_ts is not None:
                days_since = (now - last_oc_ts) / 86400
            else:
                # Never completed an OC — use days_in_faction as the measure
                days_since = float(days_in_faction)

            if days_since < OC_WARNING_DAYS:
                continue

            member_data = {
                'torn_name': member.get('name', f'ID:{tid}'),
                'torn_id': tid,
                'torn_level': member.get('level', '?'),
                'days': round(days_since, 1),
                'last_oc_ts': last_oc_ts,  # None if never done one
            }

            if days_since >= OC_CRITICAL_DAYS:
                oc_critical.append(member_data)
            else:
                oc_warning.append(member_data)

        return oc_warning, oc_critical

    async def check_and_alert_inactive(self):
        """
        Check for inactive members on Torn and send alerts
        NOW CHECKS ALL FACTION MEMBERS (registered or not)
        Called by the daily background task
        """
        print(f'Running daily Torn inactivity check... ({datetime.now()})')

        # Get the alerts channel - ALWAYS use inactivity-alerts channel
        channel = self.bot.get_channel(INACTIVITY_ALERTS_CHANNEL_ID)

        if not channel:
            print(f'ERROR: Could not find inactivity alerts channel (ID: {INACTIVITY_ALERTS_CHANNEL_ID})')
            print('Please check INACTIVITY_ALERTS_CHANNEL_ID in config.py')
            return

        # Get the guild
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            print(f'Warning: Could not find guild (ID: {GUILD_ID})')
            # Still continue - we can check faction members without Discord

        # Fetch Torn faction member data and crimes in parallel
        import asyncio
        torn_members, crimes = await asyncio.gather(
            fetch_faction_members(),
            fetch_faction_crimes()
        )

        if not torn_members:
            print('Error: Could not fetch Torn faction data')
            error_embed = create_embed(
                title='Inactivity Check Failed',
                description='Could not connect to Torn API to check member activity. Will retry tomorrow.',
                color=EMBED_COLOR_CRITICAL
            )
            await channel.send(embed=error_embed)
            return

        if crimes is None:
            crimes = {}

        # Track inactive members
        warning_members = []  # 3+ days inactive
        critical_members = []  # 5+ days inactive

        # Check ALL faction members from Torn API
        print(f'Checking {len(torn_members)} faction members...')
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
        oc_warning, oc_critical = self._get_oc_status(torn_members, crimes)
        print(f'OC check: {len(oc_warning)} warning, {len(oc_critical)} critical out of {len(torn_members)} total')

        # Always post daily summary
        total_inactive = len(warning_members) + len(critical_members)
        total_checked = len(torn_members)

        print(f'Inactivity check: {total_inactive} inactive out of {total_checked} total')
        await self._send_inactivity_alert(channel, warning_members, critical_members, total_checked, oc_warning, oc_critical)

    async def _send_inactivity_alert(self, channel, warning_members, critical_members, total_faction_members, oc_warning=None, oc_critical=None):
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
        """
        if oc_warning is None:
            oc_warning = []
        if oc_critical is None:
            oc_critical = []

        has_inactivity = bool(warning_members or critical_members)
        has_oc_issues = bool(oc_warning or oc_critical)

        # Only ping management if there's something to report
        if MANAGEMENT_PING_ROLE_ID and (has_inactivity or has_oc_issues):
            await channel.send(f'<@&{MANAGEMENT_PING_ROLE_ID}>')

        # If everyone is active on both checks, post a positive summary
        if not warning_members and not critical_members and not oc_warning and not oc_critical:
            active_embed = create_embed(
                title='✅ Daily Inactivity Check',
                description=f'All **{total_faction_members}** faction members are active!\n\nNo inactivity or OC issues found.',
                color=EMBED_COLOR_SUCCESS
            )
            await channel.send(embed=active_embed)
            print(f'Posted daily check - all {total_faction_members} members active')
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
            print(f'Sent critical alert for {len(critical_members)} members')

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
            print(f'Sent warning alert for {len(warning_members)} members')

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
            print(f'Sent OC critical alert for {len(oc_critical)} members')

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
            print(f'Sent OC warning alert for {len(oc_warning)} members')

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
