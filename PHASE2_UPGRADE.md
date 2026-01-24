# Phase 2 Upgrade Guide - Torn Activity Tracking

Phase 2 is now complete! Your bot now tracks **Torn activity** instead of Discord activity.

## What's New in Phase 2

### Major Changes
- **Torn Activity Tracking**: Bot now uses Torn API to check `last_action` timestamps
- **User Registration System**: Members link their Discord account to Torn ID
- **Live Torn Data**: Shows Torn names, levels, and profiles in alerts
- **Better Accuracy**: Tracks actual game activity, not just Discord messages

### New Commands
- `/register <torn_id>` - Link your Discord account to your Torn ID
- `/unregister` - Unlink your Torn ID
- `/whois @user` - Look up someone's Torn profile
- `/registered` - See all registered members
- `/myactivity` - Check your Torn activity (updated to use Torn data)
- `/status` - Updated to show registration stats

### Removed Features
- No longer tracks Discord messages
- Old activity_log.json is not used anymore

---

## Upgrade Steps

### 1. Stop the Bot
Press **Ctrl+C** in the terminal to stop the bot.

### 2. No Changes Needed!
The code has been updated automatically. Your `config.py` already has the Torn API key filled in.

### 3. Restart the Bot
```bash
python bot.py
```

You should see:
```
Bot logged in as...
------
Verifying Torn API key...
Torn API key verified successfully!
Loaded Torn ID mappings for 0 Discord users
Synced 7 slash command(s)
Daily inactivity check will start in X hours (at 12:00 PM)
Bot is ready!
```

If you see "WARNING: Torn API key is invalid", check your TORN_API_KEY and TORN_FACTION_ID in config.py.

---

## How It Works Now

### Registration Flow
1. Members use `/register <torn_id>` to link their account
2. Bot verifies the Torn ID exists in your faction
3. Stores the mapping in `data/discord_to_torn.json`
4. Member is now tracked for inactivity

### Daily Inactivity Check (12:00 PM)
1. Bot fetches all faction members from Torn API
2. For each registered Discord user:
   - Looks up their Torn ID
   - Gets their `last_action` timestamp from Torn
   - Calculates days inactive
3. Posts alerts to #inactivity-alerts:
   - **Warning**: 3+ days inactive
   - **Critical**: 5+ days inactive
   - Shows Torn name, level, profile link
   - Reminds about unregistered members

### Data Storage
- `data/discord_to_torn.json` - Stores Discord ID → Torn ID mappings
- Old `data/activity_log.json` is no longer used

---

## Getting Started for Members

### Find Your Torn ID
1. Go to your Torn profile
2. Look at the URL: `https://www.torn.com/profiles.php?XID=1234567`
3. Your Torn ID is the number after `XID=` (e.g., 1234567)

### Register
In Discord, type:
```
/register 1234567
```
(Replace 1234567 with your actual Torn ID)

You'll get a confirmation:
```
✅ Registration Successful
Linked to Torn user: YourName [1234567]
```

### Check Your Activity
```
/myactivity
```

Shows:
- Your Torn name and level
- Days since last action
- Last action timestamp
- Profile link

---

## Admin Commands

### Check Who's Registered
```
/registered
```
Shows all Discord users who have linked their Torn ID.

### Look Up a User
```
/whois @username
```
Shows their Torn profile, level, and activity status.

### Check Bot Status
```
/status
```
Shows:
- Total Discord members
- Registered members
- Unregistered members
- Tracking method (Torn API)

---

## What Happens to Unregistered Members?

Members who haven't registered:
- **Will NOT be tracked** for inactivity
- Will see a reminder in daily alerts
- Can register anytime with `/register`

**Tip**: Encourage all faction members to register!

---

## Testing the New System

### Test 1: Register Yourself
```
/register <your_torn_id>
```
Should confirm registration and show your Torn name.

### Test 2: Check Your Activity
```
/myactivity
```
Should show your Torn activity data (name, level, last action).

### Test 3: Look Up Someone
```
/whois @yourself
```
Should show your Torn profile info.

### Test 4: Manual Inactivity Check (Admin Only)
You can manually trigger the check by waiting until 12 PM, or:
- Stop the bot
- Edit `bot.py` line 93 to `target_hour = <current_hour + 1>`
- Restart bot
- Wait 1 hour
- Check #inactivity-alerts

---

## Troubleshooting

### "Torn API key is invalid"
- Check `TORN_API_KEY` in config.py
- Get a new key from: https://www.torn.com/preferences.php#tab=api
- Make sure it's a **faction API key** (not limited key)

### "Torn ID not found in faction"
- Make sure the member is actually in your faction
- Check the Torn ID is correct (numbers only)
- Verify TORN_FACTION_ID in config.py is correct

### "Could not connect to Torn API"
- Check your internet connection
- Torn API might be down (rare)
- Try again in a few minutes

### Registration command not appearing
- Wait 5 minutes for Discord to sync commands
- Try restarting the bot
- Try kicking and re-inviting the bot

---

## API Usage

The bot is very efficient with API calls:
- **Daily check**: 1 API call per day (fetches all faction members)
- **Registration**: 1 API call per registration (verifies member)
- **Commands**: 1 API call per command (cached for 30 seconds)

With ~19 members, you'll use approximately **25-50 API calls per day**, well under Torn's limit of 100/minute.

---

## Next Steps

Once everyone is registered and Phase 2 is working well, we can add:

**Phase 3: Chain Tracking**
- `/chain` - Current chain status
- `/chainreport` - Last chain stats
- Auto-updates to #chaining

**Phase 4: War Tracking**
- `/war` - Current war status
- `/warboard` - War participation leaderboard
- Track hits and respect per member

---

## FAQ

**Q: What if someone leaves the faction?**
A: They'll be skipped in the daily check. Their mapping remains but won't cause errors.

**Q: Can I register someone else?**
A: No, each user must register themselves. This ensures accuracy.

**Q: What if I change my Torn ID?**
A: Just use `/unregister` then `/register <new_id>`.

**Q: Does this track hospital time or jail time?**
A: No, it only tracks `last_action` timestamp (any activity on Torn).

**Q: Can I test the inactivity check without waiting?**
A: Yes, but requires code edit. Ask for help if needed.

---

## Summary

Your bot now:
- ✅ Tracks real Torn activity (not Discord messages)
- ✅ Shows Torn names and levels in alerts
- ✅ Links to Torn profiles
- ✅ Verifies members are in faction
- ✅ Uses minimal API calls
- ✅ Ready for Phase 3 (chains) and Phase 4 (wars)

**Next action**: Have all faction members run `/register <torn_id>`!
