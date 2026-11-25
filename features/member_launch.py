"""
Members go live by themselves at a set time (Pat, 25/11/2026: "set auto on
that", 2pm NZ on 26/11).

`ASK_AI_MEMBERS_FROM` (unix seconds, in env/apexpunk.env) opens @ApexPunk to
members at that moment, without editing the env file or restarting. At that
moment the bot also, once:
  - edits the help guide in #commands-the-punk so it stops saying
    "members: asking opens soon",
  - posts the launch announcement in #announcements, pinging the Ascended
    Punks role (Pat approved the text).

`ASK_AI_MEMBERS=1` still works as the plain on switch (and wins). Setting
`ASK_AI_MEMBERS_FROM=0` (or removing it) cancels the launch: it is only a time.
"""

import logging
import time

import discord

from utils.helpers import load_json, save_json

logger = logging.getLogger(__name__)

STATE_FILE = 'data/member_launch.json'
# Only announce within this long after the launch time. If the bot was down
# at 2pm and comes back much later (or the state file is lost), a launch post
# days late would read as news that isn't; the switch itself still opens.
ANNOUNCE_WINDOW_S = 6 * 3600


def members_open(now=None):
    """True once members may ask: the plain switch, or the launch time passed."""
    from config import ASK_AI_MEMBERS, ASK_AI_MEMBERS_FROM
    if ASK_AI_MEMBERS:
        return True
    return bool(ASK_AI_MEMBERS_FROM) and (now or time.time()) >= ASK_AI_MEMBERS_FROM


def announcement_text(help_channel_id=None):
    """The launch post, as Pat approved it (25/11/2026). The channel is a
    real link when the guide channel is found, else its plain name."""
    where = f'<#{help_channel_id}>' if help_channel_id else '#commands-the-punk'
    return '\n'.join([
        '**🤖 ApexPunk is open to all Punks!**',
        "Yo crew 🤘 Your faction bot just got a mouth. @ me with a question in plain English and I'll "
        "answer from the faction's own records, live from Torn.",
        '',
        '**Try these:**',
        '• `@ApexPunk r we winning?` ⚔️ live war score',
        '• `@ApexPunk how active am I?` 📋 your own stats',
        '• `@ApexPunk do I have any strikes?` ⚠️',
        '• `@ApexPunk how does chaining work?` 📚 Torn game questions',
        "• `@ApexPunk who's in hosp?` 🏥",
        '• `@ApexPunk tell me a joke` / `roast me` 😈',
        '',
        '**Good to know:**',
        "• You only see your **own** records, not other members'.",
        '• One question every 2 minutes (chat and jokes are free, within reason).',
        f'• Everything I can do: {where}',
        '',
        '**Punks up, enemies down.** 🔥',
    ])


def due(now, start, state):
    """Should the launch steps run now? Pure, so it is easy to test."""
    if not start or now < start or now > start + ANNOUNCE_WINDOW_S:
        return False
    return (state or {}).get('announced_for') != start


async def launch_tick(bot, ask_ai, guild_id, path=STATE_FILE, now=None):
    """Run the once-only launch steps when the time has come. Returns what
    happened (for the log) or None. Safe to call every minute."""
    from config import ASK_AI_MEMBERS_FROM, ASK_AI_MEMBER_COOLDOWN_S, ANNOUNCEMENTS_CHANNEL_ID, \
        MEMBER_COMMANDS_CHANNEL_ID
    now = now or time.time()
    state = load_json(path, default={}) or {}
    if not due(now, ASK_AI_MEMBERS_FROM, state):
        return None
    guild = bot.get_guild(guild_id)
    if guild is None:
        return None   # not connected yet: try again next minute

    # 1. The guide stops saying "opens soon".
    from features.ask_help_post import HelpPost, guide_text, find_channel
    try:
        if ask_ai is not None:
            ask_ai.help_post = ask_ai.help_post or HelpPost()
            await ask_ai.help_post.publish(guild, guide_text(True, ASK_AI_MEMBER_COOLDOWN_S),
                                           MEMBER_COMMANDS_CHANNEL_ID)
    except Exception as e:
        logger.error(f'Member launch: could not update the guide: {e!r}')

    # 2. The announcement, once. The state is written only after it posted,
    # so a failure is retried next minute (inside the window) instead of
    # silently skipping the launch post.
    channel = guild.get_channel(ANNOUNCEMENTS_CHANNEL_ID) if ANNOUNCEMENTS_CHANNEL_ID else None
    if channel is None:
        logger.error('Member launch: #announcements not found, launch post not sent')
        return None
    from features.event_reminders import announcement_ping
    vs = getattr(ask_ai, 'verification_system', None)
    role_id = (getattr(vs, 'config', None) or {}).get('verified_role_id') if vs is not None else None
    ping, allowed = announcement_ping(False, int(role_id) if role_id else None)
    help_ch = find_channel(guild, MEMBER_COMMANDS_CHANNEL_ID)
    text = (ping + '\n' if ping else '') + announcement_text(getattr(help_ch, 'id', None))
    try:
        await channel.send(text, allowed_mentions=allowed)
    except discord.HTTPException as e:
        logger.error(f'Member launch: announcement failed: {e!r}')
        return None
    save_json(path, {'announced_for': ASK_AI_MEMBERS_FROM, 'announced_at': int(now)})
    logger.info('Member launch: members are in, guide updated, announcement posted')
    return 'launched'
