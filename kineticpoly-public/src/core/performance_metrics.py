"""
Performance Metrics – Tracks and reports risk-adjusted returns, execution quality,
and robustness metrics.

"""
import logging
import time
import numpy as np
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

CLOSING_SIDES = ("SELL", "MERGE")


@dataclass
class Trade:
    timestamp: float
    token_id: str
    side: str
    size: float
    price: float
    pnl: float
    fee: float
    model_prob: float = 0.5
    market_prob: float = 0.5
    strategy: str = "UNKNOWN"


class PerformanceMetrics:
    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance
        self.trades: List[Trade] = []
        self.win_rate = 0.0
        self.profit_factor = 0.0
        self.sharpe_ratio = 0.0
        self.sortino_ratio = 0.0
        self.expectancy = 0.0
        self.max_consecutive_wins = 0
        self.max_consecutive_losses = 0
        self.avg_trade_interval = 0.0
        self.recovery_cycles = 0
        self.max_drawdown = 0.0

        # Execution quality
        self.fill_rate = 0.0
        self.avg_slippage = 0.0
        self.avg_latency_ms = 0.0
        self.p95_latency_ms = 0.0

        # Robustness
        self.oos_decay = 1.0
        self.deflated_sharpe = 0.0

        # Internal
        self._last_trade_time = None
        self._consecutive_wins = 0
        self._consecutive_losses = 0
        self._win_streak_max = 0
        self._loss_streak_max = 0
        self._returns: List[float] = []

        # Reconciled reporting
        self.closed_trades_count = 0
        self.closed_wins = 0
        self.closed_losses = 0

        logger.info("PerformanceMetrics initialised")

    # ─── Timestamp helper ───
    @staticmethod
    def _parse_timestamp(ts) -> float:
        if ts is None:
            return time.time()
        if isinstance(ts, (int, float)):
            return float(ts)
        if isinstance(ts, str):
            try:
                if ts.endswith('Z'):
                    ts = ts.replace('Z', '+00:00')
                dt = datetime.fromisoformat(ts)
                return dt.timestamp()
            except Exception:
                try:
                    return float(ts)
                except ValueError:
                    return time.time()
        return time.time()

    @staticmethod
    def _is_closing_trade(trade_data: Dict[str, Any]) -> bool:
        side = str(trade_data.get("side", "")).upper()
        if side not in CLOSING_SIDES:
            return False
        pnl = float(trade_data.get("pnl", 0) or 0)
        return abs(pnl) > 1e-6

    # ─── Trade intake ───
    def add_trade(self, trade_data: Dict[str, Any], _rebuild_metrics: bool = True) -> None:
        """Add a single trade. Set `_rebuild_metrics=False` when bulk-loading."""
        timestamp = self._parse_timestamp(trade_data.get("timestamp"))

        trade = Trade(
            timestamp=timestamp,
            token_id=trade_data.get("token_id", ""),
            side=trade_data.get("side", ""),
            size=float(trade_data.get("size", 0.0) or 0.0),
            price=float(trade_data.get("price", 0.0) or 0.0),
            pnl=float(trade_data.get("pnl", 0.0) or 0.0),
            fee=float(trade_data.get("fee", 0.0) or 0.0),
            model_prob=float(trade_data.get("model_prob", 0.5) or 0.5),
            market_prob=float(trade_data.get("market_prob", 0.5) or 0.5),
            strategy=trade_data.get("strategy", "UNKNOWN") or "UNKNOWN",
        )
        self.trades.append(trade)

        # Streak tracking only meaningful for closing trades
        if self._is_closing_trade(trade_data):
            if trade.pnl > 0:
                self._consecutive_wins += 1
                self._consecutive_losses = 0
                if self._consecutive_wins > self.max_consecutive_wins:
                    self.max_consecutive_wins = self._consecutive_wins
            elif trade.pnl < 0:
                self._consecutive_losses += 1
                self._consecutive_wins = 0
                if self._consecutive_losses > self.max_consecutive_losses:
                    self.max_consecutive_losses = self._consecutive_losses
            else:
                self._consecutive_wins = 0
                self._consecutive_losses = 0

        if _rebuild_metrics:
            self._update_metrics()

    # ─── Aggregation ───
    def _update_metrics(self) -> None:
        if not self.trades:
            return

        pnls = [t.pnl for t in self.trades]
        total_pnl = sum(pnls)

        closed_trades = [
            t for t in self.trades
            if t.side.upper() in CLOSING_SIDES and abs(t.pnl) > 1e-6
        ]
        closed_wins = [t for t in closed_trades if t.pnl > 0]
        closed_losses = [t for t in closed_trades if t.pnl < 0]

        self.closed_trades_count = len(closed_trades)
        self.closed_wins = len(closed_wins)
        self.closed_losses = len(closed_losses)
        self.win_rate = (len(closed_wins) / len(closed_trades)) if closed_trades else 0.0

        gross_profit = sum(t.pnl for t in closed_wins)
        gross_loss = abs(sum(t.pnl for t in closed_losses))
        self.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
        self.expectancy = (total_pnl / len(closed_trades)) if closed_trades else 0.0

        # Sharpe/Sortino from per-trade PnL scaled by initial balance
        if len(closed_trades) > 1 and self.initial_balance > 0:
            returns = [t.pnl / self.initial_balance for t in closed_trades]
            self._returns = returns
            mean_ret = float(np.mean(returns))
            std_ret = float(np.std(returns))
            self.sharpe_ratio = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0.0
            downside = [r for r in returns if r < 0]
            if downside:
                downside_std = float(np.std(downside))
                self.sortino_ratio = (mean_ret / downside_std) * np.sqrt(252) if downside_std > 0 else 0.0
            else:
                self.sortino_ratio = 0.0
        else:
            self.sharpe_ratio = 0.0
            self.sortino_ratio = 0.0

        # Average time between closing trades
        if len(closed_trades) > 1:
            intervals = [
                closed_trades[i].timestamp - closed_trades[i - 1].timestamp
                for i in range(1, len(closed_trades))
            ]
            self.avg_trade_interval = float(np.mean(intervals)) if intervals else 0.0
        else:
            self.avg_trade_interval = 0.0

    # ─── External updates ───
    def update_execution_stats(self, fill_rate: float, avg_latency_ms: float,
                               p95_latency_ms: float, avg_slippage: float) -> None:
        self.fill_rate = fill_rate
        self.avg_latency_ms = avg_latency_ms
        self.p95_latency_ms = p95_latency_ms
        self.avg_slippage = avg_slippage

    def sync_from_paper_engine(self, paper_engine) -> None:
        """
        Rebuild trade list and derived metrics from the paper engine's
        authoritative trade_history. Also pulls initial_balance and
        max_drawdown so downstream reporting is consistent.
        """
        self.trades.clear()
        for trade_data in paper_engine.trade_history:
            self.add_trade(trade_data, _rebuild_metrics=False)

        # Propagate authoritative account-level facts
        try:
            if getattr(paper_engine, "initial_balance", None):
                self.initial_balance = float(paper_engine.initial_balance)
        except Exception:
            pass
        self.max_drawdown = float(getattr(paper_engine, "max_drawdown", 0.0) or 0.0)
        self.recovery_cycles = 0

        self._update_metrics()

    # ─── Reporting ───
    def get_report(self) -> Dict[str, Any]:
        return {
            "total_trades": len(self.trades),
            "closed_trades": self.closed_trades_count,
            "closed_wins": self.closed_wins,
            "closed_losses": self.closed_losses,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "expectancy": self.expectancy,
            "max_drawdown": self.max_drawdown,
            "max_consecutive_wins": self.max_consecutive_wins,
            "max_consecutive_losses": self.max_consecutive_losses,
            "avg_trade_interval": self.avg_trade_interval,
            "fill_rate": self.fill_rate,
            "avg_slippage": self.avg_slippage,
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
        }

    def print_report(self, use_total_equity: bool = False) -> None:
        """use_total_equity is accepted for backwards compat; ignored."""
        if not self.trades:
            print("\n📊 PERFORMANCE METRICS REPORT")
            print("No trades recorded yet.")
            return

        total_pnl = sum(t.pnl for t in self.trades)
        win_rate_pct = self.win_rate * 100
        max_dd_pct = self.max_drawdown * 100

        print("\n" + "=" * 80)
        print("📊 PERFORMANCE METRICS REPORT")
        print("=" * 80)
        print(f"Total Trades:        {len(self.trades)}")
        print(f"Closed Trades:       {self.closed_trades_count}")
        print(f"Closed Wins/Losses:  {self.closed_wins}/{self.closed_losses}")

        print("\n🔴 RISK-ADJUSTED RETURNS")
        print(f"  Total Realized P&L: ${total_pnl:,.2f}")
        print(f"  Profit Factor:      {self.profit_factor:.2f}")
        print(f"  Sharpe Ratio:       {self.sharpe_ratio:.2f}")
        print(f"  Sortino Ratio:      {self.sortino_ratio:.2f}")
        print(f"  Expectancy:         ${self.expectancy:,.4f}")
        print(f"  Win Rate (closed):  {win_rate_pct:.1f}%")
        print(f"  Max Drawdown:       {max_dd_pct:.2f}%")
        print(f"  Recovery Cycles:    {self.recovery_cycles}")

        print("\n⚡ EXECUTION QUALITY")
        print(f"  Avg Slippage:       {self.avg_slippage:.6f}")
        print(f"  Fill Rate:          {self.fill_rate:.2f}%")

        print("\n🖥️ TECHNICAL RELIABILITY")
        print(f"  Avg Latency:        {self.avg_latency_ms:.2f} ms")
        print(f"  P95 Latency:        {self.p95_latency_ms:.2f} ms")

        print("\n📈 ADDITIONAL METRICS")
        print(f"  Max Consecutive Wins:   {self.max_consecutive_wins}")
        print(f"  Max Consecutive Losses: {self.max_consecutive_losses}")
        print(f"  Avg Trade Interval:     {self.avg_trade_interval:.1f} s")
        print("=" * 80)