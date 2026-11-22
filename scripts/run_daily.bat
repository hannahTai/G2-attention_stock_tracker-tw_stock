@echo off
rem Daily job: crawler (API -> raw) -> transform (raw -> stage) -> analyze (stage -> app) -> ai (LLM -> ai_* tables), today only.
rem Runs main.py: a crawler failure does not stop the later steps (analyze checks its own inputs);
rem a transform failure skips analyze and ai.
rem Called by Windows Task Scheduler; output is appended to data\logs\daily-YYYY-MM-DD.log
rem (one file per day, files older than 30 days are deleted; same naming as the Celery worker logs, see common\logs.py)
rem (Comments are in English on purpose: cmd may misread UTF-8 Chinese in .bat files.)

rem Go to the project root (this file lives in scripts\)
cd /d "%~dp0.."

rem Make Python write UTF-8, otherwise Chinese text breaks when redirected to the log
set PYTHONUTF8=1

rem Write each line to the log immediately (otherwise output only appears when the run ends)
set PYTHONUNBUFFERED=1

rem Project is on D: and the uv cache on C:, so hardlinks fail; copy quietly instead
set UV_LINK_MODE=copy

rem Today's date as YYYY-MM-DD (%date% depends on the Windows language setting, so ask PowerShell)
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
set LOG=data\logs\daily-%TODAY%.log

if not exist data\logs mkdir data\logs
rem Delete daily logs older than 30 days (the permanent record is the tw_stock.job_run table)
forfiles /p data\logs /m daily-*.log /d -30 /c "cmd /c del @path" >nul 2>&1

echo ===== %date% %time% >> %LOG%
uv run python main.py daily >> %LOG% 2>&1
set EXIT_CODE=%errorlevel%
echo exit code %EXIT_CODE% >> %LOG%
exit /b %EXIT_CODE%
