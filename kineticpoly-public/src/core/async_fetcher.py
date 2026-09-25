"""
AsyncMarketDataFetcher – robust HTTP polling with batching, exponential backoff,
connection pooling, and graceful shutdown.

CRITICAL FIX: Polymarket's CLOB /book endpoint returns bids in ascending
order (best at end) and asks in descending order (best at end). This fetcher
sorts each book best-first on arrival so every downstream consumer can
read bids[0] and asks[0].
"""
import asyncio
import logging
import random
import ssl
from typing import Dict, Any, List, Optional
import aiohttp

logger = logging.getLogger("AsyncMarketDataFetcher")

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


class AsyncMarketDataFetcher:
    def __init__(
        self,
        max_concurrent_requests: int = 50,
        batch_size: int = 20,
        max_retries: int = 4,
        base_backoff_sec: float = 0.5,
        timeout_sec: float = 10.0,
    ):
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.base_backoff_sec = base_backoff_sec
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._session: Optional[aiohttp.ClientSession] = None
        self._shutdown_event = asyncio.Event()
        self._closed = False
        # Track how many books came back with best-at-end ordering
        self._unsorted_count = 0
        self._total_books = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            ssl_context = ssl.create_default_context()
            ssl_context.set_ciphers('DEFAULT@SECLEVEL=1')
            ssl_context.options |= ssl.OP_NO_TICKET

            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=50,
                ssl=ssl_context,
                enable_cleanup_closed=True,
                keepalive_timeout=30.0,
            )
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Content-Type": "application/json",
            }
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout,
                headers=headers,
            )
            self._shutdown_event.clear()
            self._closed = False
        return self._session

    def cancel(self):
        self._shutdown_event.set()
        self._closed = True

    async def close(self):
        self._shutdown_event.set()
        self._closed = True
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("🔒 AsyncMarketDataFetcher HTTP session closed.")

    async def _fetch_with_backoff(self, method: str, url: str,
                                  params: Optional[Dict] = None,
                                  json_data: Optional[Any] = None) -> Optional[Any]:
        session = await self._get_session()
        for attempt in range(1, self.max_retries + 1):
            if self._shutdown_event.is_set():
                return None
            await asyncio.sleep(random.uniform(0.1, 0.3))
            async with self.semaphore:
                try:
                    if method.upper() == "POST":
                        async with session.post(url, params=params, json=json_data) as response:
                            if response.status == 200:
                                return await response.json()
                            error_text = await response.text()
                            if response.status == 400:
                                logger.warning(f"⚠️ HTTP 400 from {url}: {error_text}")
                                return None
                            if response.status in (429, 502, 503, 504):
                                logger.warning(f"⚠️ HTTP {response.status} (attempt {attempt}/{self.max_retries})")
                            else:
                                logger.error(f"❌ Unhandled HTTP {response.status}: {error_text}")
                                return None
                    else:  # GET
                        async with session.get(url, params=params) as response:
                            if response.status == 200:
                                return await response.json()
                            error_text = await response.text()
                            if response.status == 400:
                                logger.warning(f"⚠️ HTTP 400 from {url}: {error_text}")
                                return None
                            if response.status in (429, 502, 503, 504):
                                logger.warning(f"⚠️ HTTP {response.status} (attempt {attempt}/{self.max_retries})")
                            else:
                                logger.error(f"❌ Unhandled HTTP {response.status}: {error_text}")
                                return None
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ConnectionResetError) as err:
                    logger.warning(f"⚠️ Network error (attempt {attempt}/{self.max_retries}): {err}")

            if attempt < self.max_retries:
                backoff = self.base_backoff_sec * (2 ** (attempt - 1))
                sleep_duration = backoff + random.uniform(0, backoff * 0.5)
                await asyncio.sleep(sleep_duration)

        logger.error(f"❌ Max retries reached for {url}")
        return None

    @staticmethod
    def _level_price(entry) -> Optional[float]:
        try:
            if isinstance(entry, dict):
                return float(entry.get("price", 0))
            return float(entry[0])
        except (ValueError, TypeError, IndexError):
            return None

    @classmethod
    def _sort_book(cls, book: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sort bids best-first (highest price) and asks best-first (lowest price).
        Polymarket returns levels with the best at the END of each array.
        This normalises every book so bids[0] and asks[0] are the top of book.
        """
        if not isinstance(book, dict):
            return book

        raw_bids = book.get("bids") or []
        raw_asks = book.get("asks") or []

        try:
            bids_sorted = sorted(
                [b for b in raw_bids if cls._level_price(b) is not None],
                key=lambda b: cls._level_price(b),
                reverse=True,     # highest price first
            )
            asks_sorted = sorted(
                [a for a in raw_asks if cls._level_price(a) is not None],
                key=lambda a: cls._level_price(a),
                reverse=False,    # lowest price first
            )
        except Exception:
            bids_sorted = list(raw_bids)
            asks_sorted = list(raw_asks)

        out = dict(book)
        out["bids"] = bids_sorted
        out["asks"] = asks_sorted
        return out

    async def get_markets_async(self, active: bool = True, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        if self._shutdown_event.is_set():
            return []
        url = f"{GAMMA_BASE}/markets"
        params = {
            "active": str(active).lower(),
            "closed": "false",
            "limit": limit,
            "offset": offset,
        }
        data = await self._fetch_with_backoff("GET", url, params=params)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("data", [])
        return []

    async def get_order_books_async(self, token_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not token_ids:
            return {}
        if self._shutdown_event.is_set():
            return {}

        valid_tokens = [str(t).strip() for t in token_ids if str(t).strip()]
        if not valid_tokens:
            return {}
        valid_tokens = list(dict.fromkeys(valid_tokens))

        batches = [
            valid_tokens[i : i + self.batch_size]
            for i in range(0, len(valid_tokens), self.batch_size)
        ]
        logger.info(f"📦 Split {len(valid_tokens)} tokens into {len(batches)} batch request(s).")

        tasks = [self._fetch_order_book_batch(batch) for batch in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        aggregated_books: Dict[str, Dict[str, Any]] = {}
        for res in results:
            if isinstance(res, dict):
                aggregated_books.update(res)
            elif isinstance(res, Exception):
                logger.error(f"❌ Batch failed: {res}")

        # Diagnostic: log how many books needed sorting
        if self._total_books > 0 and self._total_books % 500 == 0:
            logger.info(f"📊 Book sort stats: {self._unsorted_count}/{self._total_books} "
                        f"books had best-at-end ordering")

        return aggregated_books

    async def _fetch_order_book_batch(self, batch_tokens: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch books for a batch. Sorts each book best-first before returning."""
        url = f"{CLOB_BASE}/books"
        payload = [{"token_id": tid} for tid in batch_tokens]
        data = await self._fetch_with_backoff("POST", url, json_data=payload)

        if not data:
            return {}

        out: Dict[str, Dict[str, Any]] = {}

        def _register(t_id: str, book: Dict[str, Any]):
            """Sort and register the book, tracking whether sorting was needed."""
            if not isinstance(book, dict):
                return
            raw_bids = book.get("bids") or []
            raw_asks = book.get("asks") or []
            # Check whether the book needs sorting (best at end)
            needs_sort = False
            if len(raw_bids) >= 2:
                first_bid = self._level_price(raw_bids[0])
                last_bid = self._level_price(raw_bids[-1])
                if first_bid is not None and last_bid is not None and last_bid > first_bid:
                    needs_sort = True
            if len(raw_asks) >= 2:
                first_ask = self._level_price(raw_asks[0])
                last_ask = self._level_price(raw_asks[-1])
                if first_ask is not None and last_ask is not None and last_ask < first_ask:
                    needs_sort = True
            if needs_sort:
                self._unsorted_count += 1
            self._total_books += 1

            out[str(t_id)] = self._sort_book(book)

        if isinstance(data, dict):
            for t_id, book in data.items():
                _register(t_id, book)
        elif isinstance(data, list):
            for book in data:
                t_id = str(book.get("asset_id") or book.get("token_id") or "")
                if t_id:
                    _register(t_id, book)

        return out