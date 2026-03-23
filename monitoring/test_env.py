from dotenv import load_dotenv
import os

load_dotenv()

token = os.getenv('TELEGRAM_BOT_TOKEN')
chat_id = os.getenv('TELEGRAM_CHAT_ID')

print(f"Token: {token}")
print(f"Chat ID: {chat_id}")