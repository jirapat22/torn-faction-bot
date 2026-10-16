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

from config import (
    CLEANUP_DAYS,
    CLEANUP_MAX_PER_RUN,
    WELCOME_CHANNEL_ID,
    WAR_TRACKER_CHANNEL_ID,
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

    async def sweep(self):
        """
        Delete aged, deletable messages from the two swept channels.

        Capped per run because messages older than 14 days cannot be bulk
        deleted and go one API call at a time. The cap is not a limit on how
        much gets cleaned, only on how fast - this runs daily and will catch
        up over a few days rather than hammering the API once.

        Returns {label: deleted}.
        """
        results = {}
        if not self.bot:
            return results
        cutoff = datetime.now(timezone.utc) - timedelta(days=CLEANUP_DAYS)

        for channel_id, label in self._targets():
            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.warning(f'Cleanup: channel {channel_id} ({label}) not found')
                continue
            deleted = 0
            try:
                # oldest_first so a backlog drains from the far end rather
                # than re-walking the same recent messages every day.
                async for msg in channel.history(limit=400, before=cutoff,
                                                 oldest_first=True):
                    if deleted >= CLEANUP_MAX_PER_RUN:
                        break
                    if not self._deletable(msg):
                        continue
                    try:
                        await msg.delete()
                        deleted += 1
                    except discord.NotFound:
                        continue            # already gone, fine
                    except discord.Forbidden:
                        logger.warning(f'Cleanup: no permission in {label}')
                        break
            except Exception as e:
                logger.error(f'Cleanup failed in {label}: {e}')
                continue
            results[label] = deleted
            if deleted:
                logger.info(f'Cleanup: removed {deleted} message(s) older than '
                            f'{CLEANUP_DAYS} days from {label}')
        return results
