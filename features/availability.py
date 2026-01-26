"""
Availability Polling Module
Allows Verified members to indicate availability for war/chain planning using time blocks in Torn time (TCT = UTC).

Features:
- /avail_setup - Create availability poll (management only)
- /avail_summary - View availability summary with ASCII timetable
- /tct_now - Show current Torn time
- Button-based time block selection for verified members
"""

import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from utils.helpers import load_json, save_json, create_embed
from config import (
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    MANAGEMENT_ROLE_IDS
)


# Block labels (A-L for up to 12 blocks)
BLOCK_LABELS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L']


class AvailabilityManager:
    """Manages availability polls and member responses"""

    def __init__(self, bot):
        self.bot = bot
        self.polls_file = 'data/availability_polls.json'
        self.data_file = 'data/availability_data.json'
        self.polls = {}  # {date: poll_config}
        self.data = {}   # {date: {block_id: [user_ids]}}

    async def load_data(self):
        """Load polls and availability data from JSON files"""
        self.polls = load_json(self.polls_file, default={})
        self.data = load_json(self.data_file, default={})
        print(f'Loaded {len(self.polls)} availability polls')

    async def save_polls(self):
        """Save poll configurations to JSON"""
        save_json(self.polls_file, self.polls)

    async def save_data(self):
        """Save availability data to JSON"""
        save_json(self.data_file, self.data)

    def create_poll(self, date: str, channel_id: int, message_id: int,
                    start_time: str, end_time: str, block_length: int) -> List[Dict]:
        """
        Create a new availability poll

        Args:
            date: Date string (YYYY-MM-DD)
            channel_id: Discord channel ID where poll was posted
            message_id: Discord message ID of the poll
            start_time: Start time (HH:MM)
            end_time: End time (HH:MM)
            block_length: Hours per block

        Returns:
            List of block definitions
        """
        # Parse times
        start_h, start_m = map(int, start_time.split(':'))
        end_h, end_m = map(int, end_time.split(':'))

        # Calculate total hours
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        # Handle overnight (e.g., 22:00 to 06:00)
        if end_minutes <= start_minutes:
            end_minutes += 24 * 60

        total_minutes = end_minutes - start_minutes
        total_hours = total_minutes / 60

        # Generate blocks
        blocks = []
        current_minutes = start_minutes
        block_id = 0

        while current_minutes < end_minutes and block_id < 12:
            block_start_h = (current_minutes // 60) % 24
            block_start_m = current_minutes % 60

            next_minutes = current_minutes + (block_length * 60)
            block_end_h = (next_minutes // 60) % 24
            block_end_m = next_minutes % 60

            blocks.append({
                'id': block_id,
                'label': BLOCK_LABELS[block_id],
                'start': f'{block_start_h:02d}:{block_start_m:02d}',
                'end': f'{block_end_h:02d}:{block_end_m:02d}'
            })

            current_minutes = next_minutes
            block_id += 1

        # Store poll config
        self.polls[date] = {
            'channel_id': channel_id,
            'message_id': message_id,
            'start_time': start_time,
            'end_time': end_time,
            'block_length': block_length,
            'blocks': blocks,
            'created_at': datetime.now(timezone.utc).isoformat()
        }

        # Initialize empty data for this date
        self.data[date] = {str(b['id']): [] for b in blocks}

        return blocks

    def toggle_availability(self, date: str, block_id: int, user_id: int) -> bool:
        """
        Toggle a user's availability for a specific block

        Args:
            date: Date string
            block_id: Block ID (0-11)
            user_id: Discord user ID

        Returns:
            True if user is now available, False if removed
        """
        if date not in self.data:
            self.data[date] = {}

        block_key = str(block_id)
        if block_key not in self.data[date]:
            self.data[date][block_key] = []

        user_id_str = str(user_id)

        if user_id_str in self.data[date][block_key]:
            self.data[date][block_key].remove(user_id_str)
            return False
        else:
            self.data[date][block_key].append(user_id_str)
            return True

    def get_block_count(self, date: str, block_id: int) -> int:
        """Get number of users available for a block"""
        if date not in self.data:
            return 0
        block_key = str(block_id)
        if block_key not in self.data[date]:
            return 0
        return len(self.data[date][block_key])

    def get_user_selections(self, date: str, user_id: int) -> List[int]:
        """Get list of block IDs the user has selected"""
        if date not in self.data:
            return []

        user_id_str = str(user_id)
        selected = []
        for block_key, users in self.data[date].items():
            if user_id_str in users:
                selected.append(int(block_key))
        return selected

    def get_poll(self, date: str) -> Optional[Dict]:
        """Get poll configuration for a date"""
        return self.polls.get(date)

    def get_summary(self, date: str) -> Optional[Dict]:
        """
        Get availability summary for a date

        Returns:
            Dict with block counts and best windows, or None if no poll
        """
        poll = self.polls.get(date)
        if not poll:
            return None

        blocks = poll['blocks']
        block_counts = []

        for block in blocks:
            count = self.get_block_count(date, block['id'])
            block_counts.append({
                'id': block['id'],
                'label': block['label'],
                'start': block['start'],
                'end': block['end'],
                'count': count
            })

        # Sort by count descending to find best windows
        sorted_blocks = sorted(block_counts, key=lambda x: x['count'], reverse=True)
        best_windows = sorted_blocks[:3] if sorted_blocks else []

        return {
            'date': date,
            'blocks': block_counts,
            'best_windows': best_windows,
            'block_length': poll['block_length'],
            'start_time': poll['start_time'],
            'end_time': poll['end_time']
        }

    def generate_timetable(self, date: str) -> str:
        """
        Generate ASCII timetable for availability

        Returns:
            Formatted string with bar chart
        """
        summary = self.get_summary(date)
        if not summary:
            return "No poll data available."

        # Find max count for scaling
        max_count = max((b['count'] for b in summary['blocks']), default=1)
        if max_count == 0:
            max_count = 1

        lines = []
        for block in summary['blocks']:
            time_range = f"{block['start'][:2]}–{block['end'][:2]}"
            count = block['count']

            # Scale bars (1 block = roughly 1-2 people, max ~10 blocks)
            if count == 0:
                bar = '·'
            else:
                bar_length = max(1, min(10, (count * 10) // max_count + 1))
                bar = '█' * bar_length

            lines.append(f"{time_range}: {bar:<10} {count}")

        return '\n'.join(lines)


class AvailabilityView(discord.ui.View):
    """Persistent view with buttons for availability selection"""

    def __init__(self, manager: AvailabilityManager, date: str, blocks: List[Dict], verified_role_name: str):
        super().__init__(timeout=None)  # Persistent view
        self.manager = manager
        self.date = date
        self.verified_role_name = verified_role_name

        # Add buttons for each block (max 12)
        for block in blocks[:12]:
            button = AvailabilityButton(
                manager=manager,
                date=date,
                block=block,
                verified_role_name=verified_role_name
            )
            self.add_item(button)


class AvailabilityButton(discord.ui.Button):
    """Button for a single time block"""

    def __init__(self, manager: AvailabilityManager, date: str, block: Dict, verified_role_name: str):
        self.manager = manager
        self.date = date
        self.block = block
        self.verified_role_name = verified_role_name

        # Get current count
        count = manager.get_block_count(date, block['id'])

        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=f"{block['label']}: {block['start']}–{block['end']} ({count})",
            custom_id=f"avail_{date}_{block['id']}"
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle button click"""
        # Check if user has Verified role
        verified_role = discord.utils.get(interaction.user.roles, name=self.verified_role_name)
        if not verified_role:
            try:
                await interaction.user.send(
                    f"You must be **Verified** to set availability. "
                    f"Please use `/register` with your Torn ID first."
                )
            except discord.Forbidden:
                pass  # Can't DM user
            await interaction.response.defer()
            return

        # Toggle availability
        is_now_available = self.manager.toggle_availability(
            self.date,
            self.block['id'],
            interaction.user.id
        )
        await self.manager.save_data()

        # Update button label with new count
        count = self.manager.get_block_count(self.date, self.block['id'])
        self.label = f"{self.block['label']}: {self.block['start']}–{self.block['end']} ({count})"

        # Change button style based on user's selection
        user_selections = self.manager.get_user_selections(self.date, interaction.user.id)
        if self.block['id'] in user_selections:
            self.style = discord.ButtonStyle.success  # Green when selected
        else:
            self.style = discord.ButtonStyle.secondary  # Gray when not selected

        # Update the message
        await interaction.response.edit_message(view=self.view)

        # Send ephemeral feedback
        if is_now_available:
            status = f"✅ You're now **available** for {self.block['start']}–{self.block['end']} TCT"
        else:
            status = f"❌ You're now **unavailable** for {self.block['start']}–{self.block['end']} TCT"

        await interaction.followup.send(status, ephemeral=True)


def time_to_unix(date_str: str, time_str: str) -> int:
    """
    Convert date and TCT time to Unix timestamp

    Args:
        date_str: YYYY-MM-DD
        time_str: HH:MM

    Returns:
        Unix timestamp
    """
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


async def setup_availability_commands(bot, manager: AvailabilityManager, verified_role_name: str):
    """Register availability slash commands"""

    def is_management(interaction: discord.Interaction) -> bool:
        """Check if user has management permissions"""
        if interaction.user.guild_permissions.manage_guild:
            return True
        if MANAGEMENT_ROLE_IDS:
            user_role_ids = [role.id for role in interaction.user.roles]
            if any(role_id in MANAGEMENT_ROLE_IDS for role_id in user_role_ids):
                return True
        return False

    # ===== /avail_setup command =====
    @bot.tree.command(
        name='avail_setup',
        description='[Management] Create an availability poll for war/chain planning'
    )
    @app_commands.describe(
        date='Date for availability (YYYY-MM-DD)',
        start_time='Start time in TCT/UTC (HH:MM, e.g., 00:00)',
        end_time='End time in TCT/UTC (HH:MM, e.g., 24:00 or 23:59)',
        block_length='Hours per time block (1-6, default: 2)'
    )
    async def avail_setup(
        interaction: discord.Interaction,
        date: str,
        start_time: str,
        end_time: str,
        block_length: int = 2
    ):
        """Create an availability poll"""
        # Check permissions
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Validate block length
        if block_length < 1 or block_length > 6:
            embed = create_embed(
                title='Invalid Block Length',
                description='Block length must be between 1 and 6 hours.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Validate date format
        try:
            parsed_date = datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            embed = create_embed(
                title='Invalid Date',
                description='Please use YYYY-MM-DD format (e.g., 2026-01-30)',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Validate time format
        try:
            # Handle 24:00 as special case
            if end_time == '24:00':
                end_time = '23:59'

            datetime.strptime(start_time, '%H:%M')
            datetime.strptime(end_time, '%H:%M')
        except ValueError:
            embed = create_embed(
                title='Invalid Time',
                description='Please use HH:MM format (e.g., 00:00, 14:30)',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()

        # Create blocks
        blocks = manager.create_poll(
            date=date,
            channel_id=interaction.channel.id,
            message_id=0,  # Will update after sending
            start_time=start_time,
            end_time=end_time,
            block_length=block_length
        )

        if not blocks:
            embed = create_embed(
                title='Error',
                description='Failed to create time blocks. Check your time range.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Build block list with both local and TCT times
        block_list = []
        for block in blocks:
            unix_start = time_to_unix(date, block['start'])
            unix_end = time_to_unix(date, block['end'])
            # Show local time with TCT in parentheses
            block_list.append(
                f"**{block['label']}**: <t:{unix_start}:t> – <t:{unix_end}:t> "
                f"({block['start']}–{block['end']} TCT)"
            )

        # Create embed
        unix_date = time_to_unix(date, '00:00')
        embed = create_embed(
            title=f"🕒 Availability Poll – <t:{unix_date}:D>",
            description=(
                f"**Select ALL time blocks when you are available.**\n\n"
                f"Click buttons to toggle your availability.\n"
                f"Times shown are your **local time** with TCT (Torn City Time = UTC) in parentheses.\n\n"
                f"**Time Blocks:**\n" + '\n'.join(block_list)
            ),
            color=EMBED_COLOR_INFO
        )
        embed.set_footer(text=f"Date: {date} | Block size: {block_length}h | Only Verified members can vote")

        # Create view with buttons
        view = AvailabilityView(manager, date, blocks, verified_role_name)

        # Send message
        message = await interaction.followup.send(embed=embed, view=view)

        # Update poll with message ID
        manager.polls[date]['message_id'] = message.id
        await manager.save_polls()

        print(f'Availability poll created for {date} with {len(blocks)} blocks')

    # ===== /avail_summary command =====
    @bot.tree.command(
        name='avail_summary',
        description='View availability summary for a specific date'
    )
    @app_commands.describe(
        date='Date to check (YYYY-MM-DD)'
    )
    async def avail_summary(interaction: discord.Interaction, date: str):
        """Show availability summary with timetable"""
        await interaction.response.defer()

        summary = manager.get_summary(date)

        if not summary:
            embed = create_embed(
                title='No Poll Found',
                description=f'No availability poll exists for {date}.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.followup.send(embed=embed)
            return

        # Build block summary with local and TCT times
        block_lines = []
        for block in summary['blocks']:
            unix_start = time_to_unix(date, block['start'])
            block_lines.append(
                f"**{block['label']}** (<t:{unix_start}:t> / {block['start']}–{block['end']} TCT): "
                f"**{block['count']}** available"
            )

        # Build best windows
        best_lines = []
        for block in summary['best_windows']:
            if block['count'] > 0:
                unix_start = time_to_unix(date, block['start'])
                best_lines.append(
                    f"• **{block['label']}** (<t:{unix_start}:t> / {block['start']}–{block['end']} TCT) – "
                    f"**{block['count']}** members"
                )

        # Create embed
        unix_date = time_to_unix(date, '00:00')
        embed = create_embed(
            title=f"🧮 Availability Summary – <t:{unix_date}:D>",
            description='\n'.join(block_lines),
            color=EMBED_COLOR_SUCCESS
        )

        if best_lines:
            embed.add_field(
                name='⭐ Best Windows',
                value='\n'.join(best_lines),
                inline=False
            )
        else:
            embed.add_field(
                name='⭐ Best Windows',
                value='No members have voted yet.',
                inline=False
            )

        embed.set_footer(text=f"Times shown: Local time / TCT (Torn City Time = UTC)")

        # Generate ASCII timetable
        timetable = manager.generate_timetable(date)
        timetable_text = f"```\n🕒 Availability – {date} (Torn Time)\n\n{timetable}\n```"

        await interaction.followup.send(embed=embed, content=timetable_text)

    # ===== /tct_now command =====
    @bot.tree.command(
        name='tct_now',
        description='Show the current Torn City Time (TCT = UTC)'
    )
    async def tct_now(interaction: discord.Interaction):
        """Show current Torn time"""
        now = datetime.now(timezone.utc)
        unix = int(now.timestamp())

        embed = create_embed(
            title='🕒 Torn City Time (TCT)',
            description=(
                f"**Current TCT:** {now.strftime('%H:%M:%S')} ({now.strftime('%Y-%m-%d')})\n\n"
                f"**Your local time:** <t:{unix}:F>\n"
                f"**Relative:** <t:{unix}:R>"
            ),
            color=EMBED_COLOR_INFO
        )
        embed.set_footer(text="TCT = UTC/GMT | Torn always uses this timezone")

        await interaction.response.send_message(embed=embed)

    # ===== /avail_delete command =====
    @bot.tree.command(
        name='avail_delete',
        description='[Management] Delete an availability poll'
    )
    @app_commands.describe(
        date='Date of poll to delete (YYYY-MM-DD)'
    )
    async def avail_delete(interaction: discord.Interaction, date: str):
        """Delete an availability poll"""
        if not is_management(interaction):
            embed = create_embed(
                title='Permission Denied',
                description='You do not have permission to use this command.',
                color=EMBED_COLOR_CRITICAL
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if date not in manager.polls:
            embed = create_embed(
                title='Not Found',
                description=f'No availability poll exists for {date}.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Delete poll and data
        del manager.polls[date]
        if date in manager.data:
            del manager.data[date]

        await manager.save_polls()
        await manager.save_data()

        embed = create_embed(
            title='✅ Poll Deleted',
            description=f'Availability poll for {date} has been deleted.',
            color=EMBED_COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ===== /my_availability command =====
    @bot.tree.command(
        name='my_availability',
        description='View your availability selections for a date'
    )
    @app_commands.describe(
        date='Date to check (YYYY-MM-DD)'
    )
    async def my_availability(interaction: discord.Interaction, date: str):
        """View your own availability selections"""
        poll = manager.get_poll(date)

        if not poll:
            embed = create_embed(
                title='No Poll Found',
                description=f'No availability poll exists for {date}.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        selections = manager.get_user_selections(date, interaction.user.id)
        blocks = poll['blocks']

        if not selections:
            embed = create_embed(
                title='Your Availability',
                description=f'You have not selected any time blocks for {date}.',
                color=EMBED_COLOR_INFO
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Build selection list
        selected_blocks = []
        for block in blocks:
            if block['id'] in selections:
                unix_start = time_to_unix(date, block['start'])
                selected_blocks.append(
                    f"✅ **{block['label']}**: <t:{unix_start}:t> ({block['start']}–{block['end']} TCT)"
                )

        unix_date = time_to_unix(date, '00:00')
        embed = create_embed(
            title=f'Your Availability – <t:{unix_date}:D>',
            description='\n'.join(selected_blocks),
            color=EMBED_COLOR_SUCCESS
        )
        embed.set_footer(text=f"You've selected {len(selections)} time block(s)")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    print('Availability commands registered (/avail_setup, /avail_summary, /avail_delete, /my_availability, /tct_now)')


async def restore_availability_views(bot, manager: AvailabilityManager, verified_role_name: str):
    """
    Restore persistent views for existing polls after bot restart.
    Call this in on_ready after loading data.
    """
    for date, poll in manager.polls.items():
        channel_id = poll.get('channel_id')
        message_id = poll.get('message_id')
        blocks = poll.get('blocks', [])

        if not channel_id or not message_id or not blocks:
            continue

        try:
            channel = bot.get_channel(channel_id)
            if not channel:
                continue

            # Re-create view and add to bot
            view = AvailabilityView(manager, date, blocks, verified_role_name)
            bot.add_view(view, message_id=message_id)
            print(f'Restored availability view for {date}')

        except Exception as e:
            print(f'Failed to restore availability view for {date}: {e}')
