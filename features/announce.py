"""
Announcements drafted by chatting with the bot.

"@ApexPunk make an announcement about Friday's war" starts it. The AI
(features/ask_ai.py) keeps asking for what's missing, one question at a time
(what, when, which channel, who to ping), then shows a DRAFT card here with
three buttons: ✅ Post, ✏️ Change, ❌ Cancel. Nothing is posted until the
person who asked presses Post (Pat's design, 07/11/2026).

Choices behind it (Pat's):
- default channel: the announcements channel (#ascended-broadcasts); naming
  another channel ("post it in #war-room") uses that instead;
- the ping is asked every time and shown on the draft (nobody / @everyone /
  @here / a role), so a draft can never ping by surprise;
- management only, like the rest of @ApexPunk.

Guard rails, in code rather than trusted to the AI:
- every number in a draft (dates, times, amounts) must come from what the
  person said; an invented "Friday 8pm" gets a question back, not a draft;
- channel and role names are looked up for real; unknown ones get a question;
- only the drafter can press the buttons, and the draft only goes where the
  card says it will.
"""

import logging
import re
import time

import discord

from config import ANNOUNCEMENTS_CHANNEL_ID
from features.rules_posts import guild_channel_names, names_to_links
from utils.helpers import member_is_management

logger = logging.getLogger(__name__)

POST_LIMIT = 2000
DRAFTS_KEPT = 50


def resolve_channel(guild, name):
    """
    The channel a draft goes to: '' -> the announcements channel; '<#id>' or
    a name (with or without its emoji, e.g. 'war-room') -> that channel.
    None when no such channel exists.
    """
    name = (name or '').strip()
    if not name:
        return guild.get_channel(ANNOUNCEMENTS_CHANNEL_ID) if ANNOUNCEMENTS_CHANNEL_ID else None
    m = re.fullmatch(r'<#(\d+)>', name)
    if m:
        return guild.get_channel(int(m.group(1)))
    names = guild_channel_names(guild)
    raw = name if name.startswith('#') else '#' + name
    linked, unknown = names_to_links(raw if ' ' not in raw else f'#[{raw[1:]}]', names)
    m = re.fullmatch(r'<#(\d+)>', linked.strip())
    return guild.get_channel(int(m.group(1))) if m else None


def resolve_ping(guild, ping):
    """
    (prefix text, AllowedMentions, label) for the ping on a draft, or None
    when the role named doesn't exist. Only what the draft shows is allowed
    to ping: everything else in the text is silenced.
    """
    p = (ping or '').strip()
    m = re.search(r'<@&(\d+)>', p)
    if m and guild.get_role(int(m.group(1))):
        role = guild.get_role(int(m.group(1)))
        return role.mention, discord.AllowedMentions(everyone=False, users=False, roles=[role]), f'@{role.name}'
    # "ascended punks role", "@Ascended Punks", "the Punks role" -> the name alone.
    p = re.sub(r'^(the\s+)?@?', '', p, flags=re.I)
    p = re.sub(r'\s+roles?$', '', p, flags=re.I).strip()
    low = p.lower()
    if low in ('', 'none', 'no', 'nobody', 'no one', 'no ping', 'nothing'):
        return '', discord.AllowedMentions.none(), 'nobody'
    if low in ('everyone', 'all', 'everybody'):
        return '@everyone', discord.AllowedMentions(everyone=True, users=False, roles=False), '@everyone'
    if low == 'here':
        return '@here', discord.AllowedMentions(everyone=True, users=False, roles=False), '@here'
    norm = lambda x: re.sub(r'[^a-z0-9]', '', x.lower())
    role = discord.utils.find(lambda r: norm(r.name) == norm(p), guild.roles)
    if role is None:   # a unique partial match: "punks" -> "Ascended Punks"
        hits = [r for r in guild.roles if norm(p) and norm(p) in norm(r.name) and r.name != '@everyone']
        role = hits[0] if len(hits) == 1 else None
    if role is None:
        return None
    return role.mention, discord.AllowedMentions(everyone=False, users=False, roles=[role]), f'@{role.name}'


def build_post(text, prefix):
    return f'{prefix}\n{text}' if prefix else text


class DraftView(discord.ui.View):
    """The ✅ Post / ✏️ Change / ❌ Cancel buttons under a draft. Fixed
    custom_ids and one registration at startup, so a press after a restart
    says "expired" instead of failing silently."""

    def __init__(self, store):
        super().__init__(timeout=None)
        self.store = store

    async def _draft_for(self, interaction):
        draft = self.store.get(interaction.message.id)
        if draft is None:
            await interaction.response.send_message(
                'That draft has expired (the bot restarted since). Ask me again to redo it.', ephemeral=True)
            return None
        if interaction.user.id != draft['author_id']:
            await interaction.response.send_message('Only the person who asked for this draft can do that 🔒',
                                                    ephemeral=True)
            return None
        return draft

    @discord.ui.button(label='Post', emoji='✅', style=discord.ButtonStyle.success, custom_id='announce:post')
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        draft = await self._draft_for(interaction)
        if draft is None:
            return
        guild = interaction.guild
        channel = guild.get_channel(draft['channel_id'])
        pinged = resolve_ping(guild, draft['ping'])
        if channel is None or pinged is None:
            await interaction.response.send_message('The channel or role on this draft no longer exists.',
                                                    ephemeral=True)
            return
        prefix, allowed, label = pinged
        await interaction.response.defer()
        sent = await channel.send(build_post(draft['text'], prefix), allowed_mentions=allowed)
        self.store.pop(interaction.message.id, None)
        await interaction.message.edit(
            content=f'✅ Posted in {channel.mention} by {interaction.user.mention} (ping: {label}). {sent.jump_url}',
            embed=None, view=None, allowed_mentions=discord.AllowedMentions.none())
        logger.info(f'Announcement posted in #{channel} by {interaction.user} (ping: {label})')

    @discord.ui.button(label='Change', emoji='✏️', style=discord.ButtonStyle.secondary, custom_id='announce:edit')
    async def change(self, interaction: discord.Interaction, button: discord.ui.Button):
        draft = await self._draft_for(interaction)
        if draft is None:
            return
        await interaction.response.send_modal(EditDraftModal(self.store, interaction.message, draft))

    @discord.ui.button(label='Cancel', emoji='❌', style=discord.ButtonStyle.danger, custom_id='announce:cancel')
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        draft = await self._draft_for(interaction)
        if draft is None:
            return
        self.store.pop(interaction.message.id, None)
        await interaction.response.edit_message(content='❌ Draft cancelled. Nothing was posted.', embed=None,
                                                view=None)


class EditDraftModal(discord.ui.Modal):
    def __init__(self, store, message, draft):
        super().__init__(title='Change the announcement')
        self.store, self.message, self.draft = store, message, draft
        names = guild_channel_names(message.guild)
        from features.rules_posts import links_to_names
        self.body = discord.ui.TextInput(label='Text (type channels as #war-room)', style=discord.TextStyle.paragraph,
                                         default=links_to_names(draft['text'], names), max_length=1900)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        text, unknown = names_to_links(self.body.value.strip(), guild_channel_names(interaction.guild))
        pinged = resolve_ping(interaction.guild, self.draft['ping'])
        prefix = pinged[0] if pinged else ''
        if len(build_post(text, prefix)) > POST_LIMIT:
            await interaction.response.send_message('That is too long for one Discord message. Nothing changed.',
                                                    ephemeral=True)
            return
        self.draft['text'] = text
        await interaction.response.edit_message(
            content=build_post(text, prefix),
            embed=draft_info(interaction.guild.get_channel(self.draft['channel_id']), self.draft['ping_label']),
            allowed_mentions=discord.AllowedMentions.none())
        if unknown:
            await interaction.followup.send('⚠️ Not turned into links (no channel by that name): '
                                            + ', '.join('#' + u for u in unknown), ephemeral=True)


def draft_info(channel, ping_label, unsure=(), cut=()):
    """
    The small box UNDER the preview: where it goes, who gets pinged, what to
    check. The message itself is the exact post (Pat wanted to see exactly
    what will be posted, 07/11/2026), so the info can't live inside it.
    """
    where = channel.mention if channel is not None else '(unknown channel)'
    lines = [f'📝 **Draft, not posted yet.** Above is exactly what will be posted in {where}.',
             f'Ping: **{ping_label}**' + (' (shown above, not sent in this preview)' if ping_label != 'nobody' else '')]
    if unsure:
        lines.append(f'⚠️ I worked these numbers out myself, check them: **{", ".join(unsure)}**')
    if cut:
        lines.append('✂️ Left out because you didn\'t say it: ' + '; '.join(f'"{c[:80]}"' for c in cut[:5]))
    lines.append('-# Check every detail: I only know what you told me. Only you can press these. '
                 'Reply with changes, or use ✏️ Change to edit it yourself.')
    return discord.Embed(description='\n'.join(lines), color=0xf1c40f)


async def send_draft(message, store, draft, unsure=()):
    """
    Reply to `message` with a draft card. `draft` = {'channel', 'ping', 'text'}
    as the AI gave them. Returns (sent message, None), or (None, question)
    when the channel/role can't be found or the post would be too long.
    """
    guild = message.guild
    channel = resolve_channel(guild, draft.get('channel'))
    if channel is None:
        return None, (f"I can't find a channel called **{draft.get('channel')}**. Which channel should it go in?"
                      if draft.get('channel') else 'Which channel should it go in?')
    pinged = resolve_ping(guild, draft.get('ping'))
    if pinged is None:
        return None, (f"I can't find a role called **{draft.get('ping')}**. Who should I ping: nobody, "
                      '@everyone, @here, or which role?')
    prefix, _, label = pinged
    text, _ = names_to_links((draft.get('text') or '').strip(), guild_channel_names(guild))
    if len(build_post(text, prefix)) > POST_LIMIT:
        return None, 'That came out too long for one Discord message. Want it shorter, or split into two?'
    # The preview IS the post: same text, same ping line, but AllowedMentions
    # .none() so nobody is actually pinged until Post is pressed.
    sent = await message.reply(build_post(text, prefix), embed=draft_info(channel, label, unsure, draft.get('cut') or ()),
                               view=DraftView(store), mention_author=False,
                               allowed_mentions=discord.AllowedMentions.none())
    store[sent.id] = {'author_id': message.author.id, 'channel_id': channel.id, 'ping': draft.get('ping') or '',
                      'ping_label': label, 'text': text, 'at': time.time()}
    while len(store) > DRAFTS_KEPT:
        store.pop(next(iter(store)))
    return sent, None


def can_draft(member):
    return member_is_management(member)
