# Setup Guide

This guide explains how to install and run the football analysis system on a new computer.

The project is meant to be simple to run: download or clone the files, open the folder in your code editor, run the setup command once, then run the intensive engine.

## Quick Setup Flow

1. Install Python 3.11 or newer.
2. Clone or download the repository.
3. Open a terminal in the project folder.
4. Run `setup_environment.py` or use the runner script.
5. The system creates `.venv` and installs the required libraries.
6. The repository includes `football/.env` with a default stake amount.
7. The system creates `football/data/` for local runtime output.
8. Run `football/fb_run_intensive.py` through the virtual environment.

The default stake setting is:

```env
STARTING_CAPITAL=1700
```

You can change this in `football/.env`.

## 1. Install Python

Install Python 3.11 or newer from:

```text
https://www.python.org/downloads/
```

On Windows, tick this option during installation:

```text
Add python.exe to PATH
```

Check Python after installation.

Windows PowerShell:

```powershell
python --version
```

macOS or Linux:

```bash
python3 --version
```

## 2. Get The Project

Clone with Git:

```bash
git clone https://github.com/ErasmusDuah/ghanaian-odds-market-inefficiency-analysis-system.git
cd ghanaian-odds-market-inefficiency-analysis-system
```

Or download the ZIP from GitHub, extract it, and open a terminal inside the extracted project folder.

## 3. Run Setup

Windows PowerShell:

```powershell
python setup_environment.py
```

macOS or Linux:

```bash
python3 setup_environment.py
```

Setup does the following automatically:

- creates `.venv` if it does not exist
- installs packages from `requirements.txt`
- skips Windows-only packages on macOS/Linux
- installs the Playwright Chromium runtime when possible
- keeps `football/.env` available; if it is missing, setup recreates it with default values
- creates `football/data/` if it does not exist

## 4. Run The Intensive Engine

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe football\fb_run_intensive.py
```

macOS or Linux:

```bash
./.venv/bin/python football/fb_run_intensive.py
```


## 5. Change The Stake Amount

Open:

```text
football/.env
```

Then change:

```env
STARTING_CAPITAL=1000
```

The intensive runner uses `STARTING_CAPITAL` as the main stake setting. `STAKE_AMOUNT` is still accepted only as a legacy fallback for older local config files.

If the stake value is missing, zero or not a number, the system stops with a clear message.

## Generated Local Outputs

Generated files are written mainly under:

```text
football/data/
```

These outputs are local analysis artifacts. They may include JSON snapshots, TXT summaries, local histories and analysis logs.

On a fresh run, the intensive engine can create these local research trackers when it needs to write analysis output:

- `football/data/arbitrage_tracker.csv` for balanced and unbalanced opportunity history
- `football/data/quasi_arb_ml.xlsx` for quasi-arbitrage research data and repetition handling

Stake tracker workbooks are not part of the fresh install. They are local manual-log artifacts and should only appear when a local workflow explicitly logs staked opportunities.

Generated outputs are kept out of Git because they are rebuilt during normal use and may contain private or time-sensitive data.

## Private Local Files

These generated/private files should stay local and should not be committed:`r`n`r`n- `football/data/`
- generated odds JSON files
- generated match TXT files
- local tracker files
- local scan histories
- raw debug captures

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

If dependency installation fails, run setup again after checking your internet connection:

Windows PowerShell:

```powershell
python setup_environment.py
```

macOS or Linux:

```bash
python3 setup_environment.py
```
