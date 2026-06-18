import asyncio
import aiohttp

async def fetch_odds(site):
    url = f"{site}/service-api/LineFeed/GetGameZip?id=728259926&lng=en&cfview=0&isSubGames=true&GroupEvents=true&countevents=250"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"{site}/en/line/table-tennis",
        "Origin": site,
        "Accept": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    val = data.get("Value") or {}
                    ge = val.get("GE") or []
                    home_odd, away_odd = None, None
                    for g in ge:
                        if g.get("G") == 1:
                            events = g.get("E") or []
                            for row in events:
                                for e in row:
                                    if e.get("T") == 1:
                                        home_odd = e.get("C") or e.get("CV")
                                    elif e.get("T") == 3:
                                        away_odd = e.get("C") or e.get("CV")
                    print(f"Site: {site} => Home: {home_odd}, Away: {away_odd}")
                else:
                    print(f"Site: {site} => Status {resp.status}")
        except Exception as e:
            print(f"Site: {site} => Error: {e}")

async def main():
    await fetch_odds("https://1xbet.com.gh")
    await fetch_odds("https://1xbet.com")
    # Also test another common mirror or sister brand like 22bet just in case
    await fetch_odds("https://22bet.com")

if __name__ == "__main__":
    asyncio.run(main())
