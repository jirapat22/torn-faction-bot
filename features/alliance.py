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

from utils.helpers import load_json, save_json, create_embed, is_management, format_time_dual
from utils.torn_api import fetch_any_faction_data
from config import (
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    GUILD_ID,
    ALLIANCE_CHANNEL_ID,
)


import logging

logger = logging.getLogger(__name__)

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
        self.announced_24h = set()  # War IDs that have had 24-hour reminder

    async def load_data(self):
        """Load alliance data from JSON"""
        data = load_json(self.data_file, default={
            'allies': {},
            'channel_id': ALLIANCE_CHANNEL_ID,
            'active_wars': {},
            'announced_wars': [],
            'announced_scheduled': [],
            'announced_24h': []
        })
        self.allies = data.get('allies', {})
        # Stored channel wins; fall back to env-configured default if unset.
        self.channel_id = data.get('channel_id') or ALLIANCE_CHANNEL_ID
        self.active_wars = data.get('active_wars', {})
        self.announced_wars = set(data.get('announced_wars', []))
        self.announced_scheduled = set(data.get('announced_scheduled', []))
        self.announced_24h = set(data.get('announced_24h', []))
        logger.info(f'Loaded {len(self.allies)} alliance factions to track')

    async def save_data(self):
        """Save alliance data to JSON"""
        save_json(self.data_file, {
            'allies': self.allies,
            'channel_id': self.channel_id,
            'active_wars': self.active_wars,
            'announced_wars': list(self.announced_wars),
            'announced_scheduled': list(self.announced_scheduled),
            'announced_24h': list(self.announced_24h)
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
        """Fetch faction info from Torn API using shared API layer"""
        data = await fetch_any_faction_data(faction_id, selections='basic')
        if not data:
            logger.warning(f'Alliance: Could not fetch faction {faction_id}')
        return data

    async def _fetch_faction_wars(self, faction_id: str) -> Optional[Dict]:
        """Fetch faction war data from Torn API v1 using rankedwars selection.
        v1 rankedwars works for any faction, unlike v2 wars which only returns
        the API key owner's faction wars."""
        data = await fetch_any_faction_data(faction_id, selections='basic,rankedwars')
        if not data:
            logger.warning(f'Alliance: Could not fetch wars for faction {faction_id}')
            return None

        # Convert v1 rankedwars format to the wars format we expect
        # v1: {rankedwars: {war_id: {war: {start, end, target}, factions: {fid: {...}}}}}
        # We need: {wars: {ranked: {war_id, start, end, factions: [...]}}}
        rankedwars = data.get('rankedwars', {})
        if rankedwars:
            now = datetime.now(timezone.utc).timestamp()
            best_war = None
            best_priority = -1  # 2=active, 1=scheduled, 0=recently ended

            for wid, wdata in rankedwars.items():
                war_info = wdata.get('war', {})
                factions_dict = wdata.get('factions', {})
                start = war_info.get('start', 0)
                end = war_info.get('end', 0)

                # Check if this war involves the target faction
                if str(faction_id) not in [str(k) for k in factions_dict.keys()]:
                    continue

                # Determine priority: active > scheduled > recently ended
                if start <= now and (not end or end == 0 or end > now):
                    priority = 2  # Active war
                elif start > now:
                    priority = 1  # Scheduled
                elif end and end > 0 and (now - end) < 604800:
                    priority = 0  # Ended within last 7 days
                else:
                    continue  # Too old, skip

                # Pick the highest priority, tiebreak by most recent start
                if priority > best_priority or (priority == best_priority and start > best_war.get('start', 0)):
                    best_priority = priority
                    best_war = {'wid': wid, 'wdata': wdata, 'start': start}

            if best_war:
                wid = best_war['wid']
                wdata = best_war['wdata']
                war_info = wdata.get('war', {})
                factions_dict = wdata.get('factions', {})

                # Convert factions dict to list format
                factions_list = []
                for fid, fdata in factions_dict.items():
                    if isinstance(fdata, dict):
                        fdata['id'] = int(fid)
                        factions_list.append(fdata)

                data['wars'] = {
                    'ranked': {
                        'war_id': int(wid),
                        'start': war_info.get('start', 0),
                        'end': war_info.get('end', 0),
                        'target': war_info.get('target', 0),
                        'factions': factions_list
                    }
                }
            else:
                data['wars'] = {}
        else:
            data['wars'] = {}

        return data

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
            logger.warning(f'Alliance: Could not find channel {self.channel_id}')
            return

        for faction_id, ally_info in self.allies.items():
            ally_name = ally_info.get('name', f'Faction {faction_id}')

            try:
                faction_data = await self._fetch_faction_wars(faction_id)
                if not faction_data:
                    logger.info(f'Alliance: No data returned for {ally_name} [{faction_id}]')
                    continue

                wars = faction_data.get('wars', {})

                # Debug: log the structure we received
                logger.info(f'Alliance: {ally_name} wars data type: {type(wars)}, keys: {wars.keys() if isinstance(wars, dict) else "N/A"}')

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
                logger.error(f'Alliance: Error checking wars for {ally_name}: {e}')
                import traceback
                traceback.print_exc()

        await self.save_data()

    async def _process_war_v2(self, channel, faction_id: str, ally_name: str, war_id: str, war_item: Dict):
        """Process a single war from API v2 format"""
        # v2 format has different structure - war data is directly in the item
        start_time = war_item.get('start', 0)
        end_time = war_item.get('end', 0)
        now = datetime.now(timezone.utc).timestamp()

        logger.info(f'Alliance v2: Processing war {war_id} for {ally_name} - start: {start_time}, end: {end_time}, now: {now}')

        # Get enemy faction info from war data
        factions = war_item.get('factions', [])
        enemy_name = 'Unknown Faction'
        enemy_id = None
        ally_in_war = False

        # Find enemy faction (the one that is NOT the ally)
        if isinstance(factions, list):
            # Check if ally is actually in this war
            for fdata in factions:
                if str(fdata.get('id', '')) == str(faction_id):
                    ally_in_war = True
                    break

            if not ally_in_war:
                logger.info(f'Alliance: War {war_id} does not involve {ally_name} [{faction_id}], skipping')
                return

            for fdata in factions:
                fid = str(fdata.get('id', ''))
                if fid != str(faction_id):
                    enemy_name = fdata.get('name', f'Faction {fid}')
                    enemy_id = fid
                    break
        elif isinstance(factions, dict):
            # Check if ally is in the war
            ally_in_war = str(faction_id) in [str(k) for k in factions.keys()]

            if not ally_in_war:
                logger.info(f'Alliance: War {war_id} does not involve {ally_name} [{faction_id}], skipping')
                return

            for fid, fdata in factions.items():
                if str(fid) != str(faction_id):
                    enemy_name = fdata.get('name', f'Faction {fid}') if isinstance(fdata, dict) else f'Faction {fid}'
                    enemy_id = fid
                    break

        # War hasn't started yet - announce scheduled and 24h reminder
        if start_time > now:
            hours_until = (start_time - now) / 3600

            # Announce immediately when war is first detected
            if war_id not in self.announced_scheduled:
                await self._announce_war_scheduled(channel, ally_name, enemy_name, start_time, hours_until)
                self.announced_scheduled.add(war_id)

            # 24-hour reminder (within 22-26 hour window to catch it reliably)
            if war_id not in self.announced_24h and 22 <= hours_until <= 26:
                await self._announce_war_24h_reminder(channel, ally_name, enemy_name, start_time)
                self.announced_24h.add(war_id)

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

    async def _announce_war_scheduled(self, channel, ally_name: str, enemy_name: str, start_time: int, hours_until: float):
        """Announce a scheduled war"""
        embed = create_embed(
            title='⚔️ Alliance War Scheduled',
            description=(
                f"**{ally_name}** is starting a war with **{enemy_name}**\n\n"
                f"**Starts in:** {hours_until:.1f} hours\n"
                f"**Start time:** {format_time_dual(start_time, date=True)}\n"
                f"**Countdown:** <t:{int(start_time)}:R>"
            ),
            color=EMBED_COLOR_WARNING
        )

        await channel.send(embed=embed)
        logger.info(f'Alliance: Announced scheduled war - {ally_name} vs {enemy_name}')

    async def _announce_war_24h_reminder(self, channel, ally_name: str, enemy_name: str, start_time: int):
        """Announce 24-hour reminder for upcoming war"""
        embed = create_embed(
            title='⏰ Alliance War - 24 Hours',
            description=(
                f"**{ally_name}** vs **{enemy_name}** starts in ~24 hours!\n\n"
                f"**Start time:** <t:{int(start_time)}:F>\n"
                f"**Countdown:** <t:{int(start_time)}:R>"
            ),
            color=EMBED_COLOR_INFO
        )

        await channel.send(embed=embed)
        logger.info(f'Alliance: Announced 24h reminder - {ally_name} vs {enemy_name}')

    async def _announce_war_started(self, channel, ally_name: str, enemy_name: str):
        """Announce a war has started"""
        embed = create_embed(
            title='🔥 Alliance War Started!',
            description=f"**{ally_name}**'s war with **{enemy_name}** has begun!",
            color=EMBED_COLOR_CRITICAL
        )

        await channel.send(embed=embed)
        logger.info(f'Alliance: Announced war start - {ally_name} vs {enemy_name}')

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

            # Handle both v2 (list) and v1 (dict) formats
            if isinstance(factions, list):
                # v2 API format: list of faction objects
                for fdata in factions:
                    if isinstance(fdata, dict):
                        fid = str(fdata.get('id', ''))
                        score = fdata.get('score', 0)
                        if fid == str(faction_id):
                            ally_score = score
                        else:
                            enemy_score = score
            elif isinstance(factions, dict):
                # v1 API format: dict with faction_id keys
                for fid, fdata in factions.items():
                    score = fdata.get('score', 0) if isinstance(fdata, dict) else 0
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
            self.announced_24h.discard(war_id)

        logger.info(f'Alliance: Announced war {result} - {ally_name} vs {enemy_name}')


async def setup_alliance_commands(bot, manager: AllianceManager):
    """Register alliance slash commands"""

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
        debug_lines.append(f'Tracking **{len(manager.allies)}** allies')
        debug_lines.append('')

        for faction_id, ally_info in manager.allies.items():
            ally_name = ally_info.get('name', f'Faction {faction_id}')
            debug_lines.append(f'**{ally_name}**')

            faction_data = await manager._fetch_faction_wars(faction_id)
            if not faction_data:
                debug_lines.append('  ❌ API error')
                debug_lines.append('')
                continue

            wars = faction_data.get('wars', {})
            if not wars:
                debug_lines.append('  No wars')
                debug_lines.append('')
                continue

            if isinstance(wars, dict):
                ranked_war = wars.get('ranked')

                if ranked_war and isinstance(ranked_war, dict):
                    war_id = str(ranked_war.get('war_id', 'unknown'))
                    start = ranked_war.get('start', 0)
                    end = ranked_war.get('end', 0)
                    now = datetime.now(timezone.utc).timestamp()

                    # Get enemy from factions and verify ally is in the war
                    factions = ranked_war.get('factions', [])
                    enemy_name = 'Unknown'
                    ally_in_war = False
                    if isinstance(factions, list):
                        for fdata in factions:
                            if isinstance(fdata, dict):
                                fid = str(fdata.get('id', ''))
                                if fid == str(faction_id):
                                    ally_in_war = True
                                else:
                                    enemy_name = fdata.get('name', f'Faction {fid}')
                    elif isinstance(factions, dict):
                        ally_in_war = str(faction_id) in [str(k) for k in factions.keys()]
                        for fid, fdata in factions.items():
                            if str(fid) != str(faction_id):
                                enemy_name = fdata.get('name', f'Faction {fid}') if isinstance(fdata, dict) else f'Faction {fid}'

                    if not ally_in_war:
                        debug_lines.append(f'  ⚠️ API returned wrong war (#{war_id})')
                        debug_lines.append('')
                        continue

                    if war_id != 'unknown' and war_id != '0':
                        # end can be None or 0 for ongoing wars
                        if start > now:
                            status = 'scheduled'
                        elif end is None or end == 0 or now < end:
                            status = 'active'
                        else:
                            status = 'ended'
                        # Status emoji and time info
                        if status == 'scheduled':
                            emoji = '📅'
                            time_info = f'Starts in {(start - now) / 3600:.1f}h'
                        elif status == 'active':
                            emoji = '⚔️'
                            time_info = f'Running for {(now - start) / 3600:.1f}h'
                        else:
                            emoji = '🏁'
                            duration = (end - start) / 3600
                            ended_ago = (now - end) / 3600
                            time_info = f'Lasted {duration:.1f}h (ended {ended_ago:.1f}h ago)'

                        # Announcement tracking
                        ann = []
                        if war_id in manager.announced_scheduled:
                            ann.append('Sched')
                        if war_id in manager.announced_24h:
                            ann.append('24h')
                        if war_id in manager.announced_wars:
                            ann.append('Start')
                        ann_text = ', '.join(ann) if ann else 'None'

                        debug_lines.append(f'  {emoji} vs {enemy_name} (#{war_id})')
                        debug_lines.append(f'  {time_info}')
                        debug_lines.append(f'  Announced: {ann_text}')
                    else:
                        debug_lines.append(f'  No ranked war')
                else:
                    debug_lines.append(f'  No ranked war data')
            else:
                debug_lines.append(f'  Unknown format')
            debug_lines.append('')

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
    logger.info('Alliance commands registered (/alliance add, remove, list, setchannel, check)')
