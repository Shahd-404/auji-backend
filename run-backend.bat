@echo off
setlocal
cd /d %~dp0

if not exist .venv\Scripts\python.exe (
  py -m venv .venv
  call .\.venv\Scripts\python.exe -m pip install -U pip wheel
  call .\.venv\Scripts\pip.exe install -r requirements.txt
)

set PYTHONPATH=%cd%
set SCRAPER_HEADLESS=1

call .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
endlocal
