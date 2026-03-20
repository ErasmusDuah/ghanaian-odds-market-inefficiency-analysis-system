import json

with open('data/sportybet_sample.json') as f:
    sample = json.load(f)

tournaments = sample.get('data', {}).get('tournaments', [])
if tournaments:
    events = tournaments[0].get('events', [])
    if events:
        print(f"Match: {events[0].get('homeTeamName')} vs {events[0].get('awayTeamName')}")
        markets = events[0].get('markets', [])
        print(f"\nAvailable markets ({len(markets)} total):")
        for m in markets:
            outcomes = m.get('outcomes', [])
            print(f"\n  ID: {m.get('id')} | Name: {m.get('name')} | Desc: {m.get('desc')}")
            for o in outcomes[:3]:
                print(f"    → {o.get('desc', '')} odds: {o.get('odds')}")