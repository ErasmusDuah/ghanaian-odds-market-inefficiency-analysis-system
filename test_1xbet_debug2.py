import json
import time
from datetime import datetime

with open('data/onexbet_odds.json', 'r') as f:
    pass # Wait, that has the filtered matches.

import asyncio
from data.onexbet import scrape_onexbet

async def test():
    matches = await scrape_onexbet()
    print("Done")

if __name__ == "__main__":
    asyncio.run(test())
