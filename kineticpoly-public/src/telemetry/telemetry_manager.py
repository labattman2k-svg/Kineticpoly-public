"""
Telemetry Manager – SQLite-backed logging for trades, positions, and metrics.
Provides a simple interface for recording and querying telemetry data.
"""
import sqlite3
import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class TelemetryManager:
    def __init__(self, db_path: str = "telemetry.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()

            c.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT,
                    side TEXT,
                    size REAL,
                    price REAL,
                    pnl REAL,
                    fee REAL,
                    strategy TEXT,
                    timestamp TEXT
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS position_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT,
                    size REAL,
                    avg_price REAL,
                    market_price REAL,
                    unrealized_pnl REAL,
                    timestamp TEXT
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS metrics_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    equity REAL,
                    balance REAL,
                    exposure REAL,
                    drawdown REAL,
                    open_positions INTEGER,
                    total_trades INTEGER,
                    daily_pnl REAL,
                    unrealized_pnl REAL,
                    timestamp TEXT
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT,
                    token_id TEXT,
                    side TEXT,
                    price REAL,
                    size REAL,
                    status TEXT,
                    strategy TEXT,
                    timestamp TEXT
                )
            """)

            conn.commit()
            conn.close()
            logger.debug(f"Telemetry database initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize telemetry database: {e}")

    def record_trade(self, trade: Dict[str, Any]):
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()
            c.execute("""
                INSERT INTO trades (token_id, side, size, price, pnl, fee, strategy, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get("token_id"),
                trade.get("side"),
                trade.get("size"),
                trade.get("price"),
                trade.get("pnl", 0.0),
                trade.get("fee", 0.0),
                trade.get("strategy", "UNKNOWN"),
                trade.get("timestamp", datetime.now(timezone.utc).isoformat())
            ))
            conn.commit()
            conn.close()
            logger.debug(f"📊 Trade recorded: {trade.get('token_id')[:12]} {trade.get('side')} {trade.get('size'):.2f} @ {trade.get('price'):.4f} P&L=${trade.get('pnl',0):.2f}")
        except Exception as e:
            logger.warning(f"Failed to record trade to telemetry: {e}")

    def snapshot_positions(self, positions: Dict[str, Dict[str, Any]]):
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()
            timestamp = datetime.now(timezone.utc).isoformat()
            for token_id, data in positions.items():
                c.execute("""
                    INSERT INTO position_snapshots (token_id, size, avg_price, market_price, unrealized_pnl, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    token_id,
                    data.get("size", 0.0),
                    data.get("avg_price", 0.0),
                    data.get("market_price", 0.0),
                    data.get("unrealized_pnl", 0.0),
                    timestamp
                ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to snapshot positions: {e}")

    def snapshot_metrics(self, metrics: Dict[str, Any]):
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()
            c.execute("""
                INSERT INTO metrics_snapshots (
                    equity, balance, exposure, drawdown, open_positions,
                    total_trades, daily_pnl, unrealized_pnl, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metrics.get("equity", 0.0),
                metrics.get("balance", 0.0),
                metrics.get("exposure", 0.0),
                metrics.get("drawdown", 0.0),
                metrics.get("open_positions", 0),
                metrics.get("total_trades", 0),
                metrics.get("daily_pnl", 0.0),
                metrics.get("unrealized_pnl", 0.0),
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to snapshot metrics: {e}")

    def record_order(self, order: Dict[str, Any]):
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()
            c.execute("""
                INSERT INTO orders (order_id, token_id, side, price, size, status, strategy, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.get("order_id"),
                order.get("token_id"),
                order.get("side"),
                order.get("price"),
                order.get("size"),
                order.get("status"),
                order.get("strategy", "UNKNOWN"),
                order.get("timestamp", datetime.now(timezone.utc).isoformat())
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to record order: {e}")

    def sync_reconciled_telemetry(self, engine) -> Dict[str, Any]:
        """
        Reconcile realised P&L from the trades table against the live
        account state exposed by `engine`. The engine must provide:
            engine.get_total_value() -> float
            engine.initial_balance   -> float
        """
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()
            c.execute("SELECT SUM(pnl) FROM trades")
            row = c.fetchone()
            realized_pnl = row[0] if row[0] is not None else 0.0

            total_value = engine.get_total_value()
            initial = engine.initial_balance
            total_pnl = total_value - initial
            unrealized_pnl = total_pnl - realized_pnl

            c.execute("SELECT COUNT(*) FROM trades")
            fills = c.fetchone()[0] or 0

            try:
                c.execute("SELECT COUNT(*) FROM orders WHERE status = 'FILLED'")
                filled_orders = c.fetchone()[0] or 0
                c.execute("SELECT COUNT(*) FROM orders")
                total_orders = c.fetchone()[0] or 1
                fill_rate = (filled_orders / total_orders) * 100 if total_orders > 0 else 0.0
            except sqlite3.OperationalError:
                fill_rate = 0.0

            conn.close()
            return {
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "total_pnl": total_pnl,
                "fill_rate_pct": fill_rate,
                "avg_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "avg_slippage": 0.0,
            }
        except Exception as e:
            logger.warning(f"Failed to reconcile telemetry: {e}")
            return {
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "fill_rate_pct": 0.0,
                "avg_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "avg_slippage": 0.0,
            }

    def get_trades(self, limit: int = 100, token_id: Optional[str] = None) -> List[Dict]:
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()
            if token_id:
                c.execute("SELECT * FROM trades WHERE token_id = ? ORDER BY timestamp DESC LIMIT ?", (token_id, limit))
            else:
                c.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = c.fetchall()
            columns = [desc[0] for desc in c.description]
            conn.close()
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.warning(f"Failed to get trades: {e}")
            return []

    # ── NEW: alias for convenience ──
    def get_recent_trades(self, limit: int = 100) -> List[Dict]:
        """Alias for get_trades(limit)."""
        return self.get_trades(limit=limit)

    def get_positions_snapshot(self, limit: int = 1) -> List[Dict]:
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()
            c.execute("SELECT * FROM position_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = c.fetchall()
            columns = [desc[0] for desc in c.description]
            conn.close()
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.warning(f"Failed to get positions: {e}")
            return []

    def get_metrics_snapshot(self, limit: int = 1) -> List[Dict]:
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()
            c.execute("SELECT * FROM metrics_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = c.fetchall()
            columns = [desc[0] for desc in c.description]
            conn.close()
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.warning(f"Failed to get metrics: {e}")
            return []

    def clear_all(self):
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            c = conn.cursor()
            c.execute("DELETE FROM trades")
            c.execute("DELETE FROM position_snapshots")
            c.execute("DELETE FROM metrics_snapshots")
            c.execute("DELETE FROM orders")
            conn.commit()
            conn.close()
            logger.info("🧹 All telemetry data cleared.")
        except Exception as e:
            logger.warning(f"Failed to clear telemetry: {e}")