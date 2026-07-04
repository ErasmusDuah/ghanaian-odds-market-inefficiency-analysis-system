# Setup Guide

This guide explains how to install and run the football analysis system on a new computer.

The main README explains the research purpose of the project. This file focuses on practical setup, local configuration and running the intensive engine.

## Quick Setup Flow

1. Install Python manually.
2. Clone or download the repository.
3. Run one setup/run command.
4. The system creates a virtual environment.
5. The system installs the required Python packages.
6. The system creates `football/.env` with a default stake amount of `800` cedis.
7. The system creates the needed local folders and analysis tracker files.
8. Optional: add Telegram details if you want alerts.
9. Run the intensive system.

The default setup uses:

```env
STARTING_CAPITAL=800
```

You can change this later in `football/.env` if you want a different amount.

## 1. Install Python

Install Python 3.11 or newer from:

```text
https://www.python.org/downloads/
```

On Windows, tick this option during installation:

```text
Add python.exe to PATH
```

After installation, open a new terminal and check Python.

Windows PowerShell:

```powershell
python --version
```

macOS or Linux:

```bash
python3 --version
```

## 2. Get The Project

### Option A: Clone With Git

Git is useful for cloning and updating the project, but the system itself does not require Git to run after the files are on your computer.

Windows PowerShell, macOS or Linux:

```bash
git clone https://github.com/ErasmusDuah/ghanaian-odds-market-inefficiency-analysis-system.git
cd ghanaian-odds-market-inefficiency-analysis-system
```

### Option B: Download Without Git

If Git is not installed, download the project through your browser:

1. Open this GitHub link in Chrome, Edge, Firefox or any browser:

```text
https://github.com/ErasmusDuah/ghanaian-odds-market-inefficiency-analysis-system
```

2. Click the green `Code` button near the top-right of the project page.
3. Click `Download ZIP`.
4. Go to your Downloads folder.
5. Right-click the downloaded ZIP file and extract it.
6. Open a terminal inside the extracted project folder.

The system can run normally without Git after the files are on your computer.

## 3. Run The One-Command Setup

The first run installs dependencies and then starts the intensive engine.

Windows PowerShell:

```powershell
.\run.ps1
```

If PowerShell blocks scripts, run this once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then run again:

```powershell
.\run.ps1
```

macOS or Linux:

```bash
chmod +x run.sh
./run.sh
```

The run command does the following automatically:

- creates `.venv` if it does not exist
- installs packages from `requirements.txt`
- skips Windows-only packages on macOS/Linux
- installs the Playwright Chromium runtime when possible
- creates `football/.env` if it does not exist
- creates `football/data/` if it does not exist
- starts `football/fb_run_intensive.py`

## 4. Change The Stake Amount

The setup creates `football/.env` with this default value:

```env
STARTING_CAPITAL=800
```

To use a different amount, open:

```text
football/.env
```

Then change the value:

```env
STARTING_CAPITAL=1000
```

The intensive runner uses `STARTING_CAPITAL` as the main stake setting. `STAKE_AMOUNT` is still accepted only as a legacy fallback for older local config files.

If the stake value is missing, zero or not a number, the system stops with a clear message so the run does not continue with a bad configuration.

## 5. Telegram Alerts Are Optional

Telegram is not required. If the Telegram fields are blank, the system runs normally without alerts.

The config can stay like this:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

If you want Telegram alerts, follow the steps below.

## 6. Create A Telegram Bot

1. Open Telegram.
2. Search for `@BotFather`.
3. Open the verified BotFather chat.
4. Send:

```text
/start
```

5. Send:

```text
/newbot
```

6. BotFather will ask for a bot name. This can be any display name, for example:

```text
Odds Analysis Alerts
```

7. BotFather will ask for a username. It must end with `bot`, for example:

```text
ghanaian_odds_alert_bot
```

8. BotFather will send a bot token. It looks like this:

```text
123456789:ABCDEF_your_token_here
```

9. Copy that token into `football/.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCDEF_your_token_here
```

Do not commit `football/.env` to Git. It is private and already ignored by `.gitignore`.

## 7. Get Your Telegram Chat ID

Your bot needs your chat ID before it can send alerts.

1. Open your new bot in Telegram.
2. Press `Start` or send:

```text
/start
```

3. In the project folder, run this after setup has installed the packages.

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe football\fb_get_group_id.py
```

macOS or Linux:

```bash
./.venv/bin/python football/fb_get_group_id.py
```

4. Follow the message printed in the terminal.
5. Copy the chat ID into `football/.env`:

```env
TELEGRAM_CHAT_ID=123456789
```

For some groups, Telegram chat IDs can be negative. That is normal.

## 8. Run The Intensive Engine Again

After the first setup, future runs use the existing virtual environment.

Windows PowerShell:

```powershell
.\run.ps1
```

macOS or Linux:

```bash
./run.sh
```

You can also run the engine directly.

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe football\fb_run_intensive.py
```

macOS or Linux:

```bash
./.venv/bin/python football/fb_run_intensive.py
```

## Generated Local Outputs

Generated files are written mainly under:

```text
football/data/
```

These outputs are local analysis artifacts. They may include JSON snapshots, TXT summaries and analysis logs.

On a fresh PC, the system automatically creates these local analysis trackers when needed:

- `football/data/arbitrage_tracker.csv` for balanced and unbalanced opportunity history
- `football/data/quasi_arb_ml.xlsx` for quasi-arbitrage research data and repetition handling

Most generated outputs are ignored by Git because they are rebuilt during normal use and may contain private or time-sensitive data.

## Private Files

These should stay local and should not be committed:

- `football/.env`
- generated odds JSON files
- generated match TXT files
- private local tracker files
- local scan histories
- raw debug captures

The repository includes `.gitignore` rules to help keep these files out of Git.

## Fresh Scan Behaviour

Each intensive run is designed to work from a fresh market snapshot.

The normal cycle is:

1. Clear old generated odds and match files.
2. Collect fresh odds from supported platforms.
3. Normalize platform data into a shared structure.
4. Apply market validation checks.
5. Run the intensive comparison engine.
6. Write new local outputs.

This reduces stale-data risk because the analysis starts from a new snapshot on each scan.

## Troubleshooting

If `python` is not recognized on Windows, reinstall Python and tick `Add python.exe to PATH`.

If `python3` is not found on macOS/Linux, install Python 3.11 or newer through Python.org or your system package manager.

If PowerShell blocks `run.ps1`, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

If dependency installation fails, check your internet connection and run the setup command again.

If Playwright browser installation fails, retry later with:

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

macOS or Linux:

```bash
./.venv/bin/python -m playwright install chromium
```

If no matches are returned, check your internet connection first. Then rerun the system because a platform may have timed out or returned an incomplete response.

If a scraper is slow, reduce `MAX_PARALLEL_SCRAPERS` in `football/.env`.

If scans are too slow and your connection is stable, increase `MAX_PARALLEL_SCRAPERS` carefully.

If a platform repeatedly times out, increase `SCRAPER_GLOBAL_TIMEOUT` slightly.

## Responsible Use

This system is intended for educational, mathematical and research purposes.

The Ghanaian football odds market is used as a real-world dynamic pricing environment for studying implied probability, market disagreement, optimization, automation and data-driven analysis.
