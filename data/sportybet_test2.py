import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime


async def scrape_sportybet():

    captured_odds = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()

        # Intercept ALL API responses
        async def handle_response(response):
            url = response.url
            try:
                ct = response.headers.get('content-type', '')
                if 'json' in ct and 'factsCenter' in url:
                    data = await response.json()
                    captured_odds.append({
                        'url': url,
                        'data': data
                    })
                    print(f"  📡 Captured: {url[50:]}")
            except Exception:
                pass

        page.on('response', handle_response)

        print("🌐 Opening Sportybet Ghana...")

        await page.goto(
            'https://www.sportybet.com/gh/sport/football',
            timeout=30000,
            wait_until='networkidle'
        )

        await page.wait_for_timeout(5000)

        print(f"\n✅ Captured {len(captured_odds)} API responses")

        # Save everything
        with open('data/sportybet_api_calls.json', 'w') as f:
            json.dump(captured_odds, f, indent=2)

        print("💾 Saved to data/sportybet_api_calls.json")
        print("\n📋 All captured URLs:")
        for item in captured_odds:
            print(f"  → {item['url']}")

        await browser.close()

    return captured_odds


if __name__ == "__main__":
    print("\n" + "🟢 " * 20)
    print("   SPORTYBET API CAPTURE")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}")
    print("🟢 " * 20 + "\n")

    asyncio.run(scrape_sportybet())