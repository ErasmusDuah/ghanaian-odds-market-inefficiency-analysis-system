# Setup Guide

This guide explains how to set up and run the football system on a new PC.

The main README explains the research purpose of the project. This file focuses on practical setup, local configuration and run instructions.

## Requirements

Install these before running the system:

- Python 3.11 or newer
- Git
- Google Chrome or a Chromium-compatible browser
- A stable internet connection

The project was developed on Windows, so the examples below use PowerShell.

## Clone The Repository

```powershell
git clone https://github.com/ErasmusDuah/ghanaian-odds-market-inefficiency-analysis-system.git
cd ghanaian-odds-market-inefficiency-analysis-system
```

If you already have the project locally at `C:\quant_bet_alpha`, you can continue using that folder.

## Create A Virtual Environment

From the project root:

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the virtual environment again.

## Install Python Dependencies

```powershell
pip install -r requirements.txt
```

Some scrapers or supporting tools may use Playwright, so install the browser runtime too:

```powershell
playwright install chromium
```

## Create The Local Environment File

The football system reads local settings from:

```text
football/.env
```

A safe template is included:

```text
football/.env.example
```

Copy the example file:

```powershell
Copy-Item football\.env.example football\.env
```

Then open `football/.env` and fill in your local values.

Example:

```env
STARTING_CAPITAL=500
CURRENCY=GHS

TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

MAX_PARALLEL_SCRAPERS=8
SCRAPER_GLOBAL_TIMEOUT=90
```

The Telegram values are optional unless notification features are being used.

The `.env` file is ignored by Git and should never be committed because it may contain private credentials.

## Configuration Values

`STARTING_CAPITAL` controls the allocation amount used by the analysis engine.

`CURRENCY` controls the displayed currency label.

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are only needed if Telegram notification features are enabled.

`MAX_PARALLEL_SCRAPERS` controls how many scraper modules can run at the same time. For example, `MAX_PARALLEL_SCRAPERS=8` means the system can run up to 8 platform scrapers concurrently. A higher value may be faster but can increase timeouts or connection resets. A lower value may be slower but smoother.

`SCRAPER_GLOBAL_TIMEOUT` controls the maximum time, in seconds, allowed for a scraping cycle before slow platforms are excluded from that scan.

## Run The Football System

The active football runner is:

```powershell
python football\fb_run_intensive.py
```

If you want to run it with the project virtual environment explicitly:

```powershell
C:\quant_bet_alpha\.venv\Scripts\python.exe C:\quant_bet_alpha\football\fb_run_intensive.py
```

The intensive runner collects fresh market snapshots, applies validation checks, compares equivalent markets and writes local output files.

## Generated Local Outputs

Generated files are written mainly under:

```text
football/data/
```

These outputs are local analysis artifacts. They may include JSON snapshots, TXT summaries, logs and private tracker workbooks.

Most generated outputs are ignored by Git because they are rebuilt during normal use and may contain private or time-sensitive data.

## Private Files

These should stay local and should not be committed:

- `football/.env`
- generated odds JSON files
- generated match TXT files
- private tracker workbooks
- local scan histories
- raw debug captures

The repository includes `.gitignore` rules to help keep these files out of Git.

## Fresh Scan Behaviour

Each intensive run is designed to work from a fresh market snapshot.

The normal cycle is:

1. Clear old generated odds and match files
2. Collect fresh odds from supported platforms
3. Normalize platform data into a shared structure
4. Apply market validation checks
5. Run the intensive comparison engine
6. Write new local outputs

This reduces stale-data risk, although market values can still change after a snapshot is collected.

## Troubleshooting

If no matches are returned, check your internet connection first. Then rerun the system because a platform may have timed out or returned an incomplete response.

If a scraper is slow, reduce `MAX_PARALLEL_SCRAPERS` in `football/.env`.

If scans are too slow and your connection is stable, increase `MAX_PARALLEL_SCRAPERS` carefully.

If a platform repeatedly times out, increase `SCRAPER_GLOBAL_TIMEOUT` slightly.

If Python cannot find packages, make sure the virtual environment is activated and run `pip install -r requirements.txt` again.

If Playwright-related errors appear, run:

```powershell
playwright install chromium
```

## Responsible Use

This system is intended for educational, mathematical and research purposes.

The Ghanaian football odds market is used as a real-world dynamic pricing environment for studying implied probability, market disagreement, optimization, automation and data-driven analysis.