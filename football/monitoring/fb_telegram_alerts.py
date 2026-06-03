"""
Football Telegram Alerts — Sends arb notifications to the Football Telegram group.
Reads credentials from football/.env
"""
import requests
import os
import time
import html
from dotenv import load_dotenv
from datetime import datetime

# Load football-specific .env
_DIR = os.path.dirname(os.path.abspath(__file__))
_SPORT_DIR = os.path.dirname(_DIR)
load_dotenv(os.path.join(_SPORT_DIR, '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SPORT_TAG = "⚽ FOOTBALL"


def send_message(message, reply_markup=None, silent=False):
    """Sends a message to your Telegram. Optionally attach inline keyboard buttons."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
        'disable_notification': silent,
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup
        
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code == 200:
                return True
            else:
                print(f"❌ Telegram API Error {response.status_code}: {response.text}")
                time.sleep(2)
        except requests.exceptions.RequestException as e:
            print(f"❌ Telegram request error (attempt {attempt+1}/3): {e}")
            time.sleep(2)
            
    return False


PLATFORM_URLS = {
    'Sportybet':    'https://www.sportybet.com/gh/sport/football/',
    'Betway':       'https://www.betway.com.gh/sport/soccer/',
    'Football.com': 'https://www.football.com/gh/sport/football',
    '1xBet':        'https://1xbet.com.gh/en/line/football/',
    '22Bet':        'https://22bet.com.gh/prematch/football',
    'MSport':       'https://www.msport.com/gh/sport/football',
}


def send_arb_alert(opportunity):
    """Sends arbitrage opportunity alert."""

    home = opportunity['match'].split(' vs ')[0].strip()
    away = opportunity['match'].split(' vs ')[-1].strip()

    safe_match = html.escape(opportunity['match'])
    safe_home = html.escape(home)
    safe_away = html.escape(away)
    safe_tournament = html.escape(opportunity['tournament'])
    safe_market = html.escape(opportunity['market'])

    bets = opportunity.get('bets', [])
    bet_lines = ""
    for bet in bets:
        platform_url = PLATFORM_URLS.get(bet['platform'], '#')
        safe_outcome = html.escape(bet['outcome'])
        bet_lines += (
            f"\n\n🎯 <b>{bet['platform']}</b>"
            f"\n   🔗 <a href=\"{platform_url}\">Open {bet['platform']}</a>"
            f"\n   Bet:   {safe_outcome}"
            f"\n   Odds:  {bet['odds']}"
            f"\n   Stake: GHS {bet['stake']:.2f}"
            f"\n   Win:   GHS {bet['profit_if_wins']:.2f}"
        )

    total_stake_used = sum(bet.get('stake', 0) for bet in bets)

    message = (
        f"⚡ <b>{SPORT_TAG} — ARB OPPORTUNITY FOUND!</b>\n"
        f"==================================\n"
        f"🏆 <b>{safe_match}</b>\n"
        f"📅 {opportunity['kickoff']} | {safe_tournament}\n"
        f"🔎 Search: <code>{safe_home} vs {safe_away}</code>\n"
        f"==================================\n"
        f"📊 Market: {safe_market}\n"
        f"💰 Profit: {opportunity['profit_pct']:.2f}% = GHS {opportunity['profit_ghs']:.2f}\n"
        f"💵 Total Stake: GHS {total_stake_used:.2f}\n\n"
        f"📋 <b>BETS TO PLACE:</b>"
        f"{bet_lines}\n\n"
        f"⏰ <i>Act fast — odds shift quickly!</i>"
    )

    return send_message(message)


def send_scan_summary(opportunities, events_scanned, cycle_time_seconds):
    """Sends scan summary"""
    
    total_minutes = cycle_time_seconds / 60

    if opportunities:
        total_profit = sum(o['profit_ghs'] for o in opportunities)
        best = max(opportunities, key=lambda x: x['profit_pct'])
        safe_best_match = html.escape(best['match'])
        
        message = (
            f"<b>🚨🚨 {SPORT_TAG} ARB FOUND!! SCAN COMPLETE!! 🚨🚨</b>\n"
            f"⚽ Events scanned    : {events_scanned}\n"
            f"⏱️ Total cycle time: {cycle_time_seconds:.1f} seconds ({total_minutes:.1f} minutes)\n"
            f"🎯 Arb opportunities : {len(opportunities)}!!\n"
            f"💰 Total potential profit: GHS {total_profit:.2f}!!\n"
            f"📈 Best: {best['profit_pct']:.2f}% on {safe_best_match}!!"
        )
    else:
        message = (
            f"<b>{SPORT_TAG} SCAN COMPLETE!</b>\n"
            f"⚽ Events scanned    : {events_scanned}\n"
            f"⏱️ Total cycle time: {cycle_time_seconds:.1f} seconds ({total_minutes:.1f} minutes)\n"
            f"💡 No arb opportunities right now"
        )

    return send_message(message, silent=False)


def send_scan_started_message(scan_count):
    """Sends notification that a scan has begun"""
    message = (
        f"🔄 <b>{SPORT_TAG} — Starting Scan #{scan_count}</b>\n"
        f"🕐 Time: {datetime.now().strftime('%H:%M:%S')}\n"
        f"⏳ Fetching fresh odds..."
    )
    return send_message(message, silent=False)


def send_startup_message():
    """Sends system startup notification"""
    message = (
        f"🚀 <b>{SPORT_TAG} System Started!</b>\n\n"
        f"✅ Sportybet Ghana\n"
        f"✅ Betway Ghana\n"
        f"✅ Football.com Ghana\n"
        f"✅ 1xBet Ghana\n"
        f"✅ 22Bet Ghana\n"
        f"✅ MSport Ghana\n\n"
        f"🔍 Scanning every 5 minutes\n"
        f"📱 You'll be alerted when arb found!\n\n"
        f"🕐 Started: "
        f"{datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}"
    )
    return send_message(message)


def test_connection():
    """Tests Telegram connection"""
    return send_message(
        f"✅ <b>{SPORT_TAG}</b> — Telegram connected!")


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    print("Testing Football Telegram connection...")
    if test_connection():
        print("✅ Message sent successfully!")
    else:
        print("❌ Failed to send message")
