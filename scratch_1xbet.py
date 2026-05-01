import requests
import json

url = "https://1xbet.com.gh/LineFeed/Get1x2_VZip?sports=1&count=1000&tf=2400000&tz=0&mode=4&lng=en"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
}
resp = requests.get(url, headers=headers)
print("Status:", resp.status_code)
if resp.status_code == 200:
    data = resp.json()
    items = data.get("Value", [])
    print("Found items:", len(items))
