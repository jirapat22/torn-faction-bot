"""
The member-facing command reference in #commands-the-punk.

Why a posted board rather than /help: members do not read /help, because to
read it they have to already know a command exists and which channel it is
allowed in. A read-only channel they can scroll answers both at once.

Deliberately MEMBERS ONLY. Every management view is left out - a list that
mixes them tells most of the faction about buttons they cannot press, and
"Permission Denied" is the worst possible answer to a member trying to help.
Each entry carries the channels the command is actually permitted in, because
Discord's own error for a command used in the wrong channel does not say
where to go instead.

Keeping it honest: the gating lives in the command handlers (is_management
checks per action), so when an action changes hands this list has to be
edited with it. test_member_command_board_lists_only_member_actions() pins
every entry here against the real choice lists so the two cannot silently
drift apart.
"""

import logging

from utils.helpers import create_embed
from config import (
    EMBED_COLOR_INFO,
    WAR_CHANNEL_ID,
    CHAINING_CHANNEL_ID,
    GENERAL_CHANNEL_ID,
    AVAILABILITY_CHANNEL_ID,
    WELCOME_CHANNEL_ID,
    MEMBER_COMMANDS_CHANNEL_ID,
)

logger = logging.getLogger(__name__)

# (command, what it does for a member, [channel ids it is allowed in])
# An empty channel list means "anywhere".
SECTIONS = [
    ('\U0001f464 Your account', [
        ('/register torn_id:<your id>',
         'Links your Discord to your Torn account and sets your nickname. '
         'Do this first: every alert, poll and payout the bot works out '
         'depends on it.',
         [WELCOME_CHANNEL_ID]),
        ('/whois member:@someone',
         'Who a Discord account is in Torn, or the other way round.', []),
        ('/myactivity',
         'Your own activity as the bot sees it, which is what the inactivity '
         'check reads.', []),
        ('/unregister',
         'Unlinks your account.', []),
    ]),
    ('⚔️ War', [
        ('/war view:Status',
         'Current war: score, enemy, and how long is left.',
         [WAR_CHANNEL_ID]),
        ('/war view:FlyStatus',
         'Who is in the air or abroad right now, ours and theirs.',
         [WAR_CHANNEL_ID]),
    ]),
    ('⛓️ Chains', [
        ('/chain view:Status',
         'The live chain: hits on it, and the time left before it drops.',
         [CHAINING_CHANNEL_ID]),
        ('/chain view:Report',
         'Who did what on the last chain.',
         [CHAINING_CHANNEL_ID]),
        ('/chain view:Event',
         'During a chain event: your hits, your rank, and the top 5. '
         'Only you see it.',
         [CHAINING_CHANNEL_ID]),
    ]),
    ('\U0001f5d3️ Availability', [
        ('/avail action:Mine date:DD-MM-YYYY',
         'The blocks you picked on that poll.',
         [GENERAL_CHANNEL_ID, AVAILABILITY_CHANNEL_ID]),
        ('/avail action:Summary date:DD-MM-YYYY',
         'Everyone\'s answers for that poll.',
         [GENERAL_CHANNEL_ID, AVAILABILITY_CHANNEL_ID]),
        ('/avail action:List',
         'Which polls are open.',
         [GENERAL_CHANNEL_ID, AVAILABILITY_CHANNEL_ID]),
    ]),
    ('\U0001f916 Bot and time', [
        ('/tct_now', 'Torn City Time right now, which is what every faction '
                     'deadline is set in.', []),
        ('/help', 'The short version of this list.', []),
        ('/ping', 'Check the bot is awake.', []),
    ]),
]

FOOTER = ('Management has more views on /war, /chain and /avail, plus /faction '
          'and /strikes.')


def _where(channel_ids):
    """Render the allowed channels, dropping any that are not configured."""
    live = [f'<#{c}>' for c in channel_ids if c]
    if not live:
        return 'any channel'
    return ' or '.join(live)


def build_embed(sections=None):
    """
    Render the board. Pure, so the content can be tested without Discord.

    Returns a single embed. The description is the identity used to decide
    whether a repost is needed, so it must depend only on the data above.
    """
    sections = SECTIONS if sections is None else sections
    parts = []
    for title, rows in sections:
        lines = [f'**{title}**']
        for cmd, what, chans in rows:
            lines.append(f'`{cmd}`\n-# {what}\n-# Use in: {_where(chans)}')
        parts.append('\n'.join(lines))
    desc = ('Everything here is yours to use. Commands are typed in Discord: '
            'start with `/` and pick from the menu.\n\n' + '\n\n'.join(parts)
            + f'\n\n-# {FOOTER}')
    return create_embed(title='\U0001f916 Commands you can use',
                        description=desc[:4000], color=EMBED_COLOR_INFO)


async def refresh(bot, channel_id=None, force=False):
    """
    Put the current board in the channel, replacing whatever the bot posted
    there before.

    Only reposts when the text actually changed, so a restart loop cannot turn
    the channel into a stack of identical boards. Returns 'posted',
    'unchanged', or None when it could not run.
    """
    channel_id = channel_id or MEMBER_COMMANDS_CHANNEL_ID
    if not channel_id or bot is None:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning(f'Member commands: channel {channel_id} not found')
        return None

    embed = build_embed()
    new_desc = embed.description

    mine = []
    try:
        async for msg in channel.history(limit=50):
            if bot.user and msg.author.id == bot.user.id:
                mine.append(msg)
    except Exception as e:
        logger.error(f'Member commands: could not read channel history: {e}')
        return None

    if not force and mine:
        current = mine[0]
        existing = current.embeds[0].description if current.embeds else None
        if existing == new_desc:
            # Already correct. Leaving it alone also keeps the message id
            # stable, so anybody who bookmarked or linked it still lands on it.
            return 'unchanged'

    # Edit the existing board rather than reposting it. The bot owns the
    # message, so an edit is silent: no new message at the bottom of the
    # channel, no notification, and it keeps its place and its id for anyone
    # who linked it. Reposting on every wording change made the board look
    # like news when nothing had happened.
    if mine:
        try:
            await mine[0].edit(embed=embed)
            for stale in mine[1:]:
                try:
                    await stale.delete()
                except Exception as e:
                    logger.debug(f'Member commands: could not delete a '
                                 f'duplicate board: {e}')
            logger.info('Member commands: board edited in place')
            return 'edited'
        except Exception as e:
            # Fall through to posting a fresh one. An edit can fail on a
            # message too old to edit, and a stale board is worse than a
            # second one.
            logger.warning(f'Member commands: edit failed, reposting: {e}')

    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f'Member commands: post failed: {e}')
        return None

    # Delete the old copies only after the new one is up. The other order
    # leaves the channel empty if the send fails.
    for msg in mine:
        try:
            await msg.delete()
        except Exception as e:
            logger.debug(f'Member commands: could not delete old board: {e}')

    logger.info(f'Member commands: board refreshed, replaced {len(mine)}')
    return 'posted'
