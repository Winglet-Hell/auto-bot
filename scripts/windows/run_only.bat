@echo off
cd /d "%~dp0\..\.."
if not exist ".venv\Scripts\python.exe" (
  echo [error] Virtual env not found. Please run scripts\windows\run_bot.bat first.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
set PYTHONPATH=src
python -c "from auto_bot import run; run()"


