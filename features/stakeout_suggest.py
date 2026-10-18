"""
Who to stake out when a war is declared.

Adding stakeouts by hand means reading the enemy roster, deciding who matters
and typing an ID for each one - tedious enough that it mostly does not happen,
and the people worth watching are exactly the ones you find out about the
hard way.

So when a war posts, the bot names the handful worth watching and hands over
the commands ready to run. It deliberately stops at suggesting: a stakeout
costs API calls on a timer, and a bot that silently started watching eight
people would be spending that budget on its own authority.

Who is "worth watching": their strongest, measured against OUR roster rather
than in the abstract, because that is the only comparison that means
anything. The banding comes from faction.threat_bands so the watch list and
the war board can never disagree about the same enemy.
"""

import logging

from utils.helpers import create_embed
from config import EMBED_COLOR_WARNING

logger = logging.getLogger(__name__)

# Enough to cover the people who actually decide a war, few enough that
# somebody will really add them. Past this it becomes a list to ignore.
SUGGEST_LIMIT = 6


def pick_targets(enemy_members, own_members, ff_data, has_ff,
                 limit=SUGGEST_LIMIT):
    """
    Pick the enemies worth a stakeout. Pure, so the choice is testable.

    Returns [(torn_id, name, level, bs_or_None, band)] worst-first, where
    band is 'AVOID' or 'CAUTION'. Soft targets are never suggested - knowing
    when somebody you can already beat wakes up is not worth an API call.

    An empty enemy roster returns [] rather than guessing. Note this reads
    the roster it is GIVEN: an unreadable one is the caller's problem to
    catch, because suggesting nobody and failing to fetch look identical
    from in here.
    """
    from features.faction import threat_bands

    if not enemy_members:
        return []

    label, _is_soft, _bench = threat_bands(own_members, ff_data, has_ff)

    rows = []
    for tid, m in enemy_members.items():
        if not isinstance(m, dict):
            continue
        name = m.get('name', f'ID:{tid}')
        lvl = m.get('level', 0) or 0
        p = (ff_data or {}).get(str(tid))
        bs = int(p['bs_estimate']) if p and p.get('bs_estimate') else None
        band = label(bs or 0, lvl)
        if band not in ('AVOID', 'CAUTION'):
            continue
        rows.append((str(tid), name, lvl, bs, band))

    # Worst first: AVOID above CAUTION, then by whatever measure we have.
    rows.sort(key=lambda r: (0 if r[4] == 'AVOID' else 1,
                             -(r[3] or 0), -r[2]))
    return rows[:limit]


def build_embed(targets, enemy_label, has_ff):
    """Render the suggestions. Returns None when there is nothing to say."""
    if not targets:
        return None

    lines = []
    for tid, name, lvl, bs, band in targets:
        stat = f'{bs:,}' if bs else f'level {lvl}'
        lines.append(f'`{tid}` **{name}** — {band}, {stat}')

    cmds = '\n'.join(f'/enemy stakeout add torn_id:{t[0]}' for t in targets[:3])
    note = ('' if has_ff else
            '\n-# No FFScouter data, so these are ranked by level. Treat the '
            'order as rough.')

    return create_embed(
        title=f'\U0001f575️ Worth watching · {enemy_label}',
        description=(
            f'The {len(targets)} enemies most likely to hurt us, measured '
            f'against our own roster.\n\n' + '\n'.join(lines) +
            f'\n\n**Add the ones you want:**\n```\n{cmds}\n```'
            f'\n-# Nothing is being watched yet. A stakeout tells you when '
            f'they come online or leave hospital, so pick the ones you plan '
            f'to hit or hide from.' + note),
        color=EMBED_COLOR_WARNING)


async def post_suggestions(bot, channel_id, enemy_faction_id, enemy_label=''):
    """
    Fetch both rosters, pick targets, post. Returns the number suggested,
    or None when it could not run - an unreadable enemy roster says nothing
    rather than posting an empty "nobody is dangerous".
    """
    if not bot or not channel_id or not enemy_faction_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning('Stakeout suggestions: channel not found')
        return None

    from utils.torn_api import (fetch_faction_members, fetch_any_faction_data,
                                fetch_ffscouter_stats)
    from config import FFSCOUTER_API_KEY

    enemy = await fetch_any_faction_data(enemy_faction_id, selections='basic')
    enemy_members = (enemy or {}).get('members') or {}
    own_members = await fetch_faction_members()
    if not enemy_members or not own_members:
        logger.warning('Stakeout suggestions: roster unreadable, skipping')
        return None

    # FFScouter is optional. Without it the bands fall back to level, which
    # is rough but still beats management picking names blind.
    ff_data = {}
    if FFSCOUTER_API_KEY:
        ff_data = await fetch_ffscouter_stats(
            list(own_members.keys()) + list(enemy_members.keys())) or {}
    has_ff = bool(ff_data)

    targets = pick_targets(enemy_members, own_members, ff_data, has_ff)
    embed = build_embed(targets, enemy_label or str(enemy_faction_id), has_ff)
    if embed is None:
        logger.info('Stakeout suggestions: nobody above our own average')
        return 0

    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f'Stakeout suggestions: post failed: {e}')
        return None
    logger.info(f'Stakeout suggestions: posted {len(targets)} target(s)')
    return len(targets)
