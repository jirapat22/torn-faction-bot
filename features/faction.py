"""
Faction Tools Module
Faction-level commands for comparison, intel, and management.

Features:
- /faction compare <faction_id> — Side-by-side comparison vs another faction
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
)


def _format_number(n) -> str:
    """Format large numbers with commas"""
    try:
        return f'{int(n):,}'
    except (TypeError, ValueError):
        return 'N/A'


def _format_age(days) -> str:
    """Convert days to human-readable age"""
    try:
        days = int(days)
        years = days // 365
        months = (days % 365) // 30
        if years > 0:
            return f'{years}y {months}m'
        return f'{months}m'
    except (TypeError, ValueError):
        return 'N/A'


def _avg_level(members: dict) -> str:
    """Compute average level from members dict"""
    if not members:
        return 'N/A'
    levels = [m.get('level', 0) for m in members.values() if isinstance(m, dict)]
    if not levels:
        return 'N/A'
    return f'{sum(levels) / len(levels):.1f}'


def _parse_rankedwars(rankedwars: dict, faction_id: int, own_faction_id: int) -> list:
    """
    Extract recent war results for a given faction from rankedwars data.

    Returns list of result strings (W/L/D) for the last 5 wars, newest first.
    """
    if not rankedwars:
        return []

    results = []
    fid_str = str(faction_id)
    own_fid_str = str(own_faction_id)

    # Sort wars by end time descending (most recent first)
    sorted_wars = sorted(
        rankedwars.items(),
        key=lambda x: x[1].get('end', 0),
        reverse=True
    )

    for war_id, war in sorted_wars:
        if len(results) >= 5:
            break

        factions = war.get('factions', {})
        if fid_str not in factions:
            continue

        winner = str(war.get('winner', ''))
        if not winner:
            results.append('D')  # Draw / no winner yet
        elif winner == fid_str:
            results.append('W')
        else:
            results.append('L')

    return results


async def setup_faction_commands(bot):
    """Register /faction slash commands"""

    @bot.tree.command(
        name='faction',
        description='Faction tools: compare stats with another faction'
    )
    @app_commands.describe(
        action='What to do',
        faction_id='Torn faction ID (e.g., 12345) — required for Compare'
    )
    @app_commands.choices(action=[
        app_commands.Choice(name='Compare - Side-by-side faction comparison', value='compare'),
    ])
    async def faction_cmd(
        interaction: discord.Interaction,
        action: str,
        faction_id: int = None
    ):
        if action == 'compare':
            if not is_management(interaction):
                await interaction.response.send_message(
                    embed=create_embed(
                        title='Permission Denied',
                        description='You need Management permissions to compare factions.',
                        color=EMBED_COLOR_CRITICAL
                    ),
                    ephemeral=True
                )
                return

            if not faction_id:
                await interaction.response.send_message(
                    embed=create_embed(
                        title='Missing Faction ID',
                        description='Please provide a `faction_id` to compare against.\n'
                                    'Find it on the faction\'s Torn profile page.',
                        color=EMBED_COLOR_WARNING
                    ),
                    ephemeral=True
                )
                return

            await interaction.response.defer()

            # Fetch both factions in parallel
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

            # --- Parse own faction ---
            own_name = own_basic.get('name', 'Us') if own_basic else 'Us'
            own_tag = own_basic.get('tag', '') if own_basic else ''
            own_members = own_basic.get('members', {}) if own_basic else {}
            own_respect = own_basic.get('respect', 0) if own_basic else 0
            own_best_chain = own_basic.get('best_chain', 0) if own_basic else 0
            own_age = own_basic.get('age', 0) if own_basic else 0
            own_rank = (own_basic.get('rank', {}) or {}) if own_basic else {}

            # --- Parse enemy faction ---
            enemy_name = enemy_basic.get('name', f'Faction {faction_id}')
            enemy_tag = enemy_basic.get('tag', '')
            enemy_members = enemy_basic.get('members', {})
            enemy_respect = enemy_basic.get('respect', 0)
            enemy_best_chain = enemy_basic.get('best_chain', 0)
            enemy_age = enemy_basic.get('age', 0)
            enemy_rank = enemy_basic.get('rank', {}) or {}

            # --- War records ---
            own_rankedwars = own_wars.get('rankedwars', {}) if own_wars else {}
            enemy_rankedwars = enemy_wars.get('rankedwars', {}) if enemy_wars else {}

            own_record = _parse_rankedwars(own_rankedwars, TORN_FACTION_ID, TORN_FACTION_ID)
            enemy_record = _parse_rankedwars(enemy_rankedwars, faction_id, TORN_FACTION_ID)

            # --- Build comparison table ---
            W = 14  # column width

            def row(label, a, b):
                return f'{label:<13} {str(a):<{W}} {str(b)}'

            own_label = f'[{own_tag}] {own_name}'[:W] if own_tag else own_name[:W]
            enemy_label = f'[{enemy_tag}] {enemy_name}'[:W] if enemy_tag else enemy_name[:W]

            header = f'{"":13} {own_label:<{W}} {enemy_label}'
            divider = '─' * len(header)

            # Rank names
            own_rank_name = own_rank.get('name', 'N/A') if isinstance(own_rank, dict) else str(own_rank)
            enemy_rank_name = enemy_rank.get('name', 'N/A') if isinstance(enemy_rank, dict) else str(enemy_rank)

            table = '\n'.join([
                header,
                divider,
                row('Members',    len(own_members),           len(enemy_members)),
                row('Avg Level',  _avg_level(own_members),    _avg_level(enemy_members)),
                row('Best Chain', _format_number(own_best_chain), _format_number(enemy_best_chain)),
                row('Respect',    _format_number(own_respect),    _format_number(enemy_respect)),
                row('Age',        _format_age(own_age),       _format_age(enemy_age)),
                row('Rank',       own_rank_name,              enemy_rank_name),
            ])

            # War record section
            def record_line(results):
                if not results:
                    return 'No ranked war data'
                wins = results.count('W')
                losses = results.count('L')
                draws = results.count('D')
                record_str = ' '.join(results)
                return f'{record_str}  ({wins}W {losses}L {draws}D)'

            war_section = (
                f'**{own_name}:** {record_line(own_record)}\n'
                f'**{enemy_name}:** {record_line(enemy_record)}'
            )

            # Torn profile link for enemy
            torn_link = f'https://www.torn.com/factions.php?step=profile&ID={faction_id}'

            embed = create_embed(
                title=f'Faction Comparison',
                description=f'```\n{table}\n```\n'
                            f'**Recent Wars (last 5):**\n{war_section}\n\n'
                            f'[View {enemy_name} on Torn]({torn_link})',
                color=EMBED_COLOR_INFO
            )

            await interaction.followup.send(embed=embed)

    print('Faction commands registered (/faction)')
