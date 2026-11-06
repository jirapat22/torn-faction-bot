"""
Rules sync: keep the bot's copy of the rulebook in step with #faction-edicts.

"@ApexPunk what's the OC rule?" quotes the rules word for word
(features/ask_ai.py, faction_rules tool). The bot used to quote a copy kept
in the repo (features/faction_rules.md), because without Discord's Message
Content intent it could not read other people's posts. With the intent on
(07/11/2026), it reads the channel itself: on startup and every 30 minutes,
so an edit Pat makes to a rules post reaches the bot within half an hour.

None-vs-empty, again: if the channel can't be read, or every post comes back
blank (the intent switched off), the last good copy is KEPT. Overwriting it
with nothing would make the bot say "no such rule" about rules that exist.
"""

import logging
import os
import re
import time

from config import RULES_CHANNEL_ID

logger = logging.getLogger(__name__)

LIVE_FILE = 'data/faction_rules_live.md'
MAX_MESSAGES = 50


def render_rules(posts, channel_names):
    """
    Rules posts (oldest first, as plain strings) -> one markdown document.
    Discord's raw channel links '<#123>' become '#name' so the AI can say
    them, and '[text](<url>)' links become 'text (url)'.
    """
    out = []
    for text in posts:
        t = re.sub(r'<#(\d+)>', lambda m: '#' + channel_names.get(int(m.group(1)), 'channel'), text)
        t = re.sub(r'\[([^\]]+)\]\(<?(https?://[^)>\s]+)>?\)', r'\1 (\2)', t)
        t = re.sub(r'[ \t]+\n', '\n', t).strip()
        if t:
            out.append(t)
    return '\n\n'.join(out)


async def refresh(bot, path=LIVE_FILE):
    """
    Read #faction-edicts and save it as the live rulebook. Returns the number
    of posts saved, or None when nothing was written (the old copy stays).
    """
    if not RULES_CHANNEL_ID:
        return None
    channel = bot.get_channel(RULES_CHANNEL_ID)
    if channel is None:
        logger.warning('Rules sync: rules channel not found, keeping the last copy')
        return None
    try:
        msgs = [m async for m in channel.history(limit=MAX_MESSAGES, oldest_first=True)]
    except Exception as e:   # Forbidden, HTTP errors: keep the last good copy
        logger.warning(f'Rules sync: could not read the rules channel ({e!r}), keeping the last copy')
        return None
    # People's posts, plus the bot's OWN (it posts the rulebook itself now,
    # features/rules_posts.py). Other bots' messages are never rules.
    me = bot.user.id if bot.user else None
    posts = [m.content for m in msgs
             if (not m.author.bot or m.author.id == me) and (m.content or '').strip()]
    if not posts:
        # Blank posts usually mean the Message Content intent is off again.
        logger.warning(f'Rules sync: {len(msgs)} message(s) but no readable text, keeping the last copy')
        return None
    names = {c.id: c.name for c in channel.guild.channels}
    names.update({t.id: t.name for t in channel.guild.threads})
    # Links to forum guide posts (threads, often archived) aren't in either
    # list, and showed up as "#channel". Look each one up once.
    for cid in {int(x) for p in posts for x in re.findall(r'<#(\d+)>', p)} - set(names):
        try:
            names[cid] = (await bot.fetch_channel(cid)).name
        except Exception:
            pass   # deleted or hidden: stays "#channel"
    body = render_rules(posts, names)
    header = (f'<!-- Synced from #{channel.name} at {time.strftime("%d/%m/%Y %H:%M", time.gmtime())} UTC '
              f'by features/rules_sync.py. Edit the Discord posts, not this file. -->\n\n')
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(header + body + '\n')
    os.replace(tmp, path)   # atomic, like utils/helpers.save_json
    logger.info(f'Rules sync: saved {len(posts)} post(s) from #{channel.name}')
    return len(posts)
