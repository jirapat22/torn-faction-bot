# Ascended Punks Bot - Command Guide

## Member Commands (Everyone)

### `/register <torn_id>`
Link your Discord account to your Torn account.
- **torn_id**: Your Torn player ID (found in your profile URL)
- Auto-renames you to `TornName [TornID]`
- Gives you the Verified role

### `/whois [member]`
Look up a member's Torn info.
- Shows Torn ID, name, level, faction
- Leave blank to check yourself

### `/myactivity`
Check your own Torn activity status.
- Shows days since last action
- Private response (only you see it)

### `/status`
Show bot and tracking statistics.
- Total members, registered count, etc.

### `/ping`
Check if the bot is responding.

### `/unregister`
Unlink your Torn ID from Discord.

---

## Chain Command

### `/chain [view]`
All chain info in one command.
- **Status** (default) — Current chain status: hit count, timeout, respect modifier
- **Report** — Last completed chain top contributors, total hits, respect earned
- **Board** — Full participation board with ALL members ranked *(Management only)*
  - Aggregates all chains in the last 24 hours
  - Includes members with 0 hits for accountability

---

## War Command

### `/war [view] [member]`
All war info in one command.
- **Status** (default) — Current ranked war status: score, enemy, time remaining
- **Board** — War participation leaderboard *(Management only)*
- **Xan** — Xanax reimbursement calculation *(Management only)*
- **History** — War performance across multiple wars *(Management only)*
  - No member: last 5 wars overview, top/bottom performers
  - With member: that member's last 5 war records

### `/enemy list`
View all tracked enemies with their status.
- Shows online/offline/hospital/jail status
- Grouped by status for easy viewing

---

## Management Commands (Requires Management Role)

### `/announce`
Post an announcement to the announcements channel.
- Option to ping @everyone or not
- Opens a modal for multi-line text

### `/createevent`
Create a Discord scheduled event.
- **War** - Until manually ended
- **Chain** - 24 hour event
- **Happy Jump** - 24 hour event
- **Custom** - Set your own duration
- All times in TCT (UTC)

### `/verify <action>`
Manage verification settings.
- `options` - View current settings
- `setrole` - Set the verified role
- `setguest` - Set the guest role
- `toggle` - Toggle settings (change_name, append_id, etc.)
- `manual` - Manually verify a member
- `unverify` - Remove verification
- `fixrole` - Give verified role to registered users missing it
- `stats` - View verification statistics

### `/rolesync <action>`
Configure automatic role sync with Torn positions. Supports **multiple Discord roles per position** (e.g., "Warlord Of Pinheads" → @Management + @Pinheads).
- `status` - View current mappings
- `setrole` - Add a Discord role to a Torn position (additive — run multiple times for multiple roles)
- `remove` - Remove a specific role from a position (provide role), or remove all mappings for a position
- `sync` - Force sync all members now
- `toggle` - Enable/disable auto sync

### `/war board` / `/war xan` / `/war history`
War management views (see War Command above).
- **Board** — Stats: War hits, Out hits, incoming WHt/OHt, participation count
- **Xan** — Reimbursement: War hits + Chain outside hits = Total reimbursable
- **History** — Performance across last 5-10 wars, top/bottom performers

### `/enemy <action>`
Manage enemy watchlist during war.
- `list` - Show all tracked enemies
- `add` - Add enemy (requires torn_id and name)
- `remove` - Remove enemy from watchlist
- `check` - Force check all enemy statuses
- `clear` - Clear entire watchlist

### `/stakeout <action>`
Track enemy behavior patterns for strategic threat analysis over 24-48 hours.
- `add <torn_id> [hours]` - Start watching a player (default 24h, max 48h)
- `extend <torn_id> <hours>` - Add more tracking time without losing data
- `status <torn_id>` - Quick current status check (online/hospital/etc.)
- `list` - Show all active stakeouts with time remaining
- `report <torn_id>` - View comprehensive threat analysis with:
  - Activity heatmap by hour (TCT)
  - **Peak Danger Windows** (when they're most active/online)
  - **Safe Windows** (when they're offline/hospital - opportunity to act)
  - Sleep pattern detection (consecutive offline streaks)
  - Weekday vs Weekend activity comparison
  - Most dangerous and safest days
  - Estimated timezone
  - Online/hospital percentages
- `stop <torn_id>` - Stop watching (keeps history for reports)
- `clear` - Remove all stakeouts and historical data
- `test` - Test stakeout functionality (development)

### `/alliance <action>`
Track ally faction wars and get announcements.
- `add <faction_id> [faction_name]` - Add an ally faction to track
- `remove <faction_id>` - Remove an ally from tracking
- `list` - Show all tracked ally factions with war status
- `setchannel [channel]` - Set channel for war announcements (default: current)
- `check` - Force check all ally wars now (debug)

---

## Availability Command

### `/avail [action] [date] [start_time] [end_time] [end_date] [block_length] [poll_duration]`
All availability polling in one command.
- **Setup** — Create a poll *(Management only)*
  - Requires: `date` (start date), `start_time`, `end_time`
  - Optional: `end_date` (multi-day), `block_length` (1-6h, default 2), `poll_duration` (default 24h)
  - Creates buttons for each time block (labeled A-X)
  - Members click buttons to toggle availability *(Requires Verified role)*
  - Auto-posts results when poll closes
- **Summary** — View poll results with bar chart + member grid
  - Requires: `date` (any date within the poll)
- **Mine** — Check your own selections
  - Requires: `date`
- **List** — Show all active polls
- **Delete** — Remove a poll *(Management only)*
  - Requires: `date`

### `/tct_now`
Show the current Torn City Time (UTC).

---

## Stakeout vs Enemy: What's the Difference?

| Feature | **Stakeout** | **Enemy Watchlist** |
|---------|-------------|---------------------|
| **Purpose** | Strategic long-term intelligence | Real-time tactical alerts |
| **Duration** | 24-48 hours of monitoring | Active during war events only |
| **Output** | Detailed behavior analysis reports | Instant status change alerts |
| **Use Case** | Learn enemy patterns (sleep, activity) | Know when to attack NOW |
| **Data** | Activity heatmaps, danger/safe windows | Current online/hospital status |
| **When** | Before or between wars | During active war |

**Stakeout** = "Study their habits" — Track strong enemies over time to understand when they're most active (dangerous) or offline (safe windows to act).

**Enemy Watchlist** = "Alert me NOW" — Get pinged the moment an enemy comes online or leaves hospital during an active war.

---

## Automatic Features

### Inactivity Alerts
- Runs daily at 00:00 TCT (midnight UTC)
- Posts to #inactivity-alerts
- Pings @Management only if inactive members found
- Warning: 3+ days inactive
- Critical: 5+ days inactive

### Chain Updates
- Posts milestone updates during chain events (10, 25, 50, 100, 250, 500, 1000...)
- Posts completion message when chain ends
- Only active during Discord Chain/War events
- Requires 3 consecutive checks before declaring chain ended (prevents false reports from API glitches)

### War Updates
- Posts war completion report
- Pings @Management when war ends
- Auto-clears enemy watchlist when war ends

### Enemy Status Checks
- Runs every 15 minutes when enemies are tracked
- Posts alerts when enemies come online or leave hospital

### Role Sync
- Runs every hour
- Silently syncs Discord roles based on Torn faction positions

### Welcome Messages
- Welcomes new members in #welcome-pit
- Directs them to /register and rules

### Event Reminders (35-Hour Advance)
- Checks Discord scheduled events every hour
- Posts reminder when an event is ~35 hours away
- Routes to appropriate channel based on event name:
  - "Happy Jump" events → #punk-plaza
  - "Chain" events → #chaining
  - "War" events → #war
  - Other events → #announcements

### Alliance War Tracking
- Checks ally factions every 5 minutes
- Announces when ally war is scheduled (with countdown timer)
- **24-hour reminder** before ally war starts
- Announces when ally war starts
- Announces when ally war ends (win/loss/draw with score)
- Posts to configured alliance channel (default: #war)

### Stakeout Monitoring
- Checks all stakeout targets every 15 minutes
- Records online/offline/hospital status over time
- Tracks weekday vs weekend patterns separately
- Detects consecutive offline streaks for sleep pattern analysis
- Auto-expires stakeouts after their duration ends

---

## Quick Reference

| Command | Who Can Use | Purpose |
|---------|-------------|---------|
| `/register` | Everyone | Link Torn account |
| `/chain` | Everyone | Chain status / report / board |
| `/chain board` | Management | Full chain participation board |
| `/war` | Everyone | War status / board / xan / history |
| `/war board/xan/history` | Management | War leaderboard, xan calc, history |
| `/enemy list` | Everyone | View enemy watchlist |
| `/announce` | Management | Post announcements |
| `/verify` | Management | Manage verification |
| `/rolesync` | Management | Configure role sync |
| `/enemy add/remove` | Management | Manage watchlist |
| `/stakeout add/stop/extend` | Management | Track enemy behavior |
| `/stakeout status/report` | Everyone | View threat analysis |
| `/stakeout clear` | Management | Clear all stakeout data |
| `/alliance add/remove` | Management | Track ally factions |
| `/alliance setchannel` | Management | Set announcement channel |
| `/alliance list` | Everyone | View ally war status |
| `/alliance check` | Management | Force check ally wars |
| `/avail setup` | Management | Create availability poll |
| `/avail summary/mine/list` | Everyone | View poll results / your picks / list polls |
| `/avail delete` | Management | Delete a poll |
| `/tct_now` | Everyone | Show current TCT time |
| `/test_inactivity` | Management | Manually run inactivity check |

---

*Last updated: February 16, 2026*
*Bot by Ascended Punks*
