"""
INTENSIVE ENGINE — PAIRING DIAGNOSTIC

Shows EVERY platform combination being tried for each matched game,
so you can see exactly how the exhaustive scanning works.

Usage:
    python diagnose_intensive.py

Reads from the last scraped JSON files in data/.
"""

import json
import os
import sys

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.intensive_engine import (
    match_all_platforms, is_virtual_match,
    PLATFORMS, PLATFORM_DISPLAY, SOURCE_MAP,
    _f, _unique_odds, _unique_ou_odds,
)
import numpy as np

# ── LOAD DATA ──────────────────────────────────────────────────────────────────

def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []

raw = {
    'sportybet':    load('data/sportybet_odds.json'),
    'betway':       load('data/betway_odds.json'),
    'footballcom':  load('data/footballcom_odds.json'),
    'onexbet':      load('data/onexbet_odds.json'),
    'twentytwobet': load('data/twentytwobet_odds.json'),
    'msport':       load('data/msport_odds.json'),
    'bangbet':      load('data/bangbet_odds.json'),
}

print("\n" + "=" * 70)
print("  INTENSIVE ENGINE — PAIRING DIAGNOSTIC")
print("=" * 70)
print("\n📦 Matches loaded per platform:")
for plat, matches in raw.items():
    print(f"   {PLATFORM_DISPLAY[plat]:15s}: {len(matches)} matches")

# Filter virtual
filtered = {k: [m for m in v if not is_virtual_match(m)] for k, v in raw.items()}
all_matches = [m for v in filtered.values() for m in v]

print(f"\n🔗 Total matches across all platforms: {len(all_matches)}")

# ── GROUP ──────────────────────────────────────────────────────────────────────

groups = match_all_platforms(all_matches)
empty  = {'odds_1x2': {}, 'odds_ou': {}, 'odds_gg': {}}

print(f"⚽ Matched game groups found: {len(groups)}")
print()

if not groups:
    print("❌ No groups found — not enough cross-platform matches to compare.")
    sys.exit(0)

# ── SHOW EACH GROUP ────────────────────────────────────────────────────────────

for g_idx, group in enumerate(groups, 1):
    matches    = group['matches']
    first      = matches[0]
    match_name = f"{first['home_team']} vs {first['away_team']}"
    kickoff    = first['kickoff']
    sources    = group['sources']

    print("=" * 70)
    print(f"  GROUP {g_idx}: {match_name}")
    print(f"  Kickoff : {kickoff}")
    print(f"  Found on: {len(set(sources))} platforms → "
          f"{', '.join(PLATFORM_DISPLAY[p] for p in PLATFORMS if SOURCE_MAP[p] in sources)}")
    print("=" * 70)

    pair = {
        plat: next((m for m in matches if m['source'] == SOURCE_MAP[plat]), empty)
        for plat in PLATFORMS
    }

    # ── 1X2 PAIRINGS ──────────────────────────────────────────────────────────
    print("\n  ── 1X2 MARKET ──────────────────────────────────────────────────")

    h_items = _unique_odds(pair, 'odds_1x2', 'home')
    d_items = _unique_odds(pair, 'odds_1x2', 'draw')
    a_items = _unique_odds(pair, 'odds_1x2', 'away')

    print(f"  Unique Home odds available : "
          f"{[(round(o,2), PLATFORM_DISPLAY[p]) for o,p in h_items] or 'none'}")
    print(f"  Unique Draw odds available : "
          f"{[(round(o,2), PLATFORM_DISPLAY[p]) for o,p in d_items] or 'none'}")
    print(f"  Unique Away odds available : "
          f"{[(round(o,2), PLATFORM_DISPLAY[p]) for o,p in a_items] or 'none'}")

    if h_items and d_items and a_items:
        h_arr = [x[0] for x in h_items]
        d_arr = [x[0] for x in d_items]
        a_arr = [x[0] for x in a_items]

        combo_count = len(h_arr) * len(d_arr) * len(a_arr)
        print(f"\n  Combinations to check: {len(h_arr)} × {len(d_arr)} × {len(a_arr)} = {combo_count}")
        print()
        print(f"  {'Home Platform':15s} | {'Draw Platform':15s} | {'Away Platform':15s} | "
              f"{'H-Odds':>7} | {'D-Odds':>7} | {'A-Odds':>7} | {'Arb Sum':>8} | {'Profit%':>8}")
        print(f"  {'-'*15}-+-{'-'*15}-+-{'-'*15}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*8}-+-{'-'*8}")

        for h_o, h_p in h_items:
            for d_o, d_p in d_items:
                for a_o, a_p in a_items:
                    arb  = 1/h_o + 1/d_o + 1/a_o
                    pct  = ((1 - arb) / arb * 100) if arb < 1 else 0
                    flag = " ✅ ARB!" if arb < 1 else ""
                    print(f"  {PLATFORM_DISPLAY[h_p]:15s} | {PLATFORM_DISPLAY[d_p]:15s} | "
                          f"{PLATFORM_DISPLAY[a_p]:15s} | {h_o:7.2f} | {d_o:7.2f} | "
                          f"{a_o:7.2f} | {arb:8.4f} | {pct:7.2f}%{flag}")
    else:
        print("  ⚠️  Not enough odds to form 1X2 combinations")

    # ── O/U PAIRINGS ──────────────────────────────────────────────────────────
    all_lines = set()
    for plat in PLATFORMS:
        all_lines.update(pair[plat].get('odds_ou', {}).keys())

    if all_lines:
        print(f"\n  ── O/U MARKET ({len(all_lines)} lines) ─────────────────────────────────")
        for line in sorted(all_lines):
            o_items = _unique_ou_odds(pair, line, 'over')
            u_items = _unique_ou_odds(pair, line, 'under')

            if not o_items or not u_items:
                continue

            combo_count = len(o_items) * len(u_items)
            print(f"\n  Line {line} — {len(o_items)} × {len(u_items)} = {combo_count} combos")
            print(f"  {'Over Platform':15s} | {'Under Platform':15s} | "
                  f"{'O-Odds':>7} | {'U-Odds':>7} | {'Arb Sum':>8} | {'Profit%':>8}")
            print(f"  {'-'*15}-+-{'-'*15}-+-{'-'*7}-+-{'-'*7}-+-{'-'*8}-+-{'-'*8}")

            for o_o, o_p in o_items:
                for u_o, u_p in u_items:
                    arb  = 1/o_o + 1/u_o
                    pct  = ((1 - arb) / arb * 100) if arb < 1 else 0
                    flag = " ✅ ARB!" if arb < 1 else ""
                    print(f"  {PLATFORM_DISPLAY[o_p]:15s} | {PLATFORM_DISPLAY[u_p]:15s} | "
                          f"{o_o:7.2f} | {u_o:7.2f} | {arb:8.4f} | {pct:7.2f}%{flag}")

    # ── GG/NG PAIRINGS ────────────────────────────────────────────────────────
    y_items = _unique_odds(pair, 'odds_gg', 'yes')
    n_items = _unique_odds(pair, 'odds_gg', 'no')

    if y_items and n_items:
        combo_count = len(y_items) * len(n_items)
        print(f"\n  ── GG/NG MARKET — {combo_count} combos ──────────────────────────────")
        print(f"  {'GG-Yes Platform':15s} | {'GG-No Platform':15s} | "
              f"{'Y-Odds':>7} | {'N-Odds':>7} | {'Arb Sum':>8} | {'Profit%':>8}")
        print(f"  {'-'*15}-+-{'-'*15}-+-{'-'*7}-+-{'-'*7}-+-{'-'*8}-+-{'-'*8}")

        for y_o, y_p in y_items:
            for n_o, n_p in n_items:
                arb  = 1/y_o + 1/n_o
                pct  = ((1 - arb) / arb * 100) if arb < 1 else 0
                flag = " ✅ ARB!" if arb < 1 else ""
                print(f"  {PLATFORM_DISPLAY[y_p]:15s} | {PLATFORM_DISPLAY[n_p]:15s} | "
                      f"{y_o:7.2f} | {n_o:7.2f} | {arb:8.4f} | {pct:7.2f}%{flag}")

    print()

print("=" * 70)
print(f"  DONE — {len(groups)} group(s) diagnosed")
print("=" * 70)
