"""
Gamma API client – with robust session, retries, and correct exception handling.
Now properly handles requests.exceptions.SSLError and other network errors.
"""
import json
import requests
import logging
import time
import asyncio
import aiohttp
from typing import List, Dict, Optional, Union
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests.exceptions import SSLError, ConnectionError, Timeout, RequestException

logger = logging.getLogger(__name__)
GAMMA_BASE = "https://gamma-api.polymarket.com"

class GammaAPI:
    _session = None

    @classmethod
    def _create_robust_session(cls):
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        retries = Retry(
            total=5,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @classmethod
    def _get_session(cls):
        if cls._session is None:
            cls._session = cls._create_robust_session()
        return cls._session

    @staticmethod
    def _request_with_retry(method: str, url: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
        session = GammaAPI._get_session()
        for attempt in range(5):
            try:
                resp = session.get(url, params=params, timeout=timeout)
                if resp.status_code == 200:
                    return resp.json()
                else:
                    logger.warning(f"Request to {url} returned {resp.status_code}, retrying...")
                    time.sleep(1.0 * (attempt + 1))
            except (ConnectionError, Timeout, SSLError) as e:
                logger.warning(f"Request failed (attempt {attempt+1}/5): {e}. Retrying in {2 ** attempt:.1f}s...")
                time.sleep(2 ** attempt)
            except RequestException as e:
                logger.error(f"Unrecoverable request error: {e}")
                return None
        logger.error(f"Failed to fetch from {url} after 5 retries.")
        return None

    # ── Asynchronous methods ──
    @staticmethod
    async def get_markets_async(session: aiohttp.ClientSession, active: bool = True, limit: int = 10,
                                sport: Optional[Union[str, List[str]]] = None,
                                tag_id: Optional[int] = None,
                                offset: int = 0) -> List[Dict]:
        if session.closed:
            logger.warning("GammaAPI.get_markets_async called with closed session; returning empty list.")
            return []
        params = {"active": str(active).lower(), "closed": "false", "limit": limit, "offset": offset}
        if tag_id:
            params["tag_id"] = tag_id
        if sport:
            params["sport"] = sport
        url = f"{GAMMA_BASE}/markets"
        try:
            async with session.get(url, params=params, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
                else:
                    logger.warning(f"Async markets request returned {resp.status}")
                    return []
        except aiohttp.ClientError as e:
            logger.error(f"Async markets fetch error: {e}")
            return []
        except Exception as e:
            logger.error(f"Async markets fetch error: {e}")
            return []

    @staticmethod
    async def get_tokens_async(session: aiohttp.ClientSession, market_id: str) -> List[Dict]:
        if session.closed:
            logger.warning("GammaAPI.get_tokens_async called with closed session; returning empty list.")
            return []
        url = f"{GAMMA_BASE}/markets/{market_id}"
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    return []
                market = await resp.json()
                if not market:
                    return []
        except aiohttp.ClientError as e:
            logger.error(f"Async tokens fetch error: {e}")
            return []
        except Exception as e:
            logger.error(f"Async tokens fetch error: {e}")
            return []

        clob_ids_str = market.get("clobTokenIds", "[]")
        try:
            clob_ids = json.loads(clob_ids_str)
            if not isinstance(clob_ids, list):
                clob_ids = []
        except json.JSONDecodeError:
            clob_ids = [t.strip() for t in clob_ids_str.split(",") if t.strip()]

        outcomes = market.get("outcomes", [])
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
                if not isinstance(outcomes, list):
                    outcomes = []
            except json.JSONDecodeError:
                outcomes = [outcomes]

        outcome_prices_raw = market.get("outcomePrices", "[]")
        try:
            outcome_prices = json.loads(outcome_prices_raw)
            if not isinstance(outcome_prices, list):
                outcome_prices = []
        except json.JSONDecodeError:
            outcome_prices = []

        result = []
        for i, asset_id in enumerate(clob_ids):
            if not asset_id:
                continue
            result.append({
                "token_id": str(asset_id),
                "asset_id": str(asset_id),
                "outcome": outcomes[i] if i < len(outcomes) else f"Outcome{i}",
                "price": float(outcome_prices[i]) if i < len(outcome_prices) and outcome_prices[i] is not None else None,
            })
        return result

    # ── Synchronous methods ──
    @staticmethod
    def get_markets(active: bool = True, closed: bool = False, limit: int = 10,
                    sport: Optional[Union[str, List[str]]] = None,
                    tag_id: Optional[int] = None,
                    offset: int = 0) -> List[Dict]:
        params = {"active": str(active).lower(), "closed": str(closed).lower(), "limit": limit, "offset": offset}
        if tag_id:
            params["tag_id"] = tag_id
        if sport:
            params["sport"] = sport
        data = GammaAPI._request_with_retry("GET", f"{GAMMA_BASE}/markets", params=params)
        return data if data else []

    @staticmethod
    def get_market(market_id: str) -> Optional[Dict]:
        return GammaAPI._request_with_retry("GET", f"{GAMMA_BASE}/markets/{market_id}")

    @staticmethod
    def get_tokens(market_id: str) -> List[Dict]:
        market = GammaAPI.get_market(market_id)
        if not market:
            return []

        clob_ids_str = market.get("clobTokenIds", "[]")
        try:
            clob_ids = json.loads(clob_ids_str)
            if not isinstance(clob_ids, list):
                clob_ids = []
        except json.JSONDecodeError:
            clob_ids = [t.strip() for t in clob_ids_str.split(",") if t.strip()]

        outcomes = market.get("outcomes", [])
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
                if not isinstance(outcomes, list):
                    outcomes = []
            except json.JSONDecodeError:
                outcomes = [outcomes]

        outcome_prices_raw = market.get("outcomePrices", "[]")
        try:
            outcome_prices = json.loads(outcome_prices_raw)
            if not isinstance(outcome_prices, list):
                outcome_prices = []
        except json.JSONDecodeError:
            outcome_prices = []

        result = []
        for i, asset_id in enumerate(clob_ids):
            if not asset_id:
                continue
            result.append({
                "token_id": str(asset_id),
                "asset_id": str(asset_id),
                "outcome": outcomes[i] if i < len(outcomes) else f"Outcome{i}",
                "price": float(outcome_prices[i]) if i < len(outcome_prices) and outcome_prices[i] is not None else None,
            })
        return result