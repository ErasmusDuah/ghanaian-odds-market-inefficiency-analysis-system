# ⚡ Quant Bet Alpha — Sports Arbitrage Engine

A high-performance, asynchronous sports arbitrage scanner for the Ghanaian market.
Scans **5 platforms** simultaneously and identifies risk-free arbitrage opportunities in real time.

## 🏦 Supported Platforms
| Platform | Method | Speed |
|---|---|---|
| SportyBet GH | API (aiohttp) | ~30s |
| 1xBet GH | API (aiohttp) | ~30s |
| 22Bet GH | API (aiohttp) | ~15s |
| Betway GH | Browser (Playwright) | ~45s |
| Football.com GH | Browser (Playwright) | ~45s |

##  Setup on a New PC

### 1. Install Python
Download Python 3.11+ from [python.org](https://python.org/downloads). Make sure to tick **"Add to PATH"** during installation.

### 2. Clone the Project
```bash
git clone https://github.com/YOUR_USERNAME/quant-bet-alpha.git
cd quant-bet-alpha
```

### 3. Install All Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Create Your `.env` File
Create a file named `.env` in the root folder with this content:
```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
STARTING_CAPITAL=500
CURRENCY=GHS
```
> ⚠️ Never share or commit your `.env` file. It contains your private credentials.

### 5. Run the System
```bash
# Run the full arbitrage engine (scans every 5 minutes)
python main.py

# Or run individual scrapers to test:
python data/sportybet.py
python data/onexbet.py
python data/twentytwobet.py
python data/betway.py
python data/footballcom.py

# Run the arbitrage engine only (reads from saved JSON files):
python engine/arbitrage_engine.py
```

## 📁 Project Structure
```
quant_bet_alpha/
├── main.py                    # Master scheduler — runs everything
├── requirements.txt           # All Python dependencies
├── .env                       # Your secrets (NOT in git)
│
├── data/                      # Scrapers
│   ├── sportybet.py
│   ├── onexbet.py
│   ├── twentytwobet.py
│   ├── betway.py
│   └── footballcom.py
│
├── engine/
│   └── arbitrage_engine.py    # Arb detection & stake calculator
│
├── api/
│   ├── server.py              # FastAPI web server
│   └── database.py            # SQLite ledger
│
└── monitoring/
    └── telegram_alerts.py     # Telegram bot alerts
```

## ⚙️ Configuration
All settings are controlled from your `.env` file:
| Variable | Description | Default |
|---|---|---|
| `STARTING_CAPITAL` | Total stake per arb cycle (GHS) | `500` |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token | — |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID | — |

## 📈 How It Works
1. `main.py` triggers all 5 scrapers simultaneously every 5 minutes
2. Each scraper fetches today's upcoming football odds from its platform
3. The arbitrage engine matches the same game across platforms using fuzzy team name matching
4. For every matched group, it checks 1X2, Over/Under, and GG/NG markets for arb
5. If arb is found (sum of implied probabilities < 1), it calculates optimal stakes
6. Alerts are sent to your Telegram bot with full bet details
