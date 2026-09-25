#!/usr/bin/env python3
"""Check whether a specific NFL/CFB matchup exists as a moneyline on Polymarket."""
import sys
import requests

QUERIES = sys.argv[1:] or [
    "Seahawks Commanders",
    "Bengals Steelers",
    "Chiefs Dolphins",
]

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})

for q in QUERIES:
    print(f"\n=== query: {q!r} ===")
    try:
        r = s.get("https://gamma-api.polymarket.com/public-search",
                  params={"q": q, "limit_per_type": 50},
                  timeout=20)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}")
            continue
        data = r.json()
    except Exception as e:
        print(f"  exception: {e}")
        continue

    events = data.get("events") or []
    print(f"  {len(events)} events returned")
    for ev in events[:10]:
        title = ev.get("title") or ""
        slug = ev.get("slug") or ""
        tags = [t.get("label") if isinstance(t, dict) else t
                for t in (ev.get("tags") or [])]
        markets = ev.get("markets") or []
        print(f"\n  event: {title!r}")
        print(f"    slug: {slug}")
        print(f"    tags: {tags}")
        print(f"    {len(markets)} markets:")
        for m in markets[:8]:
            q_txt = m.get("question") or ""
            outcomes = m.get("outcomes") or ""
            print(f"      - {q_txt[:90]}")
            print(f"        outcomes: {outcomes}")