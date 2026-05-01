import asyncio
from data.onexbet import scrape_onexbet
import json

async def test():
    matches = await scrape_onexbet()
    with open("c:\\quant_bet_alpha\\1xbet_test_output.json", "w") as f:
        json.dump(matches, f, indent=2)

if __name__ == "__main__":
    asyncio.run(test())
