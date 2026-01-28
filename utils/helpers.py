"""
Helper functions for the Torn Faction Bot
Includes JSON file handling, embed creation, etc.
"""

import json
import os
import time as _time
from datetime import datetime, timezone
import discord
from config import EMBED_COLOR_INFO, EMBED_COLOR_WARNING, EMBED_COLOR_CRITICAL, EMBED_COLOR_SUCCESS


def ensure_data_directories():
    """Create data directories if they don't exist"""
    directories = ['data', 'utils', 'features']
    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory)
            print(f'Created directory: {directory}')


def load_json(file_path, default=None):
    """
    Load data from a JSON file
    Returns default value if file doesn't exist or is corrupt
    """
    if default is None:
        default = {}

    try:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
        else:
            # Create the file with default data
            save_json(file_path, default)
            return default
    except json.JSONDecodeError:
        print(f'Warning: {file_path} is corrupt, resetting to default')
        save_json(file_path, default)
        return default
    except Exception as e:
        print(f'Error loading {file_path}: {e}')
        return default


def save_json(file_path, data):
    """
    Save data to a JSON file
    Creates the file if it doesn't exist
    """
    try:
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f'Error saving {file_path}: {e}')
        return False


def create_embed(title, description, color=EMBED_COLOR_INFO, fields=None):
    """
    Create a Discord embed with consistent formatting

    Args:
        title: Embed title
        description: Embed description
        color: Embed color (use EMBED_COLOR_* constants)
        fields: List of dicts with 'name', 'value', 'inline' keys

    Returns:
        discord.Embed object
    """
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )

    if fields:
        for field in fields:
            embed.add_field(
                name=field.get('name', 'Field'),
                value=field.get('value', 'No value'),
                inline=field.get('inline', False)
            )

    return embed


def format_timestamp(timestamp, format_type='F', show_tct=False):
    """
    Convert Unix timestamp to Discord timestamp format
    Automatically shows in user's local timezone

    Args:
        timestamp: Unix timestamp (seconds since epoch)
        format_type: Discord timestamp format
            't' = Short Time (16:20)
            'T' = Long Time (16:20:30)
            'd' = Short Date (20/04/2021)
            'D' = Long Date (20 April 2021)
            'f' = Short Date/Time (20 April 2021 16:20)
            'F' = Long Date/Time (Tuesday, 20 April 2021 16:20) [DEFAULT]
            'R' = Relative Time (2 months ago)
        show_tct: If True, also show TCT (Torn City Time = UTC) in parentheses

    Returns:
        Formatted Discord timestamp string
    """
    discord_time = f'<t:{int(timestamp)}:{format_type}>'

    if show_tct:
        # Convert to TCT (UTC)
        from datetime import datetime
        utc_time = datetime.utcfromtimestamp(int(timestamp))
        tct_str = utc_time.strftime('%H:%M:%S TCT')
        return f'{discord_time} ({tct_str})'

    return discord_time


def format_timestamp_with_tct(timestamp, format_type='f'):
    """
    Format timestamp showing both local time (Discord auto-convert) and TCT

    Args:
        timestamp: Unix timestamp
        format_type: Discord timestamp format (default 'f' for short date/time)

    Returns:
        String like "<t:timestamp:f> (16:20:30 TCT)"
    """
    return format_timestamp(timestamp, format_type, show_tct=True)


def get_days_since(timestamp):
    """
    Calculate days since a given timestamp

    Args:
        timestamp: Unix timestamp (seconds since epoch)

    Returns:
        Number of days as integer
    """
    now = _time.time()
    seconds_diff = now - timestamp
    return int(seconds_diff / 86400)  # 86400 seconds in a day


def get_current_timestamp():
    """
    Get current Unix timestamp

    Returns:
        Current timestamp as integer
    """
    return int(datetime.now().timestamp())
