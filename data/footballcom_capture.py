import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime


async def find_today():

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()

        async def handle_response(response):
            try:
                if 'factsCenter' in response.url:
                    ct = response.headers.get('content-type', '')
                    if 'json' in ct:
                        data = await response.json()
                        size = len(json.dumps(data))
                        if size > 5000:
                            print(f"\n📡 Size: {size}")
                            print(f"   URL: {response.url}")
            except Exception:
                pass

        page.on('response', handle_response)

        print("🌐 Loading Football.com Ghana...")
        await page.goto(
            'https://www.football.com/gh/sport/football',
            timeout=60000,
            wait_until='domcontentloaded'
        )
        await page.wait_for_timeout(3000)

        print("\n🖱️ Looking for Today button...")
        # Try clicking today filter
        try:
            buttons = await page.query_selector_all('button, a')
            for btn in buttons:
                text = await btn.inner_text()
                if 'today' in text.lower() or \
                        'Today' in text:
                    print(f"  Found: {text}")
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    break
        except Exception as e:
            print(f"  ⚠️ {e}")

        # Also try scrolling
        for i in range(5):
            await page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)

        await page.wait_for_timeout(3000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(find_today())