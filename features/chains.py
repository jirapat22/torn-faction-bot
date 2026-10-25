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


import logging

logger = logging.getLogger(__name__)

# ===== Chain break warnings =====
# The chain guide's single operational rule is "under 30 seconds and nobody's
# hit? You hit" - but nothing ever told anyone, so it only worked for whoever
# happened to have the timer open.
#
# Gated on 25+ because the noise lives below it. Measured over 100 chains in
# 147 days: 10+ happens 0.68/day, 25+ only 0.19/day (once every 5 days), so the
# gate alone filters out the one-person chains without needing anyone to flag
# an event first. Inside a war the timer dropped under 30s about 6.8 times a
# day, which is the realistic ceiling on how often this can fire.
CHAIN_ALERT_MIN_COUNT = 25
CHAIN_ALERT_SECONDS_LEFT = 30
CHAIN_ALERT_COOLDOWN = 300

def chain_seconds_left(timeout, now):
    """
    Seconds until the chain breaks, from the API's `timeout` field.

    Torn sends a COUNTDOWN in seconds ("Seconds until chain breaks", per its
    own OpenAPI spec), not a clock time. Treating it as a timestamp made
    `timeout - now` hugely negative, so the "chain dying" warning never fired
    once in 650+ messages, and the status view rendered the timer as "56 years
    ago" (245 seconds after 1970) before a guard hid it. A value that does look
    like a timestamp is still converted, in case Torn ever switches to one.
    """
    timeout = int(timeout or 0)
    if timeout > 1_000_000_000:
        return timeout - int(now)
    return timeout


class ChainTracker:
    """Tracks faction chains and provides statistics"""

    def __init__(self, bot):
        self.bot = bot
        self.last_chain_count = 0
        self._last_chain_warning = 0  # ts of the last about-to-break ping
        self.last_chain_start = 0  # Start timestamp of the chain we're currently tracking
        self.was_chain_active = False  # Track if chain was active in last check
        self.chain_data_file = 'data/chain_history.json'
        self.chain_state_file = 'data/chain_state.json'
        self.chain_session_file = 'data/chain_session.json'
        self.chain_history = {}
        self.chain_session = {'chains': []}  # Per-member data for chains in current session
        self.last_reported_chain_id = None  # Track last reported chain to prevent duplicates
        # The start time is what really identifies a chain: the "id" above is
        # the hit count, so two different chains that both end on 231 hits
        # would look like the same one and the second would never be posted.
        self.last_reported_chain_start = 0
        self.chain_end_checks = 0  # Counter for consecutive "no chain" checks (prevents false completion)

    async def load_chain_history(self):
        """Load chain history from JSON file"""
        self.chain_history = load_json(self.chain_data_file, default={})

        # Load chain state (last reported chain ID + last chain count)
        state = load_json(self.chain_state_file, default={})
        self.last_reported_chain_id = state.get('last_reported_chain_id')
        self.last_chain_count = state.get('last_chain_count', 0)
        self.last_chain_start = state.get('last_chain_start', 0)
        self.last_reported_chain_start = state.get('last_reported_chain_start', 0)

        logger.info(f'Loaded chain history with {len(self.chain_history)} records')
        if self.last_reported_chain_id:
            logger.info(f'Last reported chain: #{self.last_reported_chain_id}')
        if self.last_chain_count > 0:
            logger.info(f'Restored last chain count: {self.last_chain_count}')

    async def save_chain_history(self):
        """Save chain history to JSON file"""
        save_json(self.chain_data_file, self.chain_history)

    async def load_chain_session(self):
        """Load chain session data (per-member stats across chains). Auto-clears if older than 24h."""
        data = load_json(self.chain_session_file, default={'chains': [], 'version': 2})
        # Clear v1 data (had broken chainId fetch producing duplicate member data)
        if data.get('version', 1) < 2:
            logger.info('Clearing stale chain session data (v1 format)')
            self.chain_session = {'chains': [], 'version': 2}
            await self.save_chain_session()
            return
        # Auto-clear if oldest chain is older than 24 hours
        now = get_current_timestamp()
        chains = data.get('chains', [])
        chains = [c for c in chains if now - c.get('timestamp', 0) < 86400]
        self.chain_session = {'chains': chains, 'version': 2}
        if chains:
            logger.info(f'Loaded chain session with {len(chains)} chain(s) in last 24h')

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
        logger.info(f'Saved chain #{chain_id} member data to session ({len(members)} members)')

    @staticmethod
    def contributors_of(sorted_members):
        """
        Who to list under "Top Contributors".

        Only people who hit. The list used to fill five slots regardless, so
        chain #13, which was 13 hits between two members, printed three more
        at 0 hits each under that heading - which says they took part when
        they did not.

        A separate function rather than an inline filter so the rule can be
        tested against the real code instead of a copy of it.
        """
        return [m for m in (sorted_members or []) if m.get('hits', 0) > 0]

    async def _fetch_chains_list(self):
        """
        Completed chains from Torn, or None when the API could not be read.

        None rather than {}: an empty dict means "there were no other chains"
        and silently lowers the respect total on the board, with nothing
        saying the number is short. The same None-vs-empty rule the rest of
        this repo runs on.
        """
        data = await fetch_faction_data('chains')
        if not data:
            return None
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
        # None means the call failed. The per-member chains we already saved
        # are still good, so the board is still worth showing; it just has to
        # say the respect total may be short rather than present it as the
        # whole picture.
        chains_list_ok = chains_list is not None
        other_chains = []
        for cid, cdata in (chains_list or {}).items():
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
            'chains': chain_summaries,
            # False means Torn's chain list could not be read, so any chain
            # without saved per-member data is missing from these totals.
            'chains_list_ok': chains_list_ok,
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
        cooldown = int(chain_data.get('cooldown') or 0)
        if cooldown > 0:
            return create_embed(
                title=f'Chain over: {current} hits',
                description=('\U0001f9ca On cooldown. A new chain can start '
                             f'<t:{get_current_timestamp() + cooldown}:R>.'),
                color=EMBED_COLOR_INFO)
        timeout = chain_data.get('timeout', 0)
        modifier = float(chain_data.get('modifier', 1.0))

        # Determine color based on time left before the chain breaks
        now = get_current_timestamp()
        time_remaining = chain_seconds_left(timeout, now)
        # A live chain's timer is 0-300s (every hit resets it to 5:00);
        # anything else is not a number worth showing.
        timeout_valid = 0 < time_remaining <= 300

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
            fields.append({'name': 'Breaks', 'value': format_timestamp(now + time_remaining, 'R'), 'inline': True})

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

        # Add top contributors (max 5). Only people who actually hit:
        # the list used to fill five slots regardless, so a 13 hit chain
        # between two members printed three more under the heading "Top
        # Contributors" with 0 hits each, which reads as having taken part.
        contributors = self.contributors_of(sorted_members)
        if contributors:
            top_contributors = '\n'.join([
                f'{i+1}. **{m["name"]}**: {m["hits"]} hits' + (f' ({m["respect"]:.2f} respect)' if m["respect"] > 0 else '')
                for i, m in enumerate(contributors[:5])
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
                    f'Chain #{c["chain_id"]} · {c["respect"]:.0f} respect '
                    f'({format_timestamp(c["end"], "R")}){marker}'
                )
            header = f'**{chain_count} Chains (last 24h)**'
            if chains_with_data < chain_count:
                header += f'\nMember breakdown from {chains_with_data} chain(s) · older chains only show totals'
            if not aggregated_data.get('chains_list_ok', True):
                # Say the total is short rather than present a quietly low
                # number as the whole picture.
                header += ('\n⚠️ Torn\'s chain list could not be read, '
                           'so any chain without saved member data is missing '
                           'from these totals.')
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
            logger.error(f'Error checking chain events: {e}')
            return False

    async def _report_chain_end(self, channel, expect_start=None, cooldown_ends=None):
        """
        Post the finished chain's report once. Returns True when the chain is
        dealt with (posted now, or already posted earlier), False when the
        report is not ready yet and the caller should try again next tick.

        expect_start: the start time of the chain that just ended. Torn can
        take a moment to swap in the new report, and posting the PREVIOUS
        chain's report as this one's would be worse than a minute's wait.
        """
        chainreport = await self.get_chain_report()
        if not chainreport:
            return expect_start is None
        report_start = int(chainreport.get('start') or 0)
        if expect_start and report_start != int(expect_start):
            logger.info('Chain over, but Torn has not published its report yet; retrying')
            return False

        chain_id = chainreport.get('chain', 0)
        # Save per-member data for aggregation (always, even if already reported)
        await self._save_chain_member_data(chainreport)

        already = (report_start and report_start == self.last_reported_chain_start) or \
                  (not report_start and chain_id == self.last_reported_chain_id)
        if already:
            logger.info(f'Chain #{chain_id} already reported, skipping duplicate')
            return True

        report_embed = await self.format_chainreport_embed(chainreport)
        header = '\U0001f3c1 **Chain Complete!**'
        if cooldown_ends:
            header += f' Faction is on cooldown, a new chain can start <t:{int(cooldown_ends)}:R>.'
        await channel.send(header, embed=report_embed)
        logger.info(f'Posted chain completion report for chain #{chain_id}')

        self.last_reported_chain_id = chain_id
        self.last_reported_chain_start = report_start
        save_json(self.chain_state_file, {
            'last_reported_chain_id': chain_id,
            'last_chain_count': self.last_chain_count,
            'last_chain_start': self.last_chain_start,
            'last_reported_chain_start': self.last_reported_chain_start,
        })
        return True

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
            logger.warning(f'Warning: Could not find #chaining channel (ID: {CHAINING_CHANNEL_ID})')
            return

        # Torn says outright when a chain is over: the faction goes on
        # cooldown and the API keeps showing the final count. The old check
        # waited for the count to fall to 0, which Torn only does when the
        # cooldown ENDS, so "Chain Complete" arrived 20+ minutes late and
        # people kept hitting a dead chain. An explicit cooldown is not the
        # API glitch the 3-check rule below guards against, so it is trusted
        # on the first read.
        cooldown = int((chain or {}).get('cooldown') or 0)
        if chain and cooldown > 0:
            # Below 10 hits is not a chain (same threshold as tracking). And a
            # chain already reported needs no report fetch on every minute of
            # a 20-minute cooldown.
            reported = (chain.get('start')
                        and int(chain['start']) == int(self.last_reported_chain_start or 0))
            if int(chain.get('current') or 0) >= 10 and not reported:
                done = await self._report_chain_end(
                    channel, expect_start=chain.get('start'),
                    cooldown_ends=get_current_timestamp() + cooldown)
                if done:
                    self.was_chain_active = False
                    self.chain_end_checks = 0
            # Never fall through: a chain on cooldown is not "active", and
            # treating its frozen count as live would re-announce milestones.
            return

        # Detect chain END (was active, now not)
        # Require 3 consecutive "no chain" checks to prevent false completion from API glitches
        if self.was_chain_active and not chain:
            self.chain_end_checks += 1
            logger.info(f'Chain not detected, check {self.chain_end_checks}/3...')

            if self.chain_end_checks < 3:
                # Wait for more checks before declaring chain ended
                return

            logger.info('Chain confirmed ended after 3 checks. Posting report...')

            await self._report_chain_end(channel)

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
                logger.info(f'New chain detected (start={chain_start}) with {current_count} hits')
            elif current_count < self.last_chain_count:
                # No start timestamps to compare - fall back to count heuristic.
                # A count lower than last known is likely a new chain.
                self.last_chain_count = 0
                logger.info(f'New chain detected (count reset) with {current_count} hits')
            else:
                logger.info(f'Chain resumed at {current_count} hits (was tracking at {self.last_chain_count})')

        # Always keep last_chain_start in sync
        if chain_start:
            self.last_chain_start = chain_start

        # About to break? The milestone posts below are after-the-fact; this is
        # the only thing that can actually save a chain. Checked before them so
        # a dying chain is never queued behind a milestone announcement.
        timeout = chain.get('timeout', 0)
        now_ts = get_current_timestamp()
        seconds_left = chain_seconds_left(timeout, now_ts)
        # A timeout of 0 or past the 5:00 maximum is Torn glitching, not a
        # chain on its last legs - the status embed guards the same way.
        timeout_sane = 0 < seconds_left <= 300
        if (timeout_sane
                and current_count >= CHAIN_ALERT_MIN_COUNT
                and seconds_left <= CHAIN_ALERT_SECONDS_LEFT
                and now_ts - self._last_chain_warning >= CHAIN_ALERT_COOLDOWN):
            self._last_chain_warning = now_ts
            await channel.send(
                f'\u26a0\ufe0f **Chain dying: {current_count} hits, '
                f'{int(seconds_left)}s left.** Any successful hit resets it to 5:00. '
                f'A lost fight does NOT, so pick someone you can beat.'
            )
            logger.info(f'Chain break warning sent at {current_count} hits, '
                        f'{int(seconds_left)}s left')

        # Only post at bonus milestones
        should_post = False
        hit_milestone = None

        # Check in descending order so a chain that jumps past several
        # milestones between two 1-minute polls (plausible when multiple
        # members hit at once) announces the highest one actually reached,
        # not the lowest - ascending order would report "10 hits!" for a
        # true jump to 120, and the skipped 25/50/100 would never get
        # announced since last_chain_count moves past them regardless.
        milestones = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
        for milestone in reversed(milestones):
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
            logger.info(f'Posted chain update: {display_count} hits (actual: {current_count})')

        # Update last count and persist to disk (survives restarts)
        self.last_chain_count = current_count
        save_json(self.chain_state_file, {
            'last_reported_chain_id': self.last_reported_chain_id,
            'last_chain_count': self.last_chain_count,
            'last_chain_start': self.last_chain_start,
            'last_reported_chain_start': self.last_reported_chain_start,
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
