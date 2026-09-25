"""
OrderBookCache – thread-safe in-memory cache for Polymarket order books.

CRITICAL FIX: Polymarket's CLOB /book endpoint returns levels with the
BEST price at the END of each array (bids ascending, asks descending).
This cache normalizes and SORTS levels best-first on write, so every
downstream consumer can safely read bids[0] and asks[0].
"""
import time
import logging
import threading
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


class OrderBookCache:
    def __init__(self, max_age_seconds: float = 30.0):
        self._books: Dict[str, Dict[str, List[Dict[str, float]]]] = {}
        self._timestamps: Dict[str, float] = {}
        self._miss_count = 0
        self._hit_count = 0
        self.max_age = max_age_seconds
        self._lock = threading.RLock()
        self._last_log_time = 0

    # ─── Level normalisation ─────────────────────────────────────────
    @staticmethod
    def _normalise_levels(levels: Any) -> List[Dict[str, float]]:
        """Convert any level format to [{'price': p, 'size': s}, ...]."""
        out: List[Dict[str, float]] = []
        if not levels:
            return out
        for lvl in levels:
            try:
                if isinstance(lvl, dict):
                    p = float(lvl.get("price", 0))
                    s = float(lvl.get("size", 0))
                elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                    p = float(lvl[0])
                    s = float(lvl[1])
                else:
                    continue
                if p <= 0 or s <= 0:
                    continue
                out.append({"price": p, "size": s})
            except (ValueError, TypeError, IndexError):
                continue
        return out

    @staticmethod
    def _sort_bids_best_first(levels: List[Dict[str, float]]) -> List[Dict[str, float]]:
        """Best (highest price) bid first."""
        return sorted(levels, key=lambda x: x["price"], reverse=True)

    @staticmethod
    def _sort_asks_best_first(levels: List[Dict[str, float]]) -> List[Dict[str, float]]:
        """Best (lowest price) ask first."""
        return sorted(levels, key=lambda x: x["price"])

    # ─── Bulk update (called by EdgeEngine) ─────────────────────────
    def update(self, books: Dict[str, Dict[str, Any]]) -> None:
        """
        Bulk-update: {token_id: {'bids': [...], 'asks': [...]}}
        Normalizes + SORTS best-first. Accepts dict or list level formats.
        """
        now = time.time()
        with self._lock:
            for token_id, book in books.items():
                if not isinstance(book, dict):
                    continue
                raw_bids = self._normalise_levels(book.get("bids", []))
                raw_asks = self._normalise_levels(book.get("asks", []))
                if not raw_bids and not raw_asks:
                    continue
                self._books[str(token_id)] = {
                    "bids": self._sort_bids_best_first(raw_bids),
                    "asks": self._sort_asks_best_first(raw_asks),
                }
                self._timestamps[str(token_id)] = now

    # ─── Single-token update (called by WebSocket) ──────────────────
    def update_snapshot(self, token_id: str, bids: Any, asks: Any) -> None:
        with self._lock:
            raw_bids = self._normalise_levels(bids)
            raw_asks = self._normalise_levels(asks)
            self._books[str(token_id)] = {
                "bids": self._sort_bids_best_first(raw_bids),
                "asks": self._sort_asks_best_first(raw_asks),
            }
            self._timestamps[str(token_id)] = time.time()

    def update_delta(self, token_id: str, updates: dict) -> None:
        if "bids" in updates and "asks" in updates:
            self.update_snapshot(token_id, updates["bids"], updates["asks"])

    # ─── Reads ───────────────────────────────────────────────────────
    def get_order_book(self, token_id: str) -> Optional[Dict]:
        with self._lock:
            key = str(token_id)
            if key not in self._books:
                self._miss_count += 1
                self._log_miss()
                return None
            if time.time() - self._timestamps.get(key, 0) > self.max_age:
                del self._books[key]
                del self._timestamps[key]
                self._miss_count += 1
                self._log_miss()
                return None
            self._hit_count += 1
            return self._books[key]

    def get_book(self, token_id: str) -> Optional[Dict]:
        return self.get_order_book(token_id)

    def get(self, token_id: str) -> Optional[Dict]:
        return self.get_order_book(token_id)

    def get_best_bid_ask(self, token_id: str) -> Optional[Tuple[float, float]]:
        book = self.get_order_book(token_id)
        if not book:
            return None
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return None
        try:
            return float(bids[0].get("price", 0)), float(asks[0].get("price", 1))
        except (AttributeError, ValueError, TypeError):
            return None

    def get_midpoint(self, token_id: str) -> Optional[float]:
        res = self.get_best_bid_ask(token_id)
        if not res:
            return None
        best_bid, best_ask = res
        if 0 < best_bid < 1 and 0 < best_ask < 1 and best_bid < best_ask:
            return (best_bid + best_ask) / 2.0
        return None

    # ─── Maintenance ─────────────────────────────────────────────────
    def _log_miss(self):
        now = time.time()
        if self._miss_count % 1000 == 0 and now - self._last_log_time > 5:
            logger.info(f"📚 OrderBookCache: {self._miss_count} cache misses (stale)")
            self._last_log_time = now

    def prune_stale(self) -> int:
        now = time.time()
        with self._lock:
            stale = [k for k, ts in self._timestamps.items() if now - ts > self.max_age]
            for k in stale:
                del self._books[k]
                del self._timestamps[k]
            return len(stale)

    def get_stats(self) -> Dict:
        with self._lock:
            return {
                "hits": self._hit_count,
                "misses": self._miss_count,
                "cached_books": len(self._books),
                "max_age": self.max_age,
            }