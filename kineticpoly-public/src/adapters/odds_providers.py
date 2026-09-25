"""
External Odds Providers.

Common async interface over multiple sportsbook and prediction-market
sources. Every provider normalises to a single (home_odds, away_odds)
shape so the consensus estimator and the order-book layer can consume
them uniformly.

Implemented:
  * KalshiPublicProvider   - prediction-market order book
  * ESPNOddsProvider       - scoreboard moneylines
  * SharpAPIProvider       - per-selection rows grouped by event_uuid
  * SXBetProvider          - V3 REST API, paginated
  * TheOddsAPIProvider     - aggregator (opt-in via API key)
  * RapidAPIProvider       - aggregator (opt-in via API key)
  * SportsDataIOProvider   - aggregator (opt-in via API key)
  * BallDontLieProvider    - stats-only placeholder

Provider reliability weights, depth assumptions, matching thresholds,
and per-provider tuning live in a deployment-specific JSON file loaded
by `_load_provider_config()`. The repository ships with safe defaults
so the module is importable and testable without any private config.
"""
import os
import json
import logging
import aiohttp
import asyncio
import time
import requests
from typing import Optional, Dict, Tuple, List, Any
from abc import ABC, abstractmethod
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


# ─── Deployment-specific configuration ───────────────────────────────
_PRIVATE_CONFIG_PATH = os.getenv(
    "PROVIDER_CONFIG_PATH", "config/private/providers.json"
)


class ProviderConfig:
    """
    Tuning knobs shared across providers. Defaults are conservative
    placeholders; production deployments override them from a private
    JSON file that is not shipped in this repository.
    """

    # Provider reliability weights used by the consensus estimator.
    # Empty by default — the estimator falls back to `default_weight`.
    sharpness_weights: Dict[str, float] = {}
    default_weight: float = 0.20

    # Depth normalisation for the consensus weighting.
    depth_baseline_usd: float = 20_000.0
    depth_clamp: Tuple[float, float] = (0.5, 1.5)

    # Team-name fuzzy matching threshold (0..1).
    match_threshold: float = 0.65

    # Slippage model.
    slippage_stake_usd: float = 25.0
    slippage_discount: float = 0.5
    slippage_price_cap: float = 0.85

    # Assumed USD depth per provider for the consensus estimator.
    provider_depth_usd: Dict[str, float] = {
        "theoddsapi": 50_000.0,
        "sharpapi": 30_000.0,
        "espn": 5_000.0,
        "sxbet": 5_000.0,
        "kalshi": 1_000.0,
    }

    # Preferred bookmaker order for TheOddsAPI (ranked sharp → soft).
    bookmaker_preference: List[str] = []

    # SharpAPI pacing.
    sharpapi_min_interval: float = 5.5
    sharpapi_cache_ttl: float = 900.0

    # SX Bet protocol constants. The 1e20 odds scale is defined by the
    # SX Bet protocol (percentageOdds is a 1e20-scaled integer). Type
    # and sport IDs are deployment-specific; empty defaults disable the
    # provider until a private config is loaded.
    sx_odds_scale: float = 1e20
    sx_moneyline_types: set = set()
    sx_sport_ids: List[int] = []


def _load_provider_config() -> None:
    """Merge a private JSON config (if present) into ProviderConfig."""
    if not os.path.exists(_PRIVATE_CONFIG_PATH):
        return
    try:
        with open(_PRIVATE_CONFIG_PATH, "r") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load provider config: {e}")
        return
    for key, val in data.items():
        if hasattr(ProviderConfig, key):
            setattr(ProviderConfig, key, val)


_load_provider_config()


def calculate_weighted_consensus_fair_price(odds_data):
    """
    Weighted consensus of de-vigged moneylines.

    Each item contributes with weight = sharpness[provider] * depth_mult,
    where depth_mult is a clamped linear function of the item's depth.
    Returns (consensus_home_prob, stdev_across_providers).
    """
    if not odds_data:
        return 0.50, 0.0

    lo, hi = ProviderConfig.depth_clamp
    baseline = ProviderConfig.depth_baseline_usd
    default_w = ProviderConfig.default_weight
    weights = ProviderConfig.sharpness_weights

    weighted_sum = 0.0
    total_weight = 0.0
    probabilities = []

    for item in odds_data:
        provider = item.get("provider", "").lower()
        home_odds = item.get("home_odds")
        away_odds = item.get("away_odds")
        if (not home_odds or not away_odds
                or home_odds <= 1.0 or away_odds <= 1.0):
            continue

        raw_home = 1.0 / home_odds
        raw_away = 1.0 / away_odds
        overround = raw_home + raw_away
        if overround <= 0:
            continue
        devigged_home = raw_home / overround

        base_weight = weights.get(provider, default_w)
        depth = item.get("depth_usd", baseline)
        depth_mult = max(lo, min(hi, depth / baseline))
        final_weight = base_weight * depth_mult

        weighted_sum += devigged_home * final_weight
        total_weight += final_weight
        probabilities.append(devigged_home)

    if total_weight == 0.0:
        return 0.50, 0.0

    consensus = weighted_sum / total_weight
    if len(probabilities) > 1:
        variance = (sum((p - consensus) ** 2 for p in probabilities)
                    / len(probabilities))
    else:
        variance = 0.0
    return round(consensus, 4), round(variance ** 0.5, 4)


def get_slippage_adjusted_bounds(fair_price, order_book_asks,
                                 target_stake_usd=None):
    """
    Walk the ask book for `target_stake_usd`, compute the VWAP, and
    return (adjusted_price, fair_price). The adjusted price reflects the
    slippage incurred to fill the target stake.
    """
    if target_stake_usd is None:
        target_stake_usd = ProviderConfig.slippage_stake_usd

    if not order_book_asks:
        return min(ProviderConfig.slippage_price_cap, fair_price), fair_price

    acc_usd = acc_shares = 0.0
    for price, size in order_book_asks:
        cost = price * size
        if acc_usd + cost >= target_stake_usd:
            need = target_stake_usd - acc_usd
            acc_shares += need / price
            acc_usd = target_stake_usd
            break
        acc_usd += cost
        acc_shares += size

    if acc_shares > 0:
        vwap = acc_usd / acc_shares
        slip = max(0.0, vwap - fair_price)
        adjusted = min(
            ProviderConfig.slippage_price_cap,
            fair_price - slip * ProviderConfig.slippage_discount,
        )
    else:
        adjusted = min(ProviderConfig.slippage_price_cap, fair_price)

    return round(adjusted, 4), fair_price


# ─── Team-name matching helpers ──────────────────────────────────────
def _norm(name: str) -> str:
    s = (name or "").lower().strip()
    for suffix in (" fc", " cf", " afc", " sc", " ac"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    s = s.replace(".", "").replace("-", " ")
    return " ".join(s.split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _match(home: str, away: str,
           h_candidate: str, a_candidate: str,
           threshold: Optional[float] = None):
    """
    Classify a candidate pair as 'forward' (home↔h, away↔a),
    'reverse' (home↔a, away↔h), or no match. Returns (orientation, score).
    """
    if threshold is None:
        threshold = ProviderConfig.match_threshold

    fwd = min(_similarity(home, h_candidate), _similarity(away, a_candidate))
    rev = min(_similarity(home, a_candidate), _similarity(away, h_candidate))
    if fwd >= threshold and fwd >= rev:
        return "forward", fwd
    if rev >= threshold:
        return "reverse", rev
    return None, 0.0


# ─── Base ────────────────────────────────────────────────────────────
class OddsProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    def supports_bulk(self) -> bool:
        return False

    @abstractmethod
    async def fetch_event_odds(self, sport: str, home: str, away: str):
        ...

    async def fetch_bulk_odds(self, sport: str):
        raise NotImplementedError("Bulk fetch not implemented.")


# ─── TheOddsAPI (opt-in) ─────────────────────────────────────────────
class TheOddsAPIProvider(OddsProvider):
    def __init__(self, api_key=None):
        self.api_key = (api_key or os.getenv("ODDS_API_KEY")
                        or os.getenv("THE_ODDS_API_KEY"))
        if not self.api_key:
            logger.warning("TheOddsAPI key missing. Inactive.")
            self.api_key = None
        else:
            self.api_key = self.api_key.strip().strip("'\"")
        self.base_url = "https://api.the-odds-api.com/v4"
        self.regions = ["eu", "us"]
        self.sport_map = {
            "basketball": "basketball_nba",
            "football": "americanfootball_nfl",
            "baseball": "baseball_mlb",
            "hockey": "icehockey_nhl",
            "soccer": "soccer_epl",
        }

    @property
    def name(self):
        return "theoddsapi"

    @property
    def supports_bulk(self):
        return True

    async def fetch_bulk_odds(self, sport):
        if not self.api_key:
            return None
        sk = self.sport_map.get((sport or "").lower())
        if not sk:
            return None
        session = None
        try:
            session = aiohttp.ClientSession()
            async with session.get(
                f"{self.base_url}/sports/{sk}/odds",
                params={"apiKey": self.api_key,
                        "regions": ",".join(self.regions),
                        "markets": "h2h", "oddsFormat": "decimal"},
                timeout=15,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data if isinstance(data, list) else None
        except Exception:
            return None
        finally:
            if session and not session.closed:
                await session.close()

    async def fetch_event_odds(self, sport, home, away,
                               event_id=None, sport_key=None):
        if not self.api_key:
            return None
        if event_id:
            return await self._by_event_id(event_id)
        return None

    async def _by_event_id(self, event_id):
        candidates = [
            "soccer_epl", "soccer_spain_la_liga",
            "soccer_germany_bundesliga", "soccer_italy_serie_a",
            "soccer_france_ligue_one", "soccer_uefa_champs_league",
            "soccer_uefa_europa_league", "soccer_usa_mls",
            "basketball_nba", "americanfootball_nfl",
            "baseball_mlb", "icehockey_nhl",
        ]
        session = None
        try:
            session = aiohttp.ClientSession()
            for sk in candidates:
                try:
                    async with session.get(
                        f"{self.base_url}/sports/{sk}/events/"
                        f"{event_id}/odds",
                        params={"apiKey": self.api_key,
                                "regions": ",".join(self.regions),
                                "markets": "h2h",
                                "oddsFormat": "decimal"},
                        timeout=15,
                    ) as resp:
                        if resp.status == 404:
                            continue
                        if resp.status != 200:
                            continue
                        return self._parse_event(await resp.json())
                except Exception:
                    continue
            return None
        finally:
            if session and not session.closed:
                await session.close()

    def _parse_event(self, ev, orientation="forward"):
        bookmakers = ev.get("bookmakers", []) or []
        if not bookmakers:
            return None
        pref = ProviderConfig.bookmaker_preference
        if pref:
            ranked = sorted(
                bookmakers,
                key=lambda b: pref.index(b["key"])
                if b["key"] in pref else len(pref),
            )
        else:
            ranked = bookmakers
        for bk in ranked:
            h2h = next((m for m in bk.get("markets", [])
                        if m.get("key") == "h2h"), None)
            if not h2h:
                continue
            outcomes = h2h.get("outcomes", []) or []
            if len(outcomes) < 2:
                continue
            home_name = ev.get("home_team")
            hp = ap = None
            for o in outcomes:
                if o.get("name") == home_name:
                    hp = float(o.get("price", 0))
                else:
                    ap = float(o.get("price", 0))
            if not hp or not ap or hp <= 1.0 or ap <= 1.0:
                continue
            if orientation == "reverse":
                hp, ap = ap, hp
            pk = (bk["key"] if bk["key"] in ProviderConfig.sharpness_weights
                  else "theoddsapi")
            return {"home_odds": hp, "away_odds": ap,
                    "depth_usd": ProviderConfig.provider_depth_usd["theoddsapi"],
                    "provider": pk,
                    "bookmaker": bk["key"]}
        return None


# ─── SharpAPI ────────────────────────────────────────────────────────
class SharpAPIProvider(OddsProvider):
    """
    SharpAPI — https://api.sharpapi.io

    /odds returns one row per selection. A two-sided moneyline produces
    two rows sharing an event_uuid, distinguished by selection_type
    ("home" | "away"). We group by event_uuid, keep only
    market_type == "moneyline", and emit one game per event.

    Pagination: {"pagination": {"has_more": bool, "next_cursor": str}}.
    Pass cursor=<next_cursor> as a query param to fetch the next page.
    """

    BASE_URL = "https://api.sharpapi.io/api/v1"

    def __init__(self):
        self.api_key = (os.getenv("SHARP_API_KEY")
                        or os.getenv("SHARPAPI_KEY"))
        if not self.api_key:
            logger.warning("SharpAPI key missing. Inactive.")
            self.api_key = None
        else:
            self.api_key = self.api_key.strip().strip("'\"")
        self.timeout = 30.0
        self.sport_map = {
            "basketball": "basketball", "nba": "basketball",
            "football": "football", "nfl": "football", "cfb": "football",
            "baseball": "baseball", "mlb": "baseball",
            "hockey": "hockey", "nhl": "hockey",
            "soccer": "soccer", "tennis": "tennis",
            "cricket": "cricket",
            "rugby": "rugby",
        }
        self._min_interval = ProviderConfig.sharpapi_min_interval
        self._last_request_time = 0.0
        self._cache = {}
        self._cache_ttl = ProviderConfig.sharpapi_cache_ttl

    @property
    def name(self):
        return "sharpapi"

    @property
    def supports_bulk(self):
        return True

    def _paginate(self, sport_key, max_pages: int = 5,
                  market_type: str = "moneyline"):
        if not self.api_key:
            return []
        all_rows = []
        cursor = None
        for page in range(max_pages):
            params = {
                "sport": sport_key,
                "oddsFormat": "DECIMAL",
                "limit": 100,
            }
            if market_type:
                params["market_type"] = market_type
            if cursor:
                params["cursor"] = cursor
            try:
                r = requests.get(
                    f"{self.BASE_URL}/odds",
                    headers={"X-API-Key": self.api_key,
                             "Accept": "application/json"},
                    params=params,
                    timeout=self.timeout,
                )
            except Exception as e:
                logger.debug(f"SharpAPI paginate {sport_key}: {e}")
                break
            if r.status_code == 401:
                logger.warning("SharpAPI 401 — key rejected")
                return all_rows
            if r.status_code == 403:
                logger.warning("SharpAPI 403 — plan missing sport")
                return all_rows
            if r.status_code == 429:
                logger.warning("SharpAPI 429 — rate limited")
                break
            if r.status_code != 200:
                break
            try:
                body = r.json()
            except Exception:
                break
            rows = body.get("data", [])
            if not isinstance(rows, list) or not rows:
                break
            if market_type:
                rows = [row for row in rows
                        if (row.get("market_type") or "").lower() == market_type]
            all_rows.extend(rows)

            pg = body.get("pagination") or {}
            if not pg.get("has_more"):
                break
            cursor = pg.get("next_cursor")
            if not cursor:
                break
            if page < max_pages - 1:
                time.sleep(self._min_interval)
        return all_rows

    @staticmethod
    def _group_to_games(rows):
        by_event = {}
        for row in rows:
            if not row.get("is_active", True):
                continue
            if row.get("is_player_prop"):
                continue
            mt = (row.get("market_type") or "").lower()
            if mt != "moneyline":
                continue
            key = row.get("event_uuid") or row.get("event_id")
            if not key:
                continue
            by_event.setdefault(key, []).append(row)

        games = []
        for key, ev_rows in by_event.items():
            home_row = next(
                (r for r in ev_rows
                 if r.get("selection_type") == "home"
                 or r.get("team_side") == "home"), None)
            away_row = next(
                (r for r in ev_rows
                 if r.get("selection_type") == "away"
                 or r.get("team_side") == "away"), None)
            if not home_row or not away_row:
                continue
            try:
                ho = float(home_row.get("odds_decimal"))
                ao = float(away_row.get("odds_decimal"))
            except (ValueError, TypeError):
                continue
            if ho <= 1.0 or ao <= 1.0:
                continue
            games.append({
                "event_uuid": key,
                "event_id": home_row.get("event_id"),
                "sport": home_row.get("sport"),
                "league": home_row.get("league"),
                "home_team": home_row.get("home_team"),
                "away_team": home_row.get("away_team"),
                "home_odds": ho,
                "away_odds": ao,
                "commence_time": home_row.get("event_start_time"),
                "sportsbook": home_row.get("sportsbook"),
                "is_live": bool(home_row.get("is_live", False)),
            })
        return games

    async def fetch_bulk_odds(self, sport):
        if not self.api_key:
            return None
        sk = self.sport_map.get((sport or "").lower())
        if not sk:
            return None
        now = time.time()
        if sk in self._cache                 and (now - self._cache[sk][0]) < self._cache_ttl:
            return self._cache[sk][1]

        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

        loop = asyncio.get_event_loop()
        try:
            rows = await loop.run_in_executor(None, self._paginate, sk)
        except Exception as e:
            logger.debug(f"SharpAPI bulk {sk} failed: {e}")
            return self._cache.get(sk, (None, None))[1]

        games = self._group_to_games(rows)
        self._cache[sk] = (time.time(), games)
        return games

    async def fetch_event_odds(self, sport, home, away, **kwargs):
        bulk = await self.fetch_bulk_odds(sport)
        if not bulk:
            return None
        for g in bulk:
            orient, _ = _match(home or "", away or "",
                               g.get("home_team") or "",
                               g.get("away_team") or "")
            if not orient:
                continue
            ho, ao = g["home_odds"], g["away_odds"]
            if orient == "reverse":
                ho, ao = ao, ho
            return {"home_odds": ho, "away_odds": ao,
                    "depth_usd": ProviderConfig.provider_depth_usd["sharpapi"],
                    "provider": "sharpapi",
                    "sportsbook": g.get("sportsbook")}
        return None


# ─── RapidAPI (opt-in) ───────────────────────────────────────────────
class RapidAPIProvider(OddsProvider):
    def __init__(self):
        self.api_key = os.getenv("RAPID_API_KEY")
        if not self.api_key:
            logger.warning("RapidAPI key missing. Inactive.")
            self.api_key = None
        else:
            self.api_key = self.api_key.strip().strip("'\"")
        self.host = os.getenv("RAPID_API_HOST",
                              "odds-rapid-api.p.rapidapi.com")
        self.base_url = f"https://{self.host}"
        self._last_request_time = 0.0
        self._min_interval = 2.0

    @property
    def name(self):
        return "rapidapi"

    async def _fetch_raw(self, sport):
        if not self.api_key:
            return None
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()
        session = None
        try:
            session = aiohttp.ClientSession()
            async with session.get(
                f"{self.base_url}/v1/odds",
                headers={"X-RapidAPI-Key": self.api_key,
                         "X-RapidAPI-Host": self.host},
                params={"sport": (sport or "").lower(),
                        "region": "us", "oddsFormat": "decimal"},
                timeout=10,
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception:
            return None
        finally:
            if session and not session.closed:
                await session.close()

    async def fetch_event_odds(self, sport, home, away, **kwargs):
        return None


# ─── SportsDataIO (opt-in) ───────────────────────────────────────────
class SportsDataIOProvider(OddsProvider):
    def __init__(self):
        self.api_key = os.getenv("SPORTS_DATA_IO_KEY")
        if not self.api_key:
            logger.warning("SportsDataIO key missing. Inactive.")
            self.api_key = None
        else:
            self.api_key = self.api_key.strip().strip("'\"")

    @property
    def name(self):
        return "sportsdataio"

    async def fetch_event_odds(self, sport, home, away, **kwargs):
        return None


# ─── BallDontLie (stats only) ────────────────────────────────────────
class BallDontLieProvider(OddsProvider):
    @property
    def name(self):
        return "balldontlie"

    async def fetch_event_odds(self, sport, home, away):
        return None


# ─── ESPN ────────────────────────────────────────────────────────────
class ESPNOddsProvider(OddsProvider):
    SPORT_ENDPOINTS = {
        "soccer": ("soccer", "eng.1"),
        "baseball": ("baseball", "mlb"),
        "football": ("football", "nfl"),
        "basketball": ("basketball", "nba"),
        "hockey": ("hockey", "nhl"),
    }

    def __init__(self, timeout=10):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    @property
    def name(self):
        return "espn"

    @staticmethod
    def _clean(n):
        s = (n or "").lower().strip()
        s = s.replace(".", "").replace("-", " ")
        return " ".join(s.split())

    @classmethod
    def _sim(cls, a, b):
        return SequenceMatcher(None, cls._clean(a), cls._clean(b)).ratio()

    @staticmethod
    def _devig(oa, ob):
        def dec(o):
            if o > 0:
                return (o / 100.0) + 1.0
            if o < 0:
                return (100.0 / abs(o)) + 1.0
            return o
        da, db = dec(oa), dec(ob)
        ia, ib = 1.0 / da, 1.0 / db
        t = ia + ib
        return ia / t, ib / t

    async def _scoreboard(self, sp, lg, match_date):
        url = (f"https://site.api.espn.com/apis/site/v2/sports/"
               f"{sp}/{lg}/scoreboard")
        params = {}
        if match_date:
            params["dates"] = match_date.replace("-", "")
        session = None
        try:
            session = aiohttp.ClientSession()
            async with session.get(url, headers=self.headers,
                                   params=params,
                                   timeout=self.timeout) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        except Exception:
            return []
        finally:
            if session and not session.closed:
                await session.close()

        out = []
        for event in data.get("events", []):
            comps = event.get("competitions", [])
            if not comps:
                continue
            comp = comps[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue
            eh = ea = None
            for c in competitors:
                if c.get("homeAway") == "home":
                    eh = c["team"]["displayName"]
                else:
                    ea = c["team"]["displayName"]
            if not eh or not ea:
                continue
            odds_list = comp.get("odds", [])
            if not odds_list:
                continue
            main = odds_list[0]
            hml = main.get("homeTeamOdds", {}).get("moneyLine")
            aml = main.get("awayTeamOdds", {}).get("moneyLine")
            if not hml or not aml:
                continue
            fh, fa = self._devig(hml, aml)
            out.append({"home_team": eh, "away_team": ea,
                        "home_odds": 1.0 / fh if fh > 0 else 0.0,
                        "away_odds": 1.0 / fa if fa > 0 else 0.0})
        return out

    async def fetch_league_odds_map(self, sport, league, match_date,
                                    teams=None):
        sk = (sport or "").lower()
        sp = self.SPORT_ENDPOINTS.get(sk, ("soccer", "eng.1"))[0]
        events = await self._scoreboard(sp, league, match_date)
        out = {}
        for ev in events:
            for t in (ev["home_team"], ev["away_team"]):
                out[self._clean(t)] = {"home_odds": ev["home_odds"],
                                       "away_odds": ev["away_odds"]}
        return out

    async def fetch_event_odds(self, sport, home, away,
                               league=None, match_date=None):
        sk = (sport or "").lower()
        if league:
            sp = self.SPORT_ENDPOINTS.get(sk, ("soccer", "eng.1"))[0]
            lg = league
        elif sk in self.SPORT_ENDPOINTS:
            sp, lg = self.SPORT_ENDPOINTS[sk]
        else:
            return None
        events = await self._scoreboard(sp, lg, match_date)
        for ev in events:
            orient, _ = _match(home or "", away or "",
                               ev["home_team"], ev["away_team"])
            if not orient and home:
                if self._sim(home, ev["home_team"]) >= 0.55:
                    orient = "forward"
                elif self._sim(home, ev["away_team"]) >= 0.55:
                    orient = "reverse"
            if not orient:
                continue
            ho, ao = ev["home_odds"], ev["away_odds"]
            if orient == "reverse":
                ho, ao = ao, ho
            return {"home_odds": ho, "away_odds": ao,
                    "depth_usd": ProviderConfig.provider_depth_usd["espn"]}
        return None

    async def fetch_bulk_odds(self, sport):
        return None


# ─── Kalshi ──────────────────────────────────────────────────────────
class KalshiPublicProvider(OddsProvider):
    def __init__(self, timeout=10):
        self.base_url = "https://external-api.kalshi.com/trade-api/v2"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "KineticArb/1.0",
        })

    @property
    def name(self):
        return "kalshi"

    def fetch_markets(self, limit=100, status="open"):
        try:
            r = self.session.get(
                f"{self.base_url}/markets",
                params={"limit": limit, "status": status},
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json().get("markets", [])
        except Exception:
            return []

    def get_orderbook(self, ticker):
        try:
            r = self.session.get(
                f"{self.base_url}/markets/{ticker}/orderbook",
                timeout=self.timeout,
            )
            r.raise_for_status()
            raw = r.json().get("orderbook", {})
            bids = [[round(p / 100.0, 4), q]
                    for p, q in raw.get("yes", [])]
            asks = [[round((100 - p) / 100.0, 4), q]
                    for p, q in raw.get("no", [])]
            return {"ticker": ticker, "timestamp": time.time(),
                    "bids": sorted(bids, key=lambda x: x[0],
                                   reverse=True),
                    "asks": sorted(asks, key=lambda x: x[0])}
        except Exception:
            return None

    async def fetch_event_odds(self, sport, home, away):
        return None

    @staticmethod
    def _cents(v):
        if v is None:
            return None
        try:
            f = float(v)
        except (ValueError, TypeError):
            return None
        return f * 100.0 if f <= 1.0 else f

    def _market_metadata(self, ticker):
        try:
            r = self.session.get(f"{self.base_url}/markets/{ticker}",
                                 timeout=self.timeout)
            r.raise_for_status()
            return r.json().get("market", {}) or {}
        except Exception:
            return None

    def get_event_odds(self, ticker):
        book = self.get_orderbook(ticker)
        if book and book["bids"] and book["asks"]:
            try:
                bb = float(book["bids"][0][0])
                ba = float(book["asks"][0][0])
                if 0 < bb < 1 and 0 < ba < 1 and bb <= ba:
                    mid = (bb + ba) / 2.0
                    depth = sum(p * q for p, q in book["bids"][:3])
                    return {
                        "provider": "kalshi", "ticker": ticker,
                        "home_odds": round(1.0 / mid, 4)
                        if mid > 0 else 0.0,
                        "away_odds": round(1.0 / (1.0 - mid), 4)
                        if mid < 1 else 0.0,
                        "depth_usd": depth, "source": "orderbook",
                    }
            except (IndexError, TypeError, ValueError,
                    ZeroDivisionError):
                pass
        m = self._market_metadata(ticker)
        if not m:
            return None
        yb = (self._cents(m.get("yes_bid"))
              or self._cents(m.get("yes_bid_dollars")))
        ya = (self._cents(m.get("yes_ask"))
              or self._cents(m.get("yes_ask_dollars")))
        lp = (self._cents(m.get("last_price"))
              or self._cents(m.get("last_price_dollars")))
        mid_cents = None
        source = None
        if yb and ya and 0 < yb < 100 and 0 < ya < 100:
            mid_cents = (yb + ya) / 2.0
            source = "metadata_bid_ask"
        elif lp and 0 < lp < 100:
            mid_cents = lp
            source = "metadata_last_price"
        if mid_cents is None:
            return None
        mid = mid_cents / 100.0
        return {
            "provider": "kalshi", "ticker": ticker,
            "home_odds": round(1.0 / mid, 4) if mid > 0 else 0.0,
            "away_odds": round(1.0 / (1.0 - mid), 4)
            if mid < 1 else 0.0,
            "depth_usd": ProviderConfig.provider_depth_usd["kalshi"],
            "source": source,
        }

    async def fetch_bulk_odds(self, sport):
        return None


# ─── SX Bet ──────────────────────────────────────────────────────────
class SXBetProvider(OddsProvider):
    """
    SX Bet V3 REST API.

    /markets/active is paginated via paginationKey. Each market is
    screened by _is_moneyline before order-book lookup. Odds are
    scaled by ODDS_SCALE and inverted to decimal.

    Percentage odds use a 1e20 scale defined by the SX Bet protocol
    (percentageOdds is a 1e20-scaled integer). Moneyline type IDs and
    sport IDs are deployment-specific and loaded from config.
    """

    BASE_URL = "https://api.sx.bet"
    ODDS_SCALE = ProviderConfig.sx_odds_scale
    MONEYLINE_TYPES = ProviderConfig.sx_moneyline_types
    SPORT_IDS = ProviderConfig.sx_sport_ids

    def __init__(self, timeout: int = 20, max_retries: int = 4):
        self.api_key = (os.getenv("SX_API_KEY")
                        or os.getenv("SX_BET_API_KEY"))
        if self.api_key:
            self.api_key = self.api_key.strip().strip("'\"")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "KineticArb/1.0",
        })
        self._markets_cache = None
        self._markets_cache_time = 0.0
        self._markets_cache_ttl = 60.0

    @property
    def name(self):
        return "sxbet"

    @property
    def supports_bulk(self):
        return True

    def _request_with_retry(self, method, url, params=None, headers=None):
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.session.request(
                    method, url, params=params, headers=headers,
                    timeout=self.timeout,
                )
                if r.status_code < 500:
                    return r
            except (ConnectionResetError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                logger.debug(
                    f"SXBet network error {attempt}/"
                    f"{self.max_retries}: {type(e).__name__}")
            if attempt < self.max_retries:
                time.sleep(1.0 * (2 ** (attempt - 1)))
        return None

    def fetch_active_markets(self):
        """Paginate /markets/active across configured sport IDs."""
        now = time.time()
        if (self._markets_cache is not None
                and now - self._markets_cache_time
                < self._markets_cache_ttl):
            return self._markets_cache

        all_markets = []
        for sport_id in self.SPORT_IDS:
            pagination_key = None
            for _page in range(40):
                params = {"limit": 100, "sportIds": str(sport_id)}
                if pagination_key:
                    params["paginationKey"] = str(pagination_key)
                r = self._request_with_retry(
                    "GET", f"{self.BASE_URL}/markets/active",
                    params=params)
                if r is None or r.status_code != 200:
                    break
                try:
                    body = r.json()
                except Exception:
                    break
                container = body.get("data", {}) or {}
                markets = container.get("markets", []) or []
                if not markets:
                    break
                all_markets.extend(markets)
                pagination_key = (container.get("paginationKey")
                                  or container.get("nextKey"))
                if len(markets) < 100 or not pagination_key:
                    break

        self._markets_cache = all_markets
        self._markets_cache_time = now
        return all_markets

    @classmethod
    def _is_moneyline(cls, m: dict) -> bool:
        if m.get("type") in cls.MONEYLINE_TYPES:
            return True
        o1 = str(m.get("outcomeOneName", "")).strip().lower()
        o2 = str(m.get("outcomeTwoName", "")).strip().lower()
        t1 = str(m.get("teamOneName", "")).strip().lower()
        t2 = str(m.get("teamTwoName", "")).strip().lower()
        line = m.get("line")
        if line not in (None, 0, 0.0):
            return False
        return bool(t1 and t2 and o1 == t1 and o2 == t2)

    @staticmethod
    def _best_price(orders):
        best = None
        for o in orders:
            pct = o.get("percentageOdds")
            if pct is None:
                continue
            try:
                v = int(pct)
            except (ValueError, TypeError):
                continue
            if best is None or v < best:
                best = v
        return best

    def _best_odds(self, market_hash):
        headers = ({"x-sx-api-key": self.api_key}
                   if self.api_key else None)
        r = self._request_with_retry(
            "GET", f"{self.BASE_URL}/orderbook-v3/snapshot",
            params={"marketHash": market_hash},
            headers=headers)
        if r is None or r.status_code != 200:
            return None
        try:
            data = r.json().get("data", {}) or {}
        except Exception:
            return None
        o1 = data.get("outcomeOne", []) or []
        o2 = data.get("outcomeTwo", []) or []
        if not o1 or not o2:
            return None
        best1 = self._best_price(o1)
        best2 = self._best_price(o2)
        if best1 is None or best2 is None:
            return None
        p1 = best1 / self.ODDS_SCALE
        p2 = best2 / self.ODDS_SCALE
        if p1 <= 0 or p2 <= 0 or p1 >= 1 or p2 >= 1:
            return None
        return {"outcomeOne": {"decimal_odds": 1.0 / p1,
                               "implied_prob": p1},
                "outcomeTwo": {"decimal_odds": 1.0 / p2,
                               "implied_prob": p2}}

    async def fetch_event_odds(self, sport, home, away,
                               event_id=None, **kwargs):
        market = None
        if event_id:
            for m in self.fetch_active_markets():
                if (str(m.get("marketHash", "")).lower()
                        == str(event_id).lower()):
                    market = m
                    break
        if market is None:
            market = self._find_moneyline_market(home, away)
        if market is None:
            return None
        odds = self._best_odds(str(market["marketHash"]))
        if not odds:
            return None
        d1 = odds["outcomeOne"]["decimal_odds"]
        d2 = odds["outcomeTwo"]["decimal_odds"]
        t1 = str(market.get("teamOneName", "")).lower()
        h = (home or "").lower()
        if h and h not in t1:
            d1, d2 = d2, d1
        return {"home_odds": d1, "away_odds": d2,
                "depth_usd": ProviderConfig.provider_depth_usd["sxbet"],
                "provider": "sxbet",
                "market_hash": market["marketHash"]}

    def _find_moneyline_market(self, home, away):
        if not home:
            return None
        for m in self.fetch_active_markets():
            if not self._is_moneyline(m):
                continue
            orient, _ = _match(home, away or "",
                               m.get("teamOneName", ""),
                               m.get("teamTwoName", ""))
            if orient:
                return m
        return None

    async def fetch_bulk_odds(self, sport):
        """Return all active SX Bet moneylines grouped as game dicts."""
        out = []
        for m in self.fetch_active_markets():
            if not self._is_moneyline(m):
                continue
            mh = m.get("marketHash")
            if not mh:
                continue
            odds = self._best_odds(str(mh))
            if not odds:
                continue
            out.append({
                "marketHash": mh,
                "teamOneName": m.get("teamOneName"),
                "teamTwoName": m.get("teamTwoName"),
                "leagueLabel": m.get("leagueLabel"),
                "sportLabel": m.get("sportLabel"),
                "gameTime": m.get("gameTime"),
                "home_odds": odds["outcomeOne"]["decimal_odds"],
                "away_odds": odds["outcomeTwo"]["decimal_odds"],
            })
        return out
