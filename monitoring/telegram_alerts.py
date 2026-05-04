import requests
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TOTAL_STAKE = int(os.getenv('STARTING_CAPITAL', 500))


def send_message(message, reply_markup=None):
    """Sends a message to your Telegram. Optionally attach inline keyboard buttons."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Telegram error: {e}")
        return False


PLATFORM_URLS = {
    'Sportybet':    'https://www.sportybet.com/gh/sport/football/',
    'Betway':       'https://www.betway.com.gh/sport/soccer/',
    'Football.com': 'https://www.football.com.gh/',
    '1xBet':        'https://1xbet.com.gh/en/line/football/',
    '22Bet':        'https://22bet.com.gh/prematch/football',
}


def send_arb_alert(opportunity):
    """Sends arbitrage opportunity alert with inline keyboard buttons that open
    bookmaker links in the device's external browser (not Telegram's in-app browser)."""

    home = opportunity['match'].split(' vs ')[0].strip()
    away = opportunity['match'].split(' vs ')[-1].strip()

    bets = opportunity.get('bets', [])
    bet_lines = ""
    # Build one inline keyboard button per platform (opens in external browser)
    keyboard_buttons = []
    for bet in bets:
        platform_url = PLATFORM_URLS.get(bet['platform'], '#')
        bet_lines += (
            f"\n\n🎯 <b>{bet['platform']}</b>"
            f"\n   🔗 <a href=\"{platform_url}\">Open {bet['platform']}</a>"
            f"\n   Bet:   {bet['outcome']}"
            f"\n   Odds:  {bet['odds']}"
            f"\n   Stake: GHS {bet['stake']:.2f}"
            f"\n   Win:   GHS {bet['profit_if_wins']:.2f}"
        )
        # Each platform gets its own row so the label is clearly readable
        keyboard_buttons.append([
            {"text": f"🌐 Open {bet['platform']}", "url": platform_url}
        ])

    message = (
        f"⚡ <b>ARB OPPORTUNITY FOUND!</b>\n"
        f"==================================\n"
        f"🏆 <b>{opportunity['match']}</b>\n"
        f"📅 {opportunity['kickoff']} | {opportunity['tournament']}\n"
        f"🔎 Search: <code>{home} vs {away}</code>\n"
        f"==================================\n"
        f"📊 Market: {opportunity['market']}\n"
        f"💰 Profit: {opportunity['profit_pct']:.2f}% = GHS {opportunity['profit_ghs']:.2f}\n"
        f"💵 Total Stake: GHS {TOTAL_STAKE}\n\n"
        f"📋 <b>BETS TO PLACE:</b>"
        f"{bet_lines}\n\n"
        f"⏰ <i>Act fast — odds shift quickly!</i>\n"
        f"👇 <i>Tap a button below to open the platform in your browser:</i>"
    )

    # Inline keyboard — URL buttons always open in the external browser
    reply_markup = {"inline_keyboard": keyboard_buttons} if keyboard_buttons else None

    return send_message(message, reply_markup=reply_markup)


def send_scan_summary(opportunities, events_scanned, cycle_time_seconds):
    """Sends scan summary"""
    
    total_minutes = cycle_time_seconds / 60

    if opportunities:
        total_profit = sum(o['profit_ghs'] for o in opportunities)
        best = max(opportunities, key=lambda x: x['profit_pct'])
        
        message = (
            f"<b>SCAN COMPLETE!</b>\n"
            f"⚽ Events scanned    : {events_scanned}\n"
            f"⏱️ Total cycle time: {cycle_time_seconds:.1f} seconds ({total_minutes:.1f} minutes)\n"
            f"🎯 Arb opportunities : {len(opportunities)}\n"
            f"💰 Total potential profit: GHS {total_profit:.2f}\n"
            f"📈 Best: {best['profit_pct']:.2f}% on {best['match']}"
        )
    else:
        message = (
            f"<b>SCAN COMPLETE!</b>\n"
            f"⚽ Events scanned    : {events_scanned}\n"
            f"⏱️ Total cycle time: {cycle_time_seconds:.1f} seconds ({total_minutes:.1f} minutes)\n"
            f"💡 No arb opportunities right now"
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
