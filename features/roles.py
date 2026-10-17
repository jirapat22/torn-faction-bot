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

import logging

logger = logging.getLogger(__name__)


def privilege_level(role_permissions, role_id, management_role_ids=None):
    """
    How dangerous is it to hand this role out by Torn position?

    Returns 'admin', 'management', or None.

    This exists because /rolesync setrole is a one-line way to give a whole
    squad real power: map a position to @Management and every member holding
    that position in Torn can issue and dismiss strikes, with no human step
    in Discord. Map one to an Administrator role and they can delete the
    server. Both are typos away from the harmless case, and the command gives
    no hint which one you just did.

    Taking the permission integer rather than a discord.Role keeps this
    testable without a guild.
    """
    ADMINISTRATOR = 1 << 3
    try:
        perms = int(role_permissions)
    except (TypeError, ValueError):
        perms = 0
    if perms & ADMINISTRATOR:
        return 'admin'
    ids = {str(x) for x in (management_role_ids or [])}
    if str(role_id) in ids:
        return 'management'
    return None



class RoleSyncManager:
    """Manages automatic role sync between Torn and Discord"""

    def __init__(self, bot):
        self.bot = bot
        self.config_file = 'data/role_sync_config.json'
        self.config = {}
        # Position to role mapping (position_name -> [role_id, ...])
        # Users will set these via /rolesync setrole command
        self.position_roles = {}

    async def load_config(self):
        """Load role sync config from JSON file"""
        default_config = {
            'enabled': True,
            'position_roles': {},  # {position_name: [role_id, ...]}
            'last_sync': 0
        }
        self.config = load_json(self.config_file, default=default_config)
        raw_roles = self.config.get('position_roles', {})
        # Migrate old format (single int/str) to new format (list of ints)
        self.position_roles = {}
        for pos, val in raw_roles.items():
            if isinstance(val, list):
                self.position_roles[pos] = [int(v) for v in val if v]
            else:
                self.position_roles[pos] = [int(val)]
        logger.info(f'Loaded role sync config with {len(self.position_roles)} position mappings')

    async def save_config(self):
        """Save role sync config to JSON file"""
        self.config['position_roles'] = self.position_roles
        save_json(self.config_file, self.config)

    def set_position_role(self, position_name: str, role_id: int):
        """Add a Discord role to a Torn position mapping"""
        key = position_name.lower()
        if key not in self.position_roles:
            self.position_roles[key] = []
        if role_id not in self.position_roles[key]:
            self.position_roles[key].append(role_id)

    def remove_position_role(self, position_name: str, role_id: int = None):
        """Remove a role from a position mapping, or remove the entire position"""
        position_lower = position_name.lower()
        if position_lower not in self.position_roles:
            return False
        if role_id is not None:
            # Remove specific role from this position
            roles = self.position_roles[position_lower]
            if role_id in roles:
                roles.remove(role_id)
                if not roles:
                    del self.position_roles[position_lower]
                return True
            return False
        else:
            # Remove entire position mapping
            del self.position_roles[position_lower]
            return True

    def get_roles_for_position(self, position_name: str) -> list:
        """Get Discord role IDs for a Torn position"""
        return self.position_roles.get(position_name.lower(), [])

    def get_all_position_role_ids(self) -> set:
        """Get all role IDs across all position mappings"""
        all_ids = set()
        for role_ids in self.position_roles.values():
            all_ids.update(role_ids)
        return all_ids

    def get_all_position_roles(self) -> dict:
        """Get all position to role mappings"""
        return self.position_roles.copy()

    async def sync_member_role(self, guild: discord.Guild, discord_member: discord.Member,
                                torn_position: str) -> bool:
        """
        Sync a single member's roles based on their Torn position.
        A position can map to multiple Discord roles.

        Returns:
            True if roles were changed, False otherwise
        """
        all_position_role_ids = self.get_all_position_role_ids()

        # Get the role IDs for this position (may be empty if no mapping)
        target_role_ids = set(self.get_roles_for_position(torn_position)) if torn_position else set()
        target_roles = [guild.get_role(rid) for rid in target_role_ids]
        target_roles = [r for r in target_roles if r is not None]

        # Roles the member should NOT have (position-managed roles that aren't for their current position)
        roles_to_remove = [r for r in discord_member.roles
                          if r.id in all_position_role_ids and r.id not in target_role_ids]

        # Roles the member should have but doesn't
        roles_to_add = [r for r in target_roles if r not in discord_member.roles]

        # Nothing to change
        if not roles_to_remove and not roles_to_add:
            return False

        try:
            if roles_to_remove:
                await discord_member.remove_roles(*roles_to_remove, reason='Role sync: position changed')
            if roles_to_add:
                await discord_member.add_roles(*roles_to_add, reason=f'Role sync: Torn position is {torn_position}')
            return True
        except discord.Forbidden:
            return False  # Can't modify this member's roles
        except Exception as e:
            logger.error(f'Error syncing role for {discord_member.name}: {e}')
            return False

    async def sync_all_members(self, faction_members: dict = None):
        """
        Sync roles for all faction members
        Called by background task

        Args:
            faction_members: Pre-fetched {torn_id: member_data}, to avoid a
                              second Torn API call when the caller already
                              fetched it this tick. Fetches internally if omitted.
        """
        if not self.config.get('enabled', True):
            return

        if not self.position_roles:
            return  # No position mappings configured

        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            logger.warning('Role sync: Could not find guild')
            return

        # Fetch faction members from Torn (unless the caller already has them)
        if faction_members is None:
            faction_members = await fetch_faction_members()
        if not faction_members:
            logger.warning('Role sync: Could not fetch faction members')
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
            logger.info(f'Role sync: Updated roles for {synced_count} members')

        # Update last sync time
        from utils.helpers import get_current_timestamp
        self.config['last_sync'] = get_current_timestamp()
        await self.save_config()


async def setup_rolesync_commands(bot, role_sync_manager: RoleSyncManager):
    """Set up role sync slash commands"""
    from discord import app_commands
    from utils.helpers import create_embed, load_json, is_management
    from utils.torn_api import fetch_faction_members
    from config import (EMBED_COLOR_SUCCESS, EMBED_COLOR_INFO,
                        EMBED_COLOR_CRITICAL, EMBED_COLOR_WARNING)

    @bot.tree.command(
        name='rolesync',
        description='[Management] Configure automatic role sync with Torn positions'
    )
    @app_commands.describe(
        action='Action to perform',
        position='Torn faction position name (e.g., Leader, Management)',
        role='Discord role to assign for this position',
        confirm='[setrole] Required when the role hands out management powers'
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
        role: discord.Role = None,
        confirm: bool = False
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
                lines = []
                for pos, role_ids in mappings.items():
                    role_mentions = ', '.join([f'<@&{rid}>' for rid in role_ids])
                    lines.append(f'**{pos.title()}** -> {role_mentions}')
                mapping_text = '\n'.join(lines)
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

            from config import MANAGEMENT_ROLE_IDS
            level = privilege_level(role.permissions.value, role.id,
                                    MANAGEMENT_ROLE_IDS)
            if level == 'admin':
                # Never, with or without a confirm. A Torn position is not a
                # safe source of truth for Administrator.
                await interaction.followup.send(embed=create_embed(
                    title='Refused',
                    description=(
                        f'{role.mention} has **Administrator**, so mapping it '
                        f'to a Torn position would hand full control of this '
                        f'server to whoever holds that position in Torn - '
                        f'including anybody promoted there later.\n\n'
                        f'-# Assign a role like this by hand.'),
                    color=EMBED_COLOR_CRITICAL), ephemeral=True)
                return
            if level == 'management' and not confirm:
                holders = role_sync_manager.get_roles_for_position(position)
                await interaction.followup.send(embed=create_embed(
                    title='This hands out management, not just a colour',
                    description=(
                        f'{role.mention} is a management role: anybody whose '
                        f'Torn position is **{position}** would be able to '
                        f'issue and dismiss strikes, and would get it '
                        f'automatically the moment they are promoted in '
                        f'Torn.\n\n'
                        f'-# Currently mapped: '
                        + (', '.join(f'<@&{r}>' for r in holders) or 'nothing')
                        + '. Run it again with `confirm:True` if that is what '
                          'you want.'),
                    color=EMBED_COLOR_WARNING), ephemeral=True)
                return

            role_sync_manager.set_position_role(position, role.id)
            await role_sync_manager.save_config()

            # Show all roles now mapped to this position
            all_roles = role_sync_manager.get_roles_for_position(position)
            role_mentions = ', '.join([f'<@&{rid}>' for rid in all_roles])

            embed = create_embed(
                title='Position Mapping Updated',
                description=f'Torn position **{position}** will now get: {role_mentions}\n\n'
                           f'*Use `/rolesync remove` with position + role to remove a specific role.*',
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'remove':
            if not position:
                await interaction.followup.send('Please provide a position name to remove.', ephemeral=True)
                return

            role_id = role.id if role else None
            if role_sync_manager.remove_position_role(position, role_id):
                await role_sync_manager.save_config()
                if role_id:
                    # Removed specific role - show remaining roles if any
                    remaining = role_sync_manager.get_roles_for_position(position)
                    if remaining:
                        remaining_text = ', '.join([f'<@&{rid}>' for rid in remaining])
                        desc = f'Removed {role.mention} from **{position}**\nRemaining roles: {remaining_text}'
                    else:
                        desc = f'Removed {role.mention} from **{position}** (no roles left, position removed)'
                else:
                    desc = f'Removed all role mappings for position **{position}**'
                embed = create_embed(
                    title='Position Mapping Updated',
                    description=desc,
                    color=EMBED_COLOR_SUCCESS
                )
            else:
                embed = create_embed(
                    title='Not Found',
                    description=f'No mapping found for position **{position}**' + (f' with role {role.mention}' if role else ''),
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
                f'`{pos}` -> {", ".join([f"<@&{rid}>" for rid in role_ids])}'
                for pos, role_ids in mappings.items()
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
                target_role_ids = set(role_sync_manager.get_roles_for_position(position)) if position else set()
                target_roles = [guild.get_role(rid) for rid in target_role_ids]
                target_roles = [r for r in target_roles if r is not None]

                all_managed_ids = role_sync_manager.get_all_position_role_ids()
                roles_to_remove = [r for r in discord_member.roles
                                  if r.id in all_managed_ids and r.id not in target_role_ids]
                roles_to_add = [r for r in target_roles if r not in discord_member.roles]

                if not roles_to_remove and not roles_to_add:
                    if target_roles:
                        already_correct += 1
                    else:
                        no_mapping += 1
                    continue

                # Actually assign/remove roles
                try:
                    if roles_to_remove:
                        await discord_member.remove_roles(*roles_to_remove, reason='Role sync')
                    if roles_to_add:
                        await discord_member.add_roles(*roles_to_add, reason=f'Role sync: Torn position is {position}')
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

    logger.info('Role sync commands registered (/rolesync)')
