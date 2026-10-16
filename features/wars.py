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

import asyncio
import discord
from datetime import datetime
from utils.helpers import load_json, save_json, create_embed, format_timestamp, get_current_timestamp
from utils.torn_api import fetch_faction_data, fetch_faction_data_v2, fetch_faction_attacks
from config import (
    WAR_CHANNEL_ID,
    LEADER_USER_ID,
    INACTIVITY_ALERTS_CHANNEL_ID,
    AVAILABILITY_CHANNEL_ID,
    STRIKES_CHANNEL_ID,
    WAR_OFFLINE_STRIKE_HOURS,
    PREWAR_OFFLINE_HOURS,
    TURTLE_HIT_THRESHOLD as _TURTLE_HIT_THRESHOLD,
    TURTLE_WINDOW_MINUTES as _TURTLE_WINDOW_MINUTES,
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

# The attacks log is the only source for hits taken and outside hits, and at
# war end those numbers are archived permanently. A transient 504 on one page
# is worth retrying rather than silently shortening the war.
# ===== Turtle alerts =====
# The war guide calls this the faction's biggest problem: "last war we bled
# most of our points through members getting farmed while they were offline".
#
# The threshold is measured, not guessed. Across war 50338's 1005 incoming
# hits, 925 of them were "Attacked" (Leave), which leaves the target OUT of
# hospital and therefore still farmable - that is what farming looks like here.
# Simulated against that real log:
#   2 hits in 30min -> 128 alerts/day (fires for 33 of 35 members: useless)
#   3 hits in 30min ->   4.7/day, 8 members
#   4 hits in 60min ->  12.0/day, 16 members   <- chosen
#   5 hits in 60min ->   1.0/day, 2 members (too narrow to help)
# 4-in-60 subsumes a 3-in-30 burst a little later, and catches twice as many
# people as 3-in-30 for noise that is still well under one an hour.
# ===== Chain bonus hits =====
# Chain positions that pay a big FLAT respect bonus, ignoring fair fight, war
# and chain multipliers. The war guide makes taking one on a non-enemy during
# a war a strike, because that respect does not count toward the war score.
CHAIN_BONUS_MILESTONES = frozenset({
    10, 25, 50, 100, 250, 500, 1000, 2500,
    5000, 10000, 25000, 50000, 100000,
})
# The ordinary chain multiplier caps at x2, so a modifier above that is the
# flat bonus rather than a multiplier. Measured over 2,827 attacks: normal
# chain_bonus values ran 1.0-1.74 and bonus hits came back as exactly 10, 20,
# 40 and 80 (positions 10/25/50/100). Nothing landed between 2 and 10.
CHAIN_BONUS_MODIFIER_FLOOR = 2

# ===== Pre-war revive check =====
# The war guide tells everyone to set revives to "nobody" for the whole war.
# A revivable member who gets hospitalised is pulled straight back out and
# farmed again, which is the bleed the guide calls our biggest problem.
#
# Torn exposes this: user/{id}?selections=profile returns `revivable` for OTHER
# faction members on the faction key. That is unusual - most per-player
# settings refuse (selections=travel returns error 7 for anyone but the key
# owner) - so it is worth stating plainly that this one does not.
#
# revivable=1 means SOMEBODY can revive them. It cannot tell "Everyone" apart
# from "Friends & faction", but neither of those is "Nobody", which is what the
# rule asks for.
#
# Seconds before war start to post the check. ONE pass, close enough to the
# start that it is the last word and people still have time to act on it.
# Deliberately not a second pass two days out: the availability poll already
# carries the reminder in text, and naming and tagging people that early just
# means doing it twice. Costs one profile call per member, so it must stay a
# single pass and never a loop.
REVIVE_CHECK_CHECKPOINTS = (6 * 3600,)

TURTLE_WINDOW_SECONDS = _TURTLE_WINDOW_MINUTES * 60
TURTLE_HIT_THRESHOLD = _TURTLE_HIT_THRESHOLD
TURTLE_COOLDOWN_SECONDS = 3600

# States where a member cannot be attacked at all. Everything else counts as
# exposed - including Abroad, which is the WORST case rather than a safe one:
# landed abroad gives the attacker an extra x1.25, which the guide singles out.
# Only Traveling (still in the air) is actually safe.
TURTLED_STATES = {'Hospital', 'Jail', 'Federal', 'Traveling'}

ATTACK_PAGE_RETRIES = 3
ATTACK_PAGE_RETRY_DELAY = 2

# Shown instead of silently printing zeros when the attacks log is unreadable.
WAR_LOG_INCOMPLETE_NOTE = (
    '\u26a0\ufe0f **Torn\'s attacks log could not be read in full.** Hits taken, '
    'outside hits and friendly hosps below are incomplete. Try again shortly.'
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
        self.pending_wars_file = 'data/pending_wars.json'
        self.turtle_alerts_file = 'data/turtle_alerts.json'
        self.revive_checks_file = 'data/revive_checks.json'
        # {war_id: [checkpoint seconds already posted]}
        self.revive_checks = {}
        self.turtle_cooldowns = {}  # {torn_id: last_alert_ts}
        # {torn_id: longest seconds seen offline during the CURRENT war}.
        # last_action only moves forward when a member acts, so (now - it)
        # sampled each tick IS their current offline streak; the max over the
        # war is the longest one, without storing any history.
        self.war_offline = {}
        self.verification_system = None  # set by bot.py, for Discord mentions
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

    async def load_turtle_alerts(self):
        """Load per-member turtle alert cooldowns."""
        data = load_json(self.turtle_alerts_file, default={})
        self.turtle_cooldowns = data.get('cooldowns', {})
        self.war_offline = data.get('war_offline', {})

    async def save_turtle_alerts(self):
        save_json(self.turtle_alerts_file, {
            'cooldowns': self.turtle_cooldowns,
            'war_offline': self.war_offline,
        })

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
                                     enemy_name, friendly_hosps=None, attack_stats=None):
        """Save per-member war data + friendly-hosp log to persistent history. Keeps last 10 wars.

        Saves even if rankedwarreport member data is missing — the friendly-hosp
        event log alone is still valuable to preserve.

        attack_stats (from _get_war_attack_stats) supplies hits taken. Without
        it `defends` persists as 0 for everyone, and unlike the display bugs
        this one is permanent: it writes a wrong number into war history that
        no later fix can recover, because the attacks log is only queryable for
        so long. The stored key keeps its `defends` name so records written
        before this still render.
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
            if attack_stats is None:
                # The log was unreadable. Archiving 0 here would be permanent
                # and indistinguishable from a member who was never touched,
                # so record "unknown" and let the history view say so.
                war_inc = outside_inc = None
            else:
                war_inc, outside_inc = self._incoming_split(
                    stats, attack_stats.get(user_id) or {})
            members_data[user_id] = {
                'name': name,
                'attacks': stats.get('attacks', 0),
                'defends': war_inc,
                'outside_incoming': outside_inc,
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
        Uses the v2 'rankedwarreport' selection (v1 answers error 23 since
        Torn retired it). v2 gives attacks/score per member but NOT defends —
        hits taken come from the attacks log instead, see _incoming_split.
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

    async def get_upcoming_war(self):
        """
        The soonest ranked war that is enlisted but has NOT started yet.

        get_latest_war() deliberately only matches wars already underway, so
        nothing in the bot could see an upcoming one until now. Reuses the same
        cached rankedwars fetch, so asking costs no extra API call.

        Returns {'war_id', 'start', 'data'} or None.
        """
        rankedwars = await self._fetch_rankedwars()
        if not rankedwars:
            return None

        now = get_current_timestamp()
        soonest = None
        for war_id, war_data in rankedwars.items():
            war = war_data.get('war', {})
            start = war.get('start', 0)
            end = war.get('end', 0)
            # Not started, and not a finished war whose start we are re-reading
            if start > now and not end:
                if soonest is None or start < soonest['start']:
                    soonest = {'war_id': str(war_id), 'start': start, 'data': war_data}
        return soonest

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

    async def _fetch_all_attacks_since(self, war_start, war_end=None):
        """
        Fetch ALL faction attacks in the war window by paginating through the API.
        The Torn attacks API returns ~100 attacks per call, so we page through
        using ascending sort and shifting the 'from' timestamp.

        Args:
            war_start: Unix timestamp to start fetching from
            war_end: Unix timestamp to stop at, or None for an ongoing war.
                Without it the window never closes, so every later hit on the
                former opponent keeps counting as a war hit — measured on war
                50338, the log read 422 against the official 421 because one
                member hit them again after it was over. Left unbounded, Xanax
                reimbursement for a finished war grows the longer you wait.

        Returns:
            Dict of {attack_id: attack_data} with all attacks since war_start,
            or None if the log could not be read in full. Callers must treat
            None as "unknown", never as "no attacks" - a partial log produces
            confidently wrong hit counts, and at war end those get archived
            where no later fix can reach them.
        """
        all_attacks = {}
        current_from = int(war_start)
        # ~100 attacks a page, so this allows 20k - far beyond any real war.
        # Exhausting it means something is wrong, not that the war was busy.
        max_pages = 200

        for page in range(max_pages):
            attacks = None
            for attempt in range(ATTACK_PAGE_RETRIES):
                attacks = await fetch_faction_attacks(
                    from_ts=current_from, to_ts=war_end, sort='asc')
                if attacks is not None:
                    break
                if attempt + 1 < ATTACK_PAGE_RETRIES:
                    logger.warning(f'War attacks page {page + 1}: fetch failed, '
                                   f'retry {attempt + 1}/{ATTACK_PAGE_RETRIES - 1}')
                    await asyncio.sleep(ATTACK_PAGE_RETRY_DELAY)

            if attacks is None:
                # Out of retries. Returning what we have would look like a
                # complete, much quieter war than actually happened.
                logger.error(f'War: attacks log incomplete - page {page + 1} '
                             f'unreachable after {ATTACK_PAGE_RETRIES} tries; '
                             f'reporting unknown rather than {len(all_attacks)} partial')
                return None

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

            # Nothing new means the last page repeated itself - we have it all.
            # This is the only stop condition, deliberately: a short page used
            # to end the loop too, but "fewer than 90" is a guess about the
            # API's page size, and guessing wrong truncates silently. One
            # redundant call at the end is the cheaper mistake.
            if new_count == 0:
                break

            # Page from the latest timestamp WITHOUT adding a second. `from` is
            # inclusive, so re-reading the boundary is free (attack ids dedupe
            # above) - whereas +1 skipped every attack sharing that second with
            # the page's last record, which during a chain is several.
            current_from = latest_timestamp

        else:
            logger.error(f'War: hit the {max_pages}-page cap with '
                         f'{len(all_attacks)} attacks - log is incomplete')
            return None

        logger.info(f'War: Fetched {len(all_attacks)} total attacks since war start')
        return all_attacks

    @staticmethod
    def _incoming_split(war_stats, log_stats):
        """Split hits taken into (war, outside).

        Shared by the participation board and the management report so the two
        can never disagree about the same war.

        The ranked war report's `defends` is authoritative when it is actually
        there, so it still wins - but v2 dropped the field, and reading a
        MISSING field with .get('defends', 0) is indistinguishable from a real
        zero. That is what made war 50338 show "War Incoming 0 / Outside
        Incoming 1005": every one of the 931 war hits fell through into the
        outside bucket, because outside was derived as total minus a defends
        that was never there.

        Falling back to the attacks log fixes it. The log classifies by the war
        modifier, which survives stealth attacks - 395 of those 931 had blank
        attacker fields, so anything keyed on attacker_faction would miss them.
        """
        defends = war_stats.get('defends')
        if defends is not None:
            total_inc = log_stats.get('total_incoming', 0)
            return defends, max(0, total_inc - defends)
        return log_stats.get('war_incoming', 0), log_stats.get('outside_incoming', 0)

    async def _get_war_attack_stats(self, enemy_faction_id, war_start, war_end=None):
        """
        Compute per-member war statistics from the faction attacks log.
        Paginates through ALL attacks since war start for accurate data.

        Args:
            enemy_faction_id: The enemy faction ID in the ranked war
            war_start: War start timestamp to filter attacks
            war_end: War end timestamp, or None while the war is running

        Returns:
            Dict of {user_id: {'war_hits': N, 'outside_hits': N, 'total_incoming': N,
                               'war_incoming': N, 'outside_incoming': N,
                               'hosps_given': N, 'hosps_received': N,
                               'mugs_given': N, 'mugs_outside': N}},
            or None if the attacks log could not be read in full. Callers must
            branch on None rather than render the zeros it would otherwise
            imply.
            A "_bonus_outside" key is added at top level with one record per
            chain bonus hit landed on a non-enemy:
              [{'timestamp', 'attacker_id', 'attacker_name', 'defender_id',
                'defender_name', 'defender_faction', 'chain', 'bonus'}, ...]
            A "_friendly_hosps" key is added at top level with the raw event log:
              [{'timestamp', 'attacker_id', 'attacker_name',
                'defender_id', 'defender_name'}, ...]
        """
        stats = {}
        friendly_hosps = []  # ordered event log
        bonus_outside = []   # ordered event log of bonus hits on non-enemies

        def _ensure(uid):
            if uid not in stats:
                stats[uid] = {
                    'war_hits': 0, 'outside_hits': 0, 'chain_outside_hits': 0,
                    'total_incoming': 0, 'war_incoming': 0, 'outside_incoming': 0,
                    'hosps_given': 0, 'hosps_received': 0,
                    'mugs_given': 0, 'mugs_outside': 0, 'mugs_faction': 0,
                    'bonus_outside': 0,
                }

        try:
            attacks = await self._fetch_all_attacks_since(war_start, war_end)
            if attacks is None:
                return None  # incomplete log - say "unknown", not "quiet war"
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

                # ----- Bonus hit on a non-enemy -----
                # "A bonus hit on an outside target during a war = 1 strike."
                # Checked HERE, above the friendly-hosp and mug branches, because
                # those `continue` - and a bonus spent on a faction mate is just
                # as wasted as one spent on a random, since neither is the enemy.
                if attacker_faction == our_faction and defender_faction != enemy_faction:
                    chain_pos = attack.get('chain') or 0
                    # Only a successful hit advances the chain. Measured over
                    # 2,827 attacks, every Lost/Stalemate/Escape/Interrupted/
                    # Timeout record carried chain=0, so this filter is belt and
                    # braces rather than the thing holding the check up.
                    if (chain_pos in CHAIN_BONUS_MILESTONES
                            and result in ('Attacked', 'Mugged', 'Hospitalized', 'Special')):
                        mods = attack.get('modifiers') or {}
                        bonus_mod = (mods.get('chain_bonus')
                                     if isinstance(mods, dict) else None)
                        # A modifier at or below x2 is positive evidence this was
                        # NOT the bonus hit, so it is not flagged. A MISSING field
                        # falls back to the chain position alone: if Torn renames
                        # the modifier the check should get noisier, never
                        # silently switch itself off the way `defends` did.
                        if bonus_mod is None or bonus_mod > CHAIN_BONUS_MODIFIER_FLOOR:
                            _ensure(attacker_id)
                            stats[attacker_id]['bonus_outside'] += 1
                            bonus_outside.append({
                                'timestamp': (attack.get('timestamp_ended')
                                              or attack.get('timestamp_started', 0)),
                                'attacker_id': attacker_id,
                                'attacker_name': attack.get('attacker_name', ''),
                                'defender_id': defender_id,
                                'defender_name': attack.get('defender_name', '') or 'unknown',
                                'defender_faction': attack.get('defender_factionname', ''),
                                'chain': chain_pos,
                                'bonus': bonus_mod,
                            })

                # Friendly hospitalization — teammate hosps teammate to keep them
                # safe from enemy attacks. Tracked separately from war/outside hits.
                if (attacker_faction == our_faction
                        and defender_faction == our_faction
                        and attacker_id != defender_id
                        and result == 'Mugged'):
                    # "No mugging each other" has no exception, unlike sparring
                    # and friendly hosps which are both allowed by agreement.
                    _ensure(attacker_id)
                    stats[attacker_id]['mugs_faction'] += 1
                    continue

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
                        # "Leave, don't mug" is a war rule, and hiding a mug
                        # that management finds is a strike. The log settles
                        # whether it happened; only a human can judge whether
                        # it was returned and owned up to.
                        if result == 'Mugged':
                            stats[attacker_id]['mugs_given'] += 1
                    else:
                        stats[attacker_id]['outside_hits'] += 1
                        # Mugging a non-enemy during a war still costs 25% of
                        # the respect the chain would have earned, so it is
                        # tracked too - just reported apart from mugging the
                        # opponent, which is the explicit war rule.
                        if result == 'Mugged':
                            stats[attacker_id]['mugs_outside'] += 1
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
            # Half-built stats are indistinguishable from a quiet war, so this
            # reports unknown too rather than returning what it managed.
            logger.error(f'War: Error computing attack stats: {e}')
            return None

        # Attach raw friendly-hosp log under a non-uid key so callers can render it
        stats['_friendly_hosps'] = sorted(friendly_hosps, key=lambda h: h.get('timestamp', 0))
        stats['_bonus_outside'] = sorted(bonus_outside, key=lambda h: h.get('timestamp', 0))
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

    def _war_insight_text(self, report_factions, attack_stats, responders=None):
        """
        The read-out management actually wants from a war board: who carried
        it, who bled, who did nothing, and whether we are getting better.

        Counts lead each line, because "9 of 34 made no hits" lands harder
        than a list of names and survives being skim-read.

        Hits taken come from the attacks log, so when it could not be read
        this says so rather than reporting everyone as untouched - the same
        rule the rest of the war reporting follows.
        """
        our = {}
        for fid, fdata in (report_factions or {}).items():
            if str(fid) == str(TORN_FACTION_ID):
                our = fdata.get('members', {}) or {}
                break
        if not our:
            return None

        stats = attack_stats or {}
        log_ok = attack_stats is not None
        responders = responders or set()

        rows = []
        for tid, m in our.items():
            st = stats.get(str(tid)) or {}
            hits = m.get('attacks', 0) or 0
            taken = (st.get('total_incoming', 0) or 0) if log_ok else None
            rows.append({
                'tid': str(tid),
                'name': st.get('name') or m.get('name') or f'ID:{tid}',
                'hits': hits,
                'score': m.get('score', 0) or 0,
                'taken': taken,
            })

        total = len(rows)
        fought = [r for r in rows if r['hits'] > 0]
        nothing = [r for r in rows if r['hits'] == 0]
        excused = [r for r in nothing if r['tid'] in responders]

        lines = []

        # --- who carried it ---
        top = sorted(fought, key=lambda r: -r['score'])[:3]
        if top:
            total_score = sum(r['score'] for r in rows) or 1
            share = sum(r['score'] for r in top) / total_score * 100
            lines.append(
                '**Carried it** \u00b7 ' + ', '.join(
                    f"{r['name']} ({r['hits']} hits, {r['score']:.0f})" for r in top)
                + f'\n-# Top 3 scored {share:.0f}% of our total. '
                  f'{len(fought)} of {total} made at least one hit.')

        # --- who bled ---
        if log_ok:
            bled = sorted((r for r in rows if (r['taken'] or 0) > 0),
                          key=lambda r: -(r['taken'] - r['hits']))[:3]
            total_taken = sum(r['taken'] or 0 for r in rows)
            if bled:
                lines.append(
                    '**Bled the most** \u00b7 ' + ', '.join(
                        f"{r['name']} ({r['taken']} taken vs {r['hits']} made)"
                        for r in bled)
                    + f'\n-# {total_taken} hits taken across the faction. '
                      f'{sum(1 for r in rows if (r["taken"] or 0) > r["hits"])} '
                      f'members took more than they made.')
        else:
            lines.append('**Bled the most** \u00b7 unknown \u2014 the attacks log '
                         'could not be read in full, so hits taken are not '
                         'reported rather than shown as zero.')

        # --- who did nothing ---
        if nothing:
            silent = [r for r in nothing if r['tid'] not in responders]
            names = ', '.join(r['name'] for r in sorted(
                silent, key=lambda r: r['name'])[:12])
            more = f' +{len(silent) - 12} more' if len(silent) > 12 else ''
            lines.append(
                f'**Did nothing** \u00b7 {len(nothing)} of {total} made no hits'
                + (f', {len(excused)} of them answered the poll'
                   if excused else '')
                + (f'\n-# No hits and no poll answer: {names}{more}'
                   if silent else '\n-# All of them answered the poll.'))

        # --- against last war ---
        prev = [w for w in (self.war_member_stats.get('wars') or [])
                if w.get('members')]
        if prev:
            last = prev[-1]
            lm = last.get('members') or {}
            last_hits = sum(m.get('attacks', 0) or 0 for m in lm.values())
            last_none = sum(1 for m in lm.values() if not m.get('attacks'))
            this_hits = sum(r['hits'] for r in rows)

            def delta(now, before, unit=''):
                if not before:
                    return f'{now}{unit}'
                d = now - before
                sign = '+' if d > 0 else ''
                return f'{now}{unit} ({sign}{d})'

            lines.append(
                f'**Against war #{last.get("war_id")}** ({last.get("enemy_name")}, '
                f'{last.get("result")}) \u00b7 '
                f'hits {delta(this_hits, last_hits)}, '
                f'no-shows {delta(len(nothing), last_none)}, '
                f'roster {delta(total, len(lm))}')

        return '\n\n'.join(lines) if lines else None

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
        # Bound the window at war end. Without it every later hit on the
        # former opponent still counts as a war hit, so a finished war's
        # numbers keep climbing (measured: 422 vs the official 421).
        war_end = war.get('end', 0) or None  # None while the war is running
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
            attack_stats = await self._get_war_attack_stats(
                enemy_faction_id, war_start, war_end)
        # None means the log couldn't be read in full. Render the report from
        # what the war report alone provides and label the gap, rather than
        # printing zeros that read as "nobody was hit".
        attacks_log_ok = attack_stats is not None
        if not attacks_log_ok:
            attack_stats = {}

        if our_war_members:
            logger.info(f'War: Using rankedwarreport data ({len(our_war_members)} members)')
            # Use rankedwarreport data (accurate and complete)
            for user_id, member_data in faction_members.items():
                member_name = member_data.get('name', f'ID:{user_id}')
                war_stats = our_war_members.get(user_id, {})
                log_stats = attack_stats.get(user_id, {})

                war_inc, outside_inc = self._incoming_split(war_stats, log_stats)

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
        if not attacks_log_ok:
            description = f'{description}\n\n{WAR_LOG_INCOMPLETE_NOTE}'

        embed = create_embed(
            title='War Participation Report',
            description=description + legend,
            color=color,
            fields=embed_fields,
        )
        if top_hospitalizers:
            top_text = '\n'.join(
                f'{i+1}. **{h["name"]}** · {h["count"]}x'
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
        # Bound the window at war end. Without it every later hit on the
        # former opponent still counts as a war hit, so a finished war's
        # numbers keep climbing (measured: 422 vs the official 421).
        war_end = war.get('end', 0) or None  # None while the war is running
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
            attack_stats = await self._get_war_attack_stats(
                enemy_faction_id, war_start, war_end)
        xan_log_ok = attack_stats is not None
        if not attack_stats:
            attack_stats = {}

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
        if not xan_log_ok:
            description = f'{description}\n\n{WAR_LOG_INCOMPLETE_NOTE}'

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
            incoming, outside_incoming = self._incoming_split(stats, log_stats)

            score = stats.get('score', 0)

            member_stats.append({
                'name': member_name,
                'user_id': user_id,
                'outgoing': outgoing,
                'incoming': incoming,
                'outside_incoming': outside_incoming,
                'mugs': log_stats.get('mugs_given', 0),
                'mugs_outside': log_stats.get('mugs_outside', 0),
                'mugs_faction': log_stats.get('mugs_faction', 0),
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

        description = (f'**War #{war_id}** vs {enemy_name}\n'
                       f'**Final Score:** {our_score} - {enemy_score}')
        if attack_stats is None:
            description = f'{description}\n\n{WAR_LOG_INCOMPLETE_NOTE}'

        embed = create_embed(
            title=f'⚔️ War Management Report - {result}',
            description=description,
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
                f'{i+1}. **{m["name"]}** · Attacks: {m["outgoing"]} | Hit: {m["incoming"]}x | Score: {m["score"]}'
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

        # Rule checks the attacks log can actually settle. Deliberately framed
        # as "to check", not as strikes: the log proves a mug happened, but the
        # rules forgive one that was returned and owned up to, and it cannot see
        # whether someone posted a notice. Management decides; this just means
        # nobody has to read 1,800 attack rows to find the candidates.
        muggers = [m for m in member_stats
                   if m.get('mugs') or m.get('mugs_outside') or m.get('mugs_faction')]
        if muggers:
            mug_lines = []
            for m in sorted(muggers, key=lambda x: -(x['mugs_faction'] * 100
                                                     + x['mugs'] * 10
                                                     + x['mugs_outside']))[:15]:
                bits = []
                if m['mugs_faction']:
                    bits.append(f'**{m["mugs_faction"]}x on a FACTION MATE**')
                if m['mugs']:
                    bits.append(f'**{m["mugs"]}x on the opponent**')
                if m['mugs_outside']:
                    bits.append(f'{m["mugs_outside"]}x outside')
                mug_lines.append(f'- **{m["name"]}** mugged ' + ', '.join(bits))
            mug_lines.append(
                '-# Talk to them before issuing anything. The rules forgive a mug '
                'that was returned and apologised for, so ask first and get proof '
                'the money went back. Only then is it a strike, after the war.')
            if len(embed.fields) >= 24:
                embeds.append(embed)
                embed = create_embed(title='⚔️ Report (continued)', color=color)
            embed.add_field(name='🔍 To check', value='\n'.join(mug_lines),
                            inline=False)

        # Add members who didn't participate
        non_participants = [m for m in member_stats if m['outgoing'] == 0]
        if non_participants:
            non_lines = [f'• {m["name"]}' for m in non_participants]
            np_chunks = self._chunk_text(non_lines, 1024)
            for idx, chunk in enumerate(np_chunks):
                if len(embed.fields) >= 24:
                    embeds.append(embed)
                    embed = create_embed(title='⚔️ Report (continued)', color=color)
                label = ('❌ No Participation (strike unless they posted a notice)'
                         if len(np_chunks) == 1
                         else f'❌ No Participation ({idx + 1})')
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
                f'{icon} vs **{w["enemy_name"]}** · {w["our_score"]}-{w["enemy_score"]} '
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
            f'{i+1}. **{m["name"]}** · Avg: {m["avg_score"]:.1f} score, '
            f'{m["total_attacks"]} attacks ({m["wars_played"]} wars)'
            for i, m in enumerate(sorted_by_score[:5])
        ]) or 'No data'

        # Bottom performers (at least 2 wars played)
        eligible = [m for m in sorted_by_score if m['wars_played'] >= 2]
        bottom_5 = eligible[-5:] if eligible else []
        bottom_text = '\n'.join([
            f'• **{m["name"]}** · Avg: {m["avg_score"]:.1f} score, '
            f'{m["total_attacks"]} attacks ({m["wars_played"]} wars)'
            for m in reversed(bottom_5)
        ]) or 'Not enough data (need 2+ wars)'

        # Top defensive players (most friendly hosps given across last 5 wars)
        sorted_by_hosps = sorted(
            (m for m in member_totals.values() if m['hosps_given'] > 0),
            key=lambda x: x['hosps_given'], reverse=True,
        )[:5]
        hosps_text = '\n'.join([
            f'{i+1}. **{m["name"]}** · {m["hosps_given"]} hosps given ({m["wars_played"]} wars)'
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
                f'{icon} vs **{w["enemy_name"]}** · {w["our_score"]}-{w["enemy_score"]}'
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
            f'{i+1}. **{m["name"]}** · {m["total_score"]} score, '
            f'{m["total_attacks"]} attacks ({m["wars_played"]}/{total} wars)'
            for i, m in enumerate(sorted_members[:10])
        ]) or 'No data'

        description = (
            f'**Record:** {wins}W · {losses}L ({win_rate:.0f}% win rate)\n'
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
                    # None = archived while the attacks log was unreadable.
                    # Distinct from 0, which means genuinely never hit.
                    'defends': mdata.get('defends', 0),
                    'defends_known': mdata.get('defends') is not None,
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
                f'   Attacks: {w["attacks"]} | '
                f'Defends: {w["defends"] if w["defends_known"] else "?"} | '
                f'Score: {w["score"]}{hosp_tag}'
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
            title=f'War Stats · {display_name}',
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

    def _mention_for(self, torn_id):
        """Discord mention for a Torn id, or None if they never registered."""
        vs = self.verification_system
        if not vs:
            return None
        for discord_id, rec in (getattr(vs, 'verified_users', None) or {}).items():
            if str((rec or {}).get('torn_id')) == str(torn_id):
                return f'<@{discord_id}>'
        return None

    async def check_turtle_alerts(self):
        """
        Warn members who are being farmed while still attackable.

        Only runs during a war. Reads ONE page of the attacks log covering the
        last hour rather than paging the whole war: at war 50338's rate that is
        about 22 incoming hits an hour, far inside a single page, so this costs
        one API call a tick instead of twenty.
        """
        if not self.was_war_active:
            return
        now = get_current_timestamp()

        attacks = await fetch_faction_attacks(
            from_ts=now - TURTLE_WINDOW_SECONDS, sort='asc')
        if attacks is None:
            return  # outage; silence beats guessing who is being farmed

        our = str(TORN_FACTION_ID)
        counted = ('Attacked', 'Mugged', 'Hospitalized', 'Special')
        incoming = {}
        for a in attacks.values():
            if not isinstance(a, dict):
                continue
            if str(a.get('defender_faction')) != our:
                continue
            if str(a.get('attacker_faction')) == our:
                continue  # a friendly hosp is the opposite of a problem
            if a.get('result') not in counted:
                continue
            did = str(a.get('defender_id'))
            incoming[did] = incoming.get(did, 0) + 1

        candidates = {t: n for t, n in incoming.items() if n >= TURTLE_HIT_THRESHOLD}

        data = await fetch_faction_data('basic')
        members = (data or {}).get('members')
        if not members:
            return  # cannot tell who is turtled, so do not guess

        # Offline tracking runs every tick, candidates or not - a member can be
        # absent the whole war without ever being hit, and that is its own rule.
        offline_changed = False
        for tid, m in members.items():
            la = (m.get('last_action') or {}).get('timestamp')
            if not la:
                continue
            gap = now - int(la)
            if gap > self.war_offline.get(str(tid), 0):
                self.war_offline[str(tid)] = gap
                offline_changed = True
        if offline_changed:
            await self.save_turtle_alerts()

        if not candidates:
            return

        lines = []
        changed = False
        for tid, hits in sorted(candidates.items(), key=lambda kv: -kv[1]):
            member = members.get(tid)  # v1 basic keys member ids as strings
            if not member:
                continue
            state = (member.get('status') or {}).get('state', '')
            if state in TURTLED_STATES:
                continue  # already safe, nothing to nag about
            last = self.turtle_cooldowns.get(tid, 0)
            if now - last < TURTLE_COOLDOWN_SECONDS:
                continue

            name = member.get('name', tid)
            who = self._mention_for(tid) or f'**{name}**'
            where = 'abroad' if state == 'Abroad' else 'in Torn'
            extra = ' (abroad is worse: they get an extra x1.25)' if state == 'Abroad' else ''
            lines.append(
                f'{who} has taken **{hits} hits** in the last hour and is still '
                f'exposed {where}{extra}. Hospital or get in the air.'
            )
            self.turtle_cooldowns[tid] = now
            changed = True

        if changed:
            # Drop rows older than a day so this cannot grow without bound
            cutoff = now - 86400
            for k in [k for k, v in self.turtle_cooldowns.items() if v < cutoff]:
                del self.turtle_cooldowns[k]
            await self.save_turtle_alerts()

        if not lines:
            return

        channel = self.bot.get_channel(WAR_CHANNEL_ID) if self.bot else None
        if not channel:
            logger.warning('Turtle alert: war channel not found')
            return
        embed = create_embed(
            title='\U0001f6e1\ufe0f Stop the bleed',
            description='\n\n'.join(lines) + (
                '\n\n-# Every hit they land scores for them. Ipecac syrup or a '
                'wrong blood bag covers about an hour, a friendly bounty covers '
                'a night, and you cannot be hit at all while flying.'),
            color=EMBED_COLOR_WARNING,
        )
        await channel.send(embed=embed)
        logger.info(f'Turtle alert posted for {len(lines)} member(s)')

    async def post_strike_candidates(self, war_id, attack_stats, report_factions):
        """
        Turn what the attacks log settles about the war into strike candidates.

        Each one is posted on its own with Issue/Dismiss buttons rather than as
        a line in a list, because each needs its own decision and the ledger
        has to know which was which. The context is the war id, so the same
        member can legitimately be raised again next war.

        The tiers still differ in how certain they are, and the reason text
        says so. Mugging a faction mate has no exception in the rules (sparring
        and friendly hosps are allowed by agreement, mugging each other never
        is). Mugging the opponent breaks the war rule, but the rules forgive
        one that was returned and apologised for. Being absent is only a strike
        if no notice was posted, and the bot knows the poll but not what was
        said in chat. A bonus hit on a non-enemy the log settles completely.
        """
        sm = getattr(self, 'strike_manager', None)
        channel = self.bot.get_channel(STRIKES_CHANNEL_ID or WAR_MANAGEMENT_CHANNEL_ID) \
            if self.bot else None
        if not channel:
            logger.warning('Strike candidates: no channel configured')
            return

        from utils.torn_api import fetch_faction_members
        roster = await fetch_faction_members() or {}
        stats = attack_stats or {}

        def name_of(tid):
            return (roster.get(str(tid)) or {}).get('name', f'ID:{tid}')

        our_members = {}
        for fid, fdata in (report_factions or {}).items():
            if str(fid) == str(TORN_FACTION_ID):
                our_members = fdata.get('members', {}) or {}
                break

        posted = self._availability_responders(war_id)
        threshold = WAR_OFFLINE_STRIKE_HOURS * 3600

        # (torn_id, type, reason) - built in order of how certain they are, so
        # the channel reads worst-first.
        candidates = []

        for tid, st in stats.items():
            if tid.startswith('_') or not isinstance(st, dict):
                continue
            if st.get('mugs_faction'):
                candidates.append((tid, 'mug_faction',
                    f"Mugged a faction mate x{st['mugs_faction']} during war "
                    f"#{war_id}. The rules allow sparring and friendly hosps by "
                    f"agreement; mugging each other has no exception."))
            if st.get('mugs_given'):
                candidates.append((tid, 'mug_opp',
                    f"Mugged the opponent x{st['mugs_given']} during war "
                    f"#{war_id}. Ask first: if the money went back and they "
                    f"owned up, the rules forgive it. Get proof."))

        for b in (stats.get('_bonus_outside') or []):
            if not isinstance(b, dict):
                continue
            target = b.get('defender_name') or 'unknown'
            fac = b.get('defender_faction') or 'no faction'
            candidates.append((b.get('attacker_id'), 'bonus',
                f"Took chain bonus **{b.get('chain')}** on {target} ({fac}) "
                f"during war #{war_id}. That respect did not count toward the "
                f"war score."))

        for tid, secs in (self.war_offline or {}).items():
            if secs < threshold or str(tid) in posted:
                continue  # answering the poll is the notice
            candidates.append((tid, 'war_offline',
                f"Offline {secs / 3600:.0f}h during war #{war_id} and did not "
                f"answer the availability poll. Check for a notice in chat "
                f"before acting."))

        for tid, m in our_members.items():
            if m.get('attacks') or str(tid) in posted:
                continue
            candidates.append((tid, 'war_nohits',
                f"Made no attacks at all in war #{war_id} and did not answer "
                f"the availability poll."))

        if not candidates:
            return

        if sm:
            await channel.send(embed=create_embed(
                title=f'\u2696\ufe0f War #{war_id} \u00b7 {len(candidates)} strike candidate(s)',
                description='Each one below has its own Issue / Dismiss '
                            'buttons. Nothing is recorded until someone clicks, '
                            'and a dismissal is remembered.',
                color=EMBED_COLOR_WARNING))
            new = 0
            for tid, stype, reason in candidates:
                if await sm.post_candidate(tid, name_of(tid), stype, reason,
                                           context=str(war_id), channel=channel):
                    new += 1
            logger.info(f'War {war_id}: {len(candidates)} candidate(s), {new} posted')
            return

        # No ledger wired (shouldn't happen in production, but the war report
        # must never be lost just because the ledger failed to construct).
        lines = [f'- **{name_of(t)}** \u00b7 {r}' for t, _k, r in candidates[:15]]
        await channel.send(embed=create_embed(
            title=f'\u2696\ufe0f Strike candidates \u00b7 war #{war_id}',
            description='\n'.join(lines)[:4000],
            color=EMBED_COLOR_WARNING))
        logger.warning('Strike candidates posted without the ledger')

    def _availability_responders(self, war_id):
        """Torn ids of everyone who answered this war's availability poll."""
        am = getattr(self, 'availability_manager', None)
        vs = getattr(self, 'verification_system', None)
        if not am or not vs:
            return set()
        discord_ids = set()
        for poll_id, poll in (getattr(am, 'polls', None) or {}).items():
            if str(poll.get('war_id')) != str(war_id):
                continue
            for uids in (am.data.get(poll_id) or {}).values():
                discord_ids.update(str(u) for u in uids)
        if not discord_ids:
            return set()
        return {str((vs.verified_users.get(d) or {}).get('torn_id'))
                for d in discord_ids if d in (vs.verified_users or {})}

    async def load_revive_checks(self):
        """Which pre-war revive checkpoints have already been posted."""
        data = load_json(self.revive_checks_file, default={})
        self.revive_checks = data.get('done', {}) if isinstance(data, dict) else {}

    async def save_revive_checks(self):
        save_json(self.revive_checks_file, {'done': self.revive_checks})

    async def scan_revive_status(self):
        """
        Who in the faction can still be revived.

        Returns {'on': [(name, torn_id), ...], 'off': n, 'unknown': n,
                 'total': n} or None if the roster itself could not be read.

        The None-vs-empty rule applies twice here. An unreadable roster returns
        None, because "nobody has revives on" and "we could not look" must not
        render the same. A member whose own profile call fails is counted as
        UNKNOWN rather than assumed safe or assumed guilty - this message names
        people, so a failed lookup must never become an accusation.
        """
        from utils.torn_api import fetch_faction_members, fetch_player_data

        roster = await fetch_faction_members()
        if not roster:
            logger.warning('Revive check: roster unreadable, skipping')
            return None

        on, unknown, off = [], 0, 0
        for tid, member in roster.items():
            data = await fetch_player_data(str(tid), selections='profile')
            if not data:
                unknown += 1
                continue
            rev = data.get('revivable')
            if rev is None:
                # The field vanished from the payload. Say unknown rather than
                # let a .get() default quietly become the answer, which is how
                # the missing `defends` field turned into "0 hits taken".
                unknown += 1
            elif rev:
                on.append((member.get('name', f'ID:{tid}'), str(tid)))
            else:
                off += 1

        on.sort(key=lambda x: x[0].lower())
        return {'on': on, 'off': off, 'unknown': unknown, 'total': len(roster)}

    async def post_revive_check(self, channel, when_label):
        """
        Post the revive check to `channel`. Returns True if something was sent.

        `when_label` says how close the war is, because the same list means
        something different two days out than it does two hours out.
        """
        result = await self.scan_revive_status()
        if result is None:
            return False

        on, off, unknown, total = (result['on'], result['off'],
                                   result['unknown'], result['total'])

        if not on:
            desc = (f'All **{off}** readable members have revives off. '
                    f'Nothing to chase.')
            if unknown:
                desc += f'\n-# {unknown} could not be read this pass.'
            await channel.send(embed=create_embed(
                title=f'\u2705 Revive check \u00b7 {when_label}',
                description=desc, color=EMBED_COLOR_SUCCESS))
            return True

        # Mention where we can map them to Discord, because this is a thing the
        # member has to go and do, not something management can fix for them.
        lines, used = [], 0
        for name, tid in on:
            who = self._mention_for(tid) or f'**{name}**'
            line = f'- {who} ({name})' if who.startswith('<@') else f'- {who}'
            if used + len(line) + 1 > 3400:   # embed description cap is 4096
                lines.append(f'- ...and {len(on) - len(lines)} more')
                break
            lines.append(line)
            used += len(line) + 1

        desc = (f'**{len(on)} of {total}** members can still be revived. '
                f'Set revives to **nobody** before the war starts.\n\n'
                + '\n'.join(lines))
        if unknown:
            desc += (f'\n\n-# {unknown} member(s) could not be read this pass '
                     f'and are not listed either way.')
        desc += ('\n-# A revived member goes straight back to being farmed, '
                 'which is where most of our points bleed.')

        await channel.send(embed=create_embed(
            title=f'\U0001fa78 Revive check \u00b7 {when_label}',
            description=desc, color=EMBED_COLOR_WARNING))
        return True

    async def post_prewar_offline(self, channel, when_label):
        """
        Who has gone quiet in the days before a war starts.

        This is deliberately NOT the post-war absence check. The leader's rule
        is that members offline this long when we enlist get REMOVED to stop
        them being free points, which only works if it is known beforehand -
        reporting it after the war is too late to act on.

        Returns True if something was posted. An unreadable roster returns
        False so the checkpoint stays open and retries.
        """
        from utils.torn_api import fetch_faction_members
        roster = await fetch_faction_members()
        if not roster:
            logger.warning('Pre-war offline: roster unreadable, skipping')
            return False

        # Who has gone quiet is an activity question, so it reads alongside the
        # daily inactivity checks rather than in the war room, where it would
        # compete with the calls people actually need to act on.
        channel = (self.bot.get_channel(INACTIVITY_ALERTS_CHANNEL_ID) or channel) \
            if self.bot else channel

        now = get_current_timestamp()
        threshold = PREWAR_OFFLINE_HOURS * 3600
        quiet = []
        for tid, m in roster.items():
            la = (m.get('last_action') or {}).get('timestamp')
            if not la:
                continue  # unknown is not the same as offline
            gap = now - int(la)
            if gap >= threshold:
                quiet.append((m.get('name', f'ID:{tid}'), str(tid), gap / 3600))

        if not quiet:
            await channel.send(embed=create_embed(
                title=f'✅ Pre-war activity check · {when_label}',
                description=(f'Everyone has been active in the last '
                             f'{PREWAR_OFFLINE_HOURS}h.'),
                color=EMBED_COLOR_SUCCESS))
            return True

        quiet.sort(key=lambda x: -x[2])
        lines, used = [], 0
        for name, tid, hrs in quiet:
            who = self._mention_for(tid) or f'**{name}**'
            line = f'- {who} ({name}) {hrs:.0f}h'
            if used + len(line) + 1 > 3400:
                lines.append(f'- ...and {len(quiet) - len(lines)} more')
                break
            lines.append(line)
            used += len(line) + 1

        await channel.send(embed=create_embed(
            title=f'⚠️ Offline before the war · {when_label}',
            description=(f'**{len(quiet)} of {len(roster)}** members have not '
                         f'acted in {PREWAR_OFFLINE_HOURS}h.\n\n'
                         + '\n'.join(lines)
                         + '\n\n-# The rule is removal, not a strike, so these '
                           'do not go to the ledger. Check for a notice first: '
                           'someone can be offline and still have said so.'),
            color=EMBED_COLOR_WARNING))
        return True

    async def post_poll_chasers(self, war_id, when_label):
        """
        Mention everyone who never answered the availability poll.

        Answering is what exempts a member from the absence strike, so a
        silent member this close to the start is either about to break the
        rule or simply forgot - and the second one is worth a ping. Posted in
        the availability channel so the mention sits next to the poll they
        need to click.

        Returns True if something was posted.
        """
        from utils.torn_api import fetch_faction_members
        if not self.bot or not AVAILABILITY_CHANNEL_ID:
            return False
        channel = self.bot.get_channel(AVAILABILITY_CHANNEL_ID)
        if not channel:
            logger.warning('Poll chase: availability channel not found')
            return False

        roster = await fetch_faction_members()
        if not roster:
            logger.warning('Poll chase: roster unreadable, skipping')
            return False

        answered = self._availability_responders(war_id)
        if not answered:
            # No poll, or nobody has answered at all. Chasing the whole faction
            # on a poll that may not exist would be worse than saying nothing.
            logger.info('Poll chase: no responders recorded, skipping')
            return False

        silent = [(m.get('name', f'ID:{t}'), str(t))
                  for t, m in roster.items() if str(t) not in answered]
        if not silent:
            await channel.send(embed=create_embed(
                title=f'\u2705 Availability \u00b7 {when_label}',
                description='Everyone has answered the poll.',
                color=EMBED_COLOR_SUCCESS))
            return True

        silent.sort(key=lambda x: x[0].lower())
        lines, used = [], 0
        for name, tid in silent:
            who = self._mention_for(tid) or f'**{name}**'
            line = f'- {who} ({name})' if who.startswith('<@') else f'- {who}'
            if used + len(line) + 1 > 3400:
                lines.append(f'- ...and {len(silent) - len(lines)} more')
                break
            lines.append(line)
            used += len(line) + 1

        await channel.send(embed=create_embed(
            title=f'\u23f0 Still no availability answer \u00b7 {when_label}',
            description=(f'**{len(silent)} of {len(roster)}** have not answered '
                         f'the poll above.\n\n' + '\n'.join(lines)
                         + '\n\n-# Answering is what exempts you from the '
                           'absence strike. Say so even if the answer is '
                           '"barely on". The poll closes an hour before the '
                           'war starts.'),
            color=EMBED_COLOR_WARNING))
        return True

    async def check_revive_status(self):
        """
        Post the revive check once per checkpoint before a war starts.

        Called every minute from the war loop. get_upcoming_war() reuses the
        cached rankedwars fetch, so a tick with no war coming costs nothing -
        and the expensive part (one profile call per member) only runs on the
        tick that actually posts.
        """
        if not self.bot:
            return
        upcoming = await self.get_upcoming_war()
        if not upcoming:
            return

        now = get_current_timestamp()
        start = int(upcoming.get('start') or 0)
        remaining = start - now
        if remaining <= 0:
            return  # already started; telling people to prepare is too late

        war_id = str(upcoming['war_id'])
        done = set(self.revive_checks.get(war_id, []))
        passed = [c for c in REVIVE_CHECK_CHECKPOINTS
                  if remaining <= c and c not in done]
        if not passed:
            return

        channel = self.bot.get_channel(WAR_CHANNEL_ID)
        if not channel:
            logger.warning('Revive check: war channel not found')
            return

        hours = min(passed) // 3600
        label = f'{hours}h before the war'
        try:
            sent = await self.post_revive_check(channel, label)
        except Exception as e:
            logger.error(f'Revive check failed for war {war_id}: {e}')
            return
        # Same checkpoint, same roster-sized cost, different question: who has
        # gone quiet. Failing this must not hold back the revive result, which
        # already went out.
        try:
            await self.post_prewar_offline(channel, label)
        except Exception as e:
            logger.error(f'Pre-war offline check failed for war {war_id}: {e}')
        # Same pass again: who still has not said whether they will be around.
        try:
            await self.post_poll_chasers(war_id, label)
        except Exception as e:
            logger.error(f'Poll chase failed for war {war_id}: {e}')
        if not sent:
            return  # unreadable roster: leave the checkpoint open to retry

        # Mark EVERY checkpoint already passed, not just the one posted, or a
        # restart that skipped past both would post twice a minute apart.
        self.revive_checks[war_id] = sorted(done | set(passed))
        await self.save_revive_checks()
        logger.info(f'Posted revive check for war {war_id} at {label}')

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
                # None here means the log was unreadable, which is NOT the same
                # as an empty one - this is the one place the difference is
                # permanent, since whatever is archived now is what the faction
                # keeps.
                attack_stats = None
                war_start_ts = final_war_data.get('start', 0)
                enemy_fid = final_war_data.get('enemy_faction_id')
                # war_history keeps `start` but not `end`; the report carries an
                # exact one. Falling back to now is close enough at war end, and
                # far better than leaving the window open forever.
                war_end_ts = (report.get('end') if report else None) or get_current_timestamp()
                if war_start_ts and enemy_fid:
                    try:
                        attack_stats = await self._get_war_attack_stats(
                            enemy_fid, war_start_ts, war_end_ts)
                    except Exception as e:
                        logger.error(f'War end: failed to compute attack stats: {e}')
                        attack_stats = None
                if attack_stats is None:
                    logger.error(
                        f'War end: attacks log unreadable for war {self.current_war_id} - '
                        f'hits taken will be archived as unknown rather than zero')

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

                # ---- Board and Xanax, posted automatically ----
                # Management had to remember to run /war board and /war xan
                # after every war and nothing prompted them. The board is
                # everyone's business so it pings management; the Xanax
                # reimbursement is the leader's job alone, so it pings them.
                # Each is wrapped separately: a failure in one must not cost
                # the other, or the strike candidates that follow.
                if management_channel:
                    mgmt_war = {'war_id': self.current_war_id,
                                'data': {'factions': mgmt_factions}}
                    try:
                        board = await self.format_warboard_embed(mgmt_war)
                        board = [b for b in (board if isinstance(board, list)
                                             else [board]) if b]
                        if board:
                            ping = (f'<@&{MANAGEMENT_PING_ROLE_ID}> '
                                    if MANAGEMENT_PING_ROLE_ID else '')
                            await management_channel.send(
                                f'{ping}\U0001f4cb **War board \u00b7 war '
                                f'#{self.current_war_id}** \u00b7 posted '
                                f'automatically at war end')
                            for b in board:
                                await management_channel.send(embed=b)
                            insight = self._war_insight_text(
                                report_factions, attack_stats,
                                self._availability_responders(self.current_war_id))
                            if insight:
                                await management_channel.send(embed=create_embed(
                                    title='\U0001f50e What the board says',
                                    description=insight[:4000],
                                    color=EMBED_COLOR_INFO))
                            logger.info('Auto-posted war board to management')
                    except Exception as e:
                        logger.error(f'Auto board post failed: {e}')

                    try:
                        xan = await self.format_xan_embed(mgmt_war)
                        xan = [x for x in (xan if isinstance(xan, list)
                                           else [xan]) if x]
                        if xan:
                            who = (f'<@{LEADER_USER_ID}>' if LEADER_USER_ID
                                   else (f'<@&{MANAGEMENT_PING_ROLE_ID}>'
                                         if MANAGEMENT_PING_ROLE_ID else ''))
                            await management_channel.send(
                                f'{who} \U0001f48a **Xanax reimbursement \u00b7 '
                                f'war #{self.current_war_id}** \u00b7 posted '
                                f'automatically at war end')
                            for x in xan:
                                await management_channel.send(embed=x)
                            logger.info('Auto-posted xan report to management')
                    except Exception as e:
                        logger.error(f'Auto xan post failed: {e}')

                try:
                    await self.post_strike_candidates(
                        self.current_war_id, attack_stats, report_factions)
                except Exception as e:
                    logger.error(f'Strike candidates failed: {e}')

                # Friendly hospitalisations, from the same single pass above
                friendly_hosps = (attack_stats or {}).get('_friendly_hosps', [])
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
                        attack_stats=attack_stats,
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
            # Last war's absences say nothing about this one.
            self.war_offline = {}
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
