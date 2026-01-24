# Torn Faction Discord Bot

A custom Discord bot for managing Torn faction activities, including inactivity tracking, chain management, war tracking, and more.

## Current Status: Phase 1 Complete

**Working Features:**
- ✅ Bot foundation and connection
- ✅ Inactivity tracking (3-day and 5-day alerts)
- ✅ Slash commands: `/ping`, `/status`, `/myactivity`
- ✅ Daily automated inactivity checks
- ✅ Persistent data storage (JSON files)

**Coming Soon:**
- 🔜 Phase 2: Torn API integration (chains, reports)
- 🔜 Phase 3: War tracking
- 🔜 Phase 4: Happy jump & crime coordination

---

## Setup Instructions

### Step 1: Install Python Dependencies

1. Open Command Prompt or PowerShell
2. Navigate to the bot directory:
   ```bash
   cd "c:\Users\User\Desktop\Torn bot"
   ```
3. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

### Step 2: Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application"
3. Give it a name (e.g., "Torn Faction Bot")
4. Go to the "Bot" tab on the left
5. Click "Add Bot" → "Yes, do it!"
6. Under "Privileged Gateway Intents", enable:
   - ✅ Presence Intent
   - ✅ Server Members Intent
   - ✅ Message Content Intent
7. Click "Save Changes"
8. Click "Reset Token" → "Copy" (save this token securely!)

### Step 3: Invite Bot to Your Server

1. In Discord Developer Portal, go to "OAuth2" → "URL Generator"
2. Select these scopes:
   - ✅ `bot`
   - ✅ `applications.commands`
3. Select these bot permissions:
   - ✅ Read Messages/View Channels
   - ✅ Send Messages
   - ✅ Manage Messages
   - ✅ Embed Links
   - ✅ Read Message History
4. Copy the generated URL at the bottom
5. Open the URL in your browser
6. Select your Discord server
7. Click "Authorize"

### Step 4: Get Discord IDs

1. In Discord, go to Settings → Advanced → Enable "Developer Mode"
2. Right-click your server icon → "Copy Server ID" (this is your GUILD_ID)
3. Right-click each channel → "Copy Channel ID":
   - #inactivity-alerts → Copy ID
   - #announcements → Copy ID
   - (Other channels for later phases)

### Step 5: Configure the Bot

1. Open `config.py` in a text editor
2. Fill in these values:
   ```python
   BOT_TOKEN = "paste_your_bot_token_here"
   GUILD_ID = 123456789012345678  # Your server ID
   INACTIVITY_ALERTS_CHANNEL_ID = 123456789012345678  # #inactivity-alerts ID
   ANNOUNCEMENTS_CHANNEL_ID = 123456789012345678  # #announcements ID
   ```
3. Save the file

### Step 6: Run the Bot

1. In Command Prompt/PowerShell (in bot directory):
   ```bash
   python bot.py
   ```
2. You should see:
   ```
   Bot logged in as YourBotName (ID: ...)
   Connected to 1 server(s)
   ------
   Synced 3 slash command(s)
   Bot is ready!
   ```

### Step 7: Test the Bot

1. In your Discord server, type `/ping`
   - Bot should respond with "Pong!" and latency
2. Send a few messages in any channel
3. Type `/myactivity`
   - Bot should show your last activity time
4. Type `/status`
   - Bot should show tracking statistics

---

## How to Update the Bot

1. Stop the bot (press Ctrl+C in the terminal)
2. Edit any files you need to change
3. Run `python bot.py` again
4. Bot reconnects automatically - no need to re-invite!

---

## File Structure

```
torn-faction-bot/
├── bot.py                    # Main bot file
├── config.py                 # Configuration (bot token, IDs, settings)
├── requirements.txt          # Python dependencies
├── README.md                 # This file
│
├── data/                     # Data storage (created automatically)
│   └── activity_log.json     # User activity tracking
│
├── utils/                    # Utility functions
│   ├── helpers.py            # JSON, embeds, timestamps
│   ├── timestamps.py         # Time conversion utilities
│   └── torn_api.py           # Torn API (Phase 2+)
│
└── features/                 # Bot features
    ├── inactivity.py         # Inactivity tracking (ACTIVE)
    ├── commands.py           # Slash commands (ACTIVE)
    ├── chains.py             # Chain tracking (Phase 2)
    └── wars.py               # War tracking (Phase 3)
```

---

## Commands

### Available Now (Phase 1)

| Command | Description |
|---------|-------------|
| `/ping` | Check if bot is online |
| `/status` | Show inactivity tracking stats |
| `/myactivity` | Check your own activity status |

### Coming in Phase 2

| Command | Description |
|---------|-------------|
| `/chain` | Show current chain status |
| `/chainreport` | Show last chain report |

### Coming in Phase 3

| Command | Description |
|---------|-------------|
| `/war` | Show current war status |
| `/warboard` | Show war participation leaderboard |

---

## Inactivity Tracking

The bot automatically:
- Tracks when members send messages
- Runs a daily check at 12:00 PM
- Posts alerts to #inactivity-alerts for:
  - **3-day warning**: Members inactive for 3+ days
  - **5-day critical alert**: Members inactive for 5+ days

Alerts include:
- Member name
- Days inactive
- Last seen timestamp (shows in everyone's local timezone)

---

## Troubleshooting

### Bot won't start
- Check that `BOT_TOKEN` in config.py is correct
- Make sure all dependencies are installed (`pip install -r requirements.txt`)
- Verify Python version is 3.11+ (`python --version`)

### Bot is online but commands don't work
- Wait 5 minutes for Discord to sync commands
- Try kicking and re-inviting the bot
- Check that bot has proper permissions in your server

### Inactivity alerts not posting
- Verify `INACTIVITY_ALERTS_CHANNEL_ID` in config.py is correct
- Check that bot has permission to send messages in that channel
- Make sure members have sent messages (bot needs data to track)

### Bot crashes or errors
- Check the console for error messages
- Paste errors here for debugging
- Bot will reconnect automatically on restart

---

## Hosting Options

### Option 1: Run on Your Computer (Current)
- Pro: Simple, no setup needed
- Con: Only online when your PC is on
- How: Just run `python bot.py`

### Option 2: Replit (Coming Soon)
- Pro: Free 24/7 hosting
- Con: May sleep after inactivity (free tier)
- How: Setup guide coming in Phase 2

### Option 3: Railway (Coming Soon)
- Pro: Reliable 24/7 hosting
- Con: Limited free tier hours
- How: Setup guide coming in Phase 2

---

## Next Steps

Once Phase 1 is working, we'll add:
1. **Phase 2**: Torn API integration for chain tracking
2. **Phase 3**: War tracking and participation boards
3. **Phase 4**: Happy jump and crime coordination

---

## Support

If you encounter issues:
1. Check the Troubleshooting section above
2. Review error messages in the console
3. Ask for help with specific error messages

---

## Credits

Built for Torn faction management
Powered by discord.py
