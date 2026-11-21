@echo off
rem Website = two programs (same pattern as the course):
rem   1. Data API  (FastAPI + uvicorn)  http://localhost:8001/docs   reads the app_* tables and views
rem   2. Web page  (Streamlit)          http://localhost:8502        only calls the API, never the database
rem The API opens in its own window. To stop: close both windows (or Ctrl+C in each).
rem Run main.py at least once first (tables, views, one day of results).
rem (Comments are in English on purpose: cmd may misread UTF-8 Chinese in .bat files.)

rem Go to the project root (this file lives in scripts\); Streamlit reads .streamlit\config.toml from here
cd /d "%~dp0.."

rem Stop if something is already using the ports (for example an old server still running)
netstat -ano | findstr /r /c:":8001 .*LISTENING" >nul && (
    echo Port 8001 is already in use. Close the old server window first, then run this again.
    pause
    exit /b 1
)
netstat -ano | findstr /r /c:":8502 .*LISTENING" >nul && (
    echo Port 8502 is already in use. Close the old Streamlit window first, then run this again.
    pause
    exit /b 1
)

rem Make Python write UTF-8
set PYTHONUTF8=1

rem Project is on D: and the uv cache on C:, so hardlinks fail; copy quietly instead
set UV_LINK_MODE=copy

rem 1. Data API in a new window (--reload: restart automatically when a file is saved)
start "API - uvicorn api.main:app" cmd /k uv run uvicorn api.main:app --reload --port 8001

rem 2. Open the browser after a few seconds, when the web page has started
start "" cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:8502"

rem 3. Web page in this window (settings in .streamlit\config.toml: port 8502, no first-run email question)
uv run streamlit run dashboard/app.py
