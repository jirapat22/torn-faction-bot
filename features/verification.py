"""
Verification Module
Allows members to register/verify their Torn account and get roles

Features:
- /register [torn-id] - Register your Torn account
- /verify options - Configure verification settings (admin only)
- Auto-rename to Torn name + ID
- Auto-assign verified role
"""

import discord
from discord import app_commands
from discord.ext import tasks
from datetime import datetime, timezone
from utils.helpers import load_json, save_json, create_embed
from utils.torn_api import fetch_player_data
from config import (
    GUILD_ID,
    TORN_FACTION_ID,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    EMBED_COLOR_INFO,
    MANAGEMENT_ROLE_IDS
)


class VerificationSystem:
    """Handles member verification and role assignment"""

    def __init__(self, bot):
        self.bot = bot
        self.config_file = 'data/verification_config.json'
        self.verified_users_file = 'data/verified_users.json'
        self.config = {}
        self.verified_users = {}
        self.inactivity_tracker = None  # Set by bot.py to keep databases in sync

    async def load_config(self):
        """Load verification config from JSON file"""
        default_config = {
            'verified_role_id': None,
            'guest_role_id': None,
            'change_name': True,
            'append_id': True,
            'append_faction_tag': False,
            'require_faction': False,
            'daily_check': False
        }
        self.config = load_json(self.config_file, default=default_config)

        # Ensure all keys exist
        for key, value in default_config.items():
            if key not in self.config:
                self.config[key] = value

        self.verified_users = load_json(self.verified_users_file, default={})
        print(f'Loaded verification config, {len(self.verified_users)} verified users')

    async def save_config(self):
        """Save verification config to JSON file"""
        save_json(self.config_file, self.config)

    async def save_verified_users(self):
        """Save verified users to JSON file"""
        save_json(self.verified_users_file, self.verified_users)

    async def verify_torn_id(self, torn_id: int):
        """
        Verify a Torn ID exists and get player info

        Args:
            torn_id: The Torn player ID

        Returns:
            dict with player info or None if invalid
        """
        player_data = await fetch_player_data(torn_id)

        if not player_data:
            return None

        # Check for error response
        if 'error' in player_data:
            return None

        return {
            'id': player_data.get('player_id', torn_id),
            'name': player_data.get('name', 'Unknown'),
            'level': player_data.get('level', 0),
            'faction_id': player_data.get('faction', {}).get('faction_id', 0),
            'faction_name': player_data.get('faction', {}).get('faction_name', ''),
            'faction_tag': player_data.get('faction', {}).get('faction_tag', ''),
            'last_action': player_data.get('last_action', {}).get('timestamp', 0)
        }

    async def register_user(self, interaction: discord.Interaction, torn_id: int):
        """
        Register a Discord user with their Torn ID

        Args:
            interaction: Discord interaction
            torn_id: Torn player ID

        Returns:
            Tuple of (success: bool, message: str)
        """
        member = interaction.user
        guild = interaction.guild

        # Check if already verified
        discord_id = str(member.id)
        if discord_id in self.verified_users:
            existing_torn_id = self.verified_users[discord_id].get('torn_id')
            return False, f'You are already verified as Torn ID {existing_torn_id}. Contact an admin if you need to change this.'

        # Check if Torn ID is already registered to someone else
        for d_id, data in self.verified_users.items():
            if data.get('torn_id') == torn_id and d_id != discord_id:
                return False, f'Torn ID {torn_id} is already registered to another Discord user.'

        # Verify Torn ID exists
        player_info = await self.verify_torn_id(torn_id)
        if not player_info:
            return False, f'Could not find Torn player with ID {torn_id}. Please check the ID and try again.'

        # Check faction requirement if enabled
        if self.config.get('require_faction'):
            from config import TORN_FACTION_ID
            if player_info['faction_id'] != TORN_FACTION_ID:
                return False, f'You must be a member of our faction to verify. Your faction: {player_info["faction_name"] or "None"}'

        # Build new nickname
        new_name = player_info['name']
        if self.config.get('append_id', True):
            new_name = f"{player_info['name']} [{torn_id}]"
        if self.config.get('append_faction_tag', False) and player_info['faction_tag']:
            new_name = f"[{player_info['faction_tag']}] {new_name}"

        # Try to rename user
        rename_success = False
        if self.config.get('change_name', True):
            try:
                await member.edit(nick=new_name[:32])  # Discord max nick length is 32
                rename_success = True
            except discord.Forbidden:
                pass  # Can't rename this user (maybe owner or higher role)

        # Give verified role
        role_success = False
        verified_role_id = self.config.get('verified_role_id')
        if verified_role_id:
            role = guild.get_role(verified_role_id)
            if role:
                try:
                    await member.add_roles(role)
                    role_success = True
                except discord.Forbidden:
                    pass  # Can't add role

        # Save verification
        self.verified_users[discord_id] = {
            'torn_id': torn_id,
            'torn_name': player_info['name'],
            'faction_id': player_info['faction_id'],
            'faction_name': player_info['faction_name'],
            'verified_at': datetime.now(timezone.utc).isoformat(),
            'verified_by': 'self'
        }
        await self.save_verified_users()

        # Also save to inactivity tracker database (keep both in sync)
        if self.inactivity_tracker:
            self.inactivity_tracker.user_mapping[discord_id] = str(torn_id)
            await self.inactivity_tracker.save_user_mapping()

        # Build response message
        status_parts = []
        if rename_success:
            status_parts.append(f'Renamed to **{new_name[:32]}**')
        elif self.config.get('change_name', True):
            status_parts.append('Could not rename (permission issue)')

        if role_success:
            role = guild.get_role(verified_role_id)
            status_parts.append(f'Given role **{role.name}**')
        elif verified_role_id:
            status_parts.append('Could not assign role (permission issue or role not found)')
        else:
            status_parts.append('No verified role configured')

        status_msg = '\n'.join(status_parts) if status_parts else 'Verification recorded'

        return True, f'Successfully verified as **{player_info["name"]}** [{torn_id}]\n\n{status_msg}'

    async def unregister_user(self, discord_id: str, guild=None):
        """Remove a user's verification from all databases"""
        removed = False

        # Remove from verification database
        if discord_id in self.verified_users:
            del self.verified_users[discord_id]
            await self.save_verified_users()
            removed = True

        # Also remove from inactivity tracker database
        if self.inactivity_tracker and discord_id in self.inactivity_tracker.user_mapping:
            del self.inactivity_tracker.user_mapping[discord_id]
            await self.inactivity_tracker.save_user_mapping()
            removed = True

        # Remove verified role from the member
        if guild and self.config.get('verified_role_id'):
            try:
                member = guild.get_member(int(discord_id))
                if member:
                    role = guild.get_role(self.config['verified_role_id'])
                    if role and role in member.roles:
                        await member.remove_roles(role)
            except Exception:
                pass  # Best effort role removal

        return removed


async def setup_verification_commands(bot, verification_system: VerificationSystem):
    """Set up verification slash commands"""

    @bot.tree.command(
        name='register',
        description='Register your Torn account to get verified'
    )
    @app_commands.describe(torn_id='Your Torn player ID (found in your Torn profile URL)')
    async def register_command(interaction: discord.Interaction, torn_id: int):
        """Register command - link Discord to Torn account"""
        await interaction.response.defer(ephemeral=True)

        success, message = await verification_system.register_user(interaction, torn_id)

        if success:
            embed = create_embed(
                title='✅ Verification Successful',
                description=message,
                color=EMBED_COLOR_SUCCESS
            )
        else:
            embed = create_embed(
                title='❌ Verification Failed',
                description=message,
                color=EMBED_COLOR_CRITICAL
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(
        name='whois',
        description='Look up a member\'s Torn info'
    )
    @app_commands.describe(member='The Discord member to look up')
    async def whois_command(interaction: discord.Interaction, member: discord.Member = None):
        """Look up a member's Torn verification info"""
        await interaction.response.defer()

        target = member or interaction.user
        discord_id = str(target.id)

        if discord_id in verification_system.verified_users:
            data = verification_system.verified_users[discord_id]
            torn_id = data.get('torn_id')
            torn_name = data.get('torn_name', 'Unknown')
            faction_name = data.get('faction_name', 'None')
            verified_at = data.get('verified_at', 'Unknown')

            # Get current player info
            player_info = await verification_system.verify_torn_id(torn_id)

            embed = create_embed(
                title=f'Torn Profile: {torn_name} [{torn_id}]',
                description=f'[View on Torn](https://www.torn.com/profiles.php?XID={torn_id})',
                color=EMBED_COLOR_INFO,
                fields=[
                    {'name': 'Discord', 'value': target.mention, 'inline': True},
                    {'name': 'Torn ID', 'value': str(torn_id), 'inline': True},
                    {'name': 'Faction', 'value': faction_name or 'None', 'inline': True}
                ]
            )

            if player_info:
                embed.add_field(name='Level', value=str(player_info['level']), inline=True)
                embed.add_field(name='Current Faction', value=player_info['faction_name'] or 'None', inline=True)

        else:
            embed = create_embed(
                title='Not Verified',
                description=f'{target.mention} has not verified their Torn account.\n\nUse `/register [torn-id]` to verify.',
                color=EMBED_COLOR_WARNING
            )

        await interaction.followup.send(embed=embed)

    # Verification options command (admin only)
    @bot.tree.command(
        name='verify',
        description='Verification settings and admin commands'
    )
    @app_commands.describe(
        action='Action to perform',
        role='Role to set (for setrole action)',
        member='Member to verify/unverify (for admin actions)',
        torn_id='Torn ID (for manual verification)',
        setting='Setting name (for toggle action)',
        value='Value to set (for toggle action)'
    )
    @app_commands.choices(action=[
        app_commands.Choice(name='options - View current settings', value='options'),
        app_commands.Choice(name='setrole - Set verified role', value='setrole'),
        app_commands.Choice(name='setguest - Set guest role', value='setguest'),
        app_commands.Choice(name='toggle - Toggle a setting', value='toggle'),
        app_commands.Choice(name='manual - Manually verify a member', value='manual'),
        app_commands.Choice(name='unverify - Remove verification from member', value='unverify'),
        app_commands.Choice(name='fixrole - Give verified role to registered users missing it', value='fixrole'),
        app_commands.Choice(name='bulksync - Re-verify members by matching nicknames to faction', value='bulksync'),
        app_commands.Choice(name='stats - Show verification statistics', value='stats')
    ])
    @app_commands.choices(setting=[
        app_commands.Choice(name='change_name - Change Discord name to Torn name', value='change_name'),
        app_commands.Choice(name='append_id - Append Torn ID to name', value='append_id'),
        app_commands.Choice(name='append_faction_tag - Append faction tag to name', value='append_faction_tag'),
        app_commands.Choice(name='require_faction - Require faction membership', value='require_faction'),
        app_commands.Choice(name='daily_check - Daily verification check', value='daily_check')
    ])
    async def verify_command(
        interaction: discord.Interaction,
        action: str,
        role: discord.Role = None,
        member: discord.Member = None,
        torn_id: int = None,
        setting: str = None,
        value: bool = None
    ):
        """Verification admin command"""
        # Check if user has management role OR admin permission
        has_permission = False

        # Check Discord admin permission
        if interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator:
            has_permission = True

        # Check management roles
        if MANAGEMENT_ROLE_IDS:
            user_role_ids = [r.id for r in interaction.user.roles]
            if any(role_id in MANAGEMENT_ROLE_IDS for role_id in user_role_ids):
                has_permission = True

        if not has_permission:
            await interaction.response.send_message(
                'You do not have permission to use this command.',
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        if action == 'options':
            # Show current settings
            verified_role_id = verification_system.config.get('verified_role_id')
            guest_role_id = verification_system.config.get('guest_role_id')

            verified_role = guild.get_role(verified_role_id) if verified_role_id else None
            guest_role = guild.get_role(guest_role_id) if guest_role_id else None

            settings_text = (
                f"**Verified Role:** {verified_role.mention if verified_role else 'Not set'}\n"
                f"**Guest Role:** {guest_role.mention if guest_role else 'Not set'}\n\n"
                f"**Settings:**\n"
                f"• Change name to Torn name: {'✅' if verification_system.config.get('change_name', True) else '❌'}\n"
                f"• Append Torn ID to name: {'✅' if verification_system.config.get('append_id', True) else '❌'}\n"
                f"• Append faction tag: {'✅' if verification_system.config.get('append_faction_tag', False) else '❌'}\n"
                f"• Require faction membership: {'✅' if verification_system.config.get('require_faction', False) else '❌'}\n"
                f"• Daily verification check: {'✅' if verification_system.config.get('daily_check', False) else '❌'}\n"
            )

            embed = create_embed(
                title='Verification Settings',
                description=settings_text,
                color=EMBED_COLOR_INFO
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'setrole':
            if not role:
                await interaction.followup.send('Please specify a role.', ephemeral=True)
                return

            verification_system.config['verified_role_id'] = role.id
            await verification_system.save_config()

            embed = create_embed(
                title='✅ Verified Role Set',
                description=f'Verified role set to {role.mention}',
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'setguest':
            if not role:
                await interaction.followup.send('Please specify a role.', ephemeral=True)
                return

            verification_system.config['guest_role_id'] = role.id
            await verification_system.save_config()

            embed = create_embed(
                title='✅ Guest Role Set',
                description=f'Guest role set to {role.mention}',
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'toggle':
            if not setting:
                await interaction.followup.send('Please specify a setting to toggle.', ephemeral=True)
                return

            if value is None:
                # Toggle current value
                current = verification_system.config.get(setting, False)
                verification_system.config[setting] = not current
            else:
                verification_system.config[setting] = value

            await verification_system.save_config()

            new_value = verification_system.config[setting]
            embed = create_embed(
                title='✅ Setting Updated',
                description=f'**{setting}** is now **{"enabled" if new_value else "disabled"}**',
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'manual':
            if not member or not torn_id:
                await interaction.followup.send('Please specify both a member and torn_id.', ephemeral=True)
                return

            # Verify the Torn ID
            player_info = await verification_system.verify_torn_id(torn_id)
            if not player_info:
                await interaction.followup.send(f'Could not find Torn player with ID {torn_id}.', ephemeral=True)
                return

            # Save verification to both databases
            discord_id = str(member.id)
            verification_system.verified_users[discord_id] = {
                'torn_id': torn_id,
                'torn_name': player_info['name'],
                'faction_id': player_info['faction_id'],
                'faction_name': player_info['faction_name'],
                'verified_at': datetime.now(timezone.utc).isoformat(),
                'verified_by': str(interaction.user.id)
            }
            await verification_system.save_verified_users()

            # Also save to inactivity tracker
            if verification_system.inactivity_tracker:
                verification_system.inactivity_tracker.user_mapping[discord_id] = str(torn_id)
                await verification_system.inactivity_tracker.save_user_mapping()

            # Try to rename and add role
            new_name = f"{player_info['name']} [{torn_id}]"
            try:
                if verification_system.config.get('change_name', True):
                    await member.edit(nick=new_name[:32])
            except discord.Forbidden:
                pass

            verified_role_id = verification_system.config.get('verified_role_id')
            if verified_role_id:
                role = guild.get_role(verified_role_id)
                if role:
                    try:
                        await member.add_roles(role)
                    except discord.Forbidden:
                        pass

            embed = create_embed(
                title='✅ Manual Verification Complete',
                description=f'{member.mention} verified as **{player_info["name"]}** [{torn_id}]',
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'unverify':
            if not member:
                await interaction.followup.send('Please specify a member.', ephemeral=True)
                return

            discord_id = str(member.id)

            # Check both databases for this user
            old_data = verification_system.verified_users.get(discord_id)
            in_inactivity = (verification_system.inactivity_tracker and
                           discord_id in verification_system.inactivity_tracker.user_mapping)

            if old_data or in_inactivity:
                # Get info for the response message
                torn_info = ''
                if old_data:
                    torn_info = f'Was: **{old_data.get("torn_name")}** [{old_data.get("torn_id")}]'
                elif in_inactivity:
                    torn_id = verification_system.inactivity_tracker.user_mapping.get(discord_id)
                    torn_info = f'Was: Torn ID {torn_id}'

                await verification_system.unregister_user(discord_id, guild=guild)

                embed = create_embed(
                    title='✅ Verification Removed',
                    description=f'Removed verification for {member.mention}\n{torn_info}\n'
                               f'Verified role has been removed.',
                    color=EMBED_COLOR_SUCCESS
                )
            else:
                embed = create_embed(
                    title='Not Verified',
                    description=f'{member.mention} was not found in any database.',
                    color=EMBED_COLOR_WARNING
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'fixrole':
            # Give verified role to all registered users who don't have it
            verified_role_id = verification_system.config.get('verified_role_id')
            if not verified_role_id:
                await interaction.followup.send('No verified role is configured. Use `/verify setrole` first.', ephemeral=True)
                return

            role = guild.get_role(verified_role_id)
            if not role:
                await interaction.followup.send('Verified role not found. It may have been deleted.', ephemeral=True)
                return

            fixed_count = 0
            failed_count = 0
            already_has_role = 0

            for discord_id, data in verification_system.verified_users.items():
                try:
                    member_obj = guild.get_member(int(discord_id))
                    if not member_obj:
                        continue

                    if role in member_obj.roles:
                        already_has_role += 1
                        continue

                    await member_obj.add_roles(role)
                    fixed_count += 1
                except Exception:
                    failed_count += 1

            embed = create_embed(
                title='✅ Role Fix Complete',
                description=f'**Fixed:** {fixed_count} members given the {role.mention} role\n'
                           f'**Already had role:** {already_has_role}\n'
                           f'**Failed:** {failed_count}',
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'bulksync':
            # Re-verify members by matching Discord nicknames to Torn faction members
            import re
            from utils.torn_api import fetch_faction_members

            faction_members = await fetch_faction_members()
            if not faction_members:
                await interaction.followup.send('Could not fetch faction members from Torn API.', ephemeral=True)
                return

            # Build lookup: torn_id -> member_data
            torn_lookup = {}
            name_lookup = {}  # torn_name_lower -> {torn_id, data}
            for tid, mdata in faction_members.items():
                torn_lookup[tid] = mdata
                name_lookup[mdata.get('name', '').lower()] = {'torn_id': tid, 'data': mdata}

            synced = 0
            already_verified = 0
            not_matched = []

            for discord_member in guild.members:
                if discord_member.bot:
                    continue

                discord_id = str(discord_member.id)

                # Skip if already verified
                if discord_id in verification_system.verified_users:
                    already_verified += 1
                    continue

                # Try to extract Torn ID from nickname like "Name [12345]"
                display = discord_member.nick or discord_member.display_name
                id_match = re.search(r'\[(\d+)\]', display)

                matched_tid = None
                matched_data = None

                if id_match:
                    potential_tid = id_match.group(1)
                    if potential_tid in torn_lookup:
                        matched_tid = potential_tid
                        matched_data = torn_lookup[potential_tid]

                # Fallback: try matching by name
                if not matched_tid:
                    # Try exact name match against faction members
                    clean_name = re.sub(r'\s*\[\d+\]', '', display).strip().lower()
                    if clean_name in name_lookup:
                        matched_tid = name_lookup[clean_name]['torn_id']
                        matched_data = name_lookup[clean_name]['data']

                if matched_tid and matched_data:
                    # Register this member
                    verification_system.verified_users[discord_id] = {
                        'torn_id': int(matched_tid),
                        'torn_name': matched_data.get('name', ''),
                        'faction_id': int(TORN_FACTION_ID),
                        'faction_name': '',  # Not available from faction members endpoint
                        'verified_at': datetime.now(timezone.utc).isoformat(),
                        'verified_by': 'bulksync'
                    }

                    # Also save to inactivity tracker
                    if verification_system.inactivity_tracker:
                        verification_system.inactivity_tracker.user_mapping[discord_id] = matched_tid

                    synced += 1
                else:
                    if not discord_member.bot:
                        not_matched.append(discord_member.display_name)

            # Save all at once
            await verification_system.save_verified_users()
            if verification_system.inactivity_tracker:
                await verification_system.inactivity_tracker.save_user_mapping()

            unmatched_text = ''
            if not_matched:
                unmatched_text = f'\n\n**Could not match ({len(not_matched)}):**\n'
                unmatched_text += ', '.join(not_matched[:15])
                if len(not_matched) > 15:
                    unmatched_text += f'\n...and {len(not_matched) - 15} more'

            embed = create_embed(
                title='Bulk Sync Complete',
                description=(
                    f'**Newly verified:** {synced}\n'
                    f'**Already verified:** {already_verified}\n'
                    f'**Could not match:** {len(not_matched)}'
                    f'{unmatched_text}'
                ),
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == 'stats':
            total_verified = len(verification_system.verified_users)
            total_members = guild.member_count

            rate = f'{(total_verified / total_members * 100):.1f}%' if total_members > 0 else '0%'
            embed = create_embed(
                title='Verification Statistics',
                description=f'**Verified Members:** {total_verified}\n'
                           f'**Total Members:** {total_members}\n'
                           f'**Verification Rate:** {rate}',
                color=EMBED_COLOR_INFO
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    print('Verification commands registered (/register, /whois, /verify)')
