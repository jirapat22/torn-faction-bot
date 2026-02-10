"""
Torn API integration
Handles all API calls to Torn.com
"""

import aiohttp
import time
from config import TORN_API_KEY, TORN_PERSONAL_API_KEY, TORN_FACTION_ID, TORN_API_CACHE_SECONDS

# API cache to prevent rate limiting
# Format: {cache_key: {'data': data, 'timestamp': timestamp}}
api_cache = {}

# Torn API base URL
TORN_API_BASE = "https://api.torn.com"

# Reusable aiohttp session (created on first request)
_session = None


async def _get_session():
    """Get or create a reusable aiohttp session"""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    return _session


def _prune_cache():
    """Remove expired entries from API cache"""
    now = time.time()
    expired = [k for k, v in api_cache.items() if now - v['timestamp'] >= TORN_API_CACHE_SECONDS]
    for k in expired:
        del api_cache[k]


async def _make_api_request(endpoint, params=None, use_personal_key=False):
    """
    Internal function to make API requests with caching and error handling

    Args:
        endpoint: API endpoint (e.g., 'faction', 'user')
        params: Dict of query parameters
        use_personal_key: If True, use TORN_PERSONAL_API_KEY instead of faction key

    Returns:
        Dict with API response or None if error
    """
    if params is None:
        params = {}

    # Create cache key before adding API key (avoid key in logs/cache strings)
    cache_key = f"{endpoint}_{str(params)}"

    # Add API key to params (use personal key for user lookups if configured)
    if use_personal_key:
        if TORN_PERSONAL_API_KEY:
            params['key'] = TORN_PERSONAL_API_KEY
        else:
            print(f'WARNING: TORN_PERSONAL_API_KEY not set, falling back to faction key for {endpoint}')
            params['key'] = TORN_API_KEY
    else:
        params['key'] = TORN_API_KEY

    # Check cache
    if cache_key in api_cache:
        cached = api_cache[cache_key]
        age = time.time() - cached['timestamp']
        if age < TORN_API_CACHE_SECONDS:
            print(f'Using cached data for {endpoint} (age: {age:.1f}s)')
            return cached['data']

    # Periodically prune expired cache entries
    if len(api_cache) > 50:
        _prune_cache()

    # Make API request
    url = f"{TORN_API_BASE}/{endpoint}"

    try:
        session = await _get_session()
        async with session.get(url, params=params) as response:
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


async def fetch_faction_data_v2(selections='wars'):
    """
    Fetch faction data from Torn API v2 endpoint.
    Some selections (like 'wars') are only available in v2.

    Args:
        selections: Comma-separated API selections

    Returns:
        Dict with faction data or None if error
    """
    data = await _make_api_request(
        f'v2/faction/{TORN_FACTION_ID}',
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


async def fetch_faction_attacks(from_ts=None, to_ts=None, sort=None):
    """
    Fetch faction attacks with optional time range and sort order.
    Used for paginating through large numbers of attacks.

    Args:
        from_ts: Unix timestamp to start from (inclusive)
        to_ts: Unix timestamp to end at (inclusive)
        sort: Sort order - 'asc' or 'desc' (default API behavior is desc)

    Returns:
        Dict of {attack_id: attack_data} or empty dict if error
    """
    params = {'selections': 'attacks'}
    if from_ts is not None:
        params['from'] = int(from_ts)
    if to_ts is not None:
        params['to'] = int(to_ts)
    if sort is not None:
        params['sort'] = sort

    data = await _make_api_request(
        f'faction/{TORN_FACTION_ID}',
        params
    )

    if not data:
        return {}

    return data.get('attacks', {})


async def fetch_any_faction_data(faction_id, selections='basic', use_personal_key=True):
    """
    Fetch data for any faction (not just the bot's own faction).
    Uses the shared session, caching, and rate-limit handling.

    Args:
        faction_id: Torn faction ID to query
        selections: API selections (e.g. 'basic', 'basic,rankedwars')
        use_personal_key: If True, use personal API key (default True)

    Returns:
        Dict with faction data or None if error
    """
    data = await _make_api_request(
        f'faction/{faction_id}',
        {'selections': selections},
        use_personal_key=use_personal_key
    )
    return data


async def fetch_player_data(torn_id, selections='profile', use_personal_key=True):
    """
    Fetch player data for any Torn player (for verification, stakeout, etc.)

    Args:
        torn_id: Torn user ID
        selections: API selections (default 'profile', can use 'profile,travel' etc.)
        use_personal_key: If True, use personal API key for user lookups (default True)

    Returns:
        Dict with player data or None if error
        Includes: player_id, name, level, faction info, last_action
    """
    data = await _make_api_request(
        f'user/{torn_id}',
        {'selections': selections},
        use_personal_key=use_personal_key
    )

    return data
