"""
Fly Tracking Module
Track enemy faction flights during ranked war and estimate landing times.

The Torn API's `travel` selection (user/{id}?selections=travel) only works
for the key owner's own ID — querying anyone else returns API error 7
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
from utils.torn_api import fetch_any_faction_data

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

    Returns (phase, destination) — destination is None when at home or unknown.
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
        logger.warning(f"Fly: couldn't parse destination for {name} [{torn_id}] — "
                       f"state={state!r} description={desc!r} details={details!r}")
        return PHASE_OUT, None

    if state == 'Abroad':
        if desc_lower.startswith('in '):
            return PHASE_ABROAD, normalize_dest(desc_lower[3:])
        logger.warning(f"Fly: couldn't parse destination for {name} [{torn_id}] — "
                       f"state={state!r} description={desc!r} details={details!r}")
        return PHASE_ABROAD, None

    # Abroad hospital: "In a Swiss hospital ..." (home hospital is just "In hospital")
    if state == 'Hospital' and desc_lower.startswith('in a') and 'hospital' in desc_lower:
        for adj, country in HOSPITAL_ADJECTIVES.items():
            if adj in desc_lower:
                return PHASE_ABROAD, country
        logger.warning(f"Fly: couldn't parse hospital country for {name} [{torn_id}] — "
                       f"state={state!r} description={desc!r} details={details!r}")
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
                    line = self._format_group_line(name, resolved['phase'], resolved['dest'], resolved['eta'])
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
                name=f'🛬 Returning — attack window ({len(returning_lines)})',
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
                name=f'🌍 Abroad — landed ({len(abroad_lines)})',
                value='\n'.join(abroad_lines[:10]) + ('\n…' if len(abroad_lines) > 10 else ''),
                inline=False
            )
        if not returning_lines and not outbound_lines and not abroad_lines:
            embed.add_field(name='Status', value='Nobody abroad — everyone is in Torn.', inline=False)
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
                description='Could not fetch the enemy roster right now — try again shortly.',
                color=EMBED_COLOR_WARNING
            )

        returning_lines, outbound_lines, abroad_entries = [], [], []
        now_ts = datetime.now(timezone.utc).timestamp()
        for tid, m in members.items():
            tid = str(tid)
            name = m.get('name', tid)
            phase, dest = parse_phase(m.get('status', {}), tid, name)

            if phase in (PHASE_OUT, PHASE_RETURNING):
                # Reuse the already leg_start-anchored eta from check_flights()
                # whenever it's for this exact same leg — recomputing from "now"
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
                line = self._format_group_line(name, r_phase, r_dest, r_eta)
                entry = (r_eta or float('inf'), line)
                (returning_lines if r_phase == PHASE_RETURNING else outbound_lines).append(entry)
            elif phase == PHASE_ABROAD:
                abroad_entries.append((name, self._format_abroad_line(name, dest)))

        returning_lines.sort(key=lambda e: e[0])
        outbound_lines.sort(key=lambda e: e[0])
        abroad_entries.sort(key=lambda e: e[0].lower())
        abroad_lines = [line for _, line in abroad_entries]

        embed = create_embed(
            title=f'📡 Fly Status: {self.enemy_faction_name}',
            description='Live snapshot — tracking keeps running in the background regardless.',
            color=EMBED_COLOR_INFO
        )
        if returning_lines:
            embed.add_field(
                name=f'🛬 Returning — attack window ({len(returning_lines)})',
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
                name=f'🌍 Abroad — landed ({len(abroad_lines)})',
                value='\n'.join(abroad_lines[:10]) + ('\n…' if len(abroad_lines) > 10 else ''),
                inline=False
            )
        if not returning_lines and not outbound_lines and not abroad_lines:
            embed.add_field(name='Status', value='Nobody abroad — everyone is in Torn.', inline=False)
        return embed

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

        for tid, m in members.items():
            tid = str(tid)
            name = m.get('name', tid)
            status = m.get('status', {})
            text_phase, text_dest = parse_phase(status, tid, name)

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

            # Compare the exact fine-grained phase, not a coarse OUT/RETURNING-
            # collapsed category — otherwise a fast turnaround (land abroad and
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

                if resolved['phase'] == PHASE_OUT:
                    alerts.append(self._format_outbound_alert(resolved['dest'], name, resolved['eta'], resolved['note']))
                else:
                    alerts.append(self._format_return_alert(tid, name, resolved['dest'], resolved['eta'], resolved['note']))

                self.players[tid] = {
                    'name': name, 'phase': resolved['phase'], 'dest': resolved['dest'],
                    'eta': resolved['eta'], 'leg_start': now_ts, 'leg_observed': True,
                }

            elif text_phase == PHASE_ABROAD:
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

    # ===== Leg estimation =====

    def _estimate_leg(self, torn_id: str, phase: str, dest: Optional[str], now_ts: float,
                      prev: Optional[Dict] = None) -> Dict:
        """Estimate ETA from status text + known flight-duration table."""
        if phase == PHASE_RETURNING and not dest:
            dest = (prev or {}).get('dest')
        eta = self._eta_ts(torn_id, dest, now_ts)
        note = f' (estimate — {self._tier_note(torn_id)})' if eta else ''
        return {'phase': phase, 'dest': dest, 'eta': eta, 'note': note}

    # ===== ETA / tier logic =====

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
        """Infer a player's travel tier from a fully-observed flight leg (departure and
        arrival both caught live), refining future ETA estimates for that player."""
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
            return (f'✈️ **{name}** → **{dest_label}** — '
                    f'lands <t:{eta}:t> (<t:{eta}:R>){note}. Watch out!')
        return f'✈️ **{name}** → **{dest_label}** — flight time unknown. Watch out!'

    def _format_return_alert(self, torn_id: str, name: str, dest: Optional[str], eta: Optional[int],
                             note: str = '') -> str:
        dest_label = dest or 'abroad'
        attack = f'https://www.torn.com/loader.php?sid=attack&user2ID={torn_id}'
        if eta:
            return (f'🛬 **{name}** ← **{dest_label}** — '
                    f'lands <t:{eta}:t> (<t:{eta}:R>){note}. '
                    f'[Attack]({attack})')
        return f'🛬 **{name}** ← **{dest_label}** — landing time unknown. [Attack]({attack})'

    def _format_group_line(self, name: str, phase: str, dest: Optional[str], eta: Optional[int]) -> str:
        """One row for the grouped Returning/Outbound lists. Takes the fields
        directly (not a self.players lookup) so it's safe to use for a
        read-only status peek that must not touch persisted tracking state."""
        arrow = '→' if phase == PHASE_OUT else '←'
        dest_label = dest or 'unknown'
        if eta:
            return f'**{name}** {arrow} **{dest_label}** — <t:{eta}:t> (<t:{eta}:R>)'
        return f'**{name}** {arrow} **{dest_label}** — ETA unknown'

    def _format_abroad_line(self, name: str, dest: Optional[str]) -> str:
        """One row for the Abroad (landed, not currently flying) list."""
        return f'**{name}** — {dest or "unknown"}'

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
