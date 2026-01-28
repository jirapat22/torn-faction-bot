"""
Torn API integration
Handles all API calls to Torn.com
"""

import aiohttp
import time
from config import TORN_API_KEY, TORN_FACTION_ID, TORN_API_CACHE_SECONDS

# API cache to prevent rate limiting
# Format: {cache_key: {'data': data, 'timestamp': timestamp}}
api_cache = {}

# Torn API base URL
TORN_API_BASE = "https://api.torn.com"


async def _make_api_request(endpoint, params=None):
    """
    Internal function to make API requests with caching and error handling

    Args:
        endpoint: API endpoint (e.g., 'faction', 'user')
        params: Dict of query parameters

    Returns:
        Dict with API response or None if error
    """
    if params is None:
        params = {}

    # Create cache key before adding API key (avoid key in logs/cache strings)
    cache_key = f"{endpoint}_{str(params)}"

    # Add API key to params
    params['key'] = TORN_API_KEY

    # Check cache
    if cache_key in api_cache:
        cached = api_cache[cache_key]
        age = time.time() - cached['timestamp']
        if age < TORN_API_CACHE_SECONDS:
            print(f'Using cached data for {endpoint} (age: {age:.1f}s)')
            return cached['data']

    # Make API request
    url = f"{TORN_API_BASE}/{endpoint}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    print(f'Torn API error: HTTP {response.status}')
                    return None

                data = await response.json()

                # Check for API errors
                if 'error' in data:
                    error_code = data['error'].get('code', 'unknown')
                    error_msg = data['error'].get('error', 'Unknown error')
                    print(f'Torn API error {error_code}: {error_msg}')
                    return None

                # Cache the response
                api_cache[cache_key] = {
                    'data': data,
                    'timestamp': time.time()
                }

                return data

    except aiohttp.ClientError as e:
        print(f'Network error accessing Torn API: {e}')
        return None
    except Exception as e:
        print(f'Unexpected error with Torn API: {e}')
        return None


async def fetch_faction_members():
    """
    Fetch faction member data including last action timestamps

    Returns:
        Dict of {torn_id: member_data} or None if error
        member_data includes: name, level, last_action, status, etc.
    """
    data = await _make_api_request(
        f'faction/{TORN_FACTION_ID}',
        {'selections': 'basic'}
    )

    if not data:
        return None

    members = data.get('members', {})
    print(f'Fetched data for {len(members)} faction members from Torn API')
    return members


async def fetch_faction_data(selections='basic'):
    """
    Fetch general faction data from Torn API

    Args:
        selections: Comma-separated API selections (basic, chainreport, wars, etc.)

    Returns:
        Dict with faction data or None if error
    """
    data = await _make_api_request(
        f'faction/{TORN_FACTION_ID}',
        {'selections': selections}
    )

    return data


async def fetch_chain_data():
    """
    Fetch current chain data

    Returns:
        Dict with chain data or None if error
    """
    data = await fetch_faction_data('chain')

    if not data:
        return None

    chain = data.get('chain', {})
    return chain


async def fetch_war_data():
    """
    Fetch current war data

    Returns:
        Dict with war data or None if error
    """
    data = await fetch_faction_data('wars')

    if not data:
        return None

    wars = data.get('wars', {})
    return wars


async def verify_api_key():
    """
    Verify that the Torn API key is valid

    Returns:
        True if valid, False if invalid
    """
    data = await _make_api_request(f'faction/{TORN_FACTION_ID}', {'selections': 'basic'})
    return data is not None


async def get_member_last_action(torn_id):
    """
    Get a specific member's last action timestamp

    Args:
        torn_id: Torn user ID

    Returns:
        Unix timestamp of last action, or None if error
    """
    members = await fetch_faction_members()

    if not members:
        return None

    member = members.get(str(torn_id))
    if not member:
        return None

    return member.get('last_action', {}).get('timestamp')


async def fetch_player_data(torn_id):
    """
    Fetch player data for any Torn player (for verification)

    Args:
        torn_id: Torn user ID

    Returns:
        Dict with player data or None if error
        Includes: player_id, name, level, faction info, last_action
    """
    data = await _make_api_request(
        f'user/{torn_id}',
        {'selections': 'profile'}
    )

    return data
