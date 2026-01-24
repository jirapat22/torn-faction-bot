# Railway Deployment - Quick Start

## What Was Created

I've created these files for Railway deployment:

1. **config_railway.py** - New config that uses environment variables
2. **.env.example** - Template showing required environment variables
3. **.gitignore_railway** - Updated gitignore (allows config.py, blocks .env)
4. **RAILWAY_DEPLOYMENT_GUIDE.md** - Complete step-by-step guide
5. **setup_env.py** - Script to auto-generate .env from your current config

## Before You Start

Your current files:
- ✅ `config.py` - Has your hardcoded secrets (bot token, API key, channel IDs)
- ✅ `bot.py` - Main bot file
- ✅ `requirements.txt` - Dependencies list
- ✅ `.gitignore` - Currently blocks config.py (needs update)

## Quick Setup (5 Minutes)

### Option 1: Automated Setup (Recommended)

```bash
# 1. Backup your current config
copy config.py config_local_backup.py

# 2. Run the setup script to generate .env
python setup_env.py

# 3. Replace config with Railway version
copy config_railway.py config.py

# 4. Update gitignore
copy .gitignore_railway .gitignore

# 5. Test locally
python bot.py
```

If bot starts normally, you're ready for Railway! Stop it (Ctrl+C) and proceed to GitHub setup.

### Option 2: Manual Setup

```bash
# 1. Backup your current config
copy config.py config_local_backup.py

# 2. Replace config with Railway version
copy config_railway.py config.py

# 3. Create .env from example
copy .env.example .env

# 4. Edit .env and fill in your actual values
notepad .env

# 5. Update gitignore
copy .gitignore_railway .gitignore

# 6. Test locally
python bot.py
```

## What Changed

**Old config.py:**
```python
BOT_TOKEN = "MTQ2NDQy..."  # Hardcoded
GUILD_ID = 1464244521318748378  # Hardcoded
```

**New config.py:**
```python
BOT_TOKEN = os.getenv("DISCORD_TOKEN")  # From environment
GUILD_ID = int(os.getenv("GUILD_ID", "0"))  # From environment
```

**Why this matters:**
- ✅ Secrets stay out of GitHub (in .env file)
- ✅ Railway reads from environment variables
- ✅ Same code works locally (uses .env) and on Railway (uses Railway variables)

## Your .env File

This file is created on your PC and contains your secrets:

```env
DISCORD_TOKEN=MTQ2NDQyNzA0MDQxNjU5NTk2OA.G0N3Xz.MJEzOP5u5cIRPGsvNVr9KqpjXV09eGsyiUfre0
GUILD_ID=1464244521318748378
INACTIVITY_ALERTS_CHANNEL_ID=1464252699523416086
...
```

**NEVER commit this file to GitHub!**

The `.gitignore_railway` file blocks it automatically.

## GitHub Setup

```bash
# Initialize git (if not already done)
git init

# Stage all files
git add .

# IMPORTANT: Verify .env is NOT staged
git status

# Commit
git commit -m "Initial commit - Railway ready"

# Create repository on GitHub (make it PRIVATE)
# Then push:
git remote add origin https://github.com/YOUR_USERNAME/torn-faction-bot.git
git branch -M main
git push -u origin main
```

## Railway Setup

1. Go to https://railway.com
2. Login with GitHub
3. New Project → Deploy from GitHub repo
4. Select your bot repository
5. Add environment variables (see RAILWAY_DEPLOYMENT_GUIDE.md Part 3, Step 13)
6. Redeploy
7. Check logs - bot should be online!

## Files to Commit to GitHub

✅ Commit these:
- bot.py
- config.py (new version with os.getenv)
- requirements.txt
- .env.example
- .gitignore (updated version)
- utils/
- features/
- All other code files

❌ DO NOT commit:
- .env (contains secrets)
- config_local_backup.py (has hardcoded secrets)
- data/ folder (will be created by bot)

## Testing Locally vs Railway

**Local (your PC):**
- Bot reads from `.env` file
- Data saved to `data/` folder

**Railway (cloud):**
- Bot reads from Railway environment variables
- Data saved to Railway's ephemeral storage (resets on redeploy)
- For persistent data, upgrade to Hobby plan ($5/month) and add Volume

## Troubleshooting

**Bot won't start locally after changes:**
1. Check .env file exists
2. Check .env has all values filled in
3. Check no typos in .env (no spaces around `=`)

**Bot won't start on Railway:**
1. Check all environment variables are set in Railway dashboard
2. Check Railway logs for specific error
3. Verify DISCORD_TOKEN is correct

**Commands not working:**
1. Wait 2-3 minutes for Discord to sync
2. Restart Discord app
3. Check GUILD_ID matches your server

## Next Steps

1. ✅ Complete local setup (above)
2. ✅ Test bot locally
3. ✅ Push to GitHub
4. ✅ Deploy to Railway
5. ✅ Verify bot is online in Discord

**Full guide:** See RAILWAY_DEPLOYMENT_GUIDE.md for detailed step-by-step instructions.

## Update Workflow

Once deployed, to update your bot:

```bash
# 1. Edit code on your PC
# 2. Test: python bot.py
# 3. Commit and push:
git add .
git commit -m "Fixed bug"
git push origin main

# Railway auto-deploys in ~30-45 seconds
```

## Important Notes

- `.env` file stays on your PC only
- Railway uses Variables (set in dashboard)
- Same code works locally and on Railway
- Auto-deploy from GitHub (no manual Railway clicks)
- ~30 second downtime during redeploys

## Questions?

Check RAILWAY_DEPLOYMENT_GUIDE.md for:
- Complete setup instructions
- Environment variable list
- Troubleshooting guide
- Railway free tier limits
- Verification checklist

---

**Ready to deploy? Follow the steps above or see RAILWAY_DEPLOYMENT_GUIDE.md**
