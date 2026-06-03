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


PLATFORMS = [
    'sportybet', 'betway', 'footballcom',
    'onexbet', 'twentytwobet', 'msport', 'bangbet',
]

PLATFORM_DISPLAY = {
    'sportybet':    'Sportybet',
    'betway':       'Betway',
    'footballcom':  'Football.com',
    'onexbet':      '1xBet',
    '1xbet':        '1xBet',
    'twentytwobet': '22Bet',
    'msport':       'MSport',
    'bangbet':      'Bangbet',
}


def clean_player_tokens(name: str) -> set[str]:
    name = str(name).lower().strip()
    name = re.sub(r'[^a-z0-9]', ' ', name)
    return set(name.split())


def player_names_match(a: str, b: str) -> bool:
    tokens_a = clean_player_tokens(a)
    tokens_b = clean_player_tokens(b)
    
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


def parse_kickoff(ko_str):
    if not ko_str:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(ko_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _matches_same_game(a, b):
    if a['source'] == b['source']:
        return False, False
        
    ko_a = parse_kickoff(a.get('kickoff', ''))
    ko_b = parse_kickoff(b.get('kickoff', ''))
    if not ko_a or not ko_b:
        return False, False
        
    # Must be within 1.5 hours (5400 seconds)
    if abs((ko_a - ko_b).total_seconds()) > 5400:
        return False, False

    home_a, away_a = a.get('home_team', ''), a.get('away_team', '')
    home_b, away_b = b.get('home_team', ''), b.get('away_team', '')

    match_normal = player_names_match(home_a, home_b) and player_names_match(away_a, away_b)
    if match_normal:
        return True, False

    match_inverted = player_names_match(home_a, away_b) and player_names_match(away_a, home_b)
    if match_inverted:
        return True, True

    return False, False


def match_all_platforms(all_matches):
    from collections import defaultdict, deque
    n = len(all_matches)
    adj = defaultdict(list)
    
    # Build direct match relationships
    for i in range(n):
        for j in range(i + 1, n):
            same, inverted = _matches_same_game(all_matches[i], all_matches[j])
            if same:
                adj[i].append((j, inverted))
                adj[j].append((i, inverted))

    visited = [False] * n
    groups = []
    
    for i in range(n):
        if visited[i]:
            continue
            
        component_indices = []
        inversion_map = {i: False}
        
        queue = deque([i])
        visited[i] = True
        
        while queue:
            curr = queue.popleft()
            component_indices.append(curr)
            curr_inv = inversion_map[curr]
            
            for neighbor, edge_inverted in adj[curr]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    inversion_map[neighbor] = curr_inv ^ edge_inverted
                    queue.append(neighbor)
                    
        if len(component_indices) < 2:
            continue
            
        sources = [all_matches[idx]['source'] for idx in component_indices]
        if len(set(sources)) < 2:
            continue
            
        normalized_matches = []
        for idx in component_indices:
            m = all_matches[idx]
            normalized_matches.append({**m, 'is_inverted': inversion_map[idx]})
            
        groups.append({'matches': normalized_matches, 'sources': sources})

    return groups


def validate_odds(home_odds, away_odds):
    if home_odds <= 1.01 or away_odds <= 1.01:
        return False
    arb_sum = 1 / home_odds + 1 / away_odds
    if arb_sum < 0.80 or arb_sum > 1.50:
        return False
    return True


def scan_2way_arb(group, total_stake):
    matches = group['matches']
    opportunities = []

    # Test every pair of platforms
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
                
            # Grab odds
            h1 = float(o1.get('home', 0.0))
            a1 = float(o1.get('away', 0.0))
            
            h2 = float(o2.get('home', 0.0))
            a2 = float(o2.get('away', 0.0))
            
            if not validate_odds(h1, a1) or not validate_odds(h2, a2):
                continue
                
            # If match 2 is inverted relative to match 1, swap home and away on match 2
            # Wait, is_inverted in the list is relative to root.
            # Relation of m2 to m1: they are inverted if their relative inversion states to root are different!
            relative_inversion = m1['is_inverted'] != m2['is_inverted']
            if relative_inversion:
                # swap h2 and a2
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
                            'platform': PLATFORM_DISPLAY[m1['source'].replace('_gh', '')],
                            'odds': h1,
                            'stake': stake1,
                            'profit_if_wins': win1
                        },
                        {
                            'outcome': m2_outcome_a,
                            'platform': PLATFORM_DISPLAY[m2['source'].replace('_gh', '')],
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
                            'platform': PLATFORM_DISPLAY[m1['source'].replace('_gh', '')],
                            'odds': h1,
                            'stake': fl_stk[0],
                            'profit_if_wins': fl_prf[0]
                        },
                        {
                            'outcome': m2_outcome_a,
                            'platform': PLATFORM_DISPLAY[m2['source'].replace('_gh', '')],
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
                        other_prf = [p for i, p in enumerate(q_prf) if i != worst_idx]
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
                                        'platform': PLATFORM_DISPLAY[m1['source'].replace('_gh', '')],
                                        'odds': h1,
                                        'stake': q_stk[0],
                                        'profit_if_wins': q_prf[0]
                                    },
                                    {
                                        'outcome': m2_outcome_a,
                                        'platform': PLATFORM_DISPLAY[m2['source'].replace('_gh', '')],
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
                            'platform': PLATFORM_DISPLAY[m1['source'].replace('_gh', '')],
                            'odds': a1,
                            'stake': stake1,
                            'profit_if_wins': win1
                        },
                        {
                            'outcome': m2_outcome_h,
                            'platform': PLATFORM_DISPLAY[m2['source'].replace('_gh', '')],
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
                            'platform': PLATFORM_DISPLAY[m1['source'].replace('_gh', '')],
                            'odds': a1,
                            'stake': fl_stk[0],
                            'profit_if_wins': fl_prf[0]
                        },
                        {
                            'outcome': m2_outcome_h,
                            'platform': PLATFORM_DISPLAY[m2['source'].replace('_gh', '')],
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
                        other_prf = [p for i, p in enumerate(q_prf) if i != worst_idx]
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
                                        'platform': PLATFORM_DISPLAY[m1['source'].replace('_gh', '')],
                                        'odds': a1,
                                        'stake': q_stk[0],
                                        'profit_if_wins': q_prf[0]
                                    },
                                    {
                                        'outcome': m2_outcome_h,
                                        'platform': PLATFORM_DISPLAY[m2['source'].replace('_gh', '')],
                                        'odds': h2_actual,
                                        'stake': q_stk[1],
                                        'profit_if_wins': q_prf[1]
                                    }
                                ]
                            })

    return opportunities


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

        opps = scan_2way_arb(group, total_stake)
        for opp in opps:
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

    bal_path = os.path.join(_DATA_DIR, 'tt_balanced.txt')
    with open(bal_path, 'w', encoding='utf-8') as f:
        f.write(f"⚖️  BALANCED ARBITRAGE (TABLE TENNIS) — {len(balanced_opps)} opportunities\n")
        f.write(f"Guaranteed equal profit on ALL outcomes\n")
        f.write(f"{sep}\n")
        if balanced_opps:
            for opp in sorted(balanced_opps, key=lambda x: x['profit_pct'], reverse=True):
                _write_opportunity_to_file(f, opp)
        else:
            f.write("  💡 No balanced arb opportunities right now\n")

    unb_path = os.path.join(_DATA_DIR, 'tt_unbalanced.txt')
    with open(unb_path, 'w', encoding='utf-8') as f:
        f.write(f"📊 UNBALANCED ARBITRAGE (TABLE TENNIS) — {len(unbalanced_opps)} opportunities\n")
        f.write(f"All outcomes profitable — amounts differ\n")
        f.write(f"{sep}\n")
        if unbalanced_opps:
            for opp in sorted(unbalanced_opps, key=lambda x: x['max_profit_ghs'], reverse=True):
                _write_opportunity_to_file(f, opp)
        else:
            f.write("  💡 No unbalanced arb opportunities right now\n")

    qua_path = os.path.join(_DATA_DIR, 'tt_quasi.txt')
    with open(qua_path, 'w', encoding='utf-8') as f:
        f.write(f"🛡️  QUASI-ARB (TABLE TENNIS) — {len(quasi_opps)} opportunities\n")
        f.write(f"Near-arbitrage training data\n")
        f.write(f"{sep}\n")
        if quasi_opps:
            for opp in sorted(quasi_opps, key=lambda x: abs(x.get('profit_ghs', 0)), reverse=True):
                _write_opportunity_to_file(f, opp)
        else:
            f.write("  💡 No quasi-arb opportunities right now\n")

    # ── Compact terminal summary (matches football/basketball format) ─────────────
    print(f"\n{sep}")
    print(f"🏓 Events scanned  : {len(groups)}")
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

    return opportunities, len(groups)
