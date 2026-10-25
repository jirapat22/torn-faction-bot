"""
Targets and retals: one channel, two jobs.

1. A pinned TARGET BOARD of people the faction wants hit. Any verified member
   can add someone (up to PER_MEMBER_CAP each), entries drop off after
   TARGET_DAYS, and the bot edits the board in place every couple of minutes
   with each target's status and a rough stat estimate.

2. RETAL messages. Torn pays a retaliation bonus for hitting someone within
   five minutes of them hitting one of us, so the bot reads the faction attack
   log every 30s and posts each attacker it sees. The message deletes itself
   when the window closes, so the channel cleans up after itself.

Decisions that are easy to undo by accident:

- One message per ATTACKER, not per hit. The bonus is about the attacker
  ("hit someone who hit us in the last 5 min"), so three hits by one person is
  one retal with a window that runs from their latest hit.
- The countdown runs from when the hit ENDED, not from when the bot noticed.
  A message posted 30s late still disappears exactly when the bonus does.
- Stealthed hits are skipped: the log hides the attacker, and a retal you
  cannot aim is noise. Measured on the live log, most incoming hits are
  stealthed, so a quiet channel is expected, not a fault.
- Only hits that LANDED post (hospitalized, mugged, attacked-and-left). An
  attacker who lost took nothing from us.
- Stats come from FFScouter's estimates, a rough guide. A missing estimate
  says "unknown" rather than pretending to be a number.
"""

import asyncio
import logging
import re
import time

import discord
from discord import app_commands

from utils.helpers import load_json, save_json, create_embed, is_management
from utils.torn_api import (fetch_faction_attacks, fetch_player_data,
                            fetch_ffscouter_stats)
from config import (TORN_FACTION_ID, WAR_CHANNEL_ID, VERIFIED_ROLE_NAME, GUILD_ID,
                    EMBED_COLOR_INFO, EMBED_COLOR_SUCCESS, EMBED_COLOR_WARNING,
                    EMBED_COLOR_CRITICAL)

logger = logging.getLogger(__name__)

STORE = 'data/targets.json'
CHANNEL_NAME = 'targets-and-retals'

RETAL_WINDOW = 300          # Torn's retaliation bonus lasts 5 minutes
TARGET_DAYS = 7
PER_MEMBER_CAP = 5
REASON_MAX = 80

# Results where the attacker actually took something from us. 'Lost',
# 'Stalemate', 'Escape' and friends are attacks we won or nobody did.
LANDED = {'Hospitalized', 'Mugged', 'Attacked'}

BOARD_TITLE = '\U0001f3af Target board'
RETAL_PREFIX = '⚔️ Retal'
RETALIATED_PREFIX = '✅ Retaliated'

ADD_BUTTON_ID = 'tgt:retal:add'


def profile_url(torn_id):
    return f'https://www.torn.com/profiles.php?XID={torn_id}'


def attack_url(torn_id):
    return f'https://www.torn.com/page.php?sid=attack&user2ID={torn_id}'


# ------------------------------------------------------------ pure helpers
# Everything here is free of Discord and the network so the rules are tested
# directly, the same way the rest of the repo tests its decision logic.

def parse_torn_id(text):
    """
    A Torn id from whatever a member pastes: a bare number, a profile link,
    an attack link, or Torn's own "Name [1234567]" format. None if no id.
    """
    text = (text or '').strip()
    if text.isdigit():
        return int(text)
    match = re.search(r'(?:XID=|user2ID=)(\d+)', text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r'\[(\d+)\]', text)
    if match:
        return int(match.group(1))
    return None


def landed_hits(attacks, faction_id):
    """
    Incoming hits worth a retal message, oldest first.

    Skips hits on other factions, stealthed hits (no attacker to aim at),
    faction mates sparring with each other, and attacks that did not land.
    """
    out = []
    fid = str(faction_id)
    for code, a in (attacks or {}).items():
        if str(a.get('defender_faction')) != fid:
            continue
        if a.get('stealthed') or not a.get('attacker_id'):
            continue
        if str(a.get('attacker_faction')) == fid:
            continue
        if a.get('result') not in LANDED:
            continue
        out.append({
            'code': a.get('code') or code,
            'attacker_id': str(a['attacker_id']),
            'attacker_name': a.get('attacker_name') or str(a['attacker_id']),
            'defender_name': a.get('defender_name') or str(a.get('defender_id')),
            'result': a['result'],
            'ended': int(a.get('timestamp_ended') or 0),
        })
    out.sort(key=lambda h: h['ended'])
    return out


def merge_hit(retals, hit):
    """
    Fold one hit into the open retal for that attacker. Returns True when
    something changed (a new attacker, or a new hit on an existing one).

    A fresh hit re-opens a retal that somebody already answered: the attacker
    went again, so there is a new bonus to collect.
    """
    key = hit['attacker_id']
    retal = retals.get(key)
    if retal is None:
        retal = {'attacker_id': key, 'attacker_name': hit['attacker_name'],
                 'hits': [], 'codes': [], 'expires': 0,
                 'message_id': None, 'retaliated_by': None}
        retals[key] = retal
    if hit['code'] in retal['codes']:
        return False
    retal['codes'].append(hit['code'])
    retal['hits'].append({'defender_name': hit['defender_name'],
                          'result': hit['result'], 'ended': hit['ended']})
    retal['expires'] = max(retal['expires'], hit['ended'] + RETAL_WINDOW)
    retal['attacker_name'] = hit['attacker_name']
    retal['retaliated_by'] = None
    return True


def find_retaliation(attacks, faction_id, attacker_id, since):
    """
    Name of the first of our members to land a hit on attacker_id at or after
    `since`, or None. Read from the same attack log page, so it costs no call.
    """
    fid = str(faction_id)
    best = None
    for a in (attacks or {}).values():
        if str(a.get('attacker_faction')) != fid:
            continue
        if str(a.get('defender_id')) != str(attacker_id):
            continue
        if a.get('result') not in LANDED:
            continue
        ended = int(a.get('timestamp_ended') or 0)
        if ended < since:
            continue
        if best is None or ended < best[0]:
            best = (ended, a.get('attacker_name') or str(a.get('attacker_id')))
    return best[1] if best else None


# Board sections, in the order they are shown: hittable first.
SECTIONS = [
    (0, '\U0001f7e2 Hit now'),
    (1, '\U0001f534 Hospital'),
    (2, '\U0001f535 Abroad'),
    (3, '\u2708\ufe0f Flying'),
    (4, '\u26d3\ufe0f Jail'),
    (5, '\u26aa Status unavailable'),
    (6, '\u26ab Fallen'),
]


def status_line(profile, now):
    """
    ((section, until), detail) for a target's Torn status. The section picks
    the board group (see SECTIONS); `until` orders hospital and jail by who is
    out soonest; `detail` is the short tail of the line ('' when there is
    nothing to add, as for someone who is simply okay).
    """
    status = (profile or {}).get('status') or {}
    state = status.get('state')
    until = int(status.get('until') or 0)
    desc = status.get('description') or ''
    if state == 'Okay':
        return (0, 0), ''
    if state == 'Hospital':
        if until and until > now:
            return (1, until), f'out <t:{until}:R>'
        return (1, now), 'out any moment'
    if state == 'Abroad':
        return (2, 0), desc.replace('In ', 'in ', 1)
    if state == 'Traveling':
        return (3, until), desc.replace('Traveling ', '', 1)
    if state in ('Jail', 'Federal'):
        return (4, until), f'out <t:{until}:R>' if until > now else desc
    if state == 'Fallen':
        return (6, 0), ''
    return (5, 0), ''


def stats_line(ff, now):
    """'~1.2m stats (est. 3 days ago)', or 'stats unknown' when FFScouter has
    nothing. Never a made-up number."""
    if not ff or not ff.get('bs_estimate'):
        return 'stats unknown'
    human = ff.get('bs_estimate_human') or f"{int(ff['bs_estimate']):,}"
    updated = int(ff.get('last_updated') or 0)
    if not updated:
        return f'~{human} stats'
    days = max(0, int((now - updated) // 86400))
    age = 'today' if days == 0 else ('1 day ago' if days == 1 else f'{days} days ago')
    return f'~{human} stats (est. {age})'


def prune_targets(targets, now):
    """Drop targets older than TARGET_DAYS. Returns the removed names."""
    cutoff = now - TARGET_DAYS * 86400
    gone = [tid for tid, t in targets.items() if int(t.get('added_at') or 0) <= cutoff]
    return [targets.pop(tid).get('name', tid) for tid in gone]


def count_added_by(targets, user_id):
    return sum(1 for t in targets.values() if str(t.get('added_by')) == str(user_id))


def build_board_embed(targets, profiles, ff, now):
    """
    The pinned board: one short line per target, grouped by status so "who
    can I hit right now" is always the first block. Weakest first inside a
    group (hospital and jail by who is out soonest). The name is the attack
    link; who added it and why live in /target action:List, because mid-fight
    nobody reads them and they doubled the board's height.

    `profiles` is {torn_id: profile or None}, `ff` the FFScouter dict.
    """
    groups = {}
    for tid, t in targets.items():
        prof = profiles.get(tid)
        (section, until), detail = status_line(prof, now)
        est = (ff.get(tid) or {}).get('bs_estimate')
        # unknown stats sort after every known one, never as "weakest"
        weak_key = (est is None, est or 0)
        order = (until, weak_key) if section in (1, 4) else (weak_key,)
        name = (prof or {}).get('name') or t.get('name') or tid
        level = (prof or {}).get('level') or t.get('level')
        human = (ff.get(tid) or {}).get('bs_estimate_human') if est else None
        parts = [f'[{name}]({attack_url(tid)})']
        if level:
            parts.append(f'Lv{level}')
        parts.append(human or 'stats ?')
        if detail:
            parts.append(detail)
        groups.setdefault(section, []).append((order, ' \u00b7 '.join(parts)))

    blocks, shown = [], 0
    # Discord caps the description at 4096 chars; stop adding lines well
    # before that so an oversized board shortens instead of failing to post.
    budget = 3800
    for section, label in SECTIONS:
        rows = sorted(groups.get(section, []), key=lambda r: r[0])
        if not rows:
            continue
        lines = [f'**{label}**']
        for _order, line in rows:
            if budget - len(line) - 1 < 0:
                break
            budget -= len(line) + 1
            lines.append(line)
            shown += 1
        if len(lines) > 1:
            blocks.append('\n'.join(lines))

    if not targets:
        body = ('Nobody yet. Add someone with `/target action:Add`, '
                'or tap "Add to targets" on a retal.')
    else:
        body = '\n\n'.join(blocks)
    body += (f'\n\n-# Add your own with /target action:Add. '
             f'Entries drop off after {TARGET_DAYS} days.')
    embed = create_embed(title=BOARD_TITLE, description=body, color=EMBED_COLOR_WARNING)

    total = len(targets)
    footer = f'{total} target{"s" if total != 1 else ""} \u00b7 tap a name to attack'
    if total > shown:
        footer += f' \u00b7 {total - shown} more not shown, see /target action:List'
    embed.set_footer(text=footer + ' \u00b7 stats are FFScouter estimates, a rough guide')
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_retal_embed(retal, ff, now):
    attacker = f"{retal['attacker_name']} [{retal['attacker_id']}]"
    hits = ', '.join(f"{h['result']} **{h['defender_name']}**" for h in retal['hits'][-5:])
    if retal.get('retaliated_by'):
        title = f"{RETALIATED_PREFIX} by {retal['retaliated_by']}"
        color = EMBED_COLOR_SUCCESS
        closing = 'Done. No need for anyone else to hit them.'
    else:
        title = f'{RETAL_PREFIX}: {attacker}'
        color = EMBED_COLOR_CRITICAL
        closing = f"Window closes <t:{retal['expires']}:R>"
    embed = create_embed(
        title=title[:256],
        description=(f'[{attacker}]({profile_url(retal["attacker_id"])}) hit us: {hits}\n'
                     f'{stats_line(ff, now)}\n{closing}'),
        color=color)
    embed.set_footer(text='This message deletes itself when the 5 minutes are up.')
    return embed


# ------------------------------------------------------------ Discord bits

class AddFromRetalButton(discord.ui.Button):
    """Puts the attacker on the board as added by whoever tapped."""

    def __init__(self, manager):
        super().__init__(label='Add to targets', style=discord.ButtonStyle.secondary,
                         custom_id=ADD_BUTTON_ID)
        self.manager = manager

    async def callback(self, interaction: discord.Interaction):
        # The button carries no id of its own (one fixed custom_id keeps a
        # single registration working for every retal), so look the attacker
        # up from the message it sits on.
        retal = self.manager.retal_for_message(interaction.message.id if interaction.message else None)
        if retal is None:
            await interaction.response.send_message(
                'That retal has closed. Use `/target action:Add` instead.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await self.manager.add_target(interaction, int(retal['attacker_id']),
                                            'from a retal')
        await interaction.followup.send(msg, ephemeral=True)


class RetalView(discord.ui.View):
    """
    Persistent: no timeout, fixed custom_id, registered once on startup.
    The Attack link button has no custom_id and needs no handler, so it is
    only added when a message is actually sent.
    """

    def __init__(self, manager, attacker_id=None):
        super().__init__(timeout=None)
        if attacker_id:
            self.add_item(discord.ui.Button(label='Attack', style=discord.ButtonStyle.link,
                                            url=attack_url(attacker_id)))
        self.add_item(AddFromRetalButton(manager))


class TargetManager:
    def __init__(self, bot):
        self.bot = bot
        self.verification_system = None   # wired in bot.py, for the verified role
        self.data = {'channel_id': 0, 'board_message_id': 0,
                     'targets': {}, 'retals': {}, 'seen': {}}
        # The board refresh is triggered both by its loop and by /target add,
        # and the retal tick edits the same channel: serialise each so two
        # runs never post two boards or two copies of a retal.
        self._board_lock = asyncio.Lock()
        self._retal_lock = asyncio.Lock()
        # Both loops start together on boot and both call ensure_channel; on
        # the first deploy each saw no channel and each created one, leaving
        # two. Creation has to be one-at-a-time.
        self._channel_lock = asyncio.Lock()

    # ---- persistence

    async def load(self):
        data = load_json(STORE, {}) or {}
        for key, default in self.data.items():
            data.setdefault(key, default if not isinstance(default, dict) else {})
        self.data = data
        logger.info(f"Targets: {len(self.data['targets'])} on the board, "
                    f"{len(self.data['retals'])} open retal(s)")

    def save(self):
        save_json(STORE, self.data)

    # ---- channel

    def _verified_role(self, guild):
        rid = None
        if self.verification_system:
            rid = self.verification_system.config.get('verified_role_id')
        role = guild.get_role(rid) if rid else None
        return role or discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)

    async def ensure_channel(self):
        """
        The channel, creating it if needed: next to the war channel, visible
        to verified members only. Returns None (and logs why) when it cannot,
        rather than creating something visible to the whole server.
        """
        async with self._channel_lock:
            return await self._ensure_channel()

    async def _ensure_channel(self):
        ch = self.bot.get_channel(self.data.get('channel_id') or 0)
        if ch is not None:
            return ch
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return None
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if ch is None:
            verified = self._verified_role(guild)
            if verified is None:
                logger.warning('Targets: no verified role found, not creating the channel '
                               '(it would be hidden from everyone or shown to everyone)')
                return None
            war = self.bot.get_channel(WAR_CHANNEL_ID)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                verified: discord.PermissionOverwrite(view_channel=True),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, embed_links=True,
                    manage_messages=True, read_message_history=True),
            }
            try:
                ch = await guild.create_text_channel(
                    CHANNEL_NAME, category=getattr(war, 'category', None),
                    overwrites=overwrites,
                    topic='Target board (pinned) and live retals. '
                          'Add a target with /target action:Add.',
                    reason='Targets and retals channel')
                logger.info(f'Targets: created #{CHANNEL_NAME}')
            except discord.Forbidden:
                logger.error('Targets: missing Manage Channels permission, '
                             f'cannot create #{CHANNEL_NAME}')
                return None
        self.data['channel_id'] = ch.id
        self.save()
        return ch

    # ---- targets

    def _is_verified(self, interaction):
        member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        if member is None:
            return False
        role = self._verified_role(interaction.guild)
        return (role is not None and role in member.roles) or is_management(interaction)

    async def add_target(self, interaction, torn_id, reason=''):
        """Returns the message to show the member (only they see it)."""
        if not self._is_verified(interaction):
            return 'Only verified members can add targets. Use `/register` first.'
        targets = self.data['targets']
        tid = str(torn_id)
        if tid in targets:
            return (f"**{targets[tid].get('name', tid)}** is already on the board "
                    f"(added by {targets[tid].get('added_by_name', 'someone')}).")
        if count_added_by(targets, interaction.user.id) >= PER_MEMBER_CAP:
            return (f'You already have {PER_MEMBER_CAP} targets on the board. '
                    'Remove one with `/target action:Remove` first.')
        profile = await fetch_player_data(tid, 'profile')
        if profile is None:
            # None is "could not ask Torn", which includes a wrong id; say so
            # rather than claiming the player does not exist.
            return "Couldn't look that player up on Torn. Check the id and try again."
        if str((profile.get('faction') or {}).get('faction_id')) == str(TORN_FACTION_ID):
            return f"**{profile.get('name', tid)}** is one of us."
        targets[tid] = {
            'name': profile.get('name') or tid,
            'level': profile.get('level'),
            'reason': (reason or '').strip()[:REASON_MAX],
            'added_by': str(interaction.user.id),
            'added_by_name': interaction.user.display_name,
            'added_at': int(time.time()),
        }
        self.save()
        asyncio.create_task(self.refresh_board())
        return f"Added **{targets[tid]['name']}** to the target board for {TARGET_DAYS} days."

    def remove_target(self, interaction, torn_id):
        tid = str(torn_id)
        target = self.data['targets'].get(tid)
        if target is None:
            return "That player isn't on the board."
        if str(target.get('added_by')) != str(interaction.user.id) and not is_management(interaction):
            return (f"Only {target.get('added_by_name', 'whoever added them')} or management "
                    'can remove that one.')
        self.data['targets'].pop(tid)
        self.save()
        asyncio.create_task(self.refresh_board())
        return f"Removed **{target.get('name', tid)}** from the board."

    async def _profiles(self, ids):
        out = {}
        for tid in ids:
            # 90s cache: the board refreshes every 2 min, so this is always
            # fresh enough while /target add can reuse a lookup it just made.
            out[tid] = await fetch_player_data(tid, 'profile', cache_seconds=90)
        return out

    async def refresh_board(self):
        async with self._board_lock:
            ch = await self.ensure_channel()
            if ch is None:
                return None
            now = int(time.time())
            if prune_targets(self.data['targets'], now):
                self.save()
            ids = list(self.data['targets'])
            profiles = await self._profiles(ids)
            ff = await fetch_ffscouter_stats(ids) if ids else {}
            for tid, prof in profiles.items():
                # Keep the stored name current, so the board still reads
                # right on a tick where Torn did not answer.
                if prof and tid in self.data['targets']:
                    self.data['targets'][tid]['name'] = prof.get('name') or self.data['targets'][tid]['name']
            embed = build_board_embed(self.data['targets'], profiles, ff, now)

            msg = None
            if self.data.get('board_message_id'):
                try:
                    msg = await ch.fetch_message(self.data['board_message_id'])
                except discord.NotFound:
                    msg = None
                except discord.HTTPException as e:
                    logger.error(f'Targets: could not read the board: {e}')
                    return None
            if msg is not None:
                await msg.edit(embed=embed)
                return 'edited'
            msg = await ch.send(embed=embed)
            try:
                await msg.pin()
            except discord.HTTPException as e:
                logger.error(f'Targets: could not pin the board: {e}')
            self.data['board_message_id'] = msg.id
            self.save()
            return 'posted'

    # ---- retals

    def retal_for_message(self, message_id):
        for retal in self.data['retals'].values():
            if message_id and retal.get('message_id') == message_id:
                return retal
        return None

    async def _delete(self, ch, message_id):
        if not message_id:
            return
        try:
            await ch.get_partial_message(message_id).delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            logger.error(f'Targets: could not delete retal message: {e}')

    async def sweep_stray_retals(self):
        """
        On startup: delete retal messages the store no longer knows about
        (a crash between posting and saving), so none sit there forever.
        """
        ch = await self.ensure_channel()
        if ch is None:
            return 0
        known = {r.get('message_id') for r in self.data['retals'].values()}
        removed = 0
        async for msg in ch.history(limit=100):
            if (msg.author.id == self.bot.user.id and msg.embeds and msg.id not in known
                    and (msg.embeds[0].title or '').startswith((RETAL_PREFIX, RETALIATED_PREFIX))):
                await self._delete(ch, msg.id)
                removed += 1
        return removed

    async def retal_tick(self):
        async with self._retal_lock:
            ch = await self.ensure_channel()
            if ch is None:
                return
            now = int(time.time())
            retals = self.data['retals']
            changed = set()

            # Small overlap past the window: a hit that ended just before the
            # previous tick's cut-off must not fall in the gap between reads.
            attacks = await fetch_faction_attacks(from_ts=now - RETAL_WINDOW - 60)
            if attacks is not None:
                for hit in landed_hits(attacks, TORN_FACTION_ID):
                    if hit['ended'] + RETAL_WINDOW <= now or hit['code'] in self.data['seen']:
                        continue
                    self.data['seen'][hit['code']] = hit['ended']
                    if merge_hit(retals, hit):
                        changed.add(hit['attacker_id'])
                for key, retal in retals.items():
                    if retal.get('retaliated_by') or retal['expires'] <= now:
                        continue
                    last_hit = max(h['ended'] for h in retal['hits'])
                    who = find_retaliation(attacks, TORN_FACTION_ID, key, last_hit)
                    if who:
                        retal['retaliated_by'] = who
                        changed.add(key)
                        logger.info(f"Retal: {retal['attacker_name']} hit back by {who}")
            # An outage (attacks is None) still lets expiry run below: closing
            # a retal needs only the clock, never the API.

            if changed:
                ff = await fetch_ffscouter_stats(list(changed))
                for key in changed:
                    retal = retals[key]
                    if retal['expires'] <= now:
                        continue
                    embed = build_retal_embed(retal, ff.get(key), now)
                    view = None if retal.get('retaliated_by') else RetalView(self, key)
                    if retal.get('message_id'):
                        try:
                            msg = ch.get_partial_message(retal['message_id'])
                            await msg.edit(embed=embed, view=view)
                            continue
                        except discord.NotFound:
                            retal['message_id'] = None
                        except discord.HTTPException as e:
                            logger.error(f'Targets: could not edit retal: {e}')
                            continue
                    try:
                        msg = await ch.send(embed=embed, view=view)
                        retal['message_id'] = msg.id
                        # Logged so "did the bot see that hit?" is one grep,
                        # not a reconstruction from the attack log.
                        logger.info(f"Retal: posted {retal['attacker_name']} "
                                    f"({len(retal['hits'])} hit(s), closes in "
                                    f"{retal['expires'] - now}s)")
                    except discord.HTTPException as e:
                        logger.error(f'Targets: could not post retal: {e}')

            for key in [k for k, r in retals.items() if r['expires'] <= now]:
                await self._delete(ch, retals.pop(key).get('message_id'))
                changed.add(key)

            # Remember hit codes a little past their window, so a late page
            # cannot re-post a retal that already closed.
            stale = [c for c, ended in self.data['seen'].items() if ended + RETAL_WINDOW + 600 < now]
            for c in stale:
                del self.data['seen'][c]
            if changed or stale:
                self.save()


async def setup_target_commands(bot, manager):
    @bot.tree.command(name='target', description='The target board: add, remove or list targets')
    @app_commands.describe(
        action='What to do',
        who='[Add/Remove] Torn id or profile link',
        reason='[Add] Why (optional, shows on the board)')
    @app_commands.choices(action=[
        app_commands.Choice(name='Add - Put someone on the board', value='add'),
        app_commands.Choice(name='Remove - Take one of yours off (management: any)', value='remove'),
        app_commands.Choice(name='List - Everyone on the board, just for you', value='list'),
    ])
    async def target(interaction: discord.Interaction,
                     action: app_commands.Choice[str],
                     who: str = '', reason: str = ''):
        if action.value == 'list':
            targets = manager.data['targets']
            if not targets:
                await interaction.response.send_message('The board is empty.', ephemeral=True)
                return
            lines = [f"**{t.get('name', tid)}** [{tid}] · added by {t.get('added_by_name', '?')}"
                     + (f" · \"{t['reason']}\"" if t.get('reason') else '')
                     for tid, t in targets.items()]
            await interaction.response.send_message(
                embed=create_embed(title=BOARD_TITLE, description='\n'.join(lines)[:4000],
                                   color=EMBED_COLOR_INFO),
                ephemeral=True)
            return

        torn_id = parse_torn_id(who)
        if torn_id is None:
            await interaction.response.send_message(
                'Give a Torn id or paste their profile link in `who`.', ephemeral=True)
            return
        if action.value == 'remove':
            await interaction.response.send_message(
                manager.remove_target(interaction, torn_id), ephemeral=True)
            return
        # Add looks the player up on Torn, which can take longer than the
        # 3 seconds Discord allows before an answer.
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(
            await manager.add_target(interaction, torn_id, reason), ephemeral=True)

    logger.info('Target command registered (/target add/remove/list)')
