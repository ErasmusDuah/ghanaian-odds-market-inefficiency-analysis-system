"""1xBet stake-limit probe helpers.

This module only validates a coupon leg and reads max-stake metadata. It never
places a bet and never calls the secure MakeBet endpoints.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

try:
    from curl_cffi import requests
except Exception:  # pragma: no cover - dependency is present in the project venv
    requests = None


ENDPOINT = "https://1xbet.com.gh/service-api/LiveBet-update/Open/UpdateCoupon"
REFERER = "https://1xbet.com.gh/en/line/football"
DEFAULT_TIMEOUT = float(os.getenv("ONEXBET_LIMIT_TIMEOUT", "2.5"))
MAX_PROBES = int(os.getenv("ONEXBET_LIMIT_MAX_PROBES", "80"))

_probe_count = 0


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _money(value: Any) -> Optional[float]:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _looks_like_limit_key(key: Any) -> bool:
    text = str(key or "").lower()
    return (
        "limit" in text
        or "maxbet" in text
        or "max_bet" in text
        or "maxstake" in text
        or "max_stake" in text
        or text in {"maxbet", "maxstake"}
    )


def _first_money_deep(value: Any) -> Optional[float]:
    if isinstance(value, dict):
        priority = (
            "maxBet", "MaxBet", "max_bet", "MaxBetAmount", "maxBetAmount",
            "MaxStake", "maxStake", "max_stake", "Limit", "limit", "BetLimit", "betLimit",
        )
        for key in priority:
            found = _money(value.get(key))
            if found is not None:
                return found
        for key, child in value.items():
            if _looks_like_limit_key(key):
                found = _money(child)
                if found is not None:
                    return found
            if isinstance(child, (dict, list)):
                found = _first_money_deep(child)
                if found is not None:
                    return found
    elif isinstance(value, list):
        for child in value:
            found = _first_money_deep(child)
            if found is not None:
                return found
    return None


def _selection(market_key: str, outcome_key: str, line: Optional[str], odds: float) -> Optional[Dict[str, Any]]:
    market = str(market_key or "")
    outcome = str(outcome_key or "").lower()

    group = None
    type_id = None
    param = 0

    if market == "odds_1x2":
        group = 1
        type_id = {"home": 1, "draw": 2, "away": 3}.get(outcome)
    elif market == "odds_dc":
        group = 8
        type_id = {"1x": 4, "12": 5, "x2": 6}.get(outcome)
    elif market == "odds_gg":
        group = 19
        type_id = {"gg": 180, "ng": 181}.get(outcome)
    elif market == "odds_ou":
        group = 17
        type_id = {"over": 9, "under": 10}.get(outcome)
        try:
            param = float(line)
        except (TypeError, ValueError):
            return None

    if group is None or type_id is None:
        return None

    return {
        "GameId": None,
        "Type": type_id,
        "Coef": float(odds),
        "Param": param,
        "PV": None,
        "PlayerId": 0,
        "Kind": 1,
        "InstrumentId": 0,
        "Seconds": 0,
        "Price": 0,
        "Expired": 0,
        "Group": group,
    }


def _headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://1xbet.com.gh",
        "Referer": REFERER,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        ),
    }
    cookie = os.getenv("ONEXBET_LIMIT_COOKIE", "").strip()
    xauth = os.getenv("ONEXBET_LIMIT_XAUTH", "").strip()
    if cookie:
        headers["Cookie"] = cookie
    if xauth:
        headers["X-Auth"] = xauth
        headers["XAuth"] = xauth
    return headers


def _user_id() -> int:
    try:
        return int(os.getenv("ONEXBET_LIMIT_USER_ID", "0") or 0)
    except ValueError:
        return 0


@lru_cache(maxsize=2048)
def _probe_cached(cache_key: Tuple[Any, ...]) -> Optional[float]:
    global _probe_count
    if requests is None or _probe_count >= MAX_PROBES:
        return None

    (
        event_id, market_key, outcome_key, line, odds, stake,
        group, country, cf_view, source,
    ) = cache_key

    selection = _selection(market_key, outcome_key, line, odds)
    if not selection:
        return None
    selection["GameId"] = int(event_id)

    payload = {
        "UserId": _user_id(),
        "Events": [selection],
        "Vid": 1,
        "ExpressNum": 0,
        "Lng": "en",
        "Country": country,
        "Group": group,
        "Summ": float(stake),
        "CfView": cf_view,
        "Source": source,
        "calcSystemsMin": False,
    }

    _probe_count += 1
    try:
        response = requests.post(
            ENDPOINT,
            json=payload,
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
            impersonate="chrome120",
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception:
        return None

    value = data.get("Value") if isinstance(data, dict) else data
    limit = _first_money_deep(value)
    if limit is None:
        return None

    # Public, logged-out responses can return maxBet=0 or minBet-like values.
    return limit if limit > 0 else None


def resolve_onexbet_runtime_limit(
    match: Dict[str, Any],
    market_key: str,
    outcome_key: str,
    line: Optional[str],
    odds: float,
    stake: float,
) -> Optional[float]:
    if not _env_flag("ONEXBET_LIMITS_ENABLED", "1"):
        return None
    event_id = match.get("event_id") or match.get("game_id")
    if not event_id:
        return None
    try:
        event_id_int = int(event_id)
        odds_float = float(odds)
        stake_float = float(stake)
    except (TypeError, ValueError):
        return None
    if odds_float <= 1.01 or stake_float <= 0:
        return None

    group = int(os.getenv("ONEXBET_LIMIT_GROUP", "40"))
    country = int(os.getenv("ONEXBET_LIMIT_COUNTRY", "80"))
    cf_view = int(os.getenv("ONEXBET_LIMIT_CF_VIEW", "0"))
    source = int(os.getenv("ONEXBET_LIMIT_SOURCE", "1"))
    rounded_stake = round(stake_float, 2)

    return _probe_cached((
        event_id_int, market_key, outcome_key, str(line) if line is not None else "",
        round(odds_float, 6), rounded_stake, group, country, cf_view, source,
    ))


def reset_onexbet_limit_cache() -> None:
    global _probe_count
    _probe_count = 0
    _probe_cached.cache_clear()

