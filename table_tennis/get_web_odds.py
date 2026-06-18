import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()
        
        url = "https://1xbet.com.gh/en/line/table-tennis/3016632-wtt-contender-zagreb-doubles"
        print(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            print("Page loaded successfully.")
            
            # Wait a few seconds for JS rendering
            await page.wait_for_timeout(5000)
            
            # Get the body inner text
            body_text = await page.locator("body").inner_text()
            
            # Let's write the body text to a file so we can view it
            with open("body_text.txt", "w", encoding="utf-8") as f:
                f.write(body_text)
            print("Wrote body text to body_text.txt")
            
            # Print lines that contain Huang or Lin Shidong or Chen
            lines = body_text.split("\n")
            print("\nLines in body text containing 'Huang', 'Lin', 'Shidong', 'Zagreb', or 'Chen':")
            for i, line in enumerate(lines):
                if any(x in line for x in ["Huang", "Lin", "Shidong", "Zagreb", "Chen"]):
                    # Print context of 5 lines around the matching line
                    start = max(0, i - 2)
                    end = min(len(lines), i + 5)
                    print(f"\n--- Line {i} Context ---")
                    for j in range(start, end):
                        marker = ">>> " if j == i else "    "
                        print(f"{marker}{lines[j]}")
                    print("-" * 25)
                    
        except Exception as e:
            print(f"Error occurred: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
