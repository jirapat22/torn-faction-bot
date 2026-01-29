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

## Availability Commands

### `/avail_setup <start_date> <start_time> <end_time> [end_date] [block_length]`
Create an availability poll for war/chain planning. *(Requires Management role)*
- **start_date**: Start date in DD-MM-YYYY format
- **start_time**: Start time in TCT/UTC (HH:MM, e.g. 00:00)
- **end_time**: End time in TCT/UTC (HH:MM, e.g. 24:00)
- **end_date**: Optional end date for multi-day events (DD-MM-YYYY)
- **block_length**: Hours per time block (1-6, default: 2)
- Creates buttons for each time block (labeled A-X)
- Members click buttons to toggle availability *(Requires Verified role)*
- Shows both TCT and local time

### `/avail_summary <date>`
View an ASCII timetable of who is available when.
- **date**: Any date within the poll range (DD-MM-YYYY)

### `/avail_delete <date>`
Delete an availability poll. *(Requires Management role)*
- **date**: Any date within the poll to delete (DD-MM-YYYY)

### `/avail_list`
List all active availability polls.

### `/my_availability <date>`
Check your own availability selections for a poll.
- **date**: Any date within the poll (DD-MM-YYYY)

### `/tct_now`
Show the current Torn City Time (UTC).

---

## Automatic Features

### Inactivity Alerts
- Runs daily at 00:00 TCT (midnight UTC)
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

### Event Reminders (30-Hour Advance)
- Checks Discord scheduled events every hour
- Posts reminder when an event is ~30 hours away
- Routes to appropriate channel based on event name:
  - "Happy Jump" events → #punk-plaza
  - "Chain" events → #chaining
  - "War" events → #war
  - Other events → #announcements

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
| `/avail_setup` | Management | Create availability poll |
| `/avail_summary` | Everyone | View availability timetable |
| `/tct_now` | Everyone | Show current TCT time |
| `/test_inactivity` | Management | Manually run inactivity check |

---

*Last updated: January 2026*
*Bot by Ascended Punks*
