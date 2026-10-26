# Apexpunk: a Discord bot for a Torn faction

A Discord bot that helps run my 35-member faction in the online game [Torn](https://www.torn.com): war and chain updates, enemy flight tracking, availability polls, and a daily check on who has gone inactive. It is used by **27 members** who linked their Discord to the game, and it has tracked **13 wars**.

**Live overview with screenshots:** [jirapat.dev/#apexpunk](https://jirapat.dev/#apexpunk)

<p>
  <img src="https://jirapat.dev/shots/apexpunk-1-war.webp" width="260" alt="Chain timer and enemy flight landing windows in the war room">
  <img src="https://jirapat.dev/shots/apexpunk-2-inactivity.webp" width="260" alt="The daily check flagging inactive members">
</p>

<sub>Screenshots are the bot's real message code run on made-up players.</sub>

## Interesting problems solved

- **Jobs that can't silently die.** Ten background jobs run 24/7 (wars, chains, inactivity, flights, polls...). A watchdog checks every 5 minutes and restarts any that stopped, and a health endpoint only reports healthy when Discord is connected and every job is running.
- **"API failed" is not "nothing happened".** Returning an empty list for both once made the bot flag nearly the whole faction for missing crimes. Fetch helpers now return `None` for a failure, and reports skip that section instead of guessing.
- **Complete war reports.** Hits taken come from Torn's paginated attack log. Pages retry on errors, so one failed page can't make a war look quieter than it was, and a finished war's query window is closed so later hits don't leak in.
- **Predicting enemy landing times.** Torn won't reveal other players' flight times, so the bot measures each player's real travel speed (median of their past flights) and shows a landing window, with the early edge first because that's the dangerous one.
- **Staying inside API limits** with a rolling-window rate limiter and short response caching.

## Tech

Python · discord.py · aiohttp · Torn API (v1 + v2) · FFScouter API · JSON-file storage · Docker

## How it was built

Built with [Claude Code](https://claude.com/claude-code) (an AI coding assistant). I decided the features, directed the design and tested every change; Claude wrote most of the code. **118 automated tests** (`python -m pytest -q`).

## What it does

**Automatic (background jobs):**

| Job | Interval | Purpose |
|-----|----------|---------|
| `daily_inactivity_check` | 24h | Inactivity + OC-participation report; auto-verify members (exact matches applied, fuzzy ones flagged for review); left-faction check |
| `war_update_check` | 1m | Detect war start/end, post reports; also flags declared-but-unstarted wars |
| `chain_update_check` | 1m | Live chain tracking |
| `event_reminder_check` | 1h | 35h reminders for Discord events + Torn calendar events; surfaces new in-game faction newsletters |
| `enemy_status_check` | 15m | Enemy watchlist status (during war) |
| `stakeout_check` | 15m | Enemy behavior/sleep-pattern tracking |
| `role_sync_check` | 1h | Sync Discord roles to Torn faction positions; also records an activity-profile snapshot (when/how often each member is typically online) from the same faction fetch |
| `fly_check` | 60s | Enemy flight tracking during war (while toggled on) |
| `poll_end_check` | 1m | Auto-close availability polls and post results |
| `loop_watchdog` | 5m | Restart any loop that stopped |

**Commands** (management-only marked ⚙):

- `/register`, `/unregister`, `/whois` — link Discord ↔ Torn
- `/verify` ⚙ — verification settings, bulksync, fixrole, manual verify, stats
- `/myactivity`, `/status`, `/ping`, `/version`, `/help`
- `/chain` — chain status / report / board
- `/war` — status / board / xan calc / history / trends / fly (enemy flight tracking with landing ETAs)
- `/avail` — availability polls (setup ⚙ / summary / mine / list / delete ⚙), plus coverage ⚙ (best time to schedule + quiet hours) and activity ⚙ (who's typically online when, roster-wide or per member); `/tct_now` — current Torn time
- `/enemy` ⚙ — war enemy watchlist
- `/stakeout` ⚙ — enemy behavior tracking
- `/announce` ⚙, `/createevent` ⚙ — announcements + Discord scheduled events
- `/test` ⚙ — manually trigger a background feature check

## Setup

### 1. Discord application
1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application → Bot tab.
2. Enable **Presence**, **Server Members**, and **Message Content** intents.
3. Reset token, copy it → this is `DISCORD_TOKEN`.
4. OAuth2 → URL Generator → scopes `bot` + `applications.commands`; permissions:
   View Channels, Send Messages, Manage Messages, Embed Links, Read Message History,
   Manage Roles, Manage Nicknames, Manage Events. Invite the bot with that URL.
5. Put the bot's role **above** the roles it manages, so role/nick edits work.

### 2. Torn API key
Faction API key with faction access → `TORN_API_KEY`. Optional personal key for
user lookups → `TORN_PERSONAL_API_KEY` (falls back to the faction key if unset).

### 3. Configure
Copy `.env.example` to `.env` and fill it in (`.env` is gitignored). See
**Environment variables** below.

### 4. Run
```bash
pip install -r requirements.txt
python bot.py
```

## Deployment

Runs in Docker (the repo ships a `Dockerfile`) on a self-hosted home server, started with
`python bot.py`. Settings come from environment variables (below). With `PORT` set, a small
health endpoint reports `healthy` only when Discord is connected **and** every background
loop is running.

## Environment variables

**Required:** `DISCORD_TOKEN`, `GUILD_ID`, `TORN_API_KEY`, `TORN_FACTION_ID`,
`MANAGEMENT_ROLE_IDS` (comma-separated), `MANAGEMENT_PING_ROLE_ID`,
`VERIFIED_ROLE_NAME`.

**Channels** (IDs): `INACTIVITY_ALERTS_CHANNEL_ID`, `ANNOUNCEMENTS_CHANNEL_ID`,
`WAR_CHANNEL_ID`, `WAR_MANAGEMENT_CHANNEL_ID`, `CHAINING_CHANNEL_ID`,
`HAPPY_JUMP_CHANNEL_ID`, `GENERAL_CHANNEL_ID`, `EVENT_POSTING_CHANNEL_ID`,
`WELCOME_CHANNEL_ID`, `RULES_CHANNEL_ID`,
`WAR_TRACKER_CHANNEL_ID` (enemy flight alerts).

**Optional / tunables:** `TORN_PERSONAL_API_KEY`, `FFSCOUTER_API_KEY`,
`COMMAND_PREFIX`, `INACTIVITY_WARNING_DAYS` (3), `INACTIVITY_CRITICAL_DAYS` (5),
`OC_WARNING_DAYS` (2), `OC_CRITICAL_DAYS` (4), `OC_NEW_MEMBER_GRACE_DAYS` (3),
`LEFT_FACTION_ALERT_DAYS` (7), `TORN_API_CACHE_SECONDS` (30),
`TORN_API_MAX_REQUESTS_PER_MINUTE` (60).

## State

Runtime state is JSON files under `data/` (gitignored). They're created on
demand — nothing to seed.

## Tests

```bash
python -m pytest -q
```
