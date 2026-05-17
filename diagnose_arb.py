"""
Diagnostic: compare what the engine finds vs what a raw scan finds.
Helps identify why Claude/ChatGPT found more arb than the engine.
"""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')

from engine.experimental_engine import match_all_platforms, validate_odds, get_best_odds

platforms = {
    'sportybet_gh':    'data/sportybet_odds.json',
    'footballcom_gh':  'data/footballcom_odds.json',
    '1xbet_gh':        'data/onexbet_odds.json',
    'twentytwobet_gh': 'data/twentytwobet_odds.json',
    'betway_gh':       'data/betway_odds.json',
}

data = {}
for src, path in platforms.items():
    try:
        with open(path) as f:
            data[src] = json.load(f)
        print(f"  {src}: {len(data[src])} matches")
    except FileNotFoundError:
        data[src] = []
        print(f"  {src}: MISSING")

all_matches = []
for src, matches in data.items():
    all_matches.extend(matches)

groups = match_all_platforms(all_matches)
print(f"\nMatched groups: {len(groups)}\n")

# ── SCAN 1: RAW (no validate_odds — like Claude/GPT would do) ─────────────────
raw_1x2_arb = []

for group in groups:
    matches = group['matches']
    first = matches[0]
    label = f"{first['home_team']} vs {first['away_team']}"

    # Raw: just grab best odds per outcome, no full-market validation
    best_h = max(((m.get('odds_1x2', {}).get('home', 0), m['source']) for m in matches), key=lambda x: x[0])
    best_d = max(((m.get('odds_1x2', {}).get('draw', 0), m['source']) for m in matches), key=lambda x: x[0])
    best_a = max(((m.get('odds_1x2', {}).get('away', 0), m['source']) for m in matches), key=lambda x: x[0])

    if all(o[0] > 1.01 for o in [best_h, best_d, best_a]):
        arb_sum = 1/best_h[0] + 1/best_d[0] + 1/best_a[0]
        if arb_sum < 1:
            profit = (1 - arb_sum) / arb_sum * 100
            raw_1x2_arb.append((label, profit, arb_sum, best_h, best_d, best_a))

# ── SCAN 2: VALIDATED (what current engine does) ───────────────────────────────
validated_1x2_arb = []

empty = {'odds_1x2': {}, 'odds_ou': {}, 'odds_gg': {}}
source_keys = {
    'sportybet':    'sportybet_gh',
    'betway':       'betway_gh',
    'footballcom':  'footballcom_gh',
    'onexbet':      '1xbet_gh',
    'twentytwobet': 'twentytwobet_gh',
}
platform_labels = {
    'sportybet':    'Sportybet',
    'betway':       'Betway',
    'footballcom':  'Football.com',
    'onexbet':      '1xBet',
    'twentytwobet': '22Bet',
}

for group in groups:
    matches = group['matches']
    first = matches[0]
    label = f"{first['home_team']} vs {first['away_team']}"

    pair = {k: next((m for m in matches if m['source'] == v), empty)
            for k, v in source_keys.items()}

    odds_pairs = [(pair[k].get('odds_1x2', {}), platform_labels[k]) for k in source_keys]

    best_h = get_best_odds('home', '1x2', *odds_pairs)
    best_d = get_best_odds('draw', '1x2', *odds_pairs)
    best_a = get_best_odds('away', '1x2', *odds_pairs)

    if all(o[0] > 1.01 for o in [best_h, best_d, best_a]):
        arb_sum = 1/best_h[0] + 1/best_d[0] + 1/best_a[0]
        if arb_sum < 1:
            profit = (1 - arb_sum) / arb_sum * 100
            validated_1x2_arb.append((label, profit))

# ── COMPARE ────────────────────────────────────────────────────────────────────
print("=" * 60)
print(f"RAW scan (no validate_odds):  {len(raw_1x2_arb)} 1X2 arb found")
print(f"VALIDATED scan (current):     {len(validated_1x2_arb)} 1X2 arb found")
print("=" * 60)

raw_labels    = {r[0] for r in raw_1x2_arb}
val_labels    = {r[0] for r in validated_1x2_arb}
missed        = raw_labels - val_labels

print(f"\n>>> MISSED by validated engine ({len(missed)}):")
for r in raw_1x2_arb:
    if r[0] in missed:
        h, d, a = r[3], r[4], r[5]
        print(f"\n  {r[0]}  | profit={r[1]:.2f}%")
        print(f"    Home: {h[0]} ({h[1]})")
        print(f"    Draw: {d[0]} ({d[1]})")
        print(f"    Away: {a[0]} ({a[1]})")
        # Find out WHY it was missed — check each platform's validate_odds
        for grp in groups:
            if grp['matches'][0]['home_team'] == r[0].split(' vs ')[0]:
                for src_key, src_id in source_keys.items():
                    m = next((x for x in grp['matches'] if x['source'] == src_id), None)
                    if m and m.get('odds_1x2'):
                        od = m['odds_1x2']
                        valid = validate_odds(od, '1x2')
                        h_val = od.get('home', 0)
                        d_val = od.get('draw', 0)
                        a_val = od.get('away', 0)
                        if h_val > 1.01 or d_val > 1.01 or a_val > 1.01:
                            arb_s = (1/h_val if h_val > 1.01 else 0) + \
                                    (1/d_val if d_val > 1.01 else 0) + \
                                    (1/a_val if a_val > 1.01 else 0)
                            print(f"    [{platform_labels[src_key]}] odds={od} | arb_sum={arb_s:.3f} | valid={valid}")
                break

print("\n\nAll raw arb found:")
for r in sorted(raw_1x2_arb, key=lambda x: -x[1]):
    star = " *** MISSED ***" if r[0] in missed else ""
    print(f"  {r[1]:.2f}% | {r[0]}{star}")
