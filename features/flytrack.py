"""
Fly Tracking Module
Track enemy faction flights during ranked war and estimate landing times.

Primary path: Torn's `travel` selection (user/{id}?selections=travel) returns
destination, method, and time_left for ANY player, not just the API key
owner — travel destination is public game info (visible on profiles unless
the target has a max-level Theater), so this gives an EXACT arrival time.
We only call it for the handful of members currently mid-flight, resolved
once per leg (not every poll tick).

Fallback path: if that lookup ever comes back empty (privacy upgrade, API
hiccup), we timestamp the status transition and estimate using a known
flight-duration table, defaulting to the airstrip tier (x0.70) until a
player's real tier (standard/airstrip/WLT/business) is learned from a fully
observed leg.

Toggled via /war view:Fly (management only). Alerts post to the war-tracker
channel. Auto-stops when the ranked war ends.
"""

import discord
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple

from utils.helpers import load_json, save_json, create_embed
from config import (
    TORN_FACTION_ID,
    WAR_TRACKER_CHANNEL_ID,
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
)
from utils.torn_api import fetch_any_faction_data, fetch_player_data

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

# Consecutive "no active war" reads required before auto-stop
# (a single None can just be an API hiccup)
WAR_END_STRIKES = 3

# Safety cap on individual travel-selection lookups per poll tick, so a chaotic
# moment (many departures at once) can't blow through the API rate budget.
# Anyone beyond the cap still gets tracked via the estimate fallback this tick.
MAX_TRAVEL_LOOKUPS_PER_TICK = 25


def normalize_dest(raw: str) -> Optional[str]:
    """Map a destination string to its canonical name."""
    if not raw:
        return None
    cleaned = raw.strip().rstrip('.').strip()
    return DEST_ALIASES.get(cleaned.lower(), cleaned)


def _coarse_category(phase: str) -> str:
    """Collapse a fine-grained phase to the roster-visible category
    (Torn's `state` field can't distinguish outbound vs returning)."""
    if phase in (PHASE_OUT, PHASE_RETURNING):
        return 'traveling'
    if phase == PHASE_ABROAD:
        return 'abroad'
    return 'home'


def parse_phase(status: Dict) -> Tuple[str, Optional[str]]:
    """
    Fallback classifier: guess phase/destination from status text when the
    travel-selection lookup is unavailable. Accepts both American and
    British spelling ("traveling"/"travelling") since Torn is UK-developed
    and the free-text description isn't guaranteed to match the API's
    state enum spelling.

    Returns (phase, destination) — destination is None when at home or unknown.
    """
    state = (status or {}).get('state', '') or ''
    desc = ((status or {}).get('description', '') or '').strip()
    details = ((status or {}).get('details', '') or '').strip()
    desc_lower = f'{desc} {details}'.strip().lower()

    if state == 'Traveling':
        for prefix in ('returning to torn from ', 'returning to torn city from '):
            if desc_lower.startswith(prefix):
                return PHASE_RETURNING, normalize_dest(desc_lower[len(prefix):])
        for prefix in ('traveling to ', 'travelling to '):
            if desc_lower.startswith(prefix):
                dest = normalize_dest(desc_lower[len(prefix):])
                if dest and dest.lower() == 'torn':
                    return PHASE_RETURNING, None
                return PHASE_OUT, dest
        return PHASE_OUT, None

    if state == 'Abroad':
        if desc_lower.startswith('in '):
            return PHASE_ABROAD, normalize_dest(desc_lower[3:])
        return PHASE_ABROAD, None

    # Abroad hospital: "In a Swiss hospital ..." (home hospital is just "In hospital")
    if state == 'Hospital' and desc_lower.startswith('in a') and 'hospital' in desc_lower:
        for adj, country in HOSPITAL_ADJECTIVES.items():
            if adj in desc_lower:
                return PHASE_ABROAD, country
        return PHASE_ABROAD, None

    return PHASE_HOME, None


class FlyTracker:
    """Tracks enemy faction flights during ranked war with landing estimates."""

    def __init__(self, bot, war_tracker=None):
        self.bot = bot
        self.war_tracker = war_tracker  # Set by bot.py
        self.data_file = 'data/flytrack.json'
        self.enabled = False
        self.war_id = None
        self.enemy_faction_id = None
        self.enemy_faction_name = ''
        self.players = {}  # {torn_id: {name, phase, dest, eta, leg_start, leg_observed}}
        self.tiers = {}    # {torn_id: {tier, name, observed_minutes, dest, ts}} — persists across wars
        self._war_end_strikes = 0

    async def load_data(self):
        data = load_json(self.data_file, default={})
        self.enabled = data.get('enabled', False)
        self.war_id = data.get('war_id')
        self.enemy_faction_id = data.get('enemy_faction_id')
        self.enemy_faction_name = data.get('enemy_faction_name', '')
        self.players = data.get('players', {})
        self.tiers = data.get('tiers', {})
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
                description='Fly tracking needs an active ranked war — none found.',
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
        self._war_end_strikes = 0

        # Baseline snapshot: resolve anyone already mid-flight (real ETA via
        # individual lookup where possible) without alerting, so toggling on
        # mid-war doesn't spam a message per member.
        members = await self._fetch_enemy_members()
        in_air_lines = []
        now_ts = datetime.now(timezone.utc).timestamp()
        lookups_used = 0
        if members:
            for tid, m in members.items():
                tid = str(tid)
                status = m.get('status', {})
                phase, dest = parse_phase(status)

                if phase in (PHASE_OUT, PHASE_RETURNING) and lookups_used < MAX_TRAVEL_LOOKUPS_PER_TICK:
                    lookups_used += 1
                    resolved = await self._resolve_leg(tid, status, dest, now_ts)
                    self.players[tid] = {
                        'name': m.get('name', tid), 'phase': resolved['phase'],
                        'dest': resolved['dest'], 'eta': resolved['eta'],
                        'leg_start': now_ts, 'leg_observed': False,  # mid-flight spotted, not a full leg
                    }
                    in_air_lines.append(self._format_latest_line(tid))
                else:
                    self.players[tid] = {
                        'name': m.get('name', tid), 'phase': phase, 'dest': dest,
                        'eta': None, 'leg_start': None, 'leg_observed': False,
                    }
                    if phase in (PHASE_OUT, PHASE_RETURNING):
                        in_air_lines.append(self._format_latest_line(tid))

        self.enabled = True
        await self.save_data()
        logger.info(f'Fly tracking ENABLED vs {enemy_name} [{enemy_id}], {len(self.players)} members baselined')

        description = (
            f'Watching **{enemy_name}** [{enemy_id}] — polling every 60 seconds.\n'
            f'Alerts post to <#{WAR_TRACKER_CHANNEL_ID}>.\n\n'
            f'ETAs use Torn\'s real travel time when available; falls back to an '
            f'**airstrip** (x0.70) estimate otherwise, refined once a player\'s '
            f'real tier is learned.'
        )
        embed = create_embed(
            title='✈️ Fly Tracking ON',
            description=description,
            color=EMBED_COLOR_SUCCESS
        )
        if in_air_lines:
            embed.add_field(
                name=f'Currently in the air ({len(in_air_lines)})',
                value='\n'.join(in_air_lines[:15]) + ('\n…' if len(in_air_lines) > 15 else ''),
                inline=False
            )
        else:
            embed.add_field(name='Currently in the air', value='Nobody — all enemies are grounded.', inline=False)
        embed.set_footer(text='Toggle off with /war view:Fly · auto-stops when the war ends')
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

    # ===== Polling =====

    async def check_flights(self):
        """One poll tick: fetch enemy roster, detect transitions, post alerts.
        Called every 60s by the background loop in bot.py."""
        if not self.enabled or not self.enemy_faction_id:
            return

        # Auto-stop when the war ends (tolerate transient API failures)
        war_info = await self.war_tracker.get_current_war() if self.war_tracker else None
        if war_info is None:
            self._war_end_strikes += 1
            if self._war_end_strikes >= WAR_END_STRIKES:
                embed = await self.disable(reason='War ended')
                await self._post(embed=embed)
                return
        else:
            self._war_end_strikes = 0

        members = await self._fetch_enemy_members()
        if not members:
            return  # API hiccup — keep previous state, try next tick

        now_ts = datetime.now(timezone.utc).timestamp()
        alerts = []
        changed = False
        lookups_used = 0

        for tid, m in members.items():
            tid = str(tid)
            name = m.get('name', tid)
            status = m.get('status', {})
            text_phase, text_dest = parse_phase(status)

            prev = self.players.get(tid)
            if prev is None:
                # New member joined mid-war — baseline silently
                self.players[tid] = {
                    'name': name, 'phase': text_phase, 'dest': text_dest,
                    'eta': None, 'leg_start': None, 'leg_observed': False,
                }
                changed = True
                continue

            prev_phase = prev.get('phase')
            prev_category = _coarse_category(prev_phase)
            new_category = _coarse_category(text_phase)

            if new_category == prev_category:
                if prev.get('name') != name:
                    prev['name'] = name
                    changed = True
                continue

            # --- Transition handling ---
            changed = True

            if new_category == 'traveling':
                if lookups_used < MAX_TRAVEL_LOOKUPS_PER_TICK:
                    lookups_used += 1
                    resolved = await self._resolve_leg(tid, status, text_dest, now_ts, prev=prev)
                else:
                    resolved = self._estimate_leg(tid, text_phase, text_dest, now_ts, prev)

                if resolved['phase'] == PHASE_OUT:
                    alerts.append(self._format_outbound_alert(resolved['dest'], name, resolved['eta'], resolved['note']))
                else:
                    alerts.append(self._format_return_alert(tid, name, resolved['dest'], resolved['eta'], resolved['note']))

                self.players[tid] = {
                    'name': name, 'phase': resolved['phase'], 'dest': resolved['dest'],
                    'eta': resolved['eta'], 'leg_start': now_ts, 'leg_observed': True,
                }

            elif new_category == 'abroad':
                # Landed abroad — learn tier if we watched the whole outbound leg
                if prev_phase == PHASE_OUT and prev.get('leg_observed') and prev.get('leg_start'):
                    self._learn_tier(tid, name, prev.get('dest'), now_ts - prev['leg_start'])
                self.players[tid] = {
                    'name': name, 'phase': PHASE_ABROAD, 'dest': text_dest or prev.get('dest'),
                    'eta': None, 'leg_start': None, 'leg_observed': False,
                }

            else:  # home — landed back in Torn
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

        if changed:
            await self.save_data()

    async def _fetch_enemy_members(self) -> Optional[Dict]:
        """Fetch enemy faction roster, bypassing the API cache for fresh statuses."""
        data = await fetch_any_faction_data(self.enemy_faction_id, 'basic', cache_seconds=0)
        if not data:
            return None
        return data.get('members', {})

    # ===== Leg resolution: real travel data first, estimate as fallback =====

    async def _resolve_leg(self, torn_id: str, status: Dict, text_dest: Optional[str],
                           now_ts: float, prev: Optional[Dict] = None) -> Dict:
        """
        Resolve a newly-detected traveling member to {phase, dest, eta, note}.
        Tries the individual `travel` selection (exact time_left) first;
        falls back to text-based estimate if that comes back unusable.
        """
        travel = await self._fetch_travel(torn_id)
        if travel:
            time_left = travel.get('time_left') or 0
            raw_dest = travel.get('destination') or ''
            if time_left > 0 and raw_dest:
                eta = int(now_ts + time_left)
                if raw_dest.strip().lower() == 'torn':
                    # Returning: travel.destination says "Torn", not the origin —
                    # the country they're coming FROM is whatever we last saw them at.
                    origin = (prev or {}).get('dest') or text_dest
                    return {'phase': PHASE_RETURNING, 'dest': normalize_dest(origin) if origin else None,
                            'eta': eta, 'note': ''}
                return {'phase': PHASE_OUT, 'dest': normalize_dest(raw_dest), 'eta': eta, 'note': ''}

        # Fallback: estimate from text + known flight durations
        phase, dest = parse_phase(status)
        return self._estimate_leg(torn_id, phase, dest, now_ts, prev)

    def _estimate_leg(self, torn_id: str, phase: str, dest: Optional[str], now_ts: float,
                      prev: Optional[Dict] = None) -> Dict:
        """Estimate ETA from status text + duration table when real travel data is unavailable."""
        if phase == PHASE_RETURNING and not dest:
            dest = (prev or {}).get('dest')
        eta = self._eta_ts(torn_id, dest, now_ts)
        note = f' (estimate — {self._tier_note(torn_id)})' if eta else ''
        return {'phase': phase, 'dest': dest, 'eta': eta, 'note': note}

    async def _fetch_travel(self, torn_id: str) -> Optional[Dict]:
        """Individual travel-selection lookup — only called for currently-traveling members."""
        data = await fetch_player_data(torn_id, selections='travel', cache_seconds=0)
        if not data:
            return None
        return data.get('travel') or None

    # ===== ETA / tier logic (fallback path) =====

    def _tier_for(self, torn_id: str) -> Tuple[str, bool]:
        """Return (tier, learned) for a player — learned=False means assumption."""
        entry = self.tiers.get(str(torn_id))
        if entry and entry.get('tier') in TIER_MULTIPLIERS:
            return entry['tier'], True
        return DEFAULT_TIER, False

    def _eta_ts(self, torn_id: str, dest: Optional[str], leg_start: float) -> Optional[int]:
        """Estimated landing timestamp for a leg, or None if destination unknown."""
        std = STANDARD_FLIGHT_MINUTES.get(dest or '')
        if not std:
            return None
        tier, _ = self._tier_for(torn_id)
        return int(leg_start + std * TIER_MULTIPLIERS[tier] * 60)

    def _tier_note(self, torn_id: str) -> str:
        tier, learned = self._tier_for(torn_id)
        label = TIER_LABELS[tier]
        return f'{label} — learned' if learned else f'assuming {label}'

    def _learn_tier(self, torn_id: str, name: str, dest: Optional[str], elapsed_seconds: float):
        """Infer a player's travel tier from a fully-observed flight leg (fallback-path only —
        legs resolved via real travel data don't need this, since we already have ground truth)."""
        std = STANDARD_FLIGHT_MINUTES.get(dest or '')
        if not std or elapsed_seconds <= 0:
            return

        best_tier, best_err = None, None
        for tier, mult in TIER_MULTIPLIERS.items():
            expected = std * mult * 60
            err = abs(elapsed_seconds - expected) / expected
            if best_err is None or err < best_err:
                best_tier, best_err = tier, err

        # Reject measurements that don't sit near any tier (glitchy data)
        if best_err is None or best_err > 0.25:
            logger.info(f'Fly: tier learn rejected for {name} [{torn_id}] — '
                        f'{elapsed_seconds/60:.1f}min to {dest} matches no tier well')
            return

        old = self.tiers.get(str(torn_id), {}).get('tier')
        self.tiers[str(torn_id)] = {
            'tier': best_tier,
            'name': name,
            'observed_minutes': round(elapsed_seconds / 60, 1),
            'dest': dest,
            'ts': datetime.now(timezone.utc).timestamp(),
        }
        if old != best_tier:
            logger.info(f'Fly: learned tier for {name} [{torn_id}]: {best_tier} '
                        f'({elapsed_seconds/60:.1f}min to {dest})')

    # ===== Message formatting =====

    def _format_outbound_alert(self, dest: Optional[str], name: str, eta: Optional[int],
                               note: str = '') -> str:
        dest_label = dest or 'an unknown destination'
        if eta:
            return (f'✈️ **{name}** flew out to **{dest_label}** — '
                    f'arrive at <t:{eta}:t> (<t:{eta}:R>){note}. Watch out!')
        return f'✈️ **{name}** flew out to **{dest_label}** — flight time unknown. Watch out!'

    def _format_return_alert(self, torn_id: str, name: str, dest: Optional[str], eta: Optional[int],
                             note: str = '') -> str:
        dest_label = dest or 'abroad'
        attack = f'https://www.torn.com/loader.php?sid=attack&user2ID={torn_id}'
        if eta:
            return (f'🛬 **{name}** is returning from **{dest_label}** — '
                    f'lands at <t:{eta}:t> (<t:{eta}:R>){note}. '
                    f'[Attack]({attack})')
        return f'🛬 **{name}** is returning from **{dest_label}** — landing time unknown. [Attack]({attack})'

    def _format_latest_line(self, torn_id: str) -> str:
        """Line for a player spotted mid-flight at toggle-on time."""
        p = self.players.get(torn_id, {})
        name = p.get('name', torn_id)
        phase = p.get('phase')
        dest = p.get('dest')
        eta = p.get('eta')
        direction = f'→ {dest or "unknown destination"}' if phase == PHASE_OUT else f'← returning from {dest or "abroad"}'
        if eta:
            return f'**{name}** {direction} — lands at <t:{eta}:t> (<t:{eta}:R>)'
        return f'**{name}** {direction} — ETA unknown'

    async def _post(self, content: str = None, embed: discord.Embed = None):
        """Post to the war-tracker channel."""
        if not WAR_TRACKER_CHANNEL_ID:
            logger.warning('Fly: WAR_TRACKER_CHANNEL_ID not set — dropping alert')
            return
        channel = self.bot.get_channel(WAR_TRACKER_CHANNEL_ID)
        if not channel:
            logger.warning(f'Fly: channel {WAR_TRACKER_CHANNEL_ID} not found')
            return
        try:
            if content:
                # Discord message limit is 2000 chars — chunk on line boundaries
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
