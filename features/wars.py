"""
War Tracking Module
Tracks faction wars and provides detailed war statistics

Features:
- /war command - Show current war status
- /warboard command - Show war participation leaderboard
- Automatic war end reports posted to #war channel
- Detailed management reports posted to private war management channel
- Tracks incoming and outgoing attacks per member
"""

import discord
from datetime import datetime
from utils.helpers import load_json, save_json, create_embed, format_timestamp, get_current_timestamp
from utils.torn_api import fetch_faction_data, fetch_faction_data_v2, fetch_faction_attacks
from config import (
    WAR_CHANNEL_ID,
    WAR_MANAGEMENT_CHANNEL_ID,
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    MANAGEMENT_PING_ROLE_ID,
    TORN_FACTION_ID,
    VERIFIED_ROLE_NAME
)


class WarTracker:
    """Tracks faction wars and provides statistics"""

    def __init__(self, bot):
        self.bot = bot
        self.was_war_active = False
        self.current_war_id = None
        self.war_data_file = 'data/war_history.json'
        self.war_history = {}
        self.enemy_watchlist = None  # Set by bot.py for auto-clear
        # Track attacks during active war
        self.current_war_attacks = {
            'outgoing': {},  # {member_id: hit_count}
            'incoming': {},  # {member_id: times_hit}
            'scores': {}     # {member_id: score}
        }

    async def load_war_history(self):
        """Load war history from JSON file"""
        self.war_history = load_json(self.war_data_file, default={})
        print(f'Loaded war history with {len(self.war_history)} records')

    async def save_war_history(self):
        """Save war history to JSON file"""
        save_json(self.war_data_file, self.war_history)

    async def _fetch_rankedwars(self):
        """Fetch ranked wars data from Torn API (v1 - works for any faction)"""
        data = await fetch_faction_data('rankedwars')
        if not data:
            return None
        rankedwars = data.get('rankedwars', {})
        if not rankedwars:
            return None
        return rankedwars

    async def _fetch_rankedwarreport(self):
        """
        Fetch ranked war report with complete per-member stats.
        Uses v1 'rankedwarreport' selection which includes attacks, defends,
        and score for every member in both factions.
        Caches the report so it remains available after the war ends.

        Returns:
            Dict with {factions: {fid: {name, score, members: {uid: stats}}}} or None
        """
        cache_file = 'data/warreport_cache.json'

        data = await fetch_faction_data('rankedwarreport')
        if data:
            report = data.get('rankedwarreport', {})
            if report:
                factions = report.get('factions', {})
                if factions:
                    # Log what we got
                    for fid, fdata in factions.items():
                        if isinstance(fdata, dict):
                            members = fdata.get('members', {})
                            print(f'War report: Faction {fid} ({fdata.get("name", "?")}) - {len(members)} members')
                    # Cache the report for after war ends
                    save_json(cache_file, report)
                    return report

        # API didn't return report — try loading from cache
        cached = load_json(cache_file, default=None)
        if cached:
            print('War report: Using cached report (API no longer returning data)')
            return cached

        print('War report: No data from API and no cache available')
        return None

    async def get_current_war(self):
        """
        Get current ACTIVE war status from Torn API

        Returns:
            Dict with war data or None if error/no war
        """
        rankedwars = await self._fetch_rankedwars()
        if not rankedwars:
            print('War: No ranked wars data found')
            return None

        # Find active war - must have started and not ended
        now = get_current_timestamp()
        for war_id, war_data in rankedwars.items():
            war = war_data.get('war', {})
            start = war.get('start', 0)
            end = war.get('end', 0)

            # Skip wars that ended more than 7 days ago
            if end and end > 0 and (now - end) > 604800:
                continue

            # War is active if it has started and either:
            # - end is 0/None (hasn't ended yet - active war)
            # - end is in the future
            if start > 0 and start <= now and (not end or end > now):
                return {
                    'war_id': war_id,
                    'data': war_data
                }

        return None

    async def get_latest_war(self):
        """
        Get the latest war (active or most recently completed).
        Used by /warboard to allow post-war review.

        Returns:
            Dict with war data and is_active flag, or None
        """
        rankedwars = await self._fetch_rankedwars()
        if not rankedwars:
            return None

        now = get_current_timestamp()

        # First try to find an active war
        for war_id, war_data in rankedwars.items():
            war = war_data.get('war', {})
            start = war.get('start', 0)
            end = war.get('end', 0)
            if start > 0 and start <= now and (not end or end > now):
                return {
                    'war_id': war_id,
                    'data': war_data,
                    'is_active': True
                }

        # No active war - find the most recently started one (for post-war review)
        latest_war = None
        latest_start = 0
        for war_id, war_data in rankedwars.items():
            war = war_data.get('war', {})
            start = war.get('start', 0)
            end = war.get('end', 0)

            # Skip wars that ended more than 30 days ago
            if end and end > 0 and (now - end) > 2592000:
                continue

            if start > latest_start:
                latest_start = start
                latest_war = {
                    'war_id': war_id,
                    'data': war_data,
                    'is_active': False
                }

        return latest_war

    async def format_war_embed(self, war_info):
        """
        Create a Discord embed for current war status

        Args:
            war_info: War data from Torn API

        Returns:
            discord.Embed
        """
        if not war_info:
            return create_embed(
                title='No Active War',
                description='There is no active ranked war at the moment.',
                color=EMBED_COLOR_INFO
            )

        war_data = war_info['data']
        war = war_data.get('war', {})
        factions = war_data.get('factions', {})

        # Get war details
        start_time = war.get('start', 0)
        end_time = war.get('end', 0)
        target = war.get('target', 0)  # Goal points to win

        # Get our faction and enemy faction by matching TORN_FACTION_ID
        our_faction = None
        enemy_faction = None

        for faction_id, faction_data in factions.items():
            if str(faction_id) == str(TORN_FACTION_ID):
                our_faction = faction_data
            else:
                enemy_faction = faction_data

        # Fallback: just use first two if ID match failed
        if not our_faction or not enemy_faction:
            faction_list = list(factions.values())
            our_faction = faction_list[0] if len(faction_list) > 0 else {}
            enemy_faction = faction_list[1] if len(faction_list) > 1 else {}

        our_score = our_faction.get('score', 0)
        enemy_score = enemy_faction.get('score', 0)
        enemy_name = enemy_faction.get('name', 'Unknown')

        # Determine color and status (NO TIED - only winning or losing)
        if our_score > enemy_score:
            color = EMBED_COLOR_SUCCESS
            status = f'✅ Winning {our_score} - {enemy_score}'
        else:  # Losing (includes equal scores - if equal, we're not winning)
            color = EMBED_COLOR_WARNING
            status = f'⚠️ Losing {our_score} - {enemy_score}'

        # Build fields
        fields = [
            {'name': 'Our Score', 'value': str(our_score), 'inline': True},
            {'name': 'Enemy Score', 'value': str(enemy_score), 'inline': True},
            {'name': 'Target Score', 'value': str(target) if target > 0 else 'N/A', 'inline': True},
            {'name': 'War ID', 'value': str(war_info['war_id']), 'inline': True},
            {'name': 'Started', 'value': format_timestamp(start_time, 'R'), 'inline': True},
        ]

        # Only show end time if it's set (0 means war hasn't ended yet)
        if end_time and end_time > 0:
            fields.append({'name': 'Ends', 'value': format_timestamp(end_time, 'R'), 'inline': True})
        else:
            fields.append({'name': 'Status', 'value': 'In Progress', 'inline': True})

        # Build embed
        embed = create_embed(
            title=f'Ranked War vs {enemy_name}',
            description=status,
            color=color,
            fields=fields
        )

        return embed

    async def _fetch_all_attacks_since(self, war_start):
        """
        Fetch ALL faction attacks since war_start by paginating through the API.
        The Torn attacks API returns ~100 attacks per call, so we page through
        using ascending sort and shifting the 'from' timestamp.

        Args:
            war_start: Unix timestamp to start fetching from

        Returns:
            Dict of {attack_id: attack_data} with all attacks since war_start
        """
        all_attacks = {}
        current_from = int(war_start)
        max_pages = 50  # Safety limit to prevent infinite loops

        for page in range(max_pages):
            attacks = await fetch_faction_attacks(from_ts=current_from, sort='asc')

            if not attacks:
                break

            # Count new attacks (not already seen)
            new_count = 0
            latest_timestamp = current_from

            for attack_id, attack in attacks.items():
                if attack_id not in all_attacks:
                    all_attacks[attack_id] = attack
                    new_count += 1

                # Track the latest timestamp to use as next page's 'from'
                ts = attack.get('timestamp_started', 0) if isinstance(attack, dict) else 0
                if ts > latest_timestamp:
                    latest_timestamp = ts

            print(f'War attacks page {page + 1}: got {len(attacks)} attacks, {new_count} new (total: {len(all_attacks)})')

            # If we got fewer than 90 attacks, we've likely reached the end
            # (API returns ~100, using 90 as threshold to account for variation)
            if len(attacks) < 90:
                break

            # If no new attacks were found, we're done
            if new_count == 0:
                break

            # Move to next page (start from latest timestamp to get next batch)
            # Add 1 second to avoid re-fetching the same last attack
            current_from = latest_timestamp + 1

        print(f'War: Fetched {len(all_attacks)} total attacks since war start')
        return all_attacks

    async def _get_war_attack_stats(self, enemy_faction_id, war_start):
        """
        Compute per-member war statistics from the faction attacks log.
        Paginates through ALL attacks since war start for accurate data.

        Args:
            enemy_faction_id: The enemy faction ID in the ranked war
            war_start: War start timestamp to filter attacks

        Returns:
            Dict of {user_id: {'war_hits': N, 'outside_hits': N, 'total_incoming': N, 'war_incoming': N, 'outside_incoming': N}}
        """
        stats = {}

        def _ensure(uid):
            if uid not in stats:
                stats[uid] = {'war_hits': 0, 'outside_hits': 0, 'chain_outside_hits': 0, 'total_incoming': 0, 'war_incoming': 0, 'outside_incoming': 0}

        try:
            attacks = await self._fetch_all_attacks_since(war_start)
            our_faction = str(TORN_FACTION_ID)
            enemy_faction = str(enemy_faction_id)

            for attack_id, attack in attacks.items():
                if not isinstance(attack, dict):
                    continue

                attacker_id = str(attack.get('attacker_id', 0))
                attacker_faction = str(attack.get('attacker_faction', 0))
                defender_id = str(attack.get('defender_id', 0))
                defender_faction = str(attack.get('defender_faction', 0))
                result = attack.get('result', '')

                # Our member attacked someone (successful attacks only)
                if attacker_faction == our_faction:
                    if result not in ('Attacked', 'Mugged', 'Hospitalized', 'Special'):
                        continue

                    _ensure(attacker_id)
                    if defender_faction == enemy_faction:
                        stats[attacker_id]['war_hits'] += 1
                    else:
                        stats[attacker_id]['outside_hits'] += 1
                        if attack.get('chain', 0) > 10:
                            stats[attacker_id]['chain_outside_hits'] += 1

                # Someone attacked our member (successful attacks only)
                elif defender_faction == our_faction and attacker_faction != our_faction:
                    if result not in ('Attacked', 'Mugged', 'Hospitalized', 'Special'):
                        continue

                    _ensure(defender_id)
                    stats[defender_id]['total_incoming'] += 1
                    # Use war modifier as fallback classification
                    modifiers = attack.get('modifiers', {})
                    war_modifier = modifiers.get('war', 1.0) if isinstance(modifiers, dict) else 1.0
                    if war_modifier > 1.0:
                        stats[defender_id]['war_incoming'] += 1
                    else:
                        stats[defender_id]['outside_incoming'] += 1

        except Exception as e:
            print(f'War: Error computing attack stats: {e}')

        return stats

    def _chunk_text(self, lines, max_length=1024):
        """Split lines into chunks that fit within Discord's field limit"""
        chunks = []
        current_chunk = []
        current_length = 0

        for line in lines:
            if current_length + len(line) + 1 > max_length and current_chunk:
                chunks.append('\n'.join(current_chunk))
                current_chunk = [line]
                current_length = len(line)
            else:
                current_chunk.append(line)
                current_length += len(line) + 1

        if current_chunk:
            chunks.append('\n'.join(current_chunk))

        return chunks

    async def format_warboard_embed(self, war_info):
        """
        Create Discord embeds for war participation leaderboard.
        Shows ALL faction members with 3 metrics each.
        Uses rankedwarreport for accurate data, falls back to attacks log.
        Returns a list of embeds if content is too large for one.

        Args:
            war_info: War data from get_latest_war()

        Returns:
            discord.Embed or list of discord.Embed
        """
        if not war_info:
            return create_embed(
                title='No War Data',
                description='No ranked war data available.',
                color=EMBED_COLOR_INFO
            )

        war_data = war_info['data']
        factions = war_data.get('factions', {})
        war = war_data.get('war', {})
        war_start = war.get('start', 0)
        is_active = war_info.get('is_active', True)

        # Find our faction and enemy faction from rankedwars data
        our_faction_data = None
        enemy_faction_id = None
        enemy_faction_data = None
        for faction_id, faction_data in factions.items():
            if str(faction_id) == str(TORN_FACTION_ID):
                our_faction_data = faction_data
            else:
                enemy_faction_id = faction_id
                enemy_faction_data = faction_data

        if not enemy_faction_id:
            return create_embed(
                title='No War Data',
                description='Could not identify war factions.',
                color=EMBED_COLOR_WARNING
            )

        # Fetch faction members (for names + full roster)
        from utils.torn_api import fetch_faction_members
        faction_members = await fetch_faction_members()

        if not faction_members:
            return create_embed(
                title='API Error',
                description='Could not fetch faction member data.',
                color=EMBED_COLOR_CRITICAL
            )

        # Try rankedwarreport first (complete and accurate per-member data)
        our_war_members = None
        enemy_war_members = None
        report = await self._fetch_rankedwarreport()
        if report:
            report_factions = report.get('factions', {})
            for fid, fdata in report_factions.items():
                if str(fid) == str(TORN_FACTION_ID):
                    our_war_members = fdata.get('members', {})
                    # Update faction scores from report (more current)
                    if fdata.get('score') is not None:
                        our_faction_data = fdata
                else:
                    enemy_war_members = fdata.get('members', {})
                    if fdata.get('score') is not None:
                        enemy_faction_data = fdata

        # Build member list with stats
        member_list = []
        data_source = 'report'

        # Always fetch attacks log for outside hits and incoming split
        attack_stats = await self._get_war_attack_stats(enemy_faction_id, war_start)

        if our_war_members:
            print(f'War: Using rankedwarreport data ({len(our_war_members)} members)')
            # Use rankedwarreport data (accurate and complete)
            for user_id, member_data in faction_members.items():
                member_name = member_data.get('name', f'ID:{user_id}')
                war_stats = our_war_members.get(user_id, {})
                log_stats = attack_stats.get(user_id, {})

                war_inc = war_stats.get('defends', 0)
                total_inc = log_stats.get('total_incoming', 0)
                outside_inc = max(0, total_inc - war_inc)

                member_list.append({
                    'name': member_name,
                    'war_hits': war_stats.get('attacks', 0),
                    'outside_hits': log_stats.get('outside_hits', 0),
                    'war_incoming': war_inc,
                    'outside_incoming': outside_inc,
                    'score': war_stats.get('score', 0),
                })
        else:
            print('War: rankedwarreport not available, falling back to attacks log')
            data_source = 'attacks'
            # Fall back to attacks log (paginated to fetch all war attacks)
            for user_id, member_data in faction_members.items():
                member_name = member_data.get('name', f'ID:{user_id}')
                stats = attack_stats.get(user_id, {})

                member_list.append({
                    'name': member_name,
                    'war_hits': stats.get('war_hits', 0),
                    'outside_hits': stats.get('outside_hits', 0),
                    'war_incoming': stats.get('war_incoming', 0),
                    'outside_incoming': stats.get('outside_incoming', 0),
                    'score': 0,
                })

        # Sort by score if available, otherwise by war hits
        if data_source == 'report':
            sorted_members = sorted(member_list, key=lambda x: x['score'], reverse=True)
        else:
            sorted_members = sorted(member_list, key=lambda x: x['war_hits'], reverse=True)

        # Calculate totals
        total_war_hits = sum(m['war_hits'] for m in member_list)
        total_outside = sum(m['outside_hits'] for m in member_list)
        total_war_incoming = sum(m['war_incoming'] for m in member_list)
        total_outside_incoming = sum(m['outside_incoming'] for m in member_list)
        participants = len([m for m in member_list if m['war_hits'] > 0])

        # War status header
        enemy_name = enemy_faction_data.get('name', 'Unknown') if enemy_faction_data else 'Unknown'
        our_score = our_faction_data.get('score', 0) if our_faction_data else 0
        enemy_score = enemy_faction_data.get('score', 0) if enemy_faction_data else 0

        if is_active:
            status_text = 'In Progress'
            color = EMBED_COLOR_INFO
        else:
            status_text = 'VICTORY' if our_score > enemy_score else 'DEFEAT'
            color = EMBED_COLOR_SUCCESS if our_score > enemy_score else EMBED_COLOR_CRITICAL

        description = (
            f'**vs {enemy_name}** | Score: {our_score} - {enemy_score} | {status_text}\n'
            f'War ID: {war_info["war_id"]}'
        )

        # Build first embed with summary
        embeds = []
        embed = create_embed(
            title='War Participation Report',
            description=description,
            color=color,
            fields=[
                {'name': 'War Attacks', 'value': str(total_war_hits), 'inline': True},
                {'name': 'Outside Hits', 'value': str(total_outside), 'inline': True},
                {'name': 'War Incoming', 'value': str(total_war_incoming), 'inline': True},
                {'name': 'Outside Incoming', 'value': str(total_outside_incoming), 'inline': True},
                {'name': 'Participated', 'value': f'{participants}/{len(member_list)}', 'inline': True},
            ]
        )

        # Build monospace table for clean alignment
        # Find the longest name to set column width
        max_name = max(len(m['name']) for m in sorted_members)
        max_name = min(max_name, 15)  # Cap at 15 chars to fit mobile

        # Build table lines
        header = f'{"#":>2}  {"Name":<{max_name}}  War  Out  WHt  OHt'
        separator = '-' * len(header)
        table_lines = [header, separator]

        for i, m in enumerate(sorted_members):
            name = m['name'][:max_name]
            line = f'{i+1:>2}  {name:<{max_name}}  {m["war_hits"]:>3}  {m["outside_hits"]:>3}  {m["war_incoming"]:>3}  {m["outside_incoming"]:>3}'
            table_lines.append(line)

        # Split table into code block chunks that fit Discord's 1024 char field limit
        # Each line is wrapped in a code block, so account for ``` markers (8 chars)
        table_chunks = []
        current_chunk = []
        current_length = 0

        for line in table_lines:
            # +1 for newline, +8 for ``` markers
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
            if idx == 0:
                embed.add_field(
                    name=f'All Members ({len(sorted_members)})',
                    value=chunk,
                    inline=False
                )
            else:
                if len(embed.fields) >= 24 or len(embed) > 5000:
                    embeds.append(embed)
                    embed = create_embed(
                        title=f'War Participation Report (cont.)',
                        color=color
                    )
                embed.add_field(
                    name=f'Members (cont.)',
                    value=chunk,
                    inline=False
                )

        embeds.append(embed)

        # Return single embed or list
        return embeds[0] if len(embeds) == 1 else embeds

    async def format_xan_embed(self, war_info):
        """
        Create Discord embed for Xanax reimbursement calculation.
        Shows war hits + chain outside hits per member.

        Args:
            war_info: War data from get_latest_war()

        Returns:
            discord.Embed or list of discord.Embed
        """
        if not war_info:
            return create_embed(
                title='No War Data',
                description='No ranked war data available.',
                color=EMBED_COLOR_INFO
            )

        war_data = war_info['data']
        factions = war_data.get('factions', {})
        war = war_data.get('war', {})
        war_start = war.get('start', 0)
        is_active = war_info.get('is_active', True)

        # Find our faction and enemy faction
        our_faction_data = None
        enemy_faction_id = None
        enemy_faction_data = None
        for faction_id, faction_data in factions.items():
            if str(faction_id) == str(TORN_FACTION_ID):
                our_faction_data = faction_data
            else:
                enemy_faction_id = faction_id
                enemy_faction_data = faction_data

        if not enemy_faction_id:
            return create_embed(
                title='No War Data',
                description='Could not identify war factions.',
                color=EMBED_COLOR_WARNING
            )

        # Fetch faction members for names
        from utils.torn_api import fetch_faction_members
        faction_members = await fetch_faction_members()

        if not faction_members:
            return create_embed(
                title='API Error',
                description='Could not fetch faction member data.',
                color=EMBED_COLOR_CRITICAL
            )

        # Try rankedwarreport for accurate war hits
        our_war_members = None
        report = await self._fetch_rankedwarreport()
        if report:
            report_factions = report.get('factions', {})
            for fid, fdata in report_factions.items():
                if str(fid) == str(TORN_FACTION_ID):
                    our_war_members = fdata.get('members', {})
                    if fdata.get('score') is not None:
                        our_faction_data = fdata
                else:
                    if fdata.get('score') is not None:
                        enemy_faction_data = fdata

        # Fetch attacks log for chain outside hits
        attack_stats = await self._get_war_attack_stats(enemy_faction_id, war_start)

        # Build member list
        member_list = []
        for user_id, member_data in faction_members.items():
            member_name = member_data.get('name', f'ID:{user_id}')
            log_stats = attack_stats.get(user_id, {})
            chain_outside = log_stats.get('chain_outside_hits', 0)

            if our_war_members:
                war_stats = our_war_members.get(user_id, {})
                war_hits = war_stats.get('attacks', 0)
            else:
                war_hits = log_stats.get('war_hits', 0)

            total = war_hits + chain_outside
            if total > 0:
                member_list.append({
                    'name': member_name,
                    'war_hits': war_hits,
                    'chain_outside': chain_outside,
                    'total': total,
                })

        # Sort by total descending
        sorted_members = sorted(member_list, key=lambda x: x['total'], reverse=True)

        # Totals
        total_war = sum(m['war_hits'] for m in member_list)
        total_chain = sum(m['chain_outside'] for m in member_list)
        total_all = total_war + total_chain

        # War status header
        enemy_name = enemy_faction_data.get('name', 'Unknown') if enemy_faction_data else 'Unknown'
        our_score = our_faction_data.get('score', 0) if our_faction_data else 0
        enemy_score = enemy_faction_data.get('score', 0) if enemy_faction_data else 0

        if is_active:
            status_text = 'In Progress'
            color = EMBED_COLOR_INFO
        else:
            status_text = 'VICTORY' if our_score > enemy_score else 'DEFEAT'
            color = EMBED_COLOR_SUCCESS if our_score > enemy_score else EMBED_COLOR_CRITICAL

        description = (
            f'**vs {enemy_name}** | Score: {our_score} - {enemy_score} | {status_text}\n'
            f'War ID: {war_info["war_id"]}'
        )

        embeds = []
        embed = create_embed(
            title='Xanax Reimbursement Report',
            description=description,
            color=color,
            fields=[
                {'name': 'War Hits', 'value': str(total_war), 'inline': True},
                {'name': 'Chain Outside', 'value': str(total_chain), 'inline': True},
                {'name': 'Total', 'value': str(total_all), 'inline': True},
            ]
        )

        # Build monospace table
        max_name = max((len(m['name']) for m in sorted_members), default=10)
        max_name = min(max_name, 15)

        header = f'{"#":>2}  {"Name":<{max_name}}  War  Chn  Tot'
        separator = '-' * len(header)
        table_lines = [header, separator]

        for i, m in enumerate(sorted_members):
            name = m['name'][:max_name]
            line = f'{i+1:>2}  {name:<{max_name}}  {m["war_hits"]:>3}  {m["chain_outside"]:>3}  {m["total"]:>3}'
            table_lines.append(line)

        # Split into chunks for Discord field limits
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
            if idx == 0:
                embed.add_field(
                    name=f'Members ({len(sorted_members)})',
                    value=chunk,
                    inline=False
                )
            else:
                if len(embed.fields) >= 24 or len(embed) > 5000:
                    embeds.append(embed)
                    embed = create_embed(
                        title='Xanax Reimbursement Report (cont.)',
                        color=color
                    )
                embed.add_field(
                    name='Members (cont.)',
                    value=chunk,
                    inline=False
                )

        embeds.append(embed)
        return embeds[0] if len(embeds) == 1 else embeds

    async def format_war_management_report(self, war_info, final_data=None):
        """
        Create a detailed management report with incoming/outgoing attacks
        Posted to private war management channel for leaders/management

        Args:
            war_info: War data from Torn API
            final_data: Final war data snapshot (if war ended)

        Returns:
            discord.Embed
        """
        if not war_info and not final_data:
            return None

        # Use final_data if available, otherwise use war_info
        data_to_use = final_data if final_data else war_info['data']
        if final_data:
            war_id = final_data.get('war_id', 'Unknown')
        else:
            war_id = war_info['war_id']

        factions = data_to_use.get('factions', {})

        # Find our faction's members
        our_members = None
        enemy_members = None
        our_faction_data = None
        enemy_faction_data = None

        for faction_id, faction_data in factions.items():
            if str(faction_id) == str(TORN_FACTION_ID):
                our_members = faction_data.get('members', {})
                our_faction_data = faction_data
            else:
                enemy_members = faction_data.get('members', {})
                enemy_faction_data = faction_data

        if not our_members:
            return None

        # Fetch faction members to get names
        from utils.torn_api import fetch_faction_members
        faction_members = await fetch_faction_members()

        # Build detailed member stats
        member_stats = []
        for user_id, stats in our_members.items():
            # Get member name
            member_name = 'Unknown'
            if faction_members and user_id in faction_members:
                member_name = faction_members[user_id].get('name', f'ID:{user_id}')
            else:
                member_name = f'ID:{user_id}'

            # Get outgoing attacks (attacks made)
            outgoing = stats.get('attacks', 0)

            # Get incoming attacks (times they got hit)
            # This is in the 'defends' field
            incoming = stats.get('defends', 0)

            score = stats.get('score', 0)

            member_stats.append({
                'name': member_name,
                'user_id': user_id,
                'outgoing': outgoing,
                'incoming': incoming,
                'score': score
            })

        # Sort by score (highest first)
        sorted_stats = sorted(member_stats, key=lambda x: x['score'], reverse=True)

        # Get war result
        our_score = our_faction_data.get('score', 0) if our_faction_data else 0
        enemy_score = enemy_faction_data.get('score', 0) if enemy_faction_data else 0
        enemy_name = enemy_faction_data.get('name', 'Unknown') if enemy_faction_data else 'Unknown'

        result = '🏆 VICTORY' if our_score > enemy_score else '❌ DEFEAT'
        color = EMBED_COLOR_SUCCESS if our_score > enemy_score else EMBED_COLOR_CRITICAL

        # Build management embed
        embed = create_embed(
            title=f'⚔️ War Management Report - {result}',
            description=f'**War #{war_id}** vs {enemy_name}\n**Final Score:** {our_score} - {enemy_score}',
            color=color
        )

        # Add summary stats
        total_outgoing = sum(m['outgoing'] for m in member_stats)
        total_incoming = sum(m['incoming'] for m in member_stats)
        total_score = sum(m['score'] for m in member_stats)

        embed.add_field(
            name='📊 Summary',
            value=f'**Total Attacks Made:** {total_outgoing}\n**Total Times Hit:** {total_incoming}\n**Total Score:** {total_score}',
            inline=False
        )

        # Add detailed member breakdown (show all members)
        # Split into chunks if needed (Discord limit is 1024 chars per field)
        member_details = []
        for i, m in enumerate(sorted_stats):
            member_details.append(
                f'{i+1}. **{m["name"]}**\n'
                f'   └ Attacks: {m["outgoing"]} | Defended: {m["incoming"]}x | Score: {m["score"]}'
            )

        # Join all member details
        full_details = '\n'.join(member_details)

        # Split into multiple fields if too long
        if len(full_details) > 1024:
            # Split into chunks
            chunks = []
            current_chunk = []
            current_length = 0

            for detail in member_details:
                if current_length + len(detail) + 1 > 1024:
                    chunks.append('\n'.join(current_chunk))
                    current_chunk = [detail]
                    current_length = len(detail)
                else:
                    current_chunk.append(detail)
                    current_length += len(detail) + 1

            if current_chunk:
                chunks.append('\n'.join(current_chunk))

            # Add chunks as separate fields
            for idx, chunk in enumerate(chunks):
                embed.add_field(
                    name=f'👥 Member Performance (Part {idx + 1})',
                    value=chunk,
                    inline=False
                )
        else:
            embed.add_field(
                name='👥 Member Performance',
                value=full_details,
                inline=False
            )

        # Add members who got hit the most (potential weak links)
        most_hit = sorted(member_stats, key=lambda x: x['incoming'], reverse=True)[:5]
        if most_hit:
            most_hit_text = '\n'.join([
                f'{i+1}. **{m["name"]}**: Hit {m["incoming"]}x'
                for i, m in enumerate(most_hit) if m["incoming"] > 0
            ])
            if most_hit_text:
                embed.add_field(
                    name='⚠️ Most Targeted (Review Defense)',
                    value=most_hit_text,
                    inline=False
                )

        # Add members who didn't participate
        non_participants = [m for m in member_stats if m['outgoing'] == 0]
        if non_participants:
            non_participant_text = '\n'.join([
                f'• {m["name"]}'
                for m in non_participants
            ])
            embed.add_field(
                name='❌ No Participation',
                value=non_participant_text,
                inline=False
            )

        return embed

    async def check_war_update(self):
        """
        Check if war status changed and post update if needed
        Auto-posts war results when war ends
        Called by the background task every minute
        """
        # Get current war
        war_info = await self.get_current_war()
        channel = self.bot.get_channel(WAR_CHANNEL_ID)
        management_channel = self.bot.get_channel(WAR_MANAGEMENT_CHANNEL_ID) if WAR_MANAGEMENT_CHANNEL_ID != 0 else None

        if not channel:
            print(f'Warning: Could not find #war channel (ID: {WAR_CHANNEL_ID})')
            return

        # Detect war END (was active, now not)
        if self.was_war_active and not war_info:
            print('War ended! Posting war results...')

            # Get final war stats from history
            if self.current_war_id and self.current_war_id in self.war_history:
                final_war_data = self.war_history[self.current_war_id]
                factions = final_war_data.get('factions', {})

                # Determine win/loss and opponent
                our_score = 0
                enemy_score = 0
                enemy_name = 'Unknown'
                for fid, fdata in factions.items():
                    if str(fid) == str(TORN_FACTION_ID):
                        our_score = fdata.get('score', 0)
                    else:
                        enemy_score = fdata.get('score', 0)
                        enemy_name = fdata.get('name', 'Unknown')

                we_won = our_score > enemy_score

                if we_won:
                    title = '🏆 War Complete!'
                    description = f'We have won against **{enemy_name}**!\n\nGreat job everyone!'
                    color = EMBED_COLOR_SUCCESS
                else:
                    title = '💀 War Complete!'
                    description = f'We have lost against **{enemy_name}**.\n\nWe will get them next time!'
                    color = EMBED_COLOR_CRITICAL

                public_embed = create_embed(
                    title=title,
                    description=description,
                    color=color,
                    fields=[
                        {'name': 'Final Score', 'value': f'**{our_score}** - {enemy_score}' if we_won else f'{our_score} - **{enemy_score}**', 'inline': True}
                    ]
                )

                # Ping the faction role (Ascended punks)
                ping_text = ''
                guild = channel.guild
                faction_role = discord.utils.find(
                    lambda r: r.name.lower() == VERIFIED_ROLE_NAME.lower(),
                    guild.roles
                )
                if faction_role:
                    ping_text = f'{faction_role.mention} '

                await channel.send(f'{ping_text}🏁 **War Complete!**', embed=public_embed)
                print('Posted war completion report to public channel')

                # Auto-clear enemy watchlist
                if self.enemy_watchlist:
                    cleared = self.enemy_watchlist.clear_all()
                    await self.enemy_watchlist.save_watchlist()
                    if cleared > 0:
                        print(f'Auto-cleared {cleared} enemies from watchlist')

                # Post detailed management report
                if management_channel:
                    management_embed = await self.format_war_management_report(
                        None,
                        final_data={
                            'war_id': self.current_war_id,
                            'factions': final_war_data.get('factions', {})
                        }
                    )
                    if management_embed:
                        await management_channel.send('📊 **War Management Report**', embed=management_embed)
                        print('Posted detailed war report to management channel')
                else:
                    print('Warning: War management channel not configured')

            # Reset state
            self.was_war_active = False
            self.current_war_id = None
            self.current_war_attacks = {'outgoing': {}, 'incoming': {}, 'scores': {}}
            return

        # No active war
        if not war_info:
            return

        # War is active
        war_id = war_info['war_id']

        # Mark war as active if this is first detection
        if not self.was_war_active:
            self.was_war_active = True
            self.current_war_id = war_id
            # Clear old war report cache so fresh data is used
            save_json('data/warreport_cache.json', {})
            print(f'War detected: {war_id}')

        # Save war data to history (faction-level scores for final report)
        self.war_history[war_id] = {
            'timestamp': get_current_timestamp(),
            'factions': war_info['data'].get('factions', {})
        }

        # Prune old war entries (keep last 20)
        if len(self.war_history) > 20:
            sorted_keys = sorted(self.war_history.keys(), key=lambda k: self.war_history[k].get('timestamp', 0))
            for old_key in sorted_keys[:-20]:
                del self.war_history[old_key]

        await self.save_war_history()
