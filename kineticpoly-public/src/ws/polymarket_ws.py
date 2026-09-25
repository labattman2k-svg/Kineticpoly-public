import asyncio
import json
import logging
import random
import websockets
from typing import List, Callable, Optional
from src.core.order_book_cache import OrderBookCache

logger = logging.getLogger(__name__)

class PolymarketWebSocket:
    def __init__(self, cache: OrderBookCache):
        self.cache = cache
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._ws = None
        self._loop = None
        self._on_book_update: Optional[Callable[[str], None]] = None

    def set_book_callback(self, cb: Optional[Callable[[str], None]]) -> None:
        """Register a callback invoked with the asset_id after each book update."""
        self._on_book_update = cb

    async def subscribe_orderbooks(self, asset_ids: List[str], callback: Optional[Callable] = None):
        self._running = True
        self._shutdown_event.clear()
        self._loop = asyncio.get_running_loop()
        logger.info(f"🔌 WebSocket client starting: {len(asset_ids)} assets")
        while self._running and not self._shutdown_event.is_set():
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    logger.info("✅ WebSocket connected")
                    for asset_id in asset_ids:
                        if self._shutdown_event.is_set():
                            break
                        await ws.send(json.dumps({"type": "subscribe", "channel": "orderbook", "asset_id": asset_id}))
                        await asyncio.sleep(0.02)
                    while self._running and not self._shutdown_event.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        except asyncio.CancelledError:
                            break
                        except websockets.exceptions.ConnectionClosed:
                            break
                        if msg:
                            try:
                                data = json.loads(msg)
                                event = data.get("event") or data.get("type") or data.get("event_type")
                                if event in ("orderbook", "book"):
                                    asset_id = (
                                        data.get("asset_id")
                                        or data.get("market")
                                        or data.get("token_id")
                                    )
                                    if asset_id:
                                        bids = data.get("bids", [])
                                        asks = data.get("asks", [])
                                        self.cache.update_snapshot(asset_id, bids, asks)
                                        if self._on_book_update:
                                            self._on_book_update(asset_id)
                            except Exception:
                                pass
            except websockets.exceptions.ConnectionClosed:
                if self._shutdown_event.is_set():
                    break
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._shutdown_event.is_set():
                    break
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(2)
        self._ws = None
        logger.info("✅ WebSocket stopped")

    def stop(self):
        logger.info("🛑 Stopping WebSocket client...")
        self._running = False
        self._shutdown_event.set()
        if self._ws:
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop).result(timeout=1)
            except:
                pass
        logger.info("✅ WebSocket client stop signal sent")