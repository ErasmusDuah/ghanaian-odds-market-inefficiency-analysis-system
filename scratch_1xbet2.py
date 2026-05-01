from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    def on_resp(resp):
        if "LineFeed" in resp.url or "Get1x2" in resp.url or "sports" in resp.url:
            print("Intercepted:", resp.url)
    page.on("response", on_resp)
    page.goto("https://1xbet.com.gh/en/line/football")
    time.sleep(5)
    browser.close()
