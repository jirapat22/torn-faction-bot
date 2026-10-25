"""
Where everyone is, and what time it is for them.

Three deliberate choices:

1. No commands. Members pick a region from a dropdown in the channel - the
   same persistent-view mechanism the availability poll uses. Asking the
   faction to remember a command name is how you end up with four people
   registered and everyone else invisible.

2. The IANA zone is stored, never a UTC offset. An offset is correct for
   about five months at a time: the moment Europe or NZ moves its clocks,
   half a stored-offset board is an hour wrong and nobody notices until
   somebody misses a chain. zoneinfo recomputes it from the zone every time.

3. No image. An embed can be edited in place every 5 minutes for free, reflows on a
   phone, and is searchable; an image is a fresh upload every refresh and a
   thumbnail you have to tap. The clock emoji carry the visual weight - they
   really do show the hour hand in the right position.
"""

import logging
from datetime import datetime, timezone

import discord

from utils.helpers import create_embed, load_json, save_json
from config import EMBED_COLOR_INFO, TIMEZONE_CHANNEL_ID

logger = logging.getLogger(__name__)

STORE = 'data/timezones.json'

# Discord allows 25 options per menu, which is not enough for the world, so
# the picker is two menus. Labels are places people recognise, values are
# IANA zones so daylight saving is somebody else's problem.
REGIONS_WEST = [
    ('\U0001f1fa\U0001f1f8', 'US - Pacific', 'America/Los_Angeles'),
    ('\U0001f1fa\U0001f1f8', 'US - Mountain', 'America/Denver'),
    ('\U0001f1fa\U0001f1f8', 'US - Central', 'America/Chicago'),
    ('\U0001f1fa\U0001f1f8', 'US - Eastern', 'America/New_York'),
    ('\U0001f1fa\U0001f1f8', 'Alaska', 'America/Anchorage'),
    ('\U0001f1fa\U0001f1f8', 'Hawaii', 'Pacific/Honolulu'),
    ('\U0001f1e8\U0001f1e6', 'Canada - Atlantic', 'America/Halifax'),
    # UTC-4 all year. Bonaire, Curacao, Aruba and Puerto Rico match US Eastern
    # in summer and drift an hour off it in winter, so neither "US - Eastern"
    # nor "Canada - Atlantic" (both shift) is right for them. Kralendijk is a
    # tzdata alias of Puerto_Rico, so one zone covers the lot.
    ('\U0001f334', 'Caribbean (no DST)', 'America/Puerto_Rico'),
    ('\U0001f1f2\U0001f1fd', 'Mexico', 'America/Mexico_City'),
    ('\U0001f1e8\U0001f1f4', 'Colombia / Peru', 'America/Bogota'),
    ('\U0001f1e7\U0001f1f7', 'Brazil', 'America/Sao_Paulo'),
    ('\U0001f1e6\U0001f1f7', 'Argentina', 'America/Argentina/Buenos_Aires'),
    ('\U0001f1e8\U0001f1f1', 'Chile', 'America/Santiago'),
    ('\U0001f1ec\U0001f1e7', 'UK / Ireland', 'Europe/London'),
    ('\U0001f1f5\U0001f1f9', 'Portugal', 'Europe/Lisbon'),
    ('\U0001f1ea\U0001f1fa', 'Europe - West', 'Europe/Paris'),
    ('\U0001f1ea\U0001f1fa', 'Europe - East', 'Europe/Athens'),
    ('\U0001f1f9\U0001f1f7', 'Turkiye', 'Europe/Istanbul'),
    ('\U0001f1f7\U0001f1fa', 'Russia - Moscow', 'Europe/Moscow'),
    ('\U0001f1f3\U0001f1ec', 'Africa - West', 'Africa/Lagos'),
    ('\U0001f1ea\U0001f1ec', 'Egypt', 'Africa/Cairo'),
    ('\U0001f1f0\U0001f1ea', 'Africa - East', 'Africa/Nairobi'),
    ('\U0001f1ff\U0001f1e6', 'Africa - South', 'Africa/Johannesburg'),
]

REGIONS_EAST = [
    ('\U0001f1e6\U0001f1ea', 'UAE / Gulf', 'Asia/Dubai'),
    ('\U0001f1f8\U0001f1e6', 'Saudi Arabia', 'Asia/Riyadh'),
    ('\U0001f1ee\U0001f1f7', 'Iran', 'Asia/Tehran'),
    ('\U0001f1f5\U0001f1f0', 'Pakistan', 'Asia/Karachi'),
    ('\U0001f1ee\U0001f1f3', 'India', 'Asia/Kolkata'),
    ('\U0001f1e7\U0001f1e9', 'Bangladesh', 'Asia/Dhaka'),
    ('\U0001f1f9\U0001f1ed', 'Thailand / Vietnam', 'Asia/Bangkok'),
    ('\U0001f1ee\U0001f1e9', 'Indonesia - West', 'Asia/Jakarta'),
    ('\U0001f1f8\U0001f1ec', 'Singapore / Malaysia', 'Asia/Singapore'),
    ('\U0001f1e8\U0001f1f3', 'China', 'Asia/Shanghai'),
    ('\U0001f1ed\U0001f1f0', 'Hong Kong / Taiwan', 'Asia/Hong_Kong'),
    ('\U0001f1f5\U0001f1ed', 'Philippines', 'Asia/Manila'),
    ('\U0001f1ef\U0001f1f5', 'Japan', 'Asia/Tokyo'),
    ('\U0001f1f0\U0001f1f7', 'South Korea', 'Asia/Seoul'),
    ('\U0001f1e6\U0001f1fa', 'Australia - West', 'Australia/Perth'),
    ('\U0001f1e6\U0001f1fa', 'Australia - Central', 'Australia/Adelaide'),
    ('\U0001f1e6\U0001f1fa', 'Australia - Brisbane', 'Australia/Brisbane'),
    ('\U0001f1e6\U0001f1fa', 'Australia - Sydney / Melbourne', 'Australia/Sydney'),
    ('\U0001f1f3\U0001f1ff', 'New Zealand', 'Pacific/Auckland'),
    ('\U0001f1eb\U0001f1ef', 'Fiji', 'Pacific/Fiji'),
    ('\U0001f1f5\U0001f1ec', 'Papua New Guinea', 'Pacific/Port_Moresby'),
]

ALL_REGIONS = REGIONS_WEST + REGIONS_EAST
BY_ZONE = {zone: (emoji, label) for emoji, label, zone in ALL_REGIONS}

# 🕐..🕧 - Discord renders these with the hand in the right place, which is
# the whole reason this board needs no picture.
CLOCKS = ['\U0001f55b', '\U0001f550', '\U0001f551', '\U0001f552', '\U0001f553',
          '\U0001f554', '\U0001f555', '\U0001f556', '\U0001f557', '\U0001f558',
          '\U0001f559', '\U0001f55a']
HALF = ['\U0001f567', '\U0001f55c', '\U0001f55d', '\U0001f55e', '\U0001f55f',
        '\U0001f560', '\U0001f561', '\U0001f562', '\U0001f563', '\U0001f564',
        '\U0001f565', '\U0001f566']

SPARK = '▁▂▃▄▅▆▇█'


def friendly_time(dt):
    """
    '4:18 pm'. Written by hand because the obvious strftime for it is
    platform-specific: %-I is Linux only and %#I is Windows only, so either
    one breaks half the places this runs.
    """
    hour = dt.hour % 12 or 12
    ampm = 'am' if dt.hour < 12 else 'pm'
    return f'{hour}:{dt.minute:02d} {ampm}'


def clock_emoji(hour, minute=0):
    """The clock face whose hands match this time, to the nearest half hour."""
    idx = hour % 12
    return HALF[idx] if 15 <= minute < 45 else CLOCKS[(idx + (minute >= 45)) % 12]


def sparkline(curve):
    """
    A 24-character day in one line. curve is {hour: 0..1}.

    Scaled against the busiest hour rather than against 1.0: what matters is
    the SHAPE of the day - when this faction is around and when it is not -
    and an absolute scale just renders every hour as a low bar.
    """
    if not curve:
        return ''
    top = max(curve.values()) or 1
    out = []
    for h in range(24):
        v = max(0.0, min(1.0, (curve.get(h, 0) or 0) / top))
        # Floor, not round: only the busiest hour should reach a full
        # block, and a near-dead hour should look near-dead rather
        # than being rounded up a step.
        out.append(SPARK[min(len(SPARK) - 1, int(v * (len(SPARK) - 1)))])
    return ''.join(out)


def local_now(zone, now=None):
    """Current time in that zone, or None when the zone is unknown."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    now = now or datetime.now(timezone.utc)
    try:
        return now.astimezone(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


# Waking hours by the clock on their own wall. Generous at both ends on
# purpose: this answers "is it rude to ping them", not "are they at a desk".
WAKING_START_HOUR = 8
WAKING_END_HOUR = 23


def is_waking(local):
    """
    Is it a civil hour where this person is?

    The board exists so chains get called at hours people are awake, so awake
    has to mean their wall clock - not whether they happened to click Torn in
    the last quarter of an hour. Measured on five registered members, the old
    definition read "0 of 5 awake" while three of them were plainly mid
    afternoon, which makes the headline look broken rather than informative.
    """
    return WAKING_START_HOUR <= local.hour < WAKING_END_HOUR


def group_by_region(store, active_ids=(), now=None):
    """
    Turn {discord_id: {...}} into one row per region, east to west.

    Pure, so the board can be tested without Discord or a clock. Returns a
    list of dicts: zone, emoji, label, local (datetime), offset_hours,
    members [(discord_id, name)], awake (int, from the local clock), active
    (int, acted on Torn in the last 15 minutes).
    """
    now = now or datetime.now(timezone.utc)
    active = {str(a) for a in (active_ids or ())}
    rows = {}
    for did, rec in (store or {}).items():
        if str(did).startswith('_'):
            continue            # bookkeeping keys
        zone = (rec or {}).get('zone')
        if not zone:
            continue
        local = local_now(zone, now)
        if local is None:
            continue
        emoji, label = BY_ZONE.get(zone, ('\U0001f30d', zone))
        row = rows.setdefault(zone, {
            'zone': zone, 'emoji': emoji, 'label': label, 'local': local,
            'offset_hours': local.utcoffset().total_seconds() / 3600,
            'members': [], 'awake': 0, 'active': 0,
        })
        row['members'].append((str(did), (rec or {}).get('name', str(did))))
        if str(did) in active:
            row['active'] += 1
    # Awake is a property of the zone's clock rather than of the individual,
    # so it is filled once per row instead of counted per member.
    for row in rows.values():
        row['awake'] = len(row['members']) if is_waking(row['local']) else 0
    # East first: the day starts there, so the board reads like the sun moving
    return sorted(rows.values(), key=lambda r: -r['offset_hours'])


def build_embed(groups, active_names=(), total_members=0, curve=None,
                best=None, quiet=None, unset=0):
    """
    Render the board. Pure. Returns one embed.

    Region cards are inline fields on purpose: Discord lays them out three
    across on a desktop and reflows to one or two on a phone, which is where
    most of the faction will read this. A hand-drawn table would scroll
    sideways there and look broken.
    """
    known = sum(len(g['members']) for g in groups)
    awake_total = sum(g['awake'] for g in groups)

    desc = [f'**{awake_total}** of **{known}** in waking hours '
            f'({WAKING_START_HOUR:02d}:00–{WAKING_END_HOUR:02d}:00 '
            f'their time).']
    if active_names:
        shown = ' · '.join(active_names[:12])
        more = len(active_names) - 12
        desc.append(f'\U0001f7e2 **{len(active_names)}** on Torn in the last '
                    f'15 min: {shown}'
                    + (f' **+{more}**' if more > 0 else ''))
    if curve:
        line = sparkline(curve)
        desc.append(f'```\n{line}\n00    06    12    18   23  (TCT)\n```')
    if best:
        desc.append(f'-# Busiest **{best}** · quietest **{quiet}** (TCT)'
                    if quiet else f'-# Busiest **{best}** (TCT)')
    if unset:
        desc.append(f'-# **{unset}** not on the board yet - pick your '
                    f'region from the menu above and you appear here.')

    embed = create_embed(title='\U0001f30d Where everyone is',
                         description='\n\n'.join(desc)[:4000],
                         color=EMBED_COLOR_INFO)

    # 25 fields is Discord's cap; a faction spread over more than 24 regions
    # is not a problem anybody has.
    for g in groups[:24]:
        local = g['local']
        names = ', '.join(n for _d, n in g['members'][:4])
        if len(g['members']) > 4:
            names += f' +{len(g["members"]) - 4}'
        dots = ('\U0001f7e2' * g['awake']) + ('⚫' * (len(g['members']) - g['awake']))
        embed.add_field(
            name=f'{g["emoji"]} {g["label"]}',
            value=(f'{clock_emoji(local.hour, local.minute)} '
                   f'**{friendly_time(local)}**\n{dots[:12]}\n-# {names}'),
            inline=True)
    return embed


# ---------------------------------------------------------------- picker

class RegionSelect(discord.ui.Select):
    """One of the two menus. custom_id is fixed so it survives restarts."""

    def __init__(self, regions, placeholder, custom_id):
        super().__init__(placeholder=placeholder, custom_id=custom_id,
                         min_values=1, max_values=1,
                         options=[discord.SelectOption(label=label, value=zone,
                                                       emoji=emoji)
                                  for emoji, label, zone in regions])

    async def callback(self, interaction: discord.Interaction):
        zone = self.values[0]
        emoji, label = BY_ZONE.get(zone, ('\U0001f30d', zone))
        store = load_json(STORE, default={}) or {}
        did = str(interaction.user.id)
        store[did] = {
            'zone': zone,
            'name': interaction.user.display_name,
            'set_at': datetime.now(timezone.utc).timestamp(),
        }
        save_json(STORE, store)

        local = local_now(zone)
        when = friendly_time(local) if local else '?'
        # Ephemeral: 35 people picking a region should not be 35 messages in
        # the channel. The board is where it shows up.
        await interaction.response.send_message(
            f'{emoji} Saved - you are on the board as **{label}**, '
            f'where it is now **{when}**.\n'
            f'-# Moved? Just pick again, it overwrites.', ephemeral=True)

        try:
            await refresh_board(interaction.client)
        except Exception as e:
            logger.error(f'Timezones: board refresh after pick failed: {e}')


class TimezonePicker(discord.ui.View):
    """Persistent: no timeout, fixed custom_ids, re-registered on startup."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RegionSelect(REGIONS_WEST,
                                   'Americas, Europe, Africa...',
                                   'tz:pick:west'))
        self.add_item(RegionSelect(REGIONS_EAST,
                                   'Asia, Middle East, Pacific...',
                                   'tz:pick:east'))


def picker_embed():
    return create_embed(
        title='\U0001f30d Put yourself on the map',
        description=(
            'Pick where you are from a menu below. One click, nothing to '
            'type, and you only do it once.\n\n'
            'It puts you on the board underneath, so everyone can see what '
            'time it is for you before they ping you at 4am - and so chains '
            'and wars get called at hours people are actually awake.\n\n'
            '-# Clocks change by themselves. Pick the place, not the offset, '
            'and daylight saving sorts itself out.'),
        color=EMBED_COLOR_INFO)


# ---------------------------------------------------------------- posting

async def _bot_messages(channel, bot, title):
    out = []
    async for msg in channel.history(limit=50):
        if (bot.user and msg.author.id == bot.user.id and msg.embeds
                and (msg.embeds[0].title or '') == title):
            out.append(msg)
    return out


async def post_picker(bot, channel_id=None, force=False):
    """
    The pinned picker. Posted once and left alone - re-posting it would only
    push the board down the channel.

    Its menus are stored by Discord with the message, though, so a region
    added in code would never show up on the old post. On startup the existing
    picker gets the current menus edited in place, which keeps its position.
    """
    channel_id = channel_id or TIMEZONE_CHANNEL_ID
    if not channel_id or bot is None:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning('Timezones: channel not found')
        return None

    embed = picker_embed()
    try:
        existing = await _bot_messages(channel, bot, embed.title)
    except Exception as e:
        logger.error(f'Timezones: cannot read channel: {e}')
        return None
    if existing and not force:
        try:
            # history() is newest first, so this is the live picker
            await existing[0].edit(view=TimezonePicker())
        except Exception as e:
            logger.error(f'Timezones: could not refresh the picker menus: {e}')
        return 'unchanged'

    message = await channel.send(embed=embed, view=TimezonePicker())
    try:
        await message.pin()
    except Exception as e:
        logger.error(f'Timezones: could not pin the picker: {e}')
    for old in existing:
        try:
            await old.delete()
        except Exception:
            pass
    return 'posted'


async def refresh_board(bot, channel_id=None, roster=None, profile=None):
    """
    Edit the board in place with the current clocks.

    Edited, not re-posted: reposting every 5 minutes would bury the picker
    and spam the channel. Returns 'edited', 'posted', or None.
    """
    channel_id = channel_id or TIMEZONE_CHANNEL_ID
    if not channel_id or bot is None:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        return None

    store = load_json(STORE, default={}) or {}

    # Active = acted on Torn in the last 15 minutes, which is a different
    # question from awake (see is_waking) and must not be confused with it:
    # reporting "0 of 5 awake" at 4pm is how this went wrong once already.
    # Discord presence would be easier but means nothing here - plenty of
    # people idle in Discord all day and never open Torn.
    awake_ids, roster_size = set(), 0
    if roster is None:
        from utils.torn_api import fetch_faction_members
        roster = await fetch_faction_members()
    if roster:
        roster_size = len(roster)
        now_ts = datetime.now(timezone.utc).timestamp()
        tid_to_did = {}
        for did, rec in store.items():
            if not str(did).startswith('_'):
                tid_to_did[str((rec or {}).get('torn_id'))] = str(did)
        for tid, m in roster.items():
            la = (m.get('last_action') or {}).get('timestamp')
            if la and (now_ts - int(la)) <= 15 * 60:
                did = tid_to_did.get(str(tid))
                if did:
                    awake_ids.add(did)

    groups = group_by_region(store, active_ids=awake_ids)
    active_names = [n for g in groups for d, n in g['members'] if d in awake_ids]

    curve, best, quiet = None, None, None
    if profile is not None:
        try:
            curve = profile.hourly_curve()
            cov = profile.get_coverage()
            if cov:
                (b0, b1, _p), (q0, q1, _q), _n = cov
                best = f'{b0:02d}:00-{b1:02d}:00'
                quiet = f'{q0:02d}:00-{q1:02d}:00'
        except Exception as e:
            logger.debug(f'Timezones: no activity curve: {e}')

    known = sum(len(g['members']) for g in groups)
    embed = build_embed(groups, active_names=active_names,
                        total_members=roster_size, curve=curve,
                        best=best, quiet=quiet,
                        unset=max(0, roster_size - known))

    try:
        existing = await _bot_messages(channel, bot, embed.title)
    except Exception as e:
        logger.error(f'Timezones: cannot read channel: {e}')
        return None

    if existing:
        try:
            await existing[0].edit(embed=embed)
            for extra in existing[1:]:
                await extra.delete()
            return 'edited'
        except Exception as e:
            logger.error(f'Timezones: edit failed: {e}')
            return None

    await channel.send(embed=embed)
    return 'posted'
