"""
Strike Ledger

The bot already detected every rule break it can see; what it never did was
*record* one. Every alert was a message, so "how many strikes does X have?"
could only be answered by scrolling a channel. This module is the missing
half: a durable per-member ledger, and a candidate -> human click -> strike
flow so nothing is ever recorded without someone deciding it.

Three stores, all in data/strikes.json:

  strikes   - issued strikes, the ledger itself
  pending   - candidates posted but not yet actioned, keyed so the button can
              find its details after a restart (a Discord custom_id is capped
              at 100 chars and cannot carry a reason string)
  dismissed - candidates management said no to, so a daily detection stops
              re-raising the same thing every morning. This is what makes the
              channel quiet enough to be worth reading.
"""

import time
import uuid
import logging

import discord

from utils.helpers import load_json, save_json, create_embed
from config import (
    STRIKE_EXPIRY_DAYS,
    STRIKES_CHANNEL_ID,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_CRITICAL,
    EMBED_COLOR_INFO,
)

logger = logging.getLogger(__name__)

# Short codes so a custom_id stays well inside Discord's 100-char limit.
STRIKE_TYPES = {
    'inactive': 'Offline with no notice',
    'war_offline': 'Offline during a war',
    'war_nohits': 'No hits in a war',
    'prewar_offline': 'Offline before a war started',
    'oc': 'Not in an organised crime',
    'bonus': 'Bonus hit on a non-enemy',
    'mug_faction': 'Mugged a faction mate',
    'mug_opp': 'Mugged the opponent',
    'trial': 'Missed the level 15 deadline',
    'chain': 'Missed the chain minimum',
    'conduct': 'Conduct',
    'other': 'Other',
}


class StrikeManager:
    def __init__(self, bot=None):
        self.bot = bot
        self.file = 'data/strikes.json'
        self.strikes = []     # [{id, torn_id, torn_name, type, reason, ...}]
        self.pending = {}     # {key: {...}}
        self.dismissed = {}   # {key: timestamp}

    # ---------- persistence ----------

    async def load(self):
        data = load_json(self.file, default={})
        if not isinstance(data, dict):
            data = {}
        self.strikes = data.get('strikes', []) or []
        self.pending = data.get('pending', {}) or {}
        self.dismissed = data.get('dismissed', {}) or {}
        logger.info(f'Strikes: {len(self.active())} active, '
                    f'{len(self.pending)} pending, {len(self.dismissed)} dismissed')

    async def save(self):
        save_json(self.file, {
            'strikes': self.strikes,
            'pending': self.pending,
            'dismissed': self.dismissed,
        })

    # ---------- the ledger ----------

    def _expiry_cutoff(self):
        return time.time() - STRIKE_EXPIRY_DAYS * 86400

    def active(self, torn_id=None):
        """
        Strikes that still count. Expiry is computed on READ rather than by
        deleting rows, so a strike that aged out is still in the file and the
        history stays auditable - you can always see what someone did in
        March even though it no longer counts against them.
        """
        cutoff = self._expiry_cutoff()
        out = [s for s in self.strikes
               if s.get('status') == 'active' and s.get('issued_at', 0) >= cutoff]
        if torn_id is not None:
            out = [s for s in out if str(s.get('torn_id')) == str(torn_id)]
        return out

    def history(self, torn_id):
        """Everything ever recorded for a member, expired and removed included."""
        return [s for s in self.strikes if str(s.get('torn_id')) == str(torn_id)]

    def add(self, torn_id, torn_name, stype, reason, issued_by='bot', war_id=None):
        rec = {
            'id': uuid.uuid4().hex[:8],
            'torn_id': str(torn_id),
            'torn_name': torn_name,
            'type': stype,
            'reason': reason,
            'issued_at': time.time(),
            'issued_by': issued_by,
            'war_id': war_id,
            'status': 'active',
        }
        self.strikes.append(rec)
        return rec

    def remove(self, strike_id, removed_by):
        for s in self.strikes:
            if s.get('id') == strike_id and s.get('status') == 'active':
                s['status'] = 'removed'
                s['removed_by'] = removed_by
                s['removed_at'] = time.time()
                return s
        return None

    def counts(self):
        """{torn_id: (name, n)} for everyone with at least one active strike."""
        out = {}
        for s in self.active():
            tid = str(s['torn_id'])
            name, n = out.get(tid, (s.get('torn_name', tid), 0))
            out[tid] = (s.get('torn_name', name), n + 1)
        return out

    # ---------- candidates ----------

    @staticmethod
    def key_for(stype, torn_id, context=''):
        """
        Identity of a candidate. The context separates things that can
        legitimately recur: a war-offline candidate is per war, so the same
        member can be raised again next war, but a daily OC candidate uses a
        stable context and therefore stays dismissed.
        """
        return f'{stype}:{torn_id}:{context}'

    def is_open(self, stype, torn_id, context=''):
        """False if this exact candidate is already posted, dismissed or struck."""
        key = self.key_for(stype, torn_id, context)
        if key in self.pending or key in self.dismissed:
            return False
        for s in self.active(torn_id):
            if s.get('type') == stype and str(s.get('context', '')) == str(context):
                return False
        return True

    async def post_candidate(self, torn_id, torn_name, stype, reason,
                             context='', channel=None):
        """
        Post one candidate with Issue / Dismiss buttons. Returns True if posted.

        One message per candidate rather than a list embed, because each needs
        its own decision - and an actioned message edits itself into its
        outcome, so the channel is a worklist that empties rather than a log
        that only grows.
        """
        if not self.bot:
            return False
        if not self.is_open(stype, torn_id, context):
            return False

        channel = channel or self.bot.get_channel(STRIKES_CHANNEL_ID)
        if not channel:
            logger.warning('Strikes: no channel configured for candidates')
            return False

        key = self.key_for(stype, torn_id, context)
        self.pending[key] = {
            'torn_id': str(torn_id),
            'torn_name': torn_name,
            'type': stype,
            'reason': reason,
            'context': str(context),
            'created': time.time(),
        }
        await self.save()

        prior = len(self.active(torn_id))
        embed = create_embed(
            title=f'⚖️ {STRIKE_TYPES.get(stype, stype)}',
            description=(f'**{torn_name}** '
                         f'[profile](<https://www.torn.com/profiles.php?XID={torn_id}>)\n\n'
                         f'{reason}\n\n'
                         f'-# {prior} active strike(s) already. Nothing is '
                         f'recorded until someone clicks.'),
            color=EMBED_COLOR_WARNING,
        )
        try:
            await channel.send(embed=embed, view=StrikeActionView(self, key))
            return True
        except Exception as e:
            logger.error(f'Strikes: failed to post candidate {key}: {e}')
            self.pending.pop(key, None)
            await self.save()
            return False

    async def notify_member(self, torn_id, torn_name, stype, reason, channel):
        """
        Tell the member: a DM, and a mention in the strikes channel.

        Both, deliberately. A DM can be closed or ignored and leaves no record
        anyone else can see; a channel mention is visible but easy to scroll
        past. The DM failing is normal - plenty of people block DMs from
        servers - so it is reported, never raised.
        """
        mention = None
        vs = getattr(self, 'verification_system', None)
        for discord_id, rec in (getattr(vs, 'verified_users', None) or {}).items():
            if str((rec or {}).get('torn_id')) == str(torn_id):
                mention = f'<@{discord_id}>'
                break

        dm_ok = False
        if mention:
            try:
                user = await self.bot.fetch_user(int(mention[2:-1]))
                await user.send(embed=create_embed(
                    title='⚖️ You have received a strike',
                    description=(f'**{STRIKE_TYPES.get(stype, stype)}**\n\n{reason}\n\n'
                                 f'Strikes expire after {STRIKE_EXPIRY_DAYS} days. '
                                 f'If you think this is wrong, reply to management '
                                 f'in the server.'),
                    color=EMBED_COLOR_CRITICAL))
                dm_ok = True
            except Exception as e:
                logger.info(f'Strikes: could not DM {torn_name}: {e}')

        if channel:
            who = mention or f'**{torn_name}**'
            note = '' if dm_ok else ' (DM did not go through, tell them directly)'
            try:
                await channel.send(
                    f'{who} — strike recorded: '
                    f'{STRIKE_TYPES.get(stype, stype)}.{note}')
            except Exception as e:
                logger.error(f'Strikes: could not post notification: {e}')
        return dm_ok


class StrikeActionView(discord.ui.View):
    """
    Issue / Dismiss buttons on a candidate.

    Persistent (timeout=None) and keyed entirely by custom_id, because after a
    restart Discord hands back only that string - the candidate's details are
    looked up from `pending` rather than carried in the id, which could not
    hold them anyway inside 100 characters.
    """

    def __init__(self, manager, key=None):
        super().__init__(timeout=None)
        self.manager = manager
        if key is not None:
            self.add_item(StrikeButton(manager, key, issue=True))
            self.add_item(StrikeButton(manager, key, issue=False))


class StrikeButton(discord.ui.Button):
    def __init__(self, manager, key, issue):
        super().__init__(
            label='Issue strike' if issue else 'Dismiss',
            style=discord.ButtonStyle.danger if issue else discord.ButtonStyle.secondary,
            custom_id=f'st:{"i" if issue else "d"}:{key}'[:100],
        )
        self.manager = manager
        self.key = key
        self.issue = issue

    async def callback(self, interaction: discord.Interaction):
        from utils.helpers import is_management
        if not is_management(interaction):
            await interaction.response.send_message(
                'Only management can action a strike.', ephemeral=True)
            return

        m = self.manager
        cand = m.pending.pop(self.key, None)
        if cand is None:
            await interaction.response.send_message(
                'This candidate was already actioned.', ephemeral=True)
            return

        actor = interaction.user.display_name
        if self.issue:
            rec = m.add(cand['torn_id'], cand['torn_name'], cand['type'],
                        cand['reason'], issued_by=actor,
                        war_id=cand.get('context') or None)
            rec['context'] = cand.get('context', '')
            await m.save()
            total = len(m.active(cand['torn_id']))
            embed = create_embed(
                title=f'\U0001f534 Strike issued · {STRIKE_TYPES.get(cand["type"], cand["type"])}',
                description=(f'**{cand["torn_name"]}**\n\n{cand["reason"]}\n\n'
                             f'-# Issued by {actor}. They now have **{total}** '
                             f'active strike(s). Expires in {STRIKE_EXPIRY_DAYS} days.'),
                color=EMBED_COLOR_CRITICAL)
            await interaction.response.edit_message(embed=embed, view=None)
            await m.notify_member(cand['torn_id'], cand['torn_name'],
                                  cand['type'], cand['reason'],
                                  interaction.channel)
        else:
            m.dismissed[self.key] = time.time()
            await m.save()
            embed = create_embed(
                title=f'✅ Dismissed · {STRIKE_TYPES.get(cand["type"], cand["type"])}',
                description=(f'**{cand["torn_name"]}**\n\n{cand["reason"]}\n\n'
                             f'-# Dismissed by {actor}. This will not be raised '
                             f'again.'),
                color=EMBED_COLOR_SUCCESS)
            await interaction.response.edit_message(embed=embed, view=None)
