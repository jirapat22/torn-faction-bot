# Quick Start Guide - Get Your Bot Running in 10 Minutes

Follow these steps exactly to get your bot online quickly!

## 1. Install Dependencies (2 minutes)

Open Command Prompt and run:
```bash
cd "c:\Users\User\Desktop\Torn bot"
pip install discord.py aiohttp python-dotenv
```

Wait for installation to complete.

---

## 2. Create Discord Bot (3 minutes)

### Create the Application
1. Go to: https://discord.com/developers/applications
2. Click **"New Application"**
3. Name it: "Torn Faction Bot" (or whatever you like)
4. Click **"Create"**

### Set Up the Bot
1. Click **"Bot"** in the left menu
2. Click **"Add Bot"** → **"Yes, do it!"**
3. Scroll down to **"Privileged Gateway Intents"**
4. Enable ALL three toggles:
   - ✅ Presence Intent
   - ✅ Server Members Intent
   - ✅ Message Content Intent
5. Click **"Save Changes"**
6. Scroll back up and click **"Reset Token"** → **"Yes, do it!"**
7. Click **"Copy"** to copy your token
8. **Save this token somewhere safe!** You'll need it in Step 4

### Invite Bot to Server
1. Click **"OAuth2"** in the left menu
2. Click **"URL Generator"**
3. Under "Scopes", check:
   - ✅ bot
   - ✅ applications.commands
4. Under "Bot Permissions", check:
   - ✅ Read Messages/View Channels
   - ✅ Send Messages
   - ✅ Manage Messages
   - ✅ Embed Links
   - ✅ Read Message History
5. Scroll down and **copy the generated URL**
6. Paste the URL in your browser
7. Select your Discord server
8. Click **"Authorize"**

Your bot should now appear offline in your server!

---

## 3. Get Discord IDs (2 minutes)

### Enable Developer Mode
1. Open Discord
2. Click the gear icon (User Settings) at bottom left
3. Go to **Advanced** (in left menu)
4. Toggle **ON** "Developer Mode"
5. Close settings

### Copy Your Server ID
1. Right-click your server icon (top left)
2. Click **"Copy Server ID"**
3. Paste it somewhere - you'll need it next!

### Copy Channel IDs
1. Find your **#inactivity-alerts** channel
2. Right-click it → **"Copy Channel ID"**
3. Paste it somewhere - you'll need it next!
4. Repeat for **#announcements** channel

If you don't have these channels yet, create them now:
- Create a text channel called "inactivity-alerts"
- Create a text channel called "announcements"
- Then copy their IDs

---

## 4. Configure the Bot (2 minutes)

1. Open `config.py` in Notepad (or any text editor)
2. Find this line:
   ```python
   BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
   ```
3. Replace `YOUR_BOT_TOKEN_HERE` with your token from Step 2 (keep the quotes!)
4. Find this line:
   ```python
   GUILD_ID = 0
   ```
5. Replace `0` with your server ID from Step 3 (no quotes needed)
6. Find this line:
   ```python
   INACTIVITY_ALERTS_CHANNEL_ID = 0
   ```
7. Replace `0` with your #inactivity-alerts channel ID
8. Find this line:
   ```python
   ANNOUNCEMENTS_CHANNEL_ID = 0
   ```
9. Replace `0` with your #announcements channel ID
10. **Save the file** (Ctrl+S)

Your config.py should look something like this:
```python
BOT_TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GhJk1L.mNoPqRsTuVwXyZ0123456789AbCdEfGhIjKlMn"
GUILD_ID = 1234567890123456789
INACTIVITY_ALERTS_CHANNEL_ID = 9876543210987654321
ANNOUNCEMENTS_CHANNEL_ID = 1357924680135792468
```

---

## 5. Start the Bot (1 minute)

1. Open Command Prompt
2. Navigate to bot folder:
   ```bash
   cd "c:\Users\User\Desktop\Torn bot"
   ```
3. Run the bot:
   ```bash
   python bot.py
   ```

You should see:
```
Bot logged in as Torn Faction Bot (ID: ...)
Connected to 1 server(s)
------
Synced 3 slash command(s)
Daily inactivity check will start in XX hours (at 12:00 PM)
Bot is ready!
```

**Your bot is now ONLINE!** You should see it online in your Discord server!

---

## 6. Test the Bot

In your Discord server, try these commands:

### Test 1: Check if bot responds
Type: `/ping`

Bot should reply with "Pong!" and show latency.

### Test 2: Send some messages
Send 2-3 normal messages in any channel.

### Test 3: Check your activity
Type: `/myactivity`

Bot should show your last activity (just now!).

### Test 4: Check bot status
Type: `/status`

Bot should show tracking statistics.

---

## ✅ Success!

Your bot is now:
- ✅ Online and connected
- ✅ Tracking member activity
- ✅ Ready to send daily inactivity alerts at 12:00 PM
- ✅ Responding to slash commands

## What Happens Next?

The bot will now:
1. Track every message sent by members
2. Run a daily check at 12:00 PM
3. Post alerts to #inactivity-alerts if anyone is inactive for 3+ or 5+ days

## To Stop the Bot

Press **Ctrl+C** in the Command Prompt window.

## To Restart the Bot

Just run `python bot.py` again - it will reconnect automatically!

---

## Troubleshooting

### "No module named discord"
Run: `pip install discord.py`

### Bot says "Invalid bot token"
- Check that you copied the full token from Discord Developer Portal
- Make sure there are quotes around it in config.py
- Try resetting the token and copying it again

### Commands don't appear
- Wait 5 minutes for Discord to sync commands
- Try using the commands anyway (type `/ping` and see if it works)
- Restart the bot

### Bot is online but commands don't work
- Make sure bot has proper permissions in your server
- Try kicking and re-inviting the bot
- Check that Application Commands permission is enabled

---

## Need Help?

Check the full README.md for detailed troubleshooting and explanations!
