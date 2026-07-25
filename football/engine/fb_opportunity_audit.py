"""Pre-output audit for football arbitrage opportunities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from engine.fb_market_guard import is_pseudo_or_virtual_match, MIN_DECIMAL_ODDS, MAX_DECIMAL_ODDS
except ImportError:
    from fb_market_guard import is_pseudo_or_virtual_match, MIN_DECIMAL_ODDS, MAX_DECIMAL_ODDS


@dataclass
class OpportunityAuditReport:
    input_count: int = 0
    output_count: int = 0
    dropped_count: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def add(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        self.dropped_count += 1


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _market_outcome_compatible(market: str, outcome: str) -> bool:
    market_l = str(market or '').lower()
    outcome_l = str(outcome or '').lower()
    if 'over/under' in market_l:
        return 'over' in outcome_l or 'under' in outcome_l
    if 'gg/ng' in market_l:
        return 'gg' in outcome_l or 'ng' in outcome_l or 'yes' in outcome_l or 'no' in outcome_l
    if 'double chance' in market_l:
        return any(token in outcome_l for token in ('1x', 'x2', '12', 'home win', 'away win', 'draw'))
    if '1x2' in market_l:
        return any(token in outcome_l for token in ('home win', 'away win', 'draw'))
    return True


def _drop(report: OpportunityAuditReport, reason: str) -> None:
    report.add(reason)


def audit_opportunity(opp: dict[str, Any], report: OpportunityAuditReport | None = None) -> bool:
    if not isinstance(opp, dict):
        if report:
            _drop(report, 'opp_not_dict')
        return False

    match = str(opp.get('match') or '')
    if ' vs ' not in match:
        if report:
            _drop(report, 'match_missing_vs')
        return False
    home, away = [part.strip() for part in match.split(' vs ', 1)]
    if is_pseudo_or_virtual_match({'home_team': home, 'away_team': away, 'tournament': opp.get('tournament', '')}):
        if report:
            _drop(report, 'match_pseudo_or_virtual')
        return False

    bets = opp.get('bets') or []
    if len(bets) not in (2, 3):
        if report:
            _drop(report, 'bad_leg_count')
        return False

    total_stake = sum(_f(b.get('stake')) for b in bets)
    if total_stake <= 0:
        if report:
            _drop(report, 'bad_total_stake')
        return False

    market = str(opp.get('market') or '')
    for bet in bets:
        odds = _f(bet.get('odds'))
        stake = _f(bet.get('stake'))
        if not (MIN_DECIMAL_ODDS < odds <= MAX_DECIMAL_ODDS) or stake <= 0:
            if report:
                _drop(report, 'bad_odds_or_stake')
            return False
        if not _market_outcome_compatible(market, str(bet.get('outcome') or '')):
            if report:
                _drop(report, 'market_outcome_mismatch')
            return False
        expected_profit = round(odds * stake - total_stake, 2)
        actual_profit = _f(bet.get('profit_if_wins'))
        tolerance = max(0.10, total_stake * 0.003)
        if abs(expected_profit - actual_profit) > tolerance:
            if report:
                _drop(report, 'profit_math_mismatch')
            return False

    category = str(opp.get('category') or '').lower()
    profits = [_f(b.get('profit_if_wins')) for b in bets]
    if category == 'balanced' and min(profits) <= 0:
        if report:
            _drop(report, 'balanced_not_all_positive')
        return False
    if category == 'unbalanced':
        if min(profits) <= 0:
            if report:
                _drop(report, 'unbalanced_not_all_positive')
            return False
        if abs(_f(opp.get('min_profit_ghs')) - min(profits)) > 1.0:
            if report:
                _drop(report, 'unbalanced_min_mismatch')
            return False
    if category == 'quasi':
        if min(abs(p) for p in profits) > max(1.0, total_stake * 0.002):
            if report:
                _drop(report, 'quasi_no_breakeven_leg')
            return False

    return True


def audit_opportunities(opportunities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], OpportunityAuditReport]:
    report = OpportunityAuditReport(input_count=len(opportunities or []))
    clean = []
    for opp in opportunities or []:
        if audit_opportunity(opp, report):
            clean.append(opp)
    report.output_count = len(clean)
    return clean, report


def audit_report_line(report: OpportunityAuditReport) -> str:
    return f"Arb audit: {report.input_count}->{report.output_count} opportunities, dropped {report.dropped_count}"
