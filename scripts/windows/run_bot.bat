@echo off
setlocal ENABLEDELAYEDEXPANSION
cd /d "%~dp0\..\.."

if not exist ".venv\Scripts\python.exe" (
  echo [setup] Creating virtual environment...
  py -3.12 -m venv .venv 2>nul || python -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
if exist requirements.txt (
  echo [setup] Installing requirements...
  python -m pip install -r requirements.txt --prefer-binary
) else (
  python -m pip install playwright python-dotenv --prefer-binary
)

python -m playwright install chromium

set PYTHONPATH=src
python -c "from auto_bot import run; run()"


