# Ascended Punks Discord - Permissions Guide

This document outlines the recommended Discord permissions for roles and channels to work properly with the ApexPunk bot.

---

## Role Hierarchy (Top to Bottom)

| Role | Purpose | Notes |
|------|---------|-------|
| **Leader** | Faction leader | Full admin access |
| **Management** | Officers/admins | Can use all bot management commands |
| **Discord** | Technical admin | Special role for command testing |
| **Legend** | Senior members | Regular verified member access |
| **Punk** | Regular members | Regular verified member access |
| **Bozo** | New faction members | Regular verified member access |
| **Ascended punks** | Verified role | Given after /register - grants channel access |
| **chainwatcher** | Chain notifications | Manual role for chain pings |
| **@everyone** | Unverified | Can only see welcome-pit |

---

## Role Permissions

### Leader
```
Administrator: ✅ (gives all permissions)
```

### Management
```
General:
- View Channels: ✅
- Manage Channels: ❌
- Manage Roles: ✅ (to assign verified role)
- Manage Server: ❌
- View Audit Log: ✅
- Manage Webhooks: ❌
- Manage Server: ❌

Membership:
- Create Invite: ✅
- Change Nickname: ✅
- Manage Nicknames: ✅
- Kick Members: ✅
- Ban Members: ✅
- Timeout Members: ✅

Text:
- Send Messages: ✅
- Send Messages in Threads: ✅
- Create Public Threads: ✅
- Create Private Threads: ✅
- Embed Links: ✅
- Attach Files: ✅
- Add Reactions: ✅
- Use External Emoji: ✅
- Use External Stickers: ✅
- Mention @everyone: ✅
- Manage Messages: ✅
- Manage Threads: ✅
- Read Message History: ✅
- Send TTS: ❌
- Use Application Commands: ✅

Voice:
- Connect: ✅
- Speak: ✅
- Mute Members: ✅
- Deafen Members: ✅
- Move Members: ✅
```

### Discord (Technical Admin)
```
Same as Management, plus:
- Administrator: ✅ (for testing)
```

### Legend / Punk / Bozo
```
General:
- View Channels: ✅

Text:
- Send Messages: ✅
- Send Messages in Threads: ✅
- Embed Links: ✅
- Attach Files: ✅
- Add Reactions: ✅
- Use External Emoji: ✅
- Read Message History: ✅
- Use Application Commands: ✅

Voice:
- Connect: ✅
- Speak: ✅
```

### Ascended punks (Verified Role)
```
General:
- View Channels: ✅

Text:
- Send Messages: ✅
- Send Messages in Threads: ✅
- Embed Links: ✅
- Attach Files: ✅
- Add Reactions: ✅
- Use External Emoji: ✅
- Read Message History: ✅
- Use Application Commands: ✅

Voice:
- Connect: ✅
- Speak: ✅
```

### chainwatcher
```
(No special permissions - just used for pings)
```

### @everyone (Unverified)
```
General:
- View Channels: ❌ (controlled per channel)

Text:
- Send Messages: ❌
- Read Message History: ❌
- Use Application Commands: ✅ (only for /register)
```

---

## Channel Permissions

### Management Category

#### #management (1464252528677097618)
| Role | View | Send | History | Commands |
|------|------|------|---------|----------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Management | ✅ | ✅ | ✅ | ✅ |
| @everyone | ❌ | ❌ | ❌ | ❌ |

#### #announcement-event-posting (1464591013111791798)
| Role | View | Send | History | Commands |
|------|------|------|---------|----------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Management | ✅ | ✅ | ✅ | ✅ |
| @everyone | ❌ | ❌ | ❌ | ❌ |

#### #inactivity-check (1464252699523416086)
| Role | View | Send | History | Commands |
|------|------|------|---------|----------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Management | ✅ | ✅ | ✅ | ✅ |
| @everyone | ❌ | ❌ | ❌ | ❌ |

#### #war-reports (1464526256983904342)
| Role | View | Send | History | Commands |
|------|------|------|---------|----------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Management | ✅ | ✅ | ✅ | ✅ |
| @everyone | ❌ | ❌ | ❌ | ❌ |

#### #command-testing (1464245517725860027)
| Role | View | Send | History | Commands |
|------|------|------|---------|----------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Discord | ✅ | ✅ | ✅ | ✅ |
| @everyone | ❌ | ❌ | ❌ | ❌ |

---

### Text Channels Category (1464244523793256570)

**Applies to:** punk-plaza, off-torn-duty, chain, war-room, organised-crimes, ascended-vault, leave-notices

| Role | View | Send | History | Commands |
|------|------|------|---------|----------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Management | ✅ | ✅ | ✅ | ✅ |
| Legend | ✅ | ✅ | ✅ | ✅ |
| Punk | ✅ | ✅ | ✅ | ✅ |
| Bozo | ✅ | ✅ | ✅ | ✅ |
| Ascended punks | ✅ | ✅ | ✅ | ✅ |
| @everyone | ❌ | ❌ | ❌ | ❌ |

---

### Important Category

#### #welcome-pit (WELCOME_CHANNEL_ID)
**Purpose:** New members land here, see welcome message, use /register

| Role | View | Send Messages | History | Use Commands |
|------|------|---------------|---------|--------------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Management | ✅ | ✅ | ✅ | ✅ |
| Ascended punks | ✅ | ❌ | ✅ | ❌ |
| @everyone | ✅ | ❌ | ✅ | ✅ (/register only) |

**Special Setup for @everyone:**
- View Channel: ✅
- Send Messages: ❌
- Read Message History: ✅
- Use Application Commands: ✅
- Add Reactions: ❌
- Embed Links: ❌
- Attach Files: ❌

#### #faction-edicts (Rules Channel)
**Purpose:** View only for rules - no typing

| Role | View | Send | History | Commands |
|------|------|------|---------|----------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Management | ✅ | ✅ | ✅ | ✅ |
| Ascended punks | ✅ | ❌ | ✅ | ❌ |
| @everyone | ❌ | ❌ | ❌ | ❌ |

#### #commands-the-punk
**Purpose:** Command reference - view only

| Role | View | Send | History | Commands |
|------|------|------|---------|----------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Management | ✅ | ✅ | ✅ | ✅ |
| Ascended punks | ✅ | ❌ | ✅ | ❌ |
| @everyone | ❌ | ❌ | ❌ | ❌ |

---

### Announcement Category

#### #ascended-broadcasts
| Role | View | Send | History | Commands |
|------|------|------|---------|----------|
| Leader | ✅ | ✅ | ✅ | ✅ |
| Management | ✅ | ✅ | ✅ | ✅ |
| Ascended punks | ✅ | ❌ | ✅ | ❌ |
| @everyone | ❌ | ❌ | ❌ | ❌ |

---

## How to Set Up in Discord

### Step 1: Set Category Permissions
1. Right-click category → Edit Category → Permissions
2. Set @everyone to deny all
3. Add roles with appropriate permissions
4. Channels inherit from category by default

### Step 2: Override for Specific Channels
For channels that need different permissions than their category:
1. Right-click channel → Edit Channel → Permissions
2. Click the role to modify
3. Set overrides (✅ allow, ❌ deny, ➖ inherit)

### Step 3: Welcome-pit Special Setup
```
@everyone:
  View Channel: ✅
  Send Messages: ❌
  Read Message History: ✅
  Use Application Commands: ✅
  Add Reactions: ❌

Ascended punks:
  Send Messages: ❌
  Use Application Commands: ❌
```

### Step 4: Set Verified Role in Bot
Run in Discord:
```
/verify setrole role:@Ascended punks
```

---

## Troubleshooting

### "I can't see channels after registering"
- Check that user has "Ascended punks" role
- Run `/verify fixrole` to give role to registered users missing it

### "Commands don't work"
- Ensure "Use Application Commands" is enabled for that role in that channel
- Bot needs "applications.commands" scope

### "Welcome message not appearing"
- Set WELCOME_CHANNEL_ID in Railway environment variables
- Bot needs "Send Messages" permission in welcome-pit

### "Bot can't rename users"
- Bot role must be ABOVE the user's highest role
- Can't rename server owner

### "Bot can't give roles"
- Bot role must be ABOVE the role it's trying to give
- Bot needs "Manage Roles" permission

---

## Railway Environment Variables Needed

```
GUILD_ID=your_server_id
WELCOME_CHANNEL_ID=welcome_pit_channel_id
RULES_CHANNEL_ID=faction_edicts_channel_id
GENERAL_CHANNEL_ID=punk_plaza_channel_id
MANAGEMENT_ROLE_IDS=leader_role_id,management_role_id
INACTIVITY_ALERTS_CHANNEL_ID=inactivity_check_channel_id
WAR_CHANNEL_ID=war_room_channel_id
WAR_MANAGEMENT_CHANNEL_ID=war_reports_channel_id
CHAINING_CHANNEL_ID=chain_channel_id
ANNOUNCEMENTS_CHANNEL_ID=ascended_broadcasts_channel_id
```

---

*Last updated: January 2026*
