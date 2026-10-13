"""
Fly Tracking Module
Track enemy faction flights during ranked war and estimate landing times.

The Torn API's `travel` selection (user/{id}?selections=travel) only works
for the key owner's own ID · querying anyone else returns API error 7
("Incorrect ID-entity relation": the selection is private and the ID
doesn't match the key owner), confirmed live against this faction's war.
So there is no way to read an enemy's exact remaining flight time.

Landing times are therefore ESTIMATES: we timestamp the moment a player's
status flips to Traveling (accurate to one poll tick) and add a known
flight duration, defaulting to the airstrip tier (x0.70) until a player's
real tier (standard/airstrip/WLT/business) is learned from a fully observed
leg. Destination/direction come from the status text, which is logged at
WARNING when it can't be parsed so the exact wording can be nailed down
from production logs.

Toggled via /war view:Fly (management only). Alerts post to the war-tracker
channel. Auto-stops when the ranked war ends.

Also warns our own members when an enemy takes off toward the country they
are currently sitting in ("someone is following you") · see _follow_alerts.
"""

import discord
import statistics
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple

from utils.helpers import load_json, save_json, create_embed
from config import (
    TORN_FACTION_ID,
    FFSCOUTER_API_KEY,
    WAR_TRACKER_CHANNEL_ID,
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
)
from utils.torn_api import fetch_any_faction_data, fetch_ffscouter_stats

import logging

logger = logging.getLogger(__name__)

# Player phases on the travel cycle
PHASE_HOME = 'home'            # In Torn (Okay/Hospital/Jail/Federal)
PHASE_OUT = 'out'              # Traveling to <destination>
PHASE_ABROAD = 'abroad'        # In <destination> (incl. abroad hospital)
PHASE_RETURNING = 'returning'  # Returning to Torn from <destination>

# Standard (no-perk) one-way flight times in minutes
STANDARD_FLIGHT_MINUTES = {
    'Mexico': 26,
    'Cayman Islands': 35,
    'Canada': 41,
    'Hawaii': 134,
    'United Kingdom': 159,
    'Argentina': 167,
    'Switzerland': 175,
    'Japan': 225,
    'China': 242,
    'UAE': 271,
    'South Africa': 297,
}

# lowercase alias -> canonical destination name
DEST_ALIASES = {
    'uae': 'UAE',
    'united arab emirates': 'UAE',
    'uk': 'United Kingdom',
    'great britain': 'United Kingdom',
    'torn': 'Torn',
}
DEST_ALIASES.update({name.lower(): name for name in STANDARD_FLIGHT_MINUTES})

# Flight duration multipliers per travel tier
TIER_MULTIPLIERS = {
    'standard': 1.00,
    'airstrip': 0.70,
    'wlt': 0.50,       # Wind Lines Travel job perk
    'business': 0.30,  # business class ticket
}
DEFAULT_TIER = 'airstrip'
TIER_LABELS = {
    'standard': 'standard flight',
    'airstrip': 'airstrip',
    'wlt': 'WLT',
    'business': 'business class',
}

# The tiers above are LABELS ONLY · the ETA maths uses a player's measured
# speed ratio (actual flight minutes / standard minutes), never the bucket.
#
# Why: snapping a measurement to the nearest bucket threw away the accurate
# number we had just observed. Measured against 82 real flights, every single
# prediction landed LATE (avg 8 min, worst 24) because every bucket is a touch
# slower than reality · e.g. MegaBates was measured at 143 min to South Africa,
# then predicted with the WLT bucket's 148.5 min, so he landed ~5.5 min early.
#
# Empirical ratios from those 82 flights: median 0.669, p25 0.657, p75 0.692.
DEFAULT_RATIO = 0.669          # used until we have measured that player
MIN_PLAUSIBLE_RATIO = 0.15     # faster than this is a glitch, not a perk
MAX_PLAUSIBLE_RATIO = 1.30
MAX_RATIO_SAMPLES = 5          # keep a few and take the median, to damp noise

# Landing times are shown as a window, not a single minute. The early edge is
# the "be ready by" moment, because the cost of error is asymmetric: an enemy
# landing sooner than advertised gets your people hit, while landing later
# only costs them a short wait.
POLL_LAG_SECONDS = 60          # we spot a departure up to one poll late
MEASURED_SPREAD = 0.03         # tight · we have watched this player fly
ASSUMED_SPREAD = 0.10          # loose · we are guessing their travel perks

# ===== Follow warnings =====
# When an enemy departs for a country one of our members is sitting in, that
# member gets pinged. Two dials keep it from becoming the noise Ikaros was
# worried about during a busy war:
#
# 1. A per-member-per-country cooldown. Once someone has been told "enemies are
#    heading to Switzerland", repeating it every few minutes adds nothing · they
#    already know to watch that country. Enemies departing in the SAME poll tick
#    are still grouped into one message, so a 6-man wave is one ping, not six.
# 2. A stat threshold. An enemy with a fraction of the member's battle stats
#    can't realistically hurt them, so they aren't worth a notification.
FOLLOW_ALERT_COOLDOWN_SECONDS = 15 * 60
FOLLOW_THREAT_RATIO = 0.5      # below this x the member's stats, never worth a ping
FOLLOW_COOLDOWN_RETENTION = 4  # prune cooldown rows older than N x the cooldown

# Enemies are split into bands RELATIVE TO THE MEMBER being warned, because
# "strong" has no absolute meaning here - measured live, the same enemy (PUAD,
# ~817k) is a genuine threat to Trizzy973 (516k) and completely irrelevant to
# TrevorLahey420 (7.5m) standing in the same country.
#
# Only a non-trivial enemy can TRIGGER a message; once one has, every other
# enemy heading to that country in the same tick is listed too, since the extra
# context is free - it rides along in a message that was being sent anyway.
BAND_STRONGER = 'stronger'   # at or above the member's estimate
BAND_UNKNOWN = 'unknown'     # no estimate - treated as a threat, never hidden
BAND_WEAKER = 'weaker'       # below the member, but still above the trigger bar
BAND_TRIVIAL = 'trivial'     # too weak to warrant a notification on its own
BAND_LABELS = {
    BAND_STRONGER: '\U0001f534 **Stronger than you**',
    BAND_UNKNOWN: '\U0001f7e1 **Unknown strength**',
    BAND_WEAKER: '\U0001f7e2 **Weaker than you**',
}
BAND_ORDER = [BAND_STRONGER, BAND_UNKNOWN, BAND_WEAKER]

# Abroad-hospital descriptions read "In a <adjective> hospital"
HOSPITAL_ADJECTIVES = {
    'mexican': 'Mexico',
    'cayman': 'Cayman Islands',
    'canadian': 'Canada',
    'hawaiian': 'Hawaii',
    'british': 'United Kingdom',
    'argentinian': 'Argentina',
    'argentine': 'Argentina',
    'swiss': 'Switzerland',
    'japanese': 'Japan',
    'chinese': 'China',
    'emirati': 'UAE',
    'united arab emirates': 'UAE',
    'south african': 'South Africa',
}

# Consecutive "war_tracker says the war isn't active" reads required before
# auto-stop · guards against reading war_tracker.was_war_active during its
# post-restart warm-up window (see check_flights).
WAR_END_STRIKES = 3


def normalize_dest(raw: str) -> Optional[str]:
    """Map a destination string to its canonical name."""
    if not raw:
        return None
    cleaned = raw.strip().rstrip('.').strip()
    return DEST_ALIASES.get(cleaned.lower(), cleaned)


def parse_phase(status: Dict, torn_id: str = '', name: str = '') -> Tuple[str, Optional[str]]:
    """
    Classify phase/destination from status text. Accepts both American and
    British spelling ("traveling"/"travelling") since Torn is UK-developed
    and the free-text description isn't guaranteed to match the API's
    state enum spelling.

    Returns (phase, destination) · destination is None when at home or unknown.
    torn_id/name are optional, used only to label diagnostic logging when a
    traveling/abroad member's destination can't be parsed out of the text.
    """
    state = (status or {}).get('state', '') or ''
    desc = ((status or {}).get('description', '') or '').strip()
    details = ((status or {}).get('details', '') or '').strip()
    desc_lower = f'{desc} {details}'.strip().lower()

    if state == 'Traveling':
        # Confirmed live format: "Traveling from {origin} to {destination}"
        # (origin is "Torn" outbound, destination is "Torn" on the way back)
        for prefix in ('traveling from ', 'travelling from '):
            if desc_lower.startswith(prefix):
                rest = desc_lower[len(prefix):]
                if ' to ' in rest:
                    origin_raw, dest_raw = rest.split(' to ', 1)
                    origin, dest = normalize_dest(origin_raw), normalize_dest(dest_raw)
                    if origin and origin.lower() == 'torn':
                        return PHASE_OUT, dest
                    if dest and dest.lower() == 'torn':
                        return PHASE_RETURNING, origin
        # Older/alternate wordings, kept as a cheap secondary attempt
        for prefix in ('returning to torn from ', 'returning to torn city from '):
            if desc_lower.startswith(prefix):
                return PHASE_RETURNING, normalize_dest(desc_lower[len(prefix):])
        for prefix in ('traveling to ', 'travelling to '):
            if desc_lower.startswith(prefix):
                dest = normalize_dest(desc_lower[len(prefix):])
                if dest and dest.lower() == 'torn':
                    return PHASE_RETURNING, None
                return PHASE_OUT, dest
        logger.warning(f"Fly: couldn't parse destination for {name} [{torn_id}] · "
                       f"state={state!r} description={desc!r} details={details!r}")
        return PHASE_OUT, None

    if state == 'Abroad':
        if desc_lower.startswith('in '):
            return PHASE_ABROAD, normalize_dest(desc_lower[3:])
        logger.warning(f"Fly: couldn't parse destination for {name} [{torn_id}] · "
                       f"state={state!r} description={desc!r} details={details!r}")
        return PHASE_ABROAD, None

    # Abroad hospital: "In a Swiss hospital ..." (home hospital is just "In hospital")
    if state == 'Hospital' and desc_lower.startswith('in a') and 'hospital' in desc_lower:
        for adj, country in HOSPITAL_ADJECTIVES.items():
            if adj in desc_lower:
                return PHASE_ABROAD, country
        logger.warning(f"Fly: couldn't parse hospital country for {name} [{torn_id}] · "
                       f"state={state!r} description={desc!r} details={details!r}")
        return PHASE_ABROAD, None

    return PHASE_HOME, None


class FlyTracker:
    """Tracks enemy faction flights during ranked war with landing estimates."""

    def __init__(self, bot, war_tracker=None):
        self.bot = bot
        self.war_tracker = war_tracker  # Set by bot.py
        self.verification_system = None  # Set by bot.py · maps Torn id -> Discord id
        self.data_file = 'data/flytrack.json'
        self.enabled = False
        self.war_id = None
        self.enemy_faction_id = None
        self.enemy_faction_name = ''
        self.players = {}  # {torn_id: {name, phase, dest, eta, leg_start, leg_observed}}
        self.tiers = {}    # {torn_id: {tier, name, observed_minutes, dest, ts}} · persists across wars
        # {f'{our_member_torn_id}:{country}': last_alert_ts} · persisted so a
        # restart mid-war doesn't re-ping everyone we already warned.
        self.follow_cooldowns = {}
        self._war_end_strikes = 0

    async def load_data(self):
        data = load_json(self.data_file, default={})
        self.enabled = data.get('enabled', False)
        self.war_id = data.get('war_id')
        self.enemy_faction_id = data.get('enemy_faction_id')
        self.enemy_faction_name = data.get('enemy_faction_name', '')
        self.players = data.get('players', {})
        self.tiers = data.get('tiers', {})
        self.follow_cooldowns = data.get('follow_cooldowns', {})
        if self.enabled:
            logger.info(f'Fly tracking resumed for faction {self.enemy_faction_id} ({self.enemy_faction_name})')

    async def save_data(self):
        save_json(self.data_file, {
            'enabled': self.enabled,
            'war_id': self.war_id,
            'enemy_faction_id': self.enemy_faction_id,
            'enemy_faction_name': self.enemy_faction_name,
            'players': self.players,
            'tiers': self.tiers,
            'follow_cooldowns': self.follow_cooldowns,
        })

    # ===== Toggle =====

    async def enable(self) -> Tuple[bool, discord.Embed]:
        """Turn fly tracking on for the current ranked war opponent."""
        if not self.war_tracker:
            return False, create_embed(
                title='Fly Tracking Unavailable',
                description='War tracker is not initialized.',
                color=EMBED_COLOR_WARNING
            )

        war_info = await self.war_tracker.get_current_war()
        if not war_info:
            return False, create_embed(
                title='No Active War',
                description='No active ranked war found.',
                color=EMBED_COLOR_WARNING
            )

        factions = war_info.get('data', {}).get('factions', {})
        enemy_id, enemy_name = None, 'Unknown'
        for fid, fdata in factions.items():
            if str(fid) != str(TORN_FACTION_ID):
                enemy_id = str(fid)
                enemy_name = fdata.get('name', 'Unknown')
        if not enemy_id:
            return False, create_embed(
                title='Enemy Faction Not Found',
                description='Could not determine the opposing faction from war data.',
                color=EMBED_COLOR_WARNING
            )

        self.war_id = war_info.get('war_id')
        self.enemy_faction_id = enemy_id
        self.enemy_faction_name = enemy_name
        self.players = {}
        # A cooldown left over from the previous war would silently swallow the
        # first warning for that member+country in this one.
        self.follow_cooldowns = {}
        self._war_end_strikes = 0

        # Baseline snapshot: record phases without alerting, so toggling on
        # mid-war doesn't spam a message per member.
        members = await self._fetch_enemy_members()
        returning_lines, outbound_lines, abroad_entries = [], [], []
        now_ts = datetime.now(timezone.utc).timestamp()
        if members:
            for tid, m in members.items():
                tid = str(tid)
                name = m.get('name', tid)
                phase, dest = parse_phase(m.get('status', {}), tid, name)

                if phase in (PHASE_OUT, PHASE_RETURNING):
                    resolved = self._estimate_leg(tid, phase, dest, now_ts)
                    self.players[tid] = {
                        'name': name, 'phase': resolved['phase'], 'dest': resolved['dest'],
                        'eta': resolved['eta'], 'leg_start': None, 'leg_observed': False,
                    }
                    line = self._format_group_line(
                        name, resolved['phase'], resolved['dest'], resolved['eta'],
                        self._eta_window(tid, resolved['dest'], resolved['eta']))
                    entry = (resolved['eta'] or float('inf'), line)
                    (returning_lines if resolved['phase'] == PHASE_RETURNING else outbound_lines).append(entry)
                elif phase == PHASE_ABROAD:
                    self.players[tid] = {
                        'name': name, 'phase': PHASE_ABROAD, 'dest': dest,
                        'eta': None, 'leg_start': None, 'leg_observed': False,
                    }
                    abroad_entries.append((name, self._format_abroad_line(name, dest)))
                else:
                    self.players[tid] = {
                        'name': name, 'phase': phase, 'dest': dest,
                        'eta': None, 'leg_start': None, 'leg_observed': False,
                    }

        returning_lines.sort(key=lambda e: e[0])
        outbound_lines.sort(key=lambda e: e[0])
        abroad_entries.sort(key=lambda e: e[0].lower())
        abroad_lines = [line for _, line in abroad_entries]

        self.enabled = True
        await self.save_data()
        logger.info(f'Fly tracking ENABLED vs {enemy_name} [{enemy_id}], {len(self.players)} members baselined')

        description = (
            f'Watching **{enemy_name}** [{enemy_id}] · alerts in <#{WAR_TRACKER_CHANNEL_ID}> · every 60s\n'
            f'*ETAs are estimates (airstrip default, refined per player over time).*'
        )
        embed = create_embed(
            title='✈️ Fly Tracking ON',
            description=description,
            color=EMBED_COLOR_SUCCESS
        )
        if returning_lines:
            embed.add_field(
                name=f'🛬 Returning · attack window ({len(returning_lines)})',
                value='\n'.join(line for _, line in returning_lines[:10])
                      + ('\n…' if len(returning_lines) > 10 else ''),
                inline=False
            )
        if outbound_lines:
            embed.add_field(
                name=f'✈️ Outbound ({len(outbound_lines)})',
                value='\n'.join(line for _, line in outbound_lines[:10])
                      + ('\n…' if len(outbound_lines) > 10 else ''),
                inline=False
            )
        if abroad_lines:
            embed.add_field(
                name=f'🌍 Abroad · landed ({len(abroad_lines)})',
                value='\n'.join(abroad_lines[:10]) + ('\n…' if len(abroad_lines) > 10 else ''),
                inline=False
            )
        if not returning_lines and not outbound_lines and not abroad_lines:
            embed.add_field(name='Status', value='Nobody abroad. Everyone is in Torn.', inline=False)
        embed.set_footer(text='/war view:Fly again to toggle off · /war view:FlyStatus for a live peek')
        return True, embed

    async def disable(self, reason: str = 'Toggled off') -> discord.Embed:
        """Turn fly tracking off. Learned tiers are kept for future wars."""
        name = self.enemy_faction_name or 'enemy faction'
        self.enabled = False
        self.players = {}
        self.war_id = None
        self._war_end_strikes = 0
        await self.save_data()
        logger.info(f'Fly tracking DISABLED ({reason})')
        return create_embed(
            title='✈️ Fly Tracking OFF',
            description=f'Stopped watching **{name}**. ({reason})\n'
                        f'Learned travel tiers are kept for next time.',
            color=EMBED_COLOR_INFO
        )

    async def status_embed(self) -> discord.Embed:
        """Read-only snapshot of who's currently flying/abroad, on demand —
        does NOT touch self.players or self.enabled, so it's safe to call
        anytime without disturbing the running background tracker."""
        if not self.enabled:
            return create_embed(
                title='Fly Tracking Off',
                description='Turn it on with `/war view:Fly` first.',
                color=EMBED_COLOR_WARNING
            )

        members = await self._fetch_enemy_members()
        if not members:
            return create_embed(
                title='Fly Status',
                description='Could not fetch the enemy roster right now. Try again shortly.',
                color=EMBED_COLOR_WARNING
            )

        returning_lines, outbound_lines, abroad_entries = [], [], []
        # country -> [sitting there, currently inbound]. Returning players are
        # counted in neither: they're leaving that country, not occupying it.
        tally = {}
        now_ts = datetime.now(timezone.utc).timestamp()
        for tid, m in members.items():
            tid = str(tid)
            name = m.get('name', tid)
            phase, dest = parse_phase(m.get('status', {}), tid, name)

            if phase in (PHASE_OUT, PHASE_RETURNING):
                # Reuse the already leg_start-anchored eta from check_flights()
                # whenever it's for this exact same leg · recomputing from "now"
                # on every peek would push the ETA later each time it's checked,
                # since it'd treat "now" as a fresh departure instant. Compare
                # the exact phase (not a coarse OUT/RETURNING-collapsed category)
                # so a leg check_flights() hasn't caught up to yet still gets a
                # fresh estimate instead of stale data from the previous leg.
                prev = self.players.get(tid)
                if prev and prev.get('phase') == phase and prev.get('eta') is not None:
                    r_phase, r_dest, r_eta = prev['phase'], prev['dest'], prev['eta']
                else:
                    resolved = self._estimate_leg(tid, phase, dest, now_ts, prev)
                    r_phase, r_dest, r_eta = resolved['phase'], resolved['dest'], resolved['eta']
                line = self._format_group_line(name, r_phase, r_dest, r_eta,
                                               self._eta_window(tid, r_dest, r_eta))
                entry = (r_eta or float('inf'), line)
                (returning_lines if r_phase == PHASE_RETURNING else outbound_lines).append(entry)
                if r_phase == PHASE_OUT and r_dest:
                    tally.setdefault(r_dest, [0, 0])[1] += 1
            elif phase == PHASE_ABROAD:
                abroad_entries.append((name, self._format_abroad_line(name, dest)))
                if dest:
                    tally.setdefault(dest, [0, 0])[0] += 1

        returning_lines.sort(key=lambda e: e[0])
        outbound_lines.sort(key=lambda e: e[0])
        abroad_entries.sort(key=lambda e: e[0].lower())
        abroad_lines = [line for _, line in abroad_entries]

        embed = create_embed(
            title=f'📡 Fly Status: {self.enemy_faction_name}',
            description='Live snapshot. Tracking keeps running in the background regardless.',
            color=EMBED_COLOR_INFO
        )
        # Where the enemy is massing, busiest country first. Sits above the
        # per-player lists because it's the one thing you want at a glance.
        if tally:
            rows = sorted(tally.items(), key=lambda kv: (-(kv[1][0] + kv[1][1]), kv[0]))
            hotspots = []
            for dest, (sitting, inbound) in rows[:8]:
                bits = []
                if sitting:
                    bits.append(f'{sitting} there')
                if inbound:
                    bits.append(f'{inbound} inbound')
                hotspots.append(f'**{dest}** {sitting + inbound} · {" · ".join(bits)}')
            embed.add_field(name='🗺️ Hotspots',
                            value='\n'.join(hotspots), inline=False)
        if returning_lines:
            embed.add_field(
                name=f'🛬 Returning · attack window ({len(returning_lines)})',
                value='\n'.join(line for _, line in returning_lines[:10])
                      + ('\n…' if len(returning_lines) > 10 else ''),
                inline=False
            )
        if outbound_lines:
            embed.add_field(
                name=f'✈️ Outbound ({len(outbound_lines)})',
                value='\n'.join(line for _, line in outbound_lines[:10])
                      + ('\n…' if len(outbound_lines) > 10 else ''),
                inline=False
            )
        if abroad_lines:
            embed.add_field(
                name=f'🌍 Abroad · landed ({len(abroad_lines)})',
                value='\n'.join(abroad_lines[:10]) + ('\n…' if len(abroad_lines) > 10 else ''),
                inline=False
            )
        if not returning_lines and not outbound_lines and not abroad_lines:
            embed.add_field(name='Status', value='Nobody abroad. Everyone is in Torn.', inline=False)
        return embed

    # ===== Polling =====

    async def check_flights(self):
        """One poll tick: fetch enemy roster, detect transitions, post alerts.
        Called every 60s by the background loop in bot.py."""
        if not self.enabled or not self.enemy_faction_id:
            return

        # Auto-stop when the war ends OR a different war has taken over.
        # Piggyback on war_tracker's own rigorous, raw-refetch-confirmed end
        # detection (WarTracker.check_war_update, called every minute
        # independently) instead of re-deriving a weaker signal here · a
        # lone get_current_war() == None can just be a transient API
        # hiccup, and trusting a short run of those on their own is what
        # silently killed tracking mid-war before.
        #
        # self.enemy_faction_id is fixed once at enable() and never
        # re-derived, so if the tracked war ends and a genuinely different
        # war starts (new opponent), nothing else here would ever notice —
        # it would keep polling the old, no-longer-relevant faction's
        # roster indefinitely. Cross-checking current_war_id catches that.
        #
        # Both was_war_active and current_war_id reset on process restart
        # and only recover once war_update_check completes its first tick —
        # that loop and this one fire around the same on_ready() moment
        # with no ordering guarantee between them, so on every restart
        # there's a window where either check could read a stale value
        # before war_update_check has caught up. Requiring a few
        # consecutive confirmations survives that window (war_update_check
        # needs one Torn API call to recover, comfortably under a minute)
        # without reopening the false-positive problem, since both signals
        # are already hardened on war_tracker's side.
        war_changed = self.war_tracker and (
            not self.war_tracker.was_war_active
            or str(self.war_tracker.current_war_id) != str(self.war_id)
        )
        if war_changed:
            self._war_end_strikes += 1
            if self._war_end_strikes >= WAR_END_STRIKES:
                if not self.war_tracker.was_war_active:
                    reason = 'War ended'
                else:
                    reason = 'A different war is now active. Re-toggle Fly tracking for it'
                embed = await self.disable(reason=reason)
                await self._post(embed=embed)
                return
        else:
            self._war_end_strikes = 0

        members = await self._fetch_enemy_members()
        if not members:
            return  # API hiccup · keep previous state, try next tick

        now_ts = datetime.now(timezone.utc).timestamp()
        alerts = []
        departures = []  # outbound legs this tick, for the follow warnings below
        changed = False

        for tid, m in members.items():
            tid = str(tid)
            name = m.get('name', tid)
            status = m.get('status', {})
            text_phase, text_dest = parse_phase(status, tid, name)

            prev = self.players.get(tid)
            if prev is None:
                # New member joined mid-war · baseline silently
                self.players[tid] = {
                    'name': name, 'phase': text_phase, 'dest': text_dest,
                    'eta': None, 'leg_start': None, 'leg_observed': False,
                }
                changed = True
                continue

            prev_phase = prev.get('phase')

            # Compare the exact fine-grained phase, not a coarse OUT/RETURNING-
            # collapsed category · otherwise a fast turnaround (land abroad and
            # immediately re-depart within one poll tick) never registers as a
            # change, silently swallowing the return alert and leaving stale
            # outbound data in self.players.
            if text_phase == prev_phase:
                if prev.get('name') != name:
                    prev['name'] = name
                    changed = True
                continue

            # --- Transition handling ---
            changed = True

            if text_phase in (PHASE_OUT, PHASE_RETURNING):
                resolved = self._estimate_leg(tid, text_phase, text_dest, now_ts, prev)

                early = self._eta_window(tid, resolved['dest'], resolved['eta'])
                if resolved['phase'] == PHASE_OUT:
                    alerts.append(self._format_outbound_alert(
                        resolved['dest'], name, resolved['eta'], early))
                    if resolved['dest']:
                        # Only outbound legs with a known destination can be
                        # matched against where our own members are sitting.
                        departures.append({
                            'tid': tid, 'name': name, 'dest': resolved['dest'],
                            'eta': resolved['eta'], 'early': early,
                        })
                else:
                    alerts.append(self._format_return_alert(
                        tid, name, resolved['dest'], resolved['eta'], early))

                self.players[tid] = {
                    'name': name, 'phase': resolved['phase'], 'dest': resolved['dest'],
                    'eta': resolved['eta'], 'leg_start': now_ts, 'leg_observed': True,
                }

            elif text_phase == PHASE_ABROAD:
                # Landed abroad · learn tier if we watched the whole outbound leg
                if prev_phase == PHASE_OUT and prev.get('leg_observed') and prev.get('leg_start'):
                    self._learn_tier(tid, name, prev.get('dest'), now_ts - prev['leg_start'])
                self.players[tid] = {
                    'name': name, 'phase': PHASE_ABROAD, 'dest': text_dest or prev.get('dest'),
                    'eta': None, 'leg_start': None, 'leg_observed': False,
                }

            else:  # home · landed back in Torn
                if prev_phase == PHASE_RETURNING and prev.get('leg_observed') and prev.get('leg_start'):
                    self._learn_tier(tid, name, prev.get('dest'), now_ts - prev['leg_start'])
                self.players[tid] = {
                    'name': name, 'phase': PHASE_HOME, 'dest': None,
                    'eta': None, 'leg_start': None, 'leg_observed': False,
                }

        # Drop members who left the faction
        gone = [tid for tid in self.players if tid not in {str(k) for k in members}]
        for tid in gone:
            del self.players[tid]
            changed = True

        if alerts:
            await self._post(content='\n'.join(alerts))

        # Follow warnings are posted one message per member rather than folded
        # into the feed above, because each one @-mentions a specific person and
        # must not get chunked together with unrelated flights.
        if departures:
            for line in await self._follow_alerts(departures, now_ts):
                await self._post(content=line)
                changed = True  # cooldowns were stamped - persist them

        if changed:
            await self.save_data()

    async def _fetch_enemy_members(self) -> Optional[Dict]:
        """Fetch enemy faction roster, bypassing the API cache for fresh statuses."""
        data = await fetch_any_faction_data(self.enemy_faction_id, 'basic', cache_seconds=0)
        if not data:
            return None
        return data.get('members', {})

    # ===== Follow warnings =====

    async def _fetch_own_abroad(self) -> Optional[Dict]:
        """Our own members currently SITTING in a foreign country, grouped by
        country: {'Switzerland': [{'tid': .., 'name': ..}, ...]}.

        Returns None when the roster can't be read. The caller must then stay
        silent instead of concluding "nobody is abroad" - that would quietly
        switch follow warnings off for as long as the API misbehaves, which is
        exactly the None-vs-empty failure that once flagged the whole faction
        for missing OCs (see CLAUDE.md).

        Only fetched on ticks where an enemy actually departed, so quiet ticks
        cost no extra API call at all.
        """
        data = await fetch_any_faction_data(TORN_FACTION_ID, 'basic', cache_seconds=0)
        if not data:
            return None
        members = data.get('members')
        if not members:
            # HTTP 200 with no roster is a malformed answer, not a real empty
            # faction - we always have members.
            return None

        by_country = {}
        for tid, m in members.items():
            tid = str(tid)
            name = m.get('name', tid)
            phase, dest = parse_phase(m.get('status', {}), tid, name)
            # Landed only. Someone still in the air isn't sitting anywhere yet,
            # and an enemy leaving now could easily land after they do.
            # Abroad-hospital counts as sitting: they're stuck in that country
            # and become attackable the moment they're out.
            if phase == PHASE_ABROAD and dest:
                by_country.setdefault(dest, []).append({'tid': tid, 'name': name})
        return by_country

    async def _classify_threats(self, pairs: list) -> list:
        """Band each (departure, our member) pair by how the enemy compares to
        THAT member. Returns (departure, member, enemy_bs, band) tuples.

        Nothing is dropped here - banding and the decision to send are kept
        apart on purpose, so a message triggered by a real threat can also list
        the harmless enemies heading the same way without costing a second ping.

        Fails OPEN: no FFScouter key, a failed lookup, or a missing estimate all
        land in BAND_UNKNOWN, which triggers. A warning we didn't send gets
        somebody hit; a warning we didn't need costs one notification.

        Note FFScouter's own `fair_fight` number is relative to the API key
        owner, not to each of our members, so it can't be used here - the
        comparison has to be estimate vs estimate.
        """
        ff = {}
        if FFSCOUTER_API_KEY:
            ids = sorted({dep['tid'] for dep, _ in pairs} | {mem['tid'] for _, mem in pairs})
            try:
                ff = await fetch_ffscouter_stats(ids) or {}
            except Exception as e:
                logger.error(f'Fly: FFScouter lookup failed for follow warning: {e}')

        def bs(tid):
            rec = ff.get(str(tid)) or {}
            val = rec.get('bs_estimate')
            try:
                return int(val) if val else None
            except (TypeError, ValueError):
                return None

        banded = []
        for dep, mem in pairs:
            enemy_bs, member_bs = bs(dep['tid']), bs(mem['tid'])
            if enemy_bs is None or member_bs is None:
                band = BAND_UNKNOWN
            elif enemy_bs >= member_bs:
                band = BAND_STRONGER
            elif enemy_bs >= member_bs * FOLLOW_THREAT_RATIO:
                band = BAND_WEAKER
            else:
                band = BAND_TRIVIAL
            banded.append((dep, mem, enemy_bs, band))
        return banded

    def _mention_for(self, torn_id: str) -> Optional[str]:
        """Discord mention for a Torn id, via the verification database.
        None for anyone who never registered with the bot - they get named in
        the alert instead, so leadership can still nudge them manually."""
        vs = self.verification_system
        if not vs:
            return None
        for discord_id, rec in (getattr(vs, 'verified_users', None) or {}).items():
            if str((rec or {}).get('torn_id')) == str(torn_id):
                return f'<@{discord_id}>'
        return None

    def _prune_follow_cooldowns(self, now_ts: float):
        """Cooldown rows would otherwise accumulate one per member-country pair
        forever. Anything well past its cooldown can never suppress again."""
        cutoff = now_ts - (FOLLOW_ALERT_COOLDOWN_SECONDS * FOLLOW_COOLDOWN_RETENTION)
        stale = [k for k, ts in self.follow_cooldowns.items() if ts < cutoff]
        for k in stale:
            del self.follow_cooldowns[k]

    @staticmethod
    def _format_bs(value: Optional[int]) -> str:
        """Compact battle-stat estimate, e.g. 2140000000 -> '~2.1b'."""
        if not value:
            return ''
        for div, suffix in ((1e9, 'b'), (1e6, 'm'), (1e3, 'k')):
            if value >= div:
                return f'~{value / div:.1f}{suffix}'
        return f'~{value}'

    def _format_follow_alert(self, mem: Dict, items: list) -> str:
        """One warning message aimed at a single member, listing every enemy
        that just took off toward the country they're sitting in, split into
        stronger/weaker bands relative to that member.

        The mention alone identifies the person - Discord already renders it as
        their nickname, which in this server carries the Torn name, so also
        printing mem['name'] produced "@Trizzy973 [3266373] Trizzy973".

        An unregistered member can't be mentioned at all, so the warning names
        them and closes with how to fix that. Deliberately NOT escalated to a
        role ping: the right fix is them linking up, not routing their alerts
        through someone else forever.
        """
        mention = self._mention_for(mem['tid'])
        who = mention or f"**{mem['name']}** (not registered)"
        dest = items[0][0]['dest']
        count = '**1 enemy**' if len(items) == 1 else f'**{len(items)} enemies**'
        lines = [f"\u26a0\ufe0f {who} \u00b7 {count} heading to **{dest}**, where you are:"]

        # BAND_TRIVIAL shares the "weaker" heading - the split the faction asked
        # for is strong vs weak, not a third tier nobody would act on.
        grouped = {}
        for dep, enemy_bs, band in items:
            grouped.setdefault(BAND_WEAKER if band == BAND_TRIVIAL else band, []).append(
                (dep, enemy_bs))

        for band in BAND_ORDER:
            rows = grouped.get(band)
            if not rows:
                continue
            lines.append(BAND_LABELS[band])
            for dep, enemy_bs in rows:
                profile = f"https://www.torn.com/profiles.php?XID={dep['tid']}"
                stat = f" \u00b7 {self._format_bs(enemy_bs)}" if enemy_bs else ''
                when = (f"lands {self._eta_text(dep['eta'], dep['early'])}"
                        if dep['eta'] else 'landing time unknown')
                lines.append(f"\u2003\u2022 **{dep['name']}** {when}{stat} "
                             f"\u00b7 [Profile]({profile})")

        if not mention:
            # Discord subtext (-#) keeps this small and grey so it nags the one
            # person who needs it without cluttering the alert for everyone.
            lines.append('-# Not getting pinged? Run `/register` in this server, '
                         'or link your Torn account to Discord on Torn itself '
                         'and the bot will pick you up automatically.')
        return '\n'.join(lines)

    async def _follow_alerts(self, departures: list, now_ts: float) -> list:
        """Build the "someone is following you" warnings for this tick.

        Returns a list of ready-to-post message strings (one per member) and
        stamps the cooldowns for everyone warned.
        """
        own = await self._fetch_own_abroad()
        if own is None:
            logger.warning('Fly: own roster unavailable - skipping follow warnings this tick')
            return []
        if not own:
            return []  # genuinely nobody abroad, nothing to follow

        # Pair every departure with our members already sitting at that destination
        pairs = [(dep, mem) for dep in departures for mem in own.get(dep['dest'], [])]
        if not pairs:
            return []

        # Cooldown first, so suppressed pairs never cost an FFScouter lookup.
        # The stamp is only written once a warning actually goes out, so a pair
        # the stat filter drops doesn't silence the next real threat.
        fresh = []
        for dep, mem in pairs:
            last = self.follow_cooldowns.get(f"{mem['tid']}:{dep['dest']}", 0)
            if now_ts - last >= FOLLOW_ALERT_COOLDOWN_SECONDS:
                fresh.append((dep, mem))
        if not fresh:
            return []

        banded = await self._classify_threats(fresh)

        # Group by the member being warned so each person gets one message.
        # Messages stay per-member rather than one per country because the
        # bands are relative to that member's own stats - the same enemy is a
        # threat to one of two people standing in the same place.
        grouped = {}
        for dep, mem, enemy_bs, band in banded:
            key = f"{mem['tid']}:{dep['dest']}"
            grouped.setdefault(key, (mem, []))[1].append((dep, enemy_bs, band))

        messages = []
        for key, (mem, items) in grouped.items():
            # Trivial enemies ride along in a message, but never cause one.
            if all(band == BAND_TRIVIAL for _, _, band in items):
                logger.info(f"Fly: no follow warning for {mem['name']} [{mem['tid']}] in "
                            f"{items[0][0]['dest']} - all {len(items)} enemy(s) too weak")
                continue
            items.sort(key=lambda it: it[0]['eta'] or float('inf'))
            messages.append(self._format_follow_alert(mem, items))
            self.follow_cooldowns[key] = now_ts
            logger.info(f"Fly: follow warning for {mem['name']} [{mem['tid']}] in "
                        f"{items[0][0]['dest']} - {len(items)} enemy(s) inbound")

        if not messages:
            return []

        self._prune_follow_cooldowns(now_ts)
        return messages

    # ===== Leg estimation =====

    def _estimate_leg(self, torn_id: str, phase: str, dest: Optional[str], now_ts: float,
                      prev: Optional[Dict] = None) -> Dict:
        """Estimate ETA from status text + known flight-duration table."""
        if phase == PHASE_RETURNING and not dest:
            dest = (prev or {}).get('dest')
        eta = self._eta_ts(torn_id, dest, now_ts)
        note = f' (estimate · {self._tier_note(torn_id)})' if eta else ''
        return {'phase': phase, 'dest': dest, 'eta': eta, 'note': note}

    # ===== ETA / tier logic =====

    def _tier_for(self, torn_id: str) -> Tuple[str, bool]:
        """Return (tier, learned) for a player · learned=False means assumption.
        Label only; the ETA maths uses _ratio_for."""
        entry = self.tiers.get(str(torn_id))
        if entry and entry.get('tier') in TIER_MULTIPLIERS:
            return entry['tier'], True
        return DEFAULT_TIER, False

    def _ratio_for(self, torn_id: str) -> Tuple[float, bool]:
        """
        Return (speed_ratio, measured) · the player's actual flight minutes as a
        fraction of the standard time, taken from flights we have watched them
        complete. measured=False means nobody has timed them yet, so we fall
        back to the empirical median across the faction.

        Median of the stored samples, so one mistimed leg can't skew a player.
        """
        entry = self.tiers.get(str(torn_id)) or {}

        samples = [r for r in (entry.get('ratios') or [])
                   if isinstance(r, (int, float)) and MIN_PLAUSIBLE_RATIO <= r <= MAX_PLAUSIBLE_RATIO]

        # Records written before ratios existed still carry the raw measurement,
        # so derive one rather than discarding real observed data.
        if not samples:
            std = STANDARD_FLIGHT_MINUTES.get(entry.get('dest') or '')
            observed = entry.get('observed_minutes')
            if std and observed:
                derived = observed / std
                if MIN_PLAUSIBLE_RATIO <= derived <= MAX_PLAUSIBLE_RATIO:
                    samples = [derived]

        if samples:
            return statistics.median(samples), True
        return DEFAULT_RATIO, False

    def _eta_ts(self, torn_id: str, dest: Optional[str], leg_start: float) -> Optional[int]:
        """Estimated landing timestamp for a leg, or None if destination unknown."""
        std = STANDARD_FLIGHT_MINUTES.get(dest or '')
        if not std:
            return None
        ratio, _ = self._ratio_for(torn_id)
        return int(leg_start + std * ratio * 60)

    def _eta_window(self, torn_id: str, dest: Optional[str],
                    eta: Optional[int]) -> Optional[int]:
        """
        Earliest plausible landing for an ETA · the "be ready by" edge of the
        window. Covers the poll lag (we see a departure up to 60s after it
        happened, so the clock starts late) plus how confident we are in this
        player's speed. Returns None when there is no ETA to bound.
        """
        if not eta:
            return None
        std = STANDARD_FLIGHT_MINUTES.get(dest or '')
        if not std:
            return None
        ratio, measured = self._ratio_for(torn_id)
        spread = MEASURED_SPREAD if measured else ASSUMED_SPREAD
        margin = POLL_LAG_SECONDS + std * ratio * spread * 60
        early = int(eta - margin)
        return early if early < eta else None

    def _tier_note(self, torn_id: str) -> str:
        tier, learned = self._tier_for(torn_id)
        ratio, measured = self._ratio_for(torn_id)
        label = TIER_LABELS[tier]
        if measured:
            return f'{label} · measured at {ratio:.0%} of standard'
        return f'assuming {label}'

    def _learn_tier(self, torn_id: str, name: str, dest: Optional[str], elapsed_seconds: float):
        """Infer a player's travel tier from a fully-observed flight leg (departure and
        arrival both caught live), refining future ETA estimates for that player."""
        std = STANDARD_FLIGHT_MINUTES.get(dest or '')
        if not std or elapsed_seconds <= 0:
            return

        ratio = (elapsed_seconds / 60) / std

        # Reject only physically implausible timings (a missed poll, a status
        # we misread). Anything inside the plausible band is kept as measured —
        # we no longer round it to a tier, because that rounding was itself the
        # source of the "landed early" complaints.
        if not (MIN_PLAUSIBLE_RATIO <= ratio <= MAX_PLAUSIBLE_RATIO):
            logger.info(f'Fly: speed sample rejected for {name} [{torn_id}] · '
                        f'{elapsed_seconds/60:.1f}min to {dest} is {ratio:.2f}x standard')
            return

        entry = self.tiers.get(str(torn_id), {})
        samples = [r for r in (entry.get('ratios') or [])
                   if isinstance(r, (int, float))]
        samples.append(round(ratio, 4))
        samples = samples[-MAX_RATIO_SAMPLES:]

        # Nearest tier is kept purely as a human-readable label in alerts.
        best_tier = min(TIER_MULTIPLIERS, key=lambda t: abs(ratio - TIER_MULTIPLIERS[t]))

        self.tiers[str(torn_id)] = {
            'tier': best_tier,
            'name': name,
            'ratios': samples,
            'observed_minutes': round(elapsed_seconds / 60, 1),
            'dest': dest,
            'ts': datetime.now(timezone.utc).timestamp(),
        }
        logger.info(f'Fly: measured {name} [{torn_id}] at {ratio:.3f}x standard '
                    f'({elapsed_seconds/60:.1f}min to {dest}, {len(samples)} sample(s))')

    # ===== Message formatting =====

    @staticmethod
    def _eta_text(eta: Optional[int], early: Optional[int]) -> str:
        """
        Landing time as a window ("5:31–5:36 pm") when we have an early edge,
        otherwise a single time. The relative countdown always tracks the EARLY
        edge, so "in 5 minutes" is the moment to be ready by, never an optimistic
        one that leaves people exposed.
        """
        if early:
            return f'<t:{early}:t>–<t:{eta}:t> (<t:{early}:R>)'
        return f'<t:{eta}:t> (<t:{eta}:R>)'

    def _format_outbound_alert(self, dest: Optional[str], name: str, eta: Optional[int],
                               early: Optional[int] = None) -> str:
        # 🔴/🟢 instead of plane emoji · ✈️/🛬 look nearly identical at a
        # glance, which was the actual reason out/in was hard to tell apart.
        dest_label = dest or 'an unknown destination'
        if eta:
            return f'🔴 **{name}** → **{dest_label}** · lands {self._eta_text(eta, early)}'
        return f'🔴 **{name}** → **{dest_label}** · flight time unknown'

    def _format_return_alert(self, torn_id: str, name: str, dest: Optional[str],
                             eta: Optional[int], early: Optional[int] = None) -> str:
        dest_label = dest or 'abroad'
        attack = f'https://www.torn.com/loader.php?sid=attack&user2ID={torn_id}'
        if eta:
            return (f'🟢 **{name}** ← **{dest_label}** · lands {self._eta_text(eta, early)} '
                    f'· [Attack]({attack})')
        return f'🟢 **{name}** ← **{dest_label}** · landing time unknown · [Attack]({attack})'

    def _format_group_line(self, name: str, phase: str, dest: Optional[str], eta: Optional[int],
                           early: Optional[int] = None) -> str:
        """One row for the grouped Returning/Outbound lists. Takes the fields
        directly (not a self.players lookup) so it's safe to use for a
        read-only status peek that must not touch persisted tracking state."""
        icon = '🔴 →' if phase == PHASE_OUT else '🟢 ←'
        dest_label = dest or 'unknown'
        if eta:
            return f'{icon} **{name}** **{dest_label}** · {self._eta_text(eta, early)}'
        return f'{icon} **{name}** **{dest_label}** · ETA unknown'

    def _format_abroad_line(self, name: str, dest: Optional[str]) -> str:
        """One row for the Abroad (landed, not currently flying) list."""
        return f'**{name}** · {dest or "unknown"}'

    async def _post(self, content: str = None, embed: discord.Embed = None):
        """Post to the war-tracker channel."""
        if not WAR_TRACKER_CHANNEL_ID:
            logger.warning('Fly: WAR_TRACKER_CHANNEL_ID not set · dropping alert')
            return
        channel = self.bot.get_channel(WAR_TRACKER_CHANNEL_ID)
        if not channel:
            logger.warning(f'Fly: channel {WAR_TRACKER_CHANNEL_ID} not found')
            return
        try:
            if content:
                # Discord message limit is 2000 chars · chunk on line boundaries
                lines = content.split('\n')
                buf = ''
                for line in lines:
                    if len(buf) + len(line) + 1 > 1900:
                        await channel.send(buf, suppress_embeds=True)
                        buf = ''
                    buf = f'{buf}\n{line}' if buf else line
                if buf:
                    await channel.send(buf, suppress_embeds=True)
            if embed:
                await channel.send(embed=embed)
        except Exception as e:
            logger.error(f'Fly: failed to post to war-tracker channel: {e}')
