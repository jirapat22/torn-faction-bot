# Apexpunk — Torn Faction Discord Bot

A Discord bot for running a Torn.com faction: member accountability
(inactivity + organized-crime participation), war tracking, chain tracking,
verification/role sync, and event reminders. Built on `discord.py`, deployed
on Railway.

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
| `alliance_war_check` | 5m | Ally-faction war alerts |
| `role_sync_check` | 1h | Sync Discord roles to Torn faction positions |
| `loop_watchdog` | 5m | Restart any loop that stopped |

**Commands** (management-only marked ⚙):

- `/register`, `/unregister`, `/whois` — link Discord ↔ Torn
- `/verify` ⚙ — verification settings, bulksync, fixrole, manual verify, stats
- `/myactivity`, `/status`, `/ping`, `/version`, `/help`
- `/chain` — chain status / report / board
- `/war` — status / board / xan calc / history / trends
- `/enemy` ⚙ — war enemy watchlist
- `/stakeout` ⚙ — enemy behavior tracking
- `/alliance` ⚙ — ally faction war tracking
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

## Deployment (Railway)

The repo ships a `Dockerfile` and `railway.json`. Push to the connected repo;
Railway builds and runs `python bot.py`. Set every variable from `.env` in the
Railway service's Variables tab. A `PORT`-bound healthcheck endpoint reports
`healthy` only when Discord is connected **and** every background loop is running.

## Environment variables

**Required:** `DISCORD_TOKEN`, `GUILD_ID`, `TORN_API_KEY`, `TORN_FACTION_ID`,
`MANAGEMENT_ROLE_IDS` (comma-separated), `MANAGEMENT_PING_ROLE_ID`,
`VERIFIED_ROLE_NAME`.

**Channels** (IDs): `INACTIVITY_ALERTS_CHANNEL_ID`, `ANNOUNCEMENTS_CHANNEL_ID`,
`WAR_CHANNEL_ID`, `WAR_MANAGEMENT_CHANNEL_ID`, `CHAINING_CHANNEL_ID`,
`HAPPY_JUMP_CHANNEL_ID`, `GENERAL_CHANNEL_ID`, `EVENT_POSTING_CHANNEL_ID`,
`ALLIANCE_CHANNEL_ID`, `WELCOME_CHANNEL_ID`, `RULES_CHANNEL_ID`.

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
