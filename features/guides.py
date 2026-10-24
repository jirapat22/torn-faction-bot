"""
Keep the guides readable.

The guides hub is a forum, and forum posts archive themselves after a period
of inactivity. An archived thread is not deleted and its link still works -
but Discord's client cannot resolve the NAME of a thread it has not loaded,
so every `<#id>` reference to one renders as "# unknown". The reading order
in "Start here Punks!" turned into fourteen lines of `# unknown`, which is
not a broken link so much as an unreadable index.

Pinning would hold them open, except a forum allows exactly ONE pinned post
and "Start here Punks!" holds it (verified: Discord answers code 30047,
"Maximum number pinned threads in this channel reached (1)"). So the only
durable fix is to un-archive them on a schedule.

Superseded guides are left alone on purpose. A thread whose title starts
with the retired marker is meant to be out of the way, and dragging those
back every hour would undo somebody's deliberate tidying.
"""

import logging

logger = logging.getLogger(__name__)

# Titles starting with this are replaced versions, kept for reference only.
RETIRED_PREFIX = '[OLD]'


def should_restore(name, archived):
    """
    Pure, so the rule is testable without Discord.

    Only archived threads need restoring, and never the retired ones.
    """
    if not archived:
        return False
    return not (name or '').strip().upper().startswith(RETIRED_PREFIX)


async def keep_unarchived(bot, channel_id):
    """
    Un-archive the live guides. Returns the number restored, or None when the
    forum could not be read.

    None rather than 0, because "could not look" and "nothing needed doing"
    lead to different conclusions if this ever gets reported anywhere.
    """
    if not bot or not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning('Guides: hub channel not found')
        return None
    if not hasattr(channel, 'archived_threads'):
        logger.warning('Guides: configured channel is not a forum')
        return None

    restored = 0
    try:
        async for thread in channel.archived_threads(limit=100):
            if not should_restore(thread.name, True):
                continue
            try:
                await thread.edit(archived=False)
                restored += 1
                logger.info(f'Guides: restored "{thread.name}"')
            except Exception as e:
                logger.error(f'Guides: could not restore "{thread.name}": {e}')
    except Exception as e:
        logger.error(f'Guides: could not read the hub: {e}')
        return None
    return restored
