import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Intercept and print network requests
        api_requests = []
        
        async def handle_request(request):
            url = request.url
            if "service-api" in url or "api" in url or "Feed" in url:
                api_requests.append(url)
                
        page.on("request", handle_request)
        
        url = "https://1xbet.com.gh/en/line/table-tennis/3016632-wtt-contender-zagreb-doubles"
        print(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)
            
            print("\nAPI requests made by the page:")
            for req in api_requests:
                print(f" - {req}")
                
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
