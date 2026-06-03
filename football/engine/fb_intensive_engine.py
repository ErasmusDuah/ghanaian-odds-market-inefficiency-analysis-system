"""
INTENSIVE ENGINE — Exhaustive pairwise arbitrage scanner (optimised).

For each matched game group, tests EVERY possible platform pairing per market:
  - 1X2:   7^3 = 343 combos  (home × draw × away platform)
  - O/U:   7^2 = 49  combos per line (over × under platform)
  - GG/NG: 7^2 = 49  combos  (yes × no platform)

Three-layer optimisation:
  1. Pre-dedup odds  — 7 platforms collapse to unique odds values before looping
                       (e.g. if 3 platforms all show Home=1.85 we check it once,
                        cutting 1X2 combos from 343 → ~27-64 in practice)
  2. NumPy broadcast — arb sums for ALL combos computed in ONE vectorised op,
                       no Python-level arithmetic loops at all
  3. Parallel groups — every matched-game group is scanned in its own thread
                       (groups are fully independent, zero shared state)

All 7 active platforms:
  Sportybet, Betway, Football.com, 1xBet, 22Bet, MSport, Bangbet

Run via: python run_intensive.py
"""

import os
import re
import json
import numpy as np
from difflib import SequenceMatcher
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import dotenv_values

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
_EXCLUSIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'data', 'excluded_markets.json')

MIN_ARB_PROFIT_PCT        = 0.01
MAX_ARB_PROFIT_PCT        = 15.0
MIN_UNBALANCED_PROFIT_GHS = 0.10
MIN_QUASI_PROFIT_GHS      = 0.50


def _load_exclusions():
    """
    Loads data/excluded_markets.json and returns a set of
    (platform_key, market_type, line_or_none) tuples to skip.
    market_type: '1x2', 'ou', 'gg'
    line: e.g. '1.5' for O/U, None for 1x2 / GG
    """
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

# ── PLATFORM REGISTRY ──────────────────────────────────────────────────────────
PLATFORMS = [
    'sportybet', 'betway', 'footballcom',
    'onexbet', 'twentytwobet', 'msport', 'bangbet',
]

PLATFORM_DISPLAY = {
    'sportybet':    'Sportybet',
    'betway':       'Betway',
    'footballcom':  'Football.com',
    'onexbet':      '1xBet',
    'twentytwobet': '22Bet',
    'msport':       'MSport',
    'bangbet':      'Bangbet',
}

SOURCE_MAP = {
    'sportybet':    'sportybet_gh',
    'betway':       'betway_gh',
    'footballcom':  'footballcom_gh',
    'onexbet':      '1xbet_gh',
    'twentytwobet': 'twentytwobet_gh',
    'msport':       'msport_gh',
    'bangbet':      'bangbet_gh',
}


# ── FUZZY MATCHING ─────────────────────────────────────────────────────────────

STOPWORDS = {
    'fk', 'fc', 'sc', 'cf', 'ac', 'bk', 'fk', 'sk', 'if', 'bfk', 'spor', 'sport',
    'united', 'city', 'town', 'reserve', 'reserves', 'u19', 'u20', 'u21', 'u23',
    'women', 'youth', 'under', 'club', 'team', 'real', 'atletico', 'atletico',
    'depor', 'deportivo', 'de', 'la', 'del', 'ii', 'b', 'u-19', 'u-20', 'u-21'
}

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

ASSOCIATIONS = {'hapoel', 'maccabi', 'beitar', 'ironi'}
GENERIC_WORDS = {'kfar', 'fc', 'sc', 'united', 'city', 'town', 'club', 'team'}

def smart_team_match(a, b):
    tokens_a = clean_tokens(a)
    tokens_b = clean_tokens(b)
    
    if not tokens_a or not tokens_b:
        return False
        
    # --- LAYER 1: STRICT ASSOCIATION GUARD ---
    # If one is Hapoel and the other is Maccabi/Beitar/etc., they can NEVER match
    assoc_a = set(tokens_a).intersection(ASSOCIATIONS)
    assoc_b = set(tokens_b).intersection(ASSOCIATIONS)
    if assoc_a and assoc_b and assoc_a != assoc_b:
        return False

    # --- LAYER 2: CORE IDENTIFIER CHECK ---
    # Strip out both associations and generic words to find the "core" names
    core_a = [w for w in tokens_a if w not in ASSOCIATIONS and w not in GENERIC_WORDS]
    core_b = [w for w in tokens_b if w not in ASSOCIATIONS and w not in GENERIC_WORDS]
    
    # If we have core words, at least one core word MUST overlap
    if core_a and core_b:
        core_overlap = set(core_a).intersection(set(core_b))
        if not core_overlap:
            return False  # e.g., "Saba" vs "Shalem" -> no core overlap -> NO MATCH

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


# ── UNION-FIND TRANSITIVE GROUPING ─────────────────────────────────────────────

def _same_game(a, b):
    if a['source'] == b['source']:
        return False
    if a.get('kickoff', '')[:10] != b.get('kickoff', '')[:10]:
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


def match_all_platforms(all_matches):
    """
    Union-find transitive grouping.
    A↔B and B↔C → all three grouped together even if A↔C fails directly.
    """
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
            if _same_game(all_matches[i], all_matches[j]):
                union(i, j)

    components = defaultdict(list)
    for i in range(n):
        components[find(i)].append(i)

    groups = []
    for indices in components.values():
        if len(indices) < 2:
            continue
        sources = [all_matches[i]['source'] for i in indices]
        if len(set(sources)) < 2:
            continue
        groups.append({
            'matches': [all_matches[i] for i in indices],
            'sources': sources,
        })
    return groups


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _f(val):
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _stakes(odds_list, total_stake):
    """Balanced stakes — equal profit on all outcomes."""
    s = sum(1 / o for o in odds_list)
    return [round((1 / o) / s * total_stake, 2) for o in odds_list]


def _flat_stakes(odds_list, total_stake):
    """Unbalanced stakes — equal GHS on every leg."""
    per_leg = round(total_stake / len(odds_list), 2)
    return [per_leg] * len(odds_list)


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


def _unique_odds(pair, market_key, outcome_key):
    """
    Layer 1 — Pre-dedup.
    Skips platforms excluded via data/excluded_markets.json.
    """
    mtype = market_key.replace('odds_', '')  # 'odds_1x2' -> '1x2', 'odds_gg' -> 'gg'
    seen  = {}
    items = []
    for plat in PLATFORMS:
        # Skip if this platform+market is in the exclusion list
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


def _unique_ou_odds(pair, line, outcome_key):
    """
    Same as _unique_odds but for O/U nested dicts.
    Skips platforms excluded for this specific line via excluded_markets.json.
    """
    seen  = {}
    items = []
    for plat in PLATFORMS:
        # Skip if this platform+ou+line is excluded
        if (plat, 'ou', str(line)) in _EXCLUDED or (plat, 'ou', None) in _EXCLUDED:
            continue
        ou        = pair[plat].get('odds_ou') or {}
        line_data = ou.get(line) or {}
        v = _f(line_data.get(outcome_key, 0))
        if v <= 1.01:
            continue
        key = round(v, 2)
        if key not in seen:
            seen[key] = plat
            items.append((v, plat))
    return items


# ── NUMPY-VECTORISED MARKET SCANNERS ───────────────────────────────────────────

def _format(market_label, arb_sum_f, profit_pct_f, total_stake,
            leg_tuples, stakes, profits, category, extra=None):
    """Build a single result dict for one category."""
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
    """
    For one discovered arb hit, evaluate all 3 staking strategies and return
    a list of every category that passes its threshold.

    leg_tuples: list of (outcome_label, plat_key, odds_float)
    """
    odds_list = [t[2] for t in leg_tuples]
    results   = []

    # ── BALANCED ─────────────────────────────────────────────────────────────
    if MIN_ARB_PROFIT_PCT <= profit_pct_f <= MAX_ARB_PROFIT_PCT:
        bal_stk = _stakes(odds_list, total_stake)
        bal_prf = _profits(odds_list, bal_stk)
        results.append(_format(market_label, arb_sum_f, profit_pct_f,
                               total_stake, leg_tuples, bal_stk, bal_prf, 'balanced'))

    # ── UNBALANCED ───────────────────────────────────────────────────────────
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

    # ── QUASI-ARB ────────────────────────────────────────────────────────────
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


def scan_1x2_numpy(pair, total_stake):
    """
    Layer 1 + 2:
    Pre-dedup odds per outcome → NumPy broadcast computes all arb sums at once.
    For each arb hit, evaluates BALANCED, UNBALANCED, and QUASI-ARB categories.
    """
    h_items = _unique_odds(pair, 'odds_1x2', 'home')
    d_items = _unique_odds(pair, 'odds_1x2', 'draw')
    a_items = _unique_odds(pair, 'odds_1x2', 'away')

    if not h_items or not d_items or not a_items:
        return []

    h_arr = np.array([x[0] for x in h_items], dtype=np.float64)
    d_arr = np.array([x[0] for x in d_items], dtype=np.float64)
    a_arr = np.array([x[0] for x in a_items], dtype=np.float64)

    # Shape: (nh, nd, na) — all arb sums in one shot
    arb  = 1.0/h_arr[:, None, None] + 1.0/d_arr[None, :, None] + 1.0/a_arr[None, None, :]
    hits = np.argwhere(arb < 1.0)

    results = []
    for hi, di, ai in hits:
        arb_sum    = float(arb[hi, di, ai])
        profit_pct = ((1.0 - arb_sum) / arb_sum) * 100.0
        results.extend(_all_categories(
            '1X2', arb_sum, profit_pct, total_stake,
            [
                ('Home Win', h_items[hi][1], h_items[hi][0]),
                ('Draw',     d_items[di][1], d_items[di][0]),
                ('Away Win', a_items[ai][1], a_items[ai][0]),
            ]
        ))
    return results


def scan_ou_numpy(pair, total_stake):
    """
    Gathers all O/U lines from all platforms, then for each line uses NumPy
    broadcast to compute all 7^2 arb sums at once.
    For each hit, evaluates BALANCED, UNBALANCED, and QUASI-ARB categories.
    """
    all_lines = set()
    for plat in PLATFORMS:
        all_lines.update((pair[plat].get('odds_ou') or {}).keys())  # guard: None → {}

    results = []
    for line in all_lines:
        o_items = _unique_ou_odds(pair, line, 'over')
        u_items = _unique_ou_odds(pair, line, 'under')
        if not o_items or not u_items:
            continue

        o_arr = np.array([x[0] for x in o_items], dtype=np.float64)
        u_arr = np.array([x[0] for x in u_items], dtype=np.float64)

        arb  = 1.0/o_arr[:, None] + 1.0/u_arr[None, :]
        hits = np.argwhere(arb < 1.0)

        for oi, ui in hits:
            arb_sum    = float(arb[oi, ui])
            profit_pct = ((1.0 - arb_sum) / arb_sum) * 100.0
            results.extend(_all_categories(
                f'Over/Under {line}', arb_sum, profit_pct, total_stake,
                [
                    (f'Over {line}',  o_items[oi][1], o_items[oi][0]),
                    (f'Under {line}', u_items[ui][1], u_items[ui][0]),
                ]
            ))
    return results


def scan_gg_numpy(pair, total_stake):
    """
    NumPy broadcast for all 7^2 GG/NG combos.
    For each hit, evaluates BALANCED, UNBALANCED, and QUASI-ARB categories.
    """
    y_items = _unique_odds(pair, 'odds_gg', 'yes')
    n_items = _unique_odds(pair, 'odds_gg', 'no')

    if not y_items or not n_items:
        return []

    y_arr = np.array([x[0] for x in y_items], dtype=np.float64)
    n_arr = np.array([x[0] for x in n_items], dtype=np.float64)

    arb  = 1.0/y_arr[:, None] + 1.0/n_arr[None, :]
    hits = np.argwhere(arb < 1.0)

    results = []
    for yi, ni in hits:
        arb_sum    = float(arb[yi, ni])
        profit_pct = ((1.0 - arb_sum) / arb_sum) * 100.0
        results.extend(_all_categories(
            'GG/NG', arb_sum, profit_pct, total_stake,
            [
                ('GG Yes', y_items[yi][1], y_items[yi][0]),
                ('GG No',  n_items[ni][1], n_items[ni][0]),
            ]
        ))
    return results


def _scan_one_group(group, total_stake, empty):
    """Scans a single matched group — called in parallel by run_intensive."""
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
    opps.extend(scan_1x2_numpy(pair, total_stake))
    opps.extend(scan_ou_numpy(pair, total_stake))
    opps.extend(scan_gg_numpy(pair, total_stake))

    return [{**meta, **arb} for arb in opps]


# ── MAIN ENTRY POINT ───────────────────────────────────────────────────────────

def run_intensive(total_stake=None,
                  sportybet_matches=None,
                  betway_matches=None,
                  footballcom_matches=None,
                  onexbet_matches=None,
                  twentytwobet_matches=None,
                  msport_matches=None,
                  bangbet_matches=None):
    """
    Main entry — accepts pre-loaded match lists or falls back to JSON files.
    Returns: (opportunities, num_groups)
    """
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

    raw = {
        'sportybet':    sportybet_matches,
        'betway':       betway_matches,
        'footballcom':  footballcom_matches,
        'onexbet':      onexbet_matches,
        'twentytwobet': twentytwobet_matches,
        'msport':       msport_matches,
        'bangbet':      bangbet_matches,
    }
    filtered_lists = {k: [m for m in v if not is_virtual_match(m)]
                      for k, v in raw.items()}

    all_matches = [m for v in filtered_lists.values() for m in v]
    groups      = match_all_platforms(all_matches)

    if not groups:
        return [], 0

    empty       = {'odds_1x2': {}, 'odds_ou': {}, 'odds_gg': {}}
    # Layer 3 — parallel group scanning
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


def display_all(opportunities, num_groups, total_stake,
                scrape_time=None, scan_time=None, total_time=None,
                calc_end_str=None, next_run_str=None):
    sep = '=' * 60

    balanced   = [o for o in opportunities if o.get('category') == 'balanced']
    unbalanced = [o for o in opportunities if o.get('category') == 'unbalanced']
    quasi      = [o for o in opportunities if o.get('category') == 'quasi']

    # ── Write each category to its own .txt file (overwrite) ──────────────
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

    # ── Quasi-Arb ML Logger (parallel logging for ML training data) ────────────
    quasi_summary = "  📊 [Quasi ML] No new opportunities found at this time of the scan"
    try:
        from engine.fb_quasi_arb_logger import log_quasi_opportunities
        quasi_summary = log_quasi_opportunities(quasi, total_stake, quiet=True)
    except Exception as _qml_err:
        print(f"  ⚠️  [Quasi ML] Logger error (non-fatal): {_qml_err}")

    # ── Compact terminal summary ──────────────────────────────────────────
    print(f"\n{sep}")
    print(f"⚽ Events scanned  : {num_groups}")
    print(f"🌐 Platforms       : 7 (Sportybet, Betway, Football.com, 1xBet, 22Bet, MSport, Bangbet)")
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
