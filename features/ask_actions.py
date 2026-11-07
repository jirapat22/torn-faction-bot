"""
Things @ApexPunk can DO by chat, not just answer (Pat, 07/11/2026):

  - add or remove a target on the target board
  - start, end or cancel a chain event
  - post an availability poll (for the next war, or for times you give)
  - create a Discord scheduled event (war, chain, happy jump or custom)

Pat's rule for all of them: "if bot does not have sufficient information,
always ask first, dont make number up dont create stuffs half way". So:

1. The AI only fills in what the person said. Python then checks every field
   (plan_action): a number the person never typed, a "when" with words they
   never used, or a required field left empty -> the bot ASKS, in its own
   words, listing everything still missing in one go.
2. Nothing happens until a confirm card is shown with exactly what will be
   done (resolved names, the real date and time) and the person who asked
   presses ✅. Same pattern as announcement drafts (features/announce.py).
3. Strikes are deliberately NOT here: too important to start from a chat
   misread. They stay on /strikes and the chain-event shortfall buttons.

The work itself is done by the features' own code (TargetManager.add_target,
ChainEventManager.start, AvailabilityManager.create_poll, ...), so a chat
action behaves exactly like the slash command.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone

import discord

logger = logging.getLogger(__name__)

ACTIONS_KEPT = 50

# Words that make a message a request to DO something the action tools cover.
# Only used to decide whether to show the AI the action tools (each one costs
# prompt tokens on Groq's 200,000/day plan) and to keep a conversation going.
ACTION_RX = re.compile(
    r'\b(add|put|remove|take|delete|start|begin|kick ?off|end|finish|stop|cancel|close|make|create|set ?up|'
    r'schedule|post|open|run|launch)\b[^\n]{0,90}?'
    r'\b(targets?|board|chain ?events?|chains?|polls?|availability|events?)\b', re.I)

EVENT_TYPES = {
    # type: (name, hours, ends by hand, default description). Same as the
    # /createevent buttons (features/event_ui.py CommonEventModal).
    'war': ('Faction War', 168, True, '⚔️ War event - Manually end when war completes'),
    'chain': ('Chain Event', 24, False, "Let's build a massive chain! Updates in #chaining"),
    'happy_jump': ('Happy Jump', 24, False, 'Energy refill time! Use your energy before the jump'),
}


# ─────────────────────────────────────────────────────────────────────────────
# Checking what the AI filled in (pure, unit tested)
# ─────────────────────────────────────────────────────────────────────────────

def _numbers(text):
    from features.ask_ai import numbers_in
    return numbers_in(str(text or ''))


def said_number(value, said):
    """True when every number in `value` is one the person typed."""
    from features.ask_ai import unsupported_numbers
    return not unsupported_numbers(str(value), said)


_FILLER = {'at', 'on', 'the', 'this', 'next', 'tct', 'utc', 'from', 'by', 'of', 'in', 'a', 'and', 'to', 'pm',
           'am', 'starting', 'start', 'it', 'around'}


def said_words(value, said):
    """True when the words in a "when" phrase are the person's own (so the AI
    can't turn "this weekend" into "saturday 8pm")."""
    words = re.findall(r'[a-z]+', str(value or '').lower())
    have = set(re.findall(r'[a-z]+', said.lower()))
    return all(w in have or w in _FILLER for w in words)


_OWN_STOP = {'the', 'and', 'for', 'with', 'from', 'our', 'his', 'her', 'their', 'this', 'that', 'event', 'events',
             'chain', 'war', 'faction', 'target', 'targets', 'board', 'reason', 'because', 'they', 'are', 'was',
             'all', 'get', 'lets', 'let', 'its', 'you', 'your', 'him', 'them', 'who', 'has', 'have', 'into'}


def own_words(text, said):
    """True when a free-text field (a name, reason or description) is made of
    the person's own words and numbers. The AI kept "improving" these (Pat:
    "dont create stuffs"), and whatever lands here goes on a real card, board
    or Discord event."""
    have = set(re.findall(r'[a-z0-9]+', said.lower()))
    for w in re.findall(r'[a-z0-9]+', str(text or '').lower()):
        if w not in have and w not in _OWN_STOP and w not in _FILLER and (w.isdigit() or len(w) >= 3):
            return False
    return True


def _with_year(text, now):
    """'14/11 13:00' -> '14/11/2026 13:00': parse_when wants a year, people don't type one."""
    year = datetime.fromtimestamp(now, timezone.utc).year
    return re.sub(r'\b(\d{1,2})[/-](\d{1,2})\b(?![/-]\d)', lambda m: f'{m.group(1)}/{m.group(2)}/{year}',
                  text)


_CLOCK = re.compile(r'\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*(?:am|pm)\b|\bnoon\b|\bmidday\b|\bmidnight\b|'
                    r'\bin\s+\d+\s*(?:h|hrs?|hours?|m|mins?|minutes?)\b', re.I)


def parse_time(text, now, allow_now=False, need_time=False):
    """(epoch, None) or (None, why). 'now' is allowed where it makes sense.
    need_time: a bare day ("saturday", "tomorrow") is not enough, because the
    parser quietly reads it as 00:00, a time nobody said (chain/event starts)."""
    from utils.when import parse_when, WhenError
    t = (text or '').strip().lower()
    if allow_now and t in ('now', 'right now', 'straight away', 'asap', 'immediately'):
        return int(now), None
    if not _CLOCK.search(t):
        # "sunday 18" was read as Sunday 00:00, the 18 silently dropped. A
        # number that is neither a date nor a clock time gets a question.
        if re.search(r'\d', re.sub(r'\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b', '', t)):
            return None, f'I couldn\'t tell the time in "{text}". Try "saturday 18:00" or "saturday 6pm"'
        if need_time:
            return None, f'"{text}" has no time. Add one, like "{text} 20:00"'

    try:
        dt = parse_when(_with_year(text, now), now=datetime.fromtimestamp(now, timezone.utc))
    except WhenError as e:
        return None, str(e)
    except (ValueError, TypeError):
        return None, 'I couldn\'t read that time.'
    return int(dt.timestamp()), None


def _tct(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime('%a %d/%m/%Y %H:%M TCT')


def _stamp(ts):
    """TCT plus Discord's local time and countdown, like the announcements."""
    return f'{_tct(ts)} (<t:{int(ts)}:f>, <t:{int(ts)}:R>)'


def _torn_id(text):
    """A Torn ID from a bare number, "[123]" or a profile link, else None.
    Never digits out of a name: "Ikaros22" once became player 22."""
    t = str(text or '').strip()
    m = re.search(r'(?:XID=|ID=)(\d{1,10})\b', t, re.I) or re.fullmatch(r'\[?(\d{1,10})\]?', t)
    return m.group(1) if m else None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def plan_action(tool, args, said, now=None):
    """
    Check one action request. Returns ('ask', question) or ('plan', plan).

    `said` is everything the person typed in this conversation. A plan is
    {'kind', 'title', 'lines', ...params}; it still gets a live check (does
    the player exist? is an event already running?) before the card is shown.
    """
    now = now or time.time()
    args = args or {}
    need = []          # what to ask for, in plain words

    def num(field, label, required=True, lo=1, hi=None):
        """A number field: kept only if the person typed it."""
        v = _int(args.get(field))
        if not v:                # missing (0 counts as missing: "0 hours" is never meant)
            if required:
                need.append(label)
            return None
        if not said_number(v, said):
            # The AI made it up. A required one is asked for; an optional one
            # is dropped, and the card then shows the labelled default.
            if required:
                need.append(label)
            return None
        if v < lo or (hi is not None and v > hi):
            need.append(f'{label} (between {lo} and {hi})' if hi else label)
            return None
        return v

    def when(field, label, allow_now=False, required=True, need_time=False):
        raw = str(args.get(field) or '').strip()
        # ' 0': the ":00" in "13:00" is never an invented number (same as drafts).
        if not raw or not said_words(raw, said) or not said_number(raw, said + ' 0'):
            if required:
                need.append(label)
            return None, None
        ts, why = parse_time(raw, now, allow_now=allow_now, need_time=need_time)
        if ts is None:
            need.append(f'{label} ({why.rstrip(".")})')
        return ts, raw

    def ask():
        return ('ask', 'Before I do that I need: ' + '; '.join(need) + '.' if len(need) > 1 else
                f'Before I do that I need {need[0]}.')

    if tool == 'action_target':
        op = str(args.get('op') or '').lower()
        if op not in ('add', 'remove'):
            return 'ask', 'Do you want to add someone to the target board, or take someone off it?'
        who = _torn_id(args.get('who'))
        # Whole number only: "152483" must not pass because "1524830" was typed.
        # And typed as a number of its own (or in a link), not the tail of a
        # name in the conversation like "Ikaros22".
        if not who or not re.search(rf'(?:(?<![\w])|(?<=XID=)|(?<=ID=)){who}(?!\d)', said, re.I):
            return 'ask', ('Who? Give me their Torn ID or paste their profile link (I can\'t look enemies up by '
                           'name).')
        reason = str(args.get('reason') or '').strip()[:100]
        if not own_words(reason, said):
            reason = ''          # an invented reason would sit on the board for a week
        permanent = bool(args.get('permanent')) and bool(re.search(r'perm|forever|until (removed|i|we)|stay', said,
                                                                   re.I))
        return 'plan', {'kind': 'target_' + op, 'torn_id': who, 'reason': reason if op == 'add' else '',
                        'permanent': permanent}

    if tool == 'action_chain_event':
        op = str(args.get('op') or '').lower()
        if op in ('end', 'cancel'):
            return 'plan', {'kind': 'chain_' + op}
        if op != 'start':
            return 'ask', 'Do you want to start, end or cancel a chain event?'
        start, _ = when('when', 'when it starts ("now", or a TCT time like "saturday 13:00")', allow_now=True,
                        need_time=True)
        # Over several messages the AI tends to drop what was said earlier
        # ("24 hours", then "min 10", then asked for the hours again). The
        # person's own words still hold it, so Python reads it from them when
        # it is unambiguous: their number, never a guess.
        args = dict(args)
        for field, rx in (('hours', r'\b(\d+)\s*(?:h|hrs?|hours?)\b'),
                          ('minimum', r'\bmin(?:imum)?\.?\s*(?:of\s*)?(\d+)\b|\b(\d+)\s*(?:hits?\s*)?min(?:imum)?\b')):
            if not (_int(args.get(field)) and said_number(_int(args.get(field)), said)):
                found = {next(g for g in ((m,) if isinstance(m, str) else m) if g)
                         for m in re.findall(rx, said, re.I)}
                if len(found) == 1:
                    args[field] = int(found.pop())
        hours = num('hours', 'how long it runs, in hours (usually 24)', hi=24 * 7)
        minimum = num('minimum', 'the minimum hits each member must make (the rule is usually 10)', hi=1000)
        target = num('target', '', required=False, lo=0)
        if need:
            return ask()
        name = str(args.get('name') or '').strip()[:60]
        if not own_words(name, said):
            name = ''            # the card then uses the date name ("08 Nov chain event")
        return 'plan', {'kind': 'chain_start', 'start': start, 'hours': hours, 'minimum': minimum,
                        'target': target or 0, 'name': name}

    if tool == 'action_poll':
        kind = str(args.get('kind') or '').lower()
        if kind == 'war':
            return 'plan', {'kind': 'poll_war'}
        if kind != 'custom':
            return 'ask', ('Is the poll for the next ranked war (I take the times from Torn), or for times you '
                           'give me?')
        start, _ = when('start', 'when the poll\'s first block starts (a TCT day and time)')
        end, end_raw = when('end', 'when the last block ends (a TCT day and time)')
        # "monday and tuesday": a bare day as the END means the end of that
        # day, not its first minute (which made a Monday-only poll).
        if end and not re.search(r'\d', end_raw or '') and end % 86400 == 0:
            end += 86400
        block = num('block_hours', '', required=False, hi=6)
        open_h = num('open_hours', '', required=False, hi=24 * 7)
        if start and end and end <= start:
            need.append('an end time after the start')
        if start and end and end - start > 7 * 86400:
            need.append('a shorter span (a poll covers a week at most)')
        if need:
            return ask()
        return 'plan', {'kind': 'poll_custom', 'start': start, 'end': end, 'block_hours': block or 2,
                        'block_default': not block, 'open_hours': open_h or 24, 'open_default': not open_h}

    if tool == 'action_discord_event':
        etype = str(args.get('type') or '').lower().replace(' ', '_')
        if etype not in ('war', 'chain', 'happy_jump', 'custom'):
            return 'ask', 'What kind of event: war, chain, happy jump, or something else (then what\'s it called)?'
        start, _ = when('when', 'when it starts (a TCT day and time, like "saturday 8pm")', need_time=True)
        name = str(args.get('name') or '').strip()[:100]
        if not own_words(name, said):
            name = ''
        hours = None
        if etype == 'custom':
            if not name:
                need.append('what the event is called')
            hours = num('hours', 'how long it lasts, in hours', hi=24 * 14)
        desc = str(args.get('description') or '').strip()[:400]
        if not own_words(desc, said):
            desc = ''            # the type's standard description instead
        if need:
            return ask()
        if etype != 'custom':
            name, hours, manual, default_desc = EVENT_TYPES[etype]
        else:
            manual, default_desc = False, ''
        return 'plan', {'kind': 'discord_event', 'type': etype, 'name': name, 'start': start, 'hours': hours,
                        'manual_end': manual, 'description': desc or default_desc}

    return 'ask', 'What would you like me to do?'


# ─────────────────────────────────────────────────────────────────────────────
# The confirm card
# ─────────────────────────────────────────────────────────────────────────────

async def describe(plan, live, guild, now=None):
    """
    (title, lines) for the card, or (None, reason) when it can't be done (the
    player doesn't exist, an event is already running...). Looks things up
    for real, so the card shows names, not just the IDs the AI passed.
    """
    now = now or time.time()
    k = plan['kind']
    if k.startswith('target_'):
        tm = getattr(live, 'target_manager', None)
        if tm is None:
            return None, 'The target board is not running on the bot.'
        tid = plan['torn_id']
        if k == 'target_remove':
            t = tm.data['targets'].get(tid)
            if t is None:
                return None, f'Player {tid} is not on the target board.'
            return '🎯 Remove a target', [f'Take **{t.get("name", tid)}** [{tid}] off the target board.']
        # Already on the board: say so instead of a card that would do nothing
        # (add_target refuses duplicates). The one real change left is making
        # a 7-day target permanent, which add_target does on a re-add.
        existing = tm.data['targets'].get(tid)
        if existing is not None:
            who = existing.get('name', tid)
            if not (plan['permanent'] and not existing.get('permanent')):
                return None, (f'**{who}** [{tid}] is already on the target board'
                              + (' (permanent).' if existing.get('permanent') else '.'))
            plan['name'] = who
            return '🎯 Make a target permanent', [f'**{who}** [{tid}] is on the board for 7 days. '
                                                 'Make it **permanent** (until someone removes them)?']
        from utils.torn_api import fetch_player_data
        from config import TORN_FACTION_ID
        profile = await fetch_player_data(tid, 'profile')
        if profile is None:
            return None, f"I couldn't look up Torn ID {tid}. Check the ID and try again."
        if str((profile.get('faction') or {}).get('faction_id')) == str(TORN_FACTION_ID):
            return None, f'**{profile.get("name", tid)}** is one of us, so no.'
        plan['name'] = profile.get('name') or tid
        fac = (profile.get('faction') or {}).get('faction_name')
        lines = [f'Add **{plan["name"]}** [{tid}], level {profile.get("level", "?")}'
                 + (f', {fac}' if fac else '') + ' to the target board',
                 '📌 **Permanently** (until someone removes them)' if plan['permanent'] else 'For **7 days**']
        if plan['reason']:
            lines.append(f'Reason: "{plan["reason"]}"')
        return '🎯 Add a target', lines

    if k.startswith('chain_'):
        m = getattr(live, 'chain_event_manager', None)
        if m is None:
            return None, 'Chain events are not set up on the bot.'
        if k == 'chain_start':
            running = m.active() or m.pending()
            if running:
                return None, (f'**{running["name"]}** is already {running.get("status")}. End or cancel it first.')
            from features.chain_events import EVENT_EARLIEST_TCT_HOUR
            plan['name'] = plan['name'] or datetime.fromtimestamp(now, timezone.utc).strftime('%d %b chain event')
            lines = [f'Start the chain event **{plan["name"]}**',
                     'Starts: **now**' if plan['start'] <= now + 60 else f'Starts: **{_stamp(plan["start"])}**',
                     f'Runs: **{plan["hours"]} hours** (ends {_tct(plan["start"] + plan["hours"] * 3600)})',
                     f'Minimum: **{plan["minimum"]} hits** each']
            if plan['target']:
                lines.append(f'Target: **{plan["target"]}** chain hits in total')
            if datetime.fromtimestamp(plan['start'], timezone.utc).hour > EVENT_EARLIEST_TCT_HOUR \
                    and plan['start'] > now + 60:
                lines.append(f'⚠️ Torn events can start from {EVENT_EARLIEST_TCT_HOUR:02d}:00 TCT. Anyone who began '
                             'before this window opens loses those hits off their total.')
            return '⛓️ Start a chain event', lines
        ev = m.current()
        if not ev or ev.get('status') not in ('running', 'scheduled'):
            return None, 'There is no chain event running or scheduled.'
        plan['event_id'] = ev['id']
        if k == 'chain_cancel':
            return '⛓️ Cancel the chain event', [f'Cancel **{ev["name"]}** ({ev.get("status")}).',
                                                 'No strikes and no shortfall list.']
        if ev.get('status') == 'scheduled':
            return None, f'**{ev["name"]}** has not started yet, so there is nothing to end. Cancel it instead?'
        return '⛓️ End the chain event', [f'End **{ev["name"]}** now and count everyone\'s hits.',
                                          f'Then I post who is under the minimum of {ev.get("minimum")}. '
                                          'Nobody gets a strike unless someone presses the button on that list.']

    if k.startswith('poll_'):
        am = getattr(live, 'availability_manager', None)
        if am is None:
            return None, 'Availability polls are not set up on the bot.'
        if k == 'poll_war':
            wt = getattr(live, 'war_tracker', None)
            up = await wt.get_upcoming_war() if wt is not None else None
            if not up:
                return None, ('No ranked war is enlisted yet, so I don\'t know its times. Give me the days and '
                              'times for the poll instead.')
            if any(str(p.get('war_id')) == str(up['war_id']) for p in am.polls.values()):
                return None, f'There is already a poll for war {up["war_id"]}.'
            plan['war_id'], plan['war_start'] = str(up['war_id']), int(up['start'])
            return '🗳️ Post the war availability poll', [
                f'War {up["war_id"]} starts **{_stamp(up["start"])}**',
                'Same poll the bot posts by itself before a war: the first day of the war in blocks, '
                'closes an hour before it starts, posted in the availability channel.']
        lines = [f'From **{_stamp(plan["start"])}**', f'To **{_tct(plan["end"])}**',
                 f'Blocks of **{plan["block_hours"]} hours**' + (' (the usual)' if plan['block_default'] else ''),
                 f'Open for **{plan["open_hours"]} hours**' + (' (the usual)' if plan['open_default'] else ''),
                 'Posted in the availability channel.']
        return '🗳️ Post an availability poll', lines

    if k == 'discord_event':
        dur = 'until someone ends it' if plan['manual_end'] else f'{plan["hours"]} hours'
        lines = [f'Create the Discord event **{plan["name"]}**', f'Starts: **{_stamp(plan["start"])}**',
                 f'Lasts: **{dur}**', f'Description: "{plan["description"]}"' if plan['description'] else '',
                 'I also post it in its channel, like /createevent.']
        return '📅 Create a Discord event', [l for l in lines if l]

    return None, "I don't know how to do that."


class ActionView(discord.ui.View):
    """✅ Do it / ❌ Cancel under an action card. Fixed custom_ids, registered
    once at startup, so a press after a restart says "expired"."""

    def __init__(self, store, live):
        super().__init__(timeout=None)
        self.store, self.live = store, live

    async def _entry(self, interaction):
        entry = self.store.get(interaction.message.id)
        if entry is None:
            await interaction.response.send_message('That expired (the bot restarted since). Ask me again.',
                                                    ephemeral=True)
            return None
        if interaction.user.id != entry['author_id']:
            await interaction.response.send_message('Only the person who asked can do that 🔒', ephemeral=True)
            return None
        return entry

    @discord.ui.button(label='Do it', emoji='✅', style=discord.ButtonStyle.success, custom_id='action:go')
    async def go(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = await self._entry(interaction)
        if entry is None:
            return
        self.store.pop(interaction.message.id, None)   # one press only, even if Discord sends two
        await interaction.response.defer()
        try:
            result = await run_action(entry['plan'], self.live, interaction)
        except Exception as e:
            logger.exception('ask_actions: action failed')
            result = f'⚠️ That didn\'t work ({type(e).__name__}). Nothing else was changed. Try the slash command.'
        await interaction.message.edit(content=result, embed=None, view=None,
                                       allowed_mentions=discord.AllowedMentions.none())
        logger.info(f'Chat action {entry["plan"]["kind"]} by {interaction.user}: {result[:120]!r}')

    @discord.ui.button(label='Cancel', emoji='❌', style=discord.ButtonStyle.danger, custom_id='action:cancel')
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._entry(interaction) is None:
            return
        self.store.pop(interaction.message.id, None)
        await interaction.response.edit_message(content='❌ Cancelled. Nothing was done.', embed=None, view=None)


async def send_card(message, store, plan, live):
    """Reply with the confirm card. Returns (sent, None) or (None, reason it can't be done)."""
    title, lines = await describe(plan, live, message.guild)
    if title is None:
        return None, lines
    embed = discord.Embed(title=title, description='\n'.join(f'- {l}' for l in lines), color=0xf1c40f)
    embed.set_footer(text='Nothing happens until you press ✅. Only you can press these. '
                          'Reply with changes and I\'ll redo the card.')
    sent = await message.reply(embed=embed, view=ActionView(store, live), mention_author=False,
                               allowed_mentions=discord.AllowedMentions.none())
    store[sent.id] = {'author_id': message.author.id, 'plan': plan, 'at': time.time()}
    while len(store) > ACTIONS_KEPT:
        store.pop(next(iter(store)))
    return sent, None


# ─────────────────────────────────────────────────────────────────────────────
# Doing it (only after ✅)
# ─────────────────────────────────────────────────────────────────────────────

async def run_action(plan, live, interaction):
    """Do the confirmed action with the feature's own code. Returns what to show."""
    k = plan['kind']
    now = time.time()

    if k == 'target_add':
        tm = live.target_manager
        return '🎯 ' + await tm.add_target(interaction, plan['torn_id'], plan['reason'], permanent=plan['permanent'])
    if k == 'target_remove':
        return '🎯 ' + live.target_manager.remove_target(interaction, plan['torn_id'])

    if k == 'chain_start':
        m = live.chain_event_manager
        ev, notes = m.start(plan['name'], plan['start'], plan['hours'], minimum=plan['minimum'],
                            target=plan['target'])
        if not ev:
            return '⚠️ ' + ' '.join(notes)
        word = 'scheduled' if ev['status'] == 'scheduled' else 'started'
        msg = (f'⛓️ **{ev["name"]}** {word}: counting {_tct(ev["start"])} to {_tct(ev["end"])}, '
               f'minimum **{ev["minimum"]}** hits each.')
        if ev['status'] == 'scheduled':
            msg += ' I\'ll announce it in the chain channel when it begins.'
        return msg + (('\n-# ' + ' · '.join(notes)) if notes else '')

    if k in ('chain_end', 'chain_cancel'):
        m = live.chain_event_manager
        ev = m.events.get(plan.get('event_id'))
        if not ev or ev.get('status') not in ('running', 'scheduled'):
            return '⚠️ That chain event is no longer running, so nothing was changed.'
        if k == 'chain_cancel':
            m.cancel(ev)
            return f'⛓️ **{ev["name"]}** cancelled. No strikes, no shortfall list.'
        from features.chain_events import progress_embed, shortfall, eligible, ShortfallView
        totals, ok = await m.tally(ev, full=True)
        roster = await m.roster_for(ev)
        judged = eligible(roster, ev) if roster else None
        short = shortfall(totals, judged, ev['minimum'])
        embed = progress_embed(ev, totals, roster, ok)
        if not ok or short is None:
            # Same refusal as /chain Event end: never close on numbers we
            # couldn't verify, a strike list off a partial read is the risk.
            await interaction.channel.send(embed=embed)
            return ('⚠️ **Not ending it.** The counts could not be verified, so nothing is final. '
                    'Try again in a few minutes.')
        m.finish(ev)
        if not short:
            await interaction.channel.send(embed=embed)
            return f'✅ **{ev["name"]}** finished. Everyone met the minimum, nothing to action.'
        await interaction.channel.send(
            embed=embed, view=ShortfallView(m, ev, short),
            content=f'⛓️ **{ev["name"]}** finished · **{len(short)}** below the minimum. '
                    'Nothing is struck until somebody clicks.')
        return f'⛓️ **{ev["name"]}** ended. The results are posted below.'

    if k == 'poll_war':
        from config import AVAILABILITY_CHANNEL_ID, VERIFIED_ROLE_NAME
        am = live.availability_manager
        if any(str(p.get('war_id')) == plan['war_id'] for p in am.polls.values()):
            return f'There is already a poll for war {plan["war_id"]}, so I didn\'t post another.'
        channel = interaction.client.get_channel(AVAILABILITY_CHANNEL_ID)
        if channel is None:
            return '⚠️ I can\'t find the availability channel, so nothing was posted.'
        await am._post_war_poll(channel, plan['war_id'], plan['war_start'], VERIFIED_ROLE_NAME)
        return f'🗳️ War availability poll posted in {channel.mention}.'

    if k == 'poll_custom':
        return await _post_custom_poll(plan, live, interaction)

    if k == 'discord_event':
        from features.event_ui import _announce_event
        start = datetime.fromtimestamp(plan['start'], timezone.utc)
        hours = plan['hours']
        desc = (plan['description'] + '\n\n' if plan['description'] else '') + \
            f'⏰ Time: {start:%d-%m-%Y %H:%M} TCT (UTC)'
        event = await interaction.guild.create_scheduled_event(
            name=plan['name'], description=desc, start_time=start, end_time=start + timedelta(hours=hours),
            privacy_level=discord.PrivacyLevel.guild_only, entity_type=discord.EntityType.external,
            location='Torn City')
        await _announce_event(interaction, plan['name'], plan['start'], hours, plan['manual_end'])
        return (f'📅 Event **{plan["name"]}** created for {_tct(plan["start"])}.'
                + (' Right-click it → End Event when the war is over.' if plan['manual_end'] else '')
                + f' {event.url}')

    return '⚠️ I don\'t know how to do that one.'


async def _post_custom_poll(plan, live, interaction):
    """
    The /avail action:Setup poll, from a confirmed card. Mirrors the slash
    command's setup (features/availability.py) on purpose, using the same
    create_poll and day-menu view, so both polls look and behave the same.
    """
    from config import VERIFIED_ROLE_NAME, EMBED_COLOR_INFO
    from features.availability import poll_post_target, block_list_lines, AvailabilityDropdownView
    from utils.helpers import create_embed
    am = live.availability_manager
    s = datetime.fromtimestamp(plan['start'], timezone.utc)
    e = datetime.fromtimestamp(plan['end'], timezone.utc)
    end_time = e.strftime('%H:%M')
    # create_poll reads an end of 00:00 as "end of that day" and adds the day
    # itself (see _post_war_poll), so the date steps back one.
    end_date = (e - timedelta(days=1) if end_time == '00:00' else e).strftime('%d-%m-%Y')
    start_date = s.strftime('%d-%m-%Y')
    target = poll_post_target(interaction.client, interaction.channel)
    poll_id, blocks = am.create_poll(start_date=start_date, end_date=end_date, channel_id=target.id, message_id=0,
                                     start_time=s.strftime('%H:%M'), end_time=end_time,
                                     block_length=plan['block_hours'])
    if not blocks:
        am.polls.pop(poll_id, None)
        am.data.pop(poll_id, None)
        return '⚠️ Those times didn\'t make any poll blocks, so nothing was posted.'
    closes = int(time.time()) + plan['open_hours'] * 3600
    am.polls[poll_id]['poll_end_unix'] = closes
    am.polls[poll_id]['closed'] = False
    am.polls[poll_id]['style'] = 'menus'
    title = f'🕒 Availability Poll – {start_date}' + ('' if start_date == end_date else f' to {end_date}')
    embed = create_embed(
        title=title,
        description=(f'**Select ALL time blocks when you are available.**\n\n'
                     f'Times shown are your **local time** with TCT (Torn City Time = UTC) in parentheses.'
                     f'\n\n⏳ **Event starts:** <t:{blocks[0]["start_unix"]}:R> (<t:{blocks[0]["start_unix"]}:F>)'
                     f'\n📊 **Poll closes:** <t:{closes}:R> (<t:{closes}:f>)\n\n'
                     f'**Time Blocks:**\n' + '\n'.join(block_list_lines(blocks, use_buttons=False))),
        color=EMBED_COLOR_INFO)
    embed.set_footer(text=f'Block size: {plan["block_hours"]}h | Only {VERIFIED_ROLE_NAME} members can vote')
    msg = await target.send(embed=embed, view=AvailabilityDropdownView(am, poll_id, blocks, VERIFIED_ROLE_NAME))
    am.polls[poll_id]['message_id'] = msg.id
    await am.save_polls()
    return f'🗳️ Availability poll posted in {target.mention}. {msg.jump_url}'


# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions shown to the AI (only when the message asks to DO something)
# ─────────────────────────────────────────────────────────────────────────────

_ONLY_SAID = ' Leave out anything the user did not say; the bot asks for it.'
ACTION_SPECS = [
    {'type': 'function', 'function': {
        'name': 'action_target',
        'description': 'Add someone to, or remove them from, the target board. Shows a confirm card.' + _ONLY_SAID,
        'parameters': {'type': 'object', 'properties': {
            'op': {'type': 'string', 'enum': ['add', 'remove']},
            'who': {'type': 'string', 'description': 'Torn ID or profile link exactly as the user gave it'},
            'reason': {'type': 'string'},
            'permanent': {'type': 'boolean', 'description': 'Only if the user said permanent'}},
            'required': ['op']}}},
    {'type': 'function', 'function': {
        'name': 'action_chain_event',
        'description': 'Start, end or cancel a chain event (members must make a minimum of hits). '
                       'Shows a confirm card.' + _ONLY_SAID,
        'parameters': {'type': 'object', 'properties': {
            'op': {'type': 'string', 'enum': ['start', 'end', 'cancel']},
            'when': {'type': 'string', 'description': 'Start time exactly as the user said it, e.g. "now", '
                                                      '"saturday 13:00"'},
            'hours': {'type': 'integer'}, 'minimum': {'type': 'integer'}, 'target': {'type': 'integer'},
            'name': {'type': 'string'}},
            'required': ['op']}}},
    {'type': 'function', 'function': {
        'name': 'action_poll',
        'description': 'Post an availability poll: kind "war" for the next ranked war (times from Torn), or '
                       '"custom" with start and end as the user said them. Shows a confirm card.' + _ONLY_SAID,
        'parameters': {'type': 'object', 'properties': {
            'kind': {'type': 'string', 'enum': ['war', 'custom']},
            'start': {'type': 'string'}, 'end': {'type': 'string'},
            'block_hours': {'type': 'integer'}, 'open_hours': {'type': 'integer'}},
            'required': ['kind']}}},
    {'type': 'function', 'function': {
        'name': 'action_discord_event',
        'description': 'Create a Discord scheduled event. type war, chain or happy_jump use the standard '
                       'name and length; custom needs a name and hours. Shows a confirm card.' + _ONLY_SAID,
        'parameters': {'type': 'object', 'properties': {
            'type': {'type': 'string', 'enum': ['war', 'chain', 'happy_jump', 'custom']},
            'when': {'type': 'string', 'description': 'Exactly as the user said it, e.g. "saturday 8pm"'},
            'name': {'type': 'string'}, 'hours': {'type': 'integer'}, 'description': {'type': 'string'}},
            'required': ['type']}}},
]

ACTION_TOOL_NAMES = {s['function']['name'] for s in ACTION_SPECS}
