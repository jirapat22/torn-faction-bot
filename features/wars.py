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


import logging

logger = logging.getLogger(__name__)

class WarTracker:
    """Tracks faction wars and provides statistics"""

    def __init__(self, bot):
        self.bot = bot
        self.was_war_active = False
        self.current_war_id = None
        self.war_data_file = 'data/war_history.json'
        self.war_history = {}
        self.enemy_watchlist = None  # Set by bot.py for auto-clear
        self.pending_wars_file = 'data/pending_wars.json'
        self.announced_pending_wars = set()  # war_ids already announced during their notice window
        self.war_member_stats_file = 'data/war_member_stats.json'
        self.war_member_stats = {'wars': []}
        # Track attacks during active war
        self.current_war_attacks = {
            'outgoing': {},  # {member_id: hit_count}
            'incoming': {},  # {member_id: times_hit}
            'scores': {}     # {member_id: score}
        }

    async def load_war_history(self):
        """Load war history from JSON file"""
        self.war_history = load_json(self.war_data_file, default={})
        logger.info(f'Loaded war history with {len(self.war_history)} records')

    async def save_war_history(self):
        """Save war history to JSON file"""
        save_json(self.war_data_file, self.war_history)

    async def load_pending_wars(self):
        """Load the set of already-announced pending (declared-but-unstarted) war IDs"""
        data = load_json(self.pending_wars_file, default={'announced': []})
        self.announced_pending_wars = set(str(w) for w in data.get('announced', []))
        logger.info(f'Loaded {len(self.announced_pending_wars)} announced pending-war ID(s)')

    async def save_pending_wars(self):
        """Save the set of already-announced pending war IDs"""
        save_json(self.pending_wars_file, {'announced': list(self.announced_pending_wars)})

    async def load_war_member_stats(self):
        """Load historical per-member war stats"""
        self.war_member_stats = load_json(self.war_member_stats_file, default={'wars': []})
        count = len(self.war_member_stats.get('wars', []))
        logger.info(f'Loaded war member stats with {count} war records')

    async def save_war_member_stats(self):
        """Save historical per-member war stats"""
        save_json(self.war_member_stats_file, self.war_member_stats)

    async def _save_war_member_data(self, war_id, report_factions, our_score, enemy_score,
                                     enemy_name, friendly_hosps=None):
        """Save per-member war data + friendly-hosp log to persistent history. Keeps last 10 wars.

        Saves even if rankedwarreport member data is missing — the friendly-hosp
        event log alone is still valuable to preserve.
        """
        our_members = {}
        for fid, fdata in (report_factions or {}).items():
            if str(fid) == str(TORN_FACTION_ID):
                our_members = fdata.get('members', {})
                break

        from utils.torn_api import fetch_faction_members
        faction_members = await fetch_faction_members() if our_members else {}

        # Per-member friendly hosp counts (gave / received)
        hosps_given = {}
        hosps_received = {}
        for ev in (friendly_hosps or []):
            hosps_given[ev['attacker_id']] = hosps_given.get(ev['attacker_id'], 0) + 1
            hosps_received[ev['defender_id']] = hosps_received.get(ev['defender_id'], 0) + 1

        members_data = {}
        for user_id, stats in our_members.items():
            name = 'Unknown'
            if faction_members and user_id in faction_members:
                name = faction_members[user_id].get('name', f'ID:{user_id}')
            members_data[user_id] = {
                'name': name,
                'attacks': stats.get('attacks', 0),
                'defends': stats.get('defends', 0),
                'score': stats.get('score', 0),
                'hosps_given': hosps_given.get(str(user_id), 0),
                'hosps_received': hosps_received.get(str(user_id), 0),
            }

        # Skip the save only if there's truly nothing to record
        if not members_data and not friendly_hosps:
            logger.info('War member stats: nothing to save (no members and no friendly hosps)')
            return

        war_record = {
            'war_id': str(war_id),
            'timestamp': get_current_timestamp(),
            'enemy_name': enemy_name,
            'our_score': our_score,
            'enemy_score': enemy_score,
            'result': 'victory' if our_score > enemy_score else 'defeat',
            'members': members_data,
            'friendly_hosps': friendly_hosps or [],
        }

        self.war_member_stats['wars'].append(war_record)
        if len(self.war_member_stats['wars']) > 10:
            self.war_member_stats['wars'] = self.war_member_stats['wars'][-10:]

        await self.save_war_member_stats()
        logger.info(f'Saved war member stats for war {war_id} ({len(members_data)} members)')

    async def _fetch_rankedwars(self):
        """Fetch ranked wars data from Torn API (v1 - works for any faction)"""
        data = await fetch_faction_data('rankedwars')
        if not data:
            return None
        rankedwars = data.get('rankedwars', {})
        if not rankedwars:
            return None
        return rankedwars

    @staticmethod
    def _normalise_rankedwarreport(report):
        """
        Reshape a v2 ranked war report into the v1 shape every consumer here
        expects: factions as {faction_id: {..., members: {user_id: stats}}}.

        v2 returns both `factions` and each faction's `members` as LISTS. Left
        as-is, `.items()` on a list raises and `.get('members', {})` yields an
        unusable list, so one endpoint migration would break several report
        paths. Normalising at the boundary keeps the blast radius to this
        method; a dict (a v1 payload, or a report cached before this change)
        is passed through untouched.

        Note: v2 members carry id/name/level/attacks/score but no `defends`,
        so defend counts now read as 0 — the data is simply no longer offered.
        """
        if not isinstance(report, dict):
            return report
        factions = report.get('factions')
        if not isinstance(factions, list):
            return report  # already v1-shaped, or nothing usable to convert

        converted = {}
        for faction in factions:
            if not isinstance(faction, dict) or faction.get('id') is None:
                continue
            members = faction.get('members')
            if isinstance(members, list):
                members = {
                    str(m['id']): m for m in members
                    if isinstance(m, dict) and m.get('id') is not None
                }
            elif not isinstance(members, dict):
                members = {}
            converted[str(faction['id'])] = {**faction, 'members': members}

        normalised = dict(report)
        normalised['factions'] = converted
        return normalised

    async def _fetch_rankedwarreport(self, war_id=None):
        """
        Fetch ranked war report with complete per-member stats.
        Uses v1 'rankedwarreport' selection which includes attacks, defends,
        and score for every member in both factions.
        Caches the report so it remains available after the war ends.

        Args:
            war_id: Current war ID to validate cache against (prevents stale data from old wars)

        Returns:
            Dict with {factions: {fid: {name, score, members: {uid: stats}}}} or None
        """
        cache_file = 'data/warreport_cache.json'

        # v2 only — Torn retired this selection from v1, which now answers
        # "error 23: This selection is only available in API v2", so the whole
        # per-member war report was silently coming back empty.
        #
        # The v2 path id is the WAR id, NOT the faction id. Passing a faction id
        # does not error, it returns whichever war happens to carry that number:
        # faction 45635 came back as war 45635, a Nirvana vs Unicorn Apocalypse
        # match we were never in. So without a war id we must not ask at all,
        # and whatever comes back is checked for our faction before it is used.
        # A report only exists once the war has finished; an in-progress war
        # answers "error 6: Incorrect ID", which falls through to the cache.
        data = await fetch_faction_data_v2('rankedwarreport', path_id=war_id) if war_id else None
        if data:
            report = self._normalise_rankedwarreport(data.get('rankedwarreport', {}))
            if report and str(TORN_FACTION_ID) not in (report.get('factions') or {}):
                logger.warning(
                    f'War report: ignoring report for war {war_id} — it covers factions '
                    f'{list((report.get("factions") or {}))} and not ours ({TORN_FACTION_ID})'
                )
                report = {}
            if report:
                factions = report.get('factions', {})
                if factions:
                    # Log what we got
                    for fid, fdata in factions.items():
                        if isinstance(fdata, dict):
                            members = fdata.get('members', {})
                            logger.info(f'War report: Faction {fid} ({fdata.get("name", "?")}) - {len(members)} members')
                    # Cache the report with war_id for validation
                    cache_data = {'war_id': str(war_id) if war_id else None, 'report': report}
                    save_json(cache_file, cache_data)
                    return report

        # API didn't return report — try loading from cache
        cached = load_json(cache_file, default={})
        if cached and cached.get('report'):
            cached_war_id = cached.get('war_id')
            # Only use cache if it matches the current war (or no war_id to check)
            if war_id is None or cached_war_id is None or str(cached_war_id) == str(war_id):
                logger.info('War report: Using cached report (API no longer returning data)')
                return cached['report']
            else:
                logger.info(f'War report: Cache is for war {cached_war_id}, current war is {war_id} — ignoring stale cache')
                return None

        logger.info('War report: No data from API and no cache available')
        return None

    async def get_current_war(self):
        """
        Get current ACTIVE war status from Torn API

        Returns:
            Dict with war data or None if error/no war
        """
        rankedwars = await self._fetch_rankedwars()
        if not rankedwars:
            logger.info('War: No ranked wars data found')
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

        # Note: friendly-hosp count is shown in /war board (which already
        # paginates the attack log). Don't recompute it here — /war status
        # should stay a fast, glanceable read.

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

            logger.info(f'War attacks page {page + 1}: got {len(attacks)} attacks, {new_count} new (total: {len(all_attacks)})')

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

        logger.info(f'War: Fetched {len(all_attacks)} total attacks since war start')
        return all_attacks

    async def _get_war_attack_stats(self, enemy_faction_id, war_start):
        """
        Compute per-member war statistics from the faction attacks log.
        Paginates through ALL attacks since war start for accurate data.

        Args:
            enemy_faction_id: The enemy faction ID in the ranked war
            war_start: War start timestamp to filter attacks

        Returns:
            Dict of {user_id: {'war_hits': N, 'outside_hits': N, 'total_incoming': N,
                               'war_incoming': N, 'outside_incoming': N,
                               'hosps_given': N, 'hosps_received': N}}.
            A "_friendly_hosps" key is added at top level with the raw event log:
              [{'timestamp', 'attacker_id', 'attacker_name',
                'defender_id', 'defender_name'}, ...]
        """
        stats = {}
        friendly_hosps = []  # ordered event log

        def _ensure(uid):
            if uid not in stats:
                stats[uid] = {
                    'war_hits': 0, 'outside_hits': 0, 'chain_outside_hits': 0,
                    'total_incoming': 0, 'war_incoming': 0, 'outside_incoming': 0,
                    'hosps_given': 0, 'hosps_received': 0,
                }

        try:
            attacks = await self._fetch_all_attacks_since(war_start)
            our_faction = str(TORN_FACTION_ID)
            enemy_faction = str(enemy_faction_id)

            # Fetch completed chains from API and build time windows for chains >= 10
            chain_windows = []
            chains_data = await fetch_faction_data('chains')
            if chains_data:
                chains = chains_data.get('chains', {})
                for chain_id, chain_info in chains.items():
                    if not isinstance(chain_info, dict):
                        continue
                    chain_count = chain_info.get('chain', 0)
                    chain_start = chain_info.get('start', 0)
                    chain_end = chain_info.get('end', 0)
                    # Only include chains >= 10 that overlap with war period
                    if chain_count >= 10 and chain_end >= war_start:
                        chain_windows.append((chain_start, chain_end))

            def _in_chain(timestamp):
                """Check if a timestamp falls within any valid chain window"""
                for c_start, c_end in chain_windows:
                    if c_start <= timestamp <= c_end:
                        return True
                return False

            # Main pass: compute all stats
            for attack_id, attack in attacks.items():
                if not isinstance(attack, dict):
                    continue

                attacker_id = str(attack.get('attacker_id', 0))
                attacker_faction = str(attack.get('attacker_faction', 0))
                defender_id = str(attack.get('defender_id', 0))
                defender_faction = str(attack.get('defender_faction', 0))
                result = attack.get('result', '')

                # Friendly hospitalization — teammate hosps teammate to keep them
                # safe from enemy attacks. Tracked separately from war/outside hits.
                if (attacker_faction == our_faction
                        and defender_faction == our_faction
                        and attacker_id != defender_id
                        and result == 'Hospitalized'):
                    _ensure(attacker_id)
                    _ensure(defender_id)
                    stats[attacker_id]['hosps_given'] += 1
                    stats[defender_id]['hosps_received'] += 1
                    friendly_hosps.append({
                        'timestamp': attack.get('timestamp_ended') or attack.get('timestamp_started', 0),
                        'attacker_id': attacker_id,
                        'attacker_name': attack.get('attacker_name', ''),
                        'defender_id': defender_id,
                        'defender_name': attack.get('defender_name', ''),
                    })
                    continue  # don't double-count as a war/outside hit

                # Our member attacked someone (successful attacks only)
                if attacker_faction == our_faction:
                    if result not in ('Attacked', 'Mugged', 'Hospitalized', 'Special'):
                        continue

                    _ensure(attacker_id)
                    if defender_faction == enemy_faction:
                        stats[attacker_id]['war_hits'] += 1
                    else:
                        stats[attacker_id]['outside_hits'] += 1
                        # Count chain outside hit if this attack fell within a chain >= 10
                        ts = attack.get('timestamp_started', 0)
                        if _in_chain(ts):
                            stats[attacker_id]['chain_outside_hits'] += 1

                # Someone attacked our member (successful attacks only)
                elif defender_faction == our_faction and attacker_faction != our_faction:
                    if result not in ('Attacked', 'Mugged', 'Hospitalized', 'Special'):
                        continue

                    _ensure(defender_id)
                    stats[defender_id]['total_incoming'] += 1
                    # Classify by the war modifier, NOT by attacker_faction ==
                    # enemy. A stealth attack blanks attacker_name/_faction in
                    # the defender's log, and measured on war 50338 that was 395
                    # of 931 war hits taken - keying off the faction id would
                    # have silently dropped 42% of them. The modifier survives
                    # stealth, so it is the only complete signal.
                    modifiers = attack.get('modifiers', {})
                    war_modifier = modifiers.get('war', 1.0) if isinstance(modifiers, dict) else 1.0
                    if war_modifier > 1.0:
                        stats[defender_id]['war_incoming'] += 1
                    else:
                        stats[defender_id]['outside_incoming'] += 1

        except Exception as e:
            logger.error(f'War: Error computing attack stats: {e}')

        # Attach raw friendly-hosp log under a non-uid key so callers can render it
        stats['_friendly_hosps'] = sorted(friendly_hosps, key=lambda h: h.get('timestamp', 0))
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
        report = await self._fetch_rankedwarreport(war_id=war_info.get('war_id'))
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
        if not war_start:
            attack_stats = {}
        else:
            attack_stats = await self._get_war_attack_stats(enemy_faction_id, war_start)

        if our_war_members:
            logger.info(f'War: Using rankedwarreport data ({len(our_war_members)} members)')
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
                    'hosps_given': log_stats.get('hosps_given', 0),
                    'score': war_stats.get('score', 0),
                })
        else:
            logger.info('War: rankedwarreport not available, falling back to attacks log')
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
                    'hosps_given': stats.get('hosps_given', 0),
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

        # Friendly hospitalisations — teammates protecting teammates from enemy hits
        friendly_hosps = attack_stats.get('_friendly_hosps', []) if isinstance(attack_stats, dict) else []
        total_friendly_hosps = len(friendly_hosps)
        # Top hospitalizers (members who keep teammates safe most often)
        hospitalizer_counts = {}
        for ev in friendly_hosps:
            aid = ev['attacker_id']
            if aid not in hospitalizer_counts:
                hospitalizer_counts[aid] = {'name': ev.get('attacker_name') or aid, 'count': 0}
            hospitalizer_counts[aid]['count'] += 1
        top_hospitalizers = sorted(
            hospitalizer_counts.values(), key=lambda x: x['count'], reverse=True
        )[:5]

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
        legend = (
            '\n\n**War** = Hits on war opponent | **Out** = Hits outside war\n'
            '**WHt** = Times hit by war opponent | **OHt** = Times hit by outsiders\n'
            '**Hsp** = Friendly hospitalisations given (kept teammates safe)'
        )
        embed_fields = [
            {'name': 'War Attacks', 'value': str(total_war_hits), 'inline': True},
            {'name': 'Outside Hits', 'value': str(total_outside), 'inline': True},
            {'name': 'War Incoming', 'value': str(total_war_incoming), 'inline': True},
            {'name': 'Outside Incoming', 'value': str(total_outside_incoming), 'inline': True},
            {'name': 'Participated', 'value': f'{participants}/{len(member_list)}', 'inline': True},
        ]
        if total_friendly_hosps:
            embed_fields.append({
                'name': '🏥 Friendly Hosps',
                'value': str(total_friendly_hosps),
                'inline': True,
            })
        embed = create_embed(
            title='War Participation Report',
            description=description + legend,
            color=color,
            fields=embed_fields,
        )
        if top_hospitalizers:
            top_text = '\n'.join(
                f'{i+1}. **{h["name"]}** — {h["count"]}x'
                for i, h in enumerate(top_hospitalizers)
            )
            embed.add_field(
                name='🛡️ Top Hospitalizers (kept teammates safe)',
                value=top_text,
                inline=False,
            )

        # Build monospace table for clean alignment
        # Find the longest name to set column width
        max_name = max((len(m['name']) for m in sorted_members), default=10)
        max_name = min(max_name, 15)  # Cap at 15 chars to fit mobile

        # ANSI colour highlights — Discord renders these inside ```ansi blocks.
        # Top-3 distinct War hits → green; top-3 distinct WHt → red. Ties keep
        # everyone on the boundary highlighted.
        GREEN = '[32m'
        RED = '[31m'
        RESET = '[0m'
        top3_war = sorted({m['war_hits'] for m in sorted_members if m['war_hits'] > 0}, reverse=True)[:3]
        top3_wht = sorted({m['war_incoming'] for m in sorted_members if m['war_incoming'] > 0}, reverse=True)[:3]

        def _war_cell(v):
            cell = f'{v:>3}'
            return f'{GREEN}{cell}{RESET}' if v in top3_war else cell

        def _wht_cell(v):
            cell = f'{v:>3}'
            return f'{RED}{cell}{RESET}' if v in top3_wht else cell

        # Build table lines
        header = f'{"#":>2}  {"Name":<{max_name}}  War  Out  WHt  OHt  Hsp'
        separator = '-' * len(header)
        table_lines = [header, separator]

        for i, m in enumerate(sorted_members):
            name = m['name'][:max_name]
            line = (
                f'{i+1:>2}  {name:<{max_name}}  '
                f'{_war_cell(m["war_hits"])}  {m["outside_hits"]:>3}  '
                f'{_wht_cell(m["war_incoming"])}  {m["outside_incoming"]:>3}  '
                f'{m["hosps_given"]:>3}'
            )
            table_lines.append(line)

        # Split table into ansi code-block chunks that fit Discord's 1024 char field limit.
        # ```ansi ... ``` lets Discord render the embedded ANSI colour escapes.
        # Reserve 12 chars for the ```ansi\n ... \n``` wrappers.
        table_chunks = []
        current_chunk = []
        current_length = 0

        for line in table_lines:
            if current_length + len(line) + 1 > 1024 - 12 and current_chunk:
                table_chunks.append('```ansi\n' + '\n'.join(current_chunk) + '\n```')
                current_chunk = [line]
                current_length = len(line)
            else:
                current_chunk.append(line)
                current_length += len(line) + 1

        if current_chunk:
            table_chunks.append('```ansi\n' + '\n'.join(current_chunk) + '\n```')

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
        Shows war hits + chain outside hits + friendly hosps per member.

        Friendly hosps count because they cost the same energy as any other
        hit and are thrown on instruction to keep a teammate out of reach -
        reimbursing the war hit but not the defensive one would under-pay the
        people doing as they were told. They are excluded from war/outside hit
        counting upstream (see _get_war_attack_stats), so adding them here
        cannot double-count.

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
        report = await self._fetch_rankedwarreport(war_id=war_info.get('war_id'))
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
        if not war_start:
            attack_stats = {}
        else:
            attack_stats = await self._get_war_attack_stats(enemy_faction_id, war_start)

        # Build member list
        member_list = []
        for user_id, member_data in faction_members.items():
            member_name = member_data.get('name', f'ID:{user_id}')
            log_stats = attack_stats.get(user_id, {})
            chain_outside = log_stats.get('chain_outside_hits', 0)
            # Always from the attacks log: the ranked war report only counts
            # attacks on the war opponent, so it never sees a friendly hosp.
            friendly_hosps = log_stats.get('hosps_given', 0)

            if our_war_members:
                war_stats = our_war_members.get(user_id, {})
                war_hits = war_stats.get('attacks', 0)
            else:
                war_hits = log_stats.get('war_hits', 0)

            total = war_hits + chain_outside + friendly_hosps
            if total > 0:
                member_list.append({
                    'name': member_name,
                    'war_hits': war_hits,
                    'chain_outside': chain_outside,
                    'friendly_hosps': friendly_hosps,
                    'total': total,
                })

        # Sort by total descending
        sorted_members = sorted(member_list, key=lambda x: x['total'], reverse=True)

        # Totals
        total_war = sum(m['war_hits'] for m in member_list)
        total_chain = sum(m['chain_outside'] for m in member_list)
        total_hosp = sum(m['friendly_hosps'] for m in member_list)
        total_all = total_war + total_chain + total_hosp

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
        legend = (
            '\n\n**War** = Hits on war opponent | **Chn** = Outside hits during chain\n'
            '**Hsp** = Friendly hosps (teammate put safe) | **Tot** = all three combined'
        )
        embed = create_embed(
            title='Xanax Reimbursement Report',
            description=description + legend,
            color=color,
            fields=[
                {'name': 'War Hits', 'value': str(total_war), 'inline': True},
                {'name': 'Chain Outside', 'value': str(total_chain), 'inline': True},
                {'name': 'Friendly Hosps', 'value': str(total_hosp), 'inline': True},
                {'name': 'Total', 'value': str(total_all), 'inline': True},
            ]
        )

        # Build monospace table
        max_name = max((len(m['name']) for m in sorted_members), default=10)
        max_name = min(max_name, 15)

        header = f'{"#":>2}  {"Name":<{max_name}}  War  Chn  Hsp  Tot'
        separator = '-' * len(header)
        table_lines = [header, separator]

        for i, m in enumerate(sorted_members):
            name = m['name'][:max_name]
            line = (f'{i+1:>2}  {name:<{max_name}}  {m["war_hits"]:>3}  '
                    f'{m["chain_outside"]:>3}  {m["friendly_hosps"]:>3}  {m["total"]:>3}')
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

    async def format_war_management_report(self, war_info, final_data=None,
                                           attack_stats=None):
        """
        Create a detailed management report with incoming/outgoing attacks
        Posted to private war management channel for leaders/management

        Args:
            war_info: War data from Torn API
            final_data: Final war data snapshot (if war ended)
            attack_stats: Output of _get_war_attack_stats, supplying the
                per-member incoming counts. Without it every member reports 0
                hits taken - Torn's v2 ranked war report dropped the `defends`
                field that used to carry them, so the attacks log is now the
                only source. Passed in rather than fetched here because the
                war-end handler already pages the whole log for friendly hosps.

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

            # Times this member was hit. `defends` is kept only as a
            # fallback for cached v1-era reports; live v2 reports no longer
            # carry it, which is why this read 0 for the whole faction.
            log_stats = (attack_stats or {}).get(user_id) or {}
            incoming = log_stats.get('war_incoming', stats.get('defends', 0))
            outside_incoming = log_stats.get('outside_incoming', 0)

            score = stats.get('score', 0)

            member_stats.append({
                'name': member_name,
                'user_id': user_id,
                'outgoing': outgoing,
                'incoming': incoming,
                'outside_incoming': outside_incoming,
                'score': score
            })

        # Sort by score (highest first)
        sorted_stats = sorted(member_stats, key=lambda x: x['score'], reverse=True)

        # Get war result
        our_score = our_faction_data.get('score', 0) if our_faction_data else 0
        enemy_score = enemy_faction_data.get('score', 0) if enemy_faction_data else 0
        enemy_name = enemy_faction_data.get('name', 'Unknown') if enemy_faction_data else 'Unknown'

        if our_score > enemy_score:
            result = '🏆 VICTORY'
            color = EMBED_COLOR_SUCCESS
        elif enemy_score > our_score:
            result = '❌ DEFEAT'
            color = EMBED_COLOR_CRITICAL
        else:
            result = '🤝 DRAW'
            color = EMBED_COLOR_WARNING

        # Build management embeds (may need multiple to stay under Discord limits)
        embeds = []

        embed = create_embed(
            title=f'⚔️ War Management Report - {result}',
            description=f'**War #{war_id}** vs {enemy_name}\n**Final Score:** {our_score} - {enemy_score}',
            color=color
        )

        # Add summary stats
        total_outgoing = sum(m['outgoing'] for m in member_stats)
        total_incoming = sum(m['incoming'] for m in member_stats)
        total_outside_in = sum(m['outside_incoming'] for m in member_stats)
        total_score = sum(m['score'] for m in member_stats)

        # Outside hits are called out separately rather than folded in, so a
        # big number can't be mistaken for the enemy faction farming us.
        hit_line = f'**Total Times Hit:** {total_incoming}'
        if total_outside_in:
            hit_line += f' (+{total_outside_in} outside the war)'

        embed.add_field(
            name='📊 Summary',
            value=f'**Total Attacks Made:** {total_outgoing}\n{hit_line}\n'
                  f'**Total Score:** {total_score}',
            inline=False
        )

        # Add detailed member breakdown (show all members)
        member_details = []
        for i, m in enumerate(sorted_stats):
            member_details.append(
                f'{i+1}. **{m["name"]}** — Attacks: {m["outgoing"]} | Hit: {m["incoming"]}x | Score: {m["score"]}'
            )

        detail_lines = member_details if member_details else ['No member data available']

        # Chunk into 1024-char pieces and add as fields, splitting to new embed if needed
        detail_chunks = self._chunk_text(detail_lines, 1024)
        for idx, chunk in enumerate(detail_chunks):
            if len(embed.fields) >= 20:
                embeds.append(embed)
                embed = create_embed(title='⚔️ Report (continued)', color=color)
            label = '👥 Member Performance' if len(detail_chunks) == 1 else f'👥 Members (Part {idx + 1})'
            embed.add_field(name=label, value=chunk, inline=False)

        # Add members who got hit the most (potential weak links)
        most_hit = sorted(member_stats, key=lambda x: x['incoming'], reverse=True)[:5]
        if most_hit:
            most_hit_text = '\n'.join([
                f'{i+1}. **{m["name"]}**: Hit {m["incoming"]}x'
                for i, m in enumerate(most_hit) if m["incoming"] > 0
            ])
            if most_hit_text:
                if len(embed.fields) >= 24:
                    embeds.append(embed)
                    embed = create_embed(title='⚔️ Report (continued)', color=color)
                embed.add_field(name='⚠️ Most Targeted', value=most_hit_text, inline=False)

        # Add members who didn't participate
        non_participants = [m for m in member_stats if m['outgoing'] == 0]
        if non_participants:
            non_lines = [f'• {m["name"]}' for m in non_participants]
            np_chunks = self._chunk_text(non_lines, 1024)
            for idx, chunk in enumerate(np_chunks):
                if len(embed.fields) >= 24:
                    embeds.append(embed)
                    embed = create_embed(title='⚔️ Report (continued)', color=color)
                label = '❌ No Participation' if len(np_chunks) == 1 else f'❌ No Participation ({idx + 1})'
                embed.add_field(name=label, value=chunk, inline=False)

        embeds.append(embed)
        return embeds

    async def format_warstats_overview_embed(self):
        """Faction-level overview of last 5 wars with top/bottom performers."""
        wars = self.war_member_stats.get('wars', [])
        if not wars:
            return create_embed(
                title='No War History',
                description='No per-member war data has been recorded yet.\nData is saved automatically when a war ends.',
                color=EMBED_COLOR_INFO
            )

        last_5 = wars[-5:]

        # War summary — annotate each with friendly-hosp count when present
        war_lines = []
        for w in reversed(last_5):
            icon = '✅' if w['result'] == 'victory' else '❌'
            hosp_n = len(w.get('friendly_hosps', []))
            hosp_tag = f' · 🏥 {hosp_n}' if hosp_n else ''
            war_lines.append(
                f'{icon} vs **{w["enemy_name"]}** — {w["our_score"]}-{w["enemy_score"]} '
                f'({format_timestamp(w["timestamp"], "R")}){hosp_tag}'
            )

        # Aggregate member performance (incl. friendly hosps)
        member_totals = {}
        for w in last_5:
            for uid, mdata in w.get('members', {}).items():
                if uid not in member_totals:
                    member_totals[uid] = {
                        'name': mdata['name'], 'total_attacks': 0,
                        'total_score': 0, 'wars_played': 0,
                        'hosps_given': 0, 'hosps_received': 0,
                    }
                member_totals[uid]['total_attacks'] += mdata.get('attacks', 0)
                member_totals[uid]['total_score'] += mdata.get('score', 0)
                member_totals[uid]['hosps_given'] += mdata.get('hosps_given', 0)
                member_totals[uid]['hosps_received'] += mdata.get('hosps_received', 0)
                member_totals[uid]['wars_played'] += 1
                member_totals[uid]['name'] = mdata['name']

        for data in member_totals.values():
            data['avg_score'] = data['total_score'] / data['wars_played'] if data['wars_played'] > 0 else 0

        sorted_by_score = sorted(member_totals.values(), key=lambda x: x['avg_score'], reverse=True)

        top_text = '\n'.join([
            f'{i+1}. **{m["name"]}** — Avg: {m["avg_score"]:.1f} score, '
            f'{m["total_attacks"]} attacks ({m["wars_played"]} wars)'
            for i, m in enumerate(sorted_by_score[:5])
        ]) or 'No data'

        # Bottom performers (at least 2 wars played)
        eligible = [m for m in sorted_by_score if m['wars_played'] >= 2]
        bottom_5 = eligible[-5:] if eligible else []
        bottom_text = '\n'.join([
            f'• **{m["name"]}** — Avg: {m["avg_score"]:.1f} score, '
            f'{m["total_attacks"]} attacks ({m["wars_played"]} wars)'
            for m in reversed(bottom_5)
        ]) or 'Not enough data (need 2+ wars)'

        # Top defensive players (most friendly hosps given across last 5 wars)
        sorted_by_hosps = sorted(
            (m for m in member_totals.values() if m['hosps_given'] > 0),
            key=lambda x: x['hosps_given'], reverse=True,
        )[:5]
        hosps_text = '\n'.join([
            f'{i+1}. **{m["name"]}** — {m["hosps_given"]} hosps given ({m["wars_played"]} wars)'
            for i, m in enumerate(sorted_by_hosps)
        ]) or 'No friendly hospitalisations recorded'

        embed = create_embed(
            title='War Stats History',
            description=f'**Last {len(last_5)} Wars:**\n' + '\n'.join(war_lines),
            color=EMBED_COLOR_INFO,
            fields=[
                {'name': 'Top Performers (by avg score)', 'value': top_text, 'inline': False},
                {'name': 'Lowest Performers (2+ wars)', 'value': bottom_text, 'inline': False},
                {'name': '🛡️ Top Hospitalizers (kept teammates safe)', 'value': hosps_text, 'inline': False},
            ]
        )
        return embed

    async def format_war_trends_embed(self):
        """
        Faction-wide war trends: overall W-L record, win rate, and top
        contributors sorted by total score across all recorded wars.
        """
        wars = self.war_member_stats.get('wars', [])
        if not wars:
            return create_embed(
                title='No War History',
                description='No per-member war data has been recorded yet.\nData is saved automatically when a war ends.',
                color=EMBED_COLOR_INFO
            )

        total = len(wars)
        wins = sum(1 for w in wars if w['result'] == 'victory')
        losses = total - wins
        win_rate = (wins / total * 100) if total > 0 else 0

        # All wars listed (most recent first, up to 10)
        war_lines = []
        for w in reversed(wars[-10:]):
            icon = '✅' if w['result'] == 'victory' else '❌'
            war_lines.append(
                f'{icon} vs **{w["enemy_name"]}** — {w["our_score"]}-{w["enemy_score"]}'
                f' ({format_timestamp(w["timestamp"], "R")})'
            )

        # Aggregate all members across all wars
        member_totals = {}
        for w in wars:
            for uid, mdata in w.get('members', {}).items():
                if uid not in member_totals:
                    member_totals[uid] = {
                        'name': mdata['name'],
                        'total_attacks': 0,
                        'total_score': 0,
                        'wars_played': 0,
                    }
                member_totals[uid]['total_attacks'] += mdata.get('attacks', 0)
                member_totals[uid]['total_score'] += mdata.get('score', 0)
                member_totals[uid]['wars_played'] += 1
                member_totals[uid]['name'] = mdata['name']  # keep name up to date

        sorted_members = sorted(member_totals.values(), key=lambda x: x['total_score'], reverse=True)

        top_text = '\n'.join([
            f'{i+1}. **{m["name"]}** — {m["total_score"]} score, '
            f'{m["total_attacks"]} attacks ({m["wars_played"]}/{total} wars)'
            for i, m in enumerate(sorted_members[:10])
        ]) or 'No data'

        description = (
            f'**Record:** {wins}W — {losses}L ({win_rate:.0f}% win rate)\n'
            f'**Wars tracked:** {total}\n\n'
            + '\n'.join(war_lines)
        )

        embed = create_embed(
            title='War Trends',
            description=description,
            color=EMBED_COLOR_SUCCESS if wins >= losses else EMBED_COLOR_WARNING,
            fields=[
                {'name': 'Top Contributors (all-time score)', 'value': top_text, 'inline': False},
            ]
        )
        return embed

    async def format_warstats_member_embed(self, torn_id, member_name=None):
        """Individual member's war history for last 5 wars."""
        wars = self.war_member_stats.get('wars', [])
        if not wars:
            return create_embed(
                title='No War History',
                description='No per-member war data has been recorded yet.',
                color=EMBED_COLOR_INFO
            )

        # Find this member's records
        member_wars = []
        for w in wars:
            members = w.get('members', {})
            if str(torn_id) in members:
                mdata = members[str(torn_id)]
                member_wars.append({
                    'enemy_name': w['enemy_name'],
                    'result': w['result'],
                    'timestamp': w['timestamp'],
                    'attacks': mdata.get('attacks', 0),
                    'defends': mdata.get('defends', 0),
                    'score': mdata.get('score', 0),
                    'hosps_given': mdata.get('hosps_given', 0),
                    'hosps_received': mdata.get('hosps_received', 0),
                    'name': mdata.get('name', 'Unknown')
                })

        if not member_wars:
            return create_embed(
                title='No War Data',
                description=f'No war records found for this member.',
                color=EMBED_COLOR_WARNING
            )

        display_name = member_name or member_wars[-1]['name']
        last_5 = member_wars[-5:]

        lines = []
        for w in reversed(last_5):
            icon = '✅' if w['result'] == 'victory' else '❌'
            hosp_tag = ''
            if w['hosps_given'] or w['hosps_received']:
                hosp_tag = f' | 🏥 gave {w["hosps_given"]} / received {w["hosps_received"]}'
            lines.append(
                f'{icon} **vs {w["enemy_name"]}** ({format_timestamp(w["timestamp"], "R")})\n'
                f'   Attacks: {w["attacks"]} | Defends: {w["defends"]} | Score: {w["score"]}{hosp_tag}'
            )

        avg_attacks = sum(w['attacks'] for w in last_5) / len(last_5)
        avg_score = sum(w['score'] for w in last_5) / len(last_5)
        total_hosps_given = sum(w['hosps_given'] for w in member_wars)
        total_hosps_received = sum(w['hosps_received'] for w in member_wars)

        fields = [
            {'name': 'Avg Attacks', 'value': f'{avg_attacks:.1f}', 'inline': True},
            {'name': 'Avg Score', 'value': f'{avg_score:.1f}', 'inline': True},
            {'name': 'Wars Recorded', 'value': str(len(member_wars)), 'inline': True},
        ]
        if total_hosps_given or total_hosps_received:
            fields.append({
                'name': '🏥 Friendly Hosps (all-time)',
                'value': f'Gave: **{total_hosps_given}** · Received: **{total_hosps_received}**',
                'inline': False,
            })

        embed = create_embed(
            title=f'War Stats — {display_name}',
            description=f'Showing last {len(last_5)} of {len(member_wars)} recorded wars',
            color=EMBED_COLOR_INFO,
            fields=fields,
        )

        detail_text = '\n\n'.join(lines)
        if len(detail_text) <= 1024:
            embed.add_field(name='War History', value=detail_text, inline=False)
        else:
            chunks = self._chunk_text(lines, 1024)
            for idx, chunk in enumerate(chunks):
                label = 'War History' if idx == 0 else 'War History (cont.)'
                embed.add_field(name=label, value=chunk, inline=False)

        return embed

    async def check_pending_war(self):
        """
        Detect a ranked war that's been declared but hasn't started yet — Torn
        gives a multi-day notice window between declaration and start.

        DRY-RUN STAGE: only logs the raw payload and posts a plain notice embed.
        Deliberately NOT wired up to auto-create an availability poll or
        auto-populate the enemy watchlist yet — that comes after we've
        inspected a real declaration's data shape from the logged payload.
        """
        rankedwars = await self._fetch_rankedwars()
        if not rankedwars:
            return

        now = get_current_timestamp()
        updated = False

        for war_id, war_data in rankedwars.items():
            war = war_data.get('war', {})
            start = war.get('start', 0)

            if not start or start <= now:
                continue  # already started, or no start time yet

            war_id_str = str(war_id)
            if war_id_str in self.announced_pending_wars:
                continue  # already announced this one

            logger.info(f'PENDING WAR DETECTED (raw payload for inspection): war_id={war_id} data={war_data}')

            factions = war_data.get('factions', {})
            enemy_name = 'Unknown'
            for fid, fdata in factions.items():
                if str(fid) != str(TORN_FACTION_ID):
                    enemy_name = fdata.get('name', 'Unknown')

            channel = self.bot.get_channel(WAR_CHANNEL_ID)
            if channel:
                embed = create_embed(
                    title='⚔️ War Declared',
                    description=(
                        f'A ranked war against **{enemy_name}** has been declared.\n'
                        f'Starts {format_timestamp(start, "R")} ({format_timestamp(start, "F")}).'
                    ),
                    color=EMBED_COLOR_WARNING
                )
                await channel.send(embed=embed)
                logger.info(f'Posted pending-war notice for war {war_id}')
            else:
                logger.warning(f'Could not post pending-war notice — war channel not found (ID: {WAR_CHANNEL_ID})')

            self.announced_pending_wars.add(war_id_str)
            updated = True

        if updated:
            await self.save_pending_wars()

    async def check_war_update(self):
        """
        Check if war status changed and post update if needed
        Auto-posts war results when war ends
        Called by the background task every minute
        """
        # Check for a declared-but-not-yet-started war (dry-run notice only)
        await self.check_pending_war()

        # Get current war
        war_info = await self.get_current_war()
        channel = self.bot.get_channel(WAR_CHANNEL_ID)
        management_channel = self.bot.get_channel(WAR_MANAGEMENT_CHANNEL_ID) if WAR_MANAGEMENT_CHANNEL_ID != 0 else None

        if not channel:
            logger.warning(f'Warning: Could not find #war channel (ID: {WAR_CHANNEL_ID})')
            return

        # Detect war END (was active, now not)
        if self.was_war_active and not war_info:
            # Re-fetch raw rankedwars data to confirm the war is truly over.
            # get_current_war() returning None can mean:
            #   (a) API is down — raw_data is None
            #   (b) Torn glitch — war entry temporarily has a non-zero end timestamp
            #   (c) War genuinely ended — war entry has end <= now
            # We only announce in case (c).
            raw_data = await fetch_faction_data('rankedwars')
            if raw_data is None:
                logger.info('War: API unavailable, skipping war-end detection to prevent false announcement')
                return

            rankedwars = raw_data.get('rankedwars', {})
            now = get_current_timestamp()

            if self.current_war_id and str(self.current_war_id) in rankedwars:
                war_entry = rankedwars[str(self.current_war_id)]
                war = war_entry.get('war', {})
                end = war.get('end', 0)
                if not end or end > now:
                    # end is 0 or in the future — war is still active, Torn API glitch
                    logger.info(f'War: API returned end={end} for active war {self.current_war_id}, ignoring false end signal')
                    return

            logger.info('War ended! Posting war results...')

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
                logger.info('Posted war completion report to public channel')

                # Auto-clear enemy watchlist
                if self.enemy_watchlist:
                    cleared = self.enemy_watchlist.clear_all()
                    await self.enemy_watchlist.save_watchlist()
                    if cleared > 0:
                        logger.info(f'Auto-cleared {cleared} enemies from watchlist')

                # Fetch rankedwarreport for management report + war stats history
                report = await self._fetch_rankedwarreport(war_id=self.current_war_id)
                report_factions = report.get('factions', {}) if report else {}

                # Page the attacks log once. Both the management report (for
                # per-member incoming, which the v2 war report no longer
                # carries) and the friendly-hosp log below come from this pass.
                attack_stats = {}
                war_start_ts = final_war_data.get('start', 0)
                enemy_fid = final_war_data.get('enemy_faction_id')
                if war_start_ts and enemy_fid:
                    try:
                        attack_stats = await self._get_war_attack_stats(enemy_fid, war_start_ts)
                    except Exception as e:
                        logger.error(f'War end: failed to compute attack stats: {e}')

                # Post detailed management report (use rankedwarreport for per-member stats)
                if management_channel:
                    # Fall back to rankedwars data if report unavailable (no member data though)
                    mgmt_factions = report_factions if report_factions else final_war_data.get('factions', {})
                    management_embeds = await self.format_war_management_report(
                        None,
                        final_data={
                            'war_id': self.current_war_id,
                            'factions': mgmt_factions
                        },
                        attack_stats=attack_stats,
                    )
                    if management_embeds:
                        for i, mgmt_embed in enumerate(management_embeds):
                            content = '📊 **War Management Report**' if i == 0 else None
                            await management_channel.send(content=content, embed=mgmt_embed)
                        logger.info('Posted detailed war report to management channel')
                    else:
                        logger.warning(
                            f'War end: management report not posted for war {self.current_war_id} — '
                            f'format_war_management_report returned no embeds (likely no our-faction '
                            f'member data in the war report yet)'
                        )
                else:
                    logger.warning('Warning: War management channel not configured')

                # Friendly hospitalisations, from the same single pass above
                friendly_hosps = attack_stats.get('_friendly_hosps', [])
                if friendly_hosps:
                    logger.info(f'War end: collected {len(friendly_hosps)} friendly hosp event(s)')

                # Save per-member war stats + friendly-hosp log. Save even when
                # report_factions is empty so the friendly-hosp event log alone
                # still gets preserved.
                if report_factions or friendly_hosps:
                    await self._save_war_member_data(
                        self.current_war_id, report_factions or {},
                        our_score, enemy_score, enemy_name,
                        friendly_hosps=friendly_hosps,
                    )

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
            logger.info(f'War detected: {war_id}')

        # Save war data to history (faction-level scores for final report)
        # Persist start/enemy_faction_id so the war-end handler can compute
        # friendly-hosp stats without a second rankedwars fetch.
        war_data = war_info.get('data', {})
        enemy_faction_id = None
        for fid in war_data.get('factions', {}).keys():
            if str(fid) != str(TORN_FACTION_ID):
                enemy_faction_id = str(fid)
                break
        self.war_history[war_id] = {
            'timestamp': get_current_timestamp(),
            'factions': war_data.get('factions', {}),
            'start': war_data.get('war', {}).get('start', 0),
            'enemy_faction_id': enemy_faction_id,
        }

        # Prune old war entries (keep last 20)
        if len(self.war_history) > 20:
            sorted_keys = sorted(self.war_history.keys(), key=lambda k: self.war_history[k].get('timestamp', 0))
            for old_key in sorted_keys[:-20]:
                del self.war_history[old_key]

        await self.save_war_history()
