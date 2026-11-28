"""
The "how to ask @ApexPunk" guide, posted by the bot in #commands-the-punk.

Why a post instead of a long help reply (Pat, 22/11/2026): a wall of help
text in the middle of a chat channel buries the conversation, and the next
person asks again anyway. Now "@ApexPunk help" replies with one short line
linking to this message.

Same idea as features/rules_posts.py: the bot posts it ONCE, keeps the
message id in data/ask_help_post.json, and on later starts edits that same
message in place when the text changed (silent, keeps its place and its
link). Deleted by hand: it is posted again. Channel missing or the post
failing: the help reply falls back to the short help text.

The channel is found by NAME ("commands-the-punk", with or without an
emoji prefix), as Pat asked, with MEMBER_COMMANDS_CHANNEL_ID as the backup.
"""

import logging
import re

import discord

from utils.helpers import load_json, save_json

logger = logging.getLogger(__name__)

DATA_FILE = 'data/ask_help_post.json'
CHANNEL_NAME = 'commands-the-punk'


def guide_text(members_on, cooldown_s=120):
    """The guide, short and scannable: bold emoji headings, one example each.
    While members can't ask yet, it says so (the switch flips with a restart,
    which edits this post, so the line goes away by itself)."""
    mins = max(1, round(cooldown_s / 60))
    lines = [
        '🤖 **How to ask @ApexPunk**',
        '@mention the bot with a question in plain English. Reply to its answer to keep chatting.',
        '**🏠 Where** ask in #🤖-ask-apexpunk (anything goes there). Jokes and chat get answered '
        'wherever you ask; anything else gets answered in #🤖-ask-apexpunk.',
        '',
        '**📊 Your records** `@ApexPunk how many hits did I make last war?`',
        '-# Your war hits, hits taken, activity, OC, strikes and leave notices. Only your own.',
        '**⚔️ War & chain live** `@ApexPunk what\'s the war score?`',
        '**🕵️ Who is in an OC** `@ApexPunk who is in an OC right now?`',
        '**📜 Faction rules** `@ApexPunk what\'s the OC rule?`',
        '**🎮 How Torn works** `@ApexPunk how long can a ranked war last?`',
        '**🎯 Enemies** `@ApexPunk who hit us this week?`',
        '**🏆 War top 10** `@ApexPunk top performers last 3 wars` (with your own rank)',
        '**🆕 New here** `@ApexPunk I\'m new` · **🏴 The faction** `@ApexPunk tell me about the faction`',
        '**😈 Fun & chat** `@ApexPunk tell me a joke` · `roast me` · `8ball will we win?`',
        f'**⏱️ Cooldown** one question every {mins} minutes each (management: no limit)',
        '',
        '🔒 **Management only**',
        '**👥 Anyone\'s records** `@ApexPunk how active is BunnyBull?`',
        '**📋 Full war tables** `@ApexPunk who took the most hits last war?`',
        '**🛠️ Actions (✅ card)** `@ApexPunk start a chain event now, 24 hours, min 10`',
        '**📣 Announcements** `@ApexPunk make an announcement about Friday\'s war`',
        '',
        '-# Every number is checked against the records, and the bot asks when unsure. '
        'No money, items, payouts, Xanax or battle stats. Strikes stay on /strikes.',
    ]
    if not members_on:
        lines.append('-# Members: asking opens soon. For now only management gets answers.')
    return '\n'.join(lines)


def _bare(name):
    """'🤖-commands-the-punk' / '🤖commands-the-punk' -> 'commands-the-punk'."""
    return re.sub(r'^[^a-z0-9]+', '', (name or '').lower())


def find_channel(guild, fallback_id=0):
    """The text channel named like commands-the-punk, else the configured one."""
    if guild is None:
        return None
    for ch in getattr(guild, 'text_channels', []) or []:
        if _bare(ch.name) == CHANNEL_NAME or CHANNEL_NAME in ch.name.lower():
            return ch
    if fallback_id:
        return guild.get_channel(fallback_id)
    return None


def guide_message_id(path=DATA_FILE):
    """The posted guide's message id, or None. The member command board
    (features/member_commands.py) shares the channel and must not mistake it
    for one of its own old boards."""
    try:
        return int((load_json(path, default={}) or {}).get('message_id') or 0) or None
    except (TypeError, ValueError):
        return None


class HelpPost:
    def __init__(self, path=DATA_FILE):
        self.path = path
        self.data = load_json(path, default={}) or {}

    @property
    def jump_url(self):
        return self.data.get('jump_url')

    async def publish(self, guild, text, fallback_id=0):
        """Make the channel show `text`. Returns 'posted', 'edited',
        'unchanged', or None when it could not (then jump_url is cleared, so
        help falls back to the short reply instead of a dead link)."""
        channel = find_channel(guild, fallback_id)
        # "#🤖-ask-apexpunk" / "#punk-plaza" in the text become real links.
        try:
            from features.rules_posts import names_to_links, guild_channel_names
            text, _ = names_to_links(text, guild_channel_names(guild))
        except Exception as e:
            logger.warning(f'Help post: channel links not made: {e!r}')
        if channel is None:
            logger.warning('Help post: no channel named like #commands-the-punk')
            self.data.pop('jump_url', None)
            return None
        mid = self.data.get('message_id') if self.data.get('channel_id') == channel.id else None
        if mid:
            try:
                msg = await channel.fetch_message(int(mid))
                if msg.content == text:
                    return 'unchanged'
                await msg.edit(content=text, allowed_mentions=discord.AllowedMentions.none())
                self.data['jump_url'] = msg.jump_url
                save_json(self.path, self.data)
                return 'edited'
            except discord.NotFound:
                pass   # deleted by hand: post a fresh one below
            except discord.HTTPException as e:
                logger.warning(f'Help post: edit failed ({e}); reposting')
        try:
            msg = await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            logger.error(f'Help post: could not post ({e})')
            self.data.pop('jump_url', None)
            return None
        self.data.update({'channel_id': channel.id, 'message_id': msg.id, 'jump_url': msg.jump_url})
        save_json(self.path, self.data)
        return 'posted'
