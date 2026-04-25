"""
Timestamp utilities for Discord bot
Handles converting between human-readable times and Unix timestamps
"""

from datetime import datetime, timedelta, timezone
import time


def now_timestamp():
    """Get current Unix timestamp"""
    return int(time.time())


def datetime_to_timestamp(dt):
    """
    Convert datetime object to Unix timestamp

    Args:
        dt: datetime object

    Returns:
        Unix timestamp (int)
    """
    return int(dt.timestamp())


def timestamp_to_datetime(timestamp):
    """
    Convert Unix timestamp to datetime object

    Args:
        timestamp: Unix timestamp (int)

    Returns:
        datetime object
    """
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def hours_from_now(hours):
    """
    Get Unix timestamp for X hours from now

    Args:
        hours: Number of hours in the future

    Returns:
        Unix timestamp (int)
    """
    future_time = datetime.now(timezone.utc) + timedelta(hours=hours)
    return datetime_to_timestamp(future_time)


def days_from_now(days):
    """
    Get Unix timestamp for X days from now

    Args:
        days: Number of days in the future

    Returns:
        Unix timestamp (int)
    """
    future_time = datetime.now(timezone.utc) + timedelta(days=days)
    return datetime_to_timestamp(future_time)


def format_discord_timestamp(timestamp, style='F'):
    """
    Format Unix timestamp for Discord

    Args:
        timestamp: Unix timestamp
        style: Discord timestamp style
            't' = Short Time
            'T' = Long Time
            'd' = Short Date
            'D' = Long Date
            'f' = Short Date/Time
            'F' = Long Date/Time (default)
            'R' = Relative Time

    Returns:
        Formatted string for Discord
    """
    return f'<t:{int(timestamp)}:{style}>'


def seconds_between(timestamp1, timestamp2):
    """
    Calculate seconds between two timestamps

    Args:
        timestamp1: First Unix timestamp
        timestamp2: Second Unix timestamp

    Returns:
        Difference in seconds (int)
    """
    return abs(timestamp1 - timestamp2)


def days_between(timestamp1, timestamp2):
    """
    Calculate days between two timestamps

    Args:
        timestamp1: First Unix timestamp
        timestamp2: Second Unix timestamp

    Returns:
        Difference in days (int)
    """
    return seconds_between(timestamp1, timestamp2) // 86400
