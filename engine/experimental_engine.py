"""
EXPERIMENTAL ENGINE — Standalone. Does NOT touch main.py or arbitrage_engine.py.
Reads the same scraped JSON files and detects THREE categories:

  1. BALANCED ARB   — arb_sum < 1, equal profit on all outcomes (same as current system)
  2. UNBALANCED ARB — arb_sum < 1, ALL outcomes profitable but amounts differ
  3. QUASI-ARB      — arb_sum < 1, worst outcome breaks EXACTLY even, rest give profit

Run via: python run_experimental.py
"""

import json
import os
from difflib import SequenceMatcher
from dotenv import dotenv_values

# ── CONFIG ─────────────────────────────────────────────────────────────────────
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')

# Profit thresholds — kept very low so even tiny arb is caught
MIN_BALANCED_PROFIT_PCT   = 0.001  # catch anything above 0.001%
MIN_UNBALANCED_PROFIT_GHS = 0.10   # both legs must profit at least GHS 0.10
MIN_QUASI_PROFIT_GHS      = 0.50   # best leg must profit at least GHS 0.50
MAX_ARB_PROFIT_PCT        = 15.0   # sanity cap

# Near-arb: NOT a guaranteed profit, but SO close it's worth watching
# arb_sum between 1.00 and this value → flag as "near-arb"



# ── SHARED: FUZZY MATCHING (copied logic, not imported, to keep fully standalone) ──

def normalize_name(name):
    name = name.lower().strip()
    for suffix in [' fc', ' sc', ' cf', ' ac', ' bk', ' fk',
                   ' sk', ' if', ' bfk', ' spor', ' sport',
                   ' united', ' city', ' town']:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name


def similar(a, b):
    a_norm = normalize_name(a)
    b_norm = normalize_name(b)
    if a_norm == b_norm:
        return True
    if a_norm in b_norm or b_norm in a_norm:
        if min(len(a_norm), len(b_norm)) >= 5:
            return True
    return SequenceMatcher(None, a_norm, b_norm).ratio() >= 0.85


VIRTUAL_KEYWORDS = [
    'srl', 'simulated reality', 'esport', 'e-soccer', 'esoccer',
    'cyber', 'virtual', 'sim match', 'eadriatic', 'gt league',
    'efootball', 'e-football', 'fifa', 'pes ',
]


def is_virtual_match(match):
    text = ' '.join([
        match.get('home_team', ''),
        match.get('away_team', ''),
        match.get('tournament', ''),
    ]).lower()
    return any(kw in text for kw in VIRTUAL_KEYWORDS)


def tournament_similar(a, b):
    a_norm = a.lower()
    b_norm = b.lower()
    modifiers = ['u19', 'u20', 'u21', 'u23', 'women', 'reserves',
                 'srl', 'esport', 'virtual', 'cyber']
    for mod in modifiers:
        if (mod in a_norm) != (mod in b_norm):
            return False
    return True


def match_all_platforms(all_matches):
    groups = []
    used   = set()
    for i, match_a in enumerate(all_matches):
        if i in used:
            continue
        group = {'matches': [match_a], 'sources': [match_a['source']]}
        used.add(i)
        for j, match_b in enumerate(all_matches):
            if j in used or i == j:
                continue
            if match_a['source'] == match_b['source']:
                continue
            home_a = match_a.get('home_team', '')
            away_a = match_a.get('away_team', '')
            home_b = match_b.get('home_team', '')
            away_b = match_b.get('away_team', '')
            date_a = match_a.get('kickoff', '')[:10]
            date_b = match_b.get('kickoff', '')[:10]
            if date_a != date_b:
                continue
            if not tournament_similar(match_a.get('tournament', ''), match_b.get('tournament', '')):
                continue
            if not tournament_similar(home_a + away_a, home_b + away_b):
                continue
            if not similar(home_a, home_b) or not similar(away_a, away_b):
                continue
            if similar(home_a, away_b) and similar(away_a, home_b):
                continue
            group['matches'].append(match_b)
            group['sources'].append(match_b['source'])
            used.add(j)
        if len(group['matches']) > 1:
            groups.append(group)
    return groups


def validate_odds(odds_dict, market_type):
    if not odds_dict:
        return False
    if market_type == '1x2':
        try:
            h = float(odds_dict.get('home', 0) or 0)
            d = float(odds_dict.get('draw', 0) or 0)
            a = float(odds_dict.get('away', 0) or 0)
        except (TypeError, ValueError):
            return False
        if not all(o > 1.01 for o in [h, d, a]):
            return False
        arb = 1/h + 1/d + 1/a
        if arb < 0.85 or arb > 1.5:
            return False
    elif market_type == 'ou':
        try:
            ov = float(odds_dict.get('over', 0) or 0)
            un = float(odds_dict.get('under', 0) or 0)
        except (TypeError, ValueError):
            return False
        if not all(o > 1.01 for o in [ov, un]):
            return False
        arb = 1/ov + 1/un
        if arb < 0.85 or arb > 1.5:
            return False
    elif market_type == 'gg':
        try:
            y = float(odds_dict.get('yes', 0) or 0)
            n = float(odds_dict.get('no', 0) or 0)
        except (TypeError, ValueError):
            return False
        if not all(o > 1.01 for o in [y, n]):
            return False
        arb = 1/y + 1/n
        if arb < 0.85 or arb > 1.5:
            return False
    return True



def get_best_odds(outcome_key, market_type, *platform_odds_pairs):
    candidates = []
    for odds_dict, name in platform_odds_pairs:
        if not odds_dict:
            continue
        if not validate_odds(odds_dict, market_type):
            continue
        try:
            val = float(odds_dict.get(outcome_key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if val > 1.01:
            candidates.append((val, name))
    return max(candidates, key=lambda x: x[0]) if candidates else (0, 'N/A')



# ── STAKE CALCULATION STRATEGIES ───────────────────────────────────────────────

def balanced_stakes(odds_list, total_stake):
    """Standard arb stakes — equal profit on all outcomes."""
    arb_sum = sum(1 / o for o in odds_list)
    return [round((1/o) / arb_sum * total_stake, 2) for o in odds_list]


def flat_stakes(odds_list, total_stake):
    """Equal GHS on every outcome — produces unequal profits."""
    per_leg = round(total_stake / len(odds_list), 2)
    return [per_leg] * len(odds_list)


def quasi_stakes(odds_list, total_stake):
    """
    Quasi-Arb stake strategy:
    Set stake on the LOWEST ODDS outcome so it breaks exactly even
    (returns total_stake if it wins). Distribute the remainder across
    the other outcomes proportionally. All other outcomes then give profit.

    The 'lowest odds' outcome is the most likely one — you hedge it to
    break even. Every other outcome pays more than your total stake.
    """
    worst_idx  = odds_list.index(min(odds_list))
    worst_odds = odds_list[worst_idx]

    # Stake on worst outcome = total_stake / worst_odds
    # so:  stake_worst * worst_odds = total_stake  → break even
    stake_worst = round(total_stake / worst_odds, 2)

    # Remaining budget distributed proportionally among other outcomes
    remaining  = round(total_stake - stake_worst, 2)
    other_odds = [o for i, o in enumerate(odds_list) if i != worst_idx]

    if not other_odds or remaining <= 0:
        return None  # not feasible

    other_arb_sum = sum(1/o for o in other_odds)
    other_stakes  = [round((1/o) / other_arb_sum * remaining, 2) for o in other_odds]

    # Re-assemble full stakes list in original order
    stakes = []
    other_iter = iter(other_stakes)
    for i in range(len(odds_list)):
        if i == worst_idx:
            stakes.append(stake_worst)
        else:
            stakes.append(next(other_iter))
    return stakes


def compute_profits(odds_list, stakes):
    """Net profit per outcome = (odds * stake) - total_staked."""
    total_staked = sum(stakes)
    return [round(o * s - total_staked, 2) for o, s in zip(odds_list, stakes)]


def arb_sum_and_pct(odds_list):
    arb_sum = sum(1/o for o in odds_list)
    if arb_sum < 1:
        profit_pct = ((1 - arb_sum) / arb_sum) * 100
    else:
        profit_pct = 0
    return round(arb_sum, 4), round(profit_pct, 2)


# ── PER-MARKET SCANNERS ─────────────────────────────────────────────────────────

def _build_outcome_list(best_odds_tuples, outcome_labels):
    """
    best_odds_tuples: list of (odds_value, platform_name)
    outcome_labels:   list of label strings
    Returns list of dicts with outcome/odds/platform.
    """
    return [
        {'outcome': label, 'odds': val, 'platform': plat}
        for (val, plat), label in zip(best_odds_tuples, outcome_labels)
    ]


def scan_market(pair, total_stake, market_type, outcome_keys,
                outcome_labels, line_str=None):
    """
    Generic market scanner.
    Returns dict with balanced/unbalanced/quasi/near_arb results,
    or None if nothing found.
    """
    # pair values are already the correct flat dicts for this market/line
    sb  = pair['sportybet']
    bw  = pair['betway']
    fc  = pair['footballcom']
    ox  = pair['onexbet']
    ttb = pair['twentytwobet']
    soc = pair['soccabet']
    bp  = pair['betpawa']
    ms  = pair['msport']
    sup = pair['supabet']
    bb  = pair['bangbet']

    bests = []
    for key in outcome_keys:
        best = get_best_odds(key, market_type,
            (sb,  'Sportybet'),
            (bw,  'Betway'),
            (fc,  'Football.com'),
            (ox,  '1xBet'),
            (ttb, '22Bet'),
            (soc, 'Soccabet'),
            (bp,  'BetPawa'),
            (ms,  'MSport'),
            (sup, 'Supabet'),
            (bb,  'Bangbet'),
        )
        bests.append(best)

    if not all(b[0] > 1.01 for b in bests):
        return None

    odds_list = [b[0] for b in bests]
    arb_sum, profit_pct = arb_sum_and_pct(odds_list)
    outcomes = _build_outcome_list(bests, outcome_labels)

    if arb_sum >= 1:
        return None  # no guaranteed profit possible

    # ── BALANCED ──────────────────────────────────────────────────────────────
    bal_stakes  = balanced_stakes(odds_list, total_stake)
    bal_profits = compute_profits(odds_list, bal_stakes)
    balanced    = None
    if MIN_BALANCED_PROFIT_PCT <= profit_pct <= MAX_ARB_PROFIT_PCT:
        balanced = _format_result(
            market_type, line_str, arb_sum, profit_pct, total_stake,
            outcomes, bal_stakes, bal_profits, 'balanced'
        )

    # ── UNBALANCED ────────────────────────────────────────────────────────────
    # Flat stakes → natural unequal profits. All legs must profit above threshold.
    fl_stakes  = flat_stakes(odds_list, total_stake)
    fl_profits = compute_profits(odds_list, fl_stakes)
    unbalanced = None
    if all(p > MIN_UNBALANCED_PROFIT_GHS for p in fl_profits):
        min_profit  = min(fl_profits)
        max_profit  = max(fl_profits)
        max_outcome = outcomes[fl_profits.index(max_profit)]['outcome']
        unbalanced  = _format_result(
            market_type, line_str, arb_sum, profit_pct, total_stake,
            outcomes, fl_stakes, fl_profits, 'unbalanced',
            extra={
                'min_profit_ghs': min_profit,
                'max_profit_ghs': max_profit,
                'max_outcome':    max_outcome,
            }
        )

    # ── QUASI-ARB ─────────────────────────────────────────────────────────────
    q_stakes = quasi_stakes(odds_list, total_stake)
    quasi    = None
    if q_stakes:
        q_profits    = compute_profits(odds_list, q_stakes)
        worst_idx    = odds_list.index(min(odds_list))
        worst_return = odds_list[worst_idx] * q_stakes[worst_idx]
        if abs(worst_return - total_stake) <= 0.50:
            other_profits = [p for i, p in enumerate(q_profits) if i != worst_idx]
            if other_profits and max(other_profits) >= MIN_QUASI_PROFIT_GHS:
                best_profit   = max(other_profits)
                best_outcome  = outcomes[q_profits.index(max(q_profits))]['outcome']
                worst_outcome = outcomes[worst_idx]['outcome']
                quasi = _format_result(
                    market_type, line_str, arb_sum, profit_pct, total_stake,
                    outcomes, q_stakes, q_profits, 'quasi',
                    extra={
                        'break_even_outcome': worst_outcome,
                        'best_outcome':       best_outcome,
                        'best_profit_ghs':    best_profit,
                    }
                )

    return {
        'balanced':   balanced,
        'unbalanced': unbalanced,
        'quasi':      quasi,
    }


def _format_result(market_type, line_str, arb_sum, profit_pct,
                   total_stake, outcomes, stakes, profits, category, extra=None):
    market_label = {
        '1x2': '1X2',
        'ou':  f'Over/Under {line_str}',
        'gg':  'GG/NG',
    }.get(market_type, market_type.upper())

    bets = []
    for o, s, p in zip(outcomes, stakes, profits):
        bets.append({
            'outcome':        o['outcome'],
            'platform':       o['platform'],
            'odds':           o['odds'],
            'stake':          s,
            'profit_if_wins': p,
        })

    result = {
        'category':   category,
        'market':     market_label,
        'arb_sum':    arb_sum,
        'profit_pct': profit_pct,
        'profit_ghs': round(total_stake * profit_pct / 100, 2),
        'bets':       bets,
    }
    if extra:
        result.update(extra)
    return result


# ── FULL SCAN ──────────────────────────────────────────────────────────────────

def scan_group(pair, total_stake):
    """Run all 3 markets × 3 categories for one matched event group."""
    results = {'balanced': [], 'unbalanced': [], 'quasi': []}

    # Helper: build a flat odds pair for a given market from the full match pair
    def odds_pair(market_key):
        return {
            'sportybet':    pair['sportybet'].get(market_key, {}),
            'betway':       pair['betway'].get(market_key, {}),
            'footballcom':  pair['footballcom'].get(market_key, {}),
            'onexbet':      pair['onexbet'].get(market_key, {}),
            'twentytwobet': pair['twentytwobet'].get(market_key, {}),
            'soccabet':     pair['soccabet'].get(market_key, {}),
            'betpawa':      pair['betpawa'].get(market_key, {}),
            'msport':       pair['msport'].get(market_key, {}),
            'supabet':      pair['supabet'].get(market_key, {}),
            'bangbet':      pair['bangbet'].get(market_key, {}),
        }

    # 1X2
    r = scan_market(odds_pair('odds_1x2'), total_stake, '1x2',
                    ['home', 'draw', 'away'],
                    ['Home Win', 'Draw', 'Away Win'])
    if r:
        for cat in results:
            if r.get(cat):
                results[cat].append(r[cat])

    # Over/Under — ALL platforms, ALL lines
    sb_ou  = pair['sportybet'].get('odds_ou', {})
    fc_ou  = pair['footballcom'].get('odds_ou', {})
    bw_ou  = pair['betway'].get('odds_ou', {})
    ox_ou  = pair['onexbet'].get('odds_ou', {})
    ttb_ou = pair['twentytwobet'].get('odds_ou', {})
    soc_ou = pair['soccabet'].get('odds_ou', {})
    bp_ou  = pair['betpawa'].get('odds_ou', {})
    ms_ou  = pair['msport'].get('odds_ou', {})
    sup_ou = pair['supabet'].get('odds_ou', {})
    bb_ou  = pair['bangbet'].get('odds_ou', {})

    all_ou_lines = set()
    for d in [sb_ou, bw_ou, fc_ou, ox_ou, ttb_ou,
               soc_ou, bp_ou, ms_ou, sup_ou, bb_ou]:
        all_ou_lines.update(d.keys())

    for line in all_ou_lines:
        ou_pair = {
            'sportybet':    sb_ou.get(line, {}),
            'betway':       bw_ou.get(line, {}),
            'footballcom':  fc_ou.get(line, {}),
            'onexbet':      ox_ou.get(line, {}),
            'twentytwobet': ttb_ou.get(line, {}),
            'soccabet':     soc_ou.get(line, {}),
            'betpawa':      bp_ou.get(line, {}),
            'msport':       ms_ou.get(line, {}),
            'supabet':      sup_ou.get(line, {}),
            'bangbet':      bb_ou.get(line, {}),
        }
        r = scan_market(ou_pair, total_stake, 'ou',
                        ['over', 'under'],
                        [f'Over {line}', f'Under {line}'],
                        line_str=line)
        if r:
            for cat in results:
                if r.get(cat):
                    results[cat].append(r[cat])

    # GG/NG
    r = scan_market(odds_pair('odds_gg'), total_stake, 'gg',
                    ['yes', 'no'],
                    ['GG Yes', 'GG No'])
    if r:
        for cat in results:
            if r.get(cat):
                results[cat].append(r[cat])

    return results


def run_experimental(total_stake=None,
                     sportybet_matches=None,
                     betway_matches=None,
                     footballcom_matches=None,
                     onexbet_matches=None,
                     twentytwobet_matches=None,
                     soccabet_matches=None,
                     betpawa_matches=None,
                     msport_matches=None,
                     supabet_matches=None,
                     bangbet_matches=None):
    """
    Main entry point — supports up to 10 platforms.
    Accepts pre-loaded match lists OR loads from JSON files automatically.
    Returns: (balanced_opps, unbalanced_opps, quasi_opps, num_groups)
    """

    if total_stake is None:
        _env = dotenv_values(_ENV_PATH)
        total_stake = int(_env.get('STARTING_CAPITAL', 500))

    def load_json(path):
        try:
            with open(path) as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    # Existing 5 platforms
    if sportybet_matches    is None: sportybet_matches    = load_json('data/sportybet_odds.json')
    if betway_matches       is None: betway_matches       = load_json('data/betway_odds.json')
    if footballcom_matches  is None: footballcom_matches  = load_json('data/footballcom_odds.json')
    if onexbet_matches      is None: onexbet_matches      = load_json('data/onexbet_odds.json')
    if twentytwobet_matches is None: twentytwobet_matches = load_json('data/twentytwobet_odds.json')

    # New 5 platforms
    if soccabet_matches is None: soccabet_matches = load_json('data/soccabet_odds.json')
    if betpawa_matches  is None: betpawa_matches  = load_json('data/betpawa_odds.json')
    if msport_matches   is None: msport_matches   = load_json('data/msport_odds.json')
    if supabet_matches  is None: supabet_matches  = load_json('data/supabet_odds.json')
    if bangbet_matches  is None: bangbet_matches  = load_json('data/bangbet_odds.json')

    # Filter virtual/esports from ALL platforms
    sportybet_matches    = [m for m in sportybet_matches    if not is_virtual_match(m)]
    betway_matches       = [m for m in betway_matches       if not is_virtual_match(m)]
    footballcom_matches  = [m for m in footballcom_matches  if not is_virtual_match(m)]
    onexbet_matches      = [m for m in onexbet_matches      if not is_virtual_match(m)]
    twentytwobet_matches = [m for m in twentytwobet_matches if not is_virtual_match(m)]
    soccabet_matches     = [m for m in soccabet_matches     if not is_virtual_match(m)]
    betpawa_matches      = [m for m in betpawa_matches      if not is_virtual_match(m)]
    msport_matches       = [m for m in msport_matches       if not is_virtual_match(m)]
    supabet_matches      = [m for m in supabet_matches      if not is_virtual_match(m)]
    bangbet_matches      = [m for m in bangbet_matches      if not is_virtual_match(m)]

    all_matches = (
        sportybet_matches + betway_matches + footballcom_matches +
        onexbet_matches + twentytwobet_matches + soccabet_matches +
        betpawa_matches + msport_matches + supabet_matches + bangbet_matches
    )

    groups = match_all_platforms(all_matches)

    balanced_opps   = []
    unbalanced_opps = []
    quasi_opps      = []

    empty = {'odds_1x2': {}, 'odds_ou': {}, 'odds_gg': {}}

    for group in groups:
        matches    = group['matches']
        first      = matches[0]
        match_name = f"{first['home_team']} vs {first['away_team']}"
        kickoff    = first['kickoff']
        tournament = first['tournament']
        for m in matches:
            t = m.get('tournament', '')
            if '.' in t and len(t) > len(tournament):
                tournament = t
                break

        pair = {
            'sportybet':    next((m for m in matches if m['source'] == 'sportybet_gh'),    empty),
            'betway':       next((m for m in matches if m['source'] == 'betway_gh'),       empty),
            'footballcom':  next((m for m in matches if m['source'] == 'footballcom_gh'),  empty),
            'onexbet':      next((m for m in matches if m['source'] == '1xbet_gh'),        empty),
            'twentytwobet': next((m for m in matches if m['source'] == 'twentytwobet_gh'), empty),
            'soccabet':     next((m for m in matches if m['source'] == 'soccabet_gh'),     empty),
            'betpawa':      next((m for m in matches if m['source'] == 'betpawa_gh'),      empty),
            'msport':       next((m for m in matches if m['source'] == 'msport_gh'),       empty),
            'supabet':      next((m for m in matches if m['source'] == 'supabet_gh'),      empty),
            'bangbet':      next((m for m in matches if m['source'] == 'bangbet_gh'),      empty),
        }

        results = scan_group(pair, total_stake)

        meta = {'match': match_name, 'kickoff': kickoff, 'tournament': tournament}
        for arb in results['balanced']:
            balanced_opps.append({**meta, **arb})
        for arb in results['unbalanced']:
            unbalanced_opps.append({**meta, **arb})
        for arb in results['quasi']:
            quasi_opps.append({**meta, **arb})

    return balanced_opps, unbalanced_opps, quasi_opps, len(groups)


# ── DISPLAY ────────────────────────────────────────────────────────────────────

def _print_bet_rows(bets):
    for bet in bets:
        profit = bet['profit_if_wins']
        # Clamp floating-point near-zero (-0.0000x) to clean 0.00
        if abs(profit) < 0.02:
            profit = 0.0
        print(f"\n     🎯 {bet['platform']}")
        print(f"        Bet:   {bet['outcome']}")
        print(f"        Odds:  {bet['odds']}")
        print(f"        Stake: GHS {bet['stake']:.2f}")
        print(f"        Win:   GHS {profit:.2f}")


def display_balanced(opp):
    print(f"\n  {'='*55}")
    print(f"  🏆 {opp['match']}")
    print(f"  📅 {opp['kickoff']} | {opp['tournament']}")
    print(f"  {'='*55}")
    print(f"  📊 Market: {opp['market']}")
    print(f"  💰 Profit: {opp['profit_pct']:.2f}% = GHS {opp['profit_ghs']:.2f}")
    print(f"  💵 Stake:  GHS {sum(b['stake'] for b in opp['bets']):.2f}")
    print(f"\n  📋 BETS TO PLACE:")
    _print_bet_rows(opp['bets'])


def display_unbalanced(opp):
    print(f"\n  {'='*55}")
    print(f"  🏆 {opp['match']}")
    print(f"  📅 {opp['kickoff']} | {opp['tournament']}")
    print(f"  {'='*55}")
    print(f"  📊 Market:      {opp['market']}")
    print(f"  📉 Min Profit:  GHS {opp['min_profit_ghs']:.2f}  (guaranteed floor)")
    print(f"  📈 Max Profit:  GHS {opp['max_profit_ghs']:.2f}  ← if {opp['max_outcome']} wins")
    print(f"  💵 Stake:       GHS {sum(b['stake'] for b in opp['bets']):.2f}")
    print(f"\n  📋 BETS TO PLACE:")
    _print_bet_rows(opp['bets'])


def display_quasi(opp):
    print(f"\n  {'='*55}")
    print(f"  🏆 {opp['match']}")
    print(f"  📅 {opp['kickoff']} | {opp['tournament']}")
    print(f"  {'='*55}")
    print(f"  📊 Market:         {opp['market']}")
    print(f"  🛡️  Break-Even On:  {opp['break_even_outcome']} (get GHS {sum(b['stake'] for b in opp['bets']):.2f} back)")
    print(f"  💰 Best Profit:    GHS {opp['best_profit_ghs']:.2f}  ← if {opp['best_outcome']} wins")
    print(f"  💵 Total Stake:    GHS {sum(b['stake'] for b in opp['bets']):.2f}")
    print(f"\n  📋 BETS TO PLACE:")
    _print_bet_rows(opp['bets'])


def display_all(balanced_opps, unbalanced_opps, quasi_opps, num_groups, total_stake):
    sep = '=' * 60

    print(f"\n{sep}")
    print("⚖️  CATEGORY 1: BALANCED ARBITRAGE")
    print(f"   Guaranteed equal profit on ALL outcomes")
    print(sep)
    if balanced_opps:
        print(f"  ✅ {len(balanced_opps)} opportunity(s) found\n")
        for opp in balanced_opps:
            display_balanced(opp)
    else:
        print("  💡 No balanced arb opportunities right now")

    print(f"\n{sep}")
    print("📊  CATEGORY 2: UNBALANCED ARBITRAGE")
    print(f"   All outcomes profitable — amounts differ")
    print(sep)
    if unbalanced_opps:
        print(f"  ✅ {len(unbalanced_opps)} opportunity(s) found\n")
        for opp in unbalanced_opps:
            display_unbalanced(opp)
    else:
        print("  💡 No unbalanced arb opportunities right now")

    print(f"\n{sep}")
    print("🛡️   CATEGORY 3: QUASI-ARB (No-Loss)")
    print(f"   Worst case: break even | Best case: profit")
    print(sep)
    if quasi_opps:
        print(f"  ✅ {len(quasi_opps)} opportunity(s) found\n")
        for opp in quasi_opps:
            display_quasi(opp)
    else:
        print("  💡 No quasi-arb opportunities right now")

    print(f"\n{sep}")
    print(f"⚽ Events scanned  : {num_groups}")
    print(f"🌐 Platforms       : 6 active (Sportybet, Betway, Football.com, 1xBet, 22Bet, MSport)")
    print(f"⚖️  Balanced        : {len(balanced_opps)}")
    print(f"📊 Unbalanced      : {len(unbalanced_opps)}")
    print(f"🛡️  Quasi-Arb       : {len(quasi_opps)}")
    print(sep)

