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
    EMBED_COLOR_CRITICAL,
)


class ChainTracker:
    """Tracks faction chains and provides statistics"""

    def __init__(self, bot):
        self.bot = bot
        self.last_chain_count = 0
        self.last_chain_start = 0  # Start timestamp of the chain we're currently tracking
        self.was_chain_active = False  # Track if chain was active in last check
        self.chain_data_file = 'data/chain_history.json'
        self.chain_state_file = 'data/chain_state.json'
        self.chain_session_file = 'data/chain_session.json'
        self.chain_history = {}
        self.chain_session = {'chains': []}  # Per-member data for chains in current session
        self.last_reported_chain_id = None  # Track last reported chain to prevent duplicates
        self.chain_end_checks = 0  # Counter for consecutive "no chain" checks (prevents false completion)

    async def load_chain_history(self):
        """Load chain history from JSON file"""
        self.chain_history = load_json(self.chain_data_file, default={})

        # Load chain state (last reported chain ID + last chain count)
        state = load_json(self.chain_state_file, default={})
        self.last_reported_chain_id = state.get('last_reported_chain_id')
        self.last_chain_count = state.get('last_chain_count', 0)
        self.last_chain_start = state.get('last_chain_start', 0)

        print(f'Loaded chain history with {len(self.chain_history)} records')
        if self.last_reported_chain_id:
            print(f'Last reported chain: #{self.last_reported_chain_id}')
        if self.last_chain_count > 0:
            print(f'Restored last chain count: {self.last_chain_count}')

    async def save_chain_history(self):
        """Save chain history to JSON file"""
        save_json(self.chain_data_file, self.chain_history)

    async def load_chain_session(self):
        """Load chain session data (per-member stats across chains). Auto-clears if older than 24h."""
        data = load_json(self.chain_session_file, default={'chains': [], 'version': 2})
        # Clear v1 data (had broken chainId fetch producing duplicate member data)
        if data.get('version', 1) < 2:
            print('Clearing stale chain session data (v1 format)')
            self.chain_session = {'chains': [], 'version': 2}
            await self.save_chain_session()
            return
        # Auto-clear if oldest chain is older than 24 hours
        now = get_current_timestamp()
        chains = data.get('chains', [])
        chains = [c for c in chains if now - c.get('timestamp', 0) < 86400]
        self.chain_session = {'chains': chains, 'version': 2}
        if chains:
            print(f'Loaded chain session with {len(chains)} chain(s) in last 24h')

    async def save_chain_session(self):
        """Save chain session data to disk"""
        save_json(self.chain_session_file, self.chain_session)

    async def _save_chain_member_data(self, chainreport):
        """Save per-member chain data from chainreport to session for aggregation."""
        chain_id = chainreport.get('chain', 0)
        members = chainreport.get('members', {})

        # Don't save duplicates
        for existing in self.chain_session.get('chains', []):
            if existing.get('chain_id') == chain_id:
                return

        # Auto-clear entries older than 24 hours
        now = get_current_timestamp()
        self.chain_session['chains'] = [
            c for c in self.chain_session.get('chains', [])
            if now - c.get('timestamp', 0) < 86400
        ]

        self.chain_session['chains'].append({
            'chain_id': chain_id,
            'timestamp': now,
            'respect': chainreport.get('respect', 0),
            'start': chainreport.get('start', 0),
            'end': chainreport.get('end', 0),
            'members': members
        })

        await self.save_chain_session()
        print(f'Saved chain #{chain_id} member data to session ({len(members)} members)')

    async def _fetch_chains_list(self):
        """Fetch list of completed chains from Torn API."""
        data = await fetch_faction_data('chains')
        if not data:
            return {}
        return data.get('chains', {})

    async def get_aggregated_chain_data(self):
        """
        Aggregate per-member data across all chains in the last 24h.
        Uses saved session data (auto-saved when chains end) + current chainreport.
        Also fetches the chains list to show total respect for chains without per-member data.

        Returns:
            Dict with 'members' (aggregated), 'chain_count', 'total_respect',
            'chains' (with per-member data), 'other_chains' (totals only)
        """
        now = get_current_timestamp()

        # Load saved session data (chains with per-member stats)
        saved_chains = [
            c for c in self.chain_session.get('chains', [])
            if now - c.get('timestamp', 0) < 86400
        ]
        saved_chain_ids = {c.get('chain_id') for c in saved_chains}

        # Include the current chainreport (most recent completed) if not already saved
        current_report = await self.get_chain_report()
        if current_report and current_report.get('members'):
            report_chain_id = current_report.get('chain', 0)
            if report_chain_id not in saved_chain_ids:
                saved_chains.append({
                    'chain_id': report_chain_id,
                    'timestamp': current_report.get('end', now),
                    'respect': float(current_report.get('respect', 0)),
                    'start': current_report.get('start', 0),
                    'end': current_report.get('end', 0),
                    'members': current_report.get('members', {})
                })
                saved_chain_ids.add(report_chain_id)

        # Fetch chains list to find other chains in last 24h (totals only, no per-member)
        chains_list = await self._fetch_chains_list()
        other_chains = []
        for cid, cdata in chains_list.items():
            end_time = cdata.get('end', 0)
            chain_num = cdata.get('chain', int(cid))
            if end_time and now - end_time < 86400:
                # Check if we already have per-member data for this chain
                if int(cid) not in saved_chain_ids and chain_num not in saved_chain_ids:
                    other_chains.append({
                        'chain_id': chain_num,
                        'respect': float(cdata.get('respect', 0)),
                        'start': cdata.get('start', 0),
                        'end': cdata.get('end', 0),
                    })

        if not saved_chains and not other_chains:
            return None

        # Aggregate per-member stats from chains we have data for
        aggregated = {}
        total_respect = 0.0
        chain_summaries = []

        for chain_data in saved_chains:
            chain_respect = float(chain_data.get('respect', 0))
            total_respect += chain_respect
            chain_summaries.append({
                'chain_id': chain_data.get('chain_id'),
                'respect': chain_respect,
                'start': chain_data.get('start', 0),
                'end': chain_data.get('end', 0),
                'has_members': True,
            })

            for user_id, stats in chain_data.get('members', {}).items():
                if user_id not in aggregated:
                    aggregated[user_id] = {'attacks': 0, 'respect': 0.0}
                aggregated[user_id]['attacks'] += int(stats.get('attacks', 0))
                aggregated[user_id]['respect'] += float(stats.get('respect', 0.0))

        # Add other chains to totals (no per-member data)
        for oc in other_chains:
            total_respect += oc['respect']
            chain_summaries.append({
                'chain_id': oc['chain_id'],
                'respect': oc['respect'],
                'start': oc['start'],
                'end': oc['end'],
                'has_members': False,
            })

        return {
            'members': aggregated,
            'chain_count': len(saved_chains) + len(other_chains),
            'chains_with_data': len(saved_chains),
            'total_respect': total_respect,
            'chains': chain_summaries
        }

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

    async def format_chain_embed(self, chain_data, display_count=None):
        """
        Create a Discord embed for current chain status

        Args:
            chain_data: Chain data from Torn API
            display_count: Override count for display (e.g. milestone value)

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
        show_count = display_count if display_count is not None else current
        timeout = chain_data.get('timeout', 0)
        modifier = float(chain_data.get('modifier', 1.0))

        # Determine color based on timeout
        now = get_current_timestamp()
        time_remaining = timeout - now if timeout > 0 else 0

        # Check if timeout is valid (should be in the future, not 56 years ago)
        timeout_valid = timeout > now and timeout < (now + 86400)  # Within 24 hours

        if time_remaining < 60 and timeout_valid:  # Less than 1 minute
            color = EMBED_COLOR_CRITICAL
            status = '🔴 CRITICAL - Chain about to timeout!'
        elif time_remaining < 300 and timeout_valid:  # Less than 5 minutes
            color = EMBED_COLOR_WARNING
            status = '⚠️ Warning - Chain timeout soon'
        else:
            color = EMBED_COLOR_SUCCESS
            status = '✅ Chain Active'

        # Build fields
        fields = [
            {'name': 'Current Chain', 'value': str(show_count), 'inline': True},
            {'name': 'Respect Modifier', 'value': f'{modifier:.2f}x', 'inline': True}
        ]

        # Only show timeout if it's valid
        if timeout_valid:
            fields.append({'name': 'Timeout', 'value': format_timestamp(timeout, 'R'), 'inline': True})

        # Build embed
        embed = create_embed(
            title=f'Current Chain: {show_count} hits',
            description=status,
            color=color,
            fields=fields
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
                'hits': stats.get('attacks', 0),
                'respect': stats.get('respect', 0)
            })

        # Sort members by hits, then respect as tiebreaker
        sorted_members = sorted(
            member_list,
            key=lambda x: (x['hits'], x['respect']),
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
                f'{i+1}. **{m["name"]}**: {m["hits"]} hits' + (f' ({m["respect"]:.2f} respect)' if m["respect"] > 0 else '')
                for i, m in enumerate(sorted_members[:5])
            ])
            embed.add_field(
                name='Top Contributors',
                value=top_contributors,
                inline=False
            )

        return embed

    async def format_chainboard_embeds(self, aggregated_data):
        """
        Create Discord embeds for full chain participation board.
        Shows ALL faction members with combined stats across all chains in session.

        Args:
            aggregated_data: Dict from get_aggregated_chain_data() with 'members', 'chain_count', etc.

        Returns:
            discord.Embed or list of discord.Embed
        """
        if not aggregated_data:
            return create_embed(
                title='No Chain Report',
                description='No completed chains found in the last 24 hours.',
                color=EMBED_COLOR_INFO
            )

        chain_count = aggregated_data['chain_count']
        total_respect = aggregated_data['total_respect']
        chain_members = aggregated_data['members']
        chain_summaries = aggregated_data['chains']

        # Fetch full faction roster
        from utils.torn_api import fetch_faction_members
        faction_members = await fetch_faction_members()

        if not faction_members:
            return create_embed(
                title='API Error',
                description='Could not fetch faction member data.',
                color=EMBED_COLOR_CRITICAL
            )

        # Build member list with combined chain stats
        member_list = []
        for user_id, member_data in faction_members.items():
            member_name = member_data.get('name', f'ID:{user_id}')
            chain_data = chain_members.get(user_id, {})
            member_list.append({
                'name': member_name,
                'hits': chain_data.get('attacks', 0),
                'respect': chain_data.get('respect', 0.0),
            })

        sorted_members = sorted(member_list, key=lambda x: (x['hits'], x['respect']), reverse=True)

        total_hits = sum(m['hits'] for m in member_list)
        participants = len([m for m in member_list if m['hits'] > 0])

        # Build description with chain summaries
        chains_with_data = aggregated_data.get('chains_with_data', chain_count)
        if chain_count == 1:
            c = chain_summaries[0]
            desc = (
                f'**Chain #{c["chain_id"]}**\n'
                f'Completed {format_timestamp(c["end"], "R")} | '
                f'Started {format_timestamp(c["start"], "R")}'
            )
        else:
            chain_lines = []
            for c in sorted(chain_summaries, key=lambda x: x.get('end', 0)):
                marker = '' if c.get('has_members', True) else ' *(no member data)*'
                chain_lines.append(
                    f'Chain #{c["chain_id"]} — {c["respect"]:.0f} respect '
                    f'({format_timestamp(c["end"], "R")}){marker}'
                )
            header = f'**{chain_count} Chains (last 24h)**'
            if chains_with_data < chain_count:
                header += f'\nMember breakdown from {chains_with_data} chain(s) — older chains only show totals'
            desc = header + '\n' + '\n'.join(chain_lines)

        # Build embeds
        embeds = []
        embed = create_embed(
            title='Chain Participation Board',
            description=desc,
            color=EMBED_COLOR_SUCCESS,
            fields=[
                {'name': 'Total Hits', 'value': str(total_hits) if total_hits > 0 else 'N/A', 'inline': True},
                {'name': 'Total Respect', 'value': f'{total_respect:.2f}', 'inline': True},
                {'name': 'Participated', 'value': f'{participants}/{len(member_list)}', 'inline': True},
            ]
        )

        # Build monospace table
        max_name = max((len(m['name']) for m in sorted_members), default=10)
        max_name = min(max_name, 15)

        header = f'{"#":>2}  {"Name":<{max_name}}  {"Hits":>4}  {"Respect":>8}'
        separator = '-' * len(header)
        table_lines = [header, separator]

        for i, m in enumerate(sorted_members):
            name = m['name'][:max_name]
            r = m['respect']
            if r >= 1_000_000:
                respect_str = f'{r/1_000_000:.1f}M'
            elif r >= 10_000:
                respect_str = f'{r/1_000:.1f}K'
            elif r > 0:
                respect_str = f'{r:.1f}'
            else:
                respect_str = '0'
            line = f'{i+1:>2}  {name:<{max_name}}  {m["hits"]:>4}  {respect_str:>8}'
            table_lines.append(line)

        # Split into code block chunks
        table_chunks = []
        current_chunk = []
        current_length = 0

        for line in table_lines:
            if current_length + len(line) + 1 > 1024 - 8 and current_chunk:
                table_chunks.append('```\n' + '\n'.join(current_chunk) + '\n```')
                current_chunk = [line]
                current_length = len(line)
            else:
                current_chunk.append(line)
                current_length += len(line) + 1

        if current_chunk:
            table_chunks.append('```\n' + '\n'.join(current_chunk) + '\n```')

        for idx, chunk in enumerate(table_chunks):
            if len(embed.fields) >= 24:
                embeds.append(embed)
                embed = create_embed(
                    title='Chain Board (continued)',
                    description='',
                    color=EMBED_COLOR_SUCCESS
                )
            label = f'All Members ({len(sorted_members)})' if idx == 0 else 'Members (cont.)'
            embed.add_field(name=label, value=chunk, inline=False)

        embeds.append(embed)
        return embeds[0] if len(embeds) == 1 else embeds

    async def has_active_chain_event(self):
        """
        Check if there's an active Chain Event or War Event in Discord scheduled events
        (Chains happen during both chain events and war events)

        Returns:
            bool: True if a chain or war event is active, False otherwise
        """
        try:
            for guild in self.bot.guilds:
                # Get all scheduled events
                events = guild.scheduled_events

                for event in events:
                    # Check if event name contains "Chain" or "War" and is active/started
                    event_name = event.name.lower()
                    if 'chain' in event_name or 'war' in event_name:
                        # Check if event is active (started or scheduled for now)
                        from datetime import datetime, timezone
                        now = datetime.now(timezone.utc)

                        # Event is active if it has started and not ended
                        if event.status.name in ['active', 'scheduled']:
                            if event.start_time <= now:
                                # Event has started
                                if event.end_time is None or event.end_time > now:
                                    return True

            return False
        except Exception as e:
            print(f'Error checking chain events: {e}')
            return False

    async def check_chain_update(self):
        """
        Check if chain count changed and post update if needed
        Auto-posts chain report when chain ends
        Called by the background task every minute

        IMPORTANT: Only posts updates when:
        - Chain has >= 10 hits (official chain threshold)
        - There's an active Chain Event in Discord
        """
        # Get current chain
        chain = await self.get_current_chain()
        channel = self.bot.get_channel(CHAINING_CHANNEL_ID)

        if not channel:
            print(f'Warning: Could not find #chaining channel (ID: {CHAINING_CHANNEL_ID})')
            return

        # Detect chain END (was active, now not)
        # Require 3 consecutive "no chain" checks to prevent false completion from API glitches
        if self.was_chain_active and not chain:
            self.chain_end_checks += 1
            print(f'Chain not detected, check {self.chain_end_checks}/3...')

            if self.chain_end_checks < 3:
                # Wait for more checks before declaring chain ended
                return

            print('Chain confirmed ended after 3 checks. Posting report...')

            # Get chain report
            chainreport = await self.get_chain_report()
            if chainreport:
                chain_id = chainreport.get('chain', 0)

                # Save per-member data for aggregation (always, even if already reported)
                await self._save_chain_member_data(chainreport)

                # Check if we've already reported this chain
                if chain_id != self.last_reported_chain_id:
                    # Post chain report
                    report_embed = await self.format_chainreport_embed(chainreport)

                    await channel.send('🏁 **Chain Complete!**', embed=report_embed)
                    print(f'Posted chain completion report for chain #{chain_id}')

                    # Save this as last reported chain
                    self.last_reported_chain_id = chain_id
                    save_json(self.chain_state_file, {
                        'last_reported_chain_id': chain_id,
                        'last_chain_count': self.last_chain_count,
                        'last_chain_start': self.last_chain_start,
                    })
                else:
                    print(f'Chain #{chain_id} already reported, skipping duplicate')

            # Reset state - keep last_chain_count so if chain comes back
            # after an API glitch, we don't re-announce old milestones
            self.was_chain_active = False
            self.chain_end_checks = 0
            return

        # No active chain
        if not chain:
            return

        # Chain is active - reset end check counter
        self.chain_end_checks = 0
        current_count = chain.get('current', 0)

        # Only track chains with 10+ hits (official chain threshold)
        if current_count < 10:
            return

        # Mark chain as active if this is first detection with 10+ hits
        chain_start = chain.get('start', 0)
        if not self.was_chain_active:
            self.was_chain_active = True

            # Use start timestamp to distinguish a genuinely new chain from an API glitch.
            # If start differs from what we were tracking, it's a new chain → reset count.
            # If start matches, it's the same chain resuming after a glitch → keep count
            # so we don't re-announce milestones we've already posted.
            if chain_start and self.last_chain_start and chain_start != self.last_chain_start:
                self.last_chain_count = 0
                print(f'New chain detected (start={chain_start}) with {current_count} hits')
            elif current_count < self.last_chain_count:
                # No start timestamps to compare — fall back to count heuristic.
                # A count lower than last known is likely a new chain.
                self.last_chain_count = 0
                print(f'New chain detected (count reset) with {current_count} hits')
            else:
                print(f'Chain resumed at {current_count} hits (was tracking at {self.last_chain_count})')

        # Always keep last_chain_start in sync
        if chain_start:
            self.last_chain_start = chain_start

        # Only post at bonus milestones
        should_post = False
        hit_milestone = None

        milestones = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
        for milestone in milestones:
            if current_count >= milestone and self.last_chain_count < milestone:
                should_post = True
                hit_milestone = milestone
                break

        if should_post:
            # Use milestone value for the message so it shows "10 hits" not "11 hits"
            display_count = hit_milestone if hit_milestone else current_count
            # Post update to #chaining channel (no ping for regular updates)
            embed = await self.format_chain_embed(chain, display_count=display_count)
            await channel.send(f'**Chain Update: {display_count} hits!**', embed=embed)
            print(f'Posted chain update: {display_count} hits (actual: {current_count})')

        # Update last count and persist to disk (survives restarts)
        self.last_chain_count = current_count
        save_json(self.chain_state_file, {
            'last_reported_chain_id': self.last_reported_chain_id,
            'last_chain_count': self.last_chain_count,
            'last_chain_start': self.last_chain_start,
        })

        # Save chain data to history (keep last 100 entries max)
        chain_id = f'chain_{get_current_timestamp()}'
        self.chain_history[chain_id] = {
            'count': current_count,
            'timestamp': get_current_timestamp(),
            'max': chain.get('max', 0),
            'modifier': chain.get('modifier', 1.0)
        }

        # Prune old entries if history is too large
        if len(self.chain_history) > 100:
            sorted_keys = sorted(self.chain_history.keys())
            for old_key in sorted_keys[:-100]:
                del self.chain_history[old_key]

        await self.save_chain_history()
