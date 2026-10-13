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
from utils.torn_api import fetch_faction_data, fetch_any_faction_data, fetch_ffscouter_stats
from config import (
    TORN_FACTION_ID,
    EMBED_COLOR_INFO,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    EMBED_COLOR_SUCCESS,
)


import logging

logger = logging.getLogger(__name__)

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


def _format_bs(n) -> str:
    """Format battle stat number into human-readable form (e.g. 2,990,000,000 → 2.99b)"""
    try:
        n = int(n)
        if n >= 1_000_000_000:
            return f'{n / 1_000_000_000:.2f}b'
        if n >= 1_000_000:
            return f'{n / 1_000_000:.1f}m'
        if n >= 1_000:
            return f'{n / 1_000:.1f}k'
        return str(n)
    except (TypeError, ValueError):
        return 'N/A'


def _stat_tier(bs: int) -> str:
    """Return tier label for a battle stat estimate"""
    if bs >= 25_000_000_000:
        return 'Elite  (25b+)'
    if bs >= 5_000_000_000:
        return 'V.Strong(5b+)'
    if bs >= 1_000_000_000:
        return 'Strong  (1b+)'
    if bs >= 100_000_000:
        return 'Moderate(100m+)'
    return 'Developing   '


def _bs_summary(ff_data: dict, member_ids: list) -> dict:
    """
    Compute battle stat summary for a set of member IDs given FFScouter data.

    Returns:
        {estimates: [ints], no_data: int, avg: float, top10_avg: float,
         highest: int, lowest: int, tiers: {label: count}}
    """
    estimates = []
    no_data = 0
    for tid in member_ids:
        p = ff_data.get(str(tid))
        if p and p.get('bs_estimate'):
            estimates.append(int(p['bs_estimate']))
        else:
            no_data += 1

    tiers = {'Elite  (25b+)': 0, 'V.Strong(5b+)': 0,
              'Strong  (1b+)': 0, 'Moderate(100m+)': 0, 'Developing   ': 0}
    for bs in estimates:
        tiers[_stat_tier(bs)] += 1

    top10 = sorted(estimates, reverse=True)[:10]
    return {
        'estimates': estimates,
        'no_data': no_data,
        'avg': sum(estimates) / len(estimates) if estimates else 0,
        'top10_avg': sum(top10) / len(top10) if top10 else 0,
        'highest': max(estimates) if estimates else 0,
        'lowest': min(estimates) if estimates else 0,
        'tiers': tiers,
    }


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


def _build_threat_embed(enemy_members, own_members, ff_data, has_ff,
                         enemy_name, enemy_tag, faction_id):
    """
    Build a per-member threat assessment embed.
    Classifies enemy members relative to OUR OWN faction stats:
      - AVOID   : their stats > our top-3 average  (they beat our best)
      - CAUTION : their stats > our faction average (above our average)
      - Soft    : their stats < our faction average (we can likely beat them)
    Falls back to level comparison when FF data is unavailable.
    """
    from config import EMBED_COLOR_CRITICAL, EMBED_COLOR_WARNING, EMBED_COLOR_INFO

    enemy_label = f'[{enemy_tag}] {enemy_name}' if enemy_tag else enemy_name

    # ── Compute OUR benchmarks ──
    own_stats = []
    for tid, mdata in own_members.items():
        p = ff_data.get(str(tid))
        if p and p.get('bs_estimate'):
            own_stats.append(int(p['bs_estimate']))

    own_stats_sorted = sorted(own_stats, reverse=True)
    own_avg_bs   = sum(own_stats) / len(own_stats) if own_stats else 0
    own_top3_avg = sum(own_stats_sorted[:3]) / min(3, len(own_stats_sorted)) if own_stats_sorted else 0

    # Level benchmarks (fallback when no FF data)
    own_levels = [m.get('level', 0) for m in own_members.values() if isinstance(m, dict)]
    own_avg_lvl  = sum(own_levels) / len(own_levels) if own_levels else 0
    own_top3_lvl = sum(sorted(own_levels, reverse=True)[:3]) / min(3, len(own_levels)) if own_levels else 0

    use_relative_bs = has_ff and own_avg_bs > 0

    def danger_label(bs, lvl):
        if use_relative_bs:
            if bs >= own_top3_avg:
                return ' ◀ AVOID'
            if bs >= own_avg_bs:
                return ' ◀ CAUTION'
        else:
            if lvl >= own_top3_lvl:
                return ' ◀ AVOID'
            if lvl >= own_avg_lvl:
                return ' ◀ CAUTION'
        return ''

    def is_soft_target(bs, lvl):
        if use_relative_bs:
            return bs < own_avg_bs
        return lvl < own_avg_lvl

    # ── Classify enemy members ──
    known   = []  # (tid, name, lvl, bs)
    unknown = []  # (tid, name, lvl)

    for tid, mdata in enemy_members.items():
        name = mdata.get('name', f'ID:{tid}')
        lvl  = mdata.get('level', 0)
        p    = ff_data.get(str(tid))
        if p and p.get('bs_estimate'):
            known.append((tid, name, lvl, int(p['bs_estimate'])))
        else:
            unknown.append((tid, name, lvl))

    known.sort(key=lambda x: x[3], reverse=True)
    unknown.sort(key=lambda x: x[2], reverse=True)

    # ── Build tables ──
    NW, LW, SW = 16, 4, 9
    div = '─' * (4 + NW + LW + SW + 10)
    hdr = f'{"#":>3} {"Name":<{NW}} {"Lvl":>{LW}}  {"Est.Stats":>{SW}}'

    threat_lines = [hdr, div]
    soft_lines   = [hdr, div]
    avoid_count  = 0
    caution_count= 0
    soft_count   = 0

    for i, (tid, name, lvl, bs) in enumerate(known, 1):
        flag = danger_label(bs, lvl)
        row  = f'{i:>3} {name[:NW]:<{NW}} {lvl:>{LW}}  {_format_bs(bs):>{SW}}{flag}'
        if is_soft_target(bs, lvl):
            soft_lines.append(row)
            soft_count += 1
        else:
            threat_lines.append(row)
            if '◀ AVOID' in flag:
                avoid_count += 1
            elif '◀ CAUTION' in flag:
                caution_count += 1

    # Unknown stats section (sorted by level, labelled by level benchmark)
    if unknown:
        threat_lines.append('')
        threat_lines.append('  ❓ No FFScouter data (sorted by level):')
        unk_div = '─' * (4 + NW + LW + 4)
        unk_hdr = f'{"#":>3} {"Name":<{NW}} {"Lvl":>{LW}}'
        threat_lines += [unk_hdr, unk_div]
        for i, (tid, name, lvl) in enumerate(unknown, 1):
            flag = danger_label(0, lvl)
            threat_lines.append(f'{i:>3} {name[:NW]:<{NW}} {lvl:>{LW}}{flag}')

    threat_table = '\n'.join(threat_lines)
    soft_table   = '\n'.join(soft_lines) if soft_count else None

    # ── Our top fighters ──
    own_known = []
    for tid, mdata in own_members.items():
        name = mdata.get('name', f'ID:{tid}')
        lvl  = mdata.get('level', 0)
        p    = ff_data.get(str(tid))
        bs   = int(p['bs_estimate']) if p and p.get('bs_estimate') else 0
        own_known.append((name, lvl, bs))
    own_known.sort(key=lambda x: x[2] if has_ff else x[1], reverse=True)

    own_lines = [hdr, div]
    for i, (name, lvl, bs) in enumerate(own_known[:10], 1):
        bs_str = _format_bs(bs) if bs else '  unknown'
        own_lines.append(f'{i:>3} {name[:NW]:<{NW}} {lvl:>{LW}}  {bs_str:>{SW}}')
    own_table = '\n'.join(own_lines)

    # ── Benchmark legend ──
    if use_relative_bs:
        legend = (
            f'Thresholds based on your faction:\n'
            f'  ◀ AVOID   = above your top-3 avg ({_format_bs(own_top3_avg)})\n'
            f'  ◀ CAUTION = above your faction avg ({_format_bs(own_avg_bs)})\n'
            f'  Soft      = below your faction avg'
        )
    else:
        legend = (
            f'Thresholds based on your faction (level):\n'
            f'  ◀ AVOID   = above your top-3 avg level ({own_top3_lvl:.0f})\n'
            f'  ◀ CAUTION = above your faction avg level ({own_avg_lvl:.0f})\n'
            f'  Soft      = below your faction avg level'
        )

    # ── Assemble ──
    data_note = '*(via FFScouter)*' if has_ff else '*(sorted by level, add FFSCOUTER_API_KEY for real stats)*'
    desc = (
        f'**⚠️ Enemy Roster · {enemy_label}** {data_note}\n'
        f'```\n{legend}\n```\n'
        f'```\n{threat_table}\n```\n'
    )
    if soft_table:
        desc += f'**🎯 Soft Targets** *(below your faction avg, you have the stat edge)*\n```\n{soft_table}\n```\n'

    desc += f'**✅ Our Top Fighters:**\n```\n{own_table}\n```\n'

    if avoid_count:
        desc += f'\n> **{avoid_count} member(s) ◀ AVOID**. Outstat your top 3. Use your strongest members to hospital them, then leave them alone.'
    if caution_count:
        desc += f'\n> **{caution_count} member(s) ◀ CAUTION**. Above your faction average. Approach selectively.'

    if avoid_count >= 5:
        color = EMBED_COLOR_CRITICAL
    elif avoid_count >= 2 or caution_count >= 5:
        color = EMBED_COLOR_WARNING
    else:
        color = EMBED_COLOR_INFO

    return create_embed(
        title=f'Threat Assessment · {enemy_label}',
        description=desc,
        color=color
    )


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

            own_cnt   = len(own_members)
            enemy_cnt = len(enemy_members)

            own_avg     = _avg_level(own_members)
            enemy_avg   = _avg_level(enemy_members)
            own_top10   = _top_n_avg_level(own_members, 10)
            enemy_top10 = _top_n_avg_level(enemy_members, 10)

            own_chain_eff   = own_best_chain / own_cnt if own_cnt else 0
            enemy_chain_eff = enemy_best_chain / enemy_cnt if enemy_cnt else 0

            own_b   = _level_buckets(own_members)
            enemy_b = _level_buckets(enemy_members)

            # ── FFScouter battle stat lookup ──
            from config import FFSCOUTER_API_KEY
            all_ids = list(own_members.keys()) + list(enemy_members.keys())
            ff_data = await fetch_ffscouter_stats(all_ids) if FFSCOUTER_API_KEY else {}

            own_bs   = _bs_summary(ff_data, list(own_members.keys()))
            enemy_bs = _bs_summary(ff_data, list(enemy_members.keys()))
            has_ff   = bool(ff_data)

            # ── Level bucket table ──
            COL = 8
            def brow(label, a, b):
                return f'{label:<14} {str(a):>{COL}} {str(b):>{COL}}'

            bl = '─' * (14 + COL * 2 + 2)
            bucket_table = '\n'.join([
                f'{"Level":<14} {own_label[:COL]:>{COL}} {enemy_label[:COL]:>{COL}}', bl,
                brow('Lvl 1-25',   own_b['1-25'],   enemy_b['1-25']),
                brow('Lvl 26-50',  own_b['26-50'],  enemy_b['26-50']),
                brow('Lvl 51-75',  own_b['51-75'],  enemy_b['51-75']),
                brow('Lvl 76-100', own_b['76-100'], enemy_b['76-100']),
                brow('Lvl 100+',   own_b['100+'],   enemy_b['100+']),
                bl,
                brow('Total',      own_cnt,          enemy_cnt),
            ])

            # ── Stat summary table (FFScouter or level-only) ──
            COL2 = 14
            def srow(label, a, b):
                return f'{label:<14} {str(a):<{COL2}} {str(b)}'
            sl = '─' * (14 + COL2 * 2 + 2)
            sh = f'{"":14} {own_label:<{COL2}} {enemy_label}'

            if has_ff:
                stat_rows = [
                    sh, sl,
                    srow('Avg Stats',   _format_bs(own_bs['avg']),       _format_bs(enemy_bs['avg'])),
                    srow('Top 10 Avg',  _format_bs(own_bs['top10_avg']), _format_bs(enemy_bs['top10_avg'])),
                    srow('Highest',     _format_bs(own_bs['highest']),   _format_bs(enemy_bs['highest'])),
                    srow('Lowest',      _format_bs(own_bs['lowest']),    _format_bs(enemy_bs['lowest'])),
                    srow('No FF data',  own_bs['no_data'],               enemy_bs['no_data']),
                    sl,
                    srow('Avg Level',   f'{own_avg:.1f}',                f'{enemy_avg:.1f}'),
                    srow('Best Chain',  _format_number(own_best_chain),  _format_number(enemy_best_chain)),
                ]

                # Tier distribution
                tier_rows = [f'{"Tier":<16} {own_label[:COL]:>{COL}} {enemy_label[:COL]:>{COL}}', bl]
                for tier in ['Elite  (25b+)', 'V.Strong(5b+)', 'Strong  (1b+)', 'Moderate(100m+)', 'Developing   ']:
                    o = own_bs['tiers'].get(tier, 0)
                    e = enemy_bs['tiers'].get(tier, 0)
                    if o or e:
                        tier_rows.append(brow(tier, o, e))
                tier_table = '\n'.join(tier_rows)
            else:
                stat_rows = [
                    sh, sl,
                    srow('Avg Level',   f'{own_avg:.1f}',               f'{enemy_avg:.1f}'),
                    srow('Top 10 Avg',  f'{own_top10:.1f}',             f'{enemy_top10:.1f}'),
                    srow('Best Chain',  _format_number(own_best_chain), _format_number(enemy_best_chain)),
                    srow('Chain/Mbr',   f'{own_chain_eff:.1f}',         f'{enemy_chain_eff:.1f}'),
                ]
                tier_table = None

            stats_table = '\n'.join(stat_rows)

            # ── Insights ──
            insights = []

            if has_ff and own_bs['estimates'] and enemy_bs['estimates']:
                own_power   = own_bs['top10_avg']
                enemy_power = enemy_bs['top10_avg']
                ratio = own_power / enemy_power if enemy_power else 1.0

                if ratio >= 1.3:
                    insights.append(f'✅ **Strong stat advantage:** Your top fighters avg {_format_bs(own_power)} vs their {_format_bs(enemy_power)}. Expect to win most 1v1s.')
                elif ratio <= 0.77:
                    insights.append(f'⚠️ **Stat disadvantage:** Their top fighters avg {_format_bs(enemy_power)} vs yours {_format_bs(own_power)}. Avoid extended 1v1 duels. Hit fast, hospital quickly.')
                else:
                    insights.append(f'⚖️ **Evenly matched:** Top fighter stats are close ({_format_bs(own_power)} vs {_format_bs(enemy_power)}). Activity and timing will decide the war.')

                # Soft targets
                enemy_dev = enemy_bs['tiers'].get('Developing   ', 0) + enemy_bs['tiers'].get('Moderate(100m+)', 0)
                if enemy_dev > 0:
                    insights.append(f'🎯 **Soft targets:** {enemy_dev} of their members are Developing/Moderate. Prioritise these for easy score hits.')

                own_dev = own_bs['tiers'].get('Developing   ', 0) + own_bs['tiers'].get('Moderate(100m+)', 0)
                if own_dev > 0:
                    insights.append(f'🛡️ **Protect your weaker members:** {own_dev} of your members are lower stat. They are easy targets. Keep them hospitalised or travelling during war.')

                if own_bs['no_data'] > 3:
                    insights.append(f'ℹ️ **{own_bs["no_data"]} of your members** have no FFScouter data. Their stats are unknown. Encourage them to use FFScouter.')
                if enemy_bs['no_data'] > 3:
                    insights.append(f'ℹ️ **{enemy_bs["no_data"]} of their members** have no FFScouter data. Hidden unknowns. Treat them cautiously.')
            else:
                # Level-based fallback insights
                if own_avg > enemy_avg + 2:
                    insights.append(f'✅ **Level edge:** Avg {own_avg:.1f} vs {enemy_avg:.1f}. Expect an overall stat advantage.')
                elif enemy_avg > own_avg + 2:
                    insights.append(f'⚠️ **Level gap:** Their avg {enemy_avg:.1f} vs yours {own_avg:.1f}. They likely have stronger stats.')
                else:
                    insights.append(f'⚖️ **Level-matched** ({own_avg:.1f} vs {enemy_avg:.1f}). War outcome will depend on training focus and activity.')

                enemy_low = enemy_b['1-25'] + enemy_b['26-50']
                own_low   = own_b['1-25'] + own_b['26-50']
                if enemy_low:
                    insights.append(f'🎯 **Soft targets:** {enemy_low} members below level 50.')
                if own_low:
                    insights.append(f'🛡️ **Protect your low-levels:** {own_low} members below 50 are easy targets.')

                if not FFSCOUTER_API_KEY:
                    insights.append('📌 **Add FFScouter:** Set `FFSCOUTER_API_KEY` in your `.env` for real estimated stat data instead of level estimates.')

            if own_cnt > enemy_cnt + 5:
                insights.append(f'✅ **Numbers advantage:** {own_cnt} vs {enemy_cnt} members. More hits per hour possible.')
            elif enemy_cnt > own_cnt + 5:
                insights.append(f'⚠️ **Outnumbered:** {enemy_cnt} vs {own_cnt}. Stay disciplined, hospital their actives.')

            if own_chain_eff > enemy_chain_eff * 1.3:
                insights.append(f'✅ **More active chainers:** Your chain efficiency ({own_chain_eff:.1f}/mbr) beats theirs ({enemy_chain_eff:.1f}).')
            elif enemy_chain_eff > own_chain_eff * 1.3:
                insights.append(f'⚠️ **They chain harder:** Their efficiency ({enemy_chain_eff:.1f}/mbr) vs yours ({own_chain_eff:.1f}). Expect sustained pressure.')

            insights_text = '\n'.join(insights)

            ff_note = '*(Stats via FFScouter, estimates only)*' if has_ff else '*(Add `FFSCOUTER_API_KEY` for real stat estimates)*'
            desc = (
                f'**Level Distribution:**\n```\n{bucket_table}\n```\n'
                f'**Battle Stats:** {ff_note}\n```\n{stats_table}\n```\n'
            )
            if tier_table:
                desc += f'**Stat Tiers:**\n```\n{tier_table}\n```\n'
            desc += f'**Insights:**\n{insights_text}\n\n[View {enemy_name} on Torn]({torn_link})'

            embed = create_embed(
                title='Battle Strength Analysis',
                description=desc,
                color=EMBED_COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed)

            # ── Threat Assessment embed ──────────────────────────────
            threat_embed = _build_threat_embed(
                enemy_members=enemy_members,
                own_members=own_members,
                ff_data=ff_data,
                has_ff=has_ff,
                enemy_name=enemy_name,
                enemy_tag=enemy_tag,
                faction_id=faction_id,
            )
            await interaction.followup.send(embed=threat_embed)

    logger.info('Faction commands registered (/faction compare, /faction stats)')
