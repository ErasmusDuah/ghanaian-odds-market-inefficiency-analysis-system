import requests

BOT_TOKEN = "8552584667:AAFXkNwuiEvgL0MhVDThiywPEK4zv7thbyM"
CHAT_ID = "1998913506"

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

payload = {
    'chat_id': CHAT_ID,
    'text': "Test message from Quant Bet Alpha!"
}

response = requests.post(url, json=payload)
print(f"Status: {response.status_code}")
print(f"Response: {response.text}")