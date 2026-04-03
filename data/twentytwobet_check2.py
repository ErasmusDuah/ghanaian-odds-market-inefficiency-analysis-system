import json

with open('data/twentytwobet_sample.json') as f:
    data = json.load(f)

inner = data.get('data', {})
items = inner.get('items', [])
relations = inner.get('relations', {})
odds = relations.get('odds', {})
competitors = relations.get('competitors', [])

# Find first match with market 621
for item in items:
    event_id = str(item.get('id'))
    event_odds = odds.get(event_id, [])

    market_621 = next(
        (m for m in event_odds if m.get('id') == 621),
        None)

    if market_621:
        c1 = next((c for c in competitors
                   if c.get('id') == item.get(
                       'competitor1Id')), {})
        c2 = next((c for c in competitors
                   if c.get('id') == item.get(
                       'competitor2Id')), {})

        print(f"⚽ {c1.get('name')} vs {c2.get('name')}")
        print(f"🕐 {item.get('time')}")
        print(f"Market 621 outcomes:")
        for o in market_621.get('outcomes', []):
            print(f"  id:{o.get('id')} "
                  f"odds:{o.get('odds')}")

        # Also check 868 and 289
        market_868 = next(
            (m for m in event_odds
             if m.get('id') == 868), None)
        market_289 = next(
            (m for m in event_odds
             if m.get('id') == 289 and
             'total=2.5' in str(
                 m.get('specifiers', ''))), None)

        if market_868:
            print(f"\nMarket 868 (BTTS):")
            for o in market_868.get('outcomes', []):
                print(f"  id:{o.get('id')} "
                      f"odds:{o.get('odds')}")

        if market_289:
            print(f"\nMarket 289 O/U 2.5:")
            for o in market_289.get('outcomes', []):
                print(f"  id:{o.get('id')} "
                      f"odds:{o.get('odds')}")
        break