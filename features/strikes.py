"""
Strike Ledger

The bot already detected every rule break it can see; what it never did was
*record* one. Every alert was a message, so "how many strikes does X have?"
could only be answered by scrolling a channel. This module is the missing
half: a durable per-member ledger, and a candidate -> human click -> strike
flow so nothing is ever recorded without someone deciding it.

A candidate offers three mutually exclusive choices, and whoever clicked is
shown on the resolved message:

  Strike, bot tells them   - recorded, DM sent, and the DM's exact text is
                             echoed into the channel so management can see
                             what the member was actually told
  Strike, I'll tell them   - recorded and flagged AWAITING NOTICE, so a strike
                             cannot quietly exist that nobody ever delivered.
                             The message keeps a "Mark as told" button.
  Dismiss                  - no strike, remembered so the same daily detection
                             stops re-raising it every morning

Three stores, all in data/strikes.json:

  strikes   - issued strikes, the ledger itself
  pending   - candidates posted but not yet actioned, keyed so the button can
              find its details after a restart (a Discord custom_id is capped
              at 100 chars and cannot carry a reason string)
  dismissed - candidates management said no to
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
)

logger = logging.getLogger(__name__)

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


def dm_text(stype, reason):
    """
    The message a struck member receives. Built in one place because the
    channel echoes it verbatim - management should see exactly what was sent,
    not a paraphrase that could drift from it.
    """
    return (f'**{STRIKE_TYPES.get(stype, stype)}**\n\n{reason}\n\n'
            f'Strikes expire after {STRIKE_EXPIRY_DAYS} days. If you think '
            f'this is wrong, say so to management in the server.')


class StrikeManager:
    def __init__(self, bot=None):
        self.bot = bot
        self.file = 'data/strikes.json'
        self.strikes = []
        self.pending = {}
        self.dismissed = {}

    # ---------- persistence ----------

    async def load(self):
        data = load_json(self.file, default={})
        if not isinstance(data, dict):
            data = {}
        self.strikes = data.get('strikes', []) or []
        self.pending = data.get('pending', {}) or {}
        self.dismissed = data.get('dismissed', {}) or {}
        logger.info(f'Strikes: {len(self.active())} active, '
                    f'{len(self.awaiting_notice())} awaiting notice, '
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
        history stays auditable.
        """
        cutoff = self._expiry_cutoff()
        out = [s for s in self.strikes
               if s.get('status') == 'active' and s.get('issued_at', 0) >= cutoff]
        if torn_id is not None:
            out = [s for s in out if str(s.get('torn_id')) == str(torn_id)]
        return out

    def history(self, torn_id):
        return [s for s in self.strikes if str(s.get('torn_id')) == str(torn_id)]

    def awaiting_notice(self):
        """
        Active strikes someone took on to deliver personally and has not yet
        marked as told. Without this a "I'll message them" click would be
        indistinguishable from a member who was never actually informed.
        """
        return [s for s in self.active() if s.get('notified') == 'pending']

    def add(self, torn_id, torn_name, stype, reason, issued_by='bot',
            context='', notified='bot'):
        rec = {
            'id': uuid.uuid4().hex[:8],
            'torn_id': str(torn_id),
            'torn_name': torn_name,
            'type': stype,
            'reason': reason,
            'context': str(context),
            'issued_at': time.time(),
            'issued_by': issued_by,
            'notified': notified,      # 'bot' | 'pending' | 'done'
            'status': 'active',
        }
        self.strikes.append(rec)
        return rec

    def get(self, strike_id):
        for s in self.strikes:
            if s.get('id') == strike_id:
                return s
        return None

    def remove(self, strike_id, removed_by):
        for s in self.strikes:
            if s.get('id') == strike_id and s.get('status') == 'active':
                s['status'] = 'removed'
                s['removed_by'] = removed_by
                s['removed_at'] = time.time()
                return s
        return None

    def mark_told(self, strike_id, by):
        s = self.get(strike_id)
        if not s or s.get('notified') != 'pending':
            return None
        s['notified'] = 'done'
        s['notified_by'] = by
        s['notified_at'] = time.time()
        return s

    def counts(self):
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
        legitimately recur: a war candidate is per war, so the same member can
        be raised again next war, but a daily one uses a stable context and
        therefore stays dismissed.
        """
        return f'{stype}:{torn_id}:{context}'

    def is_open(self, stype, torn_id, context=''):
        key = self.key_for(stype, torn_id, context)
        if key in self.pending or key in self.dismissed:
            return False
        for s in self.active(torn_id):
            if s.get('type') == stype and str(s.get('context', '')) == str(context):
                return False
        return True

    def discord_id_for(self, torn_id):
        vs = getattr(self, 'verification_system', None)
        for discord_id, rec in (getattr(vs, 'verified_users', None) or {}).items():
            if str((rec or {}).get('torn_id')) == str(torn_id):
                return str(discord_id)
        return None

    async def post_candidate(self, torn_id, torn_name, stype, reason,
                             context='', channel=None):
        """
        Post one candidate with its three choices. Returns True if posted.

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
                         f'-# {prior} active strike(s) already. Pick one: '
                         f'nothing is recorded until you do.'),
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

    async def send_dm(self, torn_id, stype, reason):
        """
        DM the member. Returns (sent, text).

        A failed DM is ordinary - plenty of people block DMs from servers - so
        it is reported to the channel and never raised.
        """
        text = dm_text(stype, reason)
        discord_id = self.discord_id_for(torn_id)
        if not discord_id:
            return False, text
        try:
            user = await self.bot.fetch_user(int(discord_id))
            await user.send(embed=create_embed(
                title='⚖️ You have received a strike',
                description=text,
                color=EMBED_COLOR_CRITICAL))
            return True, text
        except Exception as e:
            logger.info(f'Strikes: could not DM {torn_id}: {e}')
            return False, text


class StrikeActionView(discord.ui.View):
    """
    The three choices on a candidate.

    Persistent (timeout=None) and keyed entirely by custom_id, because after a
    restart Discord hands back only that string - the candidate's details are
    looked up from `pending` rather than carried in the id, which could not
    hold them anyway inside 100 characters.
    """

    def __init__(self, manager, key=None):
        super().__init__(timeout=None)
        self.manager = manager
        if key is not None:
            self.add_item(StrikeChoice(manager, key, 'bot'))
            self.add_item(StrikeChoice(manager, key, 'self'))
            self.add_item(StrikeChoice(manager, key, 'dismiss'))


class StrikeChoice(discord.ui.Button):
    LABELS = {
        'bot': ('Strike · bot tells them', discord.ButtonStyle.danger),
        'self': ("Strike · I'll tell them", discord.ButtonStyle.primary),
        'dismiss': ('Dismiss', discord.ButtonStyle.secondary),
    }
    CODES = {'bot': 'b', 'self': 's', 'dismiss': 'd'}

    def __init__(self, manager, key, choice):
        label, style = self.LABELS[choice]
        super().__init__(label=label, style=style,
                         custom_id=f'st:{self.CODES[choice]}:{key}'[:100])
        self.manager = manager
        self.key = key
        self.choice = choice

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
                'This one was already actioned.', ephemeral=True)
            return

        actor = interaction.user.display_name
        label = STRIKE_TYPES.get(cand['type'], cand['type'])

        if self.choice == 'dismiss':
            m.dismissed[self.key] = time.time()
            await m.save()
            await interaction.response.edit_message(embed=create_embed(
                title=f'✅ Dismissed · {label}',
                description=(f'**{cand["torn_name"]}**\n\n{cand["reason"]}\n\n'
                             f'-# Dismissed by **{actor}**. Not recorded, and '
                             f'will not be raised again.'),
                color=EMBED_COLOR_SUCCESS), view=None)
            return

        notified = 'bot' if self.choice == 'bot' else 'pending'
        rec = m.add(cand['torn_id'], cand['torn_name'], cand['type'],
                    cand['reason'], issued_by=actor,
                    context=cand.get('context', ''), notified=notified)
        await m.save()
        total = len(m.active(cand['torn_id']))

        if self.choice == 'bot':
            sent, text = await m.send_dm(cand['torn_id'], cand['type'],
                                         cand['reason'])
            mention = m.discord_id_for(cand['torn_id'])
            who = f'<@{mention}>' if mention else f'**{cand["torn_name"]}**'
            # Echo the DM verbatim so management can see what was actually
            # said, not a summary of it.
            head = (f'\U0001f4e8 DM sent to {who}:' if sent
                    else f'⚠️ Could not DM {who} '
                         f'(blocked, or not registered). They still need '
                         f'telling. This is what would have been sent:')
            await interaction.response.edit_message(embed=create_embed(
                title=f'\U0001f534 Strike issued · {label}',
                description=(f'**{cand["torn_name"]}** · `{rec["id"]}`\n\n'
                             f'{cand["reason"]}\n\n'
                             f'-# Issued by **{actor}**, bot notified. '
                             f'{total} active strike(s). Expires in '
                             f'{STRIKE_EXPIRY_DAYS} days.'),
                color=EMBED_COLOR_CRITICAL), view=None)
            try:
                await interaction.channel.send(f'{head}\n>>> {text}')
            except Exception as e:
                logger.error(f'Strikes: could not echo DM: {e}')
            return

        # 'self' - recorded, but nobody has told them yet
        await interaction.response.edit_message(embed=create_embed(
            title=f'\U0001f534 Strike issued · {label}',
            description=(f'**{cand["torn_name"]}** · `{rec["id"]}`\n\n'
                         f'{cand["reason"]}\n\n'
                         f'**{actor} is telling them personally.**\n'
                         f'-# Recorded and awaiting notice. {total} active '
                         f'strike(s). Click below once you have told them, so '
                         f'it stops showing as outstanding.'),
            color=EMBED_COLOR_CRITICAL),
            view=StrikeDeliveredView(m, rec['id']))


class StrikeDeliveredView(discord.ui.View):
    """One button, on a strike somebody took on to deliver personally."""

    def __init__(self, manager, strike_id=None):
        super().__init__(timeout=None)
        if strike_id is not None:
            self.add_item(MarkToldButton(manager, strike_id))


class MarkToldButton(discord.ui.Button):
    def __init__(self, manager, strike_id):
        super().__init__(label='Mark as told',
                         style=discord.ButtonStyle.success,
                         custom_id=f'st:t:{strike_id}'[:100])
        self.manager = manager
        self.strike_id = strike_id

    async def callback(self, interaction: discord.Interaction):
        from utils.helpers import is_management
        if not is_management(interaction):
            await interaction.response.send_message(
                'Management only.', ephemeral=True)
            return
        m = self.manager
        rec = m.mark_told(self.strike_id, interaction.user.display_name)
        if rec is None:
            await interaction.response.send_message(
                'Already marked, or no longer outstanding.', ephemeral=True)
            return
        await m.save()
        label = STRIKE_TYPES.get(rec['type'], rec['type'])
        await interaction.response.edit_message(embed=create_embed(
            title=f'\U0001f534 Strike issued · {label}',
            description=(f'**{rec["torn_name"]}** · `{rec["id"]}`\n\n'
                         f'{rec["reason"]}\n\n'
                         f'-# Issued by **{rec.get("issued_by")}**, told by '
                         f'**{interaction.user.display_name}**. '
                         f'{len(m.active(rec["torn_id"]))} active strike(s).'),
            color=EMBED_COLOR_CRITICAL), view=None)
