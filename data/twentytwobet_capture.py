import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime


async def check_22bet():

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()

        async def handle_response(response):
            try:
                if 'api/event/list' in response.url:
                    ct = response.headers.get('content-type', '')
                    if 'json' in ct:
                        data = await response.json()
                        size = len(json.dumps(data))
                        print(f"\n📡 event/list found!")
                        print(f"   Size: {size}")
                        print(f"   Type: {type(data)}")

                        if isinstance(data, dict):
                            print(f"   Keys: {list(data.keys())[:10]}")
                        elif isinstance(data, list):
                            print(f"   Length: {len(data)}")
                            if data:
                                print(f"   First keys: "
                                      f"{list(data[0].keys())[:10]}")
                                print(f"\n   First item:")
                                print(json.dumps(
                                    data[0], indent=2)[:800])

                        with open('data/twentytwobet_sample.json',
                                  'w') as f:
                            json.dump(data, f, indent=2)
                        print(f"\n   💾 Saved!")

            except Exception as e:
                print(f"  ❌ {e}")

        page.on('response', handle_response)

        print("🌐 Loading 22Bet Ghana...")
        await page.goto(
            'https://22bet.com.gh/line/football',
            timeout=60000,
            wait_until='domcontentloaded'
        )
        await page.wait_for_timeout(8000)
        await browser.close()


if __name__ == "__main__":
    print("\n" + "🟣 " * 20)
    print("   22BET - DATA STRUCTURE CHECK")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟣 " * 20 + "\n")

    asyncio.run(check_22bet())