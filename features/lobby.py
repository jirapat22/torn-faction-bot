"""
People who joined Discord and never registered.

Without a verification record the bot cannot map them to a Torn ID, so they
get no alerts, no poll mention, no payout line - and because an unregistered
member can only see the welcome channel, they are also invisible to everyone
else. Sadrapp sat like that unnoticed: present, on the faction roster, and
reachable by nobody.

The weekly roster check already listed them, but a weekly list to management
is a thing to read, not a thing that happens. So this chases the person
first, in the one channel they can see, and only escalates to management when
they have ignored it for days.

Two deliberate restraints:

- Nothing happens on the first day. Somebody who joined an hour ago is
  reading the welcome message, and a bot nagging them mid-read is worse than
  useless.
- The nagging stops. After MAX_NAGS it is clearly not working, and the
  problem belongs to a human who can go and ask them in Torn.
"""

import logging
import time

import discord

from utils.helpers import create_embed, load_json, save_json
from config import (
    EMBED_COLOR_WARNING,
    WELCOME_CHANNEL_ID,
    INACTIVITY_ALERTS_CHANNEL_ID,
    MANAGEMENT_CHANNEL_ID,
)

logger = logging.getLogger(__name__)

STATE_FILE = 'data/lobby.json'

# Leave them alone for the first day: they may simply not have got to it yet.
NAG_AFTER_HOURS = 24
# Then every other day, so it reads as a reminder and not a machine gun.
NAG_EVERY_HOURS = 48
# Three is enough to prove nagging is not the answer.
MAX_NAGS = 3
# At this point a human needs to go and ask them in Torn.
ESCALATE_AFTER_DAYS = 3


def classify_lobby(members, verified_ids, now=None, state=None):
    """
    Decide who to nag and who to escalate. Pure, so the cadence is testable
    without a guild.

    Args:
        members: [{'id': str, 'name': str, 'joined_at': epoch seconds}]
                 bots already filtered out by the caller
        verified_ids: set of discord ids that HAVE a verification record
        state: {discord_id: {'nags': int, 'last': epoch}}

    Returns (to_nag, to_escalate, new_state). Entries are the member dicts.
    """
    now = time.time() if now is None else now
    state = dict(state or {})
    to_nag, to_escalate = [], []
    seen = set()

    for m in members:
        did = str(m.get('id'))
        if did in {str(v) for v in (verified_ids or set())}:
            continue
        joined = m.get('joined_at') or 0
        if not joined:
            # No join date is not a reason to pester somebody; the roster
            # check still lists them for management.
            continue
        seen.add(did)
        hours_in = (now - joined) / 3600.0
        if hours_in < NAG_AFTER_HOURS:
            continue

        rec = state.get(did) or {'nags': 0, 'last': 0}
        # A human said they would go and talk to this one. Stop the public
        # nagging - the bot and a person chasing the same member on the same
        # day reads as a pile-on - but keep tracking them, so escalation
        # still fires if the conversation never happened.
        if not rec.get('claimed'):
            if (rec['nags'] < MAX_NAGS
                    and (now - rec.get('last', 0)) >= NAG_EVERY_HOURS * 3600):
                to_nag.append(m)
                rec = dict(rec, nags=rec['nags'] + 1, last=now)
        state[did] = rec

        if hours_in >= ESCALATE_AFTER_DAYS * 24:
            to_escalate.append(m)

    # Anyone who registered or left stops being tracked - otherwise the file
    # grows forever and a rejoin would resume mid-nag.
    new_state = {k: v for k, v in state.items() if k in seen}
    return to_nag, to_escalate, new_state


def nag_text(member_ids):
    """The message itself. One per batch, so five joiners is one message."""
    who = ' '.join(f'<@{m}>' for m in member_ids)
    return (
        f'{who}\n'
        'You are not registered yet, so the bot cannot see you: no war calls, '
        'no availability poll, no payout line, and nobody can @ you.\n\n'
        '**Fix it in one of two ways**\n'
        '- Run `/register torn_id:<your Torn ID>` right here. Your ID is the '
        'number in your Torn profile URL.\n'
        '- Or link your Torn account to Discord in Torn itself, and the bot '
        'picks you up automatically overnight - no command needed.\n\n'
        '-# Never paste an API key in Discord. The bot never asks for one.')


def dm_text():
    """
    What the bot says in a DM. Shorter than the public nag and without the
    mentions, since a DM is already addressed to them.
    """
    return (
        "Hey - you joined the Ascended Punks Discord but haven't registered "
        "yet, so the bot can't see you: no war calls, no availability poll, "
        "no payout line.\n\n"
        "**Two ways to fix it**\n"
        "- Run `/register torn_id:<your Torn ID>` in the server. Your ID is "
        "the number in your Torn profile URL.\n"
        "- Or link your Torn account to Discord inside Torn itself, and the "
        "bot picks you up automatically overnight.\n\n"
        "-# Never paste an API key in Discord. The bot never asks for one.")


class LobbyChaseView(discord.ui.View):
    """
    The choice management gets when somebody has sat unregistered for a day:
    let the bot DM them, or take it yourself.

    Not persistent, so these buttons die on a restart. That is fine - the
    check runs again and posts a fresh message; the state is what matters and
    it lives in the JSON, not in the view.
    """

    def __init__(self, members, state_file=STATE_FILE):
        super().__init__(timeout=7 * 86400)
        self.members = members
        self.state_file = state_file

    @discord.ui.button(label='Bot DMs them',
                       style=discord.ButtonStyle.primary)
    async def dm_them(self, interaction, button):
        await interaction.response.defer()
        sent, failed = [], []
        for m in self.members:
            user = interaction.guild.get_member(int(m['id'])) if interaction.guild else None
            if user is None:
                failed.append(m['name'])
                continue
            try:
                await user.send(dm_text())
                sent.append(m['name'])
            except Exception:
                # Closed DMs are common and are NOT a silent success: saying
                # nothing here would leave management thinking it was handled.
                failed.append(m['name'])
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
        parts = []
        if sent:
            parts.append(f"✅ DM'd **{len(sent)}**: {', '.join(sent)}")
        if failed:
            parts.append(f"⚠️ Could not DM **{len(failed)}**: "
                         f"{', '.join(failed)} - their DMs are closed, so "
                         f"somebody has to catch them another way.")
        await interaction.followup.send('\n'.join(parts) or 'Nobody to DM.')

    @discord.ui.button(label="I'll message them",
                       style=discord.ButtonStyle.secondary)
    async def claim(self, interaction, button):
        state = load_json(self.state_file, default={}) or {}
        for m in self.members:
            rec = state.get(str(m['id'])) or {'nags': 0, 'last': 0}
            rec['claimed'] = True
            rec['claimed_by'] = str(interaction.user.id)
            state[str(m['id'])] = rec
        save_json(self.state_file, state)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"\U0001f44d {interaction.user.mention} has got "
            f"**{len(self.members)}** of them. The bot will stop nagging them "
            f"publicly, but will still escalate if they are still "
            f"unregistered after {ESCALATE_AFTER_DAYS} days.")


async def run(bot, guild, verification_system, state_file=STATE_FILE):
    """
    Nag the unregistered, escalate the stubborn. Returns a small summary.
    """
    if not bot or not guild:
        return None
    verified = set(str(k) for k in
                   (getattr(verification_system, 'verified_users', None) or {}))

    members = []
    for m in guild.members:
        if m.bot:
            continue
        joined = getattr(m, 'joined_at', None)
        members.append({
            'id': str(m.id),
            'name': getattr(m, 'display_name', str(m.id)),
            'joined_at': joined.timestamp() if joined else 0,
        })

    state = load_json(state_file, default={}) or {}
    to_nag, to_escalate, new_state = classify_lobby(members, verified,
                                                    state=state)
    if new_state != state:
        save_json(state_file, new_state)

    if to_nag and WELCOME_CHANNEL_ID:
        channel = bot.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            try:
                await channel.send(nag_text([m['id'] for m in to_nag]))
            except Exception as e:
                logger.error(f'Lobby nag failed: {e}')
        else:
            logger.warning('Lobby: welcome channel not found')

    # Tell management the same day, with the decision attached. A public nag
    # is easy for somebody to scroll past; a named person deciding to chase
    # them is what actually converts a joiner.
    if to_nag and MANAGEMENT_CHANNEL_ID:
        mgmt = bot.get_channel(MANAGEMENT_CHANNEL_ID)
        if mgmt:
            rows = '\n'.join(
                f'- <@{m["id"]}> ({m["name"]}) · '
                f'{int((time.time() - m["joined_at"]) / 3600)}h in the server'
                for m in to_nag[:20])
            try:
                await mgmt.send(
                    embed=create_embed(
                        title='\U0001f6aa Joined but never registered',
                        description=(
                            f'{rows}\n\n'
                            f'They have been reminded in the welcome pit. '
                            f'Until they register the bot cannot see them: no '
                            f'war call, no poll, no payout line.'),
                        color=EMBED_COLOR_WARNING),
                    view=LobbyChaseView(to_nag[:20], state_file=state_file))
            except Exception as e:
                logger.error(f'Lobby management notice failed: {e}')
        else:
            logger.warning('Lobby: management channel not found')

    if to_escalate and INACTIVITY_ALERTS_CHANNEL_ID:
        channel = bot.get_channel(INACTIVITY_ALERTS_CHANNEL_ID)
        if channel:
            lines = '\n'.join(
                f'- <@{m["id"]}> ({m["name"]}) · '
                f'{int((time.time() - m["joined_at"]) / 86400)} days in the server'
                for m in to_escalate[:20])
            try:
                await channel.send(embed=create_embed(
                    title=f'\U0001f6aa {len(to_escalate)} in Discord, still not registered',
                    description=(
                        f'{lines}\n\n'
                        f'-# The bot has asked them in the welcome channel and '
                        f'they have not done it. It cannot reach them any other '
                        f'way, so somebody needs to ask in Torn - or register '
                        f'them with `/verify action:manual`.'),
                    color=EMBED_COLOR_WARNING))
            except Exception as e:
                logger.error(f'Lobby escalation failed: {e}')

    if to_nag or to_escalate:
        logger.info(f'Lobby: nagged {len(to_nag)}, escalated {len(to_escalate)}')
    return {'nagged': len(to_nag), 'escalated': len(to_escalate)}
