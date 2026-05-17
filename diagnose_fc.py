import sys, json
sys.stdout.reconfigure(encoding='utf-8')
from engine.experimental_engine import match_all_platforms, is_virtual_match

def load(path):
    try:
        with open(path) as f: return json.load(f)
    except: return []

sb  = [m for m in load('data/sportybet_odds.json')    if not is_virtual_match(m)]
bw  = [m for m in load('data/betway_odds.json')       if not is_virtual_match(m)]
fc  = [m for m in load('data/footballcom_odds.json')  if not is_virtual_match(m)]
ox  = [m for m in load('data/onexbet_odds.json')      if not is_virtual_match(m)]
ttb = [m for m in load('data/twentytwobet_odds.json') if not is_virtual_match(m)]

groups = match_all_platforms(sb + bw + fc + ox + ttb)

fc_in_group   = 0
fc_best_home  = 0
fc_best_draw  = 0
fc_best_away  = 0
fc_best_over  = 0
fc_best_under = 0
fc_best_gg    = 0
fc_best_ng    = 0

for group in groups:
    sources = [m['source'] for m in group['matches']]
    if 'footballcom_gh' not in sources:
        continue
    fc_in_group += 1

    fc_m = next(m for m in group['matches'] if m['source'] == 'footballcom_gh')
    others = [m for m in group['matches'] if m['source'] != 'footballcom_gh']

    # 1X2 comparison
    fc_1x2 = fc_m.get('odds_1x2', {})
    best_h  = max((m.get('odds_1x2', {}).get('home', 0)  for m in others), default=0)
    best_d  = max((m.get('odds_1x2', {}).get('draw', 0)  for m in others), default=0)
    best_a  = max((m.get('odds_1x2', {}).get('away', 0)  for m in others), default=0)
    if fc_1x2.get('home', 0) > best_h: fc_best_home += 1
    if fc_1x2.get('draw', 0) > best_d: fc_best_draw += 1
    if fc_1x2.get('away', 0) > best_a: fc_best_away += 1

    # O/U comparison
    fc_ou = fc_m.get('odds_ou', {})
    for line, line_odds in fc_ou.items():
        other_overs  = [m.get('odds_ou', {}).get(line, {}).get('over',  0) for m in others]
        other_unders = [m.get('odds_ou', {}).get(line, {}).get('under', 0) for m in others]
        if line_odds.get('over',  0) > max(other_overs  or [0]): fc_best_over  += 1
        if line_odds.get('under', 0) > max(other_unders or [0]): fc_best_under += 1

    # GG/NG comparison
    fc_gg  = fc_m.get('odds_gg', {})
    best_y = max((m.get('odds_gg', {}).get('yes', 0) for m in others), default=0)
    best_n = max((m.get('odds_gg', {}).get('no',  0) for m in others), default=0)
    if fc_gg.get('yes', 0) > best_y: fc_best_gg += 1
    if fc_gg.get('no',  0) > best_n: fc_best_ng += 1

print(f"Groups with Football.com present : {fc_in_group}")
print()
print(f"Times FC has BEST 1X2 Home odds  : {fc_best_home}")
print(f"Times FC has BEST 1X2 Draw odds  : {fc_best_draw}")
print(f"Times FC has BEST 1X2 Away odds  : {fc_best_away}")
print(f"Times FC has BEST O/U Over odds  : {fc_best_over}")
print(f"Times FC has BEST O/U Under odds : {fc_best_under}")
print(f"Times FC has BEST GG Yes odds    : {fc_best_gg}")
print(f"Times FC has BEST GG No odds     : {fc_best_ng}")
print()

# Sample comparison
print("Sample side-by-side (first 3 matched groups with FC):")
count = 0
for group in groups:
    sources = [m['source'] for m in group['matches']]
    if 'footballcom_gh' not in sources: continue
    count += 1
    if count > 3: break
    fc_m = next(m for m in group['matches'] if m['source'] == 'footballcom_gh')
    ox_m = next((m for m in group['matches'] if m['source'] == '1xbet_gh'), None)
    bw_m = next((m for m in group['matches'] if m['source'] == 'betway_gh'), None)
    name = fc_m['home_team'] + " vs " + fc_m['away_team']
    print(f"  {name}")
    print(f"    FC   1X2: {fc_m.get('odds_1x2', {})}")
    if ox_m: print(f"    1xBet 1X2: {ox_m.get('odds_1x2', {})}")
    if bw_m: print(f"    Betway 1X2: {bw_m.get('odds_1x2', {})}")
    print()
