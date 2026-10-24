"""
Chain events: a window of time, not a single chain.

The faction rule is "10 hits minimum on an organised chain", but a 5-day
Halloween event is not one chain - it is a dozen of them, at all hours, and a
member who was asleep for three of them has not broken any rule. So the unit
this tracks is the EVENT: a named window with a minimum, inside which every
chain counts toward one per-member total.

Two decisions shape the whole module:

1. Only chains count, and `chain > 0` is how we know. wars.py measured that
   over 2,827 real attacks: every Lost/Stalemate/Escape/Interrupted/Timeout
   record carried chain=0, so a positive chain position means the hit
   genuinely advanced a chain. Treat hits (not respect) as the measure,
   because the published rule is written in hits.

2. Nothing here strikes anybody. It produces a shortfall LIST, and a human
   clicks. The cost is asymmetric: wrongly marking a casual chain official
   strikes people who did nothing wrong, while missing one costs a week of
   enforcement that can be recovered by hand. So an event is official only
   because somebody said so, and silence never punishes.

The counting is deliberately re-derivable. Running totals are kept so the
normal path is cheap, but any total that is about to justify a strike is
recomputed from the attacks log first - see `tally(full=True)`. State that
can drift must never be the thing that accuses somebody.
"""

import json
import logging
import os
import time

import discord

from utils.helpers import save_json

logger = logging.getLogger(__name__)

# A duration typo should not create a two-month event that quietly collects
# strikes. 14 days is longer than any real Torn event.
MAX_EVENT_DAYS = 14
# Matches the published faction rule; overridable per event.
DEFAULT_MINIMUM = 10
# How far back an event may be backdated at creation. Chains get called before
# anybody remembers to tell the bot, so starting "3 hours ago" is normal; a
# week ago is a typo.
MAX_BACKDATE_HOURS = 48
# And how far ahead one may be scheduled. Torn's events are announced days
# out, not months, so anything past this is a mistyped year.
MAX_SCHEDULE_DAYS = 14
# Torn lets each player choose when their own event begins, anywhere in this
# range. A faction-wide window has to open at the EARLIEST of them, or a
# member who picked 10:00 loses six hours of real chain hits off their total
# purely because the leader picked 16:00 for themselves.
EVENT_EARLIEST_TCT_HOUR = 10


# ---------------------------------------------------------------- pure logic
# Everything below is pure so it can be tested without Discord or the Torn API.

def chain_hits_by_member(attacks, our_faction_id):
    """
    {torn_id: hits} for attacks that advanced a chain.

    Returns None when `attacks` is None, preserving the repo's None-vs-empty
    contract: an unreadable log must not read as "nobody hit anything", which
    would put the entire faction on a shortfall list.
    """
    if attacks is None:
        return None
    out = {}
    for _attack_id, a in attacks.items():
        if not isinstance(a, dict):
            continue
        # Our side only. The log contains incoming attacks too, and counting
        # those would credit the enemy's hits to nobody and inflate nothing -
        # but it would also let a defender's id leak into the totals.
        if str(a.get('attacker_faction') or 0) != str(our_faction_id):
            continue
        # chain position 0 = did not advance a chain (a loss, an escape, or a
        # hit landed outside any chain). Those do not count toward a chain
        # minimum, by definition of the rule.
        if (a.get('chain') or 0) <= 0:
            continue
        attacker = str(a.get('attacker_id') or '')
        if not attacker or attacker == '0':
            continue
        out[attacker] = out.get(attacker, 0) + 1
    return out


def longest_in(attacks, our_faction_id):
    """
    The highest chain position reached in this slice of the log, or None when
    the log could not be read.

    "Reach a chain of 500" is about ONE chain getting that long, which is a
    different thing from the faction's total hits over a week. Every attack
    carries its position in the chain it belonged to, so the longest chain is
    just the biggest of those, and it costs no extra API call.

    It is always <= the total hits counted, because every hit in a chain is
    also one of those hits.
    """
    if attacks is None:
        return None
    best = 0
    for _attack_id, a in attacks.items():
        if not isinstance(a, dict):
            continue
        if str(a.get('attacker_faction') or 0) != str(our_faction_id):
            continue
        best = max(best, int(a.get('chain') or 0))
    return best


def merge_totals(base, addition):
    """
    Add a newly counted slice onto the running totals.

    None addition leaves the base untouched and reports failure, so a failed
    top-up never advances the cursor past data we did not actually read.
    """
    if addition is None:
        return dict(base or {}), False
    out = dict(base or {})
    for tid, hits in addition.items():
        out[tid] = out.get(tid, 0) + hits
    return out, True


def shortfall(totals, roster, minimum):
    """
    Who finished under the minimum.

    totals: {torn_id: hits} or None (unreadable)
    roster: {torn_id: name} - the members the rule applies to
    Returns a list of {'torn_id', 'name', 'hits'} sorted by hits ascending,
    or None when the totals could not be read.

    Members with zero chain hits are INCLUDED: "did not turn up at all" is the
    case the rule exists for, and a member missing from the attacks log is
    exactly that.
    """
    if totals is None:
        return None
    out = []
    for tid, name in (roster or {}).items():
        hits = totals.get(str(tid), 0)
        if hits < minimum:
            out.append({'torn_id': str(tid), 'name': name, 'hits': hits})
    out.sort(key=lambda m: (m['hits'], m['name'].lower()))
    return out


def met_minimum(totals, roster, minimum):
    """Count of members at or over the minimum; None when unreadable."""
    if totals is None:
        return None
    return sum(1 for tid in (roster or {})
               if totals.get(str(tid), 0) >= minimum)


def day_of(event, now=None):
    """
    (day, total_days) for display, 1-indexed and clamped.

    Day 1 starts the moment the event does, so a 5-day event reads "Day 1 of 5"
    in its first hour rather than "Day 0".
    """
    now = now or time.time()
    span = max(1, int(event['end']) - int(event['start']))
    total_days = max(1, int(round(span / 86400)))
    elapsed = max(0, now - int(event['start']))
    day = int(elapsed // 86400) + 1
    return min(day, total_days), total_days


def clamp_window(start_ts, hours, now=None):
    """
    Turn a requested start and duration into a sane window.

    Returns (start, end, notes) where notes explains anything that was
    adjusted, so the person running the command is told rather than silently
    given something other than what they typed.
    """
    now = now or time.time()
    notes = []
    start = int(start_ts)
    earliest = now - MAX_BACKDATE_HOURS * 3600
    latest = now + MAX_SCHEDULE_DAYS * 86400
    if start < earliest:
        start = int(earliest)
        notes.append(f'start pulled forward to {MAX_BACKDATE_HOURS}h ago '
                     f'(backdating further is almost always a typo)')
    elif start > latest:
        # Almost always a mistyped year or month. Scheduling something for
        # next March would sit there silently and never fire.
        start = int(latest)
        notes.append(f'start pulled back to {MAX_SCHEDULE_DAYS} days ahead '
                     f'(that date looked like a typo)')
    hours = max(1, int(hours))
    if hours > MAX_EVENT_DAYS * 24:
        hours = MAX_EVENT_DAYS * 24
        notes.append(f'duration capped at {MAX_EVENT_DAYS} days')
    return start, start + hours * 3600, notes


# ---------------------------------------------------------------- manager

class ChainEventManager:
    """
    Owns the event state. One running event at a time, on purpose: two
    overlapping events with different minimums is a question nobody can answer
    about a single hit.
    """

    def __init__(self, bot, war_tracker, faction_id):
        self.bot = bot
        # Only for its attacks paginator, which is hardened against three real
        # bugs (stopping early on an outage, leaving the window open, and
        # skipping the boundary second). FlyTracker piggybacks war_tracker the
        # same way rather than re-deriving weaker logic.
        self.war_tracker = war_tracker
        self.faction_id = str(faction_id)
        self.events_file = 'data/chain_events.json'
        self.events = {}
        self.load()

    # ------------------------------------------------------------- storage

    def load(self):
        try:
            if os.path.exists(self.events_file):
                with open(self.events_file, 'r', encoding='utf-8') as f:
                    self.events = json.load(f) or {}
                running = [e for e in self.events.values()
                           if e.get('status') == 'running']
                logger.info(f'Chain events: {len(self.events)} on record, '
                            f'{len(running)} running')
        except Exception as e:
            logger.error(f'Chain events: could not load state: {e}')
            self.events = {}

    def save(self):
        try:
            save_json(self.events_file, self.events)
        except Exception as e:
            logger.error(f'Chain events: could not save state: {e}')

    # ------------------------------------------------------------- lifecycle

    def active(self, now=None):
        """The running event, or None. Does not auto-close a finished one."""
        now = now or time.time()
        for ev in self.events.values():
            if ev.get('status') == 'running':
                return ev
        return None

    def pending(self):
        """An event scheduled but not yet begun, or None."""
        for ev in self.events.values():
            if ev.get('status') == 'scheduled':
                return ev
        return None

    def due(self, now=None):
        """
        Scheduled events whose start time has arrived.

        A missed start is NOT skipped: counting is derived from the window,
        so an event that should have begun while the bot was down still
        begins, and still counts from its scheduled time rather than from
        whenever the bot woke up.
        """
        now = now or time.time()
        return [ev for ev in self.events.values()
                if ev.get('status') == 'scheduled' and now >= int(ev['start'])]

    def begin(self, event):
        """Flip a scheduled event to running."""
        event['status'] = 'running'
        self.save()
        logger.info(f'Chain event now running: {event["name"]}')

    def start(self, name, start_ts, hours, minimum=DEFAULT_MINIMUM,
              target=0, now=None):
        """
        Begin an event. Returns (event, notes) or (None, [reason]).

        Refuses while another is running rather than silently making the new
        one authoritative - the running one may already have totals that
        somebody is about to be judged on.
        """
        now = now or time.time()
        existing = self.active(now=now) or self.pending()
        if existing:
            word = ('still running' if existing.get('status') == 'running'
                    else 'already scheduled')
            return None, [f'"{existing["name"]}" is {word}. End or cancel it '
                          f'first.']
        start, end, notes = clamp_window(start_ts, hours, now=now)
        ev = {
            'id': str(int(start)),
            'name': name.strip() or 'Chain event',
            'start': start,
            'end': end,
            'minimum': max(1, int(minimum)),
            'target': max(0, int(target)),
            # A start in the future waits; the minute loop begins it. Counting
            # is derived from the window either way, so a scheduled event that
            # the bot sleeps through still counts from its own start time.
            'status': 'scheduled' if start > now else 'running',
            'totals': {},
            # Highest chain position reached so far during the window.
            'longest': 0,
            # Nothing counted yet, so the cursor sits at the window's start.
            'cursor': start,
            'created': int(now),
        }
        self.events[ev['id']] = ev
        self.save()
        logger.info(f'Chain event started: {ev["name"]} '
                    f'({(end - start) / 3600:.0f}h, min {ev["minimum"]})')
        return ev, notes

    def cancel(self, event):
        """Stop an event without producing any shortfall list."""
        event['status'] = 'cancelled'
        self.save()
        logger.info(f'Chain event cancelled: {event["name"]}')

    def finish(self, event):
        event['status'] = 'ended'
        self.save()
        logger.info(f'Chain event ended: {event["name"]}')

    # ------------------------------------------------------------- counting

    async def tally(self, event, full=False, now=None):
        """
        Bring an event's totals up to date.

        full=True recounts the whole window from scratch and ignores the
        running totals. Any number that is about to justify a strike is
        produced this way, because accumulated state can drift and a drifted
        total accuses a real person.

        Returns (totals, ok). ok=False means the log could not be read, and
        callers must say so rather than present the stale totals as current.
        """
        now = now or time.time()
        # Never count past the window. Without a closing bound, every hit
        # after the event keeps counting - the same bug that made a finished
        # war read 422 hits against the official 421.
        until = min(int(event['end']), int(now))

        if full:
            attacks = await self.war_tracker._fetch_all_attacks_since(
                int(event['start']), until)
            counted = chain_hits_by_member(attacks, self.faction_id)
            if counted is None:
                logger.error(f'Chain event "{event["name"]}": full recount '
                             f'failed, attacks log unreadable')
                return event.get('totals', {}), False
            event['totals'] = counted
            # Recomputed, not merged: a full recount exists so nothing rests
            # on accumulated state, and a stale high-water mark would survive
            # a correction that should have lowered it.
            event['longest'] = longest_in(attacks, self.faction_id) or 0
            event['cursor'] = until
            self.save()
            return counted, True

        cursor = int(event.get('cursor', event['start']))
        if until <= cursor:
            # Nothing new to read; the stored totals are already current.
            return event.get('totals', {}), True

        attacks = await self.war_tracker._fetch_all_attacks_since(cursor, until)
        counted = chain_hits_by_member(attacks, self.faction_id)
        merged, ok = merge_totals(event.get('totals', {}), counted)
        if not ok:
            logger.warning(f'Chain event "{event["name"]}": top-up failed, '
                           f'cursor left at {cursor}')
            return event.get('totals', {}), False
        event['totals'] = merged
        slice_best = longest_in(attacks, self.faction_id)
        if slice_best is not None:
            event['longest'] = max(int(event.get('longest', 0)), slice_best)
        event['cursor'] = until
        self.save()
        return merged, True

    async def roster_for(self, event):
        """
        {torn_id: {'name', 'days_in_faction'}} for the current roster.

        Current roster deliberately: somebody who left mid-event is no longer
        ours to strike. Join dates ride along so late joiners can be excused
        from the minimum without being hidden from the leaderboard.
        """
        from utils.torn_api import fetch_faction_members
        members = await fetch_faction_members()
        if not members:
            return None
        return {str(tid): {'name': m.get('name', f'ID:{tid}'),
                           'days_in_faction': m.get('days_in_faction', 9999)}
                for tid, m in members.items()}


# ---------------------------------------------------------------- display

# What a chain event usually is, so nobody has to type a date. Hours, because
# "5 days" means five days of chaining, not a calendar boundary.
PRESETS = [
    ('Tonight - 6 hours', 6),
    ('24 hours', 24),
    ('This weekend - 48 hours', 48),
    ('5 days', 120),
    ('A week - 7 days', 168),
]


def faction_lines(event, totals):
    """
    The two faction numbers, which answer different questions.

    "Reach 500" means ONE chain getting that long, so the target hangs off
    the longest chain. The total is every member's chain hits added up across
    the window, and is always the bigger of the two, because every hit inside
    a chain is also one of those hits.
    """
    hits = sum((totals or {}).values())
    best = int(event.get('longest', 0) or 0)
    out = []
    if event.get('target'):
        out.append(f"⛓️ **Longest chain: {best}** of "
                   f"**{event['target']}** target")
    elif best:
        out.append(f"⛓️ **Longest chain: {best}**")
    out.append(f"📊 **Total chain hits: {hits}** "
               f"(everyone added up)")
    return out


def progress_embed(event, totals, roster, ok, now=None):
    """
    Where the event stands. Used mid-event and at the end.

    ok=False means the attacks log could not be read, and the embed says so
    instead of presenting stale totals as current - the whole point of this
    feature is deciding whether somebody turned up, and a confident wrong
    answer there costs somebody a strike.
    """
    from utils.helpers import create_embed
    from config import EMBED_COLOR_INFO, EMBED_COLOR_WARNING

    day, days = day_of(event, now=now)
    minimum = event['minimum']
    # The minimum applies only to members who were here when it started;
    # the leaderboard still shows everybody who hit.
    judged = eligible(roster, event, now=now) if roster else roster
    excused = (len(names_of(roster)) - len(judged)) if roster else 0
    short = shortfall(totals, judged, minimum)
    met = met_minimum(totals, judged, minimum)
    hits = sum((totals or {}).values())

    lines = [f"**Day {day} of {days}** · minimum **{minimum}** hits each"]
    lines.extend(faction_lines(event, totals))

    if not ok:
        lines.append('\n⚠️ **The attacks log could not be read.** '
                     'These numbers are the last good count, not the current '
                     'one. Do not act on them.')
    elif short is None or roster is None:
        lines.append('\n⚠️ **The roster could not be read**, so who '
                     'is short cannot be worked out right now.')
    else:
        # Two blocks rather than a tick column. A minimum of 10 is low, so
        # the people who cleared it are not the story and should not be
        # cluttered with marks against their names. The story is who has not.
        ranked = leaderboard(totals, roster, limit=0)
        short_ids = {m['torn_id'] for m in short}
        cleared = [r for r in ranked if str(r['torn_id']) not in short_ids]
        width = max([len(r['name']) for r in ranked]
                    + [len(m['name']) for m in short] + [4])

        if cleared:
            rows = '\n'.join(f"{i:>2}  {r['name']:<{width}}  {r['hits']:>4}"
                             for i, r in enumerate(cleared, start=1))
            lines.append(f'\n```\nCLEARED {minimum}+\n'
                         f'{"#":>2}  {"Name":<{width}}  Hits\n{rows}\n```')
        if short:
            rows = '\n'.join(f"    {m['name']:<{width}}  {m['hits']:>4}"
                             for m in short)
            lines.append(f'```\nUNDER {minimum}\n'
                         f'    {"Name":<{width}}  Hits\n{rows}\n```')
            lines.append(f'⚠️ **{len(short)}** under the minimum · '
                         f'✅ {met} cleared it')
        else:
            lines.append(f'✅ **Everyone has cleared {minimum}.**')
        if excused:
            lines.append(
                f'-# {excused} member{"s" if excused != 1 else ""} joined '
                f'after this event started and {"are" if excused != 1 else "is"} '
                f'not judged against the minimum.')

    return create_embed(
        title=f"⛓️ {event['name']}",
        description='\n'.join(lines),
        color=EMBED_COLOR_INFO if ok else EMBED_COLOR_WARNING)


# ---------------------------------------------------------------- views

class PresetSelect(discord.ui.Select):
    """
    Start an event without typing a date.

    Chains get called when they get called, so every preset starts NOW and
    runs forward. Backdating is possible through the command's own `hours`
    parameter for the rare case where the chain began before anybody told
    the bot.
    """

    def __init__(self, manager, name, minimum, target):
        self.manager = manager
        self._name = name
        self._minimum = minimum
        self._target = target
        super().__init__(
            placeholder='How long should it run?',
            options=[discord.SelectOption(label=label, value=str(hours))
                     for label, hours in PRESETS])

    async def callback(self, interaction):
        hours = int(self.values[0])
        ev, notes = self.manager.start(
            self._name, time.time(), hours,
            minimum=self._minimum, target=self._target)
        if not ev:
            await interaction.response.edit_message(
                content='⚠️ ' + ' '.join(notes), view=None)
            return
        msg = (f"⛓️ **{ev['name']}** started - running "
               f"**{hours}h**, minimum **{ev['minimum']}** hits each.\n"
               f"Check it any time: type `/chain` and pick **Event**.")
        if notes:
            msg += '\n-# ' + ' · '.join(notes)
        await interaction.response.edit_message(content=msg, view=None)


class PresetView(discord.ui.View):
    def __init__(self, manager, name, minimum, target):
        super().__init__(timeout=300)
        self.add_item(PresetSelect(manager, name, minimum, target))


class ShortfallView(discord.ui.View):
    """
    The end-of-event decision. Nothing is struck until somebody clicks.

    Not a persistent view: if the bot restarts these buttons go dead. That is
    survivable because the event keeps its final totals, so running
    `/chain view:Event end` again re-posts the same list - which is also why
    the strike call passes the event id as `context`, so a second run cannot
    file anybody twice.
    """

    def __init__(self, manager, event, short):
        super().__init__(timeout=86400)
        self.manager = manager
        self.event = event
        self.short = short

    @discord.ui.button(label='Raise strikes', style=discord.ButtonStyle.danger)
    async def raise_strikes(self, interaction, button):
        from utils.helpers import is_management
        if not is_management(interaction):
            await interaction.response.send_message(
                'Management only.', ephemeral=True)
            return
        await interaction.response.defer()
        sm = getattr(self.manager, 'strike_manager', None)
        if not sm:
            await interaction.followup.send(
                'No strike ledger is wired up, so nothing was filed.')
            return
        filed = 0
        for m in self.short:
            ok = await sm.post_candidate(
                m['torn_id'], m['name'], 'chain',
                f"{m['hits']} of {self.event['minimum']} hits during "
                f"{self.event['name']}",
                context=str(self.event['id']))
            if ok:
                filed += 1
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(
            f'⚖️ Filed **{filed}** strike candidate(s) in the '
            f'strikes channel. Each still needs its own confirmation there.')

    @discord.ui.button(label='Dismiss all', style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction, button):
        from utils.helpers import is_management
        if not is_management(interaction):
            await interaction.response.send_message(
                'Management only.', ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            'Dismissed - no strikes filed for this event.')


# ---------------------------------------------------------------- scheduling

def parse_tct(text, now=None):
    """
    "DD-MM-YYYY HH:MM" in TCT (which is UTC) -> epoch seconds.

    Returns (timestamp, None) or (None, reason). Deliberately strict about
    the format rather than guessing: a misread date here schedules an event
    for the wrong day, and nobody notices until the hits do not count.
    """
    from datetime import datetime, timezone
    raw = (text or '').strip()
    if not raw:
        return None, 'no start time given'
    for fmt in ('%d-%m-%Y %H:%M', '%d-%m-%y %H:%M'):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp()), None
        except ValueError:
            continue
    return None, (f'could not read "{raw}". Use DD-MM-YYYY HH:MM in TCT, '
                  f'for example 25-10-2026 16:00')


def announce_text(event):
    """
    What the chain channel sees when counting begins.

    The window explanation is spelled out because members choose their own
    Halloween start time anywhere from 10:00 to 16:00 TCT, and somebody who
    picked a different hour from the leader will otherwise assume their early
    hits were not counted - or worse, assume they were when they were not.
    """
    from datetime import datetime, timezone
    start = datetime.fromtimestamp(int(event['start']), timezone.utc)
    end = datetime.fromtimestamp(int(event['end']), timezone.utc)
    days = max(1, int(round((int(event['end']) - int(event['start'])) / 86400)))
    target = (f"\n\U0001f3af **Target:** {event['target']} chain hits "
              f"as a faction" if event.get('target') else '')
    mn = event['minimum']
    goal = (f"\U0001f3af **Faction target: {event['target']} chain hits**\n"
            if event.get('target') else '')
    return (
        f"⛓️ **{event['name']} is live**\n\n"
        f"⏱️ **Counting:** `{start:%d %b %H:%M}` to "
        f"`{end:%d %b %H:%M} TCT` ({days} days)\n"
        f"{goal}"
        f"⚠️ **Minimum: {mn} chain hits each. That is the floor, not the "
        f"goal.**\n\n"
        f"__**How it works**__\n"
        f"• Only hits you make **during a chain** count\n"
        f"• Every chain in that window adds to the **same total**\n"
        f"• {mn} is the point where you stop being a problem. It is not the "
        f"point where you stop hitting.\n"
        f"• **Every hit past {mn} is another treat roll** and pushes us "
        f"toward the target. Stopping at {mn} costs you treats, not us.\n\n"
        + (
            f"__**Everyone's event starts at a different time**__\n"
            f"Torn lets each player choose when their own Halloween begins, "
            f"anywhere from `{EVENT_EARLIEST_TCT_HOUR:02d}:00` to `16:00 "
            f"TCT`. **Check yours on the Torn calendar.**\n"
            f"Counting here opens at `{start:%H:%M} TCT`, before the earliest "
            f"anyone's can, so every chain hit counts whichever time you "
            f"picked. Just make sure your own event has actually started "
            f"before you expect treats to drop, and that you are wearing "
            f"something scary.\n\n"
            if start.hour <= EVENT_EARLIEST_TCT_HOUR else
            # Promising "every hit counts" on a window that opens late is the
            # one sentence a member would rely on and be struck by.
            f"⚠️ __**Hits before `{start:%H:%M} TCT` do not count**__\n"
            f"Torn lets each player choose when their own Halloween begins, "
            f"anywhere from `{EVENT_EARLIEST_TCT_HOUR:02d}:00` to `16:00 "
            f"TCT`. **Check yours on the Torn calendar.** If yours began "
            f"before `{start:%H:%M}`, those earlier hits are **not** in your "
            f"total.\n\n"
        )
        + f"__**Check your own total**__\n"
          f"Type `/chain` and pick **Event** from the list. It shows everyone's "
          f"hits so far and who is still short."
    )


# ------------------------------------------------------------ roster shaping

def names_of(roster):
    """{torn_id: name}, accepting either roster shape."""
    if not roster:
        return {}
    out = {}
    for tid, v in roster.items():
        out[str(tid)] = v.get('name', str(tid)) if isinstance(v, dict) else v
    return out


def eligible(roster, event, now=None):
    """
    Who the minimum actually applies to.

    Somebody who joined the faction after the event started is left out: they
    were not here for most of the window, and striking a new member for hits
    they had no chance to make is the fastest way to lose them. They still
    appear on the leaderboard, because excusing somebody is not the same as
    pretending they did nothing.

    Missing join data counts as present. A strike should never rest on a
    field that quietly disappeared from an API, which is how a missing
    `defends` once reported a whole faction as taking zero hits.
    """
    if not roster:
        return {}
    now = now or time.time()
    started = int(event['start'])
    out = {}
    for tid, v in roster.items():
        if not isinstance(v, dict):
            out[str(tid)] = v
            continue
        days = v.get('days_in_faction')
        joined = now - (days or 0) * 86400 if days is not None else 0
        if days is None or joined <= started:
            out[str(tid)] = v.get('name', str(tid))
    return out


def leaderboard(totals, roster, limit=5):
    """Highest first. Members with no hits are not ranked."""
    names = names_of(roster)
    rows = [{'torn_id': t, 'name': names.get(str(t), str(t)), 'hits': h}
            for t, h in (totals or {}).items() if h > 0 and str(t) in names]
    rows.sort(key=lambda r: (-r['hits'], r['name'].lower()))
    return rows[:limit] if limit else rows


def rank_of(torn_id, totals, roster):
    """
    (rank, ranked_count, hits) for one member, or (None, n, 0) if they have
    no hits yet. Ranking only among people who actually hit keeps "rank 26 of
    26" off the screen of somebody who simply has not started.
    """
    rows = leaderboard(totals, roster, limit=0)
    for i, r in enumerate(rows, start=1):
        if str(r['torn_id']) == str(torn_id):
            return i, len(rows), r['hits']
    return None, len(rows), 0


def member_embed(event, totals, roster, ok, torn_id, now=None):
    """
    What a member sees: the top 5, where they sit, and a reason to keep going.

    Deliberately does NOT name who is short. That list is management's, and
    putting it in front of the whole faction turns a nudge into a pillory.
    """
    from utils.helpers import create_embed
    from config import EMBED_COLOR_INFO, EMBED_COLOR_WARNING

    day, days = day_of(event, now=now)
    mn = event['minimum']
    hits_total = sum((totals or {}).values())
    lines = [f'**Day {day} of {days}**']
    lines.extend(faction_lines(event, totals))

    if not ok:
        lines.append('\n⚠️ The attacks log could not be read, so '
                     'these numbers are not current.')
        return create_embed(title=f"⛓️ {event['name']}",
                            description='\n'.join(lines),
                            color=EMBED_COLOR_WARNING)

    top = leaderboard(totals, roster, limit=5)
    if top:
        medal = {1: '\U0001f947', 2: '\U0001f948', 3: '\U0001f949'}
        lines.append('\n\U0001f525 **Top hitters**')
        lines.append('\n'.join(
            f"{medal.get(i, f'`{i}.`')} **{r['name']}** {r['hits']}"
            for i, r in enumerate(top, start=1)))

    rank, ranked, mine = rank_of(torn_id, totals, roster)
    if torn_id is None:
        lines.append('\n-# Register with `/register` to see your own total '
                     'here.')
        return create_embed(title=f"⛓️ {event['name']}",
                            description='\n'.join(lines),
                            color=EMBED_COLOR_INFO)

    lines.append('')
    if rank:
        lines.append(f'\U0001f464 **You: {mine} hits** · rank '
                     f'**{rank}** of {ranked}')
    else:
        lines.append(f'\U0001f464 **You: 0 hits** · not on the board yet')

    if mine < mn:
        lines.append(f'⚠️ **{mn - mine} more** to clear the minimum.')
    else:
        lines.append('✅ Minimum cleared.')
    # The whole reason the minimum must not read as a finish line: during
    # Halloween every chain hit is also a treat roll, so stopping costs the
    # member before it costs the faction.
    # The NEAREST person above, not the leader. The leaderboard is sorted
    # highest first, so taking the first row above them names whoever is
    # winning, which is discouraging rather than motivating when you are
    # fourth and they are forty hits away.
    above = [r for r in leaderboard(totals, roster, limit=0)
             if r['hits'] > mine]
    chase = ''
    if above and rank and rank > 1:
        nearest = min(above, key=lambda r: r['hits'])
        # One more hit than the person above, because matching their total
        # does not overtake them.
        need = nearest['hits'] - mine + 1
        chase = (f" **{need} more hit{'s' if need != 1 else ''}** to reach "
                 f"rank {rank - 1}.")
    lines.append(f'-# Every hit from here is another treat roll.{chase}')

    return create_embed(title=f"⛓️ {event['name']}",
                        description='\n'.join(lines),
                        color=EMBED_COLOR_INFO)
