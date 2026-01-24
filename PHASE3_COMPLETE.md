# Phase 3 Complete - Chain Tracking is Live! 🔗

## What's New

Your bot now has **full chain tracking** with live updates and historical reports!

### New Features

**Commands:**
- `/chain` - Show current chain status (live)
- `/chainreport` - Show last completed chain with top contributors

**Automatic Updates:**
- Posts to #chaining every 30 minutes when chain is active
- Shows chain count, timeout, respect modifier
- Color-coded alerts (green = safe, orange = timeout soon, red = critical)

**Chain History:**
- Saves all chain data to `data/chain_history.json`
- Tracks chain count, max chain, modifiers over time

---

## How It Works

### `/chain` Command

Shows live chain status with:
- Current chain count
- Max chain reached
- Respect modifier
- Timeout countdown (with color alerts)
- Cooldown status

**Color Coding:**
- 🟢 **Green** - Chain safe (5+ mins remaining)
- 🟠 **Orange** - Warning (1-5 mins remaining)
- 🔴 **Red** - Critical (less than 1 min remaining)

### `/chainreport` Command

Shows last completed chain:
- Total hits
- Total respect earned
- Start/end times
- **Top 5 contributors** (name, hits, respect)

### Automatic Updates (Every 30 Minutes)

Bot checks chain status every 30 minutes:
- If chain count changed → posts update to #chaining
- Shows same info as `/chain` command
- Only posts when chain is active
- Saves chain data to history

---

## Example Outputs

### Active Chain (Safe)
```
✅ Current Chain: 247 hits
✅ Chain Active

Current Chain: 247
Max Chain: 312
Respect Modifier: 1.75x
Timeout: in 15 minutes
Cooldown Ends: Ready
```

### Chain About to Timeout
```
🔴 Current Chain: 156 hits
🔴 CRITICAL - Chain about to timeout!

Current Chain: 156
Max Chain: 200
Respect Modifier: 1.50x
Timeout: in 45 seconds
Cooldown Ends: in 2 minutes
```

### Chain Report
```
Chain Report #12345
Chain completed 2 hours ago

Total Hits: 487
Total Respect: 12,456
Started: 3 hours ago

Top Contributors:
1. PlayerOne: 94 hits (2,847 respect)
2. PlayerTwo: 76 hits (2,234 respect)
3. PlayerThree: 68 hits (1,987 respect)
4. PlayerFour: 52 hits (1,654 respect)
5. PlayerFive: 47 hits (1,432 respect)
```

---

## Testing Right Now

### Test Commands

Try these commands immediately:

```
/chain         → Shows current chain (or "no active chain")
/chainreport   → Shows last completed chain
```

### Test Auto-Updates

The bot checks every **30 minutes** for chain updates.

If you want to test it immediately:
1. Start a chain in Torn
2. Wait for next 30-minute mark (e.g., 3:00 PM, 3:30 PM, 4:00 PM)
3. Check #chaining for automatic update

Or just wait - it will post automatically during your next chain!

---

## Data Saved

### chain_history.json

Bot saves chain snapshots:
```json
{
  "chain_1737739200": {
    "count": 247,
    "timestamp": 1737739200,
    "max": 312,
    "modifier": 1.75
  }
}
```

This helps track your faction's chaining activity over time.

---

## What Changed in Code

### New Files
- `features/chains.py` - Complete chain tracking system

### Updated Files
- `bot.py` - Added ChainTracker initialization
- `bot.py` - Added 30-minute background task
- `features/commands.py` - Added `/chain` and `/chainreport`

### New Background Task
- Runs every **30 minutes**
- Checks chain status via Torn API
- Posts to #chaining if chain count changed
- Saves chain data to history

---

## Commands Summary

You now have **9 slash commands**:

**User Commands:**
- `/ping` - Test bot
- `/register <torn_id>` - Link Discord to Torn
- `/unregister` - Remove link
- `/myactivity` - Check your Torn activity

**Info Commands:**
- `/status` - Bot stats
- `/whois @user` - Look up user's Torn profile
- `/registered` - See all registered members

**Chain Commands (NEW!):**
- `/chain` - Current chain status
- `/chainreport` - Last chain report

---

## Bot Restart Required

To activate Phase 3, **restart your bot**:

```bash
# Stop the bot (Ctrl+C)
python bot.py
```

You should see:
```
Torn API key verified successfully!
Loaded Torn ID mappings for X Discord users
Loaded chain history with X records  ← NEW!
Synced 9 slash command(s)  ← Was 7, now 9!
Chain update checker started (checks every 30 minutes)  ← NEW!
Bot is ready!
```

---

## Configuration Check

Make sure your #chaining channel ID is correct in `config.py`:

```python
CHAINING_CHANNEL_ID = 1464251955395170364  # Your #chaining channel
```

If it's wrong:
1. Right-click #chaining channel → Copy ID
2. Update `config.py`
3. Restart bot

---

## What's Next?

Phase 3 is complete! Next up:

**Phase 4: War Tracking**
- `/war` - Current war status
- `/warboard` - War participation leaderboard
- Track hits, respect, assists per member
- Auto-updates to #war during active wars

---

## Troubleshooting

### "No active chain" when there IS a chain
- Check your Torn API key has chain access
- Wait 30 seconds (API cache)
- Try again

### Chain updates not posting to #chaining
- Check CHAINING_CHANNEL_ID in config.py
- Make sure bot has permissions in #chaining
- Check that chain count actually changed (bot only posts when count changes)

### Commands not appearing
- Wait 5 minutes for Discord to sync
- Restart bot
- Try `/ping` first to test

---

## Summary

Phase 3 adds:
- ✅ Live chain status command
- ✅ Chain report with top contributors
- ✅ Automatic 30-minute updates to #chaining
- ✅ Chain history tracking
- ✅ Color-coded timeout alerts
- ✅ Full Torn API chain integration

**Restart your bot now to activate Phase 3!**

Your faction now has:
- ✅ Inactivity tracking (all 19 members)
- ✅ Chain tracking (live + reports)
- 🔜 War tracking (Phase 4)

Ready for Phase 4 when you are!
