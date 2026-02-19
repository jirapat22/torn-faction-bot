"""
Faction Tools Module
Faction-level commands for comparison, intel, and management.

Features:
- /faction compare <faction_id> — Side-by-side overview comparison
- /faction stats <faction_id>   — Level distribution & battle strength analysis
"""

import discord
from discord import app_commands
from utils.helpers import create_embed, is_management
from utils.torn_api import fetch_faction_data, fetch_any_faction_data
from config import (
    TORN_FACTION_ID,
    EMBED_COLOR_INFO,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    EMBED_COLOR_SUCCESS,
)


def _format_number(n) -> str:
    try:
        return f'{int(n):,}'
    except (TypeError, ValueError):
        return 'N/A'


def _format_age(days) -> str:
    try:
        days = int(days)
        years = days // 365
        months = (days % 365) // 30
        if years > 0:
            return f'{years}y {months}m'
        return f'{months}m'
    except (TypeError, ValueError):
        return 'N/A'


def _avg_level(members: dict) -> float:
    if not members:
        return 0.0
    levels = [m.get('level', 0) for m in members.values() if isinstance(m, dict)]
    return sum(levels) / len(levels) if levels else 0.0


def _top_n_avg_level(members: dict, n: int = 10) -> float:
    """Average level of the top N highest-level members (approximates war fighters)"""
    if not members:
        return 0.0
    levels = sorted(
        [m.get('level', 0) for m in members.values() if isinstance(m, dict)],
        reverse=True
    )
    top = levels[:n]
    return sum(top) / len(top) if top else 0.0


def _level_buckets(members: dict) -> dict:
    """Count members in level brackets"""
    buckets = {'1-25': 0, '26-50': 0, '51-75': 0, '76-100': 0, '100+': 0}
    for m in members.values():
        if not isinstance(m, dict):
            continue
        lvl = m.get('level', 0)
        if lvl <= 25:
            buckets['1-25'] += 1
        elif lvl <= 50:
            buckets['26-50'] += 1
        elif lvl <= 75:
            buckets['51-75'] += 1
        elif lvl <= 100:
            buckets['76-100'] += 1
        else:
            buckets['100+'] += 1
    return buckets


def _parse_rankedwars(rankedwars: dict, faction_id: int) -> list:
    """
    Extract recent war results for a faction from rankedwars data.
    Uses score comparison as primary method (Torn often returns winner=0
    even for completed wars). Falls back to winner field if scores are equal.

    Returns list of result strings (W/L/D) for the last 5 wars, newest first.
    """
    if not rankedwars:
        return []

    fid_str = str(faction_id)
    results = []

    # Sort by end time descending (most recent first)
    sorted_wars = sorted(
        rankedwars.items(),
        key=lambda x: x[1].get('end', 0),
        reverse=True
    )

    for _, war in sorted_wars:
        if len(results) >= 5:
            break

        factions = war.get('factions', {})
        if fid_str not in factions:
            continue

        our_score = factions[fid_str].get('score', 0)
        other_scores = [
            v.get('score', 0) for k, v in factions.items() if k != fid_str
        ]
        opp_score = max(other_scores) if other_scores else 0

        if our_score > opp_score:
            results.append('W')
        elif our_score < opp_score:
            results.append('L')
        else:
            # Scores tied — check winner field as tiebreaker
            winner = war.get('winner', 0)
            if winner and str(winner) == fid_str:
                results.append('W')
            elif winner and str(winner) != fid_str:
                results.append('L')
            else:
                results.append('D')

    return results


def _record_line(results: list) -> str:
    if not results:
        return 'No ranked war data'
    wins = results.count('W')
    losses = results.count('L')
    draws = results.count('D')
    return f'{" ".join(results)}  ({wins}W {losses}L {draws}D)'


async def setup_faction_commands(bot):
    """Register /faction slash commands"""

    @bot.tree.command(
        name='faction',
        description='[Management] Faction tools: compare overview or analyse battle strength'
    )
    @app_commands.describe(
        action='What to show',
        faction_id='Torn faction ID (required for Compare and Stats)'
    )
    @app_commands.choices(action=[
        app_commands.Choice(name='Compare - Side-by-side faction overview', value='compare'),
        app_commands.Choice(name='Stats - Level distribution & battle strength', value='stats'),
    ])
    async def faction_cmd(
        interaction: discord.Interaction,
        action: str,
        faction_id: int = None
    ):
        if not is_management(interaction):
            await interaction.response.send_message(
                embed=create_embed(
                    title='Permission Denied',
                    description='You need Management permissions to use faction tools.',
                    color=EMBED_COLOR_CRITICAL
                ),
                ephemeral=True
            )
            return

        if not faction_id:
            await interaction.response.send_message(
                embed=create_embed(
                    title='Missing Faction ID',
                    description='Please provide a `faction_id`.\nFind it on the faction\'s Torn profile page.',
                    color=EMBED_COLOR_WARNING
                ),
                ephemeral=True
            )
            return

        await interaction.response.defer()

        # --- Fetch both factions ---
        import asyncio
        own_basic, enemy_basic, own_wars, enemy_wars = await asyncio.gather(
            fetch_faction_data('basic'),
            fetch_any_faction_data(faction_id, 'basic'),
            fetch_faction_data('rankedwars'),
            fetch_any_faction_data(faction_id, 'rankedwars'),
        )

        if not enemy_basic:
            await interaction.followup.send(
                embed=create_embed(
                    title='Faction Not Found',
                    description=f'Could not fetch data for faction ID `{faction_id}`.\n'
                                'Check the ID is correct and your API key has access.',
                    color=EMBED_COLOR_WARNING
                )
            )
            return

        # --- Shared parsed values ---
        own_name    = own_basic.get('name', 'Us') if own_basic else 'Us'
        own_tag     = own_basic.get('tag', '') if own_basic else ''
        own_members = own_basic.get('members', {}) if own_basic else {}

        enemy_name    = enemy_basic.get('name', f'Faction {faction_id}')
        enemy_tag     = enemy_basic.get('tag', '')
        enemy_members = enemy_basic.get('members', {})

        own_label   = (f'[{own_tag}] {own_name}' if own_tag else own_name)[:16]
        enemy_label = (f'[{enemy_tag}] {enemy_name}' if enemy_tag else enemy_name)[:16]

        torn_link = f'https://www.torn.com/factions.php?step=profile&ID={faction_id}'

        # ── COMPARE VIEW ──────────────────────────────────────────────
        if action == 'compare':
            own_respect    = own_basic.get('respect', 0) if own_basic else 0
            own_best_chain = own_basic.get('best_chain', 0) if own_basic else 0
            own_age        = own_basic.get('age', 0) if own_basic else 0
            own_rank       = (own_basic.get('rank', {}) or {}) if own_basic else {}

            enemy_respect    = enemy_basic.get('respect', 0)
            enemy_best_chain = enemy_basic.get('best_chain', 0)
            enemy_age        = enemy_basic.get('age', 0)
            enemy_rank       = enemy_basic.get('rank', {}) or {}

            own_rw    = own_wars.get('rankedwars', {}) if own_wars else {}
            enemy_rw  = enemy_wars.get('rankedwars', {}) if enemy_wars else {}
            own_rec   = _parse_rankedwars(own_rw, TORN_FACTION_ID)
            enemy_rec = _parse_rankedwars(enemy_rw, faction_id)

            own_rank_name   = own_rank.get('name', 'N/A') if isinstance(own_rank, dict) else str(own_rank)
            enemy_rank_name = enemy_rank.get('name', 'N/A') if isinstance(enemy_rank, dict) else str(enemy_rank)

            COL = 14
            def row(label, a, b):
                return f'{label:<13} {str(a):<{COL}} {str(b)}'

            header  = f'{"":13} {own_label:<{COL}} {enemy_label}'
            divider = '─' * max(len(header), 40)

            table = '\n'.join([
                header, divider,
                row('Members',    len(own_members),                len(enemy_members)),
                row('Avg Level',  f'{_avg_level(own_members):.1f}', f'{_avg_level(enemy_members):.1f}'),
                row('Best Chain', _format_number(own_best_chain),  _format_number(enemy_best_chain)),
                row('Respect',    _format_number(own_respect),     _format_number(enemy_respect)),
                row('Age',        _format_age(own_age),            _format_age(enemy_age)),
                row('Rank',       own_rank_name,                   enemy_rank_name),
            ])

            war_section = (
                f'**{own_name}:** {_record_line(own_rec)}\n'
                f'**{enemy_name}:** {_record_line(enemy_rec)}'
            )

            embed = create_embed(
                title='Faction Comparison',
                description=(
                    f'```\n{table}\n```\n'
                    f'**Recent Wars (last 5):**\n{war_section}\n\n'
                    f'[View {enemy_name} on Torn]({torn_link})'
                ),
                color=EMBED_COLOR_INFO
            )
            await interaction.followup.send(embed=embed)

        # ── STATS VIEW ────────────────────────────────────────────────
        elif action == 'stats':
            own_best_chain   = own_basic.get('best_chain', 0) if own_basic else 0
            enemy_best_chain = enemy_basic.get('best_chain', 0)

            own_avg    = _avg_level(own_members)
            enemy_avg  = _avg_level(enemy_members)
            own_top10  = _top_n_avg_level(own_members, 10)
            enemy_top10= _top_n_avg_level(enemy_members, 10)

            own_cnt   = len(own_members)
            enemy_cnt = len(enemy_members)

            # Chain efficiency (best chain per member — higher = more active chainers)
            own_chain_eff   = own_best_chain / own_cnt if own_cnt else 0
            enemy_chain_eff = enemy_best_chain / enemy_cnt if enemy_cnt else 0

            # Level buckets
            own_b   = _level_buckets(own_members)
            enemy_b = _level_buckets(enemy_members)

            COL = 8
            def brow(label, a, b):
                return f'{label:<10} {str(a):>{COL}} {str(b):>{COL}}'

            bucket_header  = f'{"Level":<10} {own_label[:COL]:>{COL}} {enemy_label[:COL]:>{COL}}'
            bucket_divider = '─' * len(bucket_header)
            bucket_table = '\n'.join([
                bucket_header, bucket_divider,
                brow('Lvl 1-25',   own_b['1-25'],   enemy_b['1-25']),
                brow('Lvl 26-50',  own_b['26-50'],  enemy_b['26-50']),
                brow('Lvl 51-75',  own_b['51-75'],  enemy_b['51-75']),
                brow('Lvl 76-100', own_b['76-100'], enemy_b['76-100']),
                brow('Lvl 100+',   own_b['100+'],   enemy_b['100+']),
                bucket_divider,
                brow('Total',      own_cnt,          enemy_cnt),
            ])

            COL2 = 14
            def srow(label, a, b):
                return f'{label:<14} {str(a):<{COL2}} {str(b)}'

            stats_header  = f'{"":14} {own_label:<{COL2}} {enemy_label}'
            stats_divider = '─' * max(len(stats_header), 40)
            stats_table = '\n'.join([
                stats_header, stats_divider,
                srow('Avg Level',    f'{own_avg:.1f}',       f'{enemy_avg:.1f}'),
                srow('Top 10 Avg',   f'{own_top10:.1f}',     f'{enemy_top10:.1f}'),
                srow('Best Chain',   _format_number(own_best_chain), _format_number(enemy_best_chain)),
                srow('Chain/Member', f'{own_chain_eff:.1f}', f'{enemy_chain_eff:.1f}'),
            ])

            # ── Insights ──
            insights = []

            # Overall level advantage
            if own_avg > enemy_avg + 2:
                insights.append(f'✅ **Level edge:** Your avg level ({own_avg:.1f}) is notably higher — expect an overall stat advantage.')
            elif enemy_avg > own_avg + 2:
                insights.append(f'⚠️ **Level gap:** Their avg level ({enemy_avg:.1f}) is higher — their members likely have stronger battle stats.')
            else:
                insights.append(f'⚖️ **Evenly matched** on average level ({own_avg:.1f} vs {enemy_avg:.1f}).')

            # Top fighters comparison (most relevant for war)
            if own_top10 > enemy_top10 + 3:
                insights.append(f'✅ **Top fighters stronger:** Your top 10 avg ({own_top10:.1f}) outclasses theirs ({enemy_top10:.1f}) — good for carrying war hits.')
            elif enemy_top10 > own_top10 + 3:
                insights.append(f'⚠️ **Their top fighters:** Top 10 avg ({enemy_top10:.1f} vs yours {own_top10:.1f}) — their core fighters may be harder to beat.')
            else:
                insights.append(f'⚖️ **Top fighters comparable** ({own_top10:.1f} vs {enemy_top10:.1f}) — war outcome will depend on activity and strategy.')

            # Low-level members (easy targets)
            enemy_low = enemy_b['1-25'] + enemy_b['26-50']
            own_low   = own_b['1-25'] + own_b['26-50']
            if enemy_low > 0:
                insights.append(f'🎯 **Soft targets:** They have {enemy_low} members below level 50 — easier hits for score.')
            if own_low > 0:
                insights.append(f'🛡️ **Protect your low-levels:** You have {own_low} members below 50 who may be targeted for easy score.')

            # Size difference
            if own_cnt > enemy_cnt + 5:
                insights.append(f'✅ **Numbers advantage:** {own_cnt} vs {enemy_cnt} — more members means more potential hits per hour.')
            elif enemy_cnt > own_cnt + 5:
                insights.append(f'⚠️ **Outnumbered:** {enemy_cnt} vs {own_cnt} — they can sustain more hits per hour. Activity discipline is critical.')

            # Chain efficiency
            if own_chain_eff > enemy_chain_eff * 1.3:
                insights.append(f'✅ **Chain culture:** Your chain efficiency ({own_chain_eff:.1f}/member) beats theirs ({enemy_chain_eff:.1f}) — your members hit more actively.')
            elif enemy_chain_eff > own_chain_eff * 1.3:
                insights.append(f'⚠️ **They chain more:** Their chain efficiency ({enemy_chain_eff:.1f}/member) vs yours ({own_chain_eff:.1f}) — they may be more active in war hits too.')

            # Battle stat note
            insights.append(
                '📌 **Note:** Actual battle stats (Str/Spd/Def/Dex) are private. '
                'Level is the best public proxy — higher level generally means stronger stats, '
                'but gym training focus matters a lot at equal levels.'
            )

            insights_text = '\n'.join(insights)

            embed = create_embed(
                title=f'Battle Strength Analysis',
                description=(
                    f'**Level Distribution:**\n```\n{bucket_table}\n```\n'
                    f'**Power Indicators:**\n```\n{stats_table}\n```\n'
                    f'**Insights:**\n{insights_text}\n\n'
                    f'[View {enemy_name} on Torn]({torn_link})'
                ),
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed)

    print('Faction commands registered (/faction compare, /faction stats)')
