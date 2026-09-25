"""
Priority queue ordered by effective edge (descending), with a small
proximity-to-kickoff tiebreak, then FIFO.
"""
import heapq
import threading
from typing import Optional
from .signal import TradingSignal


# Deployment-tuned; overridden from private config in production.
PRIORITY_PROXIMITY_WINDOW_HOURS = 48.0
PRIORITY_PROXIMITY_WEIGHT = 0.001


class PriorityQueue:
    def __init__(self, max_size: int = 200):
        self.heap = []
        self.max_size = max_size
        self.lock = threading.RLock()
        self.counter = 0

    def push(self, signal: TradingSignal):
        with self.lock:
            edge = signal.edge if signal.edge is not None else 0.0
            hts = signal.hours_to_start if signal.hours_to_start is not None else 24.0
            if hts < 0:
                proximity = 1.0
            else:
                proximity = max(
                    0.0,
                    min(1.0, 1.0 - (hts / PRIORITY_PROXIMITY_WINDOW_HOURS)),
                )
            priority_score = edge + (proximity * PRIORITY_PROXIMITY_WEIGHT)
            entry = (-priority_score, self.counter, signal)
            heapq.heappush(self.heap, entry)
            self.counter += 1
            if len(self.heap) > self.max_size:
                heapq.heappop(self.heap)

    def pop(self) -> Optional[TradingSignal]:
        with self.lock:
            if self.heap:
                return heapq.heappop(self.heap)[2]
            return None

    def peek(self) -> Optional[TradingSignal]:
        with self.lock:
            return self.heap[0][2] if self.heap else None

    def size(self) -> int:
        with self.lock:
            return len(self.heap)
