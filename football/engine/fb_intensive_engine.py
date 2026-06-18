"""
INTENSIVE ENGINE — Optimized exhaustive sports arbitrage scanner.

For each matched game group, tests permutations across 9 platforms for 14 markets.
Features:
  1. Time-window blocking (O(N) pre-filter)
  2. Set-based token guards (zero-cost rejects)
  3. Transitive Union-Find pruning
  4. Vectorized 3D and 2D NumPy permutation scanners
"""

import os
import re
import json
import numpy as np
from difflib import SequenceMatcher
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dotenv import dotenv_values

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
_EXCLUSIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'data', 'excluded_markets.json')

MIN_ARB_PROFIT_PCT        = 0.01
MAX_ARB_PROFIT_PCT        = 15.0
MIN_UNBALANCED_PROFIT_GHS = 0.10
MIN_QUASI_PROFIT_GHS      = 0.50

def _load_exclusions():
    try:
        with open(_EXCLUSIONS_PATH) as f:
            cfg = json.load(f)
        result = set()
        for entry in cfg.get('excluded', []):
            plat   = entry.get('platform', '').lower()
            market = entry.get('market',   '').lower()
            line   = entry.get('line', None)
            if plat and market:
                result.add((plat, market, str(line) if line else None))
        return result
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

_EXCLUDED = _load_exclusions()

# ── PLATFORM REGISTRY (9 ACTIVE PLATFORMS) ─────────────────────────────────────
PLATFORMS = [
    'sportybet', 'betway', 'footballcom',
    'onexbet', 'twentytwobet', 'msport',
    'bangbet', 'soccabet', 'supabet',
]

PLATFORM_DISPLAY = {
    'sportybet':    'Sportybet',
    'betway':       'Betway',
    'footballcom':  'Football.com',
    'onexbet':      '1xBet',
    'twentytwobet': '22Bet',
    'msport':       'MSport',
    'bangbet':      'Bangbet',
    'soccabet':     'Soccabet',
    'supabet':      'Supabet',
}

SOURCE_MAP = {
    'sportybet':    'sportybet_gh',
    'betway':       'betway_gh',
    'footballcom':  'footballcom_gh',
    'onexbet':      '1xbet_gh',
    'twentytwobet': 'twentytwobet_gh',
    'msport':       'msport_gh',
    'bangbet':      'bangbet_gh',
    'soccabet':     'soccabet_gh',
    'supabet':      'supabet_gh',
}

# ── FUZZY MATCHING ─────────────────────────────────────────────────────────────
STOPWORDS = {
    'fk', 'fc', 'sc', 'cf', 'ac', 'bk', 'fk', 'sk', 'if', 'bfk', 'spor', 'sport',
    'united', 'city', 'town', 'reserve', 'reserves', 'u19', 'u20', 'u21', 'u23',
    'women', 'youth', 'under', 'club', 'team', 'real', 'atletico',
    'depor', 'deportivo', 'deportes', 'deportiva', 'cd', 'csd', 'sd', 'ud',
    'de', 'la', 'del', 'ii', 'b', 'u-19', 'u-20', 'u-21', 'citizen', 'citizens'
}

ASSOCIATIONS = {'hapoel', 'maccabi', 'beitar', 'ironi'}
GENERIC_WORDS = {'kfar', 'fc', 'sc', 'united', 'city', 'town', 'club', 'team', 'citizen', 'citizens'}

def clean_tokens(name):
    name = str(name).lower().strip()
    name = re.sub(r'[^a-z0-9]', ' ', name)
    words = name.split()
    filtered = []
    for w in words:
        if w in STOPWORDS:
            continue
        if w.startswith('y') and len(w) > 4 and w[1] in 'aeiou':
            w = w[1:]
        filtered.append(w)
    return filtered

def smart_team_match(a, b):
    tokens_a = clean_tokens(a)
    tokens_b = clean_tokens(b)
    
    if not tokens_a or not tokens_b:
        return False
        
    assoc_a = set(tokens_a).intersection(ASSOCIATIONS)
    assoc_b = set(tokens_b).intersection(ASSOCIATIONS)
    if assoc_a and assoc_b and assoc_a != assoc_b:
        return False

    core_a = [w for w in tokens_a if w not in ASSOCIATIONS and w not in GENERIC_WORDS]
    core_b = [w for w in tokens_b if w not in ASSOCIATIONS and w not in GENERIC_WORDS]
    
    if core_a and core_b:
        core_overlap = set(core_a).intersection(set(core_b))
        if not core_overlap:
            return False

    if tokens_a == tokens_b:
        return True
        
    str_a = ' '.join(tokens_a)
    str_b = ' '.join(tokens_b)
    if (len(str_a) >= 4 and str_a in str_b) or (len(str_b) >= 4 and str_b in str_a):
        return True
        
    ratio = SequenceMatcher(None, str_a, str_b).ratio()
    if ratio >= 0.72:
        return True
        
    overlap = set(tokens_a).intersection(set(tokens_b))
    long_overlap = [w for w in overlap if len(w) >= 5]
    if long_overlap and ratio >= 0.50:
        return True
        
    return False

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
    a_n, b_n = a.lower(), b.lower()
    for mod in ['u19', 'u20', 'u21', 'u23', 'women', 'reserves',
                'srl', 'esport', 'virtual', 'cyber']:
        if (mod in a_n) != (mod in b_n):
            return False
    return True

def _same_game(a, b):
    if a['source'] == b['source']:
        return False
    
    t_a = a.get('kickoff', '').split()
    t_b = b.get('kickoff', '').split()
    times_match = (len(t_a) > 1 and len(t_b) > 1 and t_a[1][:5] == t_b[1][:5])
        
    if not tournament_similar(a.get('tournament', ''), b.get('tournament', '')):
        return False
        
    ha, aa = a.get('home_team', ''), a.get('away_team', '')
    hb, ab = b.get('home_team', ''), b.get('away_team', '')
    
    if not tournament_similar(ha + aa, hb + ab):
        return False
        
    if times_match:
        home_ok = smart_team_match(ha, hb)
        away_ok = smart_team_match(aa, ab)
        if home_ok and away_ok:
            return True
            
    return smart_team_match(ha, hb) and smart_team_match(aa, ab)

# ── OPTIMIZED UNION-FIND PAIRING ENGINE ────────────────────────────────────────
def match_all_platforms(all_matches):
    parsed_matches = []
    for m in all_matches:
        ko = m.get('kickoff', '')
        ts = 0.0
        try:
            ts = datetime.strptime(ko, '%Y-%m-%d %H:%M').timestamp()
        except Exception:
            pass
        parsed_matches.append({'match': m, 'ts': ts})
    
    parsed_matches.sort(key=lambda x: x['ts'])
    
    n = len(parsed_matches)
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

    # Sliding time window of 2 hours (7200 seconds)
    TIME_WINDOW = 7200.0

    # Token guards pre-computation
    token_sets = []
    for i in range(n):
        m = parsed_matches[i]['match']
        home_tokens = set(clean_tokens(m.get('home_team', '')))
        away_tokens = set(clean_tokens(m.get('away_team', '')))
        token_sets.append((home_tokens, away_tokens))
        
    core_sets = []
    for i in range(n):
        h_tok, a_tok = token_sets[i]
        h_core = h_tok - ASSOCIATIONS - GENERIC_WORDS
        a_core = a_tok - ASSOCIATIONS - GENERIC_WORDS
        core_sets.append((h_core, a_core))

    for i in range(n):
        m_i = parsed_matches[i]['match']
        ts_i = parsed_matches[i]['ts']
        h_tok_i, a_tok_i = token_sets[i]
        h_core_i, a_core_i = core_sets[i]
        
        for j in range(i + 1, n):
            # 1. Time-window guard (since list is sorted, we break inner loop instantly)
            if parsed_matches[j]['ts'] - ts_i > TIME_WINDOW:
                break
                
            # 2. Transitive union guard
            if find(i) == find(j):
                continue
                
            m_j = parsed_matches[j]['match']
            if m_i['source'] == m_j['source']:
                continue
                
            # 3. Token Guards
            h_tok_j, a_tok_j = token_sets[j]
            h_core_j, a_core_j = core_sets[j]
            
            if h_core_i and h_core_j and not (h_core_i & h_core_j):
                continue
            if a_core_i and a_core_j and not (a_core_i & a_core_j):
                continue
                
            assoc_h_i = h_tok_i & ASSOCIATIONS
            assoc_h_j = h_tok_j & ASSOCIATIONS
            if assoc_h_i and assoc_h_j and assoc_h_i != assoc_h_j:
                continue
            assoc_a_i = a_tok_i & ASSOCIATIONS
            assoc_a_j = a_tok_j & ASSOCIATIONS
            if assoc_a_i and assoc_a_j and assoc_a_i != assoc_a_j:
                continue
                
            # If passes all guards, run fuzzy text check
            if _same_game(m_i, m_j):
                union(i, j)

    components = defaultdict(list)
    for i in range(n):
        components[find(i)].append(i)

    groups = []
    for indices in components.values():
        if len(indices) < 2:
            continue
        sources = [parsed_matches[i]['match']['source'] for i in indices]
        if len(set(sources)) < 2:
            continue
        groups.append({
            'matches': [parsed_matches[i]['match'] for i in indices],
            'sources': sources,
        })
    return groups

# ── HELPERS ────────────────────────────────────────────────────────────────────
def _f(val):
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0

def normalize_line_key(k: str) -> str:
    try:
        val = float(k)
        if val % 1.0 == 0.0:
            return f"{int(val)}.0"
        return str(val)
    except (TypeError, ValueError):
        return str(k)

def _has_odds(pair, market_key):
    return any(pair[plat].get(market_key) for plat in PLATFORMS)

def _stakes(odds_list, total_stake):
    s = sum(1 / o for o in odds_list)
    return [round((1 / o) / s * total_stake, 2) for o in odds_list]

def _flat_stakes(odds_list, total_stake):
    per_leg = round(total_stake / len(odds_list), 2)
    return [per_leg] * len(odds_list)

def _quasi_stakes(odds_list, total_stake):
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

def _unique_odds(pair, market_key, outcome_key):
    mtype = market_key.replace('odds_', '')
    seen  = {}
    items = []
    for plat in PLATFORMS:
        if (plat, mtype, None) in _EXCLUDED:
            continue
        market = pair[plat].get(market_key) or {}
        v = _f(market.get(outcome_key, 0))
        if v <= 1.01:
            continue
        key = round(v, 2)
        if key not in seen:
            seen[key] = plat
            items.append((v, plat))
    return items

def _unique_nested_odds(pair, market_key, line, outcome_key):
    seen = {}
    items = []
    mtype = market_key.replace('odds_', '')
    for plat in PLATFORMS:
        if (plat, mtype, str(line)) in _EXCLUDED or (plat, mtype, None) in _EXCLUDED:
            continue
        nested_data = pair[plat].get(market_key) or {}
        line_data = nested_data.get(line) or {}
        v = _f(line_data.get(outcome_key, 0))
        if v <= 1.01:
            continue
        key = round(v, 2)
        if key not in seen:
            seen[key] = plat
            items.append((v, plat))
    return items

# ── Permutations Output formatter ──────────────────────────────────────────────
def _format(market_label, arb_sum_f, profit_pct_f, total_stake,
            leg_tuples, stakes, profits, category, extra=None):
    result = {
        'category':   category,
        'market':     market_label,
        'arb_sum':    round(float(arb_sum_f), 4),
        'profit_pct': round(float(profit_pct_f), 2),
        'profit_ghs': round(total_stake * float(profit_pct_f) / 100, 2),
        'bets': [
            {
                'outcome':        label,
                'platform':       PLATFORM_DISPLAY[plat],
                'odds':           odds,
                'stake':          s,
                'profit_if_wins': p,
            }
            for (label, plat, odds), s, p in zip(leg_tuples, stakes, profits)
        ],
    }
    if extra:
        result.update(extra)
    return result

def _all_categories(market_label, arb_sum_f, profit_pct_f, total_stake, leg_tuples):
    odds_list = [t[2] for t in leg_tuples]
    results   = []

    # Balanced
    if MIN_ARB_PROFIT_PCT <= profit_pct_f <= MAX_ARB_PROFIT_PCT:
        bal_stk = _stakes(odds_list, total_stake)
        bal_prf = _profits(odds_list, bal_stk)
        results.append(_format(market_label, arb_sum_f, profit_pct_f,
                               total_stake, leg_tuples, bal_stk, bal_prf, 'balanced'))

    # Unbalanced
    fl_stk = _flat_stakes(odds_list, total_stake)
    fl_prf = _profits(odds_list, fl_stk)
    if all(p > MIN_UNBALANCED_PROFIT_GHS for p in fl_prf):
        min_p   = min(fl_prf)
        max_p   = max(fl_prf)
        max_out = leg_tuples[fl_prf.index(max_p)][0]
        r = _format(market_label, arb_sum_f, profit_pct_f,
                    total_stake, leg_tuples, fl_stk, fl_prf, 'unbalanced')
        r.update({'min_profit_ghs': min_p, 'max_profit_ghs': max_p, 'max_outcome': max_out})
        results.append(r)

    # Quasi
    q_stk = _quasi_stakes(odds_list, total_stake)
    if q_stk:
        q_prf      = _profits(odds_list, q_stk)
        worst_idx  = odds_list.index(min(odds_list))
        worst_ret  = odds_list[worst_idx] * q_stk[worst_idx]
        if abs(worst_ret - total_stake) <= 0.50:
            other_prf = [p for i, p in enumerate(q_prf) if i != worst_idx]
            if other_prf and max(other_prf) >= MIN_QUASI_PROFIT_GHS:
                best_p   = max(other_prf)
                best_out = leg_tuples[q_prf.index(max(q_prf))][0]
                worst_out = leg_tuples[worst_idx][0]
                r = _format(market_label, arb_sum_f, profit_pct_f,
                            total_stake, leg_tuples, q_stk, q_prf, 'quasi')
                r.update({'break_even_outcome': worst_out,
                          'best_outcome':       best_out,
                          'best_profit_ghs':    best_p})
                results.append(r)

    return results

# ── NUMPY BROADCAST SCANNER HELPERS ────────────────────────────────────────────
def scan_3way_numpy(pair, market_key, market_label, outcomes_info, total_stake):
    o0_items = _unique_odds(pair, market_key, outcomes_info[0][0])
    o1_items = _unique_odds(pair, market_key, outcomes_info[1][0])
    o2_items = _unique_odds(pair, market_key, outcomes_info[2][0])

    if not o0_items or not o1_items or not o2_items:
        return []

    arr0 = np.array([x[0] for x in o0_items], dtype=np.float64)
    arr1 = np.array([x[0] for x in o1_items], dtype=np.float64)
    arr2 = np.array([x[0] for x in o2_items], dtype=np.float64)

    arb = 1.0/arr0[:, None, None] + 1.0/arr1[None, :, None] + 1.0/arr2[None, None, :]
    hits = np.argwhere(arb < 1.0)

    results = []
    for idx0, idx1, idx2 in hits:
        arb_sum = float(arb[idx0, idx1, idx2])
        profit_pct = ((1.0 - arb_sum) / arb_sum) * 100.0
        results.extend(_all_categories(
            market_label, arb_sum, profit_pct, total_stake,
            [
                (outcomes_info[0][1], o0_items[idx0][1], o0_items[idx0][0]),
                (outcomes_info[1][1], o1_items[idx1][1], o1_items[idx1][0]),
                (outcomes_info[2][1], o2_items[idx2][1], o2_items[idx2][0]),
            ]
        ))
    return results

def scan_2way_numpy(pair, market_key, market_label, outcomes_info, total_stake):
    o0_items = _unique_odds(pair, market_key, outcomes_info[0][0])
    o1_items = _unique_odds(pair, market_key, outcomes_info[1][0])

    if not o0_items or not o1_items:
        return []

    arr0 = np.array([x[0] for x in o0_items], dtype=np.float64)
    arr1 = np.array([x[0] for x in o1_items], dtype=np.float64)

    arb = 1.0/arr0[:, None] + 1.0/arr1[None, :]
    hits = np.argwhere(arb < 1.0)

    results = []
    for idx0, idx1 in hits:
        arb_sum = float(arb[idx0, idx1])
        profit_pct = ((1.0 - arb_sum) / arb_sum) * 100.0
        results.extend(_all_categories(
            market_label, arb_sum, profit_pct, total_stake,
            [
                (outcomes_info[0][1], o0_items[idx0][1], o0_items[idx0][0]),
                (outcomes_info[1][1], o1_items[idx1][1], o1_items[idx1][0]),
            ]
        ))
    return results

def scan_2way_nested_numpy(pair, market_key, market_label_prefix, outcomes_info, total_stake):
    all_lines = set()
    for plat in PLATFORMS:
        all_lines.update((pair[plat].get(market_key) or {}).keys())

    results = []
    for line in all_lines:
        o0_items = _unique_nested_odds(pair, market_key, line, outcomes_info[0][0])
        o1_items = _unique_nested_odds(pair, market_key, line, outcomes_info[1][0])
        if not o0_items or not o1_items:
            continue

        arr0 = np.array([x[0] for x in o0_items], dtype=np.float64)
        arr1 = np.array([x[0] for x in o1_items], dtype=np.float64)

        arb = 1.0/arr0[:, None] + 1.0/arr1[None, :]
        hits = np.argwhere(arb < 1.0)

        for idx0, idx1 in hits:
            arb_sum = float(arb[idx0, idx1])
            profit_pct = ((1.0 - arb_sum) / arb_sum) * 100.0
            results.extend(_all_categories(
                f"{market_label_prefix} {line}", arb_sum, profit_pct, total_stake,
                [
                    (f"{outcomes_info[0][1]} {line}", o0_items[idx0][1], o0_items[idx0][0]),
                    (f"{outcomes_info[1][1]} {line}", o1_items[idx1][1], o1_items[idx1][0]),
                ]
              ))
    return results

def scan_double_chance_numpy(pair, dc_key, main_key, market_label, total_stake):
    results = []
    # Combo 1: 1X vs 2
    r1 = _scan_2way_hybrid(pair, dc_key, '1x', main_key, 'away', f"{market_label} 1X vs 2", '1X (Home/Draw)', 'Away Win', total_stake)
    if r1: results.extend(r1)
    # Combo 2: X2 vs 1
    r2 = _scan_2way_hybrid(pair, dc_key, 'x2', main_key, 'home', f"{market_label} X2 vs 1", 'X2 (Draw/Away)', 'Home Win', total_stake)
    if r2: results.extend(r2)
    # Combo 3: 12 vs X
    r3 = _scan_2way_hybrid(pair, dc_key, '12', main_key, 'draw', f"{market_label} 12 vs X", '12 (Home/Away)', 'Draw', total_stake)
    if r3: results.extend(r3)
    return results

def _scan_2way_hybrid(pair, key1, outcome1, key2, outcome2, market_label, label1, label2, total_stake):
    o0_items = _unique_odds(pair, key1, outcome1)
    o1_items = _unique_odds(pair, key2, outcome2)

    if not o0_items or not o1_items:
        return []

    arr0 = np.array([x[0] for x in o0_items], dtype=np.float64)
    arr1 = np.array([x[0] for x in o1_items], dtype=np.float64)

    arb = 1.0/arr0[:, None] + 1.0/arr1[None, :]
    hits = np.argwhere(arb < 1.0)

    results = []
    for idx0, idx1 in hits:
        arb_sum = float(arb[idx0, idx1])
        profit_pct = ((1.0 - arb_sum) / arb_sum) * 100.0
        results.extend(_all_categories(
            market_label, arb_sum, profit_pct, total_stake,
            [
                (label1, o0_items[idx0][1], o0_items[idx0][0]),
                (label2, o1_items[idx1][1], o1_items[idx1][0]),
            ]
        ))
    return results

# ── SCANS ONE GROUP ────────────────────────────────────────────────────────────
def _scan_one_group(group, total_stake, empty):
    matches = group['matches']
    first   = matches[0]

    match_name = f"{first['home_team']} vs {first['away_team']}"
    kickoff    = first['kickoff']
    tournament = first['tournament']
    for m in matches:
        t = m.get('tournament', '')
        if '.' in t and len(t) > len(tournament):
            tournament = t
            break

    pair = {
        plat: next((m for m in matches if m['source'] == SOURCE_MAP[plat]), empty)
        for plat in PLATFORMS
    }

    meta = {'match': match_name, 'kickoff': kickoff, 'tournament': tournament}

    opps = []

    # 1. 1X2 Match Result
    if _has_odds(pair, 'odds_1x2'):
        opps.extend(scan_3way_numpy(pair, 'odds_1x2', '1X2', [('home', 'Home Win'), ('draw', 'Draw'), ('away', 'Away Win')], total_stake))
    # 2. 1X2 One Up
    if _has_odds(pair, 'odds_1x2_one_up'):
        opps.extend(scan_3way_numpy(pair, 'odds_1x2_one_up', '1X2 One Up', [('home', 'Home Win'), ('draw', 'Draw'), ('away', 'Away Win')], total_stake))
    # 3. 1X2 Two Up
    if _has_odds(pair, 'odds_1x2_two_up'):
        opps.extend(scan_3way_numpy(pair, 'odds_1x2_two_up', '1X2 Two Up', [('home', 'Home Win'), ('draw', 'Draw'), ('away', 'Away Win')], total_stake))
    # 4. 1st Half 1X2
    if _has_odds(pair, 'odds_fh_1x2'):
        opps.extend(scan_3way_numpy(pair, 'odds_fh_1x2', '1st Half 1X2', [('home', 'Home Win'), ('draw', 'Draw'), ('away', 'Away Win')], total_stake))
    # 5. 2nd Half 1X2
    if _has_odds(pair, 'odds_sh_1x2'):
        opps.extend(scan_3way_numpy(pair, 'odds_sh_1x2', '2nd Half 1X2', [('home', 'Home Win'), ('draw', 'Draw'), ('away', 'Away Win')], total_stake))
    # 6. Corners 1X2
    if _has_odds(pair, 'odds_corners_1x2'):
        opps.extend(scan_3way_numpy(pair, 'odds_corners_1x2', 'Corners 1X2', [('home', 'Home Win'), ('draw', 'Draw'), ('away', 'Away Win')], total_stake))
    # 7. Bookings 1X2
    if _has_odds(pair, 'odds_bookings_1x2'):
        opps.extend(scan_3way_numpy(pair, 'odds_bookings_1x2', 'Bookings 1X2', [('home', 'Home Win'), ('draw', 'Draw'), ('away', 'Away Win')], total_stake))

    # 8. Over/Under
    if _has_odds(pair, 'odds_ou'):
        opps.extend(scan_2way_nested_numpy(pair, 'odds_ou', 'Over/Under', [('over', 'Over'), ('under', 'Under')], total_stake))
    # 8a. Asian Over/Under
    if _has_odds(pair, 'odds_asian_ou'):
        opps.extend(scan_2way_nested_numpy(pair, 'odds_asian_ou', 'Asian Over/Under', [('over', 'Over'), ('under', 'Under')], total_stake))
    # 9. 1st Half Over/Under
    if _has_odds(pair, 'odds_fh_ou'):
        opps.extend(scan_2way_nested_numpy(pair, 'odds_fh_ou', '1st Half Over/Under', [('over', 'Over'), ('under', 'Under')], total_stake))
    # 10. 2nd Half Over/Under
    if _has_odds(pair, 'odds_sh_ou'):
        opps.extend(scan_2way_nested_numpy(pair, 'odds_sh_ou', '2nd Half Over/Under', [('over', 'Over'), ('under', 'Under')], total_stake))
    # 11. Bookings Over/Under
    if _has_odds(pair, 'odds_bookings_ou'):
        opps.extend(scan_2way_nested_numpy(pair, 'odds_bookings_ou', 'Bookings Over/Under', [('over', 'Over'), ('under', 'Under')], total_stake))

    # 12. GG/NG
    if _has_odds(pair, 'odds_gg'):
        opps.extend(scan_2way_numpy(pair, 'odds_gg', 'GG/NG', [('yes', 'GG Yes'), ('no', 'GG No')], total_stake))
    # 13. GG/NG 2+
    if _has_odds(pair, 'odds_gg_2plus'):
        opps.extend(scan_2way_numpy(pair, 'odds_gg_2plus', 'GG/NG 2+', [('yes', 'GG Yes'), ('no', 'GG No')], total_stake))

    # 14. Double Chance markets
    if _has_odds(pair, 'odds_dc') and _has_odds(pair, 'odds_1x2'):
        opps.extend(scan_double_chance_numpy(pair, 'odds_dc', 'odds_1x2', 'Double Chance', total_stake))
    if _has_odds(pair, 'odds_fh_dc') and _has_odds(pair, 'odds_fh_1x2'):
        opps.extend(scan_double_chance_numpy(pair, 'odds_fh_dc', 'odds_fh_1x2', '1st Half Double Chance', total_stake))
    if _has_odds(pair, 'odds_sh_dc') and _has_odds(pair, 'odds_sh_1x2'):
        opps.extend(scan_double_chance_numpy(pair, 'odds_sh_dc', 'odds_sh_1x2', '2nd Half Double Chance', total_stake))

    return [{**meta, **arb} for arb in opps]

# ── RUN INTENSIVE ENGINE ───────────────────────────────────────────────────────
def run_intensive(total_stake=None,
                  sportybet_matches=None,
                  betway_matches=None,
                  footballcom_matches=None,
                  onexbet_matches=None,
                  twentytwobet_matches=None,
                  msport_matches=None,
                  bangbet_matches=None,
                  soccabet_matches=None,
                  supabet_matches=None):
    if total_stake is None:
        _env = dotenv_values(_ENV_PATH)
        total_stake = int(_env.get('STARTING_CAPITAL', 500))

    def _load(path):
        try:
            with open(path) as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    if sportybet_matches    is None: sportybet_matches    = _load('data/sportybet_odds.json')
    if betway_matches       is None: betway_matches       = _load('data/betway_odds.json')
    if footballcom_matches  is None: footballcom_matches  = _load('data/footballcom_odds.json')
    if onexbet_matches      is None: onexbet_matches      = _load('data/onexbet_odds.json')
    if twentytwobet_matches is None: twentytwobet_matches = _load('data/twentytwobet_odds.json')
    if msport_matches       is None: msport_matches       = _load('data/msport_odds.json')
    if bangbet_matches      is None: bangbet_matches      = _load('data/bangbet_odds.json')
    if soccabet_matches     is None: soccabet_matches     = _load('data/soccabet_odds.json')
    if supabet_matches      is None: supabet_matches      = _load('data/supabet_odds.json')

    raw = {
        'sportybet':    sportybet_matches,
        'betway':       betway_matches,
        'footballcom':  footballcom_matches,
        'onexbet':      onexbet_matches,
        'twentytwobet': twentytwobet_matches,
        'msport':       msport_matches,
        'bangbet':      bangbet_matches,
        'soccabet':     soccabet_matches,
        'supabet':      supabet_matches,
    }

    # Normalize nested market line keys across all platforms/matches
    nested_keys = ['odds_ou', 'odds_asian_ou', 'odds_fh_ou', 'odds_sh_ou', 'odds_bookings_ou']
    for plat_matches in raw.values():
        if not plat_matches:
            continue
        for m in plat_matches:
            for n_key in nested_keys:
                if n_key in m and isinstance(m[n_key], dict):
                    normalized_dict = {}
                    for k, v in m[n_key].items():
                        norm_k = normalize_line_key(k)
                        normalized_dict[norm_k] = v
                    m[n_key] = normalized_dict

    filtered_lists = {k: [m for m in v if not is_virtual_match(m)]
                      for k, v in raw.items()}

    all_matches = [m for v in filtered_lists.values() for m in v]
    groups      = match_all_platforms(all_matches)

    if not groups:
        return [], 0

    empty = {
        'odds_1x2': {}, 'odds_1x2_one_up': {}, 'odds_1x2_two_up': {},
        'odds_fh_1x2': {}, 'odds_sh_1x2': {}, 'odds_corners_1x2': {}, 'odds_bookings_1x2': {},
        'odds_ou': {}, 'odds_asian_ou': {}, 'odds_fh_ou': {}, 'odds_sh_ou': {}, 'odds_bookings_ou': {},
        'odds_gg': {}, 'odds_gg_2plus': {},
        'odds_dc': {}, 'odds_fh_dc': {}, 'odds_sh_dc': {}
    }
    max_workers = min(len(groups), (os.cpu_count() or 4) * 2)
    opportunities = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_scan_one_group, g, total_stake, empty)
            for g in groups
        ]
        for future in as_completed(futures):
            opportunities.extend(future.result())

    return opportunities, len(groups)

# ── DISPLAY ────────────────────────────────────────────────────────────────────
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')

def _write_opportunity_to_file(f, opp):
    try:
        from engine.fb_stake_tracker_helper import get_opp_id, load_staked_history
    except ImportError:
        from fb_stake_tracker_helper import get_opp_id, load_staked_history

    cat = opp.get('category', 'balanced')
    cat_icons = {'balanced': '⚖️ ', 'unbalanced': '📊', 'quasi': '🛡️ '}
    
    opp_id = get_opp_id(opp)
    staked_history = load_staked_history()
    if opp_id in staked_history:
        status_line = f"  ✅ [x] STAKED (ID: {opp_id})"
    else:
        status_line = f"  🚨 [ ] STAKE THIS OPP (ID: {opp_id})"

    f.write(f"\n  {'='*55}\n")
    f.write(f"{status_line}\n")
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

def display_all(opportunities, num_groups, total_stake,
                scrape_time=None, scan_time=None, total_time=None,
                calc_end_str=None, next_run_str=None):
    sep = '=' * 60

    try:
        from engine.fb_stake_tracker_helper import get_opp_id, save_active_opportunities
    except ImportError:
        from fb_stake_tracker_helper import get_opp_id, save_active_opportunities

    opp_cache = {}
    for opp in opportunities:
        opp_id = get_opp_id(opp)
        opp_cache[opp_id] = opp
    save_active_opportunities(opp_cache)

    balanced   = [o for o in opportunities if o.get('category') == 'balanced']
    unbalanced = [o for o in opportunities if o.get('category') == 'unbalanced']
    quasi      = [o for o in opportunities if o.get('category') == 'quasi']

    bal_path = os.path.join(_DATA_DIR, 'intensive_balanced.txt')
    with open(bal_path, 'w', encoding='utf-8') as f:
        f.write(f"⚖️  BALANCED ARBITRAGE — {len(balanced)} opportunities\n")
        f.write(f"Guaranteed equal profit on ALL outcomes\n")
        f.write(f"{sep}\n")
        if balanced:
            for opp in sorted(balanced, key=lambda x: x['profit_pct'], reverse=True):
                _write_opportunity_to_file(f, opp)
        else:
            f.write("  💡 No balanced arb opportunities right now\n")

    unb_path = os.path.join(_DATA_DIR, 'intensive_unbalanced.txt')
    with open(unb_path, 'w', encoding='utf-8') as f:
        f.write(f"📊 UNBALANCED ARBITRAGE — {len(unbalanced)} opportunities\n")
        f.write(f"All outcomes profitable — amounts differ\n")
        f.write(f"{sep}\n")
        if unbalanced:
            for opp in sorted(unbalanced, key=lambda x: x['max_profit_ghs'], reverse=True):
                _write_opportunity_to_file(f, opp)
        else:
            f.write("  💡 No unbalanced arb opportunities right now\n")

    qua_path = os.path.join(_DATA_DIR, 'intensive_quasi.txt')
    with open(qua_path, 'w', encoding='utf-8') as f:
        f.write(f"🛡️  QUASI-ARB (No-Loss) — {len(quasi)} opportunities\n")
        f.write(f"Worst case: break even | Best case: profit\n")
        f.write(f"{sep}\n")
        if quasi:
            for opp in sorted(quasi, key=lambda x: x['best_profit_ghs'], reverse=True):
                _write_opportunity_to_file(f, opp)
        else:
            f.write("  💡 No quasi-arb opportunities right now\n")

    quasi_summary = "  📊 [Quasi ML] No new opportunities found at this time of the scan"
    try:
        from engine.fb_quasi_arb_logger import log_quasi_opportunities
        quasi_summary = log_quasi_opportunities(quasi, total_stake, quiet=True)
    except Exception as _qml_err:
        print(f"  ⚠️  [Quasi ML] Logger error (non-fatal): {_qml_err}")

    print(f"\n{sep}")
    print(f"⚽ Events scanned  : {num_groups}")
    print(f"🌐 Platforms       : 9 (Sportybet, Betway, Football.com, 1xBet, 22Bet, MSport, Bangbet, Soccabet, Supabet)")
    print(f"⚖️  Balanced        : {len(balanced)} → {bal_path}")
    print(f"📊 Unbalanced      : {len(unbalanced)} → {unb_path}")
    print(f"🛡️  Quasi-Arb       : {len(quasi)} → {qua_path}")
    if opportunities:
        best = max(opportunities, key=lambda x: x['profit_pct'])
        best_cat = best.get('category', 'balanced').capitalize()
        print(f"💰 Total profit    : GHS {sum(o['profit_ghs'] for o in opportunities):.2f}")
        print(f"📈 Best            : {best['profit_pct']:.2f}% on {best['match']} ({best_cat})")
    if scrape_time is not None and scan_time is not None and total_time is not None:
        print(f"🌐 Scraping        : {scrape_time:.2f}s  ({scrape_time/60:.3f} min)")
        calc_suffix = f"  (Finished calculations at {calc_end_str})" if calc_end_str else ""
        print(f"🔍 Scanning        : {scan_time:.2f}s  ({scan_time/60:.3f} min){calc_suffix}")
        print(f"🕐 TOTAL           : {total_time:.2f}s  ({total_time/60:.3f} min)")
        if next_run_str:
            print(f"\n[Scheduled] Next run is at {next_run_str}")
    print(sep)
    return quasi_summary
