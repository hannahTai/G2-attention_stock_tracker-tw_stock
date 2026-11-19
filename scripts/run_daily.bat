@echo off
rem Daily job: crawler (API -> raw) -> transform (raw -> stage) -> analyze (stage -> app), today only.
rem Runs main.py: a crawler failure does not stop the later steps (analyze checks its own inputs);
rem a transform failure skips analyze.
rem Called by Windows Task Scheduler; output is appended to data\daily.log
rem (Comments are in English on purpose: cmd may misread UTF-8 Chinese in .bat files.)

rem Go to the project root (this file lives in scripts\)
cd /d "%~dp0.."

rem Make Python write UTF-8, otherwise Chinese text breaks when redirected to the log
set PYTHONUTF8=1

rem Write each line to the log immediately (otherwise output only appears when the run ends)
set PYTHONUNBUFFERED=1

rem Project is on D: and the uv cache on C:, so hardlinks fail; copy quietly instead
set UV_LINK_MODE=copy

if not exist data mkdir data
echo ===== %date% %time% >> data\daily.log
uv run python main.py daily >> data\daily.log 2>&1
set EXIT_CODE=%errorlevel%
echo exit code %EXIT_CODE% >> data\daily.log
exit /b %EXIT_CODE%
