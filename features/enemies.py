"""
Enemy Watchlist Module
Track enemy players during war - monitor their online/offline status

Features:
- /enemy add <torn_id> <name> - Add enemy to watchlist
- /enemy remove <torn_id> - Remove enemy from watchlist
- /enemy list - Show all tracked enemies with current status
- /enemy clear - Clear all enemies
- Auto-checks every 15 minutes during active war
- Posts status changes to war channel
- Auto-clears when war ends
"""

import discord
from discord import app_commands
from discord.ext import tasks
from datetime import datetime, timezone
from utils.helpers import load_json, save_json, create_embed, format_timestamp, get_current_timestamp, is_management
from utils.torn_api import fetch_player_data
from config import (
    WAR_CHANNEL_ID,
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL
)


class EnemyWatchlist:
    """Manages enemy player tracking during war"""

    def __init__(self, bot):
        self.bot = bot
        self.watchlist_file = 'data/enemy_watchlist.json'
        self.watchlist = {}  # {torn_id: {name, last_status, last_check, added_by}}
        self.previous_statuses = {}  # Track previous status to detect changes

    async def has_active_war_event(self) -> bool:
        """
        Check if there's an active War Event in Discord scheduled events.
        Enemy alerts should only post when a war event is active.

        Returns:
            bool: True if a war event is active, False otherwise
        """
        try:
            for guild in self.bot.guilds:
                events = guild.scheduled_events

                for event in events:
                    # Check if event name contains "War" and is active/started
                    event_name = event.name.lower()
                    if 'war' in event_name:
                        now = datetime.now(timezone.utc)

                        # Event is active if it has started and not ended
                        if event.status.name in ['active', 'scheduled']:
                            if event.start_time <= now:
                                # Event has started
                                if event.end_time is None or event.end_time > now:
                                    return True

            return False
        except Exception as e:
            print(f'Error checking war events: {e}')
            return False

    async def load_watchlist(self):
        """Load watchlist from JSON file"""
        self.watchlist = load_json(self.watchlist_file, default={})
        # Restore previous_statuses from stored last_status so a restart
        # doesn't cause every enemy to appear as a "status change"
        for tid, data in self.watchlist.items():
            if data.get('last_status'):
                self.previous_statuses[tid] = data['last_status']
        print(f'Loaded {len(self.watchlist)} enemies in watchlist')

    async def save_watchlist(self):
        """Save watchlist to JSON file"""
        save_json(self.watchlist_file, self.watchlist)

    def add_enemy(self, torn_id: int, name: str, added_by: int) -> bool:
        """
        Add enemy to watchlist

        Returns:
            True if added, False if already exists
        """
        torn_id_str = str(torn_id)
        if torn_id_str in self.watchlist:
            return False

        self.watchlist[torn_id_str] = {
            'name': name,
            'torn_id': torn_id,
            'last_status': None,
            'last_check': None,
            'added_by': str(added_by),
            'added_at': get_current_timestamp()
        }
        return True

    def remove_enemy(self, torn_id: int) -> bool:
        """
        Remove enemy from watchlist

        Returns:
            True if removed, False if not found
        """
        torn_id_str = str(torn_id)
        if torn_id_str not in self.watchlist:
            return False

        del self.watchlist[torn_id_str]
        if torn_id_str in self.previous_statuses:
            del self.previous_statuses[torn_id_str]
        return True

    def clear_all(self):
        """Clear entire watchlist"""
        count = len(self.watchlist)
        self.watchlist = {}
        self.previous_statuses = {}
        return count

    def get_enemy_count(self) -> int:
        """Get number of enemies in watchlist"""
        return len(self.watchlist)

    async def fetch_enemy_status(self, torn_id: int) -> dict:
        """
        Fetch current status for an enemy

        Returns:
            Dict with status info or None if error
        """
        data = await fetch_player_data(torn_id)
        if not data:
            return None

        # Extract status info
        last_action = data.get('last_action', {})
        status = data.get('status', {})

        return {
            'name': data.get('name', 'Unknown'),
            'level': data.get('level', 0),
            'online_status': last_action.get('status', 'Unknown'),  # Online/Offline/Idle
            'last_action': last_action.get('timestamp', 0),
            'last_action_relative': last_action.get('relative', 'Unknown'),
            'state': status.get('state', 'Okay'),  # Okay/Hospital/Jail/Federal/Abroad
            'state_until': status.get('until', 0),
            'description': status.get('description', '')
        }

    def format_status_emoji(self, online_status: str, state: str) -> str:
        """Get emoji for status display"""
        if state == 'Hospital':
            return '🏥'
        elif state == 'Jail':
            return '⛓️'
        elif state == 'Federal':
            return '🏛️'
        elif state == 'Abroad':
            return '✈️'
        elif online_status == 'Online':
            return '🟢'
        elif online_status == 'Idle':
            return '🟡'
        else:  # Offline
            return '🔴'

    def format_status_text(self, status_data: dict) -> str:
        """Format status as readable text"""
        emoji = self.format_status_emoji(status_data['online_status'], status_data['state'])
        state = status_data['state']

        if state in ['Hospital', 'Jail', 'Federal']:
            until = status_data.get('state_until', 0)
            if until > get_current_timestamp():
                return f"{emoji} {state} (out {format_timestamp(until, 'R')})"
            return f"{emoji} {state}"
        elif state == 'Abroad':
            return f"{emoji} Traveling"
        else:
            return f"{emoji} {status_data['online_status']}"

    async def check_all_enemies(self, post_updates: bool = True):
        """
        Check status of all enemies and post changes

        Args:
            post_updates: Whether to post status updates to war channel
                          Note: Updates only posted if there's an active War Event
        """
        if not self.watchlist:
            return

        channel = self.bot.get_channel(WAR_CHANNEL_ID)
        if not channel and post_updates:
            print('Enemy watchlist: Could not find war channel')
            return

        # Only post updates if there's an active war event in Discord
        if post_updates:
            war_event_active = await self.has_active_war_event()
            if not war_event_active:
                # Still check statuses internally, but don't post to channel
                post_updates = False

        status_changes = []
        now = get_current_timestamp()

        for torn_id_str, enemy_data in self.watchlist.items():
            torn_id = int(torn_id_str)
            status = await self.fetch_enemy_status(torn_id)

            if not status:
                continue

            # Update stored data
            self.watchlist[torn_id_str]['last_status'] = status
            self.watchlist[torn_id_str]['last_check'] = now

            # Check for status changes
            prev_status = self.previous_statuses.get(torn_id_str)
            if prev_status:
                # Detect important changes
                came_online = (
                    prev_status['online_status'] != 'Online' and
                    status['online_status'] == 'Online' and
                    status['state'] == 'Okay'
                )
                left_hospital = (
                    prev_status['state'] == 'Hospital' and
                    status['state'] == 'Okay'
                )
                left_jail = (
                    prev_status['state'] == 'Jail' and
                    status['state'] == 'Okay'
                )

                if came_online or left_hospital or left_jail:
                    change_type = 'came online' if came_online else f'left {prev_status["state"].lower()}'
                    status_changes.append({
                        'name': enemy_data['name'],
                        'torn_id': torn_id,
                        'change': change_type,
                        'status': status
                    })

            # Store current status for next comparison
            self.previous_statuses[torn_id_str] = status

        await self.save_watchlist()

        # Post important status changes
        if status_changes and post_updates and channel:
            for change in status_changes:
                embed = create_embed(
                    title=f'Enemy Alert: {change["name"]}',
                    description=f'**{change["name"]}** just **{change["change"]}**!',
                    color=EMBED_COLOR_WARNING,
                    fields=[
                        {'name': 'Status', 'value': self.format_status_text(change['status']), 'inline': True},
                        {'name': 'Level', 'value': str(change['status']['level']), 'inline': True},
                        {'name': 'Profile', 'value': f'[Attack](https://www.torn.com/loader.php?sid=attack&user2ID={change["torn_id"]})', 'inline': True}
                    ]
                )
                await channel.send(embed=embed)

            print(f'Posted {len(status_changes)} enemy status changes')

    async def format_watchlist_embed(self) -> discord.Embed:
        """Create embed showing all enemies and their status"""
        if not self.watchlist:
            return create_embed(
                title='Enemy Watchlist',
                description='No enemies are being tracked.\n\nUse `/enemy add <torn_id> <name>` to add enemies.',
                color=EMBED_COLOR_INFO
            )

        # Group by status
        online = []
        hospital = []
        jail = []
        offline = []
        unknown = []

        for torn_id_str, data in self.watchlist.items():
            status = data.get('last_status')
            name = data['name']
            torn_id = data['torn_id']

            if not status:
                unknown.append(f'**{name}** [{torn_id}] - ❓ Not checked yet')
                continue

            status_text = self.format_status_text(status)
            last_action = status.get('last_action_relative', 'Unknown')
            entry = f'**{name}** [Lvl {status["level"]}] - {status_text}\n└ Last action: {last_action} | [Attack](https://www.torn.com/loader.php?sid=attack&user2ID={torn_id})'

            state = status.get('state', 'Okay')
            if state == 'Hospital':
                hospital.append(entry)
            elif state in ['Jail', 'Federal']:
                jail.append(entry)
            elif status['online_status'] == 'Online':
                online.append(entry)
            else:
                offline.append(entry)

        # Build description
        sections = []
        if online:
            sections.append(f'**🟢 Online ({len(online)})**\n' + '\n'.join(online))
        if hospital:
            sections.append(f'**🏥 Hospital ({len(hospital)})**\n' + '\n'.join(hospital))
        if jail:
            sections.append(f'**⛓️ Jail ({len(jail)})**\n' + '\n'.join(jail))
        if offline:
            sections.append(f'**🔴 Offline ({len(offline)})**\n' + '\n'.join(offline))
        if unknown:
            sections.append(f'**❓ Unknown ({len(unknown)})**\n' + '\n'.join(unknown))

        description = '\n\n'.join(sections)

        # Truncate if too long
        if len(description) > 4000:
            description = description[:4000] + '\n\n... (list truncated)'

        embed = create_embed(
            title=f'Enemy Watchlist ({len(self.watchlist)} enemies)',
            description=description,
            color=EMBED_COLOR_INFO
        )

        return embed


async def setup_enemy_commands(bot, enemy_watchlist: EnemyWatchlist):
    """Set up enemy watchlist slash commands"""

    @bot.tree.command(
        name='enemy',
        description='Manage enemy watchlist for war'
    )
    @app_commands.describe(
        action='Action to perform',
        torn_id='Enemy Torn ID (for add/remove)',
        name='Enemy name (for add)'
    )
    @app_commands.choices(action=[
        app_commands.Choice(name='list - Show all tracked enemies', value='list'),
        app_commands.Choice(name='add - Add enemy to watchlist', value='add'),
        app_commands.Choice(name='remove - Remove enemy from watchlist', value='remove'),
        app_commands.Choice(name='check - Force check all enemy statuses now', value='check'),
        app_commands.Choice(name='clear - Clear entire watchlist', value='clear')
    ])
    async def enemy_command(
        interaction: discord.Interaction,
        action: str,
        torn_id: int = None,
        name: str = None
    ):
        """Enemy watchlist management"""
        # Most actions require management permissions
        if action in ['add', 'remove', 'clear'] and not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to modify the enemy watchlist.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=(action != 'list'))

        if action == 'list':
            # First refresh statuses
            await enemy_watchlist.check_all_enemies(post_updates=False)
            embed = await enemy_watchlist.format_watchlist_embed()
            await interaction.followup.send(embed=embed)

        elif action == 'add':
            if not torn_id or not name:
                await interaction.followup.send('Please provide both torn_id and name.', ephemeral=True)
                return

            # Verify the enemy exists
            status = await enemy_watchlist.fetch_enemy_status(torn_id)
            if not status:
                embed = create_embed(
                    title='Error',
                    description=f'Could not find player with ID {torn_id}',
                    color=EMBED_COLOR_CRITICAL
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            if enemy_watchlist.add_enemy(torn_id, name, interaction.user.id):
                await enemy_watchlist.save_watchlist()
                embed = create_embed(
                    title='Enemy Added',
                    description=f'**{name}** [{torn_id}] added to watchlist.\n\n'
                               f'Current status: {enemy_watchlist.format_status_text(status)}',
                    color=EMBED_COLOR_SUCCESS
                )
            else:
                embed = create_embed(
                    title='Already Tracked',
                    description=f'Enemy {torn_id} is already in the watchlist.',
                    color=EMBED_COLOR_INFO
                )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'remove':
            if not torn_id:
                await interaction.followup.send('Please provide the torn_id to remove.', ephemeral=True)
                return

            if enemy_watchlist.remove_enemy(torn_id):
                await enemy_watchlist.save_watchlist()
                embed = create_embed(
                    title='Enemy Removed',
                    description=f'Removed {torn_id} from watchlist.',
                    color=EMBED_COLOR_SUCCESS
                )
            else:
                embed = create_embed(
                    title='Not Found',
                    description=f'Enemy {torn_id} is not in the watchlist.',
                    color=EMBED_COLOR_WARNING
                )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'check':
            await enemy_watchlist.check_all_enemies(post_updates=False)
            embed = await enemy_watchlist.format_watchlist_embed()
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'clear':
            count = enemy_watchlist.clear_all()
            await enemy_watchlist.save_watchlist()
            embed = create_embed(
                title='Watchlist Cleared',
                description=f'Removed {count} enemies from watchlist.',
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    print('Enemy watchlist commands registered (/enemy)')
