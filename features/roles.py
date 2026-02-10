"""
Auto Role Sync Module
Automatically syncs Discord roles with Torn faction positions

Features:
- Syncs Discord roles based on Torn faction position
- Runs passively in background (every hour)
- Silent operation - no messages posted
"""

import discord
from discord.ext import tasks
from utils.helpers import load_json, save_json
from utils.torn_api import fetch_faction_members
from config import GUILD_ID


class RoleSyncManager:
    """Manages automatic role sync between Torn and Discord"""

    def __init__(self, bot):
        self.bot = bot
        self.config_file = 'data/role_sync_config.json'
        self.config = {}
        # Default position to role mapping (position_name -> role_id)
        # Users will set these via /rolesync setrole command
        self.position_roles = {}

    async def load_config(self):
        """Load role sync config from JSON file"""
        default_config = {
            'enabled': True,
            'position_roles': {},  # {position_name: role_id}
            'last_sync': 0
        }
        self.config = load_json(self.config_file, default=default_config)
        self.position_roles = self.config.get('position_roles', {})
        print(f'Loaded role sync config with {len(self.position_roles)} position mappings')

    async def save_config(self):
        """Save role sync config to JSON file"""
        self.config['position_roles'] = self.position_roles
        save_json(self.config_file, self.config)

    def set_position_role(self, position_name: str, role_id: int):
        """Map a Torn position to a Discord role"""
        self.position_roles[position_name.lower()] = role_id

    def remove_position_role(self, position_name: str):
        """Remove a position to role mapping"""
        position_lower = position_name.lower()
        if position_lower in self.position_roles:
            del self.position_roles[position_lower]
            return True
        return False

    def get_role_for_position(self, position_name: str) -> int:
        """Get Discord role ID for a Torn position"""
        return self.position_roles.get(position_name.lower())

    def get_all_position_roles(self) -> dict:
        """Get all position to role mappings"""
        return self.position_roles.copy()

    async def sync_member_role(self, guild: discord.Guild, discord_member: discord.Member,
                                torn_position: str) -> bool:
        """
        Sync a single member's role based on their Torn position

        Returns:
            True if role was changed, False otherwise
        """
        if not torn_position:
            return False

        # Get the role ID for this position
        role_id = self.get_role_for_position(torn_position)
        if not role_id:
            return False  # No mapping for this position

        role = guild.get_role(role_id)
        if not role:
            return False  # Role doesn't exist

        # Check if member already has this role
        if role in discord_member.roles:
            return False  # Already has the role

        # Get all position-based roles to manage
        all_position_role_ids = set(self.position_roles.values())
        roles_to_remove = [r for r in discord_member.roles if r.id in all_position_role_ids and r.id != role_id]

        try:
            # Remove old position roles and add new one
            if roles_to_remove:
                await discord_member.remove_roles(*roles_to_remove, reason='Role sync: position changed')
            await discord_member.add_roles(role, reason=f'Role sync: Torn position is {torn_position}')
            return True
        except discord.Forbidden:
            return False  # Can't modify this member's roles
        except Exception as e:
            print(f'Error syncing role for {discord_member.name}: {e}')
            return False

    async def sync_all_members(self):
        """
        Sync roles for all faction members
        Called by background task
        """
        if not self.config.get('enabled', True):
            return

        if not self.position_roles:
            return  # No position mappings configured

        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            print('Role sync: Could not find guild')
            return

        # Fetch faction members from Torn
        faction_members = await fetch_faction_members()
        if not faction_members:
            print('Role sync: Could not fetch faction members')
            return

        # Load verified users to get Discord ID -> Torn ID mapping
        verified_users = load_json('data/verified_users.json', default={})
        inactivity_users = load_json('data/discord_to_torn.json', default={})

        # Create Torn ID -> Discord ID mapping from both databases
        torn_to_discord = {}
        for discord_id, data in verified_users.items():
            torn_id = str(data.get('torn_id', ''))
            if torn_id:
                torn_to_discord[torn_id] = discord_id

        # Also add from inactivity tracker (discord_id -> torn_id format)
        for discord_id, torn_id in inactivity_users.items():
            if str(torn_id) not in torn_to_discord:
                torn_to_discord[str(torn_id)] = discord_id

        # Sync each verified member
        synced_count = 0
        for torn_id, member_data in faction_members.items():
            discord_id = torn_to_discord.get(torn_id)
            if not discord_id:
                continue  # Not verified in Discord

            try:
                discord_member = guild.get_member(int(discord_id))
                if not discord_member:
                    discord_member = await guild.fetch_member(int(discord_id))
            except (discord.NotFound, discord.HTTPException, ValueError):
                continue  # Not in Discord server or invalid ID

            position = member_data.get('position', '')
            if await self.sync_member_role(guild, discord_member, position):
                synced_count += 1

        if synced_count > 0:
            print(f'Role sync: Updated roles for {synced_count} members')

        # Update last sync time
        from utils.helpers import get_current_timestamp
        self.config['last_sync'] = get_current_timestamp()
        await self.save_config()


async def setup_rolesync_commands(bot, role_sync_manager: RoleSyncManager):
    """Set up role sync slash commands"""
    from discord import app_commands
    from utils.helpers import create_embed, load_json, is_management
    from utils.torn_api import fetch_faction_members
    from config import EMBED_COLOR_SUCCESS, EMBED_COLOR_INFO, EMBED_COLOR_CRITICAL

    @bot.tree.command(
        name='rolesync',
        description='[Management] Configure automatic role sync with Torn positions'
    )
    @app_commands.describe(
        action='Action to perform',
        position='Torn faction position name (e.g., Leader, Management)',
        role='Discord role to assign for this position'
    )
    @app_commands.choices(action=[
        app_commands.Choice(name='status - View current role mappings', value='status'),
        app_commands.Choice(name='setrole - Map a Torn position to Discord role', value='setrole'),
        app_commands.Choice(name='remove - Remove a position mapping', value='remove'),
        app_commands.Choice(name='sync - Force sync all members now', value='sync'),
        app_commands.Choice(name='toggle - Enable/disable auto sync', value='toggle')
    ])
    async def rolesync_command(
        interaction: discord.Interaction,
        action: str,
        position: str = None,
        role: discord.Role = None
    ):
        """Role sync management command"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if action == 'status':
            # Show current mappings
            mappings = role_sync_manager.get_all_position_roles()
            enabled = role_sync_manager.config.get('enabled', True)

            if mappings:
                mapping_text = '\n'.join([
                    f'**{pos.title()}** -> <@&{role_id}>'
                    for pos, role_id in mappings.items()
                ])
            else:
                mapping_text = 'No position mappings configured.\nUse `/rolesync setrole` to add mappings.'

            embed = create_embed(
                title='Role Sync Status',
                description=f'**Auto-sync:** {"Enabled" if enabled else "Disabled"}\n\n'
                           f'**Position Mappings:**\n{mapping_text}',
                color=EMBED_COLOR_INFO
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'setrole':
            if not position or not role:
                await interaction.followup.send('Please provide both position name and role.', ephemeral=True)
                return

            role_sync_manager.set_position_role(position, role.id)
            await role_sync_manager.save_config()

            embed = create_embed(
                title='Position Mapping Added',
                description=f'Torn position **{position}** will now get role {role.mention}',
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'remove':
            if not position:
                await interaction.followup.send('Please provide a position name to remove.', ephemeral=True)
                return

            if role_sync_manager.remove_position_role(position):
                await role_sync_manager.save_config()
                embed = create_embed(
                    title='Position Mapping Removed',
                    description=f'Removed mapping for position **{position}**',
                    color=EMBED_COLOR_SUCCESS
                )
            else:
                embed = create_embed(
                    title='Not Found',
                    description=f'No mapping found for position **{position}**',
                    color=EMBED_COLOR_INFO
                )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'sync':
            # Run sync with diagnostic output
            if not role_sync_manager.position_roles:
                await interaction.followup.send('No position mappings configured. Use `/rolesync setrole` first.', ephemeral=True)
                return

            guild = interaction.guild

            # Fetch faction data
            faction_members = await fetch_faction_members()
            if not faction_members:
                await interaction.followup.send('Could not fetch faction members from Torn API.', ephemeral=True)
                return

            # Show what positions Torn API returns
            positions_found = {}
            for tid, mdata in faction_members.items():
                pos = mdata.get('position', 'Unknown')
                if pos not in positions_found:
                    positions_found[pos] = []
                positions_found[pos].append(mdata.get('name', tid))

            positions_text = '\n'.join([
                f'**{pos}**: {len(names)} members'
                for pos, names in sorted(positions_found.items())
            ])

            # Show current mappings vs API positions
            mappings = role_sync_manager.get_all_position_roles()
            mapping_text = '\n'.join([
                f'`{pos}` -> <@&{rid}>'
                for pos, rid in mappings.items()
            ])

            # Check which API positions have mappings
            matched = []
            unmatched = []
            for api_pos in positions_found:
                if api_pos.lower() in mappings:
                    matched.append(api_pos)
                else:
                    unmatched.append(api_pos)

            match_text = ''
            if matched:
                match_text += f'**Matched:** {", ".join(matched)}\n'
            if unmatched:
                match_text += f'**No mapping:** {", ".join(unmatched)}\n'

            # Run sync manually with detailed tracking
            verified_users = load_json('data/verified_users.json', default={})

            # Also check inactivity tracker database
            inactivity_users = load_json('data/discord_to_torn.json', default={})

            # Build Torn ID -> Discord ID mapping from both databases
            torn_to_discord = {}
            for discord_id, data in verified_users.items():
                torn_id = str(data.get('torn_id', ''))
                if torn_id:
                    torn_to_discord[torn_id] = discord_id

            # Also add from inactivity tracker (discord_id -> torn_id format)
            for discord_id, torn_id in inactivity_users.items():
                if str(torn_id) not in torn_to_discord:
                    torn_to_discord[str(torn_id)] = discord_id

            total_faction = len(faction_members)
            verified_matched = 0
            role_changed = 0
            already_correct = 0
            not_in_server = 0
            no_mapping = 0
            errors = 0

            for torn_id, mdata in faction_members.items():
                discord_id = torn_to_discord.get(torn_id)
                if not discord_id:
                    continue
                verified_matched += 1

                try:
                    discord_member = guild.get_member(int(discord_id))
                    if not discord_member:
                        discord_member = await guild.fetch_member(int(discord_id))
                except (discord.NotFound, discord.HTTPException, ValueError):
                    not_in_server += 1
                    continue

                position = mdata.get('position', '')
                role_id = role_sync_manager.get_role_for_position(position)
                if not role_id:
                    no_mapping += 1
                    continue

                role = guild.get_role(role_id)
                if not role:
                    errors += 1
                    continue

                if role in discord_member.roles:
                    already_correct += 1
                    continue

                # Actually assign the role
                try:
                    all_position_role_ids = set(role_sync_manager.position_roles.values())
                    roles_to_remove = [r for r in discord_member.roles if r.id in all_position_role_ids and r.id != role_id]
                    if roles_to_remove:
                        await discord_member.remove_roles(*roles_to_remove, reason='Role sync')
                    await discord_member.add_roles(role, reason=f'Role sync: Torn position is {position}')
                    role_changed += 1
                except discord.Forbidden:
                    errors += 1
                except Exception:
                    errors += 1

            sync_text = (
                f'**Faction members:** {total_faction}\n'
                f'**Verified in database:** {len(torn_to_discord)}\n'
                f'**Matched to faction:** {verified_matched}\n'
                f'**Roles changed:** {role_changed}\n'
                f'**Already correct:** {already_correct}\n'
                f'**Not in server:** {not_in_server}\n'
                f'**No position mapping:** {no_mapping}\n'
                f'**Errors:** {errors}'
            )

            embed = create_embed(
                title='Sync Complete - Diagnostic',
                description=(
                    f'**Torn API Positions Found:**\n{positions_text}\n\n'
                    f'**Your Mappings:**\n{mapping_text}\n\n'
                    f'**Match Status:**\n{match_text}\n'
                    f'**Sync Results:**\n{sync_text}'
                ),
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'toggle':
            current = role_sync_manager.config.get('enabled', True)
            role_sync_manager.config['enabled'] = not current
            await role_sync_manager.save_config()

            new_state = 'enabled' if not current else 'disabled'
            embed = create_embed(
                title='Auto-Sync Toggled',
                description=f'Automatic role sync is now **{new_state}**',
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    print('Role sync commands registered (/rolesync)')
