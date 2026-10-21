"""
Inactivity Tracking Module
Tracks ALL faction members via Torn API and sends alerts for inactive members

NEW BEHAVIOR:
- Checks ALL faction members from Torn API (not just registered Discord users)
- For registered members: Shows Discord @mention + Torn info
- For unregistered members: Shows Torn name only
- ALWAYS posts to #inactivity-alerts channel
- Registration is OPTIONAL (only needed for Discord mentions)
"""

import time
import discord
from datetime import datetime
from utils.helpers import load_json, save_json, create_embed, format_timestamp, get_days_since, get_current_timestamp
from utils.torn_api import fetch_faction_members, fetch_faction_crimes
from config import (
    VERIFIED_ROLE_NAME,
    INACTIVITY_ALERTS_CHANNEL_ID,
    STRIKES_CHANNEL_ID,
    INACTIVITY_WARNING_DAYS,
    INACTIVITY_CRITICAL_DAYS,
    OC_WARNING_DAYS,
    OC_CRITICAL_DAYS,
    OC_NEW_MEMBER_GRACE_DAYS,
    TRIAL_DAYS,
    TRIAL_TARGET_LEVEL,
    TRIAL_WARNING_DAYS,
    LEFT_FACTION_ALERT_DAYS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    EMBED_COLOR_SUCCESS,
    GUILD_ID,
    MANAGEMENT_PING_ROLE_ID
)


import logging

logger = logging.getLogger(__name__)

# Backoff schedule for the daily check's Torn fetch. Covers ~6 minutes of
# transient API trouble (blip, brief maintenance) before giving up until
# tomorrow - see check_and_alert_inactive for why this loop alone retries.
DAILY_FETCH_RETRY_DELAYS = [60, 120, 180]


class InactivityTracker:
    """Tracks member activity on Torn and sends inactivity alerts"""

    def __init__(self, bot):
        self.bot = bot
        self.user_mapping = {}  # {discord_id: torn_id}
        self.mapping_file = 'data/discord_to_torn.json'
        self.oc_data_file = 'data/oc_participation.json'
        self.last_oc_completion = {}  # {torn_id_str: float timestamp} · accumulated across runs
        self.left_faction_file = 'data/left_faction.json'
        self.trial_file = 'data/trial_tracking.json'
        # {torn_id: {'warned': bool, 'reviewed': bool}} - so each member is
        # told once, not every day until their trial ends.
        self.trial_state = {}
        self.left_faction = {}  # {torn_id_str: {first_gone, torn_name, discord_id, access_revoked}}
        # Set by bot.py once the verification system exists; used to find the
        # verified role when revoking access after the grace period.
        self.verification_system = None

    async def load_user_mapping(self):
        """Load Discord to Torn ID mapping from JSON file"""
        self.user_mapping = load_json(self.mapping_file, default={})
        logger.info(f'Loaded Torn ID mappings for {len(self.user_mapping)} Discord users')

    async def load_oc_data(self):
        """Load accumulated OC participation data from JSON.
        If the file is from an old format (no version key), clears stale v1 data
        and starts fresh so v2 API data can accumulate cleanly.
        """
        raw = load_json(self.oc_data_file, default={})
        if raw.get('version') != 2:
            # Old format or first run - clear stale data and start fresh
            self.last_oc_completion = {}
            logger.info('OC data reset: clearing old data, will accumulate fresh v2 OC history')
            return
        data = raw.get('data', {})
        self.last_oc_completion = {str(k): float(v) for k, v in data.items() if v}
        logger.info(f'Loaded OC participation data for {len(self.last_oc_completion)} members')

    async def save_oc_data(self):
        """Save accumulated OC participation data to JSON"""
        save_json(self.oc_data_file, {'version': 2, 'data': self.last_oc_completion})

    async def load_left_faction_data(self):
        """Load left-faction tracking data from JSON."""
        raw = load_json(self.left_faction_file, default={})
        self.left_faction = {str(k): v for k, v in raw.items()}
        logger.info(f'Loaded left-faction data for {len(self.left_faction)} members')

    async def save_left_faction_data(self):
        """Save left-faction tracking data to JSON."""
        save_json(self.left_faction_file, self.left_faction)

    async def save_user_mapping(self):
        """Save Discord to Torn ID mapping to JSON file"""
        save_json(self.mapping_file, self.user_mapping)

    async def register_user(self, discord_id, torn_id):
        """
        Register a Discord user with their Torn ID

        Args:
            discord_id: Discord user ID (int or str)
            torn_id: Torn user ID (int or str)

        Returns:
            True if successful, False if failed
        """
        # Verify the Torn ID exists in faction
        members = await fetch_faction_members()

        if members is None:
            return False, "Could not connect to Torn API"

        if str(torn_id) not in members:
            return False, f"Torn ID {torn_id} not found in faction"

        # Store the mapping
        self.user_mapping[str(discord_id)] = str(torn_id)
        await self.save_user_mapping()

        torn_name = members[str(torn_id)].get('name', 'Unknown')
        return True, f"Linked to Torn user: {torn_name} [{torn_id}]"

    async def unregister_user(self, discord_id):
        """
        Unregister a Discord user (remove their Torn ID link)

        Args:
            discord_id: Discord user ID

        Returns:
            True if successful, False if user wasn't registered
        """
        discord_id_str = str(discord_id)

        if discord_id_str not in self.user_mapping:
            return False

        del self.user_mapping[discord_id_str]
        await self.save_user_mapping()
        return True

    def get_torn_id(self, discord_id):
        """
        Get Torn ID for a Discord user

        Args:
            discord_id: Discord user ID

        Returns:
            Torn ID (str) or None if not registered
        """
        return self.user_mapping.get(str(discord_id))

    def get_discord_id(self, torn_id):
        """
        Get Discord ID for a Torn user

        Args:
            torn_id: Torn user ID

        Returns:
            Discord ID (str) or None if not registered
        """
        for discord_id, tid in self.user_mapping.items():
            if tid == str(torn_id):
                return discord_id
        return None

    def _get_oc_status(self, torn_members, crimes):
        """
        Determine OC participation status for each member.

        Updates self.last_oc_completion with any new completions found in crimes.
        Only flags members who appear in accumulated OC data (skips unknowns to
        avoid false positives on first run or for members outside API history window).

        Returns:
            (oc_warning, oc_critical, data_updated) - lists and a bool flag.
        """
        # crimes is None when the Torn crimes endpoint was unreachable. Treating
        # that as "no crimes exist" would leave active_in_oc empty, stripping
        # in-progress protection from every member currently mid-OC and flagging
        # them against a stale last-completion timestamp. Report nothing instead
        # of reporting garbage.
        if crimes is None:
            logger.error('OC participation check SKIPPED: crimes data unavailable this run')
            return [], [], False, False

        now = time.time()
        data_updated = False

        # The faction rule is "not in an OC while eligible AND slots are open".
        # With every slot taken there is nothing to join, and the OC guide tells
        # members to ask management to create one - so flagging them for it
        # blames them for management's queue. Count what is actually joinable
        # before holding anyone to the rule.
        #
        # -1 means the source could not say (the v1 fallback has no slot model),
        # which is NOT the same as zero: an unknown must not silence the check.
        open_slots = 0
        slots_known = False
        for crime in (crimes if isinstance(crimes, list) else []):
            if not isinstance(crime, dict):
                continue
            n = crime.get('open_slots', -1)
            if n is None or n < 0:
                continue
            slots_known = True
            open_slots += n
        no_slots_to_join = slots_known and open_slots == 0

        # Statuses where a crime can no longer be participated in. Only
        # Recruiting and Planning are live.
        OC_DEAD_STATUSES = ('Expired', 'Successful', 'Failure')

        # Build active_in_oc and update accumulated last_oc_completion
        # crimes is a normalized list from fetch_faction_crimes()
        active_in_oc = set()

        for crime in (crimes if isinstance(crimes, list) else []):
            if not isinstance(crime, dict):
                continue
            completed = crime.get('completed', False)
            completion_ts = crime.get('completion_ts', 0)
            # An Expired crime timed out without ever running, so anyone still
            # sitting in it is NOT participating in anything - but Torn reports
            # it with completed=False, the same shape as a live crime. Without
            # this, an OC that expires with people in it would mark exactly
            # those people as busy and skip them from the check. Same trap as
            # Expired slots counting as joinable.
            dead = crime.get('status') in OC_DEAD_STATUSES
            for tid in crime.get('participants', []):
                tid = str(tid)
                if not completed and not dead:
                    active_in_oc.add(tid)
                elif completed and completion_ts:
                    tc = float(completion_ts)
                    if tid not in self.last_oc_completion or tc > self.last_oc_completion[tid]:
                        self.last_oc_completion[tid] = tc
                        data_updated = True

        oc_warning = []
        oc_critical = []

        # NOTE: this used to return early when every slot was filled, on the
        # grounds that flagging members for management's queue was unfair. The
        # faction's rule is the other way round: if there is nothing joinable
        # it is the member's job to tell management so a crime gets created,
        # not a reason to sit out. So the check still runs, and the shortage is
        # reported as context rather than used as a blanket excuse.
        if no_slots_to_join:
            logger.info('OC participation check: no open slots this run - '
                        'flagging anyway, members are expected to ask '
                        'management for a crime')

        for torn_id, member in torn_members.items():
            tid = str(torn_id)
            days_in_faction = member.get('days_in_faction', 0)

            if days_in_faction < OC_NEW_MEMBER_GRACE_DAYS:
                continue

            if tid in active_in_oc:
                continue

            last_oc_ts = self.last_oc_completion.get(tid)
            never_joined = last_oc_ts is None
            if never_joined:
                # No OC on record at all. This used to `continue`, on the
                # grounds that history builds up over time - but it never
                # expires, so a member who has genuinely never joined one was
                # invisible to this check forever. Measured 15/10/2026: two
                # members, one of them 51 days in the faction.
                #
                # The clock falls back to how long they have been in the
                # faction, less the grace days, because a brand new member
                # cannot do anything in the faction yet. Counting starts the
                # day the grace runs out, not the day they joined.
                days_since = max(0.0, days_in_faction - OC_NEW_MEMBER_GRACE_DAYS)
            else:
                days_since = (now - last_oc_ts) / 86400

            if days_since < OC_WARNING_DAYS:
                continue

            member_data = {
                'torn_name': member.get('name', f'ID:{tid}'),
                'torn_id': tid,
                'torn_level': member.get('level', '?'),
                'days': round(days_since, 1),
                'last_oc_ts': last_oc_ts,
                'never_joined': never_joined,
            }

            if days_since >= OC_CRITICAL_DAYS:
                oc_critical.append(member_data)
            else:
                oc_warning.append(member_data)

        return oc_warning, oc_critical, data_updated, no_slots_to_join

    async def _revoke_left_faction_access(self, guild, left_alerts):
        """
        Strip the verified role from members who have been out of the faction
        past the grace period, so they fall back to whatever @everyone can see
        (the welcome channel) instead of keeping full access indefinitely.

        Only ever called with a successfully-fetched roster - check_and_alert_inactive
        bails out before this if the member fetch failed - so a Torn outage can
        never be mistaken for "everyone left the faction". The grace period is the
        second guard: it takes several consecutive daily runs to reach.

        Each member is revoked once; the flag persists so a rejoin-then-leave
        cycle re-arms it (the entry is deleted on rejoin).

        Returns:
            list of torn_names actually revoked this run
        """
        if not guild:
            return []

        role = None
        vs = getattr(self, 'verification_system', None)
        role_id = vs.config.get('verified_role_id') if vs and vs.config else None
        if role_id:
            role = guild.get_role(role_id)
        if role is None:
            # Fall back to matching by configured name
            role = discord.utils.find(
                lambda r: r.name.lower() == VERIFIED_ROLE_NAME.lower(), guild.roles
            )
        if role is None:
            logger.warning('Left-faction revoke: no verified role found · skipping')
            return []

        revoked = []
        for data in left_alerts:
            tid = str(data['torn_id'])
            entry = self.left_faction.get(tid)
            if not entry or entry.get('access_revoked'):
                continue

            member = guild.get_member(int(data['discord_id']))
            if not member:
                # Gone from Discord entirely - nothing to revoke
                entry['access_revoked'] = True
                continue

            if role not in member.roles:
                entry['access_revoked'] = True
                continue

            try:
                await member.remove_roles(
                    role,
                    reason=f'Left the faction {data["days_gone"]:.0f}d ago '
                           f'(grace period {LEFT_FACTION_ALERT_DAYS}d)'
                )
                entry['access_revoked'] = True
                revoked.append(data['torn_name'])
                logger.info(f'Left-faction: revoked {role.name} from {data["torn_name"]} '
                            f'({data["days_gone"]:.0f}d gone)')
            except discord.Forbidden:
                logger.error(f'Left-faction: no permission to remove {role.name} from '
                             f'{data["torn_name"]} · is the bot role above it?')
            except Exception as e:
                logger.error(f'Left-faction: failed revoking role from {data["torn_name"]}: {e}')

        return revoked

    def _get_left_faction_status(self, torn_members, guild_member_ids=None):
        """
        Detect verified Discord members who are no longer in the Torn faction.

        - First time detected as gone: records timestamp
        - Rejoined faction: removes from tracking
        - Gone >= LEFT_FACTION_ALERT_DAYS: added to alert list

        Returns:
            (alerts, data_updated)
            alerts: list of dicts with torn_id, torn_name, discord_id, days_gone
        """
        now = time.time()
        data_updated = False
        alerts = []

        current_torn_ids = set(str(tid) for tid in torn_members.keys())

        # Name lookup from verified_users (so we have names even after they leave)
        verified_users = load_json('data/verified_users.json', default={})
        torn_id_to_name = {}
        for d_id, data in verified_users.items():
            tid = str(data.get('torn_id', ''))
            name = data.get('torn_name', '')
            if tid and name:
                torn_id_to_name[tid] = name

        LEFT_FACTION_MAX_DAYS = 30     # Stop tracking after 30 days (clearly not returning)

        for discord_id, torn_id in self.user_mapping.items():
            tid = str(torn_id)

            if tid in current_torn_ids:
                # Back in faction - stop tracking
                if tid in self.left_faction:
                    del self.left_faction[tid]
                    data_updated = True
            else:
                # Not in faction

                # Stop tracking if they've left the Discord server
                if guild_member_ids is not None and discord_id not in guild_member_ids:
                    if tid in self.left_faction:
                        torn_name = self.left_faction[tid].get('torn_name', tid)
                        del self.left_faction[tid]
                        data_updated = True
                        logger.info(f'Left-faction: Removed {torn_name} · no longer in Discord server')
                        continue

                if tid not in self.left_faction:
                    torn_name = torn_id_to_name.get(tid, f'ID:{tid}')
                    self.left_faction[tid] = {
                        'first_gone': now,
                        'torn_name': torn_name,
                        'discord_id': discord_id,
                    }
                    data_updated = True

                entry = self.left_faction[tid]
                days_gone = (now - entry['first_gone']) / 86400

                # Auto-remove after 30 days - they're clearly not coming back
                if days_gone >= LEFT_FACTION_MAX_DAYS:
                    del self.left_faction[tid]
                    data_updated = True
                    logger.info(f'Left-faction: Auto-removed {entry["torn_name"]} after {days_gone:.0f}d')
                    continue

                if days_gone >= LEFT_FACTION_ALERT_DAYS:
                    alerts.append({
                        'torn_id': tid,
                        'torn_name': entry['torn_name'],
                        'discord_id': entry['discord_id'],
                        'days_gone': round(days_gone, 1),
                    })

        return alerts, data_updated

    async def load_trial_data(self):
        """Who has already been warned about, or reviewed for, their trial."""
        data = load_json(self.trial_file, default={})
        self.trial_state = data.get('members', {}) if isinstance(data, dict) else {}
        logger.info(f'Loaded trial tracking for {len(self.trial_state)} members')

    async def save_trial_data(self):
        save_json(self.trial_file, {'members': self.trial_state})

    def _get_trial_status(self, torn_members):
        """
        New members against the two-week trial.

        The guides make two promises that are really one deadline: "reach
        level 15 within 2 weeks" and "your first 2 weeks are a trial, at the
        end management reviews your progress". So this fires twice per member
        and never again: a warning with days still left to act, then the review
        when the trial is up.

        Returns (warning, failed, passed, updated).

        Every state is latched in self.trial_state, because this runs daily -
        without it a member who missed the deadline would be re-accused every
        morning for as long as they stayed.
        """
        warning, failed, passed = [], [], []
        updated = False
        seen = set()

        for torn_id, member in (torn_members or {}).items():
            tid = str(torn_id)
            days = member.get('days_in_faction', 0) or 0
            level = member.get('level', 0) or 0

            # Well past the review and already dealt with. Skipping before
            # `seen` also prunes them below, so this file cannot grow forever.
            if days > TRIAL_DAYS + 7:
                continue
            seen.add(tid)

            st = self.trial_state.get(tid)
            if st is None:
                st = {'warned': False, 'reviewed': False}
                self.trial_state[tid] = st
                updated = True

            # days_in_faction resets when someone leaves and rejoins, so an
            # early reading means a NEW trial and the old flags must clear or
            # the second trial is silently never checked.
            if days < TRIAL_WARNING_DAYS and (st.get('warned') or st.get('reviewed')):
                st['warned'] = False
                st['reviewed'] = False
                updated = True

            data = {
                'torn_name': member.get('name', f'ID:{tid}'),
                'torn_id': tid,
                'torn_level': level,
                'days': days,
                'days_left': max(0, TRIAL_DAYS - days),
            }

            if days >= TRIAL_DAYS:
                if st.get('reviewed'):
                    continue
                st['reviewed'] = True
                updated = True
                if level < TRIAL_TARGET_LEVEL:
                    failed.append(data)
                else:
                    passed.append(data)
            elif days >= TRIAL_WARNING_DAYS and level < TRIAL_TARGET_LEVEL:
                if st.get('warned'):
                    continue
                st['warned'] = True
                updated = True
                warning.append(data)

        for tid in [t for t in self.trial_state if t not in seen]:
            del self.trial_state[tid]
            updated = True

        return warning, failed, passed, updated

    async def post_trial_alerts(self, channel, warning, failed, passed):
        """
        Post the trial results. The warning and the pass notice are routine and
        stay with the daily check; a missed deadline is strike-or-remove under
        the rules, so it goes to the strikes channel like the other one.
        """
        strikes = (self.bot.get_channel(STRIKES_CHANNEL_ID) or channel) \
            if self.bot else channel

        def line(d):
            return (f'**{d["torn_name"]}** [Lvl {d["torn_level"]}] '
                    f'[profile](<https://www.torn.com/profiles.php?XID={d["torn_id"]}>)')

        if warning:
            body = '\n'.join(
                f'{line(d)}\n  day {d["days"]} of {TRIAL_DAYS} \u00b7 '
                f'**{d["days_left"]} day(s) left** to reach level {TRIAL_TARGET_LEVEL}'
                for d in sorted(warning, key=lambda x: x['days_left']))
            await channel.send(embed=create_embed(
                title='\u23f3 Trial deadline approaching',
                description=(f'**{len(warning)} new member(s)** are not level '
                             f'{TRIAL_TARGET_LEVEL} yet with their trial running out.'
                             f'\n\n{body}\n\n'
                             f'-# Nudge them now. Levelling is fastest with '
                             f'attacks, and the Level 15 guide covers it.'),
                color=EMBED_COLOR_WARNING))

        if failed:
            body = '\n'.join(
                f'{line(d)}\n  {d["days"]} days in faction, still level '
                f'{d["torn_level"]} (needed {TRIAL_TARGET_LEVEL})'
                for d in sorted(failed, key=lambda x: -x['days']))
            sm = getattr(self, 'strike_manager', None)
            if sm:
                for d in failed:
                    await sm.post_candidate(
                        d['torn_id'], d['torn_name'], 'trial',
                        f"{d['days']} days in the faction and still level "
                        f"{d['torn_level']}, needed {TRIAL_TARGET_LEVEL} within "
                        f"{TRIAL_DAYS} days. The rules say strike OR removal "
                        f"depending on progress.",
                        context='', channel=strikes)
            else:
                await strikes.send(embed=create_embed(
                    title='\U0001f534 Trial failed \u00b7 strike or remove',
                    description=(f'**{len(failed)} member(s)** did not reach level '
                             f'{TRIAL_TARGET_LEVEL} within {TRIAL_DAYS} days.'
                             f'\n\n{body}\n\n'
                             f'-# The rules say strike or removal "depending on '
                             f'progress", so this is a judgement call, not an '
                             f'automatic strike. Posted once.'),
                    color=EMBED_COLOR_CRITICAL))

        if passed:
            await channel.send(embed=create_embed(
                title='\u2705 Trial complete \u00b7 review due',
                description=(f'**{len(passed)} member(s)** finished their '
                             f'{TRIAL_DAYS}-day trial at level '
                             f'{TRIAL_TARGET_LEVEL}+:\n\n'
                             + '\n'.join(f'{line(d)}\n  level {d["torn_level"]} '
                                         f'after {d["days"]} days'
                                         for d in passed)
                             + '\n\n-# Level is only the hard requirement. '
                               'Management still reviews the rest.'),
                color=EMBED_COLOR_SUCCESS))

    async def check_and_alert_inactive(self):
        """
        Check for inactive members on Torn and send alerts
        NOW CHECKS ALL FACTION MEMBERS (registered or not)
        Called by the daily background task
        """
        logger.info(f'Running daily Torn inactivity check... ({datetime.now()})')

        # Get the alerts channel - ALWAYS use inactivity-alerts channel
        channel = self.bot.get_channel(INACTIVITY_ALERTS_CHANNEL_ID)

        if not channel:
            logger.error(f'ERROR: Could not find inactivity alerts channel (ID: {INACTIVITY_ALERTS_CHANNEL_ID})')
            logger.info('Please check INACTIVITY_ALERTS_CHANNEL_ID in config.py')
            return

        # Get the guild
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            logger.warning(f'Warning: Could not find guild (ID: {GUILD_ID})')
            # Still continue - we can check faction members without Discord

        # Fetch Torn faction member data and crimes in parallel
        import asyncio
        torn_members, crimes = await asyncio.gather(
            fetch_faction_members(),
            fetch_faction_crimes()
        )

        # This is the only 24h loop in the bot: every other background check
        # re-runs within 1-15 minutes, so a momentary Torn blip costs them one
        # tick, while a blip here costs a full day of accountability data.
        # Torn API failures aren't cached (only successes are), so an in-place
        # retry genuinely re-hits the API rather than replaying the failure.
        # Retry if either payload is missing. A failed crimes fetch matters as
        # much as a failed member fetch: without it the OC section can't tell
        # who is mid-crime, so it gets skipped entirely below.
        if not torn_members or crimes is None:
            for delay in DAILY_FETCH_RETRY_DELAYS:
                logger.warning(f'Inactivity check: Torn fetch failed '
                               f'(members={"ok" if torn_members else "FAILED"}, '
                               f'crimes={"ok" if crimes is not None else "FAILED"}), '
                               f'retrying in {delay}s')
                await asyncio.sleep(delay)
                retry_members, retry_crimes = await asyncio.gather(
                    fetch_faction_members(),
                    fetch_faction_crimes()
                )
                # Keep whichever payload already succeeded. A retry fired for a
                # failed crimes fetch must never throw away good member data by
                # overwriting it with a fresh failure - that would turn a
                # partial success into a skipped report.
                if retry_members:
                    torn_members = retry_members
                if retry_crimes is not None:
                    crimes = retry_crimes
                if torn_members and crimes is not None:
                    logger.info('Inactivity check: Torn fetch recovered on retry')
                    break

        if not torn_members:
            total_wait = sum(DAILY_FETCH_RETRY_DELAYS) // 60
            logger.error('Error: Could not fetch Torn faction data (all retries exhausted)')
            error_embed = create_embed(
                title='Inactivity Check Failed',
                description=f'Could not connect to Torn API to check member activity, after '
                            f'{len(DAILY_FETCH_RETRY_DELAYS)} retries over ~{total_wait} minutes. '
                            f'Will retry tomorrow.',
                color=EMBED_COLOR_CRITICAL
            )
            await channel.send(embed=error_embed)
            return

        # Deliberately NOT coerced to [] - _get_oc_status needs to see None to
        # tell "crimes endpoint down" apart from "no crimes right now".
        if crimes is None:
            logger.error('Inactivity check: proceeding without OC data (crimes endpoint '
                         'unreachable after retries) · OC section will be skipped')

        # Track inactive members
        # Both thresholds are env-driven, so no number is written here: live
        # critical is 7 days to match the rule in #faction-edicts, and a
        # hardcoded comment would quietly go stale the next time it moves.
        warning_members = []   # INACTIVITY_WARNING_DAYS+ inactive
        critical_members = []  # INACTIVITY_CRITICAL_DAYS+ inactive, strike-able

        # Check ALL faction members from Torn API
        logger.info(f'Checking {len(torn_members)} faction members...')
        for torn_id, torn_member in torn_members.items():
            # Get last action timestamp from Torn
            last_action_data = torn_member.get('last_action', {})
            last_action_timestamp = last_action_data.get('timestamp')

            if not last_action_timestamp:
                # No last action data, skip this member
                continue

            # Calculate days inactive
            days_inactive = get_days_since(last_action_timestamp)

            # Skip if member is active (less than 3 days)
            if days_inactive < INACTIVITY_WARNING_DAYS:
                continue

            # Get member info from Torn
            torn_name = torn_member.get('name', 'Unknown')
            torn_level = torn_member.get('level', '?')

            # Check if this Torn user is registered in Discord
            discord_id = self.get_discord_id(torn_id)
            discord_member = None

            if discord_id and guild:
                try:
                    discord_member = guild.get_member(int(discord_id))
                    if not discord_member:
                        discord_member = await guild.fetch_member(int(discord_id))
                except (discord.NotFound, discord.HTTPException, ValueError):
                    pass  # User not in Discord server or invalid ID

            # Build member data
            member_data = {
                'discord_member': discord_member,  # Will be None if not registered
                'torn_name': torn_name,
                'torn_id': torn_id,
                'torn_level': torn_level,
                'days': days_inactive,
                'last_seen': last_action_timestamp
            }

            # Check thresholds
            if days_inactive >= INACTIVITY_CRITICAL_DAYS:
                critical_members.append(member_data)
            elif days_inactive >= INACTIVITY_WARNING_DAYS:
                warning_members.append(member_data)

        # OC participation check
        oc_warning, oc_critical, oc_updated, oc_no_slots = self._get_oc_status(
            torn_members, crimes)
        if oc_updated:
            await self.save_oc_data()
        logger.info(f'OC check: {len(oc_warning)} warning, {len(oc_critical)} critical out of {len(torn_members)} total')

        # Left-faction check (verified Discord members no longer in Torn faction)
        guild_member_ids = {str(m.id) for m in guild.members} if guild else None
        left_alerts, left_updated = self._get_left_faction_status(torn_members, guild_member_ids)
        # Revoke access for anyone past the grace period, then persist - the
        # revoke marks entries, so save after it rather than before.
        revoked = await self._revoke_left_faction_access(guild, left_alerts)
        if left_updated or revoked:
            await self.save_left_faction_data()
        logger.info(f'Left-faction check: {len(left_alerts)} alert(s), {len(revoked)} access revoked, '
                    f'tracking {len(self.left_faction)} members')

        # Always post daily summary
        total_inactive = len(warning_members) + len(critical_members)
        total_checked = len(torn_members)

        # New-member trial. Uses the roster already fetched above, so it adds
        # no API calls at all.
        trial_warn, trial_failed, trial_passed, trial_updated = \
            self._get_trial_status(torn_members)
        if trial_updated:
            await self.save_trial_data()
        logger.info(f'Trial check: {len(trial_warn)} approaching, '
                    f'{len(trial_failed)} failed, {len(trial_passed)} passed')

        logger.info(f'Inactivity check: {total_inactive} inactive out of {total_checked} total')
        await self._send_inactivity_alert(channel, warning_members, critical_members, total_checked, oc_warning, oc_critical, left_alerts, oc_no_slots)
        try:
            await self.post_trial_alerts(channel, trial_warn, trial_failed, trial_passed)
        except Exception as e:
            logger.error(f'Trial alerts failed: {e}')

    async def test_run(self):
        """
        Run inactivity + OC checks silently - no channel posts.
        Returns a summary dict for ephemeral display via /test command.
        """
        import asyncio
        torn_members, crimes = await asyncio.gather(
            fetch_faction_members(),
            fetch_faction_crimes()
        )
        if not torn_members:
            return None
        # crimes may be None (endpoint unreachable); _get_oc_status skips the
        # OC section in that case rather than flagging everyone mid-crime.

        warning_members = []
        critical_members = []

        for torn_id, torn_member in torn_members.items():
            last_action_data = torn_member.get('last_action', {})
            last_action_timestamp = last_action_data.get('timestamp')
            if not last_action_timestamp:
                continue
            days_inactive = get_days_since(last_action_timestamp)
            if days_inactive < INACTIVITY_WARNING_DAYS:
                continue
            member_data = {
                'torn_name': torn_member.get('name', 'Unknown'),
                'torn_id': torn_id,
                'torn_level': torn_member.get('level', '?'),
                'days': days_inactive,
            }
            if days_inactive >= INACTIVITY_CRITICAL_DAYS:
                critical_members.append(member_data)
            else:
                warning_members.append(member_data)

        oc_warning, oc_critical, oc_updated, _ = self._get_oc_status(torn_members, crimes)
        if oc_updated:
            await self.save_oc_data()

        guild = self.bot.get_guild(GUILD_ID)
        guild_member_ids = {str(m.id) for m in guild.members} if guild else None
        left_alerts, left_updated = self._get_left_faction_status(torn_members, guild_member_ids)
        if left_updated:
            await self.save_left_faction_data()

        return {
            'total': len(torn_members),
            'inactivity_critical': critical_members,
            'inactivity_warning': warning_members,
            'oc_critical': oc_critical,
            'oc_warning': oc_warning,
            'left_faction': left_alerts,
        }

    async def _send_handled(self, channel, embed):
        """
        Post an alert that management ticks off when they have dealt with it.

        The bot deliberately does NOT add the tick itself. It used to, as a
        one-click affordance, and that was wrong: the count showed as 1 before
        anybody had touched it, so every alert looked already handled and a
        real tick made it 2. A reaction only means something if every one of
        them came from a person.
        """
        try:
            return await channel.send(embed=embed)
        except Exception as e:
            logger.error(f'Alert send failed: {e}')
            return None

    async def _send_inactivity_alert(self, channel, warning_members, critical_members, total_faction_members, oc_warning=None, oc_critical=None, left_faction=None, oc_no_slots=False):
        """
        Send inactivity alert embed to the channel
        ALWAYS posts to inactivity-alerts channel

        Args:
            channel: Discord channel to send to (always inactivity-alerts)
            warning_members: List of members inactive for 3+ days
            critical_members: List of members inactive for 5+ days
            total_faction_members: Total number of faction members checked
            oc_warning: List of members with no OC for 2+ days
            oc_critical: List of members with no OC for 4+ days
            left_faction: List of verified Discord members gone from faction 7+ days
        """
        if oc_warning is None:
            oc_warning = []
        if oc_critical is None:
            oc_critical = []
        if left_faction is None:
            left_faction = []

        has_inactivity = bool(warning_members or critical_members)
        has_oc_issues = bool(oc_warning or oc_critical)
        has_left = bool(left_faction)

        # Only the CRITICAL tier is strike territory, so only it goes to the
        # strikes channel. A 2-day nudge is not a strike and belongs with the
        # rest of the daily check.
        oc_channel = (self.bot.get_channel(STRIKES_CHANNEL_ID) or channel) \
            if self.bot else channel

        # The ping has to land where the content lands, and only when there IS
        # content. Two ways this went wrong before:
        #
        # 15/10/2026 - OC criticals were routed to the strikes channel while
        # the ping stayed here, so everyone got pinged into an empty channel.
        #
        # 21/10/2026 - the ping fired on oc_critical being non-empty, but the
        # content is a strike CANDIDATE, and post_candidate deliberately posts
        # nothing for somebody who already has an open candidate or an active
        # strike for the same thing. Two members who were already struck were
        # still OC-critical, so the bot pinged Management and then said
        # nothing at all.
        #
        # So ask the ledger what it would actually raise before pinging about
        # it, rather than assuming every critical member produces a message.
        sm = getattr(self, 'strike_manager', None)
        oc_to_raise = [d for d in oc_critical
                       if not sm or sm.is_open('oc', d['torn_id'], '')]

        here = bool(has_inactivity or has_left or oc_warning)
        there = bool(oc_to_raise)
        if MANAGEMENT_PING_ROLE_ID:
            if here:
                await channel.send(f'<@&{MANAGEMENT_PING_ROLE_ID}>')
            # Only a second ping when it is genuinely a second channel.
            if there and (oc_channel.id != channel.id or not here):
                await oc_channel.send(f'<@&{MANAGEMENT_PING_ROLE_ID}>')

        # If everyone is active on both checks, post a positive summary
        if not warning_members and not critical_members and not oc_warning and not oc_critical and not left_faction:
            active_embed = create_embed(
                title='✅ Daily Inactivity Check',
                description=f'All **{total_faction_members}** faction members are active!\n\nNo inactivity, OC, or left-faction issues found.',
                color=EMBED_COLOR_SUCCESS
            )
            await channel.send(embed=active_embed)
            logger.info(f'Posted daily check - all {total_faction_members} members active')
            return

        # Send critical alerts
        if critical_members:
            lines = []
            for data in critical_members:
                discord_member = data['discord_member']
                torn_name = data['torn_name']
                torn_id = data['torn_id']
                torn_level = data['torn_level']
                days = data['days']
                last_seen = data['last_seen']
                discord_note = f' (Discord: {discord_member.display_name})' if discord_member else ''
                lines.append(
                    f'**{torn_name}** [Lvl {torn_level}]{discord_note}\n'
                    f'  {days} days inactive · Last action: {format_timestamp(last_seen, "R")}'
                )

            # Chunk into multiple embeds if needed (4096 char description limit)
            header = f'**{len(critical_members)} faction member(s)** have been inactive on Torn for {INACTIVITY_CRITICAL_DAYS}+ days:\n-# React \u2705 once you have dealt with it, so nobody chases them twice.\n\n'
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n\n'
            if current:
                chunks.append(current)

            for i, chunk in enumerate(chunks):
                title = '🔴 Critical Inactivity Alert (Torn)' if i == 0 else '🔴 Critical (continued)'
                embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_CRITICAL)
                await self._send_handled(channel, embed)
            logger.info(f'Sent critical alert for {len(critical_members)} members')

        # Send warning alerts
        if warning_members:
            lines = []
            for data in warning_members:
                discord_member = data['discord_member']
                torn_name = data['torn_name']
                torn_id = data['torn_id']
                torn_level = data['torn_level']
                days = data['days']
                last_seen = data['last_seen']
                discord_note = f' (Discord: {discord_member.display_name})' if discord_member else ''
                lines.append(
                    f'**{torn_name}** [Lvl {torn_level}]{discord_note}\n'
                    f'  {days} days inactive · Last action: {format_timestamp(last_seen, "R")}'
                )

            header = f'**{len(warning_members)} faction member(s)** have been inactive on Torn for {INACTIVITY_WARNING_DAYS}+ days:\n-# React \u2705 once you have dealt with it, so nobody chases them twice.\n\n'
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n\n'
            if current:
                chunks.append(current)

            for i, chunk in enumerate(chunks):
                title = '⚠️ Inactivity Warning (Torn)' if i == 0 else '⚠️ Warning (continued)'
                embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_WARNING)
                await self._send_handled(channel, embed)
            logger.info(f'Sent warning alert for {len(warning_members)} members')

        # ── OC Participation alerts ──────────────────────────────────────────

        if oc_critical:
            lines = []
            for data in oc_critical:
                last_ts = data['last_oc_ts']
                if last_ts:
                    last_str = format_timestamp(int(last_ts), 'R')
                else:
                    last_str = 'Never'
                if data.get('never_joined'):
                    detail = (f'  never joined an OC · eligible for '
                              f'{data["days"]:.0f} days')
                else:
                    detail = (f'  {data["days"]:.0f} days without OC · '
                              f'Last OC: {last_str}')
                lines.append(
                    f'**{data["torn_name"]}** [Lvl {data["torn_level"]}] '
                    f'[profile](<https://www.torn.com/profiles.php?XID={data["torn_id"]}>)\n'
                    f'{detail}'
                )

            header = (f'**{len(oc_critical)} member(s)** have not joined an OC for '
                      f'{OC_CRITICAL_DAYS}+ days. They were warned at '
                      f'{OC_WARNING_DAYS} days, so this is strike territory:\n\n')
            if oc_no_slots:
                # Context, not an excuse: the rule is that a member with
                # nothing to join tells management so a crime gets made.
                header += ('-# No slots were open on this run. Members are '
                           'expected to ask management for a crime, not sit '
                           'out.\n\n')
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n\n'
            if current:
                chunks.append(current)

            # Strike territory. Each member becomes its own candidate with
            # Issue/Dismiss buttons rather than a line in a list, because each
            # needs its own decision - and a dismissal has to be remembered or
            # this re-raises every single morning.
            posted = 0
            if sm:
                # oc_to_raise, not oc_critical: the rest already have an open
                # candidate or a live strike, and re-posting them would bury
                # the ones that need a decision.
                for data in oc_to_raise:
                    reason = (f"{data['days']:.0f} days without joining an OC "
                              f"(strike territory at {OC_CRITICAL_DAYS} days). "
                              f"They were nudged at {OC_WARNING_DAYS} days.")
                    if data.get('never_joined'):
                        reason = (f"Never joined an OC, and has been eligible for "
                                  f"{data['days']:.0f} days.")
                    if await sm.post_candidate(data['torn_id'], data['torn_name'],
                                               'oc', reason, context='',
                                               channel=oc_channel):
                        posted += 1
            else:
                for i, chunk in enumerate(chunks):
                    title = '🔴 OC Participation Critical' if i == 0 else '🔴 OC Critical (continued)'
                    embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_CRITICAL)
                    await oc_channel.send(embed=embed)
            logger.info(f'OC critical: {len(oc_critical)} member(s), '
                        f'{len(oc_to_raise)} raisable, {posted} new candidate(s)')

        if oc_warning:
            lines = []
            for data in oc_warning:
                last_ts = data['last_oc_ts']
                if last_ts:
                    last_str = format_timestamp(int(last_ts), 'R')
                else:
                    last_str = 'Never'
                if data.get('never_joined'):
                    detail = (f'  never joined an OC · eligible for '
                              f'{data["days"]:.0f} days')
                else:
                    detail = (f'  {data["days"]:.0f} days without OC · '
                              f'Last OC: {last_str}')
                lines.append(
                    f'**{data["torn_name"]}** [Lvl {data["torn_level"]}] '
                    f'[profile](<https://www.torn.com/profiles.php?XID={data["torn_id"]}>)\n'
                    f'{detail}'
                )

            header = (f'**{len(oc_warning)} member(s)** have not joined an OC for '
                      f'{OC_WARNING_DAYS}+ days. Nudge them now - at '
                      f'{OC_CRITICAL_DAYS} days it becomes a strike.\n'
                      f'-# React ✅ once you have dealt with it, so nobody '
                      f'chases them twice.\n\n')
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n\n'
            if current:
                chunks.append(current)

            # A nudge, not a strike - stays with the rest of the daily check.
            for i, chunk in enumerate(chunks):
                title = '⚠️ OC Participation Warning' if i == 0 else '⚠️ OC Warning (continued)'
                embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_WARNING)
                await self._send_handled(channel, embed)
            logger.info(f'Sent OC warning alert for {len(oc_warning)} members')

        # ── Left-faction alerts ──────────────────────────────────────────────
        if left_faction:
            guild = self.bot.get_guild(GUILD_ID)
            lines = []
            for data in left_faction:
                discord_id = data['discord_id']
                torn_name = data['torn_name']
                days_gone = data['days_gone']
                # Try to mention the Discord member
                discord_member = guild.get_member(int(discord_id)) if guild else None
                mention = discord_member.mention if discord_member else f'(ID:{discord_id})'
                revoked = self.left_faction.get(str(data['torn_id']), {}).get('access_revoked')
                suffix = ' · 🔒 access removed' if revoked else ''
                lines.append(f'**{torn_name}** {mention} · {days_gone:.0f}d since leaving faction{suffix}')

            header = (f'**{len(left_faction)} verified Discord member(s)** have not returned to the '
                      f'faction in {LEFT_FACTION_ALERT_DAYS}+ days. Their verified role is removed, '
                      f'so they keep only the access @everyone has:\n\n')
            chunks = []
            current = header
            for line in lines:
                if len(current) + len(line) + 2 > 4000:
                    chunks.append(current)
                    current = ''
                current += line + '\n'
            if current:
                chunks.append(current)

            for i, chunk in enumerate(chunks):
                title = '👋 Left Faction Alert' if i == 0 else '👋 Left Faction (continued)'
                embed = create_embed(title=title, description=chunk.strip(), color=EMBED_COLOR_WARNING)
                await channel.send(embed=embed)
            logger.info(f'Sent left-faction alert for {len(left_faction)} members')

    def get_all_mappings(self):
        """
        Get all Discord to Torn ID mappings

        Returns:
            Dict of {discord_id: torn_id}
        """
        return self.user_mapping.copy()

    def get_registered_count(self):
        """Get count of registered users"""
        return len(self.user_mapping)
