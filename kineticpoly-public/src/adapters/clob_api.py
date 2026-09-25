"""
Polymarket CLOB API client – synchronous and asynchronous order book fetchers.
Handles rate limits, timeouts, and malformed responses gracefully.
"""
import asyncio
import aiohttp
import logging
import requests
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

CLOB_BASE_URL = "https://clob.polymarket.com"
DEFAULT_TIMEOUT = 5  # seconds (reduced)


class ClobAPI:
    """Synchronous and asynchronous client for Polymarket's CLOB V2 API."""

    @staticmethod
    def get_order_book(token_id: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[Dict[str, Any]]:
        url = f"{CLOB_BASE_URL}/book"
        params = {"token_id": token_id}
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    data.setdefault("bids", [])
                    data.setdefault("asks", [])
                    return data
                return None
            else:
                logger.warning(f"CLOB sync HTTP {resp.status_code} for {token_id[:12]}")
                return None
        except Exception as e:
            logger.warning(f"CLOB sync error for {token_id[:12]}: {e}")
            return None

    @staticmethod
    def get_midpoint(token_id: str) -> Optional[float]:
        book = ClobAPI.get_order_book(token_id)
        if not book:
            return None
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return None
        try:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            return (best_bid + best_ask) / 2.0
        except (IndexError, TypeError, ValueError):
            return None

    @staticmethod
    async def get_order_book_async(session: aiohttp.ClientSession, token_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch order book asynchronously with timeout and robust error handling.
        Returns a dict with 'bids' and 'asks' lists, or None on failure.
        """
        url = f"{CLOB_BASE_URL}/book"
        params = {"token_id": token_id}
        try:
            async with session.get(url, params=params, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict):
                        data.setdefault("bids", [])
                        data.setdefault("asks", [])
                        # Ensure bids and asks are lists
                        if not isinstance(data["bids"], list):
                            data["bids"] = []
                        if not isinstance(data["asks"], list):
                            data["asks"] = []
                        return data
                    else:
                        logger.debug(f"CLOB async: non‑dict response for {token_id[:12]}")
                        return None
                else:
                    logger.debug(f"CLOB async HTTP {resp.status} for {token_id[:12]}")
                    return None
        except asyncio.TimeoutError:
            logger.debug(f"CLOB async timeout for {token_id[:12]}")
            return None
        except aiohttp.ClientError as e:
            logger.debug(f"CLOB async client error for {token_id[:12]}: {e}")
            return None
        except Exception as e:
            logger.debug(f"CLOB async unexpected error for {token_id[:12]}: {e}")
            return None

    @staticmethod
    def is_book_valid(book: Dict[str, Any]) -> bool:
        if not book:
            return False
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        return bool(bids) or bool(asks)

    @staticmethod
    def get_best_bid_ask(book: Dict[str, Any]) -> Optional[tuple[float, float]]:
        if not ClobAPI.is_book_valid(book):
            return None
        try:
            best_bid = float(book["bids"][0][0]) if book.get("bids") else 0.0
            best_ask = float(book["asks"][0][0]) if book.get("asks") else 1.0
            return best_bid, best_ask
        except (IndexError, TypeError, ValueError):
            return None