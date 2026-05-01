import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://1xbet.com.gh/en/line/football")
        
        url = "https://1xbet.com.gh/LineFeed/Get1x2_VZip?sports=1&count=5000&tf=1000000&tz=0&mode=4"
        print(f"Fetching {url}")
        
        data = await page.evaluate(f"""
            async () => {{
                const resp = await fetch("{url}", {{ credentials: 'include' }});
                return await resp.json();
            }}
        """)
        
        def extract(node, found):
            if isinstance(node, dict):
                if 'O1' in node and 'O2' in node:
                    found.append(node)
                    return
                for v in node.values():
                    extract(v, found)
            elif isinstance(node, list):
                for item in node:
                    extract(item, found)
                    
        events = []
        extract(data, events)
        print(f"Got {len(events)} events")
        
        await browser.close()

asyncio.run(run())
