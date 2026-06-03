import os
import requests
import sys
from dotenv import load_dotenv

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

def get_group_ids():
    # Load the bot token from .env
    ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    load_dotenv(ENV_PATH)
    
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        print("❌ Error: TELEGRAM_BOT_TOKEN not found in .env file.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    
    try:
        print("Fetching recent messages sent to your bot...")
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if not data.get('ok'):
            print(f"❌ Telegram API Error: {data.get('description')}")
            return
            
        updates = data.get('result', [])
        if not updates:
            print("\n⚠️ No recent messages found.")
            print("👉 Try this: Go to your Telegram group, make sure your bot is added, and type 'hello bot' in the group. Then run this script again.")
            return
            
        found_groups = {}
        for update in updates:
            message = update.get('message', update.get('my_chat_member', {}))
            chat = message.get('chat')
            
            if chat and chat.get('type') in ['group', 'supergroup']:
                chat_id = chat.get('id')
                title = chat.get('title', 'Unknown Group')
                found_groups[chat_id] = title
                
        if found_groups:
            print("\n✅ Found the following groups:")
            print("-" * 40)
            for cid, title in found_groups.items():
                print(f"Group Name : {title}")
                print(f"Chat ID    : {cid}")
            print("-" * 40)
            print("\n👉 Copy the negative number above and paste it into your .env file as TELEGRAM_CHAT_ID.")
        else:
            print("\n⚠️ No group messages found.")
            print("👉 Try this: Go to your Telegram group, type 'hello bot' in the chat, and run this script again.")
            
    except Exception as e:
        print(f"❌ Error connecting to Telegram: {e}")

if __name__ == "__main__":
    get_group_ids()
