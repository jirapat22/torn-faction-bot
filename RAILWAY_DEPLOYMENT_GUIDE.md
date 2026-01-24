# Railway Deployment Guide for Torn Faction Bot

## Prerequisites Checklist

Before starting, ensure you have:
- [x] Discord bot created at https://discord.com/developers/applications
- [x] Bot token copied
- [x] Torn API key from https://www.torn.com/preferences.php#tab=api
- [x] All Discord channel IDs copied
- [x] GitHub account
- [x] Git installed on your PC

---

## Part 1: Prepare Code for Railway

### Step 1: Backup Your Current config.py

```bash
cd "c:\Users\User\Desktop\Torn bot"
copy config.py config_local_backup.py
```

This saves your current hardcoded config as a backup.

### Step 2: Replace config.py with Railway Version

```bash
copy config_railway.py config.py
```

This replaces your hardcoded config with the environment variable version.

### Step 3: Create Your Local .env File

```bash
copy .env.example .env
```

Now open `.env` in your text editor and fill in ALL your actual values:

```env
DISCORD_TOKEN=MTQ2NDQyNzA0MDQxNjU5NTk2OA.G0N3Xz.MJEzOP5u5cIRPGsvNVr9KqpjXV09eGsyiUfre0
GUILD_ID=1464244521318748378
INACTIVITY_ALERTS_CHANNEL_ID=1464252699523416086
ANNOUNCEMENTS_CHANNEL_ID=1464251805700587543
WAR_CHANNEL_ID=1464251906062024786
WAR_MANAGEMENT_CHANNEL_ID=1464526256983904342
CHAINING_CHANNEL_ID=1464251955395170364
HAPPY_JUMP_CHANNEL_ID=0
TORN_API_KEY=EL6Ly1PGBsaY9xlj
TORN_FACTION_ID=45635
MANAGEMENT_ROLE_IDS=1464253731297169439,1464253009096872078,1464254484375928991,1464429923509731576
COMMAND_PREFIX=!
INACTIVITY_WARNING_DAYS=3
INACTIVITY_CRITICAL_DAYS=5
TORN_API_CACHE_SECONDS=30
TORN_API_MAX_REQUESTS_PER_MINUTE=60
```

**Important:** Save this file. It stays on your PC only - never commit it to GitHub!

### Step 4: Update .gitignore

```bash
copy .gitignore_railway .gitignore
```

This ensures `.env` is blocked but `config.py` is allowed (since it now uses env vars).

### Step 5: Test Locally

```bash
python bot.py
```

The bot should start normally. If it works, you're ready for Railway!

If you get errors:
- Make sure `.env` file exists and has all values filled in
- Check for typos in `.env` (no spaces around `=`)
- Verify your bot token is correct

Stop the bot (Ctrl+C) once verified.

---

## Part 2: Create GitHub Repository

### Step 6: Initialize Git (if not already done)

```bash
cd "c:\Users\User\Desktop\Torn bot"
git init
```

### Step 7: Stage All Files

```bash
git add .
```

This stages all files EXCEPT `.env` (blocked by .gitignore).

**Double-check .env is NOT staged:**

```bash
git status
```

You should see:
```
Changes to be committed:
  bot.py
  config.py
  requirements.txt
  .env.example
  ...
```

If you see `.env` in the list, STOP and run:
```bash
git reset .env
```

### Step 8: Commit

```bash
git commit -m "Initial commit - Railway deployment ready"
```

### Step 9: Create GitHub Repository

1. Go to https://github.com/new
2. Repository name: `torn-faction-bot` (or any name you want)
3. **Make it PRIVATE** (contains your bot logic)
4. **DO NOT** add README, .gitignore, or license (you already have these)
5. Click "Create repository"

### Step 10: Push to GitHub

GitHub will show you commands. Use these:

```bash
git remote add origin https://github.com/YOUR_USERNAME/torn-faction-bot.git
git branch -M main
git push -u origin main
```

Replace `YOUR_USERNAME` with your actual GitHub username.

**Verify:** Go to your GitHub repository in browser. You should see all your bot files EXCEPT `.env`.

---

## Part 3: Deploy to Railway

### Step 11: Create Railway Account

1. Go to https://railway.com
2. Click "Login"
3. Choose "Login with GitHub"
4. Authorize Railway to access your GitHub

### Step 12: Create New Project

1. Click "New Project"
2. Choose "Deploy from GitHub repo"
3. Select `torn-faction-bot` (or your repository name)
4. Railway will start building immediately - **let it fail** (we need to add environment variables first)

### Step 13: Add Environment Variables

1. In Railway dashboard, click your bot service
2. Click "Variables" tab
3. Click "New Variable" and add ALL of these:

**Required Variables:**

```
DISCORD_TOKEN = MTQ2NDQyNzA0MDQxNjU5NTk2OA.G0N3Xz.MJEzOP5u5cIRPGsvNVr9KqpjXV09eGsyiUfre0
GUILD_ID = 1464244521318748378
INACTIVITY_ALERTS_CHANNEL_ID = 1464252699523416086
ANNOUNCEMENTS_CHANNEL_ID = 1464251805700587543
WAR_CHANNEL_ID = 1464251906062024786
WAR_MANAGEMENT_CHANNEL_ID = 1464526256983904342
CHAINING_CHANNEL_ID = 1464251955395170364
HAPPY_JUMP_CHANNEL_ID = 0
TORN_API_KEY = EL6Ly1PGBsaY9xlj
TORN_FACTION_ID = 45635
MANAGEMENT_ROLE_IDS = 1464253731297169439,1464253009096872078,1464254484375928991,1464429923509731576
```

**Optional Variables (have defaults):**

```
COMMAND_PREFIX = !
INACTIVITY_WARNING_DAYS = 3
INACTIVITY_CRITICAL_DAYS = 5
TORN_API_CACHE_SECONDS = 30
TORN_API_MAX_REQUESTS_PER_MINUTE = 60
```

**How to add each variable:**
1. Click "New Variable"
2. Variable name: `DISCORD_TOKEN` (exact name, case-sensitive)
3. Value: Your actual token
4. Click outside the box to save
5. Repeat for each variable

### Step 14: Trigger Redeploy

1. Click "Deployments" tab
2. Click the three dots on the latest deployment
3. Click "Redeploy"

Railway will:
- Pull code from GitHub
- Install dependencies from requirements.txt
- Start bot.py

### Step 15: Monitor Deployment

1. Click "Deployments" tab
2. Click on the active deployment
3. Watch the logs

**Successful deployment looks like:**
```
Installing dependencies...
Collecting discord.py>=2.3.0
Collecting aiohttp>=3.9.0
Successfully installed discord.py-2.3.2 aiohttp-3.9.1
Starting application...
Bot logged in as YourBotName (ID: 1464427040416595968)
Connected to 1 server(s)
Torn API key verified successfully!
Synced 13 slash command(s) to guild 1464244521318748378
Bot is ready!
```

**If you see errors:**
- Check "Common Errors" section below
- Verify all environment variables are set correctly
- Check Railway logs for specific error messages

### Step 16: Verify Bot is Online

1. Open Discord
2. Check if your bot shows as **ONLINE**
3. Test a command: `/ping`
4. Check logs: Wait 1-2 minutes, check if inactivity tracking logs appear

---

## Part 4: Update Workflow (After Deployment)

### How to Update Bot Code

**Step 1: Edit locally**
```bash
# Edit any file on your Windows PC
# Example: features/commands.py
```

**Step 2: Test locally**
```bash
python bot.py
# Test in Discord
# Ctrl+C to stop
```

**Step 3: Commit and push**
```bash
git add .
git commit -m "Fixed bug in chain tracking"
git push origin main
```

**Step 4: Railway auto-deploys**
- Railway detects new code on GitHub
- Railway rebuilds and restarts bot (~30-45 seconds)
- Bot comes back online with new code
- JSON data persists

**No manual Railway clicks needed!**

---

## Part 5: Troubleshooting

### Error: "Build Failed"

**Check Railway logs for:**

**"ModuleNotFoundError: No module named 'X'"**
- Missing dependency in requirements.txt
- Add it to requirements.txt and push to GitHub

**"SyntaxError"**
- Python syntax error in your code
- Fix locally, test, commit, push

### Error: "Bot starts but goes offline immediately"

**Check Railway logs for:**

**"ERROR: Bot token not found"**
- DISCORD_TOKEN variable is missing or wrong
- Go to Railway → Variables → check DISCORD_TOKEN

**"LoginFailure: Improper token has been passed"**
- DISCORD_TOKEN is invalid
- Get new token from Discord Developer Portal
- Update in Railway Variables

**"Torn API key is invalid"**
- TORN_API_KEY or TORN_FACTION_ID is wrong
- Verify at https://www.torn.com/preferences.php#tab=api
- Update in Railway Variables

### Error: "Bot is online but commands don't work"

**Check:**

**Commands not appearing in Discord:**
- Wait 2-3 minutes for Discord to sync
- Restart Discord app
- Check GUILD_ID is correct in Railway Variables

**Bot not posting to channels:**
- Check channel IDs in Railway Variables
- Ensure bot has permissions in those channels
- Check Railway logs for "Could not find channel" warnings

### Error: "JSON files corrupted after redeploy"

**Recovery:**

Railway doesn't persist data between redeploys by default. For production use, you need Railway's **Volume** feature (available on Hobby plan).

**For now (free tier):**
- Data resets on each redeploy
- Inactivity tracking rebuilds from Torn API
- Chain/war history lost on redeploy

**To add persistence (Hobby plan $5/month):**
1. Railway dashboard → your service
2. Click "Volumes"
3. Mount path: `/app/data`
4. This persists the `data/` folder across redeploys

### Error: "Railway says app crashed"

**Common causes:**

**Infinite loop in code:**
- Check background tasks (@tasks.loop)
- Ensure no blocking operations

**Unhandled exception:**
- Check Railway logs for traceback
- Add try/except blocks around risky operations

**Out of memory:**
- Railway free tier has memory limits
- Check if bot is loading too much data
- Consider pagination for large datasets

---

## Part 6: Railway Free Tier Limits

**What you get on free tier:**
- ✅ 500 execution hours/month (~16 hours/day)
- ✅ 512 MB RAM
- ✅ 1 GB disk (ephemeral - resets on redeploy)
- ✅ Auto-deploy from GitHub
- ❌ No persistent storage (data/ folder resets)

**What Hobby plan ($5/month) adds:**
- ✅ Unlimited execution hours
- ✅ Persistent volumes (data/ folder survives redeploys)
- ✅ More RAM/CPU

**For 24/7 bot with data persistence:**
- Recommend Hobby plan
- Without it, your bot will:
  - Go offline after 500 hours/month
  - Lose JSON data on every redeploy

---

## Part 7: Verification Checklist

After deployment, verify:

- [ ] Bot shows as **ONLINE** in Discord
- [ ] `/ping` responds
- [ ] `/status` shows data
- [ ] `/chain` works (if chain active)
- [ ] `/war` works (if war active)
- [ ] Inactivity tracking logs appear in Railway logs
- [ ] No error messages in Railway logs
- [ ] Background tasks are running (check logs every 1-2 minutes)

---

## Part 8: Monitoring Your Bot

### Check Bot Status

**In Discord:**
- Bot status (online/offline)
- Response to commands

**In Railway:**
1. Dashboard → your service
2. Click "Deployments"
3. View real-time logs

**Logs to watch for:**
```
Bot is ready!
Inactivity check starting...
Chain update checker started (checks every minute)
War update checker started (checks every minute)
```

### View Logs

**Real-time:**
1. Railway dashboard → Deployments
2. Click active deployment
3. Logs stream live

**Search logs:**
- Use browser Ctrl+F to search
- Look for "ERROR", "WARNING", "Failed"

---

## Summary

**You've successfully:**
1. ✅ Converted bot to use environment variables
2. ✅ Created GitHub repository with code
3. ✅ Deployed to Railway
4. ✅ Set up auto-deploy from GitHub
5. ✅ Verified bot is running 24/7

**To update bot:**
1. Edit code locally
2. Test: `python bot.py`
3. Commit: `git add . && git commit -m "..." && git push origin main`
4. Railway auto-deploys in ~30-45 seconds

**Need help?**
- Check Railway logs first
- Review troubleshooting section
- Verify environment variables are set correctly

Your bot is now live! 🚀
