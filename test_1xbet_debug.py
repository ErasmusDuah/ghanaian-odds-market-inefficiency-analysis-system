import asyncio
from data.onexbet import scrape_onexbet

async def test():
    matches = await scrape_onexbet()
    for m in matches[:5]:
        print(m)

if __name__ == "__main__":
    asyncio.run(test())
