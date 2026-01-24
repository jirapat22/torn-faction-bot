"""
Chain Tracking Module
Tracks faction chains and provides chain statistics

Features:
- /chain command - Show current chain status
- /chainreport command - Show last completed chain report
- Automatic updates to #chaining every 30 minutes during active chains
"""

import discord
from datetime import datetime
from utils.helpers import load_json, save_json, create_embed, format_timestamp, get_current_timestamp
from utils.torn_api import fetch_faction_data
from config import (
    CHAINING_CHANNEL_ID,
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL
)


class ChainTracker:
    """Tracks faction chains and provides statistics"""

    def __init__(self, bot):
        self.bot = bot
        self.last_chain_count = 0
        self.was_chain_active = False  # Track if chain was active in last check
        self.chain_data_file = 'data/chain_history.json'
        self.chain_state_file = 'data/chain_state.json'
        self.chain_history = {}
        self.last_reported_chain_id = None  # Track last reported chain to prevent duplicates

    async def load_chain_history(self):
        """Load chain history from JSON file"""
        self.chain_history = load_json(self.chain_data_file, default={})

        # Load chain state (last reported chain ID)
        state = load_json(self.chain_state_file, default={})
        self.last_reported_chain_id = state.get('last_reported_chain_id')

        print(f'Loaded chain history with {len(self.chain_history)} records')
        if self.last_reported_chain_id:
            print(f'Last reported chain: #{self.last_reported_chain_id}')

    async def save_chain_history(self):
        """Save chain history to JSON file"""
        save_json(self.chain_data_file, self.chain_history)

    async def get_current_chain(self):
        """
        Get current chain status from Torn API

        Returns:
            Dict with chain data or None if error/no chain
        """
        data = await fetch_faction_data('chain')

        if not data:
            return None

        chain = data.get('chain', {})

        # Check if chain is active
        if chain.get('current', 0) == 0:
            return None

        return chain

    async def get_chain_report(self):
        """
        Get last completed chain report from Torn API

        Returns:
            Dict with chain report data or None if error
        """
        data = await fetch_faction_data('chainreport')

        if not data:
            return None

        chainreport = data.get('chainreport', {})

        if not chainreport:
            return None

        return chainreport

    async def format_chain_embed(self, chain_data):
        """
        Create a Discord embed for current chain status

        Args:
            chain_data: Chain data from Torn API

        Returns:
            discord.Embed
        """
        if not chain_data:
            return create_embed(
                title='No Active Chain',
                description='There is no active chain at the moment.',
                color=EMBED_COLOR_INFO
            )

        current = chain_data.get('current', 0)
        max_chain = chain_data.get('max', 0)
        timeout = chain_data.get('timeout', 0)
        cooldown = chain_data.get('cooldown', 0)
        modifier = chain_data.get('modifier', 1.0)

        # Determine color based on timeout
        now = get_current_timestamp()
        time_remaining = timeout - now

        if time_remaining < 60:  # Less than 1 minute
            color = EMBED_COLOR_CRITICAL
            status = '🔴 CRITICAL - Chain about to timeout!'
        elif time_remaining < 300:  # Less than 5 minutes
            color = EMBED_COLOR_WARNING
            status = '⚠️ Warning - Chain timeout soon'
        else:
            color = EMBED_COLOR_SUCCESS
            status = '✅ Chain Active'

        # Build embed
        embed = create_embed(
            title=f'Current Chain: {current} hits',
            description=status,
            color=color,
            fields=[
                {'name': 'Current Chain', 'value': str(current), 'inline': True},
                {'name': 'Max Chain', 'value': str(max_chain), 'inline': True},
                {'name': 'Respect Modifier', 'value': f'{modifier:.2f}x', 'inline': True},
                {'name': 'Timeout', 'value': format_timestamp(timeout, 'R'), 'inline': True},
                {'name': 'Cooldown Ends', 'value': format_timestamp(cooldown, 'R') if cooldown > now else 'Ready', 'inline': True}
            ]
        )

        return embed

    async def format_chainreport_embed(self, chainreport):
        """
        Create a Discord embed for chain report

        Args:
            chainreport: Chain report data from Torn API

        Returns:
            discord.Embed
        """
        if not chainreport:
            return create_embed(
                title='No Chain Report',
                description='No completed chains found.',
                color=EMBED_COLOR_INFO
            )

        # Get the most recent chain
        chain_id = chainreport.get('chain', 0)
        start_time = chainreport.get('start', 0)
        end_time = chainreport.get('end', 0)
        total_respect = chainreport.get('respect', 0)

        # Get members data (keyed by user ID)
        members = chainreport.get('members', {})

        # Fetch faction members to get names
        from utils.torn_api import fetch_faction_members
        faction_members = await fetch_faction_members()

        # Build list with names and stats
        member_list = []
        for user_id, stats in members.items():
            # Get member name from faction data
            member_name = 'Unknown'
            if faction_members and user_id in faction_members:
                member_name = faction_members[user_id].get('name', f'ID:{user_id}')
            else:
                member_name = f'ID:{user_id}'

            member_list.append({
                'name': member_name,
                'hits': stats.get('hits', 0),
                'respect': stats.get('respect', 0)
            })

        # Sort members by respect (since hits might be 0)
        sorted_members = sorted(
            member_list,
            key=lambda x: x['respect'],
            reverse=True
        )

        # Calculate total hits
        total_hits = sum(m['hits'] for m in member_list)

        # Build embed
        embed = create_embed(
            title=f'Chain Report #{chain_id}',
            description=f'Chain completed {format_timestamp(end_time, "R")}',
            color=EMBED_COLOR_SUCCESS,
            fields=[
                {'name': 'Total Hits', 'value': str(total_hits) if total_hits > 0 else 'N/A', 'inline': True},
                {'name': 'Total Respect', 'value': f'{total_respect:.2f}', 'inline': True},
                {'name': 'Started', 'value': format_timestamp(start_time, 'R'), 'inline': True}
            ]
        )

        # Add top contributors (max 5)
        if sorted_members:
            top_contributors = '\n'.join([
                f'{i+1}. **{m["name"]}**: {m["respect"]:.2f} respect{f" ({m['hits']} hits)" if m["hits"] > 0 else ""}'
                for i, m in enumerate(sorted_members[:5])
            ])
            embed.add_field(
                name='Top Contributors',
                value=top_contributors,
                inline=False
            )

        return embed

    async def check_chain_update(self):
        """
        Check if chain count changed and post update if needed
        Auto-posts chain report when chain ends
        Called by the background task every minute
        """
        # Get current chain
        chain = await self.get_current_chain()
        channel = self.bot.get_channel(CHAINING_CHANNEL_ID)

        if not channel:
            print(f'Warning: Could not find #chaining channel (ID: {CHAINING_CHANNEL_ID})')
            return

        # Detect chain END (was active, now not)
        if self.was_chain_active and not chain:
            print('Chain ended! Checking for chain report...')

            # Get chain report
            chainreport = await self.get_chain_report()
            if chainreport:
                chain_id = chainreport.get('chain', 0)

                # Check if we've already reported this chain
                if chain_id != self.last_reported_chain_id:
                    # Post chain report
                    report_embed = await self.format_chainreport_embed(chainreport)
                    await channel.send('🏁 **Chain Complete!**', embed=report_embed)
                    print(f'Posted chain completion report for chain #{chain_id}')

                    # Save this as last reported chain
                    self.last_reported_chain_id = chain_id
                    save_json(self.chain_state_file, {'last_reported_chain_id': chain_id})
                else:
                    print(f'Chain #{chain_id} already reported, skipping duplicate')

            # Reset state
            self.was_chain_active = False
            self.last_chain_count = 0
            return

        # No active chain
        if not chain:
            return

        # Chain is active
        current_count = chain.get('current', 0)

        # Mark chain as active if this is first detection
        if not self.was_chain_active:
            self.was_chain_active = True
            print(f'Chain detected with {current_count} hits')

        # Check if count changed
        if current_count != self.last_chain_count and self.last_chain_count != 0:
            # Post update to #chaining channel
            embed = await self.format_chain_embed(chain)
            await channel.send(embed=embed)
            print(f'Posted chain update: {current_count} hits')

        # Update last count
        self.last_chain_count = current_count

        # Save chain data to history
        chain_id = f'chain_{get_current_timestamp()}'
        self.chain_history[chain_id] = {
            'count': current_count,
            'timestamp': get_current_timestamp(),
            'max': chain.get('max', 0),
            'modifier': chain.get('modifier', 1.0)
        }
        await self.save_chain_history()
