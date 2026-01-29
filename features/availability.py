"""
Availability Polling Module
Allows Verified members to indicate availability for war/chain planning using time blocks in Torn time (TCT = UTC).

Features:
- /avail_setup - Create availability poll (management only)
- /avail_summary - View availability summary with ASCII timetable
- /tct_now - Show current Torn time
- Button-based time block selection for verified members
- Multi-day support for events spanning multiple days
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


# Block labels (A-X for up to 24 blocks)
BLOCK_LABELS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L',
                'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X']


def parse_date(date_str: str) -> datetime:
    """Parse DD-MM-YYYY date string to datetime"""
    return datetime.strptime(date_str, '%d-%m-%Y')


def format_date(dt: datetime) -> str:
    """Format datetime to DD-MM-YYYY string"""
    return dt.strftime('%d-%m-%Y')


def date_to_internal(date_str: str) -> str:
    """Convert DD-MM-YYYY to internal YYYY-MM-DD format for storage"""
    dt = parse_date(date_str)
    return dt.strftime('%Y-%m-%d')


def internal_to_display(internal_str: str) -> str:
    """Convert internal YYYY-MM-DD to DD-MM-YYYY for display"""
    dt = datetime.strptime(internal_str, '%Y-%m-%d')
    return dt.strftime('%d-%m-%Y')


class AvailabilityManager:
    """Manages availability polls and member responses"""

    def __init__(self, bot):
        self.bot = bot
        self.polls_file = 'data/availability_polls.json'
        self.data_file = 'data/availability_data.json'
        self.polls = {}  # {poll_id: poll_config}
        self.data = {}   # {poll_id: {block_id: [user_ids]}}

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

    def create_poll(self, start_date: str, end_date: str, channel_id: int, message_id: int,
                    start_time: str, end_time: str, block_length: int) -> tuple:
        """
        Create a new availability poll (supports multi-day)

        Args:
            start_date: Start date (DD-MM-YYYY)
            end_date: End date (DD-MM-YYYY)
            channel_id: Discord channel ID where poll was posted
            message_id: Discord message ID of the poll
            start_time: Start time on first day (HH:MM)
            end_time: End time on last day (HH:MM)
            block_length: Hours per block

        Returns:
            Tuple of (poll_id, blocks list)
        """
        # Parse dates
        start_dt = parse_date(start_date)
        end_dt = parse_date(end_date)

        # Parse times
        start_h, start_m = map(int, start_time.split(':'))
        end_h, end_m = map(int, end_time.split(':'))

        # Create datetime objects in UTC
        start_datetime = start_dt.replace(hour=start_h, minute=start_m, tzinfo=timezone.utc)
        end_datetime = end_dt.replace(hour=end_h, minute=end_m, tzinfo=timezone.utc)

        # Handle end time of 00:00 meaning end of day
        if end_h == 0 and end_m == 0:
            end_datetime = end_datetime + timedelta(days=1)

        # Generate blocks
        blocks = []
        current_dt = start_datetime
        block_id = 0

        while current_dt < end_datetime and block_id < 24:
            block_end_dt = current_dt + timedelta(hours=block_length)
            if block_end_dt > end_datetime:
                block_end_dt = end_datetime

            blocks.append({
                'id': block_id,
                'label': BLOCK_LABELS[block_id],
                'start_unix': int(current_dt.timestamp()),
                'end_unix': int(block_end_dt.timestamp()),
                'start_time': current_dt.strftime('%H:%M'),
                'end_time': block_end_dt.strftime('%H:%M'),
                'date': current_dt.strftime('%d-%m-%Y')
            })

            current_dt = block_end_dt
            block_id += 1

        # Create unique poll ID (append suffix if dates already used)
        base_id = f"{date_to_internal(start_date)}_{date_to_internal(end_date)}"
        poll_id = base_id
        suffix = 2
        while poll_id in self.polls:
            poll_id = f"{base_id}_{suffix}"
            suffix += 1

        # Store poll config
        self.polls[poll_id] = {
            'channel_id': channel_id,
            'message_id': message_id,
            'start_date': start_date,
            'end_date': end_date,
            'start_time': start_time,
            'end_time': end_time,
            'block_length': block_length,
            'blocks': blocks,
            'created_at': datetime.now(timezone.utc).isoformat()
        }

        # Initialize empty data for this poll
        self.data[poll_id] = {str(b['id']): [] for b in blocks}

        return poll_id, blocks

    def toggle_availability(self, poll_id: str, block_id: int, user_id: int) -> bool:
        """
        Toggle a user's availability for a specific block

        Returns:
            True if user is now available, False if removed
        """
        if poll_id not in self.data:
            self.data[poll_id] = {}

        block_key = str(block_id)
        if block_key not in self.data[poll_id]:
            self.data[poll_id][block_key] = []

        user_id_str = str(user_id)

        if user_id_str in self.data[poll_id][block_key]:
            self.data[poll_id][block_key].remove(user_id_str)
            return False
        else:
            self.data[poll_id][block_key].append(user_id_str)
            return True

    def get_block_count(self, poll_id: str, block_id: int) -> int:
        """Get number of users available for a block"""
        if poll_id not in self.data:
            return 0
        block_key = str(block_id)
        if block_key not in self.data[poll_id]:
            return 0
        return len(self.data[poll_id][block_key])

    def get_user_selections(self, poll_id: str, user_id: int) -> List[int]:
        """Get list of block IDs the user has selected"""
        if poll_id not in self.data:
            return []

        user_id_str = str(user_id)
        selected = []
        for block_key, users in self.data[poll_id].items():
            if user_id_str in users:
                selected.append(int(block_key))
        return selected

    def get_poll(self, poll_id: str) -> Optional[Dict]:
        """Get poll configuration by ID"""
        return self.polls.get(poll_id)

    def find_poll_by_date(self, date: str) -> Optional[str]:
        """Find a poll that includes the given date (DD-MM-YYYY)"""
        target_dt = parse_date(date)
        for poll_id, poll in self.polls.items():
            start_dt = parse_date(poll['start_date'])
            end_dt = parse_date(poll['end_date'])
            if start_dt <= target_dt <= end_dt:
                return poll_id
        return None

    def get_summary(self, poll_id: str) -> Optional[Dict]:
        """
        Get availability summary for a poll

        Returns:
            Dict with block counts and best windows, or None if no poll
        """
        poll = self.polls.get(poll_id)
        if not poll:
            return None

        blocks = poll['blocks']
        block_counts = []

        for block in blocks:
            count = self.get_block_count(poll_id, block['id'])
            block_counts.append({
                'id': block['id'],
                'label': block['label'],
                'start_unix': block['start_unix'],
                'end_unix': block['end_unix'],
                'start_time': block['start_time'],
                'end_time': block['end_time'],
                'date': block['date'],
                'count': count
            })

        # Sort by count descending to find best windows
        sorted_blocks = sorted(block_counts, key=lambda x: x['count'], reverse=True)
        best_windows = sorted_blocks[:3] if sorted_blocks else []

        return {
            'poll_id': poll_id,
            'start_date': poll['start_date'],
            'end_date': poll['end_date'],
            'blocks': block_counts,
            'best_windows': best_windows,
            'block_length': poll['block_length']
        }

    def generate_timetable(self, poll_id: str) -> str:
        """
        Generate ASCII timetable for availability

        Returns:
            Formatted string with bar chart
        """
        summary = self.get_summary(poll_id)
        if not summary:
            return "No poll data available."

        # Find max count for scaling
        max_count = max((b['count'] for b in summary['blocks']), default=1)
        if max_count == 0:
            max_count = 1

        lines = []
        current_date = None

        for block in summary['blocks']:
            # Add date header if date changed
            if block['date'] != current_date:
                if current_date is not None:
                    lines.append("")  # Blank line between days
                lines.append(f"─── {block['date']} ───")
                current_date = block['date']

            time_range = f"{block['start_time']}–{block['end_time']}"
            count = block['count']

            # Scale bars
            if count == 0:
                bar = '·'
            else:
                bar_length = max(1, min(10, (count * 10) // max_count + 1))
                bar = '█' * bar_length

            lines.append(f"{time_range}: {bar:<10} {count}")

        return '\n'.join(lines)


class AvailabilityView(discord.ui.View):
    """Persistent view with buttons for availability selection"""

    def __init__(self, manager: AvailabilityManager, poll_id: str, blocks: List[Dict], verified_role_name: str):
        super().__init__(timeout=None)  # Persistent view
        self.manager = manager
        self.poll_id = poll_id
        self.verified_role_name = verified_role_name

        # Add buttons for each block (max 24, but Discord limits to 25 components / 5 rows)
        # Use max 20 buttons (4 rows of 5)
        for block in blocks[:20]:
            button = AvailabilityButton(
                manager=manager,
                poll_id=poll_id,
                block=block,
                verified_role_name=verified_role_name
            )
            self.add_item(button)


class AvailabilityButton(discord.ui.Button):
    """Button for a single time block"""

    def __init__(self, manager: AvailabilityManager, poll_id: str, block: Dict, verified_role_name: str):
        self.manager = manager
        self.poll_id = poll_id
        self.block = block
        self.verified_role_name = verified_role_name

        # Get current count
        count = manager.get_block_count(poll_id, block['id'])

        # Short label for button (just letter and count)
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=f"{block['label']} ({count})",
            custom_id=f"avail_{poll_id}_{block['id']}"
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle button click"""
        # Check if user has Verified role
        verified_role = discord.utils.get(interaction.user.roles, name=self.verified_role_name)
        if not verified_role:
            await interaction.response.send_message(
                f"You must have the **{self.verified_role_name}** role to set availability. "
                f"Please use `/register` with your Torn ID first.",
                ephemeral=True
            )
            return

        try:
            # Toggle availability
            is_now_available = self.manager.toggle_availability(
                self.poll_id,
                self.block['id'],
                interaction.user.id
            )
            await self.manager.save_data()

            # Update all button labels with current counts (visible to everyone)
            for item in self.view.children:
                if isinstance(item, AvailabilityButton):
                    btn_count = self.manager.get_block_count(self.poll_id, item.block['id'])
                    item.label = f"{item.block['label']} ({btn_count})"
                    # Keep all buttons neutral gray - style is shared across all users
                    item.style = discord.ButtonStyle.secondary

            # Update the message
            await interaction.response.edit_message(view=self.view)

            # Send ephemeral feedback with both local and TCT time
            time_display = f"<t:{self.block['start_unix']}:t> ({self.block['start_time']}–{self.block['end_time']} TCT)"
            if is_now_available:
                status = f"✅ You're now **available** for {time_display}"
            else:
                status = f"❌ You're now **unavailable** for {time_display}"

            await interaction.followup.send(status, ephemeral=True)

        except discord.NotFound:
            # Poll message was deleted
            await interaction.response.send_message(
                "This availability poll no longer exists.", ephemeral=True
            )
        except Exception as e:
            print(f'Error in availability button callback: {e}')
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


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
        start_date='Start date (DD-MM-YYYY)',
        start_time='Start time in TCT/UTC (HH:MM, e.g., 00:00)',
        end_time='End time in TCT/UTC (HH:MM, e.g., 24:00)',
        end_date='End date if different from start (DD-MM-YYYY) - leave empty for same day',
        block_length='Hours per time block (1-6, default: 2)'
    )
    async def avail_setup(
        interaction: discord.Interaction,
        start_date: str,
        start_time: str,
        end_time: str,
        end_date: Optional[str] = None,
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

        # Default end_date to start_date if not provided
        if not end_date:
            end_date = start_date

        # Validate date format (DD-MM-YYYY)
        try:
            start_dt = parse_date(start_date)
            end_dt = parse_date(end_date)
        except ValueError:
            embed = create_embed(
                title='Invalid Date',
                description='Please use DD-MM-YYYY format (e.g., 30-01-2026)',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Validate end_date >= start_date
        if end_dt < start_dt:
            embed = create_embed(
                title='Invalid Date Range',
                description='End date must be on or after start date.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Validate time format
        try:
            # Handle 24:00 as special case (means end of day = 00:00 next day)
            check_end = end_time if end_time != '24:00' else '00:00'
            datetime.strptime(start_time, '%H:%M')
            datetime.strptime(check_end, '%H:%M')
        except ValueError:
            embed = create_embed(
                title='Invalid Time',
                description='Please use HH:MM format (e.g., 00:00, 14:30, 24:00)',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Convert 24:00 to 00:00 for processing
        if end_time == '24:00':
            end_time = '00:00'
            # Also shift end_date forward by 1 day
            end_dt = end_dt + timedelta(days=1)
            end_date = format_date(end_dt)

        await interaction.response.defer()

        # Create poll
        poll_id, blocks = manager.create_poll(
            start_date=start_date,
            end_date=end_date,
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
        current_date = None
        for block in blocks:
            # Add date header if date changed
            if block['date'] != current_date:
                if current_date is not None:
                    block_list.append("")  # Blank line
                block_list.append(f"**{block['date']}:**")
                current_date = block['date']

            # Show local time with TCT in parentheses
            block_list.append(
                f"  **{block['label']}**: <t:{block['start_unix']}:t> – <t:{block['end_unix']}:t> "
                f"({block['start_time']}–{block['end_time']} TCT)"
            )

        # Create title based on single day or multi-day
        if start_date == end_date or (start_dt == end_dt - timedelta(days=1) and end_time == '00:00'):
            title = f"🕒 Availability Poll – {start_date}"
        else:
            title = f"🕒 Availability Poll – {start_date} to {end_date}"

        # Calculate countdown to event start
        first_block = blocks[0]
        event_start_unix = first_block['start_unix']
        countdown_text = f"\n\n⏳ **Event starts:** <t:{event_start_unix}:R> (<t:{event_start_unix}:F>)"

        # Create embed
        embed = create_embed(
            title=title,
            description=(
                f"**Select ALL time blocks when you are available.**\n\n"
                f"Click buttons to toggle your availability.\n"
                f"Times shown are your **local time** with TCT (Torn City Time = UTC) in parentheses."
                f"{countdown_text}\n\n"
                f"**Time Blocks:**\n" + '\n'.join(block_list)
            ),
            color=EMBED_COLOR_INFO
        )
        embed.set_footer(text=f"Block size: {block_length}h | Only {verified_role_name} members can vote")

        # Create view with buttons
        view = AvailabilityView(manager, poll_id, blocks, verified_role_name)

        # Send message
        message = await interaction.followup.send(embed=embed, view=view)

        # Update poll with message ID
        manager.polls[poll_id]['message_id'] = message.id
        await manager.save_polls()

        print(f'Availability poll created: {poll_id} with {len(blocks)} blocks')

    # ===== /avail_summary command =====
    @bot.tree.command(
        name='avail_summary',
        description='View availability summary for a poll'
    )
    @app_commands.describe(
        date='Any date within the poll range (DD-MM-YYYY)'
    )
    async def avail_summary(interaction: discord.Interaction, date: str):
        """Show availability summary with timetable"""
        await interaction.response.defer()

        # Find poll containing this date
        poll_id = manager.find_poll_by_date(date)

        if not poll_id:
            embed = create_embed(
                title='No Poll Found',
                description=f'No availability poll exists for {date}.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.followup.send(embed=embed)
            return

        summary = manager.get_summary(poll_id)

        # Build block summary with local and TCT times
        block_lines = []
        current_date = None
        for block in summary['blocks']:
            if block['date'] != current_date:
                if current_date is not None:
                    block_lines.append("")
                block_lines.append(f"**{block['date']}:**")
                current_date = block['date']

            block_lines.append(
                f"  **{block['label']}** (<t:{block['start_unix']}:t> / {block['start_time']}–{block['end_time']} TCT): "
                f"**{block['count']}** available"
            )

        # Build best windows
        best_lines = []
        for block in summary['best_windows']:
            if block['count'] > 0:
                best_lines.append(
                    f"• **{block['label']}** ({block['date']} <t:{block['start_unix']}:t> / "
                    f"{block['start_time']}–{block['end_time']} TCT) – **{block['count']}** members"
                )

        # Create title
        if summary['start_date'] == summary['end_date']:
            title = f"🧮 Availability Summary – {summary['start_date']}"
        else:
            title = f"🧮 Availability Summary – {summary['start_date']} to {summary['end_date']}"

        embed = create_embed(
            title=title,
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
        timetable = manager.generate_timetable(poll_id)
        timetable_text = f"```\n🕒 Availability (Torn Time)\n\n{timetable}\n```"

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
                f"**Current TCT:** {now.strftime('%H:%M:%S')} ({now.strftime('%d-%m-%Y')})\n\n"
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
        date='Any date within the poll to delete (DD-MM-YYYY)'
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

        poll_id = manager.find_poll_by_date(date)
        if not poll_id:
            embed = create_embed(
                title='Not Found',
                description=f'No availability poll exists for {date}.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        poll = manager.polls[poll_id]

        # Delete poll and data
        del manager.polls[poll_id]
        if poll_id in manager.data:
            del manager.data[poll_id]

        await manager.save_polls()
        await manager.save_data()

        embed = create_embed(
            title='✅ Poll Deleted',
            description=f"Availability poll for {poll['start_date']} to {poll['end_date']} has been deleted.",
            color=EMBED_COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ===== /my_availability command =====
    @bot.tree.command(
        name='my_availability',
        description='View your availability selections for a poll'
    )
    @app_commands.describe(
        date='Any date within the poll (DD-MM-YYYY)'
    )
    async def my_availability(interaction: discord.Interaction, date: str):
        """View your own availability selections"""
        poll_id = manager.find_poll_by_date(date)

        if not poll_id:
            embed = create_embed(
                title='No Poll Found',
                description=f'No availability poll exists for {date}.',
                color=EMBED_COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        poll = manager.get_poll(poll_id)
        selections = manager.get_user_selections(poll_id, interaction.user.id)
        blocks = poll['blocks']

        if not selections:
            embed = create_embed(
                title='Your Availability',
                description=f"You have not selected any time blocks for {poll['start_date']} to {poll['end_date']}.",
                color=EMBED_COLOR_INFO
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Build selection list
        selected_blocks = []
        for block in blocks:
            if block['id'] in selections:
                selected_blocks.append(
                    f"✅ **{block['label']}**: {block['date']} <t:{block['start_unix']}:t> "
                    f"({block['start_time']}–{block['end_time']} TCT)"
                )

        if poll['start_date'] == poll['end_date']:
            title = f"Your Availability – {poll['start_date']}"
        else:
            title = f"Your Availability – {poll['start_date']} to {poll['end_date']}"

        embed = create_embed(
            title=title,
            description='\n'.join(selected_blocks),
            color=EMBED_COLOR_SUCCESS
        )
        embed.set_footer(text=f"You've selected {len(selections)} time block(s)")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ===== /avail_list command =====
    @bot.tree.command(
        name='avail_list',
        description='List all active availability polls'
    )
    async def avail_list(interaction: discord.Interaction):
        """List all active polls"""
        if not manager.polls:
            embed = create_embed(
                title='No Active Polls',
                description='There are no availability polls currently active.',
                color=EMBED_COLOR_INFO
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        poll_list = []
        for poll_id, poll in manager.polls.items():
            total_votes = sum(len(users) for users in manager.data.get(poll_id, {}).values())
            if poll['start_date'] == poll['end_date']:
                poll_list.append(f"• **{poll['start_date']}** – {len(poll['blocks'])} blocks, {total_votes} votes")
            else:
                poll_list.append(f"• **{poll['start_date']} to {poll['end_date']}** – {len(poll['blocks'])} blocks, {total_votes} votes")

        embed = create_embed(
            title='📊 Active Availability Polls',
            description='\n'.join(poll_list),
            color=EMBED_COLOR_INFO
        )
        embed.set_footer(text="Use /avail_summary <date> to see details")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    print('Availability commands registered (/avail_setup, /avail_summary, /avail_delete, /avail_list, /my_availability, /tct_now)')


async def restore_availability_views(bot, manager: AvailabilityManager, verified_role_name: str):
    """
    Restore persistent views for existing polls after bot restart.
    Call this in on_ready after loading data.
    """
    for poll_id, poll in manager.polls.items():
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
            view = AvailabilityView(manager, poll_id, blocks, verified_role_name)
            bot.add_view(view, message_id=message_id)
            print(f'Restored availability view for {poll_id}')

        except Exception as e:
            print(f'Failed to restore availability view for {poll_id}: {e}')
