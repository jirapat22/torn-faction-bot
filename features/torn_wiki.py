"""
"How does X work in Torn?" for @ApexPunk (Pat, 22/11/2026: "do all").

Three sources, in this order, all behind one tool (torn_lookup):

a) Torn's own API: every item's type, effect and description, saved to
   data/torn_items.json and refreshed once a day. Official, never stale.
   Prices are deliberately left out: Pat's rule is no money in chat.
b) Hand-saved wiki pages in features/wiki/*.md. The wiki answers bots with
   403 (checked 22/11/2026), so the pages Pat cares about are pasted in by
   hand. Each file starts "# Title" and a "Source:" line, and is split into
   sections on "## " headings, so an answer quotes the right part only.
c) A wiki search link, always, so anything not covered above is one tap
   away instead of a dead end.

Everything here is pure or file-based except refresh_items(), so the
search logic is testable without Torn.
"""

import glob
import logging
import os
import re
import time
from urllib.parse import quote_plus

from utils.helpers import load_json, save_json

logger = logging.getLogger(__name__)

ITEMS_FILE = 'data/torn_items.json'
ITEMS_MAX_AGE = 24 * 3600
WIKI_DIR = os.path.join(os.path.dirname(__file__), 'wiki')
WIKI_SEARCH = 'https://wiki.torn.com/w/index.php?search='
MAX_ITEMS = 5
WIKI_LIMIT = 2500     # characters of saved wiki text handed to the AI per question

# Words that say nothing about WHAT is being asked about.
STOP = set('''a an the is are was were be do does did how what whats what's which who why when where can could
should would will i me my we our you your it its this that these those of in on at to for from with and or
about tell explain work works working use used using get got make mean means torn wiki apexpunk please
pls hey yo any some much many there here thing stuff'''.split())


def words(text):
    """Lowercase keywords of a question, filler words removed."""
    return [w for w in re.findall(r"[a-z0-9+']+", (text or '').lower()) if w not in STOP and len(w) > 1]


def search_link(question):
    """The wiki search URL for a question's keywords (c)."""
    kw = words(question)[:6]
    return WIKI_SEARCH + quote_plus(' '.join(kw)) if kw else 'https://wiki.torn.com'


# ---------- a) items from Torn's API ----------

def slim_items(raw_items):
    """Torn's items payload -> {id: {name, type, effect, description,
    requirement}}. Prices, market value and circulation are dropped here, at
    the source, so they can never reach a chat answer."""
    out = {}
    for iid, it in (raw_items or {}).items():
        if not isinstance(it, dict) or not it.get('name'):
            continue
        out[str(iid)] = {k: (it.get(k) or '').strip() if isinstance(it.get(k), str) else it.get(k)
                         for k in ('name', 'type', 'effect', 'description', 'requirement')}
    return out


async def refresh_items(path=ITEMS_FILE, now=None):
    """The saved item list, refreshed from Torn when older than a day.
    A failed refresh keeps the last good copy; None only if there never was one."""
    now = now or time.time()
    saved = load_json(path, default={}) or {}
    if saved.get('items') and now - saved.get('fetched', 0) < ITEMS_MAX_AGE:
        return saved['items']
    try:
        from utils.torn_api import _make_api_request
        data = await _make_api_request('torn', {'selections': 'items'})
    except Exception as e:
        logger.error(f'Torn items: refresh failed: {e}')
        data = None
    items = slim_items((data or {}).get('items'))
    if items:
        save_json(path, {'fetched': now, 'items': items})
        return items
    return saved.get('items') or None


def find_items(question, items):
    """Items the question names, best first. An exact name wins outright;
    otherwise names containing the whole phrase, then names sharing words."""
    if not items:
        return []
    q = (question or '').lower()
    kw = set(words(question))
    exact, phrase, partial = [], [], []
    for it in items.values():
        name = (it.get('name') or '').lower()
        if not name:
            continue
        if re.search(rf'(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])', q):
            exact.append(it)
            continue
        nw = set(words(name))
        if nw and nw <= kw:
            phrase.append(it)
        elif kw & nw and len(kw & nw) >= max(1, len(nw) - 1):
            partial.append(it)
    # Longer exact names first: "Blood Bag : O+" before "Blood Bag".
    exact.sort(key=lambda it: -len(it['name']))
    return (exact or phrase or partial)[:MAX_ITEMS]


def item_line(it):
    bits = [f"ITEM {it['name']}" + (f" ({it['type']})" if it.get('type') else '')]
    if it.get('effect'):
        bits.append('Effect: ' + it['effect'].replace('\n', ' '))
    if it.get('requirement'):
        bits.append('Needs: ' + it['requirement'])
    if it.get('description'):
        first = re.split(r'(?<=[.!?])\s', it['description'])[0]
        bits.append('Description: ' + first)
    return ' | '.join(bits)


# ---------- b) saved wiki pages ----------

def load_pages(wiki_dir=WIKI_DIR):
    """[(title, source, [(heading, text)])] for every saved page."""
    pages = []
    for path in sorted(glob.glob(os.path.join(wiki_dir, '*.md'))):
        if os.path.basename(path).lower() == 'readme.md':
            continue
        try:
            text = open(path, encoding='utf-8').read()
        except OSError:
            continue
        title = re.search(r'^#\s+(.+)$', text, re.M)
        title = title.group(1).strip() if title else os.path.basename(path)[:-3]
        source = re.search(r'^Source:\s*(\S+)', text, re.M)
        source = source.group(1) if source else ''
        sections, head, buf = [], title, []
        for line in text.split('\n'):
            if line.startswith('## '):
                if ''.join(buf).strip():
                    sections.append((head, '\n'.join(buf).strip()))
                head, buf = line[3:].strip(), []
            elif not line.startswith('# ') and not line.startswith('Source:'):
                buf.append(line)
        if ''.join(buf).strip():
            sections.append((head, '\n'.join(buf).strip()))
        pages.append((title, source, sections))
    return pages


def find_wiki(question, pages):
    """Best saved sections for the question. A section needs at least one
    keyword in its page title or heading, so a stray word in the body of an
    unrelated page doesn't pass as an answer."""
    kw = set(words(question))
    if not kw:
        return []
    scored = []
    for title, source, sections in pages:
        tw = set(words(title))
        for head, body in sections:
            hw = set(words(head)) | tw
            if not kw & hw:
                continue
            bw = words(body)
            score = 3 * len(kw & hw) + sum(1 for w in bw if w in kw) / max(1, len(bw)) * 50
            scored.append((score, title, source, head, body))
    scored.sort(key=lambda s: -s[0])
    out, size = [], 0
    for _, title, source, head, body in scored:
        block = f"WIKI ({title}{', ' + head if head != title else ''}; saved copy of {source or 'the Torn wiki'}):\n{body}"
        if size + len(block) > WIKI_LIMIT and out:
            break
        out.append(block[:WIKI_LIMIT])
        size += len(block)
    return out


def lookup_text(question, items, pages):
    """The tool's whole answer: items, saved wiki sections, and the link."""
    found = [item_line(it) for it in find_items(question, items)] + find_wiki(question, pages)
    link = search_link(question)
    if items is None:
        found.insert(0, "(Torn's item list could not be loaded right now.)")
    tail = f'WIKI SEARCH LINK (always give this to the person): {link}'
    if not [f for f in found if not f.startswith('(')]:
        return f'Nothing saved about that. {tail}', False
    return '\n'.join(found + [tail]), True


PRICE_RX = re.compile(r'\b(?:cost|costs|price|prices|worth|sell|sells|buy for|how much is|value)\b', re.I)


def item_answer(raw, question):
    """
    The bot's own answer when the lookup found items and no wiki text:
    Torn's effect text word for word. None when there is wiki text (the AI
    summarises that) or no item at all.
    """
    lines = (raw or '').split('\n')
    if any(l.startswith('WIKI (') for l in lines):
        return None
    found = [l for l in lines if l.startswith('ITEM ')]
    if not found:
        return None
    out = []
    for line in found[:3]:
        parts = line.split(' | ')
        out.append('**' + parts[0][len('ITEM '):] + '**')
        for part in parts[1:]:
            label, _, text = part.partition(': ')
            out.append(f'- {label}: {text}' if text else f'- {part}')
        out.append('')
    if len(found) > 3:
        out.append('Also matching: ' + ', '.join(l.split(' | ')[0][len('ITEM '):] for l in found[3:]))
    if PRICE_RX.search(question or ''):
        out.append("-# I don't do prices, money stays out of chat. Check the item market in Torn.")
    out.append(f'-# From Torn\'s official item list. More on the wiki: <{search_link(question)}>')
    return '\n'.join(out).strip()
