"""
Torn API integration
Handles all API calls to Torn.com
"""

import aiohttp
import asyncio
import time
from collections import deque
from config import (
    TORN_API_KEY,
    TORN_PERSONAL_API_KEY,
    TORN_FACTION_ID,
    TORN_API_CACHE_SECONDS,
    TORN_API_MAX_REQUESTS_PER_MINUTE,
)

import logging

logger = logging.getLogger(__name__)
# API cache to prevent rate limiting
# Format: {cache_key: {'data': data, 'timestamp': timestamp}}
api_cache = {}

# Separate FFScouter cache — keyed by sorted target ID list
# Format: {cache_key: {'data': data, 'timestamp': timestamp}}
_ffscouter_cache = {}
FFSCOUTER_CACHE_SECONDS = 900  # 15 minutes — estimates don't change fast

# How recent a v1 (legacy OC1) crime record must be for the fallback to be
# treated as live data. An OC2 faction's v1 feed keeps returning 200 with
# long-frozen history, which would otherwise masquerade as "no one is in a
# crime right now". See fetch_faction_crimes.
V1_CRIMES_MAX_AGE_SECONDS = 14 * 86400

# Torn API base URL
TORN_API_BASE = "https://api.torn.com"

# Reusable aiohttp session (created on first request)
_session = None

# Rolling-window rate limiter for Torn API
_rate_window = deque()
_rate_lock = asyncio.Lock()


async def _get_session():
    """Get or create a reusable aiohttp session"""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    return _session


async def close_session():
    """Close the shared aiohttp session (called on bot shutdown)"""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def _wait_rate_limit():
    """
    Block until a Torn API request slot is available within the configured
    per-minute budget. Uses a rolling 60s window.
    """
    if TORN_API_MAX_REQUESTS_PER_MINUTE <= 0:
        return
    async with _rate_lock:
        now = time.monotonic()
        # Drop entries older than 60s
        while _rate_window and now - _rate_window[0] >= 60:
            _rate_window.popleft()
        if len(_rate_window) >= TORN_API_MAX_REQUESTS_PER_MINUTE:
            sleep_for = 60 - (now - _rate_window[0])
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            now = time.monotonic()
            while _rate_window and now - _rate_window[0] >= 60:
                _rate_window.popleft()
        _rate_window.append(now)


def _prune_cache():
    """Remove expired entries from API cache"""
    now = time.time()
    expired = [k for k, v in api_cache.items() if now - v['timestamp'] >= TORN_API_CACHE_SECONDS]
    for k in expired:
        del api_cache[k]


async def _make_api_request(endpoint, params=None, use_personal_key=False, cache_seconds=None):
    """
    Internal function to make API requests with caching and error handling

    Args:
        endpoint: API endpoint (e.g., 'faction', 'user')
        params: Dict of query parameters
        use_personal_key: If True, use TORN_PERSONAL_API_KEY instead of faction key
        cache_seconds: Override cache TTL for this request (0 = bypass cache).
                       None uses the global TORN_API_CACHE_SECONDS.

    Returns:
        Dict with API response or None if error
    """
    if params is None:
        params = {}

    # Create cache key before adding API key (avoid key in logs/cache strings)
    key_type = 'personal' if use_personal_key else 'faction'
    cache_key = f"{endpoint}_{key_type}_{str(params)}"

    # Add API key to params (use personal key for user lookups if configured)
    if use_personal_key:
        if TORN_PERSONAL_API_KEY:
            params['key'] = TORN_PERSONAL_API_KEY
        else:
            logger.warning(f'TORN_PERSONAL_API_KEY not set, falling back to faction key for {endpoint}')
            params['key'] = TORN_API_KEY
    else:
        params['key'] = TORN_API_KEY

    # Check cache
    ttl = TORN_API_CACHE_SECONDS if cache_seconds is None else cache_seconds
    if ttl > 0 and cache_key in api_cache:
        cached = api_cache[cache_key]
        age = time.time() - cached['timestamp']
        if age < ttl:
            logger.info(f'Using cached data for {endpoint} (age: {age:.1f}s)')
            return cached['data']

    # Periodically prune expired cache entries
    if len(api_cache) > 50:
        _prune_cache()

    # Make API request
    url = f"{TORN_API_BASE}/{endpoint}"

    try:
        await _wait_rate_limit()
        session = await _get_session()
        async with session.get(url, params=params) as response:
            if response.status != 200:
                logger.error(f'Torn API error: HTTP {response.status}')
                return None

            data = await response.json()

            # Check for API errors
            if 'error' in data:
                error_code = data['error'].get('code', 'unknown')
                error_msg = data['error'].get('error', 'Unknown error')
                logger.error(f'Torn API error {error_code}: {error_msg}')
                return None

            # Cache the response
            api_cache[cache_key] = {
                'data': data,
                'timestamp': time.time()
            }

            return data

    except aiohttp.ClientError as e:
        logger.error(f'Network error accessing Torn API: {e}')
        return None
    except Exception as e:
        logger.error(f'Unexpected error with Torn API: {e}')
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
    logger.info(f'Fetched data for {len(members)} faction members from Torn API')
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


async def fetch_any_faction_data(faction_id, selections='basic', use_personal_key=True,
                                 cache_seconds=None):
    """
    Fetch data for any faction (not just the bot's own faction).
    Uses the shared session, caching, and rate-limit handling.

    Args:
        faction_id: Torn faction ID to query
        selections: API selections (e.g. 'basic', 'basic,rankedwars')
        use_personal_key: If True, use personal API key (default True)
        cache_seconds: Override cache TTL (0 = always fetch fresh, for fast polling)

    Returns:
        Dict with faction data or None if error
    """
    data = await _make_api_request(
        f'faction/{faction_id}',
        {'selections': selections},
        use_personal_key=use_personal_key,
        cache_seconds=cache_seconds
    )
    return data


async def fetch_player_data(torn_id, selections='profile', use_personal_key=True,
                            cache_seconds=None):
    """
    Fetch player data for any Torn player (for verification, stakeout, etc.)

    Args:
        torn_id: Torn user ID
        selections: API selections (default 'profile', can use 'profile,travel' etc.)
        use_personal_key: If True, use personal API key for user lookups (default True)
        cache_seconds: Override cache TTL (0 = always fetch fresh)

    Returns:
        Dict with player data or None if error
        Includes: player_id, name, level, faction info, last_action
    """
    data = await _make_api_request(
        f'user/{torn_id}',
        {'selections': selections},
        use_personal_key=use_personal_key,
        cache_seconds=cache_seconds
    )

    return data


async def fetch_user_discord(torn_id):
    """
    Fetch Torn's official Torn<->Discord account link for a player.

    Authoritative: the player establishes this link through Torn itself, so
    unlike a self-set Discord nickname it cannot be spoofed.

    Returns:
        Discord user ID as a string, or '' if the player hasn't linked one.
    """
    data = await _make_api_request(f'user/{torn_id}', {'selections': 'discord'})
    if not data:
        return ''
    return str((data.get('discord') or {}).get('discordID') or '')


async def fetch_user_messages():
    """
    Fetch the API key owner's last 100 personal messages (includes
    'Faction newsletter' type entries — title/sender/timestamp only, no body).

    Returns:
        Dict of {message_id: {timestamp, ID, name, type, title, seen, read}}
    """
    data = await _make_api_request('user', {'selections': 'messages'})
    if not data:
        return {}
    return data.get('messages', {})


async def fetch_faction_crimes():
    """
    Fetch and normalize faction organized crimes.

    v2 (OC2) is authoritative. The v1 fallback exists only for factions still
    on legacy OC1 and is accepted ONLY if it actually looks live — for an OC2
    faction, v1 keeps answering 200 with ancient OC1 records (measured: newest
    585 days old, zero in progress). Serving those as current would leave the
    "who is mid-crime" set empty and flag every participating member at once,
    which is the same failure as an outage but invisible, since the list comes
    back populated. Better to report None and skip the OC section.

    Returns:
        List of normalized crime dicts:
        - 'completed': bool
        - 'completion_ts': float (Unix timestamp, 0 if active)
        - 'participants': list of torn_id strings

        Or None if the API could not be reached at all. That is deliberately
        distinct from [] ("the API answered; there are no crimes"): callers
        must not read an outage as "nobody is currently in an OC", or every
        member mid-crime loses their in-progress protection at once.
    """
    normalized = []
    reached = False
    # Whether v2 answered in the shape we know how to read. An empty list is a
    # valid "no crimes right now"; a missing key or a different type is not, and
    # must not quietly degrade into "nobody is in a crime".
    v2_shape_ok = False

    # Try v2 first (Torn OC2 system — slots-based)
    data = await fetch_faction_data_v2('crimes')
    if data is not None:
        reached = True
        if isinstance(data.get('crimes'), list):
            v2_shape_ok = True
        else:
            logger.error('Torn v2 crimes field is %s, not a list — response shape changed; '
                         'treating as unusable rather than as "no crimes"',
                         type(data.get('crimes')).__name__)
    if data and isinstance(data.get('crimes'), list) and data['crimes']:
        for crime in data['crimes']:
            if not isinstance(crime, dict):
                continue
            executed_at = crime.get('executed_at')
            completed = executed_at is not None
            completion_ts = float(executed_at) if completed else 0.0
            # v2 uses 'slots', each with a nested 'user' object (or None if unfilled)
            slots = crime.get('slots', [])
            participants = [
                str(s['user']['id'])
                for s in (slots if isinstance(slots, list) else [])
                if isinstance(s, dict) and isinstance(s.get('user'), dict) and s['user'].get('id')
            ]
            normalized.append({
                'completed': completed,
                'completion_ts': completion_ts,
                'participants': participants,
            })
        if normalized:
            return normalized

    # Fall back to v1 (legacy OC1 system) — only trusted if it looks live
    v1_normalized = []
    data = await fetch_faction_data('crimes')
    if data:
        for crime_data in (data.get('crimes') or {}).values():
            if not isinstance(crime_data, dict):
                continue
            time_completed = crime_data.get('time_completed', 0)
            raw_parts = crime_data.get('participants', [])
            if isinstance(raw_parts, dict):
                raw_parts = [{k: v} for k, v in raw_parts.items()]
            participants = []
            for p in (raw_parts if isinstance(raw_parts, list) else []):
                if isinstance(p, dict):
                    participants.extend(str(tid) for tid in p.keys())
            v1_normalized.append({
                'completed': bool(time_completed),
                'completion_ts': float(time_completed) if time_completed else 0.0,
                'participants': participants,
            })

    if v1_normalized:
        # Accept v1 only with evidence it reflects the present: an in-progress
        # crime, or a completion inside the staleness window. An OC2 faction's
        # v1 feed is frozen history and must never stand in for live data.
        newest = max((c['completion_ts'] for c in v1_normalized), default=0.0)
        has_in_progress = any(not c['completed'] for c in v1_normalized)
        if has_in_progress or (time.time() - newest) <= V1_CRIMES_MAX_AGE_SECONDS:
            return v1_normalized
        logger.error(
            'Ignoring v1 crimes fallback: newest record is %.0f days old with no crime in '
            'progress, so this is stale OC1 history, not live data. Reporting an outage '
            'instead of emptying the in-progress set.' % ((time.time() - newest) / 86400)
        )

    if not reached:
        logger.error('Torn crimes fetch failed on both v2 and v1 — returning None so callers '
                     'can tell an outage apart from "no active crimes"')
        return None

    if not v2_shape_ok:
        # v2 answered but not in a shape we could read, and v1 gave nothing
        # usable either. Returning [] here would read as "no one is in a crime".
        logger.error('No usable crimes data (v2 shape unreadable, no live v1 fallback) — '
                     'reporting an outage rather than an empty crime list')
        return None

    return normalized


async def fetch_ffscouter_stats(torn_ids: list) -> dict:
    """
    Fetch estimated battle stats for a list of Torn player IDs from FFScouter.

    Args:
        torn_ids: List of Torn player IDs (integers or strings). Max 205 per call.

    Returns:
        Dict of {torn_id_str: {'bs_estimate': int, 'bs_estimate_human': str,
                               'fair_fight': float, 'last_updated': int}}
        Empty dict if FFScouter key not configured or request fails.
    """
    from config import FFSCOUTER_API_KEY
    if not FFSCOUTER_API_KEY:
        logger.info('FFScouter: FFSCOUTER_API_KEY not set — skipping stat lookup')
        return {}

    if not torn_ids:
        return {}

    # Cap at 205 per request (API limit)
    capped_ids = torn_ids[:205]
    targets = ','.join(str(tid) for tid in capped_ids)

    # Check FFScouter cache (keyed by sorted target list)
    cache_key = ','.join(sorted(str(tid) for tid in capped_ids))
    cached = _ffscouter_cache.get(cache_key)
    if cached and time.time() - cached['timestamp'] < FFSCOUTER_CACHE_SECONDS:
        age = time.time() - cached['timestamp']
        logger.info(f'FFScouter: using cached data (age: {age:.1f}s, {len(cached["data"])} players)')
        return cached['data']

    try:
        session = await _get_session()
        url = 'https://ffscouter.com/api/v1/get-stats'
        params = {'key': FFSCOUTER_API_KEY, 'targets': targets}

        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status != 200:
                logger.error(f'FFScouter API error: HTTP {response.status}')
                return {}

            data = await response.json()

            # Handle error responses
            if isinstance(data, dict) and 'error' in data:
                logger.error(f'FFScouter error: {data["error"]}')
                return {}

            # Normalise to {torn_id_str: player_obj}
            result = {}
            players = data if isinstance(data, list) else data.get('data', data.get('players', []))
            if isinstance(players, list):
                for p in players:
                    if not isinstance(p, dict):
                        continue
                    pid = str(p.get('player_id', ''))
                    if pid:
                        result[pid] = p
            elif isinstance(players, dict):
                for pid, p in players.items():
                    if isinstance(p, dict):
                        result[str(pid)] = p

            logger.info(f'FFScouter: fetched stats for {len(result)}/{len(torn_ids)} players')
            _ffscouter_cache[cache_key] = {'data': result, 'timestamp': time.time()}
            # Prune any cache entries older than 2x TTL
            cutoff = time.time() - (FFSCOUTER_CACHE_SECONDS * 2)
            for k in [k for k, v in _ffscouter_cache.items() if v['timestamp'] < cutoff]:
                del _ffscouter_cache[k]
            return result

    except Exception as e:
        logger.error(f'FFScouter request failed: {e}')
        return {}
