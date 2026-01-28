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

import discord
from datetime import datetime
from utils.helpers import load_json, save_json, create_embed, format_timestamp, get_days_since, get_current_timestamp
from utils.torn_api import fetch_faction_members
from config import (
    INACTIVITY_ALERTS_CHANNEL_ID,
    INACTIVITY_WARNING_DAYS,
    INACTIVITY_CRITICAL_DAYS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
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

        # Fetch Torn faction member data
        torn_members = await fetch_faction_members()

        if not torn_members:
            print('Error: Could not fetch Torn faction data')
            # Post error to inactivity-alerts channel (not announcements)
            error_embed = create_embed(
                title='Inactivity Check Failed',
                description='Could not connect to Torn API to check member activity. Will retry tomorrow.',
                color=EMBED_COLOR_CRITICAL
            )
            await channel.send(embed=error_embed)
            return

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

        # Send alerts if there are inactive members
        total_inactive = len(warning_members) + len(critical_members)
        total_checked = len(torn_members)

        if warning_members or critical_members:
            print(f'Found {total_inactive} inactive members out of {total_checked} total')
            await self._send_inactivity_alert(channel, warning_members, critical_members, total_checked)
        else:
            # Don't spam channel when everyone is active - just log it
            print(f'All {total_checked} faction members are active! (no alert needed)')

    async def _send_inactivity_alert(self, channel, warning_members, critical_members, total_faction_members):
        """
        Send inactivity alert embed to the channel
        ALWAYS posts to inactivity-alerts channel

        Args:
            channel: Discord channel to send to (always inactivity-alerts)
            warning_members: List of members inactive for 3+ days
            critical_members: List of members inactive for 5+ days
            total_faction_members: Total number of faction members checked
        """
        # Ping management role if configured
        if MANAGEMENT_PING_ROLE_ID:
            await channel.send(f'<@&{MANAGEMENT_PING_ROLE_ID}>')

        # Send critical alerts
        if critical_members:
            critical_embed = create_embed(
                title='🔴 Critical Inactivity Alert (Torn)',
                description=f'**{len(critical_members)} faction member(s)** have been inactive on Torn for {INACTIVITY_CRITICAL_DAYS}+ days:',
                color=EMBED_COLOR_CRITICAL
            )

            for data in critical_members:
                discord_member = data['discord_member']
                torn_name = data['torn_name']
                torn_id = data['torn_id']
                torn_level = data['torn_level']
                days = data['days']
                last_seen = data['last_seen']

                # Show Torn name (no @mention to avoid pinging)
                field_name = f'{torn_name} [Lvl {torn_level}]'

                # Add Discord name if registered (without pinging)
                discord_note = f'\nDiscord: {discord_member.display_name}' if discord_member else ''

                critical_embed.add_field(
                    name=field_name,
                    value=f'**{days} days** inactive on Torn\nLast action: {format_timestamp(last_seen, "R")}{discord_note}\nProfile: https://www.torn.com/profiles.php?XID={torn_id}',
                    inline=False
                )

            await channel.send(embed=critical_embed)
            print(f'Sent critical alert for {len(critical_members)} members')

        # Send warning alerts
        if warning_members:
            warning_embed = create_embed(
                title='⚠️ Inactivity Warning (Torn)',
                description=f'**{len(warning_members)} faction member(s)** have been inactive on Torn for {INACTIVITY_WARNING_DAYS}+ days:',
                color=EMBED_COLOR_WARNING
            )

            for data in warning_members:
                discord_member = data['discord_member']
                torn_name = data['torn_name']
                torn_id = data['torn_id']
                torn_level = data['torn_level']
                days = data['days']
                last_seen = data['last_seen']

                # Show Torn name (no @mention to avoid pinging)
                field_name = f'{torn_name} [Lvl {torn_level}]'

                # Add Discord name if registered (without pinging)
                discord_note = f'\nDiscord: {discord_member.display_name}' if discord_member else ''

                warning_embed.add_field(
                    name=field_name,
                    value=f'**{days} days** inactive on Torn\nLast action: {format_timestamp(last_seen, "R")}{discord_note}\nProfile: https://www.torn.com/profiles.php?XID={torn_id}',
                    inline=False
                )

            await channel.send(embed=warning_embed)
            print(f'Sent warning alert for {len(warning_members)} members')

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
