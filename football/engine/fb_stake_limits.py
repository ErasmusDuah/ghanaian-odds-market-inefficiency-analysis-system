"""Stake-limit lookup helpers for football arb output.

Limits are warning-only. They never remove or recalculate an opportunity; they
only annotate a bet leg when the required stake is above a known max stake.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, Optional

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
_LIMITS_PATH = os.path.join(_DATA_DIR, 'stake_limits.json')
_LIMIT_KEYS = (
    'stake_limit', 'max_stake', 'maxStake', 'maximum_stake', 'maximumStake',
    'max_bet', 'maxBet', 'maximum_bet', 'maximumBet', 'limit', 'stakeLimit',
)


def _norm(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def _money(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in _LIMIT_KEYS:
            found = _money(value.get(key))
            if found is not None:
                return found
        return None
    try:
        text = str(value).replace(',', '').strip()
        match = re.search(r'-?\d+(?:\.\d+)?', text)
        if not match:
            return None
        amount = float(match.group(0))
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _first_limit(*values: Any) -> Optional[float]:
    for value in values:
        amount = _money(value)
        if amount is not None:
            return amount
    return None


@lru_cache(maxsize=1)
def _load_manual_limits() -> Dict[str, Any]:
    try:
        with open(_LIMITS_PATH, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def reload_stake_limits() -> None:
    _load_manual_limits.cache_clear()


def _iter_manual_rules(platform_key: str, platform_display: str) -> Iterable[Dict[str, Any]]:
    data = _load_manual_limits()
    rules = data.get('rules', data if isinstance(data.get('rules'), list) else [])
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict):
                yield rule

    platforms = data.get('platforms') or {}
    for name in {platform_key, platform_display, _norm(platform_key), _norm(platform_display)}:
        entries = platforms.get(name) if isinstance(platforms, dict) else None
        if isinstance(entries, list):
            for rule in entries:
                if isinstance(rule, dict):
                    yield {**rule, 'platform': name}


def _rule_matches(rule: Dict[str, Any], *, platform_key: str, platform_display: str,
                  match: Dict[str, Any], market_key: str, outcome_key: str,
                  line: Optional[str]) -> bool:
    if rule.get('enabled') is False:
        return False

    platform_rule = rule.get('platform') or rule.get('book') or rule.get('bookmaker')
    if platform_rule:
        allowed = {_norm(platform_key), _norm(platform_display)}
        if _norm(platform_rule) not in allowed:
            return False

    market_rule = rule.get('market') or rule.get('market_key')
    if market_rule and _norm(market_rule) not in {_norm(market_key), _norm(market_key.replace('odds_', ''))}:
        return False

    outcome_rule = rule.get('outcome') or rule.get('bet')
    if outcome_rule and _norm(outcome_rule) != _norm(outcome_key):
        return False

    if rule.get('line') is not None and str(rule.get('line')) != str(line):
        return False

    home_contains = rule.get('home_contains') or rule.get('home')
    if home_contains and _norm(home_contains) not in _norm(match.get('home_team')):
        return False

    away_contains = rule.get('away_contains') or rule.get('away')
    if away_contains and _norm(away_contains) not in _norm(match.get('away_team')):
        return False

    tournament_text = _norm(match.get('tournament'))
    league_contains = rule.get('league_contains') or rule.get('tournament_contains') or rule.get('competition_contains')
    if league_contains and _norm(league_contains) not in tournament_text:
        return False

    country_contains = rule.get('country_contains') or rule.get('country')
    if country_contains and _norm(country_contains) not in tournament_text:
        return False

    return True


def _manual_limit(platform_key: str, platform_display: str, match: Dict[str, Any],
                  market_key: str, outcome_key: str, line: Optional[str]) -> Optional[float]:
    for rule in _iter_manual_rules(platform_key, platform_display):
        if _rule_matches(rule, platform_key=platform_key, platform_display=platform_display,
                         match=match, market_key=market_key, outcome_key=outcome_key, line=line):
            limit = _first_limit(rule.get('max_stake'), rule.get('stake_limit'), rule.get('limit'), rule.get('max_bet'))
            if limit is not None:
                return limit
    return None


def _embedded_limit(match: Dict[str, Any], market_key: str, outcome_key: str, line: Optional[str]) -> Optional[float]:
    limits = match.get('stake_limits') or match.get('limits') or match.get('max_stakes')
    candidates = []
    if isinstance(limits, dict):
        keys = [
            f'{market_key}:{line}:{outcome_key}' if line is not None else None,
            f'{market_key}:{outcome_key}',
            f'{market_key}:{line}' if line is not None else None,
            market_key,
            'default',
        ]
        for key in keys:
            if key and key in limits:
                candidates.append(limits[key])
    elif isinstance(limits, list):
        for entry in limits:
            if not isinstance(entry, dict):
                continue
            if entry.get('market') and _norm(entry.get('market')) not in {_norm(market_key), _norm(market_key.replace('odds_', ''))}:
                continue
            if entry.get('outcome') and _norm(entry.get('outcome')) != _norm(outcome_key):
                continue
            if entry.get('line') is not None and str(entry.get('line')) != str(line):
                continue
            candidates.append(entry)

    market = match.get(market_key) or {}
    if isinstance(market, dict):
        if line is None:
            raw = market.get(outcome_key)
            if isinstance(raw, dict):
                candidates.append(raw)
            candidates.extend([market.get(f'{outcome_key}_limit'), market.get(f'{outcome_key}_max_stake')])
        else:
            line_data = market.get(str(line)) or market.get(line) or {}
            if isinstance(line_data, dict):
                raw = line_data.get(outcome_key)
                if isinstance(raw, dict):
                    candidates.append(raw)
                candidates.extend([line_data.get(f'{outcome_key}_limit'), line_data.get(f'{outcome_key}_max_stake')])

    return _first_limit(*candidates)


def resolve_stake_limit(match: Dict[str, Any], platform_key: str, platform_display: str,
                        market_key: str, outcome_key: str, line: Optional[str] = None) -> Optional[float]:
    embedded = _embedded_limit(match, market_key, outcome_key, line)
    if embedded is not None:
        return embedded
    return _manual_limit(platform_key, platform_display, match, market_key, outcome_key, line)


def format_limit_warning(required_stake: float, limit: Optional[float]) -> str:
    if limit is None:
        return ''
    try:
        required = float(required_stake)
        max_allowed = float(limit)
    except (TypeError, ValueError):
        return ''
    if required <= max_allowed + 0.005:
        return ''
    if abs(max_allowed - round(max_allowed)) < 0.005:
        shown = f'{round(max_allowed):.0f}'
    else:
        shown = f'{max_allowed:.2f}'.rstrip('0').rstrip('.')
    return f'  (limit of GHS {shown})'