"""
The permanent "how to get verified" post in the welcome channel.

An unregistered member can see exactly one channel - the welcome pit - so
every instruction that matters to them has to live there. It cannot live in
#faction-edicts, which needs the verified role to open: the one person who
needs the instructions is the one person who cannot read them.

The welcome message and the lobby nag both reach people, but neither stays:
the welcome message fires once on join and scrolls away, and the nag gives up
after three tries. This is the version that is simply always there.

It is PINNED, and that matters twice over. It stays at the top of a channel
that is mostly registration chatter, and the weekly cleanup skips pinned
messages - without the pin, the sweep that clears this channel would
eventually delete the instructions along with everything else.

Channel names are written in plain text, never as <#id> mentions. An
unverified member cannot see those channels, so a mention renders as a dead
link to them - which is the exact audience this post is for.
"""

import logging

from utils.helpers import create_embed
from config import (
    EMBED_COLOR_INFO,
    WELCOME_CHANNEL_ID,
)

logger = logging.getLogger(__name__)


def build_embed():
    """
    Render the board. Pure, so its content is testable, and the description
    doubles as the identity used to decide whether a repost is needed.
    """
    desc = (
        "You can only see this channel until you are verified. Two ways to "
        "fix that, and the first one is the easier:\n\n"

        "**1 · Link your Torn account to Discord, in Torn**\n"
        "-# Do it once and the bot finds you by itself overnight. Nothing to "
        "type, no ID to get wrong, and nobody can claim your account by "
        "mistake.\n\n"

        "**2 · Or register here**\n"
        "`/register torn_id:<your Torn ID>`\n"
        "-# Your ID is the number in your Torn profile URL - for example "
        "`profiles.php?XID=` **`1234567`**. You must already be in the "
        "faction for this to work.\n\n"

        "**Then what**\n"
        "-# You get the Ascended Punks role, your nickname is set to your "
        "Torn name, and the rest of the server opens up. Start with "
        "**#faction-edicts** for the rules and **#guides-hub** if you are "
        "new to Torn.\n\n"

        "⚠️ **Never paste an API key in Discord**, here or anywhere "
        "else, and never share a Full Access key with anyone. The bot will "
        "never ask you for one.\n"
        "-# Stuck? Say so here and someone will sort you out."
    )
    return create_embed(title='\U0001f44b Start here: get verified',
                        description=desc[:4000], color=EMBED_COLOR_INFO)


async def refresh(bot, channel_id=None, force=False):
    """
    Keep exactly one pinned board in the welcome channel.

    Posts and pins a new one, then removes the bot's older copies - that
    order on purpose, so a failed send leaves the old instructions in place
    rather than leaving newcomers with nothing.

    Returns 'posted', 'unchanged', or None when it could not run.
    """
    channel_id = channel_id or WELCOME_CHANNEL_ID
    if not channel_id or bot is None:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning(f'Welcome board: channel {channel_id} not found')
        return None

    embed = build_embed()

    mine = []
    try:
        async for msg in channel.history(limit=100):
            if bot.user and msg.author.id == bot.user.id and msg.embeds:
                if (msg.embeds[0].title or '') == embed.title:
                    mine.append(msg)
    except Exception as e:
        logger.error(f'Welcome board: could not read channel history: {e}')
        return None

    if not force and mine:
        current = mine[0]
        if current.embeds[0].description == embed.description:
            # Right text already. Make sure it is still pinned - somebody may
            # have unpinned it, and an unpinned board gets swept eventually.
            if not getattr(current, 'pinned', False):
                try:
                    await current.pin()
                    logger.info('Welcome board: re-pinned')
                except Exception as e:
                    logger.error(f'Welcome board: could not re-pin: {e}')
            return 'unchanged'

    try:
        message = await channel.send(embed=embed)
    except Exception as e:
        logger.error(f'Welcome board: post failed: {e}')
        return None

    try:
        await message.pin()
    except Exception as e:
        # Worth shouting about: unpinned, the weekly sweep will eventually
        # delete the only instructions an unverified member can read.
        logger.error(f'Welcome board: posted but COULD NOT PIN: {e}')

    for old in mine:
        try:
            if getattr(old, 'pinned', False):
                await old.unpin()
            await old.delete()
        except Exception as e:
            logger.debug(f'Welcome board: could not remove old copy: {e}')

    logger.info(f'Welcome board: posted and pinned, replaced {len(mine)}')
    return 'posted'
