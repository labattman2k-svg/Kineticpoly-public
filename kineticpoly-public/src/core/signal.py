from dataclasses import dataclass
from typing import Optional

@dataclass
class TradingSignal:
    token_id: str
    market_id: str
    side: str  # "BUY" or "SELL"
    price: float
    stake: float
    edge: float
    fair_price: float
    strategy: str  # opaque strategy identifier
    timestamp: float
    hours_to_start: Optional[float] = None