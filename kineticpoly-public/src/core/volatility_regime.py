"""
Volatility regime detection over a rolling window of mid-prices.

Classifies the current regime as 'low', 'medium', or 'high' based on
the standard deviation of log-returns. Thresholds are deployment-
specific and loaded from a private config; the defaults below are
placeholders that keep the module importable and testable.
"""
import os
import json
import numpy as np
from typing import Dict


_CONFIG_PATH = os.getenv("VOLATILITY_CONFIG_PATH",
                         "config/private/volatility.json")

_DEFAULT_THRESHOLDS = {"low": 0.0015, "high": 0.0050}

if os.path.exists(_CONFIG_PATH):
    try:
        with open(_CONFIG_PATH, "r") as f:
            _DEFAULT_THRESHOLDS.update(json.load(f))
    except Exception:
        pass


class VolatilityRegime:
    __slots__ = ("window", "prices", "thresholds")

    def __init__(self, window: int = 50,
                 thresholds: Dict[str, float] = None):
        self.window = window
        self.prices = []
        self.thresholds = thresholds or dict(_DEFAULT_THRESHOLDS)

    def update(self, price: float) -> None:
        """Append a new mid-price and trim to window."""
        self.prices.append(price)
        if len(self.prices) > self.window:
            self.prices = self.prices[-self.window:]

    def get_regime(self) -> str:
        """Return 'low', 'medium', or 'high'."""
        if len(self.prices) < 10:
            return "medium"
        returns = np.diff(np.log(self.prices[-self.window:]))
        vol = float(np.std(returns))
        if vol > self.thresholds["high"]:
            return "high"
        if vol < self.thresholds["low"]:
            return "low"
        return "medium"
