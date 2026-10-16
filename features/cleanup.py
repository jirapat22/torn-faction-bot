"""
Channel cleanup

Deliberately the narrow version. Only two channels are swept, and only
messages the bot itself posted or Discord's own join notices - never
anything a person typed.

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

    def _deletable(self, msg):
        """
        A message is only ever deleted if the bot wrote it, or Discord did.

        Pinned messages are kept whatever they are: somebody pinned it on
        purpose, and that is a clearer signal than any age rule.
        """
        if msg.pinned:
            return False
        if msg.type in JOIN_TYPES:
            return True
        me = self.bot.user if self.bot else None
        return bool(me and msg.author and msg.author.id == me.id)

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
            stat = {'deleted': 0, 'bot': 0, 'joins': 0, 'kept': 0, 'left': 0,
                    'name': getattr(channel, 'name', label)}
            try:
                # oldest_first so a backlog drains from the far end rather
                # than re-walking the same recent messages every week.
                async for msg in channel.history(limit=600, before=cutoff,
                                                 oldest_first=True):
                    if not self._deletable(msg):
                        stat['kept'] += 1
                        continue
                    if stat['deleted'] >= CLEANUP_MAX_PER_RUN:
                        stat['left'] += 1     # still owed, counted not deleted
                        continue
                    is_join = msg.type in JOIN_TYPES
                    try:
                        await msg.delete()
                    except discord.NotFound:
                        continue            # already gone, fine
                    except discord.Forbidden:
                        logger.warning(f'Cleanup: no permission in {label}')
                        break
                    stat['deleted'] += 1
                    stat['joins' if is_join else 'bot'] += 1
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

        The kept count is the point of this message as much as the deleted
        one: it is the evidence that members' own messages were not touched.
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
            lines.append(
                f'**#{stat["name"]}** · deleted {stat["deleted"]}'
                + (f' ({", ".join(bits)})' if bits else '')
                + f'\n-# kept {stat["kept"]} (members\' own messages and '
                  f'pinned posts)'
                + (f', {stat["left"]} still queued' if stat['left'] else ''))

        desc = (f'Removed **{total}** message(s) older than {CLEANUP_DAYS} '
                f'days.\n\n' + '\n'.join(lines))
        if left:
            desc += (f'\n\n-# {left} more are over the limit of '
                     f'{CLEANUP_MAX_PER_RUN} per run and will go next week.')
        desc += ('\n-# Only the bot’s own posts and Discord join notices '
                 'are ever deleted. Nothing in #strikes, #war-reports, '
                 '#leave-notices, #inactivity-check or #war-availability is '
                 'touched at any age.')

        try:
            await channel.send(embed=create_embed(
                title='\U0001f9f9 Weekly channel cleanup',
                description=desc[:4000], color=EMBED_COLOR_INFO))
        except Exception as e:
            logger.error(f'Cleanup report failed: {e}')
