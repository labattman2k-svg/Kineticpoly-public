#!/usr/bin/env python3
"""Test which public-search query forms surface NFL/CFB game events."""
import time
import requests

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0"})

QUERIES = [
    # current mapper form (baseline)
    "Seahawks NFL",
    "Chiefs NFL",
    # the form that worked in your test
    "Seahawks Commanders",
    "Chiefs Dolphins",
    # single nickname, no suffix
    "Seahawks",
    "Chiefs",
    # nickname + "vs"
    "Seahawks vs",
    "Chiefs vs",
    # tag-style
    "NFL (All)",
    "CFB (All)",
    # league prefix
    "NFL:",
    "CFB:",
    # date-ish
    "NFL September 2026",
]


def count_games(events):
    """Count events whose FIRST market is a two-sided team moneyline."""
    n = 0
    for ev in events:
        title = (ev.get("title") or "").lower()
        if "vs" not in title and " v " not in title:
            continue
        mks = ev.get("markets") or []
        if not mks:
            continue
        q = (mks[0].get("question") or "").lower()
        if ":" in q:
            continue
        if any(kw in q for kw in
               ("quarter", "touchdown", "spread", "o/u", "total",
                "period", "champion", "season", "playoff",
                "heisman", "award", "draft")):
            continue
        n += 1
    return n


for q in QUERIES:
    try:
        r = s.get("https://gamma-api.polymarket.com/public-search",
                  params={"q": q, "limit_per_type": 50},
                  timeout=15)
        if r.status_code != 200:
            print(f"{q!r:<32} HTTP {r.status_code}")
            continue
        data = r.json()
    except Exception as e:
        print(f"{q!r:<32} EXC {type(e).__name__}: {e}")
        continue

    events = data.get("events") or []
    games = count_games(events)
    marker = " ⭐" if games > 0 else ""
    print(f"{q!r:<32} events={len(events):<4} games={games}{marker}")
    time.sleep(0.4)