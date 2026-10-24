"""
Availability Polling Module
Allows Verified members to indicate availability for war/chain planning using time blocks in Torn time (TCT = UTC).

Features:
- /avail setup - Create availability poll (management only)
- /avail summary - View availability summary with ASCII timetable
- /avail mine - Check your own selections
- /avail list - List all active polls
- /avail delete - Delete a poll (management only)
- /tct_now - Show current Torn time
- Button-based time block selection for verified members
- Multi-day support for events spanning multiple days
"""

import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from utils.helpers import load_json, save_json, create_embed, is_management
from features.activity_profile import format_window
from config import (
    AVAILABILITY_CHANNEL_ID,
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL
)


import logging

logger = logging.getLogger(__name__)
# Block labels (A-X for up to 24 blocks)
# ===== Automatic pre-war availability poll =====
# The rules make posting availability a strike-able obligation, so the poll has
# to appear on its own - relying on someone remembering to run /avail is how the
# obligation quietly becomes unenforceable.
WAR_POLL_LEAD_SECONDS = 48 * 3600   # post once the war is this close
WAR_POLL_SPAN_HOURS = 24            # cover the first day of the war
WAR_POLL_BLOCK_HOURS = 4            # -> 6 blocks, one message, readable on mobile
# The rules say answer at least 24 hours before the war, so the poll
# closes there. It used to stay open until an hour before, which made
# the written deadline fiction: a member could answer 17 hours late and
# still count as answered, and answering is what exempts them from the
# absence strike.
WAR_POLL_CLOSE_LEAD_SECONDS = 24 * 3600  # close this long BEFORE the war


def poll_post_target(client, fallback_channel):
    """
    Where a manually created availability poll should be posted.

    Discord will not let the bot grant itself command permissions, and /avail
    is blocked in the availability channel, so the command is run from a
    management channel. It used to post the poll right there - while the
    chaser message, which always goes to the availability channel, told
    everybody to answer "the poll above". The poll and its chaser have to
    live in the same place or the chase makes no sense.

    Falls back to the invoking channel when the availability channel is unset
    or unreachable: a poll in the wrong channel is recoverable, a poll that
    was never posted is not.
    """
    if AVAILABILITY_CHANNEL_ID and client is not None:
        channel = client.get_channel(AVAILABILITY_CHANNEL_ID)
        if channel is not None:
            return channel
    return fallback_channel

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
        logger.info(f'Loaded {len(self.polls)} availability polls')
        # Auto-cleanup polls older than 7 days
        await self.cleanup_old_polls()

    async def save_polls(self):
        """Save poll configurations to JSON"""
        save_json(self.polls_file, self.polls)

    async def save_data(self):
        """Save availability data to JSON"""
        save_json(self.data_file, self.data)

    async def cleanup_old_polls(self, max_age_days: int = 7):
        """Remove polls whose end date is more than max_age_days in the past"""
        now = datetime.now(timezone.utc)
        expired = []

        for poll_id, poll in self.polls.items():
            try:
                end_dt = parse_date(poll['end_date'])
                age = (now - end_dt.replace(tzinfo=timezone.utc)).days
                if age > max_age_days:
                    expired.append(poll_id)
            except (ValueError, KeyError):
                continue

        if expired:
            for poll_id in expired:
                del self.polls[poll_id]
                self.data.pop(poll_id, None)
            await self.save_polls()
            await self.save_data()
            logger.info(f'Availability: Cleaned up {len(expired)} poll(s) older than {max_age_days} days')

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

    def set_day_availability(self, poll_id: str, day_block_ids, chosen_ids,
                             user_id) -> int:
        """
        Set exactly `chosen_ids` for one day, clearing that day's other blocks.

        A dropdown submits the whole day at once, so this replaces rather than
        toggles - and it touches ONLY the ids passed in `day_block_ids`, so
        submitting Sunday can never wipe what somebody picked for Monday.

        Returns how many blocks they are now down for that day.
        """
        if poll_id not in self.data:
            self.data[poll_id] = {}
        uid = str(user_id)
        chosen = {str(c) for c in (chosen_ids or ())}
        for bid in day_block_ids:
            key = str(bid)
            holders = self.data[poll_id].setdefault(key, [])
            if key in chosen:
                if uid not in holders:
                    holders.append(uid)
            elif uid in holders:
                holders.remove(uid)
        return len(chosen)

    ROSTER_FIELD = "\U0001f9cd Who's in"

    def build_roster(self, poll_id: str, guild, max_chars: int = 1000) -> str:
        """Per-block list of who has picked that block, for the poll message.

        Counts alone tell you a block is thin but not who to chase, which is the
        whole point of running the poll. Truncates per block rather than
        dropping blocks, so a popular early block can never hide a later one
        with nobody in it - the empty ones are the entire signal.
        """
        poll = self.polls.get(poll_id)
        if not poll:
            return ''
        data = self.data.get(poll_id, {})

        def name_for(uid):
            member = guild.get_member(int(uid)) if guild else None
            return member.display_name if member else f'<@{uid}>'

        lines = []
        for block in poll.get('blocks', []):
            uids = data.get(str(block['id']), [])
            if not uids:
                lines.append(f"**{block['label']}** (0): nobody yet")
                continue
            names = [name_for(u) for u in uids]
            shown, used = [], 0
            for n in names:
                if used + len(n) + 2 > 90:
                    break
                shown.append(n)
                used += len(n) + 2
            text = ', '.join(shown)
            if len(shown) < len(names):
                text += f' +{len(names) - len(shown)} more'
            lines.append(f"**{block['label']}** ({len(names)}): {text}")

        out = '\n'.join(lines)
        return out[:max_chars] if len(out) > max_chars else out

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
        try:
            target_dt = parse_date(date)
        except ValueError:
            return None
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

    def generate_member_grid(self, poll_id: str, guild) -> str:
        """
        Generate member × time slot grid for availability

        Args:
            poll_id: The poll ID
            guild: Discord guild to resolve user names

        Returns:
            Formatted string with member grid
        """
        summary = self.get_summary(poll_id)
        if not summary:
            return "No poll data available."

        poll_data = self.data.get(poll_id, {})
        blocks = summary['blocks']

        # Collect all unique voters and resolve names
        all_voter_ids = set()
        for block_key, user_ids in poll_data.items():
            all_voter_ids.update(user_ids)

        if not all_voter_ids:
            return "No votes yet."

        # Resolve user IDs to display names
        voters = {}  # {user_id_str: display_name}
        for uid_str in all_voter_ids:
            member = guild.get_member(int(uid_str)) if guild else None
            if member:
                voters[uid_str] = member.display_name
            else:
                voters[uid_str] = f'ID:{uid_str}'

        # Build grid: rows = voters, columns = time blocks
        labels = [b['label'] for b in blocks]
        max_name = max(len(name) for name in voters.values())
        max_name = min(max_name, 12)  # Cap name width

        # Header row with slot labels
        header = f'{"Name":<{max_name}}  ' + ' '.join(f'{l:>2}' for l in labels)
        separator = '-' * len(header)

        lines = [header, separator]

        # Sort voters by number of slots selected (most first)
        sorted_voters = sorted(
            voters.items(),
            key=lambda v: sum(1 for bk, uids in poll_data.items() if v[0] in uids),
            reverse=True
        )

        for uid_str, name in sorted_voters:
            name_display = name[:max_name]
            marks = []
            for block in blocks:
                block_key = str(block['id'])
                users = poll_data.get(block_key, [])
                marks.append(' X' if uid_str in users else ' -')
            line = f'{name_display:<{max_name}}  ' + ''.join(marks)
            lines.append(line)

        # Add count row at bottom
        lines.append(separator)
        count_line = f'{"Total":<{max_name}}  ' + ' '.join(
            f'{b["count"]:>2}' for b in blocks
        )
        lines.append(count_line)

        return '\n'.join(lines)

    def generate_timetable(self, poll_id: str) -> str:
        """
        Generate ASCII bar chart for availability

        Returns:
            Formatted string with bar chart
        """
        summary = self.get_summary(poll_id)
        if not summary:
            return "No poll data available."

        max_count = max((b['count'] for b in summary['blocks']), default=1)
        if max_count == 0:
            max_count = 1

        lines = []
        current_date = None

        for block in summary['blocks']:
            if block['date'] != current_date:
                if current_date is not None:
                    lines.append("")
                lines.append(f"─── {block['date']} ───")
                current_date = block['date']

            time_range = f"{block['start_time']}–{block['end_time']}"
            count = block['count']

            if count == 0:
                bar = '·'
            else:
                bar_length = max(1, min(10, (count * 10) // max_count + 1))
                bar = '█' * bar_length

            lines.append(f"{time_range}: {bar:<10} {count}")

        return '\n'.join(lines)

    async def check_war_poll(self, war_tracker, verified_role_name: str = 'Verified'):
        """
        Post a war availability poll once, about two days before a ranked war.

        Called every minute from the war loop. Does nothing until a war is
        within WAR_POLL_LEAD_SECONDS, and never posts twice for the same war -
        the guard is the war_id stored on the poll itself rather than a separate
        "already announced" file, so it cannot drift out of sync with reality.
        """
        if not war_tracker or not AVAILABILITY_CHANNEL_ID:
            return

        upcoming = await war_tracker.get_upcoming_war()
        if not upcoming:
            return

        now = int(datetime.now(timezone.utc).timestamp())
        start = int(upcoming['start'])
        if start - now > WAR_POLL_LEAD_SECONDS:
            return  # still too far out
        if start <= now:
            return  # already started; a poll now would be pointless

        war_id = str(upcoming['war_id'])
        for poll in self.polls.values():
            if str(poll.get('war_id')) == war_id:
                return  # already posted for this war

        channel = self.bot.get_channel(AVAILABILITY_CHANNEL_ID)
        if not channel:
            logger.warning(f'Availability: war poll channel {AVAILABILITY_CHANNEL_ID} not found')
            return

        try:
            await self._post_war_poll(channel, war_id, start, verified_role_name)
        except Exception as e:
            logger.error(f'Availability: failed to post war poll for {war_id}: {e}')

    async def _post_war_poll(self, channel, war_id: str, war_start: int,
                             verified_role_name: str):
        """Build and post the poll covering the first day of the war."""
        start_dt = datetime.fromtimestamp(war_start, timezone.utc)
        end_dt = start_dt + timedelta(hours=WAR_POLL_SPAN_HOURS)

        start_date = start_dt.strftime('%d-%m-%Y')
        start_time = start_dt.strftime('%H:%M')
        end_time = end_dt.strftime('%H:%M')
        # create_poll() treats an end time of 00:00 as "end of that day" and
        # adds the 24h itself. Handing it the real next-day date as well would
        # double the window, so step the date back and let it do the adding.
        end_date = (end_dt - timedelta(days=1) if end_time == '00:00'
                    else end_dt).strftime('%d-%m-%Y')

        poll_id, blocks = self.create_poll(
            start_date=start_date, end_date=end_date,
            channel_id=channel.id, message_id=0,
            start_time=start_time, end_time=end_time,
            block_length=WAR_POLL_BLOCK_HOURS,
        )
        if not blocks:
            self.polls.pop(poll_id, None)
            self.data.pop(poll_id, None)
            logger.error(f'Availability: war poll for {war_id} produced no blocks')
            return

        # Close an hour BEFORE the war. Closing exactly at the start left the
        # coverage picture changing right up to the moment it was needed, and
        # an answer typed as the war begins is not a plan anyone can act on.
        # It also gives the 6h chase a deadline that actually falls due.
        self.polls[poll_id]['poll_end_unix'] = war_start - WAR_POLL_CLOSE_LEAD_SECONDS
        self.polls[poll_id]['closed'] = False
        self.polls[poll_id]['war_id'] = war_id

        block_list = block_list_lines(blocks)

        embed = create_embed(
            title=f'\u2694\ufe0f War Availability \u2013 war starts {start_date}',
            description=(
                f'**War #{war_id} starts <t:{war_start}:R>** (<t:{war_start}:F>)\n\n'
                f'**Select every block you expect to be online.** Click to toggle.\n'
                f'Times are **TCT** first, then what that is for **you**.\n\n'
                f'Posting here is what exempts you. Being offline during a war '
                f'without it is a strike. Say so even if the answer is "barely on".\n\n'
                f'\U0001fa78 **Set your revives to "nobody"** before the war '
                f'starts and leave them off for the whole war. A revived '
                f'member goes straight back to being farmed. The bot posts who '
                f'still has them on 6 hours before the start.\n\n'
                f'\u23f3 **Poll closes when the war starts:** <t:{war_start}:R>\n\n'
                f'**Time Blocks:**\n' + '\n'.join(block_list)
            ),
            color=EMBED_COLOR_INFO,
        )
        embed.set_footer(text=f'Block size: {WAR_POLL_BLOCK_HOURS}h | '
                              f'Only {verified_role_name} members can vote')

        view = AvailabilityDropdownView(self, poll_id, blocks, verified_role_name)
        message = await channel.send(embed=embed, view=view)
        self.polls[poll_id]['message_id'] = message.id
        await self.save_polls()
        logger.info(f'Availability: auto-posted war poll {poll_id} for war {war_id} '
                    f'({len(blocks)} blocks, starts in {(war_start - int(datetime.now(timezone.utc).timestamp())) // 3600}h)')

    async def check_poll_endings(self):
        """Check if any polls have ended and auto-post results"""
        now = int(datetime.now(timezone.utc).timestamp())

        for poll_id, poll in list(self.polls.items()):
            poll_end = poll.get('poll_end_unix', 0)
            if not poll_end or poll.get('closed'):
                continue

            if now < poll_end:
                continue

            # Poll has ended — try to post results first, then mark as closed
            try:
                channel_id = poll.get('channel_id')
                channel = self.bot.get_channel(channel_id) if channel_id else None
                if not channel:
                    logger.warning(f'Availability: Could not find channel {channel_id} for poll {poll_id}')
                    # Mark closed so we don't retry forever for a missing channel
                    poll['closed'] = True
                    await self.save_polls()
                    continue

                # Generate results
                summary = self.get_summary(poll_id)
                if not summary:
                    poll['closed'] = True
                    await self.save_polls()
                    continue

                guild = channel.guild
                timetable = self.generate_timetable(poll_id)
                grid = self.generate_member_grid(poll_id, guild)

                # Find best window
                best = summary['best_windows'][0] if summary['best_windows'] else None

                if best and best['count'] > 0:
                    best_text = (
                        f"The most common time people are available together will be "
                        f"**{best['label']}** ({best['date']} <t:{best['start_unix']}:t> / "
                        f"{best['start_time']}–{best['end_time']} TCT) "
                        f"with **{best['count']}** members.\n\nSee you all then!"
                    )
                else:
                    best_text = "No members voted in this poll."

                # Build results embed
                if summary['start_date'] == summary['end_date']:
                    title = f"📊 Poll Results – {summary['start_date']}"
                else:
                    title = f"📊 Poll Results – {summary['start_date']} to {summary['end_date']}"

                embed = create_embed(
                    title=title,
                    description=best_text,
                    color=EMBED_COLOR_SUCCESS
                )

                # Combined chart + grid
                combined = f"```\nAvailability (Torn Time)\n\n{timetable}\n\nMember Grid\n\n{grid}\n```"

                if len(combined) <= 2000:
                    await channel.send(embed=embed, content=combined)
                else:
                    chart_text = f"```\nAvailability (Torn Time)\n\n{timetable}\n```"
                    grid_text = f"```\nMember Grid\n\n{grid}\n```"
                    await channel.send(embed=embed, content=chart_text)
                    # Paginate grid if too long
                    if len(grid_text) <= 2000:
                        await channel.send(content=grid_text)
                    else:
                        grid_lines = grid.split('\n')
                        chunk = "```\nMember Grid\n\n"
                        for line in grid_lines:
                            if len(chunk) + len(line) + 5 > 2000:  # 5 for \n + closing ```
                                chunk += "```"
                                await channel.send(content=chunk)
                                chunk = "```\n"
                            chunk += line + '\n'
                        if chunk.strip('`\n '):
                            chunk += "```"
                            await channel.send(content=chunk)

                # Edit original poll message to mark as closed
                message_id = poll.get('message_id')
                if message_id:
                    try:
                        msg = await channel.fetch_message(message_id)
                        if msg.embeds:
                            old_embed = msg.embeds[0]
                            closed_embed = create_embed(
                                title=f"🔒 {old_embed.title} (Closed)",
                                description=old_embed.description or '',
                                color=EMBED_COLOR_WARNING
                            )
                            await msg.edit(embed=closed_embed, view=None)
                    except Exception as e:
                        logger.warning(f'Availability: Could not edit poll message: {e}')

                # Mark closed AFTER results are successfully posted
                poll['closed'] = True
                await self.save_polls()
                logger.info(f'Availability: Poll {poll_id} closed, results posted')

            except Exception as e:
                logger.error(f'Availability: Error closing poll {poll_id}: {e}')


# Discord allows 25 components per message (5 rows x 5). Blocks past that get
# no button at all, which is worse than it sounds: the poll still LISTS them,
# so members read an option they cannot vote for. The cap sat at 20 for no
# recorded reason, which is how a 24-block poll silently lost U, V, W and X.
MAX_POLL_BUTTONS = 25


def block_times(b):
    """
    One block's times, TCT first.

    TCT is what the faction actually coordinates in - every rule, war time and
    chain call is written in it - so it leads, and it is monospaced to stand
    out from the local render. The local time follows, explicitly labelled,
    because an unlabelled second clock is the thing people misread.
    """
    return (f"  **{b['label']}** · `{b['start_time']}–{b['end_time']} TCT`"
            f" · you: <t:{b['start_unix']}:t>–<t:{b['end_unix']}:t>")


def block_list_lines(blocks, use_buttons=False):
    """
    The block list for a poll body, grouped by date.

    Also names anything the viewer will not be able to vote for. Listing an
    option nobody can click is a worse failure than refusing to make the
    poll, because it looks like the member chose not to answer.

    The two layouts run out of room differently: buttons cap at 25 blocks on
    the message, day menus cap at 5 days (one action row each) but hold 25
    blocks within each day.
    """
    lines, current_date = [], None
    for b in blocks:
        if b['date'] != current_date:
            if current_date is not None:
                lines.append('')
            lines.append(f"**{b['date']}:**")
            current_date = b['date']
        lines.append(block_times(b))
    if use_buttons:
        unvotable = blocks[MAX_POLL_BUTTONS:]
        if unvotable:
            names = ', '.join(b['label'] for b in unvotable)
            lines.append('')
            lines.append(
                f'⚠️ **{names}** have no button – Discord allows '
                f'only {MAX_POLL_BUTTONS} per message. Use a bigger block '
                f'size, a shorter window, or drop the old_buttons option.')
    else:
        extra_days = [d for d, _ in _menu_groups(blocks)[MAX_POLL_DAYS:]]
        if extra_days:
            lines.append('')
            lines.append(
                f'⚠️ **{", ".join(extra_days)}** have no menu – '
                f'Discord fits only {MAX_POLL_DAYS} on a message. Use a bigger '
                f'block size or split the poll into two.')
    return lines


# Discord allows 5 action rows, so 5 day-menus on one message. Each menu
# holds 25 options, which is more blocks than a day can have.
MAX_POLL_DAYS = 5


MAX_MENU_OPTIONS = 25


def _day_groups(blocks):
    """Blocks grouped by date, in order."""
    days, order = {}, []
    for b in blocks:
        if b['date'] not in days:
            days[b['date']] = []
            order.append(b['date'])
        days[b['date']].append(b)
    return [(d, days[d]) for d in order]


def _menu_groups(blocks, max_menus=None, max_options=MAX_MENU_OPTIONS):
    """
    Blocks split into at most `max_menus` menus of at most `max_options`.

    One menu per day is the readable shape, but a week-long poll is seven
    days against five action rows - so adjacent days are merged, smallest
    pair first, until they fit. Merging keeps days whole and in order, which
    a flat chunk-every-25 split would not.

    Returns [(label, blocks)]. A label spanning days reads "25-10 to 26-10".
    If it still cannot fit, the leftovers are returned beyond `max_menus` for
    the caller to warn about rather than silently dropped here.
    """
    max_menus = MAX_POLL_DAYS if max_menus is None else max_menus
    groups = []
    for date, day_blocks in _day_groups(blocks):
        # A single day bigger than one menu has to be split regardless.
        for i in range(0, len(day_blocks), max_options):
            groups.append([date, date, day_blocks[i:i + max_options]])

    while len(groups) > max_menus:
        best_size, best_i = None, None
        for i in range(len(groups) - 1):
            size = len(groups[i][2]) + len(groups[i + 1][2])
            if size <= max_options and (best_size is None or size < best_size):
                best_size, best_i = size, i
        if best_i is None:
            break  # nothing else can merge; the caller reports the overflow
        a = groups[best_i]
        b = groups.pop(best_i + 1)
        groups[best_i] = [a[0], b[1], a[2] + b[2]]

    return [(first if first == last else f'{first} to {last}', bs)
            for first, last, bs in groups]


class DaySelect(discord.ui.Select):
    """
    One day's blocks as a multi-select.

    Replaces buttons because a 24-block poll meant 24 clicks and 24
    confirmations. The ticks always start empty: a poll message is shared by
    everyone, so Discord cannot pre-tick one viewer's own picks on it. That
    is why the confirmation says the day was replaced rather than added to.
    """

    def __init__(self, manager, poll_id, date, day_blocks, verified_role_name):
        self.manager = manager
        self.poll_id = poll_id
        self.date = date
        self.day_blocks = day_blocks
        self.verified_role_name = verified_role_name
        options = []
        for b in day_blocks[:25]:
            count = manager.get_block_count(poll_id, b['id'])
            options.append(discord.SelectOption(
                label=f"{b['label']} · {b['start_time']}–{b['end_time']} TCT",
                value=str(b['id']),
                description=(f'{count} going' if count else 'nobody yet')))
        super().__init__(
            placeholder=f'{date} — pick every slot you can make',
            min_values=0, max_values=len(options), options=options,
            custom_id=f'availday_{poll_id}_{date}')

    async def callback(self, interaction: discord.Interaction):
        poll = self.manager.polls.get(self.poll_id, {})
        poll_end = poll.get('poll_end_unix', 0)
        if poll.get('closed') or (poll_end and int(datetime.now(timezone.utc).timestamp()) > poll_end):
            await interaction.response.send_message('This poll has closed.',
                                                    ephemeral=True)
            return

        verified_role = discord.utils.find(
            lambda r: r.name.lower() == self.verified_role_name.lower(),
            interaction.user.roles)
        if not verified_role:
            await interaction.response.send_message(
                f'You must have the **{self.verified_role_name}** role to set '
                f'availability. Please use `/register` with your Torn ID first.',
                ephemeral=True)
            return

        try:
            ids = [b['id'] for b in self.day_blocks]
            n = self.manager.set_day_availability(
                self.poll_id, ids, self.values, interaction.user.id)
            await self.manager.save_data()

            # Refresh every menu's counts, not just this one: another member
            # may have answered since this message was last drawn.
            for item in self.view.children:
                if isinstance(item, DaySelect):
                    for opt in item.options:
                        c = self.manager.get_block_count(self.poll_id, opt.value)
                        opt.description = f'{c} going' if c else 'nobody yet'

            embed = None
            if interaction.message and interaction.message.embeds:
                embed = interaction.message.embeds[0]
                roster = self.manager.build_roster(self.poll_id, interaction.guild)
                if roster:
                    idx = next((i for i, f in enumerate(embed.fields)
                                if f.name == AvailabilityManager.ROSTER_FIELD), None)
                    if idx is None:
                        embed.add_field(name=AvailabilityManager.ROSTER_FIELD,
                                        value=roster, inline=False)
                    else:
                        embed.set_field_at(idx, name=AvailabilityManager.ROSTER_FIELD,
                                           value=roster, inline=False)

            if embed is not None:
                await interaction.response.edit_message(embed=embed, view=self.view)
            else:
                await interaction.response.edit_message(view=self.view)

            chosen = [b for b in self.day_blocks if str(b['id']) in set(self.values)]
            if chosen:
                times = ' · '.join(
                    f"{b['start_time']}–{b['end_time']}" for b in chosen)
                msg = (f"✅ **{self.date}** set: "
                       f"{', '.join(b['label'] for b in chosen)}\n"
                       f"`{times} TCT`\n"
                       f"-# This replaced your earlier picks for {self.date}.")
            else:
                msg = (f"❌ **{self.date}**: marked you down for nothing.\n"
                       f"-# This cleared your earlier picks for {self.date}.")
            await interaction.followup.send(msg, ephemeral=True)

        except discord.NotFound:
            await interaction.response.send_message(
                'This availability poll no longer exists.', ephemeral=True)
        except Exception as e:
            logger.error(f'Error in availability select callback: {e}')
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    'Something went wrong. Please try again.', ephemeral=True)


class AvailabilityDropdownView(discord.ui.View):
    """One multi-select per day. Two days is two menus, not 24 buttons."""

    def __init__(self, manager, poll_id, blocks, verified_role_name):
        super().__init__(timeout=None)
        groups = _menu_groups(blocks)
        if len(groups) > MAX_POLL_DAYS:
            logger.error(
                f'Availability: poll {poll_id} needs {len(groups)} menus but '
                f'only {MAX_POLL_DAYS} fit on a message - '
                f'{", ".join(d for d, _ in groups[MAX_POLL_DAYS:])} unvotable')
        for label, day_blocks in groups[:MAX_POLL_DAYS]:
            self.add_item(DaySelect(manager, poll_id, label, day_blocks,
                                    verified_role_name))


class AvailabilityView(discord.ui.View):
    """Persistent view with buttons for availability selection"""

    def __init__(self, manager: AvailabilityManager, poll_id: str, blocks: List[Dict], verified_role_name: str):
        super().__init__(timeout=None)  # Persistent view
        self.manager = manager
        self.poll_id = poll_id
        self.verified_role_name = verified_role_name

        # Discord's own ceiling, not an arbitrary one. A poll that needs more
        # blocks than this says so in its body (see block_list_lines) rather
        # than quietly offering fewer options than it advertises.
        if len(blocks) > MAX_POLL_BUTTONS:
            logger.error(
                f'Availability: poll {poll_id} has {len(blocks)} blocks but '
                f'only {MAX_POLL_BUTTONS} can have buttons - '
                f'{", ".join(b["label"] for b in blocks[MAX_POLL_BUTTONS:])} '
                f'are unvotable')
        for block in blocks[:MAX_POLL_BUTTONS]:
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
        # Check if poll has closed
        poll = self.manager.polls.get(self.poll_id, {})
        poll_end = poll.get('poll_end_unix', 0)
        if poll.get('closed') or (poll_end and int(datetime.now(timezone.utc).timestamp()) > poll_end):
            await interaction.response.send_message(
                "This poll has closed.",
                ephemeral=True
            )
            return

        # Check if user has Verified role (case-insensitive)
        verified_role = discord.utils.find(
            lambda r: r.name.lower() == self.verified_role_name.lower(),
            interaction.user.roles
        )
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

            # Rebuild the embed so the roster reflects this click. Editing the
            # live embed rather than posting a summary keeps one message as the
            # single source of truth, which is what people scroll back to.
            embed = None
            if interaction.message and interaction.message.embeds:
                embed = interaction.message.embeds[0]
                roster = self.manager.build_roster(self.poll_id, interaction.guild)
                if roster:
                    idx = next((i for i, f in enumerate(embed.fields)
                                if f.name == AvailabilityManager.ROSTER_FIELD), None)
                    if idx is None:
                        embed.add_field(name=AvailabilityManager.ROSTER_FIELD,
                                        value=roster, inline=False)
                    else:
                        embed.set_field_at(idx, name=AvailabilityManager.ROSTER_FIELD,
                                           value=roster, inline=False)

            # Update the message
            if embed is not None:
                await interaction.response.edit_message(embed=embed, view=self.view)
            else:
                await interaction.response.edit_message(view=self.view)

            # Send ephemeral feedback with both local and TCT time
            # TCT first here too: the confirmation is the one message that
            # tells a member what they just agreed to, so it must read the
            # same way round as the poll they clicked.
            time_display = (f"`{self.block['start_time']}–{self.block['end_time']} TCT`"
                            f" (your <t:{self.block['start_unix']}:t>)")
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
            logger.error(f'Error in availability button callback: {e}')
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again.", ephemeral=True
                )


async def setup_availability_commands(bot, manager: AvailabilityManager, verified_role_name: str,
                                       activity_profile_manager=None):
    """Register availability slash commands"""

    # ===== /avail command (unified) =====
    @bot.tree.command(
        name='avail',
        description='Availability polls: create, view summary, check yours, list, delete, or activity data'
    )
    @app_commands.describe(
        action='What to do',
        date='Date for the poll (DD-MM-YYYY) · required for Setup/Summary/Mine/Delete',
        start_time='[Setup] Start time in TCT/UTC (HH:MM, e.g., 00:00)',
        end_time='[Setup] End time in TCT/UTC (HH:MM, e.g., 24:00)',
        end_date='[Setup] End date if multi-day (DD-MM-YYYY) · leave empty for same day',
        block_length='[Setup] Hours per time block (1-6, default: 2)',
        poll_duration='[Setup] Hours until poll closes (default: 24)',
        member='[Activity] Check one member instead of the whole roster',
        confirm='[Delete] Required to delete a poll that already has answers',
        old_buttons='[Setup] Use the old one-button-per-block layout instead of day menus'
    )
    @app_commands.choices(action=[
        app_commands.Choice(name='Setup - Create availability poll (Management)', value='setup'),
        app_commands.Choice(name='Summary - View poll results', value='summary'),
        app_commands.Choice(name='Mine - Check your selections', value='mine'),
        app_commands.Choice(name='List - Show all active polls', value='list'),
        app_commands.Choice(name='Delete - Remove a poll (Management)', value='delete'),
        app_commands.Choice(name='Coverage - Best time to schedule & quiet hours (Management)', value='coverage'),
        app_commands.Choice(name='Activity - Who is typically online when (Management)', value='activity'),
    ])
    async def avail(
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        date: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        end_date: Optional[str] = None,
        block_length: int = 2,
        poll_duration: int = 24,
        member: Optional[discord.Member] = None,
        confirm: bool = False,
        old_buttons: bool = False
    ):
        """Unified availability command"""
        action_value = action.value

        # ── Setup ──
        if action_value == 'setup':
            if not is_management(interaction):
                await interaction.response.send_message(
                    embed=create_embed(title='Permission Denied',
                                       description='You need Management permissions to create polls.',
                                       color=EMBED_COLOR_CRITICAL),
                    ephemeral=True)
                return

            # Validate required params
            if not date or not start_time or not end_time:
                await interaction.response.send_message(
                    embed=create_embed(title='Missing Parameters',
                                       description='Setup requires: `date`, `start_time`, and `end_time`.',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            start_date = date

            if poll_duration < 1:
                await interaction.response.send_message(
                    embed=create_embed(title='Invalid Poll Duration',
                                       description='Poll duration must be at least 1 hour.',
                                       color=EMBED_COLOR_CRITICAL),
                    ephemeral=True)
                return

            if block_length < 1 or block_length > 6:
                await interaction.response.send_message(
                    embed=create_embed(title='Invalid Block Length',
                                       description='Block length must be between 1 and 6 hours.',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            # Default end_date to start_date
            actual_end_date = end_date if end_date else start_date

            try:
                start_dt = parse_date(start_date)
                end_dt = parse_date(actual_end_date)
            except ValueError:
                await interaction.response.send_message(
                    embed=create_embed(title='Invalid Date',
                                       description='Please use DD-MM-YYYY format (e.g., 30-01-2026)',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            if end_dt < start_dt:
                await interaction.response.send_message(
                    embed=create_embed(title='Invalid Date Range',
                                       description='End date must be on or after start date.',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            try:
                check_end = end_time if end_time != '24:00' else '00:00'
                datetime.strptime(start_time, '%H:%M')
                datetime.strptime(check_end, '%H:%M')
            except ValueError:
                await interaction.response.send_message(
                    embed=create_embed(title='Invalid Time',
                                       description='Please use HH:MM format (e.g., 00:00, 14:30, 24:00)',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            if end_time == '24:00':
                end_time = '00:00'
                end_dt = end_dt + timedelta(days=1)
                actual_end_date = format_date(end_dt)

            await interaction.response.defer()

            poll_target = poll_post_target(interaction.client, interaction.channel)

            poll_id, blocks = manager.create_poll(
                start_date=start_date,
                end_date=actual_end_date,
                channel_id=poll_target.id,
                message_id=0,
                start_time=start_time,
                end_time=end_time,
                block_length=block_length
            )

            if not blocks:
                if poll_id in manager.polls:
                    del manager.polls[poll_id]
                if poll_id in manager.data:
                    del manager.data[poll_id]
                await interaction.followup.send(
                    embed=create_embed(title='Error',
                                       description='Failed to create time blocks. Check your time range.',
                                       color=EMBED_COLOR_CRITICAL),
                    ephemeral=True)
                return

            poll_end_unix = int(datetime.now(timezone.utc).timestamp()) + poll_duration * 3600
            manager.polls[poll_id]['poll_end_unix'] = poll_end_unix
            manager.polls[poll_id]['closed'] = False

            block_list = block_list_lines(blocks, use_buttons=old_buttons)

            if start_date == actual_end_date or (start_dt == end_dt - timedelta(days=1) and end_time == '00:00'):
                title = f"🕒 Availability Poll – {start_date}"
            else:
                title = f"🕒 Availability Poll – {start_date} to {actual_end_date}"

            first_block = blocks[0]
            event_start_unix = first_block['start_unix']
            countdown_text = f"\n\n⏳ **Event starts:** <t:{event_start_unix}:R> (<t:{event_start_unix}:F>)"
            countdown_text += f"\n📊 **Poll closes:** <t:{poll_end_unix}:R> (<t:{poll_end_unix}:f>)"

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

            # Day menus by default: a 24-block poll was 24 clicks and 24
            # confirmations. old_buttons is the way back if they misbehave.
            use_buttons = old_buttons
            manager.polls[poll_id]['style'] = 'buttons' if use_buttons else 'menus'
            view = (AvailabilityView if use_buttons else AvailabilityDropdownView)(
                manager, poll_id, blocks, verified_role_name)
            if poll_target.id == interaction.channel.id:
                message = await interaction.followup.send(embed=embed, view=view)
            else:
                message = await poll_target.send(embed=embed, view=view)
                await interaction.followup.send(
                    embed=create_embed(
                        title='Poll posted',
                        description=(f'Availability poll for **{start_date}** is '
                                     f'up in {poll_target.mention}.\n'
                                     f'[Jump to it]({message.jump_url})'),
                        color=EMBED_COLOR_SUCCESS),
                    ephemeral=True)

            manager.polls[poll_id]['message_id'] = message.id
            await manager.save_polls()
            logger.info(f'Availability poll created: {poll_id} with {len(blocks)} '
                        f'blocks in #{getattr(poll_target, "name", poll_target.id)}')

        # ── Summary ──
        elif action_value == 'summary':
            if not date:
                await interaction.response.send_message(
                    embed=create_embed(title='Missing Date',
                                       description='Please provide a `date` (DD-MM-YYYY) for the poll to view.',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            await interaction.response.defer()

            poll_id = manager.find_poll_by_date(date)
            if not poll_id:
                await interaction.followup.send(
                    embed=create_embed(title='No Poll Found',
                                       description=f'No availability poll exists for {date}.',
                                       color=EMBED_COLOR_WARNING))
                return

            summary = manager.get_summary(poll_id)

            block_lines = []
            current_date_str = None
            for block in summary['blocks']:
                if block['date'] != current_date_str:
                    if current_date_str is not None:
                        block_lines.append("")
                    block_lines.append(f"**{block['date']}:**")
                    current_date_str = block['date']
                block_lines.append(
                    f"  **{block['label']}** (<t:{block['start_unix']}:t> / {block['start_time']}–{block['end_time']} TCT): "
                    f"**{block['count']}** available"
                )

            best_lines = []
            for block in summary['best_windows']:
                if block['count'] > 0:
                    best_lines.append(
                        f"• **{block['label']}** ({block['date']} <t:{block['start_unix']}:t> / "
                        f"{block['start_time']}–{block['end_time']} TCT) – **{block['count']}** members"
                    )

            if summary['start_date'] == summary['end_date']:
                title = f"🧮 Availability Summary – {summary['start_date']}"
            else:
                title = f"🧮 Availability Summary – {summary['start_date']} to {summary['end_date']}"

            embed = create_embed(title=title, description='\n'.join(block_lines), color=EMBED_COLOR_SUCCESS)

            if best_lines:
                embed.add_field(name='⭐ Best Windows', value='\n'.join(best_lines), inline=False)
            else:
                embed.add_field(name='⭐ Best Windows', value='No members have voted yet.', inline=False)

            embed.set_footer(text=f"Times shown: Local time / TCT (Torn City Time = UTC)")

            timetable = manager.generate_timetable(poll_id)
            grid = manager.generate_member_grid(poll_id, interaction.guild)
            combined = f"```\nAvailability (Torn Time)\n\n{timetable}\n\nMember Grid\n\n{grid}\n```"

            if len(combined) <= 2000:
                await interaction.followup.send(embed=embed, content=combined)
            else:
                chart_text = f"```\nAvailability (Torn Time)\n\n{timetable}\n```"
                grid_text = f"```\nMember Grid\n\n{grid}\n```"
                await interaction.followup.send(embed=embed, content=chart_text)
                if len(grid_text) <= 2000:
                    await interaction.followup.send(content=grid_text)
                else:
                    grid_lines = grid.split('\n')
                    chunk = "```\nMember Grid\n\n"
                    for line in grid_lines:
                        if len(chunk) + len(line) + 5 > 2000:
                            chunk += "```"
                            await interaction.followup.send(content=chunk)
                            chunk = "```\n"
                        chunk += line + '\n'
                    if chunk.strip('`\n '):
                        chunk += "```"
                        await interaction.followup.send(content=chunk)

        # ── Mine ──
        elif action_value == 'mine':
            if not date:
                await interaction.response.send_message(
                    embed=create_embed(title='Missing Date',
                                       description='Please provide a `date` (DD-MM-YYYY) for the poll to check.',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            poll_id = manager.find_poll_by_date(date)
            if not poll_id:
                await interaction.response.send_message(
                    embed=create_embed(title='No Poll Found',
                                       description=f'No availability poll exists for {date}.',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            poll = manager.get_poll(poll_id)
            selections = manager.get_user_selections(poll_id, interaction.user.id)
            blocks = poll['blocks']

            if not selections:
                await interaction.response.send_message(
                    embed=create_embed(title='Your Availability',
                                       description=f"You have not selected any time blocks for {poll['start_date']} to {poll['end_date']}.",
                                       color=EMBED_COLOR_INFO),
                    ephemeral=True)
                return

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

            embed = create_embed(title=title, description='\n'.join(selected_blocks), color=EMBED_COLOR_SUCCESS)
            embed.set_footer(text=f"You've selected {len(selections)} time block(s)")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # ── List ──
        elif action_value == 'list':
            if not manager.polls:
                await interaction.response.send_message(
                    embed=create_embed(title='No Active Polls',
                                       description='There are no availability polls currently active.',
                                       color=EMBED_COLOR_INFO),
                    ephemeral=True)
                return

            poll_list = []
            for pid, poll in manager.polls.items():
                total_votes = sum(len(users) for users in manager.data.get(pid, {}).values())
                if poll['start_date'] == poll['end_date']:
                    poll_list.append(f"• **{poll['start_date']}** – {len(poll['blocks'])} blocks, {total_votes} votes")
                else:
                    poll_list.append(f"• **{poll['start_date']} to {poll['end_date']}** – {len(poll['blocks'])} blocks, {total_votes} votes")

            embed = create_embed(title='📊 Active Availability Polls', description='\n'.join(poll_list), color=EMBED_COLOR_INFO)
            embed.set_footer(text="Use /avail action:Summary date:<date> to see details")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # ── Delete ──
        elif action_value == 'delete':
            if not is_management(interaction):
                await interaction.response.send_message(
                    embed=create_embed(title='Permission Denied',
                                       description='You need Management permissions to delete polls.',
                                       color=EMBED_COLOR_CRITICAL),
                    ephemeral=True)
                return

            if not date:
                await interaction.response.send_message(
                    embed=create_embed(title='Missing Date',
                                       description='Please provide a `date` (DD-MM-YYYY) for the poll to delete.',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            poll_id = manager.find_poll_by_date(date)
            if not poll_id:
                await interaction.response.send_message(
                    embed=create_embed(title='Not Found',
                                       description=f'No availability poll exists for {date}.',
                                       color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            poll = manager.polls[poll_id]
            # Answers are the evidence that exempts a member from the war
            # absence strike, and deleting the poll destroys every one of
            # them with no undo. A poll nobody answered is free to delete; a
            # poll with answers has to be asked for twice.
            answered = sum(len(users) for users
                           in (manager.data.get(poll_id) or {}).values())
            if answered and not confirm:
                await interaction.response.send_message(
                    embed=create_embed(
                        title='Not deleted',
                        description=(
                            f"The poll for **{poll['start_date']}** holds "
                            f'**{answered}** answer(s). Those are what exempt '
                            f'people from the absence strike, and deleting '
                            f'the poll deletes them permanently.\n\n'
                            f'-# Run it again with `confirm:True` if you '
                            f'really want it gone.'),
                        color=EMBED_COLOR_WARNING),
                    ephemeral=True)
                return

            del manager.polls[poll_id]
            if poll_id in manager.data:
                del manager.data[poll_id]

            await manager.save_polls()
            await manager.save_data()

            await interaction.response.send_message(
                embed=create_embed(title='✅ Poll Deleted',
                                   description=f"Availability poll for {poll['start_date']} to {poll['end_date']} has been deleted.",
                                   color=EMBED_COLOR_SUCCESS),
                ephemeral=True)

        # ── Coverage ──
        elif action_value == 'coverage':
            if not is_management(interaction):
                await interaction.response.send_message(
                    embed=create_embed(title='Permission Denied',
                                       description='You need Management permissions to view coverage.',
                                       color=EMBED_COLOR_CRITICAL),
                    ephemeral=True)
                return

            if not activity_profile_manager:
                await interaction.response.send_message('Activity profile tracking is not available.', ephemeral=True)
                return

            result = activity_profile_manager.get_coverage()
            if not result:
                await interaction.response.send_message(
                    embed=create_embed(title='Not Enough Data Yet',
                                       description='Activity profiles need a couple days of history before '
                                                    'Coverage can show anything useful. Check back soon.',
                                       color=EMBED_COLOR_INFO),
                    ephemeral=True)
                return

            best, quiet, member_count = result
            best_start, best_end, best_avg = best
            quiet_start, quiet_end, quiet_avg = quiet
            best_count = round(best_avg * member_count)
            quiet_count = round(quiet_avg * member_count)

            embed = create_embed(
                title='📊 Faction Coverage',
                description=(
                    f'Based on {member_count} member(s) with enough tracked history.\n\n'
                    f'**Best overlap window:** {format_window(best_start, best_end)} UTC · '
                    f'~{best_count}/{member_count} usually active\n'
                    f'**Quietest window:** {format_window(quiet_start, quiet_end)} UTC · '
                    f'~{quiet_count}/{member_count} usually active'
                ),
                color=EMBED_COLOR_INFO
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # ── Activity ──
        elif action_value == 'activity':
            if not is_management(interaction):
                await interaction.response.send_message(
                    embed=create_embed(title='Permission Denied',
                                       description='You need Management permissions to view activity data.',
                                       color=EMBED_COLOR_CRITICAL),
                    ephemeral=True)
                return

            if not activity_profile_manager:
                await interaction.response.send_message('Activity profile tracking is not available.', ephemeral=True)
                return

            if member is not None:
                verified_users = load_json('data/verified_users.json', default={})
                inactivity_users = load_json('data/discord_to_torn.json', default={})

                torn_id = None
                udata = verified_users.get(str(member.id))
                if udata and udata.get('torn_id'):
                    torn_id = str(udata['torn_id'])
                elif str(member.id) in inactivity_users:
                    torn_id = str(inactivity_users[str(member.id)])

                if not torn_id or torn_id not in activity_profile_manager.profiles:
                    await interaction.response.send_message(
                        embed=create_embed(title='No Data',
                                           description=f'No activity data for {member.mention} yet.',
                                           color=EMBED_COLOR_INFO),
                        ephemeral=True)
                    return

                if not activity_profile_manager.has_enough_data(torn_id):
                    await interaction.response.send_message(
                        embed=create_embed(title='Not Enough Data Yet',
                                           description=f'{member.mention} needs a couple more days of '
                                                        f'tracked activity first.',
                                           color=EMBED_COLOR_INFO),
                        ephemeral=True)
                    return

                chunks = activity_profile_manager.get_active_windows(torn_id)
                consistency = activity_profile_manager.get_consistency(torn_id)
                name = activity_profile_manager.profiles[torn_id].get('name', member.display_name)

                if not chunks:
                    windows_text = 'No clear pattern yet. Activity looks scattered across the day.'
                else:
                    windows_text = '\n'.join(
                        f'• {format_window(s, e)} UTC (~{round(p * 100)}% of days)' for s, e, p in chunks
                    )

                consistency_text = ''
                if consistency and consistency[1] > 0:
                    active, tracked = consistency
                    consistency_text = f'\n\nActive {active}/{tracked} of the last {tracked} tracked day(s).'

                embed = create_embed(
                    title=f'🕒 Activity · {name}',
                    description=f'**Typical active windows (UTC):**\n{windows_text}{consistency_text}',
                    color=EMBED_COLOR_INFO
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            ready, warming_up = activity_profile_manager.get_roster_summary()
            if not ready and not warming_up:
                await interaction.response.send_message(
                    embed=create_embed(title='No Data Yet',
                                       description='Activity tracking just started. Check back in a couple of days.',
                                       color=EMBED_COLOR_INFO),
                    ephemeral=True)
                return

            lines = [f'**{name}** · usually active {format_window(s, e)} UTC'
                     for _tid, name, (s, e, _p) in ready]
            description = '\n'.join(lines) if lines else 'No members have enough data yet.'
            if warming_up:
                description += f'\n\n*Still building a profile: {", ".join(warming_up)}*'

            # Discord rejects an embed description over 4096 chars outright, which
            # would make the whole command fail rather than degrade. At ~53 chars a
            # row that is reachable near Torn's 100-member faction cap, so trim
            # rather than risk a 400 on a bigger roster.
            if len(description) > 3900:
                kept = []
                used = 0
                for line in lines:
                    if used + len(line) + 1 > 3700:
                        break
                    kept.append(line)
                    used += len(line) + 1
                description = ('\n'.join(kept) +
                               f'\n\n*…and {len(lines) - len(kept)} more. '
                               f'Use `member:` to look someone up directly.*')

            embed = create_embed(
                title='🕒 Faction Activity',
                description=description,
                color=EMBED_COLOR_INFO
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

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

    logger.info('Availability commands registered (/avail, /tct_now)')


async def restore_availability_views(bot, manager: AvailabilityManager, verified_role_name: str):
    """
    Restore persistent views for existing polls after bot restart.
    Call this in on_ready after loading data.
    """
    for poll_id, poll in manager.polls.items():
        if poll.get('closed'):
            continue

        channel_id = poll.get('channel_id')
        message_id = poll.get('message_id')
        blocks = poll.get('blocks', [])

        if not channel_id or not message_id or not blocks:
            continue

        try:
            channel = bot.get_channel(channel_id)
            if not channel:
                continue

            # Re-create the view the poll was POSTED with. A poll's message
            # already carries its components; handing it the other style
            # after a restart would leave every control dead, because the
            # custom_ids would no longer match what is on the message.
            # Default to BUTTONS when the key is absent. Polls created before
            # day menus existed have no style and were posted with buttons;
            # defaulting the other way registers select handlers against a
            # message full of buttons, and every click answers "interaction
            # failed" on a poll that is already live.
            use_buttons = poll.get('style', 'buttons') == 'buttons'
            view = (AvailabilityView if use_buttons else AvailabilityDropdownView)(
                manager, poll_id, blocks, verified_role_name)
            bot.add_view(view, message_id=message_id)
            logger.info(f'Restored availability view for {poll_id}')

        except Exception as e:
            logger.error(f'Failed to restore availability view for {poll_id}: {e}')
