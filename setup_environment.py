#!/usr/bin/env python
"""Prepare the local environment for the football analysis system."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FOOTBALL_DIR = ROOT / "football"
DATA_DIR = FOOTBALL_DIR / "data"
ENV_FILE = FOOTBALL_DIR / ".env"
VENV_DIR = ROOT / ".venv"
INSTALL_STAMP = VENV_DIR / ".requirements.stamp"


DEFAULT_ENV = """# Local football system configuration.
# STARTING_CAPITAL is the analysis stake used by the intensive engine.
# You can change this value to any amount you want.

STARTING_CAPITAL=1700
CURRENCY=GHS


# Optional scraper/runtime controls.
MAX_PARALLEL_SCRAPERS=8
SCRAPER_GLOBAL_TIMEOUT=90
"""


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command))
    return subprocess.run(command, cwd=ROOT, text=True, check=check)


def _core_packages_available(python_path: Path) -> bool:
    probe = (
        "import pandas, requests, dotenv, playwright, curl_cffi, openpyxl, wasmtime; "
        "print('core packages ok')"
    )
    result = subprocess.run(
        [str(python_path), "-c", probe],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def _ensure_python_version() -> None:
    version = sys.version_info
    if version < (3, 11):
        raise SystemExit(
            "Python 3.11 or newer is required. "
            f"You are running Python {version.major}.{version.minor}."
        )


def _ensure_venv() -> Path:
    python_path = _venv_python()
    if not python_path.exists():
        print("Creating virtual environment in .venv ...")
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])
    else:
        print("Virtual environment already exists.")
    return python_path


def _install_requirements(python_path: Path) -> None:
    requirements = ROOT / "requirements.txt"
    if not requirements.exists():
        raise SystemExit("requirements.txt was not found.")

    requirements_hash = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if INSTALL_STAMP.exists() and INSTALL_STAMP.read_text(encoding="utf-8").strip() == requirements_hash:
        print("Python packages are already installed for the current requirements.txt.")
        return

    if _core_packages_available(python_path):
        print("Core Python packages are already available. Skipping dependency installation.")
        INSTALL_STAMP.write_text(requirements_hash, encoding="utf-8")
        return

    filtered = ROOT / ".requirements.install.txt"
    lines = []
    for raw_line in requirements.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            lines.append(raw_line)
            continue
        if platform.system() != "Windows" and line.lower().startswith("pywin32"):
            lines.append(f"# skipped on non-Windows: {raw_line}")
            continue
        lines.append(raw_line)

    filtered.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("Installing Python packages ...")
    _run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"])
    _run([str(python_path), "-m", "pip", "install", "-r", str(filtered)])

    print("Installing Playwright Chromium runtime ...")
    result = _run([str(python_path), "-m", "playwright", "install", "chromium"], check=False)
    if result.returncode != 0:
        print("WARNING: Playwright Chromium install did not complete. You can retry later with:")
        print(f"  {python_path} -m playwright install chromium")

    INSTALL_STAMP.write_text(requirements_hash, encoding="utf-8")


def _ensure_env_file() -> None:
    FOOTBALL_DIR.mkdir(exist_ok=True)
    if ENV_FILE.exists():
        print("football/.env already exists.")
        return

    ENV_FILE.write_text(DEFAULT_ENV, encoding="utf-8")
    print("Created football/.env with default STARTING_CAPITAL=1700.")


def _ensure_local_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("Local data folder is ready.")


def main() -> int:
    _ensure_python_version()
    python_path = _ensure_venv()
    _install_requirements(python_path)
    _ensure_env_file()
    _ensure_local_dirs()
    print("\nSetup complete.")
    print("Run the intensive engine with:")
    if os.name == "nt":
        print("  .\\.venv\\Scripts\\python.exe football\\fb_run_intensive.py")
    else:
        print("  ./.venv/bin/python football/fb_run_intensive.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
