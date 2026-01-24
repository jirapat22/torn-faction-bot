# Phase 2 Complete - Torn Activity Tracking is Live!

## What Just Changed

Your bot has been upgraded from **Discord message tracking** to **Torn API activity tracking**. This is a major improvement!

### Before (Phase 1)
- ❌ Tracked Discord messages only
- ❌ Didn't know who's actually active on Torn
- ❌ No connection to Torn profiles

### Now (Phase 2)
- ✅ Tracks real Torn activity via API
- ✅ Shows Torn names, levels, and profiles
- ✅ Members register their Torn ID
- ✅ Daily checks against Torn `last_action` timestamps
- ✅ Links directly to Torn profiles in alerts

---

## What You Need to Do

### 1. Restart the Bot
```bash
cd "c:\Users\User\Desktop\Torn bot"
python bot.py
```

Look for:
```
Verifying Torn API key...
✅ Torn API key verified successfully!
```

If you see "WARNING: Torn API key is invalid", check your config.py.

### 2. Register Yourself
In Discord:
```
/register 2886415
```
(Use your actual Torn ID - find it in your Torn profile URL)

### 3. Test It
```
/myactivity
```
Should show your Torn activity data.

### 4. Have Everyone Register
Tell your faction members to use:
```
/register <their_torn_id>
```

---

## New Commands Available

| Command | What It Does |
|---------|-------------|
| `/register <torn_id>` | Link Discord to Torn ID |
| `/unregister` | Remove link |
| `/myactivity` | Check your Torn activity |
| `/whois @user` | Look up someone's Torn profile |
| `/registered` | See who's registered |
| `/status` | Bot and registration stats |
| `/ping` | Test if bot is online |

See [COMMANDS.md](COMMANDS.md) for full details.

---

## How Inactivity Tracking Works Now

1. **Members register** with `/register <torn_id>`
2. **Bot stores** Discord ID → Torn ID mapping in `data/discord_to_torn.json`
3. **Daily at 12 PM**, bot:
   - Fetches all faction members from Torn API
   - Checks `last_action` timestamp for each registered user
   - Posts alerts to #inactivity-alerts if anyone is 3+ or 5+ days inactive
4. **Alerts include**:
   - Discord mention
   - Torn name [Level]
   - Days inactive
   - Last action timestamp (in everyone's local timezone)
   - Link to Torn profile

---

## Files Updated

### Modified Files
- [bot.py](bot.py) - Added Torn API verification, removed Discord message tracking
- [features/inactivity.py](features/inactivity.py) - Complete rewrite for Torn API
- [features/commands.py](features/commands.py) - Added registration commands
- [utils/torn_api.py](utils/torn_api.py) - Full Torn API integration

### New Files
- [PHASE2_UPGRADE.md](PHASE2_UPGRADE.md) - Detailed upgrade guide
- [COMMANDS.md](COMMANDS.md) - Command reference
- [WHATS_NEW.md](WHATS_NEW.md) - This file

### Data Files (Created Automatically)
- `data/discord_to_torn.json` - Stores Discord → Torn ID mappings

---

## Testing Checklist

- [ ] Bot starts without errors
- [ ] Torn API key verification succeeds
- [ ] `/register` command works
- [ ] `/myactivity` shows Torn data
- [ ] `/status` shows registration count
- [ ] `/whois @yourself` shows your Torn profile
- [ ] All faction members register
- [ ] Wait for 12 PM daily check (or trigger manually)
- [ ] Check #inactivity-alerts for proper formatting

---

## What's Different in Alerts

### Old Alerts (Phase 1)
```
❌ Inactivity Warning
John Doe (johndoe)
3 days inactive
Last seen: 3 days ago
```

### New Alerts (Phase 2)
```
✅ Inactivity Warning (Torn)
@JohnDoe - PlayerName [Lvl 45]
3 days inactive on Torn
Last action: 3 days ago
Profile: https://www.torn.com/profiles.php?XID=1234567
```

Much better!

---

## Common Questions

**Q: What if someone isn't registered?**
A: They won't be tracked. Daily alert will remind about unregistered members.

**Q: Do I need to re-register after bot restart?**
A: No! Registrations are saved in `data/discord_to_torn.json`.

**Q: Can I see who's registered?**
A: Yes! Use `/registered`.

**Q: What if my Torn ID changes?**
A: Use `/unregister` then `/register <new_id>`.

**Q: Does this use a lot of API calls?**
A: No! Only ~25-50 calls per day for your faction size.

**Q: When is the daily check?**
A: 12:00 PM (noon) every day.

**Q: Can I test it before 12 PM?**
A: Yes, but requires a small code edit. Ask if needed.

---

## Next Phase Preview

Once Phase 2 is stable, we can add:

**Phase 3: Chain Tracking**
- Real-time chain status
- Chain reports and stats
- Auto-updates to #chaining
- Top contributors leaderboard

**Phase 4: War Tracking**
- Current war status
- War participation tracking
- Hit counts and respect earned
- War leaderboards

---

## Summary

You now have a **professional-grade Torn faction bot** with:
- ✅ Torn API integration
- ✅ Real activity tracking
- ✅ User registration system
- ✅ Rich embeds with Torn data
- ✅ Profile links
- ✅ Efficient API usage
- ✅ Ready for more features

**Action Required**: Restart the bot and have everyone use `/register <torn_id>`!

Questions? Check [PHASE2_UPGRADE.md](PHASE2_UPGRADE.md) for full details.
