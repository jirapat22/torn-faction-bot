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

## Subscription Commands (Everyone)

### `/subscribe <type>`
Opt-in to receive pings for notifications.
- **chain** - Chain start/end notifications
- **enemy** - Enemy watchlist alerts (when enemies come online)
- **war** - War start/end notifications

### `/unsubscribe <type>`
Stop receiving pings for a notification type.

### `/mysubs`
View your current notification subscriptions.

---

## Chain Commands (Everyone)

### `/chain`
Show current faction chain status.
- Current hit count, timeout, respect modifier

### `/chainreport`
Show last completed chain report.
- Top contributors, total hits, respect earned

---

## War Commands (Everyone)

### `/war`
Show current ranked war status.
- Score, enemy faction, time remaining

### `/warboard`
Show war participation leaderboard.
- Top attackers, scores, participation

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
Configure automatic role sync with Torn positions.
- `status` - View current mappings
- `setrole` - Map Torn position to Discord role
- `remove` - Remove a position mapping
- `sync` - Force sync all members now
- `toggle` - Enable/disable auto sync

### `/enemy <action>`
Manage enemy watchlist during war.
- `list` - Show all tracked enemies
- `add` - Add enemy (requires torn_id and name)
- `remove` - Remove enemy from watchlist
- `check` - Force check all enemy statuses
- `clear` - Clear entire watchlist

---

## Availability Commands (Requires Verified Role)

### `/avail_setup <title> <date> <start_hour> <end_hour> [end_date]`
Create an availability poll for war/chain planning.
- **title**: Name of the event (e.g. "Chain Event")
- **date**: Start date in DD-MM-YYYY format
- **start_hour**: Start hour in TCT (0-23)
- **end_hour**: End hour in TCT (0-23)
- **end_date**: Optional end date for multi-day events (DD-MM-YYYY)
- Creates buttons for each time block (labeled A-X)
- Members click buttons to toggle availability
- Shows both TCT and local time

### `/avail_summary <poll_id>`
View an ASCII timetable of who is available when.
- **poll_id**: The poll ID shown when created

### `/avail_delete <poll_id>`
Delete an availability poll.
- **poll_id**: The poll ID to delete

### `/avail_list`
List all active availability polls.

### `/my_availability <poll_id>`
Check your own availability selections for a poll.
- **poll_id**: The poll ID to check

### `/tct_now`
Show the current Torn City Time (UTC).

---

## Automatic Features

### Inactivity Alerts
- Runs daily at 12:00 PM
- Posts to #inactivity-alerts
- Pings @Management only if inactive members found
- Warning: 3+ days inactive
- Critical: 5+ days inactive

### Chain Updates
- Posts milestone updates during chain events (10, 25, 50, 100...)
- Pings @chainwatcher + subscribers when chain completes
- Only active during Discord Chain/War events

### War Updates
- Posts war completion report
- Pings @Management + war subscribers when war ends
- Auto-clears enemy watchlist when war ends

### Enemy Status Checks
- Runs every 15 minutes when enemies are tracked
- Pings enemy subscribers when enemies come online or leave hospital

### Role Sync
- Runs every hour
- Silently syncs Discord roles based on Torn faction positions

### Welcome Messages
- Welcomes new members in #welcome-pit
- Directs them to /register and rules

---

## Quick Reference

| Command | Who Can Use | Purpose |
|---------|-------------|---------|
| `/register` | Everyone | Link Torn account |
| `/subscribe` | Everyone | Get notification pings |
| `/chain` | Everyone | Check chain status |
| `/war` | Everyone | Check war status |
| `/enemy list` | Everyone | View enemy watchlist |
| `/announce` | Management | Post announcements |
| `/verify` | Management | Manage verification |
| `/rolesync` | Management | Configure role sync |
| `/enemy add/remove` | Management | Manage watchlist |
| `/avail_setup` | Verified | Create availability poll |
| `/avail_summary` | Verified | View availability timetable |
| `/tct_now` | Everyone | Show current TCT time |

---

*Last updated: January 2026*
*Bot by Ascended Punks*
