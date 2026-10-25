# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A Discord bot for a Torn.com faction (Ascended Punks), running on Pat's home Mac mini (since 25/10/2026). `discord.py` + `aiohttp`, no database: state is JSON files under `data/`.

## Commands

```bash
<py> -m pytest -q tests/          # full suite (113 tests), the one to trust
<py> -m pytest -q tests/test_imports.py::test_flytrack_logic   # single test
<py> tests/test_imports.py        # runs test_imports.py ONLY, see note below
<py> bot.py                       # run locally (needs .env)
```

**`<py>` is `py -3` on Windows and `python3` on macOS or Linux.** On the
Windows machine, plain `python` resolves to a broken Windows Store shim, so
never use it there. This repo gets worked on from both, so check which you
are on before assuming.

Testing gotchas, both of which have caused tests to silently not run:

- `tests/test_imports.py` has a `__main__` block that calls each test explicitly. A new test function there **must be both defined and added to that block**, or it never runs.
- `tests/test_regressions.py` has **no** `__main__` block, so it only executes under pytest. Running `test_imports.py` directly misses it entirely, which is why `pytest tests/` is the command to trust.
- `pytest` is installed but is **not** in `requirements.txt` (the bot itself doesn't need it).

Tests are deliberately dependency-free: no mocking library, just hand-rolled fake classes monkey-patched onto instances. Match that style.

### Deploying

Runs on Pat's home **Mac mini** in Docker Compose (`~/Projects/mac-server`, service `apexpunk`), **not Railway**.

- **Deploy:** push to `main`, then on the Mac run `cd ~/Projects/mac-server && bash scripts/update.sh`
  (or `git -C ~/Projects/ApexPunk pull && docker compose up -d --build apexpunk`). **Pushing alone deploys nothing.**
- **Settings/secrets:** `~/Projects/mac-server/env/apexpunk.env` (never committed). **Data:** `~/Projects/mac-server/data/apexpunk/`
  (mounted as `/app/data`), backed up nightly with the other apps.
- **Logs:** `docker compose logs -f apexpunk` (kept across restarts, unlike Railway's).
- **Live state:** `docker compose exec apexpunk cat data/<file>.json` is the way to see what the bot actually believes,
  far more reliable than reasoning about persisted state from the code.
- **Health:** `curl http://127.0.0.1:3108/health` on the Mac.
- **Railway** is a stopped cold backup: deployment removed and auto-deploy disabled (repo still linked so it can be
  redeployed by hand in an emergency). A bot must only run in ONE place, so never redeploy there while the Mac runs it.

## Architecture

### bot.py is an orchestrator, not logic

Each feature is a manager class owning its own state and JSON file. `bot.py` constructs all 11 in `on_ready()`, wires them together, then starts one `@tasks.loop` per feature. Feature modules hold the logic; `bot.py` holds the schedule.

Three managers are cross-wired by hand after construction, and the order matters because later ones reference earlier ones:

```python
verification_system.inactivity_tracker = inactivity_tracker  # shared user DB
inactivity_tracker.verification_system = verification_system # needs verified-role id
war_tracker.enemy_watchlist = enemy_watchlist                # auto-clear on war end
```

`FlyTracker` instead takes `war_tracker` as a constructor arg, it piggybacks on war-tracker's already-hardened war-end detection rather than re-deriving a weaker one.

### Background loops and their ordering trap

Every loop's `before_loop` only awaits `bot.wait_until_ready()`, so **there is no ordering guarantee between loops**. A loop reading another feature's state can see it mid-warm-up after a restart. This has caused real production bugs: flytrack once auto-disabled itself mid-war by reading `war_tracker.was_war_active` before war-tracker's first tick had run.

The established fix is a strike counter, require N consecutive confirmations before acting on another feature's state, never a single read.

`loop_watchdog` (5 min) restarts any loop that has stopped. A loop that is *executing* still counts as running, so slow work inside a loop body is safe.

`daily_inactivity_check` is the only 24h loop; it fires at **00:00 TCT (UTC)**. Everything else re-runs within 1–15 minutes, so a transient failure costs them one tick but costs the daily check a whole day, which is why it alone retries internally (`DAILY_FETCH_RETRY_DELAYS`).

`role_sync_check` (1h) does double duty: it fetches the roster once and feeds **both** role sync and the activity-profile snapshot, so adding the profile feature cost zero extra API calls.

### The None-vs-empty contract

The single most expensive bug class in this repo. A fetch helper returning `[]`/`{}` for *both* "the API failed" and "there genuinely is nothing" has twice caused the bot to confidently report nonsense, most recently flagging nearly the whole faction for missing OCs because an unreachable crimes endpoint read as "nobody is in a crime".

The convention now: **`None` means "could not fetch"; an empty container means "fetched, nothing there."** Callers must branch on `is None` and skip the affected section rather than degrade into a confident wrong answer. See `fetch_faction_crimes()` and `_get_oc_status()`.

A third shape of the same trap: a field that simply **disappears** from an API. Torn's v2 ranked war report dropped `defends`, so `stats.get('defends', 0)` turned a missing field into a confident "0 hits taken" for the entire faction after a heavy defeat. Per-member incoming now comes from the attacks log instead.

Audited the whole API surface against live payload shapes after that one. The distinction that matters: a **missing payload** is caught by the usual `if not data: return None` guard, but a **missing field inside a successful payload** is not, no guard fires, and the default silently becomes the answer. `get_chain_report` fails closed and is fine; `defends` was the only field that had actually vanished. Worst instance was in persistence, not display: war history was archiving 0 hits taken, which no later fix could recover once the attacks log aged out.

A related trap: an endpoint can return HTTP 200 and still be useless. Torn's v1 crimes endpoint keeps serving OC1 history frozen ~585 days ago for an OC2 faction, which would silently empty the "who is mid-crime" set. Staleness is checked explicitly before that fallback is trusted.

### The war attacks log

`_fetch_all_attacks_since` pages Torn's faction attacks log, and everything the war reports say about **hits taken, outside hits and friendly hosps** comes from it, the v2 war report supplies only attacks and score. At war end those numbers are **archived permanently**, so a partial read is not a cosmetic problem.

Two things it must not do, both of which it used to:

- **Stop early on an outage.** `fetch_faction_attacks` returned `{}` on failure and the paginator stops on an empty page, so one 504 mid-pagination produced a complete-looking, much quieter war. Pages now retry, and an unreadable log returns `None` all the way up. Reports label the gap; the archive stores `None` (unknown), never 0.
- **Leave the window open.** The query had a `from` but no `to`, so after a war ended every later hit on the former opponent still counted as a war hit, measured on war 50338, the log read 422 against the official 421. A finished war must pass `war_end`; an ongoing one passes `None`. Unbounded, `/war view:Xan` reimburses more Xanax the longer you leave it.
- **Skip the boundary second.** `from` is *inclusive*, so stepping to `latest_timestamp + 1` dropped every attack sharing that second with the page's last record, routine during a chain. It now re-reads the boundary and relies on attack-id dedup.

### Torn API notes (`utils/torn_api.py`)

- v1 and v2 are both in use; some selections exist only in v2 (`wars`, OC2 `crimes`). v2 is authoritative for crimes.
- Responses are cached ~30s by default; pass `cache_seconds=0` to bypass (flytrack needs fresh statuses).
- Failures are **not** cached, so an immediate retry genuinely re-hits the API.
- A rolling-window rate limiter caps requests/min.
- `selections=travel` returns error 7 for any ID but the key owner's, so exact flight times for enemies are **impossible**, this is why flight ETAs are estimated and must stay estimates.

### Flight ETA model (`features/flytrack.py`)

ETA = standard flight minutes × that player's **measured** speed ratio (their observed minutes ÷ standard), median of up to 5 samples. Unmeasured players fall back to `DEFAULT_RATIO`, the empirical median across real observed flights.

The travel "tiers" (airstrip/WLT/business) are **display labels only**, never use them for arithmetic. Rounding measurements into those buckets was itself a bug: it made every one of 82 stored flights predict late (avg 8 min), because each bucket runs ~6% slower than reality.

Landing times render as a window whose **early edge** drives the countdown. The error is asymmetric: an enemy landing sooner than advertised gets people hurt; landing later only costs a wait.

### Follow warnings

Messages stay **per member**, not per country, precisely because the bands are personal. Measured volume on a live war: ~2 enemy departures/hour faction-wide, about **one ping per abroad member every 5-10 hours**, 1.3 messages/hour channel-wide with 12 members abroad - so the cooldown almost never binds and exists for bursts.

Flytrack also pings one of our members when an enemy departs for the country they are *sitting in* (landed, not mid-flight). This is the only place the bot compares enemy state against **our own** roster, which it fetches lazily, only on ticks where someone actually departed, so quiet ticks cost nothing.

Two dials keep it off the noise cliff, and both matter: the faction rejected an unfiltered version of this idea as too spammy before it was built.

- A **per-member-per-country cooldown**. Enemies departing in the same tick are grouped into one message; across ticks the member is not re-told about a country they already know to watch.
- Enemies are **banded relative to the member being warned** (stronger / unknown / weaker), because "strong" has no absolute meaning: measured live, PUAD at ~817k was a real threat to Trizzy973 (516k) and irrelevant to TrevorLahey420 (7.5m) standing in the same country. Banding and the send decision are kept apart - a trivial enemy rides along in a message a real threat already triggered, but never causes one.
- A **stat threshold** (`FOLLOW_THREAT_RATIO`) via FFScouter. It fails **open**, no key, a failed lookup or a missing estimate all keep the warning, because a warning not sent gets someone hit while a surplus one costs one ping. FFScouter's own `fair_fight` is relative to the *key owner*, so it cannot be used for a per-member comparison; only estimate-vs-estimate works.

The cooldown is stamped only when a warning actually goes out, so an outage or a filtered-out weakling never silences the next real threat.

### State and persistence

`data/*.json` is gitignored and lives on a Railway volume (it survives redeploys). Files are written atomically via `utils/helpers.py`. There is no migration system, new fields must be read defensively with `.get(...)` and backfilled on write, because live records predate them.

Anything accumulating per-player needs an explicit retention policy; several files grow forever otherwise. Current ones: activity profiles prune after 7 days absent from the roster, chain history caps at 100.

## Conventions

- Comments explain **why**, especially the non-obvious constraint a line defends against. Much of this codebase's value is in knowing which failure a guard exists for.
- Slash commands use a single command with an `action`/`view` choice dropdown rather than many top-level commands (`/war`, `/avail`, `/verify`). Management-gated actions check `is_management(interaction)` per-action, not per-command.
- Channel IDs come from `config.py` env vars. Features post to their own channel; none is hardcoded.
- Restricting a command to certain channels is done in Discord's own server settings, not in code. Note that **Discord exempts Administrators from command permissions entirely**, so testing as an admin will not reflect what members see.
