"""Check: after merging ALL pages, how many events have market 621?"""
import asyncio, json, re, time
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

async def diagnose():
    captured_url = None
    captured_data = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1366, 'height': 768},
        )
        await context.route(
            re.compile(r'\.(png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|mp4|mp3)(\?|$)', re.I),
            lambda route: route.abort()
        )
        page = await context.new_page()

        async def on_response(response):
            nonlocal captured_url, captured_data
            if captured_url: return
            if 'event/list' not in response.url: return
            try:
                body = await response.body()
                if len(body) < 200: return
                data = json.loads(body)
                inner = data.get('data', {})
                if 'items' in inner and inner['items']:
                    captured_url = response.url
                    captured_data = inner
            except: pass

        page.on('response', on_response)
        await page.goto('https://22bet.com.gh/prematch/football', timeout=45000, wait_until='domcontentloaded')
        for _ in range(40):
            if captured_url: break
            await page.wait_for_timeout(500)

        if not captured_data:
            print("FAILED")
            await browser.close()
            return

        last_page = captured_data.get('lastPage', 1)
        print(f"Total pages: {last_page}")

        # Merge odds from ALL pages
        merged_odds = {}
        # Page 1
        for eid, markets in captured_data.get('relations', {}).get('odds', {}).items():
            merged_odds[eid] = list(markets) if isinstance(markets, list) else [markets]

        # Fetch pages 2-N in batches
        BATCH = 8
        all_page_urls = []
        for pg in range(2, last_page + 1):
            pg_url = re.sub(r'page=\d+', f'page={pg}', captured_url)
            all_page_urls.append(pg_url)

        print(f"Fetching {len(all_page_urls)} more pages...")
        for batch_start in range(0, len(all_page_urls), BATCH):
            batch = all_page_urls[batch_start:batch_start + BATCH]
            batch_json = json.dumps(batch)
            try:
                results = await page.evaluate(f"""
                    async () => {{
                        const urls = {batch_json};
                        return await Promise.all(
                            urls.map(url =>
                                fetch(url, {{ credentials: 'include' }})
                                    .then(r => r.json())
                                    .catch(() => null)
                            )
                        );
                    }}
                """)
                for r in (results or []):
                    if not r: continue
                    inner = r.get('data', {})
                    if not inner: continue
                    raw_odds = inner.get('relations', {}).get('odds', {})
                    for eid, markets in raw_odds.items():
                        if eid in merged_odds:
                            if isinstance(markets, list):
                                merged_odds[eid].extend(markets)
                        else:
                            merged_odds[eid] = list(markets) if isinstance(markets, list) else [markets]
            except Exception as e:
                print(f"  Error: {e}")
            if batch_start + BATCH < len(all_page_urls):
                await page.wait_for_timeout(300)

        await browser.close()

    # Now count how many events have market 621
    has_621 = 0
    no_621 = 0
    market_id_counts = {}
    for eid, markets in merged_odds.items():
        found_621 = False
        for m in markets:
            mid = m.get('id')
            market_id_counts[mid] = market_id_counts.get(mid, 0) + 1
            if mid == 621:
                found_621 = True
        if found_621:
            has_621 += 1
        else:
            no_621 += 1

    print(f"\n=== AFTER MERGING ALL PAGES ===")
    print(f"Total events with odds: {len(merged_odds)}")
    print(f"Events WITH market 621 (1X2): {has_621}")
    print(f"Events WITHOUT market 621: {no_621}")
    print(f"\nTop 10 market IDs:")
    for mid, count in sorted(market_id_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  Market {mid}: {count} events")

asyncio.run(diagnose())
