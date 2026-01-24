# Phase 5 Complete - Management & Events! 📅

## What's New

Your bot now has management tools for announcements and event scheduling with proper TCT (Torn City Time) support!

### New Features

**Management Commands (Leaders/Management Only):**
- `/announce` - Post announcements to the announcements channel
- `/createevent` - Create Discord scheduled events with TCT time

**Time Display Improvements:**
- All times show in user's local timezone (Discord automatic)
- TCT (Torn City Time = UTC) shown in parentheses for clarity
- Consistent time formatting across all commands

**Bug Fixes:**
- Fixed `/war` command showing twice
- Improved command sync (guild-specific, instant updates)

---

## New Commands

### `/announce` - Post Announcements

**Who can use:** Leaders and management only
- Users with "Manage Server" permission
- Users with management role IDs (set in config.py)

**Usage:**
```
/announce message:"Your announcement text" ping_everyone:False
```

**Parameters:**
- `message` - The announcement text (required)
- `ping_everyone` - Whether to ping @everyone (default: False)

**Example:**
```
/announce message:"War starts in 1 hour! Get ready!" ping_everyone:True
```

**What it does:**
1. Posts a formatted announcement to #announcements channel
2. Shows who posted it in the footer
3. Optionally pings @everyone
4. Sends confirmation to you privately

**Output:**
```
📢 Faction Announcement
War starts in 1 hour! Get ready!

Posted by YourName
```

---

### `/createevent` - Create Events

**Who can use:** Leaders and management only

**Usage:**
```
/createevent name:"Event Name" date:"2026-02-15" time:"14:30" duration:2 description:"Optional description"
```

**Parameters:**
- `name` - Event name (required) - e.g., "Faction War", "Chain Event"
- `date` - Event date in TCT (required) - Format: YYYY-MM-DD
- `time` - Event time in TCT (required) - Format: HH:MM (24-hour)
- `duration` - Duration in hours (default: 2)
- `description` - Event description (optional)

**Important:** All times are in **TCT (Torn City Time = UTC)**!

**Examples:**

**Create a war event:**
```
/createevent name:"Faction War" date:"2026-02-15" time:"20:00" duration:24 description:"War vs Enemy Faction - All hands on deck!"
```

**Create a chain event:**
```
/createevent name:"Chain Event" date:"2026-02-10" time:"18:00" duration:3 description:"Let's build a big chain!"
```

**Create a happy jump event:**
```
/createevent name:"Happy Jump" date:"2026-02-20" time:"12:00" duration:1 description:"Energy refill time!"
```

**What it does:**
1. Creates a Discord scheduled event visible in the Events tab
2. Sets location to "Torn City"
3. Shows time in TCT (UTC) in description
4. Discord automatically converts to each user's local timezone
5. Sends confirmation to you privately

**Event Display:**
Members will see the event in Discord's Events tab with:
- Event name
- Start time (in their local timezone)
- Duration
- Description with TCT time reference
- Location: "Torn City"

---

## Time Display Format

All timestamps now show both local time and TCT:

**Old format:**
```
Timeout: in 15 minutes
```

**New format (when TCT is shown):**
```
Timeout: Jan 24, 2026 8:30 PM (20:30:00 TCT)
```

**Where TCT is shown:**
- Event creation confirmations
- Event descriptions
- Anywhere precise coordination is needed

**How it works:**
- Discord automatically shows time in user's timezone
- TCT shown in parentheses for Torn coordination
- TCT = UTC (same thing, just Torn terminology)

---

## Configuration Updates

### Management Roles

Open [config.py](config.py) and set who can use management commands:

```python
# Management role IDs (users with these roles can use management commands)
MANAGEMENT_ROLE_IDS = []  # Add role IDs here, e.g., [123456789, 987654321]
# Leave empty [] to allow anyone with "Manage Server" permission
```

**To add management roles:**
1. Enable Developer Mode in Discord (Settings → Advanced → Developer Mode)
2. Right-click your "Leader" or "Co-Leader" role → Copy ID
3. Add to `MANAGEMENT_ROLE_IDS` list:
   ```python
   MANAGEMENT_ROLE_IDS = [1234567890, 9876543210]  # Example IDs
   ```
4. Save and restart bot

**Permission system:**
- Empty list `[]` = Only users with "Manage Server" permission
- With role IDs = Users with those roles OR "Manage Server" permission

---

## Testing Instructions

### 1. Update Configuration (Optional)

Add management role IDs if you want specific roles to have access:

```python
# In config.py
MANAGEMENT_ROLE_IDS = [YOUR_LEADER_ROLE_ID, YOUR_COLEADER_ROLE_ID]
```

### 2. Restart Bot

```bash
# Stop bot (Ctrl+C)
python bot.py
```

You should see:
```
Synced 13 slash command(s) to guild 1464244521318748378
```

Note: Was 11 commands, now **13 commands** (added `/announce` and `/createevent`)

### 3. Test Announcement Command

Try posting a test announcement:

```
/announce message:"Test announcement - please ignore" ping_everyone:False
```

Check #announcements channel for the post.

### 4. Test Event Creation

Create a test event:

```
/createevent name:"Test Event" date:"2026-02-01" time:"15:00" duration:1 description:"Testing event system"
```

Check Discord's Events tab (top of channel list) to see the event.

**To delete test event:**
1. Click Events tab
2. Find your test event
3. Click → Delete Event

---

## Time Conversion Examples

When creating events, use **TCT (UTC)** time:

**Scenario:** You want an event at 8 PM Eastern Time (ET)

1. Convert ET to UTC/TCT:
   - 8 PM ET = 1 AM TCT (next day) in winter
   - 8 PM ET = 12 AM TCT (next day) in summer

2. Create event with TCT time:
   ```
   /createevent name:"War" date:"2026-02-16" time:"01:00" ...
   ```

3. Discord shows to users in their timezone:
   - ET users see: 8:00 PM
   - TCT users see: 1:00 AM
   - PT users see: 5:00 PM

**Quick UTC/TCT conversion:**
- TCT is same as UTC
- Los Angeles: TCT -8 hours (winter) / -7 hours (summer)
- New York: TCT -5 hours (winter) / -4 hours (summer)
- London: TCT +0 hours (same as TCT)

**Tip:** Use https://www.worldtimebuddy.com/ to convert timezones

---

## Common Event Types

### Faction War
```
/createevent name:"Faction War vs [Enemy]" date:"YYYY-MM-DD" time:"HH:MM" duration:24 description:"All members participate! Check #war for updates"
```

### Chain Event
```
/createevent name:"Chain Event" date:"YYYY-MM-DD" time:"HH:MM" duration:3 description:"Let's build a massive chain! Chain updates in #chaining"
```

### Happy Jump
```
/createevent name:"Happy Jump" date:"YYYY-MM-DD" time:"HH:MM" duration:1 description:"Energy refill time! Use your energy before the jump"
```

### Custom Event
```
/createevent name:"Your Event Name" date:"YYYY-MM-DD" time:"HH:MM" duration:X description:"Your description"
```

---

## Files Changed

### Updated Files:
- [bot.py](bot.py:70-74) - Fixed command sync (no more duplicates)
- [config.py](config.py:35-37) - Added MANAGEMENT_ROLE_IDS
- [utils/helpers.py](utils/helpers.py:95-136) - Added TCT time display support
- [features/commands.py](features/commands.py:387-549) - Added `/announce` and `/createevent` commands

### What Stayed The Same:
- All existing commands (war, chain, inactivity, etc.)
- All existing features
- All channel IDs and settings

---

## Command Summary

You now have **13 slash commands**:

**User Commands:**
- `/ping` - Test bot
- `/register <torn_id>` - Link Discord to Torn
- `/unregister` - Remove link
- `/myactivity` - Check your Torn activity

**Info Commands:**
- `/status` - Bot stats
- `/whois @user` - Look up user's Torn profile
- `/registered` - See all registered members

**Chain Commands:**
- `/chain` - Current chain status
- `/chainreport` - Last chain report

**War Commands:**
- `/war` - Current war status
- `/warboard` - War participation leaderboard

**Management Commands (NEW!):**
- `/announce` - Post announcement (leaders only)
- `/createevent` - Create scheduled event (leaders only)

---

## Troubleshooting

### Commands still showing twice
**Solution:**
1. Completely restart bot
2. Wait 2-3 minutes
3. Completely restart Discord app
4. Commands should appear only once now

### "Permission Denied" when using management commands
**Check:**
1. Do you have "Manage Server" permission?
2. Is your role ID in `MANAGEMENT_ROLE_IDS` in config.py?
3. Restart bot after changing config.py

### Event not showing in Events tab
**Check:**
1. Date format is YYYY-MM-DD (e.g., 2026-02-15)
2. Time format is HH:MM in 24-hour (e.g., 14:30, not 2:30 PM)
3. Date/time is in the future (not past)
4. You're looking in the correct server

### Announcement not posting
**Check:**
1. ANNOUNCEMENTS_CHANNEL_ID is correct in config.py
2. Bot has permissions to post in announcements channel
3. Channel actually exists

### Wrong timezone in events
**Remember:**
- **When creating:** Use TCT (UTC) time
- **When viewing:** Discord automatically converts to your local timezone
- TCT = UTC = Torn City Time

---

## What Was Removed

As requested, the following features were **NOT** implemented:
- ❌ Happy Jump tracking (automatic)
- ❌ Crime coordination (automatic)

These can still be managed manually using `/createevent` and `/announce`.

---

## Summary of Phase 5

✅ **Announcement system**: Post to #announcements with one command
✅ **Event creation**: Create Discord scheduled events with TCT time
✅ **Management permissions**: Role-based access control
✅ **Time display**: Shows both local time and TCT
✅ **Fixed bugs**: War command no longer shows twice
✅ **Guild-specific sync**: Commands appear instantly

---

## How This Helps Your Faction

### For Leaders/Management:
- **Quick announcements**: No need to manually format announcements
- **Easy event scheduling**: Create events in seconds with TCT time
- **Automatic timezone conversion**: Discord handles all timezone math
- **Professional appearance**: Consistent formatted announcements

### For Members:
- **Clear communication**: All announcements in one place
- **Event reminders**: Discord notifies before events start
- **Local timezone**: All times shown in their timezone
- **TCT reference**: Can still see Torn time for coordination

### Real-World Usage:

**Before a war:**
```
/announce message:"War starts in 2 hours! Get your energy ready and check #war for updates." ping_everyone:True

/createevent name:"Faction War vs Rivals" date:"2026-01-25" time:"22:00" duration:24 description:"War event - all members participate!"
```

**Before a chain:**
```
/announce message:"Chain event tomorrow at 6 PM TCT. Save your energy!" ping_everyone:False

/createevent name:"Chain Event" date:"2026-01-26" time:"18:00" duration:3 description:"Building a big chain - be ready!"
```

---

## Next Steps

1. **Set management roles** (optional) in config.py
2. **Restart bot**
3. **Test `/announce`** with a test message
4. **Test `/createevent`** with a test event
5. **Delete test event** from Events tab
6. **Start using** for real announcements and events!

---

**Phase 5 Complete!** 🎉

Your faction now has:
- ✅ Inactivity tracking (all 19 members)
- ✅ Chain tracking (1-minute updates, auto reports)
- ✅ War tracking (1-minute updates, detailed management reports)
- ✅ Announcement system (formatted posts)
- ✅ Event management (with TCT time support)

**Restart your bot now to activate Phase 5!**
