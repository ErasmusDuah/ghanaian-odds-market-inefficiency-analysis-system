import json
data = json.load(open('data/onexbet_odds.json'))
empty = [m for m in data if not m.get('odds_1x2') and not m.get('odds_ou') and not m.get('odds_gg')]
print(f"Empty: {len(empty)}")
for m in empty:
    print(f"  {m['home_team']} vs {m['away_team']} | {m['tournament']}")
