"""
War Tracking Module
Tracks faction wars and provides detailed war statistics

Features:
- /war command - Show current war status
- /warboard command - Show war participation leaderboard
- Automatic war end reports posted to #war channel
- Detailed management reports posted to private war management channel
- Tracks incoming and outgoing attacks per member
"""

import discord
from datetime import datetime
from utils.helpers import load_json, save_json, create_embed, format_timestamp, get_current_timestamp
from utils.torn_api import fetch_faction_data
from config import (
    WAR_CHANNEL_ID,
    WAR_MANAGEMENT_CHANNEL_ID,
    EMBED_COLOR_INFO,
    EMBED_COLOR_SUCCESS,
    EMBED_COLOR_WARNING,
    EMBED_COLOR_CRITICAL,
    MANAGEMENT_PING_ROLE_ID,
    TORN_FACTION_ID
)


class WarTracker:
    """Tracks faction wars and provides statistics"""

    def __init__(self, bot):
        self.bot = bot
        self.was_war_active = False
        self.current_war_id = None
        self.war_data_file = 'data/war_history.json'
        self.war_history = {}
        self.subscription_manager = None  # Set by bot.py
        self.enemy_watchlist = None  # Set by bot.py for auto-clear
        # Track attacks during active war
        self.current_war_attacks = {
            'outgoing': {},  # {member_id: hit_count}
            'incoming': {},  # {member_id: times_hit}
            'scores': {}     # {member_id: score}
        }

    async def load_war_history(self):
        """Load war history from JSON file"""
        self.war_history = load_json(self.war_data_file, default={})
        print(f'Loaded war history with {len(self.war_history)} records')

    async def save_war_history(self):
        """Save war history to JSON file"""
        save_json(self.war_data_file, self.war_history)

    async def get_current_war(self):
        """
        Get current war status from Torn API

        Returns:
            Dict with war data or None if error/no war
        """
        data = await fetch_faction_data('rankedwars')

        if not data:
            return None

        rankedwars = data.get('rankedwars', {})

        # Check if there's an active war
        if not rankedwars:
            return None

        # Get the first (current) war
        for war_id, war_data in rankedwars.items():
            # Check if war is ongoing
            if war_data.get('war', {}).get('end', 0) > get_current_timestamp():
                return {
                    'war_id': war_id,
                    'data': war_data
                }

        return None

    async def format_war_embed(self, war_info):
        """
        Create a Discord embed for current war status

        Args:
            war_info: War data from Torn API

        Returns:
            discord.Embed
        """
        if not war_info:
            return create_embed(
                title='No Active War',
                description='There is no active ranked war at the moment.',
                color=EMBED_COLOR_INFO
            )

        war_data = war_info['data']
        war = war_data.get('war', {})
        factions = war_data.get('factions', {})

        # Get war details
        start_time = war.get('start', 0)
        end_time = war.get('end', 0)
        target = war.get('target', 0)  # Goal points to win

        # Get our faction and enemy faction by matching TORN_FACTION_ID
        our_faction = None
        enemy_faction = None

        for faction_id, faction_data in factions.items():
            if str(faction_id) == str(TORN_FACTION_ID):
                our_faction = faction_data
            else:
                enemy_faction = faction_data

        # Fallback: just use first two if ID match failed
        if not our_faction or not enemy_faction:
            faction_list = list(factions.values())
            our_faction = faction_list[0] if len(faction_list) > 0 else {}
            enemy_faction = faction_list[1] if len(faction_list) > 1 else {}

        our_score = our_faction.get('score', 0)
        enemy_score = enemy_faction.get('score', 0)
        enemy_name = enemy_faction.get('name', 'Unknown')

        # Determine color and status (NO TIED - only winning or losing)
        if our_score > enemy_score:
            color = EMBED_COLOR_SUCCESS
            status = f'✅ Winning {our_score} - {enemy_score}'
        else:  # Losing (includes equal scores - if equal, we're not winning)
            color = EMBED_COLOR_WARNING
            status = f'⚠️ Losing {our_score} - {enemy_score}'

        # Build embed
        embed = create_embed(
            title=f'Ranked War vs {enemy_name}',
            description=status,
            color=color,
            fields=[
                {'name': 'Our Score', 'value': str(our_score), 'inline': True},
                {'name': 'Enemy Score', 'value': str(enemy_score), 'inline': True},
                {'name': 'Target Score', 'value': str(target) if target > 0 else 'N/A', 'inline': True},
                {'name': 'War ID', 'value': str(war_info['war_id']), 'inline': True},
                {'name': 'Started', 'value': format_timestamp(start_time, 'R'), 'inline': True},
                {'name': 'Ends', 'value': format_timestamp(end_time, 'R'), 'inline': True}
            ]
        )

        return embed

    async def format_warboard_embed(self, war_info):
        """
        Create a Discord embed for war participation leaderboard

        Args:
            war_info: War data from Torn API

        Returns:
            discord.Embed
        """
        if not war_info:
            return create_embed(
                title='No Active War',
                description='There is no active ranked war to show leaderboard for.',
                color=EMBED_COLOR_INFO
            )

        war_data = war_info['data']
        factions = war_data.get('factions', {})

        # Find our faction's members
        our_members = None
        for faction_id, faction_data in factions.items():
            members = faction_data.get('members', {})
            if members:
                our_members = members
                break

        if not our_members:
            return create_embed(
                title='No War Data',
                description='No member participation data available for this war.',
                color=EMBED_COLOR_WARNING
            )

        # Fetch faction members to get names
        from utils.torn_api import fetch_faction_members
        faction_members = await fetch_faction_members()

        # Build member list with stats
        member_list = []
        for user_id, stats in our_members.items():
            # Get member name
            member_name = 'Unknown'
            if faction_members and user_id in faction_members:
                member_name = faction_members[user_id].get('name', f'ID:{user_id}')
            else:
                member_name = f'ID:{user_id}'

            # Get war stats (NO ASSISTS - only hits and score)
            hits = stats.get('attacks', 0)  # Outgoing attacks
            score = stats.get('score', 0)

            member_list.append({
                'name': member_name,
                'hits': hits,
                'score': score
            })

        # Sort by score (highest first)
        sorted_members = sorted(
            member_list,
            key=lambda x: x['score'],
            reverse=True
        )

        # Calculate totals
        total_hits = sum(m['hits'] for m in member_list)
        total_score = sum(m['score'] for m in member_list)

        # Build embed
        embed = create_embed(
            title=f'War Participation Leaderboard',
            description=f'War ID: {war_info["war_id"]}',
            color=EMBED_COLOR_INFO,
            fields=[
                {'name': 'Total Attacks', 'value': str(total_hits), 'inline': True},
                {'name': 'Total Score', 'value': str(total_score), 'inline': True},
                {'name': 'Participants', 'value': str(len(member_list)), 'inline': True}
            ]
        )

        # Add top contributors (max 10)
        if sorted_members:
            top_contributors = '\n'.join([
                f'{i+1}. **{m["name"]}**: {m["score"]} pts ({m["hits"]} attacks)'
                for i, m in enumerate(sorted_members[:10])
            ])
            embed.add_field(
                name='Top Contributors',
                value=top_contributors,
                inline=False
            )

        return embed

    async def format_war_management_report(self, war_info, final_data=None):
        """
        Create a detailed management report with incoming/outgoing attacks
        Posted to private war management channel for leaders/management

        Args:
            war_info: War data from Torn API
            final_data: Final war data snapshot (if war ended)

        Returns:
            discord.Embed
        """
        if not war_info and not final_data:
            return None

        # Use final_data if available, otherwise use war_info
        data_to_use = final_data if final_data else war_info['data']
        if final_data:
            war_id = final_data.get('war_id', 'Unknown')
        else:
            war_id = war_info['war_id']

        factions = data_to_use.get('factions', {})

        # Find our faction's members
        our_members = None
        enemy_members = None
        our_faction_data = None
        enemy_faction_data = None

        for faction_id, faction_data in factions.items():
            if str(faction_id) == str(TORN_FACTION_ID):
                our_members = faction_data.get('members', {})
                our_faction_data = faction_data
            else:
                enemy_members = faction_data.get('members', {})
                enemy_faction_data = faction_data

        if not our_members:
            return None

        # Fetch faction members to get names
        from utils.torn_api import fetch_faction_members
        faction_members = await fetch_faction_members()

        # Build detailed member stats
        member_stats = []
        for user_id, stats in our_members.items():
            # Get member name
            member_name = 'Unknown'
            if faction_members and user_id in faction_members:
                member_name = faction_members[user_id].get('name', f'ID:{user_id}')
            else:
                member_name = f'ID:{user_id}'

            # Get outgoing attacks (attacks made)
            outgoing = stats.get('attacks', 0)

            # Get incoming attacks (times they got hit)
            # This is in the 'defends' field
            incoming = stats.get('defends', 0)

            score = stats.get('score', 0)

            member_stats.append({
                'name': member_name,
                'user_id': user_id,
                'outgoing': outgoing,
                'incoming': incoming,
                'score': score
            })

        # Sort by score (highest first)
        sorted_stats = sorted(member_stats, key=lambda x: x['score'], reverse=True)

        # Get war result
        our_score = our_faction_data.get('score', 0) if our_faction_data else 0
        enemy_score = enemy_faction_data.get('score', 0) if enemy_faction_data else 0
        enemy_name = enemy_faction_data.get('name', 'Unknown') if enemy_faction_data else 'Unknown'

        result = '🏆 VICTORY' if our_score > enemy_score else '❌ DEFEAT'
        color = EMBED_COLOR_SUCCESS if our_score > enemy_score else EMBED_COLOR_CRITICAL

        # Build management embed
        embed = create_embed(
            title=f'⚔️ War Management Report - {result}',
            description=f'**War #{war_id}** vs {enemy_name}\n**Final Score:** {our_score} - {enemy_score}',
            color=color
        )

        # Add summary stats
        total_outgoing = sum(m['outgoing'] for m in member_stats)
        total_incoming = sum(m['incoming'] for m in member_stats)
        total_score = sum(m['score'] for m in member_stats)

        embed.add_field(
            name='📊 Summary',
            value=f'**Total Attacks Made:** {total_outgoing}\n**Total Times Hit:** {total_incoming}\n**Total Score:** {total_score}',
            inline=False
        )

        # Add detailed member breakdown (show all members)
        # Split into chunks if needed (Discord limit is 1024 chars per field)
        member_details = []
        for i, m in enumerate(sorted_stats):
            member_details.append(
                f'{i+1}. **{m["name"]}**\n'
                f'   └ Attacks: {m["outgoing"]} | Defended: {m["incoming"]}x | Score: {m["score"]}'
            )

        # Join all member details
        full_details = '\n'.join(member_details)

        # Split into multiple fields if too long
        if len(full_details) > 1024:
            # Split into chunks
            chunks = []
            current_chunk = []
            current_length = 0

            for detail in member_details:
                if current_length + len(detail) + 1 > 1024:
                    chunks.append('\n'.join(current_chunk))
                    current_chunk = [detail]
                    current_length = len(detail)
                else:
                    current_chunk.append(detail)
                    current_length += len(detail) + 1

            if current_chunk:
                chunks.append('\n'.join(current_chunk))

            # Add chunks as separate fields
            for idx, chunk in enumerate(chunks):
                embed.add_field(
                    name=f'👥 Member Performance (Part {idx + 1})',
                    value=chunk,
                    inline=False
                )
        else:
            embed.add_field(
                name='👥 Member Performance',
                value=full_details,
                inline=False
            )

        # Add members who got hit the most (potential weak links)
        most_hit = sorted(member_stats, key=lambda x: x['incoming'], reverse=True)[:5]
        if most_hit:
            most_hit_text = '\n'.join([
                f'{i+1}. **{m["name"]}**: Hit {m["incoming"]}x'
                for i, m in enumerate(most_hit) if m["incoming"] > 0
            ])
            if most_hit_text:
                embed.add_field(
                    name='⚠️ Most Targeted (Review Defense)',
                    value=most_hit_text,
                    inline=False
                )

        # Add members who didn't participate
        non_participants = [m for m in member_stats if m['outgoing'] == 0]
        if non_participants:
            non_participant_text = '\n'.join([
                f'• {m["name"]}'
                for m in non_participants
            ])
            embed.add_field(
                name='❌ No Participation',
                value=non_participant_text,
                inline=False
            )

        return embed

    async def check_war_update(self):
        """
        Check if war status changed and post update if needed
        Auto-posts war results when war ends
        Called by the background task every minute
        """
        # Get current war
        war_info = await self.get_current_war()
        channel = self.bot.get_channel(WAR_CHANNEL_ID)
        management_channel = self.bot.get_channel(WAR_MANAGEMENT_CHANNEL_ID) if WAR_MANAGEMENT_CHANNEL_ID != 0 else None

        if not channel:
            print(f'Warning: Could not find #war channel (ID: {WAR_CHANNEL_ID})')
            return

        # Detect war END (was active, now not)
        if self.was_war_active and not war_info:
            print('War ended! Posting war results...')

            # Get final war stats from history
            if self.current_war_id and self.current_war_id in self.war_history:
                final_war_data = self.war_history[self.current_war_id]

                # Create final war report embed for public channel
                public_embed = create_embed(
                    title=f'🏁 War Complete!',
                    description=f'War #{self.current_war_id} has ended\n\nUse `/warboard` to see final standings',
                    color=EMBED_COLOR_SUCCESS
                )

                # Post public war result with pings (important event)
                pings = []
                if MANAGEMENT_PING_ROLE_ID:
                    pings.append(f'<@&{MANAGEMENT_PING_ROLE_ID}>')
                if self.subscription_manager:
                    sub_pings = self.subscription_manager.get_subscriber_mentions('war')
                    if sub_pings:
                        pings.append(sub_pings)
                ping_text = ' '.join(pings) + ' ' if pings else ''

                await channel.send(f'{ping_text}🏁 **War Complete!**', embed=public_embed)
                print('Posted war completion report to public channel')

                # Auto-clear enemy watchlist
                if self.enemy_watchlist:
                    cleared = self.enemy_watchlist.clear_all()
                    await self.enemy_watchlist.save_watchlist()
                    if cleared > 0:
                        print(f'Auto-cleared {cleared} enemies from watchlist')

                # Post detailed management report
                if management_channel:
                    management_embed = await self.format_war_management_report(
                        None,
                        final_data={
                            'war_id': self.current_war_id,
                            'factions': final_war_data.get('factions', {})
                        }
                    )
                    if management_embed:
                        await management_channel.send('📊 **War Management Report**', embed=management_embed)
                        print('Posted detailed war report to management channel')
                else:
                    print('Warning: War management channel not configured')

            # Reset state
            self.was_war_active = False
            self.current_war_id = None
            self.current_war_attacks = {'outgoing': {}, 'incoming': {}, 'scores': {}}
            return

        # No active war
        if not war_info:
            return

        # War is active
        war_id = war_info['war_id']

        # Mark war as active if this is first detection
        if not self.was_war_active:
            self.was_war_active = True
            self.current_war_id = war_id
            print(f'War detected: {war_id}')

        # Save war data to history (with full faction data for final report)
        self.war_history[war_id] = {
            'timestamp': get_current_timestamp(),
            'factions': war_info['data'].get('factions', {})
        }
        await self.save_war_history()
