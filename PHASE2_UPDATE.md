# Phase 2 Update - Check ALL Faction Members

## What Just Changed

Your bot now checks **ALL 19 faction members** from Torn API, regardless of whether they're registered in Discord!

### Before This Update
- ❌ Only checked members who used `/register`
- ❌ Unregistered members were ignored completely
- ❌ Had to wait for everyone to register

### After This Update ✅
- ✅ **Checks all 19 faction members** automatically
- ✅ **No registration required** for tracking
- ✅ **Registration is optional** (only for Discord @mentions)
- ✅ **Always posts to #inactivity-alerts** channel
- ✅ Shows Torn profile links for everyone

---

## How It Works Now

### Daily Check (12:00 PM)
1. Bot fetches **all faction members** from Torn API
2. Checks **everyone's** `last_action` timestamp
3. Posts alerts to **#inactivity-alerts** for anyone 3+ or 5+ days inactive

### Alert Display

**Registered Members** (used `/register`):
```
@DiscordUser - TornName [Lvl 45]
5 days inactive on Torn
Last action: 5 days ago
Profile: https://www.torn.com/profiles.php?XID=123
```

**Unregistered Members** (not in Discord or didn't register):
```
TornName [Lvl 38]
4 days inactive on Torn
Last action: 4 days ago
Profile: https://www.torn.com/profiles.php?XID=456
```

---

## Benefits

### Complete Coverage
- Tracks **every single faction member**
- No one slips through the cracks
- Doesn't matter if they're in Discord or not

### Optional Registration
- Members **don't need to register** anymore
- Registration only adds Discord @mention
- Still works perfectly without it

### Always the Right Channel
- Alerts **ALWAYS** go to #inactivity-alerts
- No confusion about where to look
- Consistent and reliable

---

## Registration is Now Optional

### Who Should Register?
- Members who **want to be @mentioned** in alerts
- Members who **want to use** `/myactivity` command
- Members who want to show up in `/registered` list

### Who Doesn't Need to Register?
- Members who just need to be **tracked** (everyone is tracked automatically)
- Members not in Discord
- Members who don't want notifications

---

## Testing Right Now

Want to see it in action immediately?

### Option 1: Wait for Tomorrow
- Next check is at **12:00 PM tomorrow**
- Will automatically check all 19 faction members

### Option 2: Test Now (Manual)

1. **Stop the bot** (Ctrl+C)
2. **Edit bot.py** line 91:
   ```python
   target_hour = 15  # Change to current hour + 1
   ```
3. **Restart**:
   ```bash
   python bot.py
   ```
4. **Wait 1 hour** (or change to current hour + few minutes)
5. **Check #inactivity-alerts**

After testing, change back to `target_hour = 12`.

---

## Example Alert Output

When someone is inactive, #inactivity-alerts will show:

```
⚠️ Inactivity Warning (Torn)
3 faction member(s) have been inactive on Torn for 3+ days:

@RegisteredUser - PlayerOne [Lvl 45]
4 days inactive on Torn
Last action: 4 days ago
Profile: https://www.torn.com/profiles.php?XID=111

UnregisteredPlayer [Lvl 38]
5 days inactive on Torn
Last action: 5 days ago
Profile: https://www.torn.com/profiles.php?XID=222

AnotherPlayer [Lvl 42]
3 days inactive on Torn
Last action: 3 days ago
Profile: https://www.torn.com/profiles.php?XID=333

ℹ️ Registration Info
1 of 19 faction members are registered in Discord.
18 faction members are not registered (shown without @mention).

Want Discord Mentions?
Use /register <torn_id> to link your Torn account
```

---

## What Changed in Code

### Updated Files
- `features/inactivity.py` - Now checks ALL faction members, not just registered ones
- Added emoji indicators (🔴 for critical, ⚠️ for warning, ℹ️ for info)
- Always uses INACTIVITY_ALERTS_CHANNEL_ID (never announcements)

### Key Changes
1. **Loop through all Torn members** (not just registered Discord users)
2. **Optional Discord mention** (only if member is registered)
3. **Error messages to inactivity-alerts** (not announcements channel)
4. **Footer shows registration stats**

---

## Summary

Your bot is now **production-ready** for full faction tracking!

- ✅ Tracks all 19 faction members automatically
- ✅ No setup required from members
- ✅ Registration is optional (for @mentions only)
- ✅ Always posts to #inactivity-alerts
- ✅ Shows complete Torn profile info
- ✅ Ready for Phase 3 (chains) and Phase 4 (wars)

**No action required!** The bot is already updated and ready to use.

Next check: **Tomorrow at 12:00 PM** (or test early using instructions above)

---

## Commands Still Work

All commands still work exactly the same:
- `/register <torn_id>` - Link for Discord mentions
- `/myactivity` - Check your activity (requires registration)
- `/status` - Bot stats
- `/whois @user` - Look up registered members
- `/registered` - See registered list
- `/ping` - Test bot

The difference is tracking now happens **automatically for everyone**, not just registered members.
