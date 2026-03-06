"""
HTTP Instrumentation Wrapper - Unified RPC/API call tracking.

Provides async and sync wrappers for all outbound HTTP calls with automatic:
- Provider detection (by hostname)
- Method name standardization
- Latency and error tracking
- Credit estimation
- Recording via record_request()

This ensures ALL external API calls are measured consistently.
"""

import asyncio
import time
import logging
from typing import Optional, Dict, Any, Union
from urllib.parse import urlparse
import aiohttp
import requests

logger = logging.getLogger(__name__)


# ============================================================================
# PROVIDER AND METHOD MAPPING
# ============================================================================

def get_provider_from_hostname(hostname: str) -> str:
    """
    Detect provider from hostname.

    Examples:
        api.helius.xyz → helius_api
        api-mainnet.helius-rpc.com → helius_enhanced
        mainnet.helius-rpc.com → helius_rpc
        api.mainnet-beta.solana.com → solana_public
        api.bonfida.com → bonfida_sns
    """
    hostname_lower = hostname.lower()

    if 'helius' in hostname_lower:
        if 'api.helius.xyz' in hostname_lower:
            return 'helius_api'
        elif 'api-mainnet.helius-rpc.com' in hostname_lower or 'api-devnet.helius-rpc.com' in hostname_lower:
            return 'helius_enhanced'
        elif 'helius-rpc.com' in hostname_lower or 'mainnet.helius-rpc.com' in hostname_lower:
            return 'helius_rpc'

    if 'solana.com' in hostname_lower:
        if 'api.mainnet-beta.solana.com' in hostname_lower:
            return 'solana_public'
        elif 'api.devnet.solana.com' in hostname_lower:
            return 'solana_devnet'

    if 'bonfida' in hostname_lower:
        return 'bonfida_sns'

    if 'solscan' in hostname_lower:
        return 'solscan_api'

    # Fallback: generic RPC
    return 'solana_rpc'


def get_method_from_path_and_json(path: str, json_body: Optional[Dict]) -> str:
    """
    Extract standardized method name from REST path or RPC payload.

    REST endpoints:
        /v0/addresses/{addr}/transactions → helius_enhanced_address_transactions
        /v0/tokens/{mint} → helius_enhanced_token_metadata

    RPC methods (JSON-RPC):
        {"method": "getTransaction", ...} → getTransaction
        {"method": "getSignaturesForAddress", ...} → getSignaturesForAddress
    """

    # Check RPC payload first
    if json_body and isinstance(json_body, dict):
        if 'method' in json_body:
            return json_body['method']  # e.g., "getTransaction"

    # Parse REST path
    path_lower = path.lower()

    # Helius Enhanced API patterns
    if '/v0/addresses/' in path_lower and '/transactions' in path_lower:
        return 'helius_enhanced_address_transactions'
    if '/v0/tokens/' in path_lower:
        return 'helius_enhanced_token_metadata'
    if '/v0/transaction/' in path_lower:
        return 'helius_enhanced_transaction_details'
    if '/v0/nfts/' in path_lower:
        return 'helius_enhanced_nft_details'

    # Bonfida SNS patterns
    if '/v1/resolveSOL/' in path_lower or '/resolveSOL/' in path_lower:
        return 'bonfida_resolve_domain'

    # Generic fallback
    if '?' in path:
        endpoint = path.split('?')[0].split('/')[-1]
    else:
        endpoint = path.split('/')[-1] if '/' in path else path

    return endpoint or 'unknown'


def estimate_credits_for_request(provider: str, method: str, status_code: int) -> int:
    """
    Estimate Helius credits used for a given provider/method.

    Conservative estimates based on Helius documentation:
    - RPC calls (getTransaction, etc): 1 credit
    - Address transaction fetches (enhanced): 100 credits (depends on tx count)
    - Token metadata: 10-50 credits
    - 4xx/5xx errors: 0 credits

    TODO: Make this configurable per endpoint if Helius provides usage data in response headers.
    """

    # Errors don't consume credits
    if status_code >= 400:
        return 0

    # Helius Enhanced API - address transactions (most common in your code)
    if provider == 'helius_enhanced' and 'address_transactions' in method:
        return 100  # Rough estimate; actual depends on tx count returned

    # Helius Enhanced API - token metadata
    if provider == 'helius_enhanced' and 'token' in method.lower():
        return 25

    # Helius Enhanced API - generic
    if provider == 'helius_enhanced':
        return 50

    # Helius API (REST API like api.helius.xyz)
    if provider == 'helius_api' and 'transaction' in method.lower():
        return 50

    # Standard RPC calls
    if 'getTransaction' in method:
        return 1
    if 'getSignaturesForAddress' in method or 'getSignatures' in method:
        return 1
    if 'getBalance' in method or 'getAccountInfo' in method:
        return 1

    # Bonfida
    if provider == 'bonfida_sns':
        return 0  # Free service

    # Solscan
    if provider == 'solscan_api':
        return 0  # External service

    # Conservative default: assume 50 credits for unknown
    return 50


# ============================================================================
# ASYNC WRAPPER (for aiohttp)
# ============================================================================

async def async_request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    json: Optional[Dict] = None,
    timeout: Optional[aiohttp.ClientTimeout] = None,
    section: str = "unknown",
    source_file: str = "unknown",
    record_func = None,  # Callable that records the request (e.g., record_request)
) -> Optional[Dict[str, Any]]:
    """
    Async HTTP wrapper with automatic instrumentation.

    Args:
        session: aiohttp ClientSession
        method: HTTP method (GET, POST, etc)
        url: Full URL
        json: JSON body (for POST requests)
        timeout: aiohttp.ClientTimeout
        section: Section name for metrics (e.g., "creator_funding", "funder_tracing")
        source_file: Source file name for attribution
        record_func: Function to call for recording (e.g., record_request from metrics module)

    Returns:
        Parsed JSON response, or None if failed
    """

    if timeout is None:
        timeout = aiohttp.ClientTimeout(total=30, connect=10)

    # Parse URL for provider/method detection
    parsed = urlparse(url)
    hostname = parsed.hostname or 'unknown'
    path = parsed.path + ('?' + parsed.query if parsed.query else '')

    provider = get_provider_from_hostname(hostname)
    method_name = get_method_from_path_and_json(path, json)

    start_time = time.time()
    status_code = 0
    error_msg = None
    response_json = None

    try:
        async with session.request(
            method.upper(),
            url,
            json=json,
            timeout=timeout,
            ssl=False  # Ignore SSL warnings for internal calls
        ) as resp:
            status_code = resp.status
            latency_ms = (time.time() - start_time) * 1000

            # Attempt to parse JSON
            try:
                response_json = await resp.json()
            except Exception:
                # If not JSON-parseable, try text
                try:
                    text = await resp.text()
                    if status_code == 200:
                        logger.warning(f"[HTTP] {provider}/{method_name}: Got non-JSON response: {text[:100]}")
                except Exception:
                    pass

            # Record metrics
            if record_func:
                credits_est = estimate_credits_for_request(provider, method_name, status_code)
                record_func(
                    section=section,
                    provider=provider,
                    method=method_name,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    mode="realtime",
                    retries=0,
                    source_file=source_file,
                    host=hostname,
                    path_group=path.split('?')[0],  # Path without query string
                    credits_estimated=credits_est,
                    error=None if status_code == 200 else f"HTTP {status_code}",
                )

            # Log
            if status_code != 200:
                logger.warning(f"[HTTP] {provider}/{method_name}: HTTP {status_code} - {latency_ms:.0f}ms")
            else:
                logger.debug(f"[HTTP] {provider}/{method_name}: 200 OK - {latency_ms:.0f}ms")

            # Return JSON if successful
            if status_code == 200:
                return response_json
            else:
                return None

    except asyncio.TimeoutError as e:
        latency_ms = (time.time() - start_time) * 1000
        error_msg = f"Timeout (>{timeout.total}s)"

        if record_func:
            record_func(
                section=section,
                provider=provider,
                method=method_name,
                status_code=0,
                latency_ms=latency_ms,
                mode="realtime",
                retries=0,
                source_file=source_file,
                host=hostname,
                path_group=path.split('?')[0],
                credits_estimated=0,
                error=error_msg,
            )

        logger.error(f"[HTTP] {provider}/{method_name}: {error_msg}")
        return None

    except aiohttp.ClientError as e:
        latency_ms = (time.time() - start_time) * 1000
        error_msg = f"Connection error: {str(e)[:50]}"

        if record_func:
            record_func(
                section=section,
                provider=provider,
                method=method_name,
                status_code=0,
                latency_ms=latency_ms,
                mode="realtime",
                retries=0,
                source_file=source_file,
                host=hostname,
                path_group=path.split('?')[0],
                credits_estimated=0,
                error=error_msg,
            )

        logger.error(f"[HTTP] {provider}/{method_name}: {error_msg}")
        return None

    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        error_msg = f"Unexpected error: {str(e)[:50]}"

        if record_func:
            record_func(
                section=section,
                provider=provider,
                method=method_name,
                status_code=0,
                latency_ms=latency_ms,
                mode="realtime",
                retries=0,
                source_file=source_file,
                host=hostname,
                path_group=path.split('?')[0],
                credits_estimated=0,
                error=error_msg,
            )

        logger.error(f"[HTTP] {provider}/{method_name}: {error_msg}")
        return None


# ============================================================================
# SYNC WRAPPER (for requests)
# ============================================================================

def sync_request_json(
    method: str,
    url: str,
    *,
    json: Optional[Dict] = None,
    timeout: float = 20.0,
    section: str = "unknown",
    source_file: str = "unknown",
    record_func = None,  # Callable that records the request
) -> Optional[Dict[str, Any]]:
    """
    Sync HTTP wrapper with automatic instrumentation.

    Args:
        method: HTTP method (GET, POST, etc)
        url: Full URL
        json: JSON body (for POST requests)
        timeout: Request timeout in seconds
        section: Section name for metrics
        source_file: Source file name for attribution
        record_func: Function to call for recording

    Returns:
        Parsed JSON response, or None if failed
    """

    # Parse URL for provider/method detection
    parsed = urlparse(url)
    hostname = parsed.hostname or 'unknown'
    path = parsed.path + ('?' + parsed.query if parsed.query else '')

    provider = get_provider_from_hostname(hostname)
    method_name = get_method_from_path_and_json(path, json)

    start_time = time.time()
    status_code = 0
    error_msg = None
    response_json = None

    try:
        if method.upper() == 'GET':
            resp = requests.get(url, timeout=timeout)
        else:
            resp = requests.post(url, json=json, timeout=timeout)

        status_code = resp.status_code
        latency_ms = (time.time() - start_time) * 1000

        # Attempt to parse JSON
        try:
            response_json = resp.json()
        except Exception:
            if status_code == 200:
                logger.warning(f"[HTTP] {provider}/{method_name}: Got non-JSON response")

        # Record metrics
        if record_func:
            credits_est = estimate_credits_for_request(provider, method_name, status_code)
            record_func(
                section=section,
                provider=provider,
                method=method_name,
                status_code=status_code,
                latency_ms=latency_ms,
                mode="realtime",
                retries=0,
                source_file=source_file,
                host=hostname,
                path_group=path.split('?')[0],
                credits_estimated=credits_est,
                error=None if status_code == 200 else f"HTTP {status_code}",
            )

        # Log
        if status_code != 200:
            logger.warning(f"[HTTP] {provider}/{method_name}: HTTP {status_code} - {latency_ms:.0f}ms")
        else:
            logger.debug(f"[HTTP] {provider}/{method_name}: 200 OK - {latency_ms:.0f}ms")

        # Return JSON if successful
        if status_code == 200:
            return response_json
        else:
            return None

    except requests.Timeout as e:
        latency_ms = (time.time() - start_time) * 1000
        error_msg = f"Timeout (>{timeout}s)"

        if record_func:
            record_func(
                section=section,
                provider=provider,
                method=method_name,
                status_code=0,
                latency_ms=latency_ms,
                mode="realtime",
                retries=0,
                source_file=source_file,
                host=hostname,
                path_group=path.split('?')[0],
                credits_estimated=0,
                error=error_msg,
            )

        logger.error(f"[HTTP] {provider}/{method_name}: {error_msg}")
        return None

    except requests.ConnectionError as e:
        latency_ms = (time.time() - start_time) * 1000
        error_msg = f"Connection error: {str(e)[:50]}"

        if record_func:
            record_func(
                section=section,
                provider=provider,
                method=method_name,
                status_code=0,
                latency_ms=latency_ms,
                mode="realtime",
                retries=0,
                source_file=source_file,
                host=hostname,
                path_group=path.split('?')[0],
                credits_estimated=0,
                error=error_msg,
            )

        logger.error(f"[HTTP] {provider}/{method_name}: {error_msg}")
        return None

    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        error_msg = f"Unexpected error: {str(e)[:50]}"

        if record_func:
            record_func(
                section=section,
                provider=provider,
                method=method_name,
                status_code=0,
                latency_ms=latency_ms,
                mode="realtime",
                retries=0,
                source_file=source_file,
                host=hostname,
                path_group=path.split('?')[0],
                credits_estimated=0,
                error=error_msg,
            )

        logger.error(f"[HTTP] {provider}/{method_name}: {error_msg}")
        return None
