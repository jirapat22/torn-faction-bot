"""
Subscription System
Allows members to opt-in to specific notifications

Features:
- /subscribe <type> - Subscribe to notifications (chain, enemy, war)
- /unsubscribe <type> - Unsubscribe from notifications
- /mysubs - View current subscriptions
"""

import discord
from discord import app_commands
from utils.helpers import load_json, save_json, create_embed
from config import EMBED_COLOR_SUCCESS, EMBED_COLOR_INFO, EMBED_COLOR_WARNING


class SubscriptionManager:
    """Manages member subscriptions for notifications"""

    SUBSCRIPTION_TYPES = {
        'chain': 'Chain updates (start/end notifications)',
        'enemy': 'Enemy watchlist (when enemies come online)',
        'war': 'War updates (start/end notifications)'
    }

    def __init__(self, bot):
        self.bot = bot
        self.subscriptions_file = 'data/subscriptions.json'
        self.subscriptions = {}  # {type: [discord_id, discord_id, ...]}

    async def load_subscriptions(self):
        """Load subscriptions from JSON file"""
        self.subscriptions = load_json(self.subscriptions_file, default={
            'chain': [],
            'enemy': [],
            'war': []
        })
        # Ensure all types exist
        for sub_type in self.SUBSCRIPTION_TYPES:
            if sub_type not in self.subscriptions:
                self.subscriptions[sub_type] = []

        total = sum(len(subs) for subs in self.subscriptions.values())
        print(f'Loaded {total} total subscriptions')

    async def save_subscriptions(self):
        """Save subscriptions to JSON file"""
        save_json(self.subscriptions_file, self.subscriptions)

    def subscribe(self, discord_id: int, sub_type: str) -> bool:
        """
        Subscribe a user to a notification type

        Returns:
            True if newly subscribed, False if already subscribed
        """
        discord_id_str = str(discord_id)
        if sub_type not in self.subscriptions:
            self.subscriptions[sub_type] = []

        if discord_id_str in self.subscriptions[sub_type]:
            return False  # Already subscribed

        self.subscriptions[sub_type].append(discord_id_str)
        return True

    def unsubscribe(self, discord_id: int, sub_type: str) -> bool:
        """
        Unsubscribe a user from a notification type

        Returns:
            True if unsubscribed, False if wasn't subscribed
        """
        discord_id_str = str(discord_id)
        if sub_type not in self.subscriptions:
            return False

        if discord_id_str not in self.subscriptions[sub_type]:
            return False  # Wasn't subscribed

        self.subscriptions[sub_type].remove(discord_id_str)
        return True

    def get_user_subscriptions(self, discord_id: int) -> list:
        """Get list of subscription types for a user"""
        discord_id_str = str(discord_id)
        return [
            sub_type for sub_type, subscribers in self.subscriptions.items()
            if discord_id_str in subscribers
        ]

    def get_subscribers(self, sub_type: str) -> list:
        """Get list of Discord IDs subscribed to a type"""
        return self.subscriptions.get(sub_type, [])

    def get_subscriber_mentions(self, sub_type: str) -> str:
        """Get mention string for all subscribers of a type"""
        subscribers = self.get_subscribers(sub_type)
        if not subscribers:
            return ''
        return ' '.join([f'<@{discord_id}>' for discord_id in subscribers])

    def get_subscriber_count(self, sub_type: str) -> int:
        """Get count of subscribers for a type"""
        return len(self.subscriptions.get(sub_type, []))


async def setup_subscription_commands(bot, subscription_manager: SubscriptionManager):
    """Set up subscription slash commands"""

    @bot.tree.command(
        name='subscribe',
        description='Subscribe to notifications (chain, enemy, or war updates)'
    )
    @app_commands.describe(
        notification_type='Type of notification to subscribe to'
    )
    @app_commands.choices(notification_type=[
        app_commands.Choice(name='Chain - Get pinged for chain start/end', value='chain'),
        app_commands.Choice(name='Enemy - Get pinged when enemies come online', value='enemy'),
        app_commands.Choice(name='War - Get pinged for war start/end', value='war')
    ])
    async def subscribe_command(interaction: discord.Interaction, notification_type: str):
        """Subscribe to a notification type"""
        success = subscription_manager.subscribe(interaction.user.id, notification_type)
        await subscription_manager.save_subscriptions()

        if success:
            embed = create_embed(
                title='Subscribed',
                description=f'You are now subscribed to **{notification_type}** notifications.\n\n'
                           f'You will be pinged for important {notification_type} updates.',
                color=EMBED_COLOR_SUCCESS
            )
        else:
            embed = create_embed(
                title='Already Subscribed',
                description=f'You are already subscribed to **{notification_type}** notifications.',
                color=EMBED_COLOR_INFO
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(
        name='unsubscribe',
        description='Unsubscribe from notifications'
    )
    @app_commands.describe(
        notification_type='Type of notification to unsubscribe from'
    )
    @app_commands.choices(notification_type=[
        app_commands.Choice(name='Chain - Stop chain notifications', value='chain'),
        app_commands.Choice(name='Enemy - Stop enemy notifications', value='enemy'),
        app_commands.Choice(name='War - Stop war notifications', value='war')
    ])
    async def unsubscribe_command(interaction: discord.Interaction, notification_type: str):
        """Unsubscribe from a notification type"""
        success = subscription_manager.unsubscribe(interaction.user.id, notification_type)
        await subscription_manager.save_subscriptions()

        if success:
            embed = create_embed(
                title='Unsubscribed',
                description=f'You have unsubscribed from **{notification_type}** notifications.',
                color=EMBED_COLOR_SUCCESS
            )
        else:
            embed = create_embed(
                title='Not Subscribed',
                description=f'You were not subscribed to **{notification_type}** notifications.',
                color=EMBED_COLOR_WARNING
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(
        name='mysubs',
        description='View your current notification subscriptions'
    )
    async def mysubs_command(interaction: discord.Interaction):
        """View current subscriptions"""
        user_subs = subscription_manager.get_user_subscriptions(interaction.user.id)

        if user_subs:
            subs_text = '\n'.join([
                f'**{sub_type}** - {subscription_manager.SUBSCRIPTION_TYPES[sub_type]}'
                for sub_type in user_subs
            ])
            embed = create_embed(
                title='Your Subscriptions',
                description=f'You are subscribed to:\n\n{subs_text}',
                color=EMBED_COLOR_INFO
            )
        else:
            embed = create_embed(
                title='No Subscriptions',
                description='You are not subscribed to any notifications.\n\n'
                           'Use `/subscribe` to opt-in to notifications.',
                color=EMBED_COLOR_INFO
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    print('Subscription commands registered (/subscribe, /unsubscribe, /mysubs)')
