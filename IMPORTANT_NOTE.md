# ⚠️ IMPORTANT: Check bot.py Before Restarting

## Configuration Issue

In `bot.py` line 91, the `target_hour` is currently set to **20** (8 PM):

```python
target_hour = 20  # 12 PM
```

### Fix This Before Restart

1. Open `bot.py`
2. Find line 91
3. Change it to:
   ```python
   target_hour = 12  # 12 PM
   ```
4. Save the file

### Why This Matters

The inactivity check should run at **12:00 PM (noon)** daily, not 8:00 PM.

If you want to test it earlier, you can temporarily set it to your current hour + 1, then change it back to 12 after testing.

---

## After Fixing

1. Save `bot.py` with `target_hour = 12`
2. Restart bot:
   ```bash
   python bot.py
   ```
3. You should see:
   ```
   Daily inactivity check will start in X hours (at 12:00 PM)
   ```

---

This is the **only change** you need to make before restarting for Phase 3!
