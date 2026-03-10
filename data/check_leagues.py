import requests
from dotenv import load_dotenv
import os

load_dotenv()
API_KEY = os.getenv('ODDS_API_KEY')

# Fetch ALL available sports from the API
url = "https://api.the-odds-api.com/v4/sports"

params = {
    'apiKey': API_KEY
}

response = requests.get(url, params=params)
sports = response.json()

print("\n✅ ALL AVAILABLE FOOTBALL LEAGUES:\n")

for sport in sports:
    if 'soccer' in sport['key']:
        print(f"Key: {sport['key']}")
        print(f"Name: {sport['title']}")
        print(f"Active: {sport['active']}")
        print("-" * 40)
