import asyncio, aiohttp, json
from datetime import datetime

async def test_bangbet():
    url = 'https://bet-api.bangbet.com/api/bet/match/list'
    payload = {'sportId': 1, 'marketId': 1, 'pageNum': 1, 'pageSize': 100, 'timeType': 0}
    headers = {'Content-Type': 'application/json'}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            records = data.get('data', {}).get('list', [])
            print(f'Bangbet records: {len(records)}')
            for r in records[:3]:
                print(f"  {r.get('homeTeamName')} vs {r.get('awayTeamName')} | {r.get('scheduledDate')} | {r.get('startTime')}")

async def test_supabet():
    url = 'https://sportsbook-eu01-backend.advbet.com/distributor/api/organizations/17e5b1b7-bb38-d332-4e2f-f1fde542b6b9/events'
    params = {'filter.sports': 'football', 'filter.types': 'match', 'view': 'full', 'offset': 0, 'limit': 10}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            events = data.get('result', [])
            print(f'\nSupabet events: {len(events)}')
            for e in events[:3]:
                closes = e.get('closesAt') or e.get('startAt')
                print(f"  Match ID {e.get('id')} | closesAt: {closes}")

async def test_soccabet():
    url = 'https://www.soccabet.com/api/endpoint'
    payload = {'command': 'get_sports', 'params': {}}
    headers = {'Content-Type': 'application/json', 'Referer': 'https://www.soccabet.com'}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            sports = (data.get('data') or {}).get('sport') or data.get('sport') or []
            print(f'\nSoccabet sports: {[s.get("name") for s in sports[:5]]}')

async def main():
    await test_bangbet()
    await test_supabet()
    await test_soccabet()

asyncio.run(main())
