# Phase 4 Complete - War Tracking is Live! ⚔️

## What's New

Your bot now has **full war tracking** with live status and participation leaderboards!

### New Features

**Commands:**
- `/war` - Show current war status (live scores, time remaining)
- `/warboard` - Show war participation leaderboard with top contributors

**Automatic Updates:**
- Checks war status every 5 minutes
- Auto-posts war results to #war when wars end
- Tracks all war history in `data/war_history.json`

---

## How It Works

### `/war` Command

Shows live war status with:
- Current score (Your faction vs Enemy faction)
- Enemy faction name
- War start time
- War end time (countdown)
- War ID for reference

**Color Coding:**
- 🟢 **Green** - Winning the war
- 🟠 **Orange** - Losing the war
- 🔵 **Blue** - Tied score

### `/warboard` Command

Shows war participation leaderboard:
- **Top 10 contributors** ranked by score
- Individual stats per member:
  - Total score (war points)
  - Hits made
  - Assists provided
- **Total faction stats:**
  - Total hits
  - Total score
  - Number of participants

Uses Torn names (not Discord names or IDs)

### Automatic War End Reports

Bot checks war status every **5 minutes**:
- When war ends → posts completion message to #war
- Saves all war data to history
- Tracks war progression over time

---

## Example Outputs

### Active War (Winning)
```
✅ Ranked War vs Enemy Faction
✅ Winning 1,247 - 892

Our Score: 1,247
Enemy Score: 892
War ID: 12345
Started: 2 hours ago
Ends: in 22 hours
```

### Active War (Losing)
```
⚠️ Ranked War vs Rival Faction
⚠️ Losing 543 - 789

Our Score: 543
Enemy Score: 789
War ID: 12346
Started: 5 hours ago
Ends: in 19 hours
```

### War Participation Leaderboard
```
War Participation Leaderboard
War ID: 12345

Total Hits: 487
Total Score: 1,247
Participants: 15

Top Contributors:
1. PlayerOne: 247 pts (94 hits, 12 assists)
2. PlayerTwo: 189 pts (76 hits, 8 assists)
3. PlayerThree: 156 pts (68 hits, 5 assists)
4. PlayerFour: 134 pts (52 hits, 9 assists)
5. PlayerFive: 98 pts (47 hits, 3 assists)
...
```

### War Complete (Auto-Posted)
```
🏁 **War Complete!**

🏁 War Complete!
War #12345 has ended
```

---

## Testing Right Now

### Test Commands

Try these commands immediately:

```
/war           → Shows current war (or "no active war")
/warboard      → Shows war leaderboard (or "no active war")
```

### Test Auto-Updates

The bot checks every **5 minutes** for war status changes.

When a war ends:
1. Bot detects war completion
2. Auto-posts to #war channel
3. Saves final stats to war_history.json

---

## Data Saved

### war_history.json

Bot saves war snapshots every 5 minutes during active wars:
```json
{
  "12345": {
    "timestamp": 1737739200,
    "data": {
      "war": { "start": 1737720000, "end": 1737806400, "winner": 0 },
      "factions": {
        "45635": {
          "name": "Your Faction",
          "score": 1247,
          "members": {
            "123456": { "hits": 94, "assists": 12, "score": 247 }
          }
        }
      }
    }
  }
}
```

This helps track your faction's war performance over time.

---

## What Changed in Code

### New Files
- [features/wars.py](features/wars.py) - Complete war tracking system

### Updated Files
- [bot.py](bot.py) - Added WarTracker initialization
- [bot.py](bot.py) - Added 5-minute war background task
- [features/commands.py](features/commands.py) - Added `/war` and `/warboard` commands

### New Background Task
- Runs every **5 minutes**
- Checks war status via Torn API (`rankedwars` selection)
- Posts to #war when wars end
- Saves war data to history

### Torn API
- No changes needed! Already supports `rankedwars` selection
- Uses existing caching and error handling

---

## Commands Summary

You now have **11 slash commands**:

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

**War Commands (NEW!):**
- `/war` - Current war status
- `/warboard` - War participation leaderboard

---

## Bot Restart Required

To activate Phase 4, **restart your bot**:

```bash
# Stop the bot (Ctrl+C in the terminal)
python bot.py
```

You should see:
```
Torn API key verified successfully!
Loaded Torn ID mappings for X Discord users
Loaded chain history with X records
Loaded war history with X records  ← NEW!
Synced 11 slash command(s)  ← Was 9, now 11!
Chain update checker started (checks every minute)
War update checker started (checks every 5 minutes)  ← NEW!
Bot is ready!
```

---

## Configuration Check

Make sure your #war channel ID is correct in [config.py](config.py):

```python
WAR_CHANNEL_ID = 1464251906062024786  # Your #war channel
```

If it's wrong:
1. Right-click #war channel → Copy ID
2. Update [config.py](config.py)
3. Restart bot

---

## What's Next?

Phase 4 is complete! You now have a fully functional Torn faction bot with:

- ✅ Inactivity tracking (all 19 faction members)
- ✅ Chain tracking (1-minute updates, auto reports)
- ✅ War tracking (5-minute updates, leaderboards)

**Future phases** you might want to add:

**Phase 5: Happy Jump Tracking**
- Track happy jump donations
- Leaderboard of top donors
- Auto-thank messages

**Phase 6: Crime Coordination**
- Organize crimes with faction members
- Track crime success rates
- Auto-reminders for crime cooldowns

**Phase 7: Event Scheduling**
- Schedule faction events
- Auto-reminders before events
- RSVP tracking

---

## Troubleshooting

### "No active war" when there IS a war
- Check your Torn API key has war access
- Verify TORN_FACTION_ID is correct
- Wait 30 seconds (API cache) and try again

### War leaderboard shows IDs instead of names
- This happens if member left faction
- Bot fetches current faction roster for names
- Shows `ID:123456` for ex-members

### Commands not appearing
- Wait 5 minutes for Discord to sync
- Restart bot
- Try `/ping` first to test connection

### War updates not posting to #war
- Check WAR_CHANNEL_ID in [config.py](config.py)
- Ensure bot has permissions in #war channel
- Check console for error messages

---

## Summary

Phase 4 adds:
- ✅ Live war status command with scores
- ✅ War participation leaderboard (top 10)
- ✅ Automatic war completion reports
- ✅ War history tracking
- ✅ 5-minute war status checks
- ✅ Full Torn API war integration

**Restart your bot now to activate Phase 4!**

Your faction bot now tracks:
- ✅ Member inactivity (daily checks at 8 PM)
- ✅ Chain progress (1-minute updates)
- ✅ War status (5-minute updates)
- 🎯 Ready for Phase 5 when you need it!

Enjoy your complete faction management bot! 🎉
