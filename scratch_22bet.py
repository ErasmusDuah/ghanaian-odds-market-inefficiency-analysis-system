from playwright.sync_api import sync_playwright
import time
import json

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://22bet.com.gh/en/line/football")
    time.sleep(3)
    result = page.evaluate("""
        async () => {
            const resp = await fetch("https://22bet.com.gh/LineFeed/Get1x2_VZip?sports=1&count=1000&tf=2400000&mode=4", {
                credentials: 'include',
                headers: { 'Accept': 'application/json' }
            });
            return await resp.json();
        }
    """)
    print("Fetched items:", len(result.get("Value", [])))
    browser.close()
