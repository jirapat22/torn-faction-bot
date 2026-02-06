"""
Alliance War Tracking Module
Track wars for allied factions and post announcements.

Features:
- Track multiple ally faction wars
- Announce when war is scheduled (with countdown)
- Announce when war starts
- Announce when war ends (win/loss)

Commands:
- /alliance add <faction_id> [name] - Add ally faction to track
- /alliance remove <faction_id> - Remove ally
- /alliance list - Show tracked allies
- /alliance setchannel - Set the channel for alerts
"""

import discord
from discord import app_commands
from datetime import datetime, timezone
from typing import Optional, Dict, List

from utils.helpers import load_json, save_json, create_embed
from config import (
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    MANAGEMENT_ROLE_IDS,
    TORN_PERSONAL_API_KEY,
    GUILD_ID
)

import aiohttp


class AllianceManager:
    """Manages alliance faction war tracking"""

    def __init__(self, bot):
        self.bot = bot
        self.data_file = 'data/alliance.json'
        self.allies = {}  # {faction_id: {'name': name, 'added_by': discord_id}}
        self.channel_id = 0  # Channel to post alerts
        self.active_wars = {}  # {faction_id: war_data} - track ongoing wars
        self.announced_wars = set()  # War IDs that have been announced (start)
        self.announced_scheduled = set()  # War IDs announced as scheduled

    async def load_data(self):
        """Load alliance data from JSON"""
        # Default channel: alliance-monitor-beta
        DEFAULT_ALLIANCE_CHANNEL = 1468903679040950427

        data = load_json(self.data_file, default={
            'allies': {},
            'channel_id': DEFAULT_ALLIANCE_CHANNEL,
            'active_wars': {},
            'announced_wars': [],
            'announced_scheduled': []
        })
        self.allies = data.get('allies', {})
        self.channel_id = data.get('channel_id', DEFAULT_ALLIANCE_CHANNEL) or DEFAULT_ALLIANCE_CHANNEL
        self.active_wars = data.get('active_wars', {})
        self.announced_wars = set(data.get('announced_wars', []))
        self.announced_scheduled = set(data.get('announced_scheduled', []))
        print(f'Loaded {len(self.allies)} alliance factions to track')

    async def save_data(self):
        """Save alliance data to JSON"""
        save_json(self.data_file, {
            'allies': self.allies,
            'channel_id': self.channel_id,
            'active_wars': self.active_wars,
            'announced_wars': list(self.announced_wars),
            'announced_scheduled': list(self.announced_scheduled)
        })

    async def add_ally(self, faction_id: str, name: str = None, added_by: int = None) -> tuple:
        """
        Add an ally faction to track

        Returns:
            (success: bool, message: str)
        """
        faction_id = str(faction_id).strip()

        # Validate faction_id is numeric
        if not faction_id.isdigit():
            return False, f'Invalid faction ID: must be a number (got: {faction_id})'

        if faction_id in self.allies:
            return False, f'Faction {faction_id} is already being tracked.'

        # Fetch faction info to validate and get name
        faction_data = await self._fetch_faction_info(faction_id)
        if not faction_data:
            return False, 'Could not find faction. Check the faction ID.'

        faction_name = faction_data.get('name', name or f'Faction {faction_id}')

        self.allies[faction_id] = {
            'name': faction_name,
            'added_by': added_by
        }

        await self.save_data()
        return True, f'Now tracking **{faction_name}** [{faction_id}] for war alerts.'

    async def remove_ally(self, faction_id: str) -> tuple:
        """
        Remove an ally faction

        Returns:
            (success: bool, message: str)
        """
        faction_id = str(faction_id).strip()

        if faction_id not in self.allies:
            return False, 'This faction is not being tracked.'

        name = self.allies[faction_id].get('name', faction_id)
        del self.allies[faction_id]

        # Clean up any active war tracking
        if faction_id in self.active_wars:
            del self.active_wars[faction_id]

        await self.save_data()
        return True, f'Stopped tracking **{name}** [{faction_id}].'

    def set_channel(self, channel_id: int):
        """Set the channel for alliance alerts"""
        self.channel_id = channel_id

    def get_allies(self) -> List[Dict]:
        """Get list of tracked allies"""
        return [
            {'faction_id': fid, **data}
            for fid, data in self.allies.items()
        ]

    def get_ally_count(self) -> int:
        """Get number of tracked allies"""
        return len(self.allies)

    async def _fetch_faction_info(self, faction_id: str) -> Optional[Dict]:
        """Fetch faction info from Torn API"""
        try:
            if not TORN_PERSONAL_API_KEY:
                print('Alliance: TORN_PERSONAL_API_KEY not set')
                return None

            url = f'https://api.torn.com/faction/{faction_id}?selections=basic&key={TORN_PERSONAL_API_KEY}'

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None

                    data = await response.json()

                    if 'error' in data:
                        print(f'Alliance API error: {data["error"]}')
                        return None

                    return data

        except Exception as e:
            print(f'Alliance: Error fetching faction {faction_id}: {e}')
            return None

    async def _fetch_faction_wars(self, faction_id: str) -> Optional[Dict]:
        """Fetch faction war data from Torn API v2"""
        try:
            if not TORN_PERSONAL_API_KEY:
                print('Alliance: TORN_PERSONAL_API_KEY not set')
                return None

            # Use API v2 - wars selection requires v2
            url = f'https://api.torn.com/v2/faction/{faction_id}?selections=basic,wars&key={TORN_PERSONAL_API_KEY}'

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        print(f'Alliance: API returned status {response.status} for faction {faction_id}')
                        return None

                    data = await response.json()

                    if 'error' in data:
                        error_info = data['error']
                        print(f'Alliance: API error for faction {faction_id}: {error_info}')
                        return None

                    return data

        except Exception as e:
            print(f'Alliance: Error fetching wars for {faction_id}: {e}')
            return None

    async def check_ally_wars(self):
        """
        Check all ally factions for war updates.
        Called by background task.
        """
        if not self.allies:
            return

        if not self.channel_id:
            return

        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            print(f'Alliance: Could not find channel {self.channel_id}')
            return

        for faction_id, ally_info in self.allies.items():
            ally_name = ally_info.get('name', f'Faction {faction_id}')

            try:
                faction_data = await self._fetch_faction_wars(faction_id)
                if not faction_data:
                    print(f'Alliance: No data returned for {ally_name} [{faction_id}]')
                    continue

                wars = faction_data.get('wars', {})

                # Debug: log the structure we received
                print(f'Alliance: {ally_name} wars data type: {type(wars)}, keys: {wars.keys() if isinstance(wars, dict) else "N/A"}')

                # Handle empty wars dict - war may have ended
                if not wars:
                    if faction_id in self.active_wars:
                        await self._handle_war_ended(channel, faction_id, ally_name)
                    continue

                # API v2 structure: wars.ranked is a single war object (not dict of wars)
                # Structure: {ranked: {war_id, start, end, target, winner, factions}, raids: {...}, territory: {...}}
                if isinstance(wars, dict):
                    ranked_war = wars.get('ranked')
                    if ranked_war and isinstance(ranked_war, dict):
                        # v2: ranked is a single war object with war_id field
                        war_id = str(ranked_war.get('war_id', 'unknown'))
                        if war_id != 'unknown' and war_id != '0':
                            await self._process_war_v2(channel, faction_id, ally_name, war_id, ranked_war)
                elif isinstance(wars, list):
                    # Handle list format if API ever returns that
                    for war_item in wars:
                        if isinstance(war_item, dict):
                            war_id = str(war_item.get('id', war_item.get('war_id', 'unknown')))
                            await self._process_war_v2(channel, faction_id, ally_name, war_id, war_item)

            except Exception as e:
                print(f'Alliance: Error checking wars for {ally_name}: {e}')
                import traceback
                traceback.print_exc()

        await self.save_data()

    async def _process_war_v2(self, channel, faction_id: str, ally_name: str, war_id: str, war_item: Dict):
        """Process a single war from API v2 format"""
        # v2 format has different structure - war data is directly in the item
        start_time = war_item.get('start', 0)
        end_time = war_item.get('end', 0)
        now = datetime.now(timezone.utc).timestamp()

        print(f'Alliance v2: Processing war {war_id} for {ally_name} - start: {start_time}, end: {end_time}, now: {now}')

        # Get enemy faction info from v2 structure
        factions = war_item.get('factions', [])
        enemy_name = 'Unknown Faction'
        enemy_id = None

        # v2 factions might be a list
        if isinstance(factions, list):
            for fdata in factions:
                fid = str(fdata.get('id', ''))
                if fid != str(faction_id):
                    enemy_name = fdata.get('name', f'Faction {fid}')
                    enemy_id = fid
                    break
        elif isinstance(factions, dict):
            for fid, fdata in factions.items():
                if str(fid) != str(faction_id):
                    enemy_name = fdata.get('name', f'Faction {fid}') if isinstance(fdata, dict) else f'Faction {fid}'
                    enemy_id = fid
                    break

        # War hasn't started yet - announce scheduled
        if start_time > now:
            if war_id not in self.announced_scheduled:
                hours_until = (start_time - now) / 3600
                await self._announce_war_scheduled(channel, ally_name, enemy_name, start_time, hours_until)
                self.announced_scheduled.add(war_id)

        # War is active (started but not ended)
        # end_time can be None or 0 for ongoing wars
        elif start_time <= now and (end_time is None or end_time == 0 or now < end_time):
            if war_id not in self.announced_wars:
                await self._announce_war_started(channel, ally_name, enemy_name)
                self.announced_wars.add(war_id)

            self.active_wars[faction_id] = {
                'war_id': war_id,
                'enemy_name': enemy_name,
                'enemy_id': enemy_id,
                'start_time': start_time,
                'factions': factions
            }

        # War ended (only if end_time is set and has passed)
        elif end_time and end_time > 0 and now >= end_time:
            if faction_id in self.active_wars:
                await self._handle_war_ended(channel, faction_id, ally_name, war_item)

    async def _process_war(self, channel, faction_id: str, ally_name: str, war_id: str, war_data: Dict):
        """Process a single war and send appropriate announcements (v1 format)"""
        war = war_data.get('war', {})
        start_time = war.get('start', 0)
        end_time = war.get('end', 0)
        now = datetime.now(timezone.utc).timestamp()

        # Debug logging
        print(f'Alliance v1: Processing war {war_id} for {ally_name} - start: {start_time}, end: {end_time}, now: {now}')

        # Get enemy faction info
        factions = war_data.get('factions', {})
        enemy_name = 'Unknown Faction'
        enemy_id = None

        for fid, fdata in factions.items():
            if str(fid) != str(faction_id):
                enemy_name = fdata.get('name', f'Faction {fid}')
                enemy_id = fid
                break

        # War hasn't started yet - announce scheduled
        if start_time > now:
            if war_id not in self.announced_scheduled:
                hours_until = (start_time - now) / 3600
                await self._announce_war_scheduled(channel, ally_name, enemy_name, start_time, hours_until)
                self.announced_scheduled.add(war_id)

        # War is active (started but not ended)
        # end_time can be None or 0 for ongoing wars
        elif start_time <= now and (end_time is None or end_time == 0 or now < end_time):
            # Announce war start
            if war_id not in self.announced_wars:
                await self._announce_war_started(channel, ally_name, enemy_name)
                self.announced_wars.add(war_id)

            # Track active war for end detection
            self.active_wars[faction_id] = {
                'war_id': war_id,
                'enemy_name': enemy_name,
                'enemy_id': enemy_id,
                'start_time': start_time,
                'factions': factions
            }

        # War ended (only if end_time is set and has passed)
        elif end_time and end_time > 0 and now >= end_time:
            if faction_id in self.active_wars:
                await self._handle_war_ended(channel, faction_id, ally_name, war_data)

    async def _announce_war_scheduled(self, channel, ally_name: str, enemy_name: str, start_time: int, hours_until: float):
        """Announce a scheduled war"""
        start_dt = datetime.fromtimestamp(start_time, tz=timezone.utc)
        date_str = start_dt.strftime('%H:%M %d-%m-%Y TCT')

        embed = create_embed(
            title='⚔️ Alliance War Scheduled',
            description=(
                f"**{ally_name}** is starting a war with **{enemy_name}**\n\n"
                f"**Starts in:** {hours_until:.1f} hours\n"
                f"**Start time:** {date_str}\n"
                f"**Countdown:** <t:{int(start_time)}:R>"
            ),
            color=EMBED_COLOR_WARNING
        )

        await channel.send(embed=embed)
        print(f'Alliance: Announced scheduled war - {ally_name} vs {enemy_name}')

    async def _announce_war_started(self, channel, ally_name: str, enemy_name: str):
        """Announce a war has started"""
        embed = create_embed(
            title='🔥 Alliance War Started!',
            description=f"**{ally_name}**'s war with **{enemy_name}** has begun!",
            color=EMBED_COLOR_CRITICAL
        )

        await channel.send(embed=embed)
        print(f'Alliance: Announced war start - {ally_name} vs {enemy_name}')

    async def _handle_war_ended(self, channel, faction_id: str, ally_name: str, war_data: Dict = None):
        """Handle war end and announce result"""
        active_war = self.active_wars.get(faction_id)
        if not active_war:
            return

        enemy_name = active_war.get('enemy_name', 'Unknown')
        war_id = active_war.get('war_id')

        # Determine winner if we have the war data
        result = 'ended'
        if war_data:
            factions = war_data.get('factions', {})
            ally_score = 0
            enemy_score = 0

            for fid, fdata in factions.items():
                score = fdata.get('score', 0)
                if str(fid) == str(faction_id):
                    ally_score = score
                else:
                    enemy_score = score

            if ally_score > enemy_score:
                result = 'won'
                color = EMBED_COLOR_SUCCESS
                title = '🏆 Alliance War Victory!'
                desc = f"**{ally_name}** won against **{enemy_name}**!\n\nScore: **{ally_score}** - {enemy_score}"
            elif enemy_score > ally_score:
                result = 'lost'
                color = EMBED_COLOR_CRITICAL
                title = '💀 Alliance War Defeat'
                desc = f"**{ally_name}** lost to **{enemy_name}**\n\nScore: {ally_score} - **{enemy_score}**"
            else:
                color = EMBED_COLOR_INFO
                title = '🤝 Alliance War Draw'
                desc = f"**{ally_name}** drew with **{enemy_name}**\n\nScore: {ally_score} - {enemy_score}"
        else:
            color = EMBED_COLOR_INFO
            title = '⚔️ Alliance War Ended'
            desc = f"**{ally_name}**'s war with **{enemy_name}** has ended."

        embed = create_embed(title=title, description=desc, color=color)
        await channel.send(embed=embed)

        # Cleanup
        del self.active_wars[faction_id]
        if war_id:
            self.announced_wars.discard(war_id)
            self.announced_scheduled.discard(war_id)

        print(f'Alliance: Announced war {result} - {ally_name} vs {enemy_name}')


async def setup_alliance_commands(bot, manager: AllianceManager):
    """Register alliance slash commands"""

    def is_management(interaction: discord.Interaction) -> bool:
        """Check if user has management permissions"""
        if interaction.user.guild_permissions.manage_guild:
            return True
        if MANAGEMENT_ROLE_IDS:
            user_role_ids = [role.id for role in interaction.user.roles]
            if any(role_id in MANAGEMENT_ROLE_IDS for role_id in user_role_ids):
                return True
        return False

    alliance_group = app_commands.Group(name='alliance', description='Track ally faction wars')

    @alliance_group.command(name='add', description='[Management] Add an ally faction to track')
    @app_commands.describe(
        faction_id='Torn faction ID to track',
        name='Optional custom name for the faction'
    )
    async def alliance_add(interaction: discord.Interaction, faction_id: str, name: str = None):
        """Add an ally faction"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        success, message = await manager.add_ally(
            faction_id=faction_id,
            name=name,
            added_by=interaction.user.id
        )

        if success:
            embed = create_embed(
                title='Alliance Added',
                description=message,
                color=EMBED_COLOR_SUCCESS
            )
            if not manager.channel_id:
                embed.add_field(
                    name='⚠️ No Channel Set',
                    value='Use `/alliance setchannel` to set where alerts are posted.',
                    inline=False
                )
        else:
            embed = create_embed(
                title='Failed to Add',
                description=message,
                color=EMBED_COLOR_WARNING
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @alliance_group.command(name='remove', description='[Management] Stop tracking an ally faction')
    @app_commands.describe(faction_id='Torn faction ID to stop tracking')
    async def alliance_remove(interaction: discord.Interaction, faction_id: str):
        """Remove an ally faction"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        success, message = await manager.remove_ally(faction_id)

        if success:
            embed = create_embed(title='Alliance Removed', description=message, color=EMBED_COLOR_SUCCESS)
        else:
            embed = create_embed(title='Not Found', description=message, color=EMBED_COLOR_WARNING)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @alliance_group.command(name='list', description='Show all tracked ally factions')
    async def alliance_list(interaction: discord.Interaction):
        """List tracked allies"""
        allies = manager.get_allies()

        if not allies:
            embed = create_embed(
                title='No Allies Tracked',
                description='No ally factions are being tracked.\n\nUse `/alliance add <faction_id>` to add one.',
                color=EMBED_COLOR_INFO
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        ally_list = '\n'.join([
            f"• **{a['name']}** [{a['faction_id']}]"
            for a in allies
        ])

        embed = create_embed(
            title=f'Tracked Allies ({len(allies)})',
            description=ally_list,
            color=EMBED_COLOR_INFO
        )

        if manager.channel_id:
            embed.add_field(
                name='Alert Channel',
                value=f'<#{manager.channel_id}>',
                inline=False
            )
        else:
            embed.add_field(
                name='⚠️ No Channel Set',
                value='Use `/alliance setchannel` to set where alerts are posted.',
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @alliance_group.command(name='setchannel', description='[Management] Set the channel for alliance war alerts')
    @app_commands.describe(channel='Channel to post alliance war alerts')
    async def alliance_setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
        """Set alert channel"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        manager.set_channel(channel.id)
        await manager.save_data()

        embed = create_embed(
            title='Channel Set',
            description=f'Alliance war alerts will be posted to {channel.mention}',
            color=EMBED_COLOR_SUCCESS
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @alliance_group.command(name='check', description='[Management] Force check alliance wars and show debug info')
    async def alliance_check(interaction: discord.Interaction):
        """Force check and show debug info"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if not manager.allies:
            embed = create_embed(
                title='No Allies',
                description='No ally factions are being tracked.',
                color=EMBED_COLOR_INFO
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        debug_lines = []
        debug_lines.append(f'**Channel ID:** {manager.channel_id}')
        debug_lines.append(f'**Announced Wars:** {list(manager.announced_wars)}')
        debug_lines.append(f'**Announced Scheduled:** {list(manager.announced_scheduled)}')
        debug_lines.append(f'**Active Wars:** {list(manager.active_wars.keys())}')
        debug_lines.append('')

        for faction_id, ally_info in manager.allies.items():
            ally_name = ally_info.get('name', f'Faction {faction_id}')
            debug_lines.append(f'**Checking {ally_name} [{faction_id}]:**')

            faction_data = await manager._fetch_faction_wars(faction_id)
            if not faction_data:
                debug_lines.append('  - ❌ No data returned from API')
                continue

            wars = faction_data.get('wars', {})
            if not wars:
                debug_lines.append('  - No active wars')
                continue

            debug_lines.append(f'  - Wars type: {type(wars).__name__}')

            if isinstance(wars, dict):
                debug_lines.append(f'  - Wars keys: {list(wars.keys())}')
                ranked_war = wars.get('ranked')

                if ranked_war and isinstance(ranked_war, dict):
                    war_id = str(ranked_war.get('war_id', 'unknown'))
                    start = ranked_war.get('start', 0)
                    end = ranked_war.get('end', 0)
                    now = datetime.now(timezone.utc).timestamp()

                    # Get enemy from factions (v2 format - list of factions)
                    factions = ranked_war.get('factions', [])
                    enemy_name = 'Unknown'
                    if isinstance(factions, list):
                        for fdata in factions:
                            if isinstance(fdata, dict):
                                fid = str(fdata.get('id', ''))
                                if fid != str(faction_id):
                                    enemy_name = fdata.get('name', f'Faction {fid}')
                                    break

                    if war_id != 'unknown' and war_id != '0':
                        # end can be None or 0 for ongoing wars
                        if start > now:
                            status = 'scheduled'
                        elif end is None or end == 0 or now < end:
                            status = 'active'
                        else:
                            status = 'ended'
                        announced_start = '✓' if war_id in manager.announced_wars else '✗'
                        announced_sched = '✓' if war_id in manager.announced_scheduled else '✗'
                        debug_lines.append(f'  - **Ranked War {war_id}:** vs {enemy_name}')
                        debug_lines.append(f'    Status: {status} | Start announced: {announced_start} | Scheduled announced: {announced_sched}')
                        debug_lines.append(f'    Start: {start} | End: {end} | Now: {now:.0f}')
                    else:
                        debug_lines.append(f'  - No active ranked war (war_id={war_id})')
                else:
                    debug_lines.append(f'  - No ranked war data')
            else:
                debug_lines.append(f'  - Unexpected wars format: {type(wars)}')

        debug_text = '\n'.join(debug_lines)
        if len(debug_text) > 4000:
            debug_text = debug_text[:4000] + '\n... (truncated)'

        embed = create_embed(
            title='Alliance Debug Info',
            description=debug_text,
            color=EMBED_COLOR_INFO
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # Add group to bot
    bot.tree.add_command(alliance_group)
    print('Alliance commands registered (/alliance add, remove, list, setchannel, check)')
