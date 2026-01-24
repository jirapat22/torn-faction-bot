# Bot Commands Reference

Quick reference for all available slash commands.

---

## User Commands

### `/register <torn_id>`
**Link your Discord account to your Torn ID**

Example: `/register 2886415`

- Required for inactivity tracking
- Verifies you're in the faction
- Shows confirmation with your Torn name

### `/unregister`
**Unlink your Torn ID from Discord**

- Removes your registration
- You'll no longer be tracked for inactivity

### `/myactivity`
**Check your Torn activity status**

Shows:
- Your Torn name and level
- Days since last action
- Last action timestamp
- Activity status (Active/Warning/Critical)
- Link to your Torn profile

### `/ping`
**Check if bot is online**

- Shows bot latency
- Confirms bot is responding

---

## Info Commands

### `/status`
**Show bot status and registration stats**

Shows:
- Total Discord members
- Registered members
- Unregistered members
- Tracking method

### `/whois @user`
**Look up a Discord user's Torn profile**

Example: `/whois @JohnDoe`

Shows:
- Torn name and level
- Torn ID with profile link
- Last action time
- Days inactive

### `/registered`
**Show all registered members**

Lists all Discord users who have linked their Torn ID.

---

## How Inactivity Tracking Works

1. **Register**: Members use `/register <torn_id>`
2. **Daily Check**: Bot checks Torn API at 12:00 PM daily
3. **Alerts Posted**: If members are inactive, alerts go to #inactivity-alerts
   - **Warning**: 3+ days inactive (orange)
   - **Critical**: 5+ days inactive (red)
4. **Details Included**:
   - Discord mention
   - Torn name and level
   - Days inactive
   - Last action timestamp
   - Link to Torn profile

---

## Finding Your Torn ID

1. Go to your Torn profile: https://www.torn.com/index.php
2. Click on your name or "View Profile"
3. Look at the URL: `https://www.torn.com/profiles.php?XID=1234567`
4. Your Torn ID is `1234567` (the numbers after XID=)

---

## Command Tips

- All commands use Discord's slash command system (type `/` to see them)
- Most commands show results privately (only you can see)
- `/whois`, `/status`, `/registered` are public
- Bot updates commands automatically when you restart it
- Wait 5 minutes after restart for commands to sync

---

## Coming Soon

**Phase 3: Chain Tracking**
- `/chain` - Current chain status
- `/chainreport` - Last chain stats

**Phase 4: War Tracking**
- `/war` - Current war status
- `/warboard` - War participation

---

## Need Help?

If a command isn't working:
1. Make sure bot is online (check for green dot)
2. Wait 5 minutes after bot restart
3. Try restarting the bot
4. Check bot has proper permissions in the channel
