"""
Who is in Torn, who is in Discord, and who has fallen between the two.

Every alert the bot sends assumes those two lists match. They drift: people
join the faction and never join Discord, join Discord and never run
/register, leave the faction but stay in the server, or verify against a
Torn ID that is no longer on the roster. Each of those silently costs them
every war call, poll and mention the bot makes.

classify() is deliberately pure - it takes three plain collections and
returns four lists - because the interesting part is the set logic and that
should be testable without a Discord connection.
"""

import logging

from utils.helpers import create_embed
from config import EMBED_COLOR_WARNING, EMBED_COLOR_SUCCESS

logger = logging.getLogger(__name__)


def classify(roster, verified_users, guild_member_ids, bot_ids=()):
    """
    Sort everyone into the four ways Torn and Discord can disagree.

    Args:
        roster: {torn_id: {'name': ...}} from the faction API
        verified_users: {discord_id: {'torn_id': ..., 'torn_name': ...}}
        guild_member_ids: set of Discord ids currently in the server
        bot_ids: Discord ids to ignore, since a bot never registers

    Returns a dict of four lists. Every entry is (label, detail).
    """
    roster = {str(k): v for k, v in (roster or {}).items()}
    verified = {str(k): v for k, v in (verified_users or {}).items()}
    present = {str(x) for x in (guild_member_ids or set())}
    bots = {str(x) for x in (bot_ids or ())}

    # torn_id -> discord_id, for the verification records that point at a
    # Discord account actually still in the server.
    torn_to_discord = {}
    for did, rec in verified.items():
        tid = str((rec or {}).get('torn_id'))
        if tid and tid != 'None':
            torn_to_discord.setdefault(tid, []).append(did)

    not_in_discord, unregistered, left_faction, stale_link = [], [], [], []

    for tid, member in roster.items():
        name = (member or {}).get('name', f'ID:{tid}')
        linked = torn_to_discord.get(tid, [])
        here = [d for d in linked if d in present]
        if here:
            continue
        if linked:
            # They registered once, but that Discord account is gone from the
            # server. Worth separating: they know the bot exists.
            not_in_discord.append((name, f'registered as <@{linked[0]}>, '
                                         f'but that account has left the server'))
        else:
            not_in_discord.append((name, 'never registered, not in the server'))

    for did in sorted(present - bots):
        if did not in verified:
            unregistered.append((f'<@{did}>', 'in the server, never ran /register'))

    for did, rec in verified.items():
        tid = str((rec or {}).get('torn_id'))
        tname = (rec or {}).get('torn_name', tid)
        if tid in roster:
            continue
        if did in present:
            left_faction.append((f'<@{did}>', f'verified as **{tname}**, '
                                              f'not on the faction roster'))
        else:
            stale_link.append((tname, f'record points at Discord `{did}`, '
                                      f'which is not in the server either'))

    return {
        'not_in_discord': not_in_discord,
        'unregistered': unregistered,
        'left_faction': left_faction,
        'stale_link': stale_link,
    }


SECTIONS = [
    ('not_in_discord', '\U0001f534 In the faction, not reachable on Discord',
     'They miss every war call, poll and mention the bot sends.'),
    ('unregistered', '\U0001f7e0 In Discord, never registered',
     'The bot cannot map them to a Torn ID, so it can never @ them by name.'),
    ('left_faction', '\U0001f7e1 In Discord, not on the faction roster',
     'Left or removed, but still in the server.'),
    ('stale_link', '⚪ Dead verification record',
     'Points at a Torn ID off the roster AND a Discord account not in the '
     'server. Safe to clear.'),
]


def build_embed(result, roster_size, guild_size):
    """Render classify() output. Returns a single embed."""
    total = sum(len(v) for v in result.values())
    if not total:
        return create_embed(
            title='✅ Torn and Discord agree',
            description=(f'All **{roster_size}** faction members are in the '
                         f'server and registered, and nobody in the server is '
                         f'unaccounted for.'),
            color=EMBED_COLOR_SUCCESS)

    parts = []
    for key, title, why in SECTIONS:
        rows = result.get(key) or []
        if not rows:
            continue
        shown = rows[:15]
        body = '\n'.join(f'- **{label}** · {detail}' for label, detail in shown)
        if len(rows) > len(shown):
            body += f'\n- ...and {len(rows) - len(shown)} more'
        parts.append(f'**{title} — {len(rows)}**\n-# {why}\n{body}')

    desc = (f'Roster **{roster_size}**, server **{guild_size}**, '
            f'**{total}** mismatch(es).\n\n' + '\n\n'.join(parts))
    return create_embed(title='\U0001f50d Torn vs Discord',
                        description=desc[:4000], color=EMBED_COLOR_WARNING)


async def run_check(bot, guild, verification_system):
    """
    Fetch both sides and classify. Returns (embed, result) or (None, None)
    when the roster could not be read - an unreadable roster would otherwise
    read as "nobody is in the faction" and flag the entire server.
    """
    from utils.torn_api import fetch_faction_members
    roster = await fetch_faction_members()
    if not roster:
        logger.warning('Roster check: faction members unreadable, skipping')
        return None, None
    if not guild:
        logger.warning('Roster check: guild unavailable')
        return None, None

    verified = getattr(verification_system, 'verified_users', None) or {}
    present, bots = set(), set()
    for m in guild.members:
        present.add(str(m.id))
        if m.bot:
            bots.add(str(m.id))

    result = classify(roster, verified, present, bots)
    return build_embed(result, len(roster), len(present) - len(bots)), result


async def post_weekly_roster_check(bot, guild, verification_system, channel_id,
                                   weekday, state_file='data/roster_check.json',
                                   force=False):
    """
    Post the Torn-vs-Discord check once a week.

    Guarded by the ISO week stamp rather than "7 days since", so a restart
    cannot post it twice and a week the bot was down is skipped instead of
    arriving late. Same shape as the strike digest and the cleanup sweep.
    """
    import datetime as _dt
    from utils.helpers import load_json, save_json

    now = _dt.datetime.now(_dt.timezone.utc)
    year, week, wd = now.isocalendar()
    stamp = f'{year}-W{week:02d}'
    if not force:
        if wd - 1 != weekday:
            return False
        state = load_json(state_file, default={}) or {}
        if state.get('last_week') == stamp:
            return False

    channel = bot.get_channel(channel_id) if channel_id else None
    if not channel:
        logger.warning('Roster check: channel not configured')
        return False

    embed, result = await run_check(bot, guild, verification_system)
    if embed is None:
        return False      # unreadable roster: leave the week open to retry

    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f'Roster check post failed: {e}')
        return False

    save_json(state_file, {'last_week': stamp})
    logger.info(f'Posted weekly roster check {stamp}: '
                f'{sum(len(v) for v in result.values())} mismatch(es)')
    return True
