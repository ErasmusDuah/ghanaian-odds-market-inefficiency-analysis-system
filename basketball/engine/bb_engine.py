"""
Basketball Arbitrage Engine — Exhaustive pairwise scanner.

Markets:
  - Winner (2-way):   Home vs Away across all platform pairs
  - Overtime (Yes/No): Will there be overtime? across all platform pairs

All-out pairing: every platform's odds are tested against every other
platform's odds. No odds are left out. For N platforms with odds,
we test N*(N-1) directional pairs per market.
"""

import json
import os
import re
from datetime import datetime
from difflib import SequenceMatcher
from dotenv import dotenv_values

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')

MIN_ARB_PROFIT_PCT        = 0.01
MAX_ARB_PROFIT_PCT        = 15.0
MIN_QUASI_PROFIT_GHS      = 0.50
MIN_UNBALANCED_PROFIT_GHS = 0.10


# ── STAKING HELPERS ────────────────────────────────────────────────────────────

def _quasi_stakes(odds_list, total_stake):
    """
    Quasi-arb stakes — size the LOWEST-odds leg to break exactly even,
    distribute the remainder proportionally across all other legs.
    Returns None if not feasible.
    """
    worst_idx   = odds_list.index(min(odds_list))
    worst_odds  = odds_list[worst_idx]
    stake_worst = round(total_stake / worst_odds, 2)
    remaining   = round(total_stake - stake_worst, 2)
    other_odds  = [o for i, o in enumerate(odds_list) if i != worst_idx]
    if not other_odds or remaining <= 0:
        return None
    oarb = sum(1 / o for o in other_odds)
    other_stk = [round((1 / o) / oarb * remaining, 2) for o in other_odds]
    stakes, it = [], iter(other_stk)
    for i in range(len(odds_list)):
        stakes.append(stake_worst if i == worst_idx else next(it))
    return stakes


def _profits(odds_list, stakes):
    total = sum(stakes)
    return [round(o * s - total, 2) for o, s in zip(odds_list, stakes)]


# ── PLATFORM REGISTRY ──────────────────────────────────────────────────────────

PLATFORMS = [
    'sportybet', 'betway', 'footballcom',
    'onexbet', 'twentytwobet', 'msport',
]

PLATFORM_DISPLAY = {
    'sportybet':    'Sportybet',
    'betway':       'Betway',
    'footballcom':  'Football.com',
    'onexbet':      '1xBet',
    '1xbet':        '1xBet',
    'twentytwobet': '22Bet',
    'msport':       'MSport',
}

SOURCE_MAP = {
    'sportybet':    'sportybet_gh',
    'betway':       'betway_gh',
    'footballcom':  'footballcom_gh',
    'onexbet':      '1xbet_gh',
    'twentytwobet': 'twentytwobet_gh',
    'msport':       'msport_gh',
}


# ── TEAM NAME MATCHING ─────────────────────────────────────────────────────────

def clean_team_tokens(name: str) -> set[str]:
    name = str(name).lower().strip()
    name = re.sub(r'[^a-z0-9]', ' ', name)
    return set(name.split())


def team_names_match(a: str, b: str) -> bool:
    tokens_a = clean_team_tokens(a)
    tokens_b = clean_team_tokens(b)
    
    if not tokens_a or not tokens_b:
        return False
        
    if tokens_a == tokens_b:
        return True
        
    if tokens_a.issubset(tokens_b) and len(tokens_a) >= 2:
        return True
    if tokens_b.issubset(tokens_a) and len(tokens_b) >= 2:
        return True
        
    intersection = tokens_a.intersection(tokens_b)
    if len(intersection) >= 2:
        return True
        
    str_a = ' '.join(sorted(tokens_a))
    str_b = ' '.join(sorted(tokens_b))
    if SequenceMatcher(None, str_a, str_b).ratio() >= 0.8:
        return True
        
    return False


# ── EVENT MATCHING (UNION-FIND) ────────────────────────────────────────────────

def _matches_same_game(a, b):
    if a['source'] == b['source']:
        return False, False
        
    date_a = a.get('kickoff', '')[:10]
    date_b = b.get('kickoff', '')[:10]
    if date_a != date_b:
        return False, False

    home_a, away_a = a.get('home_team', ''), a.get('away_team', '')
    home_b, away_b = b.get('home_team', ''), b.get('away_team', '')

    match_normal = team_names_match(home_a, home_b) and team_names_match(away_a, away_b)
    if match_normal:
        return True, False

    match_inverted = team_names_match(home_a, away_b) and team_names_match(away_a, home_b)
    if match_inverted:
        return True, True

    return False, False


def match_all_platforms(all_matches):
    from collections import defaultdict
    n = len(all_matches)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i in range(n):
        for j in range(i + 1, n):
            same, inverted = _matches_same_game(all_matches[i], all_matches[j])
            if same:
                union(i, j)

    components = defaultdict(list)
    for i in range(n):
        components[find(i)].append(i)

    groups = []
    for root, indices in components.items():
        if len(indices) < 2:
            continue
        sources = [all_matches[i]['source'] for i in indices]
        if len(set(sources)) < 2:
            continue
        
        # Resolve team inversion relative to the root match
        normalized_matches = []
        root_idx = indices[0]
        root_match = all_matches[root_idx]
        
        normalized_matches.append({**root_match, 'is_inverted': False})
        
        for idx in indices[1:]:
            m = all_matches[idx]
            same, inverted = _matches_same_game(root_match, m)
            normalized_matches.append({**m, 'is_inverted': inverted})
            
        groups.append({'matches': normalized_matches, 'sources': sources})

    return groups


def validate_odds(home_odds, away_odds):
    if home_odds <= 1.01 or away_odds <= 1.01:
        return False
    arb_sum = 1 / home_odds + 1 / away_odds
    if arb_sum < 0.80 or arb_sum > 1.50:
        return False
    return True


# ── EXHAUSTIVE 2-WAY WINNER SCANNER ───────────────────────────────────────────
#
# All-out pairing: for every pair of platforms (i, j) where i != j,
# test Home(i) vs Away(j) AND Away(i) vs Home(j).
# No odds are left untested.

def scan_2way_arb(group, total_stake):
    matches = group['matches']
    opportunities = []

    for i in range(len(matches)):
        for j in range(len(matches)):
            if i == j:
                continue
            
            m1 = matches[i]
            m2 = matches[j]
            
            if m1['source'] == m2['source']:
                continue

            o1 = m1.get('odds_2way', {})
            o2 = m2.get('odds_2way', {})
            
            if not o1 or not o2:
                continue
                
            h1 = float(o1.get('home', 0.0))
            a1 = float(o1.get('away', 0.0))
            h2 = float(o2.get('home', 0.0))
            a2 = float(o2.get('away', 0.0))
            
            if not validate_odds(h1, a1) or not validate_odds(h2, a2):
                continue
                
            # Handle team inversion
            relative_inversion = m1['is_inverted'] != m2['is_inverted']
            if relative_inversion:
                h2_actual = a2
                a2_actual = h2
                m2_outcome_h = f"Away Win ({m2['away_team']})"
                m2_outcome_a = f"Home Win ({m2['home_team']})"
            else:
                h2_actual = h2
                a2_actual = a2
                m2_outcome_h = f"Home Win ({m2['home_team']})"
                m2_outcome_a = f"Away Win ({m2['away_team']})"

            # Scenario A: Back Home on m1, Away on m2
            arb_sum_a = 1 / h1 + 1 / a2_actual
            profit_pct_a = ((1 - arb_sum_a) / arb_sum_a) * 100
            
            # Scenario B: Back Away on m1, Home on m2
            arb_sum_b = 1 / a1 + 1 / h2_actual
            profit_pct_b = ((1 - arb_sum_b) / arb_sum_b) * 100

            # --- BALANCED ARB: SCENARIO A ---
            if MIN_ARB_PROFIT_PCT <= profit_pct_a <= MAX_ARB_PROFIT_PCT:
                stake1 = round((1 / h1) / arb_sum_a * total_stake, 2)
                stake2 = round((1 / a2_actual) / arb_sum_a * total_stake, 2)
                win1 = round(h1 * stake1 - (stake1 + stake2), 2)
                win2 = round(a2_actual * stake2 - (stake1 + stake2), 2)
                
                opportunities.append({
                    'category': 'balanced',
                    'market': 'Winner',
                    'arb_sum': round(arb_sum_a, 4),
                    'profit_pct': round(profit_pct_a, 2),
                    'profit_ghs': round(total_stake * profit_pct_a / 100, 2),
                    'best_outcome': f"Home Win ({m1['home_team']})",
                    'best_profit_ghs': round(total_stake * profit_pct_a / 100, 2),
                    'bets': [
                        {
                            'outcome': f"Home Win ({m1['home_team']})",
                            'platform': PLATFORM_DISPLAY.get(m1['source'].replace('_gh', ''), m1['source']),
                            'odds': h1,
                            'stake': stake1,
                            'profit_if_wins': win1
                        },
                        {
                            'outcome': m2_outcome_a,
                            'platform': PLATFORM_DISPLAY.get(m2['source'].replace('_gh', ''), m2['source']),
                            'odds': a2_actual,
                            'stake': stake2,
                            'profit_if_wins': win2
                        }
                    ]
                })

            # --- UNBALANCED ARB: SCENARIO A ---
            fl_stake = round(total_stake / 2, 2)
            fl_stk = [fl_stake, fl_stake]
            fl_prf = [
                round(h1 * fl_stake - sum(fl_stk), 2),
                round(a2_actual * fl_stake - sum(fl_stk), 2)
            ]
            if all(p > MIN_UNBALANCED_PROFIT_GHS for p in fl_prf):
                min_p = min(fl_prf)
                max_p = max(fl_prf)
                max_out = f"Home Win ({m1['home_team']})" if fl_prf[0] == max_p else m2_outcome_a
                
                opportunities.append({
                    'category': 'unbalanced',
                    'market': 'Winner',
                    'arb_sum': round(arb_sum_a, 4),
                    'profit_pct': round(profit_pct_a, 2),
                    'profit_ghs': round(total_stake * profit_pct_a / 100, 2),
                    'min_profit_ghs': min_p,
                    'max_profit_ghs': max_p,
                    'max_outcome': max_out,
                    'bets': [
                        {
                            'outcome': f"Home Win ({m1['home_team']})",
                            'platform': PLATFORM_DISPLAY.get(m1['source'].replace('_gh', ''), m1['source']),
                            'odds': h1,
                            'stake': fl_stk[0],
                            'profit_if_wins': fl_prf[0]
                        },
                        {
                            'outcome': m2_outcome_a,
                            'platform': PLATFORM_DISPLAY.get(m2['source'].replace('_gh', ''), m2['source']),
                            'odds': a2_actual,
                            'stake': fl_stk[1],
                            'profit_if_wins': fl_prf[1]
                        }
                    ]
                })

            # --- QUASI-ARB: SCENARIO A ---
            if profit_pct_a < MIN_ARB_PROFIT_PCT:
                odds_list = [h1, a2_actual]
                q_stk = _quasi_stakes(odds_list, total_stake)
                if q_stk:
                    q_prf = _profits(odds_list, q_stk)
                    worst_idx = odds_list.index(min(odds_list))
                    worst_ret = odds_list[worst_idx] * q_stk[worst_idx]
                    if abs(worst_ret - total_stake) <= 0.50:
                        other_prf = [p for idx, p in enumerate(q_prf) if idx != worst_idx]
                        if other_prf and max(other_prf) >= MIN_QUASI_PROFIT_GHS:
                            best_p = max(other_prf)
                            best_idx = 1 - worst_idx
                            best_out = f"Home Win ({m1['home_team']})" if best_idx == 0 else m2_outcome_a
                            worst_out = f"Home Win ({m1['home_team']})" if worst_idx == 0 else m2_outcome_a
                            
                            opportunities.append({
                                'category': 'quasi',
                                'market': 'Winner',
                                'arb_sum': round(arb_sum_a, 4),
                                'profit_pct': round((best_p / total_stake) * 100, 2),
                                'profit_ghs': best_p,
                                'best_outcome': best_out,
                                'best_profit_ghs': best_p,
                                'break_even_outcome': worst_out,
                                'bets': [
                                    {
                                        'outcome': f"Home Win ({m1['home_team']})",
                                        'platform': PLATFORM_DISPLAY.get(m1['source'].replace('_gh', ''), m1['source']),
                                        'odds': h1,
                                        'stake': q_stk[0],
                                        'profit_if_wins': q_prf[0]
                                    },
                                    {
                                        'outcome': m2_outcome_a,
                                        'platform': PLATFORM_DISPLAY.get(m2['source'].replace('_gh', ''), m2['source']),
                                        'odds': a2_actual,
                                        'stake': q_stk[1],
                                        'profit_if_wins': q_prf[1]
                                    }
                                ]
                            })
            
            # --- BALANCED ARB: SCENARIO B ---
            if MIN_ARB_PROFIT_PCT <= profit_pct_b <= MAX_ARB_PROFIT_PCT:
                stake1 = round((1 / a1) / arb_sum_b * total_stake, 2)
                stake2 = round((1 / h2_actual) / arb_sum_b * total_stake, 2)
                win1 = round(a1 * stake1 - (stake1 + stake2), 2)
                win2 = round(h2_actual * stake2 - (stake1 + stake2), 2)
                
                opportunities.append({
                    'category': 'balanced',
                    'market': 'Winner',
                    'arb_sum': round(arb_sum_b, 4),
                    'profit_pct': round(profit_pct_b, 2),
                    'profit_ghs': round(total_stake * profit_pct_b / 100, 2),
                    'best_outcome': f"Away Win ({m1['away_team']})",
                    'best_profit_ghs': round(total_stake * profit_pct_b / 100, 2),
                    'bets': [
                        {
                            'outcome': f"Away Win ({m1['away_team']})",
                            'platform': PLATFORM_DISPLAY.get(m1['source'].replace('_gh', ''), m1['source']),
                            'odds': a1,
                            'stake': stake1,
                            'profit_if_wins': win1
                        },
                        {
                            'outcome': m2_outcome_h,
                            'platform': PLATFORM_DISPLAY.get(m2['source'].replace('_gh', ''), m2['source']),
                            'odds': h2_actual,
                            'stake': stake2,
                            'profit_if_wins': win2
                        }
                    ]
                })

            # --- UNBALANCED ARB: SCENARIO B ---
            fl_stake = round(total_stake / 2, 2)
            fl_stk = [fl_stake, fl_stake]
            fl_prf = [
                round(a1 * fl_stake - sum(fl_stk), 2),
                round(h2_actual * fl_stake - sum(fl_stk), 2)
            ]
            if all(p > MIN_UNBALANCED_PROFIT_GHS for p in fl_prf):
                min_p = min(fl_prf)
                max_p = max(fl_prf)
                max_out = f"Away Win ({m1['away_team']})" if fl_prf[0] == max_p else m2_outcome_h
                
                opportunities.append({
                    'category': 'unbalanced',
                    'market': 'Winner',
                    'arb_sum': round(arb_sum_b, 4),
                    'profit_pct': round(profit_pct_b, 2),
                    'profit_ghs': round(total_stake * profit_pct_b / 100, 2),
                    'min_profit_ghs': min_p,
                    'max_profit_ghs': max_p,
                    'max_outcome': max_out,
                    'bets': [
                        {
                            'outcome': f"Away Win ({m1['away_team']})",
                            'platform': PLATFORM_DISPLAY.get(m1['source'].replace('_gh', ''), m1['source']),
                            'odds': a1,
                            'stake': fl_stk[0],
                            'profit_if_wins': fl_prf[0]
                        },
                        {
                            'outcome': m2_outcome_h,
                            'platform': PLATFORM_DISPLAY.get(m2['source'].replace('_gh', ''), m2['source']),
                            'odds': h2_actual,
                            'stake': fl_stk[1],
                            'profit_if_wins': fl_prf[1]
                        }
                    ]
                })

            # --- QUASI-ARB: SCENARIO B ---
            if profit_pct_b < MIN_ARB_PROFIT_PCT:
                odds_list = [a1, h2_actual]
                q_stk = _quasi_stakes(odds_list, total_stake)
                if q_stk:
                    q_prf = _profits(odds_list, q_stk)
                    worst_idx = odds_list.index(min(odds_list))
                    worst_ret = odds_list[worst_idx] * q_stk[worst_idx]
                    if abs(worst_ret - total_stake) <= 0.50:
                        other_prf = [p for idx, p in enumerate(q_prf) if idx != worst_idx]
                        if other_prf and max(other_prf) >= MIN_QUASI_PROFIT_GHS:
                            best_p = max(other_prf)
                            best_idx = 1 - worst_idx
                            best_out = f"Away Win ({m1['away_team']})" if best_idx == 0 else m2_outcome_h
                            worst_out = f"Away Win ({m1['away_team']})" if worst_idx == 0 else m2_outcome_h
                            
                            opportunities.append({
                                'category': 'quasi',
                                'market': 'Winner',
                                'arb_sum': round(arb_sum_b, 4),
                                'profit_pct': round((best_p / total_stake) * 100, 2),
                                'profit_ghs': best_p,
                                'best_outcome': best_out,
                                'best_profit_ghs': best_p,
                                'break_even_outcome': worst_out,
                                'bets': [
                                    {
                                        'outcome': f"Away Win ({m1['away_team']})",
                                        'platform': PLATFORM_DISPLAY.get(m1['source'].replace('_gh', ''), m1['source']),
                                        'odds': a1,
                                        'stake': q_stk[0],
                                        'profit_if_wins': q_prf[0]
                                    },
                                    {
                                        'outcome': m2_outcome_h,
                                        'platform': PLATFORM_DISPLAY.get(m2['source'].replace('_gh', ''), m2['source']),
                                        'odds': h2_actual,
                                        'stake': q_stk[1],
                                        'profit_if_wins': q_prf[1]
                                    }
                                ]
                            })


    return opportunities


# ── EXHAUSTIVE OVERTIME (YES/NO) SCANNER ───────────────────────────────────────
#
# All-out pairing: for every pair of platforms (i, j) where i != j,
# test OT-Yes(i) vs OT-No(j) AND OT-No(i) vs OT-Yes(j).

def scan_overtime_arb(group, total_stake):
    matches = group['matches']
    opportunities = []

    for i in range(len(matches)):
        for j in range(len(matches)):
            if i == j:
                continue

            m1 = matches[i]
            m2 = matches[j]

            if m1['source'] == m2['source']:
                continue

            ot1 = m1.get('odds_overtime', {})
            ot2 = m2.get('odds_overtime', {})

            if not ot1 or not ot2:
                continue

            yes1 = float(ot1.get('yes', 0.0))
            no1  = float(ot1.get('no', 0.0))
            yes2 = float(ot2.get('yes', 0.0))
            no2  = float(ot2.get('no', 0.0))

            if yes1 <= 1.01 or no2 <= 1.01:
                pass
            else:
                # Scenario: OT-Yes from m1, OT-No from m2
                arb_sum = 1 / yes1 + 1 / no2
                if arb_sum < 0.80 or arb_sum > 1.50:
                    pass
                else:
                    profit_pct = ((1 - arb_sum) / arb_sum) * 100

                    if MIN_ARB_PROFIT_PCT <= profit_pct <= MAX_ARB_PROFIT_PCT:
                        s1 = round((1 / yes1) / arb_sum * total_stake, 2)
                        s2 = round((1 / no2) / arb_sum * total_stake, 2)
                        w1 = round(yes1 * s1 - (s1 + s2), 2)
                        w2 = round(no2 * s2 - (s1 + s2), 2)

                        opportunities.append({
                            'category': 'balanced',
                            'market': 'Overtime',
                            'arb_sum': round(arb_sum, 4),
                            'profit_pct': round(profit_pct, 2),
                            'profit_ghs': round(total_stake * profit_pct / 100, 2),
                            'best_outcome': 'Overtime Yes',
                            'best_profit_ghs': round(total_stake * profit_pct / 100, 2),
                            'bets': [
                                {
                                    'outcome': 'Overtime Yes',
                                    'platform': PLATFORM_DISPLAY.get(m1['source'].replace('_gh', ''), m1['source']),
                                    'odds': yes1,
                                    'stake': s1,
                                    'profit_if_wins': w1
                                },
                                {
                                    'outcome': 'Overtime No',
                                    'platform': PLATFORM_DISPLAY.get(m2['source'].replace('_gh', ''), m2['source']),
                                    'odds': no2,
                                    'stake': s2,
                                    'profit_if_wins': w2
                                }
                            ]
                        })

                    # --- UNBALANCED ARB: OT ---
                    fl_stake = round(total_stake / 2, 2)
                    fl_stk = [fl_stake, fl_stake]
                    fl_prf = [
                        round(yes1 * fl_stake - sum(fl_stk), 2),
                        round(no2 * fl_stake - sum(fl_stk), 2)
                    ]
                    if all(p > MIN_UNBALANCED_PROFIT_GHS for p in fl_prf):
                        min_p = min(fl_prf)
                        max_p = max(fl_prf)
                        max_out = 'Overtime Yes' if fl_prf[0] == max_p else 'Overtime No'
                        
                        opportunities.append({
                            'category': 'unbalanced',
                            'market': 'Overtime',
                            'arb_sum': round(arb_sum, 4),
                            'profit_pct': round(profit_pct, 2),
                            'profit_ghs': round(total_stake * profit_pct / 100, 2),
                            'min_profit_ghs': min_p,
                            'max_profit_ghs': max_p,
                            'max_outcome': max_out,
                            'bets': [
                                {
                                    'outcome': 'Overtime Yes',
                                    'platform': PLATFORM_DISPLAY.get(m1['source'].replace('_gh', ''), m1['source']),
                                    'odds': yes1,
                                    'stake': fl_stk[0],
                                    'profit_if_wins': fl_prf[0]
                                },
                                {
                                    'outcome': 'Overtime No',
                                    'platform': PLATFORM_DISPLAY.get(m2['source'].replace('_gh', ''), m2['source']),
                                    'odds': no2,
                                    'stake': fl_stk[1],
                                    'profit_if_wins': fl_prf[1]
                                }
                            ]
                        })

                    # Quasi-arb for OT Yes(m1) vs No(m2)
                    if profit_pct < MIN_ARB_PROFIT_PCT:
                        odds_list = [yes1, no2]
                        q_stk = _quasi_stakes(odds_list, total_stake)
                        if q_stk:
                            q_prf = _profits(odds_list, q_stk)
                            worst_idx = odds_list.index(min(odds_list))
                            worst_ret = odds_list[worst_idx] * q_stk[worst_idx]
                            if abs(worst_ret - total_stake) <= 0.50:
                                other_prf = [p for idx, p in enumerate(q_prf) if idx != worst_idx]
                                if other_prf and max(other_prf) >= MIN_QUASI_PROFIT_GHS:
                                    best_p = max(other_prf)
                                    best_idx = 1 - worst_idx
                                    best_out = 'Overtime Yes' if best_idx == 0 else 'Overtime No'
                                    worst_out = 'Overtime Yes' if worst_idx == 0 else 'Overtime No'

                                    opportunities.append({
                                        'category': 'quasi',
                                        'market': 'Overtime',
                                        'arb_sum': round(arb_sum, 4),
                                        'profit_pct': round((best_p / total_stake) * 100, 2),
                                        'profit_ghs': best_p,
                                        'best_outcome': best_out,
                                        'best_profit_ghs': best_p,
                                        'break_even_outcome': worst_out,
                                        'bets': [
                                            {
                                                'outcome': 'Overtime Yes',
                                                'platform': PLATFORM_DISPLAY.get(m1['source'].replace('_gh', ''), m1['source']),
                                                'odds': yes1,
                                                'stake': q_stk[0],
                                                'profit_if_wins': q_prf[0]
                                            },
                                            {
                                                'outcome': 'Overtime No',
                                                'platform': PLATFORM_DISPLAY.get(m2['source'].replace('_gh', ''), m2['source']),
                                                'odds': no2,
                                                'stake': q_stk[1],
                                                'profit_if_wins': q_prf[1]
                                            }
                                        ]
                                    })

    return opportunities


# ── OUTPUT HELPERS ─────────────────────────────────────────────────────────────

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')


def _write_opportunity_to_file(f, opp):
    """Write a single opportunity's full details to a file handle."""
    cat = opp.get('category', 'balanced')
    cat_icons = {'balanced': '⚖️ ', 'unbalanced': '📊', 'quasi': '🛡️ '}
    f.write(f"\n  {'='*55}\n")
    f.write(f"  🏆 {opp['match']}\n")
    f.write(f"  📅 {opp['kickoff']} | {opp['tournament']}\n")
    f.write(f"  {'='*55}\n")
    f.write(f"  📊 Market:   {opp['market']}\n")
    f.write(f"  {cat_icons.get(cat, '')} Category: {cat.upper()}\n")

    if cat == 'balanced':
        f.write(f"  💰 Profit:   {opp['profit_pct']:.2f}% = GHS {opp['profit_ghs']:.2f} (guaranteed on all outcomes)\n")
    elif cat == 'unbalanced':
        f.write(f"  📉 Min:      GHS {opp['min_profit_ghs']:.2f}  (guaranteed floor)\n")
        f.write(f"  📈 Max:      GHS {opp['max_profit_ghs']:.2f}  ← if {opp['max_outcome']} wins\n")
    elif cat == 'quasi':
        f.write(f"  🛡️  Break-Even: {opp['break_even_outcome']} (get stake back)\n")
        f.write(f"  💰 Best:       GHS {opp['best_profit_ghs']:.2f} ← if {opp['best_outcome']} wins\n")

    f.write(f"  💵 Stake:    GHS {sum(b['stake'] for b in opp['bets']):.2f}\n")
    f.write(f"\n  📋 BETS TO PLACE:\n")
    for bet in opp['bets']:
        profit = bet['profit_if_wins']
        if abs(profit) < 0.02:
            profit = 0.0
        f.write(f"\n     🎯 {bet['platform']}\n")
        f.write(f"        Bet:   {bet['outcome']}\n")
        f.write(f"        Odds:  {bet['odds']}\n")
        f.write(f"        Stake: GHS {bet['stake']:.2f}\n")
        f.write(f"        Win:   GHS {profit:.2f}\n")


def display_opportunity(opp):
    cat = opp.get('category', 'balanced')
    cat_icons = {'balanced': '⚖️ ', 'unbalanced': '📊', 'quasi': '🛡️ '}
    print(f"\n  {'='*55}")
    print(f"  🏆 {opp['match']}")
    print(f"  📅 {opp['kickoff']} | {opp['tournament']}")
    print(f"  {'='*55}")
    print(f"  📊 Market:   {opp['market']}")
    print(f"  {cat_icons.get(cat, '')} Category: {cat.upper()}")

    if cat == 'balanced':
        print(f"  💰 Profit:   {opp['profit_pct']:.2f}% = GHS {opp['profit_ghs']:.2f} (guaranteed on all outcomes)")
    elif cat == 'unbalanced':
        print(f"  📉 Min:      GHS {opp['min_profit_ghs']:.2f}  (guaranteed floor)")
        print(f"  📈 Max:      GHS {opp['max_profit_ghs']:.2f}  ← if {opp['max_outcome']} wins")
    elif cat == 'quasi':
        print(f"  🛡️  Break-Even: {opp['break_even_outcome']} (get stake back)")
        print(f"  💰 Best:       GHS {opp['best_profit_ghs']:.2f} ← if {opp['best_outcome']} wins")

    total_stake_used = sum(bet.get('stake', 0) for bet in opp['bets'])
    print(f"  💵 Total Stake: GHS {total_stake_used:.2f}")
    print(f"\n  📋 BETS TO PLACE:")
    for bet in opp['bets']:
        profit = bet['profit_if_wins']
        if abs(profit) < 0.02:
            profit = 0.0
        print(f"\n     🎯 {bet['platform']}")
        print(f"        Bet:   {bet['outcome']}")
        print(f"        Odds:  {bet['odds']}")
        print(f"        Stake: GHS {bet['stake']:.2f}")
        print(f"        Win:   GHS {profit:.2f}")


# ── MAIN SCAN ENTRY POINT ─────────────────────────────────────────────────────

def scan_all(sportybet_matches,
             betway_matches,
             footballcom_matches,
             onexbet_matches,
             twentytwobet_matches,
             msport_matches,
             total_stake=None,
             scrape_time=None,
             scan_time=None,
             total_time=None,
             calc_end_str=None,
             next_run_str=None):

    if total_stake is None:
        _env = dotenv_values(_ENV_PATH)
        total_stake = int(_env.get('STARTING_CAPITAL', 500))

    all_matches = (sportybet_matches   +
                   betway_matches      +
                   footballcom_matches +
                   onexbet_matches     +
                   twentytwobet_matches +
                   msport_matches)

    groups = match_all_platforms(all_matches)

    if not groups:
        print("\n⚠️ No matching events found across platforms!")
        return [], 0

    opportunities = []

    for group in groups:
        matches = group['matches']
        first = matches[0]
        match_name = f"{first['home_team']} vs {first['away_team']}"
        kickoff = first['kickoff']
        tournament = first['tournament']

        # Find best tournament label
        for m in matches:
            t = m.get('tournament', '')
            if '.' in t and len(t) > len(tournament):
                tournament = t
                break

        # ── EXHAUSTIVE: 2-way Winner arb ──
        winner_opps = scan_2way_arb(group, total_stake)
        for opp in winner_opps:
            opportunities.append({
                'match': match_name,
                'kickoff': kickoff,
                'tournament': tournament,
                **opp
            })

        # ── EXHAUSTIVE: Overtime Yes/No arb ──
        overtime_opps = scan_overtime_arb(group, total_stake)
        for opp in overtime_opps:
            opportunities.append({
                'match': match_name,
                'kickoff': kickoff,
                'tournament': tournament,
                **opp
            })

    # ── Split by category ─────────────────────────────────────────────────
    balanced_opps   = [o for o in opportunities if o['category'] == 'balanced']
    unbalanced_opps = [o for o in opportunities if o['category'] == 'unbalanced']
    quasi_opps      = [o for o in opportunities if o['category'] == 'quasi']

    # ── Write to .txt files ───────────────────────────────────────────────
    sep = '=' * 60
    os.makedirs(_DATA_DIR, exist_ok=True)

    bal_path = os.path.join(_DATA_DIR, 'bb_balanced.txt')
    with open(bal_path, 'w', encoding='utf-8') as f:
        f.write(f"⚖️  BALANCED ARBITRAGE (BASKETBALL) — {len(balanced_opps)} opportunities\n")
        f.write(f"Guaranteed equal profit on ALL outcomes\n")
        f.write(f"{sep}\n")
        if balanced_opps:
            for opp in sorted(balanced_opps, key=lambda x: x['profit_pct'], reverse=True):
                _write_opportunity_to_file(f, opp)
        else:
            f.write("  💡 No balanced arb opportunities right now\n")

    unb_path = os.path.join(_DATA_DIR, 'bb_unbalanced.txt')
    with open(unb_path, 'w', encoding='utf-8') as f:
        f.write(f"📊 UNBALANCED ARBITRAGE (BASKETBALL) — {len(unbalanced_opps)} opportunities\n")
        f.write(f"All outcomes profitable — amounts differ\n")
        f.write(f"{sep}\n")
        if unbalanced_opps:
            for opp in sorted(unbalanced_opps, key=lambda x: x['max_profit_ghs'], reverse=True):
                _write_opportunity_to_file(f, opp)
        else:
            f.write("  💡 No unbalanced arb opportunities right now\n")

    qua_path = os.path.join(_DATA_DIR, 'bb_quasi.txt')
    with open(qua_path, 'w', encoding='utf-8') as f:
        f.write(f"🛡️  QUASI-ARB (BASKETBALL) — {len(quasi_opps)} opportunities\n")
        f.write(f"Near-arbitrage training data\n")
        f.write(f"{sep}\n")
        if quasi_opps:
            for opp in sorted(quasi_opps, key=lambda x: abs(x.get('profit_ghs', 0)), reverse=True):
                _write_opportunity_to_file(f, opp)
        else:
            f.write("  💡 No quasi-arb opportunities right now\n")

    # ── Compact terminal summary (matches football format) ─────────────
    print(f"\n{sep}")
    print(f"🏀 Events scanned  : {len(groups)}")
    print(f"🌐 Platforms       : 6 (Sportybet, Betway, Football.com, 1xBet, 22Bet, MSport)")
    print(f"⚖️  Balanced        : {len(balanced_opps)} → {bal_path}")
    print(f"📊 Unbalanced      : {len(unbalanced_opps)} → {unb_path}")
    print(f"🛡️  Quasi-Arbs      : {len(quasi_opps)} → {qua_path}")
    
    all_arbs = balanced_opps + unbalanced_opps
    if all_arbs:
        best = max(all_arbs, key=lambda x: x['profit_pct'])
        best_cat = best.get('category', 'balanced').capitalize()
        print(f"💰 Total profit    : GHS {sum(o['profit_ghs'] for o in all_arbs):.2f}")
        print(f"📈 Best            : {best['profit_pct']:.2f}% on {best['match']} ({best_cat})")
    else:
        print(f"💡 No balanced arb opportunities right now")
        
    if scrape_time is not None and scan_time is not None and total_time is not None:
        print(f"🌐 Scraping        : {scrape_time:.2f}s  ({scrape_time/60:.3f} min)")
        calc_suffix = f"  (Finished calculations at {calc_end_str})" if calc_end_str else ""
        print(f"🔍 Scanning        : {scan_time:.2f}s  ({scan_time/60:.3f} min){calc_suffix}")
        print(f"🕐 TOTAL           : {total_time:.2f}s  ({total_time/60:.3f} min)")
        if next_run_str:
            print(f"\n[Scheduled] Next run is at {next_run_str}")
    print(sep)
    # Display balanced and unbalanced opportunities
    if balanced_opps:
        for opp in balanced_opps:
            display_opportunity(opp)
    if unbalanced_opps:
        for opp in unbalanced_opps:
            display_opportunity(opp)

    return opportunities, len(groups)
