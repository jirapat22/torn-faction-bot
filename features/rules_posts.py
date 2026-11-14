"""
The bot owns the rulebook posts in #faction-edicts.

Why: Discord only lets an author edit their own messages. With the rules
posted by Pat, every rule change meant Pat hand-pasting 2,000-character
posts (and some never got pasted). With the bot as the author:
  - `/rules action:Edit post:3` opens a box with the post; save it and the
    bot edits its message in place (management only);
  - channels can be typed as plain "#war-room" and become real, clickable
    links (<#id>) when saved;
  - the @ApexPunk rule quotes always match, because rules_sync reads these
    same posts.

Source of truth: data/rules_posts.json (backed up nightly with the rest of
data/). Discord shows it; this file decides it.

One-time takeover (Pat's call, 07/11/2026): when the file says
`takeover_pending`, the bot saves Pat's old posts (text, plus who reacted ✅)
to data/rules_old_posts.json, deletes them, posts its own, adds a ✅ to the
last one, and lists the earlier ✅ reactors in the management channel so
nobody has to be chased to re-react.
"""

import logging
import re
import time

import discord
from discord import app_commands

from config import MANAGEMENT_CHANNEL_ID, RULES_CHANNEL_ID
from utils.helpers import is_management, load_json, save_json

logger = logging.getLogger(__name__)

DATA_FILE = 'data/rules_posts.json'
OLD_POSTS_FILE = 'data/rules_old_posts.json'
POST_LIMIT = 2000      # Discord's message limit
CONFIRM_EMOJI = '✅'
# Blank lines and a dashed line under every post, copied from Pat's original
# posts, so each post stands apart when scrolling. Added at publish time, so
# nobody has to keep it in the text they edit.
SEPARATOR = '\n\n\n\n\n\n' + '-' * 99


# ─────────────────────────────────────────────────────────────────────────────
# Channel links: '<#123>' (what Discord stores) <-> '#war-room' (what people type)
# ─────────────────────────────────────────────────────────────────────────────

def links_to_names(text, names):
    """'<#123>' -> '#war-room' for editing. Names with spaces (forum posts)
    become '#[OC Guide]' so they survive the trip back."""
    def sub(m):
        name = names.get(int(m.group(1)))
        if not name:
            return m.group(0)                      # unknown channel: keep the raw link
        return f'#[{name}]' if ' ' in name else f'#{name}'
    return re.sub(r'<#(\d+)>', sub, text)


def _bare(name):
    """'📜-faction-edicts' / '📰war-reports' -> the name without its emoji, which
    is how people type it."""
    return re.sub(r'^[^\w#\[]+-?', '', name).lower()


def names_to_links(text, channels):
    """
    '#war-room', '#⚔️-war-room' or '#[OC Guide]' -> '<#id>'. Matches the full
    name first, then the name without its emoji prefix. Returns (text,
    unknown names) so the editor can be told what did NOT become a link.
    `channels` is {id: name}.
    """
    by_full = {n.lower(): cid for cid, n in channels.items()}
    by_bare = {}
    for cid, n in channels.items():
        by_bare.setdefault(_bare(n), cid)
    unknown = []

    def find(name):
        key = name.lower()
        return by_full.get(key) or by_bare.get(_bare(key)) or by_bare.get(key)

    def bracketed(m):
        cid = find(m.group(1).strip())
        if cid:
            return f'<#{cid}>'
        unknown.append(m.group(1).strip())
        return m.group(0)

    text = re.sub(r'#\[([^\]\n]+)\]', bracketed, text)

    def plain(m):
        word = m.group(1)
        # Trailing punctuation belongs to the sentence, not the name.
        core = word.rstrip('.,;:!?)\'"')
        cid = find(core)
        if cid:
            return f'<#{cid}>' + word[len(core):]
        if re.search(r'[a-z]', core, re.I) and '-' in core:
            unknown.append(core)                   # looks like a channel name but isn't one
        return m.group(0)

    # '#' at a word start, not inside a URL ('preferences.php#tab=api') or a heading ('# Title').
    text = re.sub(r'(?<![\w/#<&])#(?!\s)(?!#)([^\s<>\[]+)', plain, text)
    return text, sorted(set(unknown))


def guild_channel_names(guild):
    names = {c.id: c.name for c in guild.channels}
    names.update({t.id: t.name for t in guild.threads})
    return names


# ─────────────────────────────────────────────────────────────────────────────
# The store and publishing
# ─────────────────────────────────────────────────────────────────────────────

class RulesPosts:
    def __init__(self, bot, path=DATA_FILE):
        self.bot = bot
        self.path = path
        self.data = load_json(path, default={}) or {}
        self.data.setdefault('posts', [])
        self.data.setdefault('message_ids', [])

    def save(self):
        save_json(self.path, self.data)

    @property
    def posts(self):
        return self.data['posts']

    def rendered(self):
        """The posts exactly as they appear in Discord: text plus the separator."""
        return [p.rstrip() + SEPARATOR for p in self.posts]

    async def _channel(self):
        channel = self.bot.get_channel(RULES_CHANNEL_ID)
        if channel is None and RULES_CHANNEL_ID:
            channel = await self.bot.fetch_channel(RULES_CHANNEL_ID)
        return channel

    async def publish(self, force=False):
        """
        Make the channel match the posts. Edits the bot's messages in place
        when they all still exist (keeps their ✅ reactions); if any is gone
        or the number of posts changed (or force=True), reposts all of them
        so the order stays right. Returns a short summary.
        """
        channel = await self._channel()
        ids = self.data['message_ids']
        msgs = []
        if force:
            msgs = None
        elif len(ids) == len(self.posts):
            for mid in ids:
                try:
                    msgs.append(await channel.fetch_message(mid))
                except discord.NotFound:
                    msgs = None
                    break
        else:
            msgs = None

        if msgs is not None:
            edited = 0
            for msg, content in zip(msgs, self.rendered()):
                if msg.content != content:
                    await msg.edit(content=content, allowed_mentions=discord.AllowedMentions.none())
                    edited += 1
            await self._ensure_confirm(msgs[-1] if msgs else None)
            return f'{edited} post(s) updated in place.'

        # Repost everything: delete whatever of ours is left, then post in order.
        for mid in ids:
            try:
                await (await channel.fetch_message(mid)).delete()
            except discord.NotFound:
                pass
        new_ids, last = [], None
        for content in self.rendered():
            last = await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            new_ids.append(last.id)
        self.data['message_ids'] = new_ids
        self.save()
        await self._ensure_confirm(last)
        return f'{len(new_ids)} post(s) posted fresh.'

    async def _ensure_confirm(self, msg):
        """The ✅ members click to confirm they read the rules, on the last post."""
        if msg is None:
            return
        if not any(str(r.emoji) == CONFIRM_EMOJI and r.me for r in msg.reactions):
            try:
                await msg.add_reaction(CONFIRM_EMOJI)
            except discord.HTTPException:
                pass

    async def takeover(self):
        """
        One-time: replace the human-written rules posts with the bot's own.
        Old text and ✅ reactors are saved BEFORE anything is deleted.
        """
        channel = await self._channel()
        old = [m async for m in channel.history(limit=50, oldest_first=True)
               if not m.author.bot]
        saved = []
        reactors = []
        for m in old:
            entry = {'id': m.id, 'author': str(m.author), 'content': m.content,
                     'created_at': m.created_at.isoformat(), 'reactions': {}}
            for r in m.reactions:
                users = [u async for u in r.users() if not u.bot]
                entry['reactions'][str(r.emoji)] = [{'id': u.id, 'name': u.display_name} for u in users]
                if str(r.emoji) == CONFIRM_EMOJI:
                    reactors += [u.display_name for u in users]
            saved.append(entry)
        save_json(OLD_POSTS_FILE, {'saved_at': time.time(), 'posts': saved})
        logger.info(f'Rules takeover: saved {len(saved)} old post(s) to {OLD_POSTS_FILE}')

        for m in old:
            await m.delete()
        self.data['message_ids'] = []
        summary = await self.publish()
        self.data['takeover_pending'] = False
        self.data['taken_over_at'] = time.time()
        self.save()

        mgmt = self.bot.get_channel(MANAGEMENT_CHANNEL_ID)
        if mgmt is not None:
            who = ', '.join(sorted(set(reactors))) or 'nobody'
            await mgmt.send(
                f'📜 I now post the rules in <#{RULES_CHANNEL_ID}> myself, so they can be edited with '
                f'`/rules`. The old posts are saved. These {len(set(reactors))} had already reacted '
                f'{CONFIRM_EMOJI} to the old rules: {who}. Everyone else can react on the new last post.',
                allowed_mentions=discord.AllowedMentions.none())
        logger.info(f'Rules takeover done: {summary}')
        return summary


# ─────────────────────────────────────────────────────────────────────────────
# /rules (management)
# ─────────────────────────────────────────────────────────────────────────────

class EditPostModal(discord.ui.Modal):
    def __init__(self, store, index, names):
        super().__init__(title=f'Edit rules post {index + 1}')
        self.store, self.index, self.names = store, index, names
        self.body = discord.ui.TextInput(
            label='Post text (type channels as #war-room)', style=discord.TextStyle.paragraph,
            default=links_to_names(store.posts[index], names), max_length=4000)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        text, unknown = names_to_links(self.body.value.strip(), self.names)
        if len(text) + len(SEPARATOR) > POST_LIMIT:
            await interaction.response.send_message(
                f'That post is {len(text) + len(SEPARATOR)} characters with the links and the divider line '
                f'in, over Discord\'s {POST_LIMIT}. '
                'Trim it and try again. (Nothing was changed.)', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        self.store.posts[self.index] = text
        self.store.data['updated_by'] = str(interaction.user)
        self.store.data['updated_at'] = time.time()
        self.store.save()
        summary = await self.store.publish()
        note = (f'\n⚠️ Not turned into links (no channel by that name): {", ".join("#" + u for u in unknown)}'
                if unknown else '')
        await interaction.followup.send(f'Post {self.index + 1} saved. {summary}{note}', ephemeral=True)


async def setup_rules_commands(bot, store):
    @app_commands.default_permissions(kick_members=True)   # management only: hidden from members' / menu
    @bot.tree.command(name='rules', description='The rulebook in #faction-edicts (management)')
    @app_commands.describe(action='What to do', post='[Edit] Which post, 1 to 9')
    @app_commands.choices(action=[
        app_commands.Choice(name='List - Show the posts and their numbers', value='list'),
        app_commands.Choice(name='Edit - Change one post (opens a text box)', value='edit'),
        app_commands.Choice(name='Repost - Post all of them fresh', value='repost'),
    ])
    async def rules(interaction: discord.Interaction, action: app_commands.Choice[str], post: int = 0):
        if not is_management(interaction):
            await interaction.response.send_message("Sorry, you don't have permission to do that 🔒",
                                                    ephemeral=True)
            return
        if not store.posts:
            await interaction.response.send_message('I have no rules posts saved yet.', ephemeral=True)
            return
        if action.value == 'list':
            lines = [f'**{i + 1}.** {p.splitlines()[0].lstrip("# ")[:60]} ({len(p)} characters)'
                     for i, p in enumerate(store.posts)]
            await interaction.response.send_message('\n'.join(lines), ephemeral=True)
            return
        if action.value == 'edit':
            if not 1 <= post <= len(store.posts):
                await interaction.response.send_message(
                    f'Which post? Use `post:` 1 to {len(store.posts)} (`/rules action:List` shows them).',
                    ephemeral=True)
                return
            await interaction.response.send_modal(
                EditPostModal(store, post - 1, guild_channel_names(interaction.guild)))
            return
        if action.value == 'repost':
            await interaction.response.defer(ephemeral=True)
            summary = await store.publish(force=True)   # deletes ours first, then posts in order
            await interaction.followup.send(f'Done. {summary}', ephemeral=True)
