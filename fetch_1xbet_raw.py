import requests
import json
import time

url = "https://1xbet.com.gh/LineFeed/Get1x2_VZip?sports=1&count=500&tf=1000000&tz=0&mode=4"
resp = requests.get(url)
data = resp.json()

def walk(node, found):
    if isinstance(node, dict):
        if 'O1' in node and 'O2' in node:
            found.append(node)
            return
        for v in node.values():
            walk(v, found)
    elif isinstance(node, list):
        for item in node:
            walk(item, found)

events = []
walk(data, events)

print(f"Total events: {len(events)}")
for ev in events:
    o1 = ev.get('O1', '')
    o2 = ev.get('O2', '')
    if 'Ferroviaria' in o1 or 'Guarani' in o1 or 'Palmeiras' in o1 or 'Caxias' in o1:
        s = ev.get('S')
        print(f"Found: {o1} vs {o2} at {s}")
