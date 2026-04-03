import requests
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')


def send_message(message):
    """Sends a message to your Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Telegram error: {e}")
        return False


def send_arb_alert(opportunity):
    """Sends arbitrage opportunity alert"""

    bets = opportunity.get('bets', [])
    bet_lines = ""
    for bet in bets:
        bet_lines += (
            f"\n🎯 <b>{bet['platform']}</b>"
            f"\n   Bet: {bet['outcome']}"
            f"\n   Odds: {bet['odds']}"
            f"\n   Stake: GHS {bet['stake']:.2f}"
        )

    message = (
        f"🚨 <b>ARB OPPORTUNITY FOUND!</b>\n\n"
        f"⚽ <b>{opportunity['match']}</b>\n"
        f"🏆 {opportunity['tournament']}\n"
        f"🕐 Kickoff: {opportunity['kickoff']}\n\n"
        f"📊 Market: {opportunity['market']}\n"
        f"💰 Profit: {opportunity['profit_pct']:.2f}%"
        f" = GHS {opportunity['profit_ghs']:.2f}\n\n"
        f"📋 <b>BETS TO PLACE:</b>"
        f"{bet_lines}\n\n"
        f"⚡ Found at: "
        f"{datetime.now().strftime('%H:%M:%S')}"
    )

    return send_message(message)


def send_scan_summary(opportunities, scan_num):
    """Sends scan summary"""

    if opportunities:
        message = (
            f"✅ <b>Scan #{scan_num} Complete</b>\n"
            f"🎯 {len(opportunities)} opportunities found!\n"
            f"💰 Total profit: GHS "
            f"{sum(o['profit_ghs'] for o in opportunities):.2f}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
    else:
        message = (
            f"🔍 <b>Scan #{scan_num}</b> — No arb yet\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )

    return send_message(message)


def send_startup_message():
    """Sends system startup notification"""
    message = (
        f"🚀 <b>Quant Bet Alpha Started!</b>\n\n"
        f"✅ Sportybet Ghana\n"
        f"✅ Betway Ghana\n"
        f"✅ Football.com Ghana\n"
        f"✅ 1xBet Ghana\n"
        f"✅ 22Bet Ghana\n\n"
        f"🔍 Scanning every 5 minutes\n"
        f"📱 You'll be alerted when arb found!\n\n"
        f"🕐 Started: "
        f"{datetime.now().strftime('%A, %d %B %Y %H:%M:%S')}"
    )
    return send_message(message)


def test_connection():
    """Tests Telegram connection"""
    return send_message(
        "✅ <b>Quant Bet Alpha</b> — Telegram connected!")


if __name__ == "__main__":
    print("Testing Telegram connection...")
    if test_connection():
        print("✅ Message sent successfully!")
    else:
        print("❌ Failed to send message")
