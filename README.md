[README.md](https://github.com/user-attachments/files/32652476/README.md)
# Kinetic Poly

Market data and execution infrastructure for Polymarket sports
prediction markets. A common async interface over Polymarket (Gamma
and CLOB), Kalshi, ESPN, SharpAPI, and SX Bet; a thread-safe order
book cache; a batched fetcher; and a SQLite-backed telemetry framework
with a live dashboard.

## What's in this repo

- **Provider adapters** for Polymarket Gamma, Polymarket CLOB,
  Kalshi, ESPN, SharpAPI, and SX Bet behind a single `OddsProvider`
  interface, with per-provider quirks handled at the adapter boundary.
- **Order book cache** — thread-safe, TTL-aware, best-first normalized.
  Handles Polymarket's non-standard bid/ask level ordering.
- **Batched async fetcher** — thousands of order books per cycle with
  exponential backoff, connection pooling, and per-host concurrency
  limits.
- **Telemetry framework** — SQLite-backed trade, position, and metric
  snapshots with a real-time Flask dashboard.
- **Diagnostic scripts** — market discovery probes, telemetry
  inspectors, and search-form testers.

## What's not in this repo

Signal generation, position sizing, risk management, and the execution
strategy are proprietary and not included. This repo is the data layer.

## Architecture

```
    Polymarket API ──┐
    Kalshi API     ──┤
    ESPN API       ──┼──►  AsyncFetcher  ──►  OrderBookCache
    SharpAPI       ──┤          │                    │
    SX Bet API     ──┘          │                    ▼
                                │              [strategy layer,
                                │               private]
                                │
                                ▼
                          TelemetryManager ──► SQLite ──► Dashboard
```

## Design notes

**Polymarket's CLOB returns book levels in reverse order** — bids
ascending, asks descending, best at the end of each array. Every
consumer downstream assumes `bids[0]` and `asks[0]` are top-of-book.
We sort once on write, so no downstream code has to know about the
non-standard ordering.

**Rate-limit budgeting across six providers** is handled per-provider.
Each provider carries its own pacing state (minimum interval, cache
TTL) loaded from config, so the fetcher skips providers without keys
and backs off providers that return 429 without touching the others.

**The async fetcher batches token IDs** into 20-per-request payloads
against Polymarket's `/books` endpoint. HTTP/2 is disabled and the
SSL security level is relaxed to work around a known interop issue
with the Polymarket CDN — see the `_get_session` docstring.

**Provider reliability weights are configuration, not code.** The
weighted-consensus estimator reads an arbitrary weight map from
`ProviderConfig`, which is populated from a private JSON file at
import time. The public repo ships with an empty map and a documented
default.

## Running

```
pip install -r requirements.txt
cp .env.example .env
# edit .env with your keys
python scripts/inspect_telemetry.py
python scripts/check_nfl_listing.py "Seahawks vs"
```

The included scripts are read-only diagnostics — they query public
APIs and print results without placing any orders.

## Testing

```
pytest
```

## License

MIT. See LICENSE.
