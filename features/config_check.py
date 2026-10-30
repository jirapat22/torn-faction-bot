"""
Does every id in the config still point at something real?

The failure this exists for is quiet, and it is the one a tired admin causes
at 2am: delete a channel, paste an id with a digit missing into the env file, or
remove a role, and the feature that used it simply stops. Every posting
feature already guards itself - get_channel() returns None, a warning goes to
the log, the function returns - so nothing crashes and nothing complains
where a human would see it. Strike candidates stop appearing and the first
anybody knows is a strike that was never raised.

So the check runs at startup and says so IN DISCORD. It is quiet when
everything resolves, and quiet again on the next restart if nothing changed,
because an operational warning that repeats on every deploy stops being read.
"""

import logging

from utils.helpers import create_embed, load_json, save_json
from config import (
    EMBED_COLOR_CRITICAL,
    INACTIVITY_ALERTS_CHANNEL_ID,
    ANNOUNCEMENTS_CHANNEL_ID,
    WAR_CHANNEL_ID,
    WAR_MANAGEMENT_CHANNEL_ID,
    CHAINING_CHANNEL_ID,
    WELCOME_CHANNEL_ID,
    RULES_CHANNEL_ID,
    GENERAL_CHANNEL_ID,
    EVENT_POSTING_CHANNEL_ID,
    WAR_TRACKER_CHANNEL_ID,
    AVAILABILITY_CHANNEL_ID,
    STRIKES_CHANNEL_ID,
    CLEANUP_REPORT_CHANNEL_ID,
    MEMBER_COMMANDS_CHANNEL_ID,
    MANAGEMENT_CHANNEL_ID,
    MANAGEMENT_PING_ROLE_ID,
    MANAGEMENT_ROLE_IDS,
    LEADER_USER_ID,
)

logger = logging.getLogger(__name__)

STATE_FILE = 'data/config_check.json'


def entries():
    """
    Everything the bot is configured to reach, with what breaks without it.

    Only ids that something actually uses belong here. A var nobody reads
    would report a problem that does not exist, which is how a warning
    channel gets ignored.
    """
    return [
        ('INACTIVITY_ALERTS_CHANNEL_ID', 'channel', INACTIVITY_ALERTS_CHANNEL_ID,
         'the daily inactivity, OC and trial alerts', True),
        ('STRIKES_CHANNEL_ID', 'channel', STRIKES_CHANNEL_ID,
         'strike candidates and the weekly digest', True),
        ('WAR_CHANNEL_ID', 'channel', WAR_CHANNEL_ID,
         'turtle alerts and the war board', True),
        ('WAR_MANAGEMENT_CHANNEL_ID', 'channel', WAR_MANAGEMENT_CHANNEL_ID,
         'war reports', True),
        ('AVAILABILITY_CHANNEL_ID', 'channel', AVAILABILITY_CHANNEL_ID,
         'the war poll and its chasers', True),
        ('CHAINING_CHANNEL_ID', 'channel', CHAINING_CHANNEL_ID,
         'chain alerts', True),
        ('WAR_TRACKER_CHANNEL_ID', 'channel', WAR_TRACKER_CHANNEL_ID,
         'enemy flight alerts', True),
        ('WELCOME_CHANNEL_ID', 'channel', WELCOME_CHANNEL_ID,
         'welcome messages and the weekly sweep', True),
        ('RULES_CHANNEL_ID', 'channel', RULES_CHANNEL_ID,
         'rules links in the welcome message', False),
        ('GENERAL_CHANNEL_ID', 'channel', GENERAL_CHANNEL_ID,
         'the fallback channel for events', False),
        ('ANNOUNCEMENTS_CHANNEL_ID', 'channel', ANNOUNCEMENTS_CHANNEL_ID,
         '/announce', False),
        ('EVENT_POSTING_CHANNEL_ID', 'channel', EVENT_POSTING_CHANNEL_ID,
         'event posts and reminders', False),
        ('CLEANUP_REPORT_CHANNEL_ID', 'channel', CLEANUP_REPORT_CHANNEL_ID,
         'the weekly cleanup report', False),
        ('MEMBER_COMMANDS_CHANNEL_ID', 'channel', MEMBER_COMMANDS_CHANNEL_ID,
         'the member command board', False),
        ('MANAGEMENT_CHANNEL_ID', 'channel', MANAGEMENT_CHANNEL_ID,
         'this report', False),
        ('MANAGEMENT_PING_ROLE_ID', 'role', MANAGEMENT_PING_ROLE_ID,
         'every @Management ping the bot sends', True),
        ('LEADER_USER_ID', 'user', LEADER_USER_ID,
         'the Xanax reimbursement ping', False),
    ] + [
        (f'MANAGEMENT_ROLE_IDS[{i}]', 'role', rid,
         'who the bot treats as management', True)
        for i, rid in enumerate(MANAGEMENT_ROLE_IDS or [])
    ]


def audit(rows, resolve):
    """
    Check each configured id against reality. Pure: resolve(kind, value)
    returns the object or None, so this is testable without Discord.

    Returns (broken, unset). An id of 0 is "deliberately not configured" and
    is reported separately from one that points at something deleted - the
    first is a choice, the second is a fault.
    """
    broken, unset = [], []
    for name, kind, value, purpose, required in rows:
        if not value:
            if required:
                unset.append((name, kind, value, purpose))
            continue
        if resolve(kind, value) is None:
            broken.append((name, kind, value, purpose))
    return broken, unset


def build_embed(broken, unset):
    """Render the findings. Returns None when there is nothing wrong."""
    if not broken and not unset:
        return None
    parts = []
    if broken:
        parts.append('**Points at something that no longer exists**\n'
                     + '\n'.join(
                         f'- `{name}` = `{value}` ({kind})\n'
                         f'-# Breaks: {purpose}'
                         for name, kind, value, purpose in broken))
    if unset:
        parts.append('**Not set at all**\n'
                     + '\n'.join(f'- `{name}` ({kind})\n-# Missing: {purpose}'
                                 for name, kind, value, purpose in unset))
    return create_embed(
        title='⚠️ Bot configuration problem',
        description=('\n\n'.join(parts)
                     + '\n\n-# Until this is fixed the feature above does '
                       'nothing and says nothing - no error, no alert. Fix '
                       'the id in the bot\'s env file, or recreate what it pointed at.'),
        color=EMBED_COLOR_CRITICAL)


async def report(bot, guild=None, state_file=STATE_FILE):
    """
    Run the check and post it where management will see it.

    Returns 'ok' when everything resolves, 'posted', 'unchanged' when the
    same problems were already reported, or None when it could not report.
    """
    if bot is None:
        return None
    guild = guild or (bot.guilds[0] if getattr(bot, 'guilds', None) else None)

    def resolve(kind, value):
        try:
            if kind == 'channel':
                return bot.get_channel(int(value))
            if kind == 'role':
                return guild.get_role(int(value)) if guild else None
            if kind == 'user':
                # A user id cannot be checked without an API call, and a
                # wrong one only costs a failed mention, so treat the guild
                # member lookup as best effort and never report a miss.
                return guild.get_member(int(value)) if guild else True
        except (TypeError, ValueError):
            return None
        return None

    broken, unset = audit(entries(), resolve)
    embed = build_embed(broken, unset)
    if embed is None:
        logger.info('Config check: every configured id resolves')
        return 'ok'

    for name, kind, value, purpose in broken + unset:
        logger.error(f'Config check: {name}={value} ({kind}) is unusable - '
                     f'{purpose} will silently do nothing')

    # Same problems as last time? Already said. Repeating it on every deploy
    # is how a warning becomes wallpaper.
    signature = ';'.join(sorted(f'{n}={v}' for n, _k, v, _p in broken + unset))
    state = load_json(state_file, default={}) or {}
    if state.get('signature') == signature:
        return 'unchanged'

    channel = None
    for cid in (MANAGEMENT_CHANNEL_ID, WAR_MANAGEMENT_CHANNEL_ID,
                INACTIVITY_ALERTS_CHANNEL_ID):
        if cid:
            channel = bot.get_channel(cid)
            if channel is not None:
                break
    if channel is None:
        logger.error('Config check: no management channel to report to')
        return None

    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f'Config check: could not post report: {e}')
        return None

    save_json(state_file, {'signature': signature})
    return 'posted'
