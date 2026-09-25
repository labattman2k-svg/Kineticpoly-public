"""
Dashboard Server – Flask web dashboard for real-time metrics.
Robust handling of bytes, missing data, and telemetry manager.
"""
import os
import json
import sqlite3
import logging
import threading
import requests
from datetime import datetime
from typing import Any, Dict

from flask import Flask, render_template_string, jsonify, request

logger = logging.getLogger("Dashboard")
app = Flask(__name__)
telemetry_manager = None
shutdown_requested = False


def set_telemetry_manager(manager):
    global telemetry_manager
    telemetry_manager = manager


def request_shutdown():
    """Request the Flask server to shutdown gracefully."""
    global shutdown_requested
    shutdown_requested = True
    try:
        requests.post('http://127.0.0.1:5000/shutdown', timeout=2)
    except Exception as e:
        logger.debug(f"Shutdown request failed (server may already be stopped): {e}")


def safe_float(val, default=0.0) -> float:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, bytes):
        try:
            decoded = val.decode('utf-8', errors='ignore').strip()
            return float(decoded)
        except (ValueError, UnicodeDecodeError):
            return default
    if isinstance(val, str):
        try:
            return float(val.strip())
        except ValueError:
            return default
    try:
        return float(str(val))
    except (ValueError, TypeError):
        return default


def safe_int(val, default=0) -> int:
    return int(safe_float(val, default))


@app.route("/")
def index():
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Kinetic Bot – Dashboard</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                   background: #0a0e17; color: #e0e0e0; padding: 20px; min-height: 100vh; }
            .container { max-width: 1200px; margin: 0 auto; }
            h1 { color: #00d4ff; font-size: 28px; margin-bottom: 5px; }
            .subtitle { color: #8892b0; margin-bottom: 30px; }
            .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 30px; }
            .stat-card { background: #141c2b; border-radius: 12px; padding: 16px 20px; border: 1px solid #23304a; }
            .stat-label { font-size: 12px; text-transform: uppercase; color: #8892b0; letter-spacing: 0.5px; }
            .stat-value { font-size: 24px; font-weight: 600; color: #fff; margin-top: 4px; }
            .stat-value.positive { color: #00d4aa; }
            .stat-value.negative { color: #ff6b6b; }
            .footer { color: #495670; font-size: 13px; text-align: center; padding: 20px 0; border-top: 1px solid #1a2438; margin-top: 30px; }
            .refresh-btn { background: #00d4ff; color: #0a0e17; border: none; padding: 8px 20px; border-radius: 6px; cursor: pointer; font-weight: 600; margin-bottom: 20px; }
            .refresh-btn:hover { background: #00b8d4; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Kinetic Bot – Dashboard</h1>
            <div class="subtitle">Real-time performance monitoring</div>
            <button class="refresh-btn" onclick="refreshData()">🔄 Refresh</button>
            <div class="stats-grid" id="metrics">
                <div class="stat-card">
                    <div class="stat-label">Total Equity</div>
                    <div class="stat-value" id="equity">Loading...</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Total P&L</div>
                    <div class="stat-value" id="pnl">Loading...</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Total Trades</div>
                    <div class="stat-value" id="trades">Loading...</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Win Rate</div>
                    <div class="stat-value" id="winrate">Loading...</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Profit Factor</div>
                    <div class="stat-value" id="profitfactor">Loading...</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Drawdown</div>
                    <div class="stat-value" id="drawdown">Loading...</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Open Positions</div>
                    <div class="stat-value" id="positions">Loading...</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Last Updated</div>
                    <div class="stat-value" id="updated">Loading...</div>
                </div>
            </div>
            <div class="footer">Data sourced from Kinetic Telemetry Database &bull; Live</div>
        </div>
        <script>
            function refreshData() {
                fetch('/metrics')
                    .then(response => response.json())
                    .then(data => {
                        const equity = data.equity !== undefined && data.equity !== null ? data.equity : 0;
                        const pnl = data.pnl !== undefined && data.pnl !== null ? data.pnl : 0;
                        const trades = data.trades !== undefined && data.trades !== null ? data.trades : 0;
                        const winRate = data.win_rate !== undefined && data.win_rate !== null ? data.win_rate : 0;
                        const profitFactor = data.profit_factor !== undefined && data.profit_factor !== null ? data.profit_factor : 0;
                        const drawdown = data.drawdown !== undefined && data.drawdown !== null ? data.drawdown : 0;
                        const positions = data.positions !== undefined && data.positions !== null ? data.positions : 0;
                        const updated = data.updated || new Date().toISOString();

                        document.getElementById('equity').textContent = '$' + equity.toFixed(2);
                        document.getElementById('pnl').textContent = '$' + pnl.toFixed(2);
                        document.getElementById('trades').textContent = trades;
                        document.getElementById('winrate').textContent = winRate.toFixed(1) + '%';
                        document.getElementById('profitfactor').textContent = profitFactor.toFixed(2);
                        document.getElementById('drawdown').textContent = drawdown.toFixed(2) + '%';
                        document.getElementById('positions').textContent = positions;
                        document.getElementById('updated').textContent = new Date(updated).toLocaleTimeString();
                    })
                    .catch(error => {
                        console.error('Error:', error);
                    });
            }
            refreshData();
            setInterval(refreshData, 10000);
        </script>
    </body>
    </html>
    """)


@app.route("/metrics")
def metrics():
    try:
        db_path = "telemetry.db"
        if not os.path.exists(db_path):
            return jsonify({"error": "Telemetry database not found"}), 500

        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM trades")
        count_row = c.fetchone()
        total_trades = safe_int(count_row[0]) if count_row else 0
        
        c.execute("SELECT SUM(pnl) FROM trades")
        sum_row = c.fetchone()
        total_pnl = safe_float(sum_row[0]) if sum_row else 0.0

        win_rate = 0.0
        if total_trades > 0:
            c.execute("SELECT pnl FROM trades")
            all_pnl = c.fetchall()
            wins = 0
            for r in all_pnl:
                try:
                    pnl_val = safe_float(r[0])
                    if pnl_val is not None and isinstance(pnl_val, (int, float)):
                        if abs(pnl_val) > 0 and pnl_val > 0:
                            wins += 1
                except Exception as e:
                    logger.debug(f"Error in win rate calculation: {e}")
                    continue
            win_rate = (wins / total_trades) * 100

        profit_factor = 0.0
        if total_trades > 0:
            c.execute("SELECT pnl FROM trades")
            all_pnl = c.fetchall()
            gross_profit = 0.0
            gross_loss = 0.0
            for r in all_pnl:
                try:
                    pnl_val = safe_float(r[0])
                    if pnl_val is not None and isinstance(pnl_val, (int, float)):
                        if abs(pnl_val) > 0:
                            if pnl_val > 0:
                                gross_profit += pnl_val
                            else:
                                gross_loss += abs(pnl_val)
                except Exception as e:
                    logger.debug(f"Error in profit factor calculation: {e}")
                    continue
            
            if gross_loss > 0:
                profit_factor = gross_profit / gross_loss

        c.execute("SELECT equity FROM metrics_snapshots ORDER BY timestamp DESC LIMIT 1")
        equity_row = c.fetchone()
        if equity_row and equity_row[0] is not None:
            equity = safe_float(equity_row[0])
        else:
            equity = 10000.0 + total_pnl

        drawdown = 0.0
        c.execute("SELECT equity FROM metrics_snapshots")
        all_equity = c.fetchall()
        if all_equity:
            equity_values = []
            for r in all_equity:
                try:
                    eq_val = safe_float(r[0])
                    if eq_val is not None and isinstance(eq_val, (int, float)):
                        equity_values.append(eq_val)
                except Exception as e:
                    logger.debug(f"Error in drawdown calculation: {e}")
                    continue
            if equity_values:
                peak = max(equity_values)
                if peak > 0:
                    drawdown = ((peak - equity) / peak) * 100

        positions = 0
        try:
            c.execute("SELECT COUNT(DISTINCT token_id) FROM positions")
            positions_row = c.fetchone()
            positions = safe_int(positions_row[0]) if positions_row else 0
        except Exception:
            pass

        conn.close()

        return jsonify({
            "equity": round(float(equity), 2),
            "pnl": round(float(total_pnl), 2),
            "trades": int(total_trades),
            "win_rate": round(float(win_rate), 1),
            "profit_factor": round(float(profit_factor), 2),
            "drawdown": round(float(drawdown), 2),
            "positions": int(positions),
            "updated": datetime.now().isoformat(),
        })

    except Exception as e:
        logger.error(f"Dashboard metrics error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/shutdown", methods=['POST'])
def shutdown():
    global shutdown_requested
    shutdown_requested = True
    logger.info("🛑 Dashboard server shutdown requested")
    
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        logger.warning("⚠️ No shutdown function available, using alternative method")
        return jsonify({"status": "shutdown_requested"}), 200
    
    func()
    return jsonify({"status": "shutting_down"}), 200


def start_server(host='0.0.0.0', port=5000, daemon=False):
    """Start the Flask dashboard server in a background thread with daemon option."""
    global shutdown_requested
    shutdown_requested = False
    server_ready = threading.Event()
    server_error = []
    
    def run():
        try:
            server_ready.set()
            logger.info(f"📊 Starting Flask dashboard server on http://{host}:{port}")
            app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        except Exception as e:
            logger.error(f"Dashboard server error: {e}")
            server_error.append(str(e))
            server_ready.set()
    
    thread = threading.Thread(target=run, daemon=daemon)
    thread.start()
    
    if server_ready.wait(timeout=5):
        if server_error:
            logger.error(f"❌ Dashboard server failed to start: {server_error[0]}")
        else:
            logger.info(f"✅ Dashboard server started successfully on http://{host}:{port}")
    else:
        logger.error(f"❌ Dashboard server startup timeout after 5 seconds")
    
    return thread


def stop_server(timeout=5):
    """Stop the Flask dashboard server gracefully."""
    global shutdown_requested
    logger.info("🛑 Stopping dashboard server...")
    shutdown_requested = True
    try:
        request_shutdown()
    except Exception as e:
        logger.debug(f"Graceful shutdown request failed: {e}")
    logger.info("✅ Dashboard server stop signal sent")