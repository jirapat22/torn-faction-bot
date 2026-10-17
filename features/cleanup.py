"""
Channel cleanup

Deliberately the narrow version. Only two channels are swept.

In the war tracker only the bot's own messages and Discord's join notices
go. The welcome pit is the one exception where a PERSON'S messages are also
swept, because the channel is pure onboarding traffic - half-typed
/register attempts, "hi", "welcome in" - and none of it is worth keeping a
month later. Nothing anybody typed in any other channel is ever touched,
and a pinned message is kept everywhere.

What is NOT swept, and why, because the tempting ones are the dangerous ones:

  #strikes          - the audit trail for every strike decision
  #war-reports      - for some wars the only readable copy of the numbers
  #leave-notices    - the notice IS the evidence in a strike appeal
  #inactivity-check - cited when someone disputes an inactivity strike
  #war-availability - poll answers are what exempt a member from a strike

Those are all channels you would cite in a dispute, so nothing in them is
touched regardless of age.
"""

import logging
from datetime import datetime, timedelta, timezone

import discord

from utils.helpers import load_json, save_json, create_embed
from config import (
    CLEANUP_DAYS,
    CLEANUP_MAX_PER_RUN,
    CLEANUP_WEEKDAY,
    CLEANUP_REPORT_CHANNEL_ID,
    WELCOME_CHANNEL_ID,
    WAR_TRACKER_CHANNEL_ID,
    EMBED_COLOR_INFO,
)

logger = logging.getLogger(__name__)

# Discord's own join notices. Deleting a member's actual words is never on
# the table, but "X joined the server" from last month is pure noise.
JOIN_TYPES = {
    discord.MessageType.new_member,
    discord.MessageType.premium_guild_subscription,
}


# The one channel where a person's own messages are swept too. Kept as an
# explicit set rather than a flag on the loop so that adding a channel here
# is a visible decision, not a side effect of changing a condition.
HUMAN_SWEEP_CHANNELS = {WELCOME_CHANNEL_ID} - {0}


class CleanupManager:
    def __init__(self, bot=None):
        self.bot = bot
        self.file = 'data/cleanup_state.json'

    def _targets(self):
        """(channel_id, label) pairs. Only ever these two."""
        out = []
        if WELCOME_CHANNEL_ID:
            out.append((WELCOME_CHANNEL_ID, 'welcome'))
        if WAR_TRACKER_CHANNEL_ID:
            out.append((WAR_TRACKER_CHANNEL_ID, 'war tracker'))
        return out

    def _deletable(self, msg, sweep_humans=False):
        """
        Whether this message can go.

        The bot's own messages and Discord's join notices always can.
        sweep_humans adds messages a person typed, and is passed for the
        welcome pit alone (see HUMAN_SWEEP_CHANNELS).

        Pinned messages are kept whatever they are, in every channel and
        regardless of sweep_humans: somebody pinned it on purpose, and that
        is a clearer signal than any age rule. It is also the escape hatch -
        pin anything in the welcome pit that should outlive the sweep.
        """
        if msg.pinned:
            return False
        if msg.type in JOIN_TYPES:
            return True
        me = self.bot.user if self.bot else None
        if me and msg.author and msg.author.id == me.id:
            return True
        return bool(sweep_humans)

    def _due(self, force=False):
        """
        True at most once in any ISO week, on CLEANUP_WEEKDAY.

        Guarded by the week stamp rather than "7 days since last run", so a
        restart cannot make it sweep twice and a week the bot was down is
        skipped rather than running late and deleting a double batch.
        """
        if force:
            return True
        now = datetime.now(timezone.utc)
        year, week, weekday = now.isocalendar()
        if weekday - 1 != CLEANUP_WEEKDAY:
            return False
        state = load_json(self.file, default={}) or {}
        return state.get('last_week') != f'{year}-W{week:02d}'

    def _stamp(self):
        year, week, _ = datetime.now(timezone.utc).isocalendar()
        save_json(self.file, {'last_week': f'{year}-W{week:02d}'})

    async def sweep(self, force=False):
        """
        Delete aged, deletable messages from the two swept channels, then say
        what was removed.

        Capped per run because messages older than 14 days cannot be bulk
        deleted and go one API call at a time. The cap limits how fast, not
        how much: a backlog drains over several weekly runs.

        Returns {label: {'deleted', 'bot', 'joins', 'kept', 'left'}}.
        """
        results = {}
        if not self.bot or not self._due(force):
            return results
        cutoff = datetime.now(timezone.utc) - timedelta(days=CLEANUP_DAYS)

        for channel_id, label in self._targets():
            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.warning(f'Cleanup: channel {channel_id} ({label}) not found')
                continue
            sweep_humans = channel_id in HUMAN_SWEEP_CHANNELS
            stat = {'deleted': 0, 'bot': 0, 'joins': 0, 'kept': 0, 'left': 0,
                    'human': 0, 'name': getattr(channel, 'name', label)}
            try:
                # oldest_first so a backlog drains from the far end rather
                # than re-walking the same recent messages every week.
                async for msg in channel.history(limit=600, before=cutoff,
                                                 oldest_first=True):
                    if not self._deletable(msg, sweep_humans):
                        stat['kept'] += 1
                        continue
                    if stat['deleted'] >= CLEANUP_MAX_PER_RUN:
                        stat['left'] += 1     # still owed, counted not deleted
                        continue
                    is_join = msg.type in JOIN_TYPES
                    me = self.bot.user if self.bot else None
                    is_mine = bool(me and msg.author
                                   and msg.author.id == me.id)
                    try:
                        await msg.delete()
                    except discord.NotFound:
                        continue            # already gone, fine
                    except discord.Forbidden:
                        logger.warning(f'Cleanup: no permission in {label}')
                        break
                    stat['deleted'] += 1
                    # Counted apart so the report can say plainly how many of
                    # the deletions were somebody's own words, not the bot's.
                    stat['joins' if is_join else
                         ('bot' if is_mine else 'human')] += 1
            except Exception as e:
                logger.error(f'Cleanup failed in {label}: {e}')
                continue
            results[label] = stat
            if stat['deleted']:
                logger.info(f'Cleanup: removed {stat["deleted"]} message(s) older '
                            f'than {CLEANUP_DAYS} days from {label}')

        if results:
            self._stamp()
            await self._report(results)
        return results

    async def _report(self, results):
        """
        Say what was deleted, and what was deliberately left behind.

        The kept count matters as much as the deleted one: it is the
        evidence of what was left alone. Where a person's own messages were
        swept - the welcome pit - the report says so in as many words, since
        a report that misdescribes what it deleted is worse than none.
        """
        if not CLEANUP_REPORT_CHANNEL_ID:
            return
        channel = self.bot.get_channel(CLEANUP_REPORT_CHANNEL_ID)
        if not channel:
            logger.warning('Cleanup: report channel not found')
            return

        total = sum(s['deleted'] for s in results.values())
        left = sum(s['left'] for s in results.values())
        lines = []
        for stat in results.values():
            bits = []
            if stat['bot']:
                bits.append(f'{stat["bot"]} bot message(s)')
            if stat['joins']:
                bits.append(f'{stat["joins"]} join notice(s)')
            if stat.get('human'):
                bits.append(f'{stat["human"]} member message(s)')
            kept_why = ('pinned posts' if stat.get('human')
                        else 'members\' own messages and pinned posts')
            lines.append(
                f'**#{stat["name"]}** · deleted {stat["deleted"]}'
                + (f' ({", ".join(bits)})' if bits else '')
                + f'\n-# kept {stat["kept"]} ({kept_why})'
                + (f', {stat["left"]} still queued' if stat['left'] else ''))

        desc = (f'Removed **{total}** message(s) older than {CLEANUP_DAYS} '
                f'days.\n\n' + '\n'.join(lines))
        if left:
            desc += (f'\n\n-# {left} more are over the limit of '
                     f'{CLEANUP_MAX_PER_RUN} per run and will go next week.')
        desc += ('\n-# The welcome pit is swept in full, members\' messages '
                 'included, because it is onboarding traffic. Everywhere '
                 'else only the bot’s own posts and Discord join notices '
                 'go. Pin anything that should survive. Nothing in #strikes, '
                 '#war-reports, #leave-notices, #inactivity-check or '
                 '#war-availability is touched at any age.')

        try:
            await channel.send(embed=create_embed(
                title='\U0001f9f9 Weekly channel cleanup',
                description=desc[:4000], color=EMBED_COLOR_INFO))
        except Exception as e:
            logger.error(f'Cleanup report failed: {e}')
