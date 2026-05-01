import asyncio
import aiohttp
import json

TODAY_URL = (
    'https://www.sportybet.com/api/gh/factsCenter/'
    'pcUpcomingEvents?sportId=sr%3Asport%3A1'
    '&marketId=1%2C18%2C10%2C29%2C11%2C26%2C36%2C14%2C60100'
    '&pageSize=10&pageNum=1'
    '&todayGames=true&timeline=0.9'
)

async def test_fetch():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.sportybet.com/gh/sport/football',
        'Accept': 'application/json',
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(TODAY_URL, headers=headers) as resp:
            data = await resp.json()
            
            with open('scratch_sportybet_sample.json', 'w') as f:
                json.dump(data, f, indent=2)

if __name__ == "__main__":
    asyncio.run(test_fetch())
