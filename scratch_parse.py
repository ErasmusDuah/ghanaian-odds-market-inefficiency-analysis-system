import json

with open('scratch_sportybet_sample.json') as f:
    data = json.load(f)

tournaments = data.get('data', {}).get('tournaments', [])
for tournament in tournaments:
    for event in tournament.get('events', []):
        markets = event.get('markets', [])
        print(f"Event: {event.get('homeTeamName')} vs {event.get('awayTeamName')}")
        for m in markets:
            if str(m.get('id')) == '18':
                print(json.dumps(m, indent=2))
        break # Just one event per tournament
