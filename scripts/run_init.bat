@echo off
rem Backfill a date range: crawler (API -> raw) -> transform (raw -> stage) -> analyze (stage -> app).
rem Runs main.py init: a crawler failure does not stop the later steps (analyze checks its own inputs);
rem a transform failure skips analyze.
rem Usage:  scripts\run_init.bat 20261001            (20261001 to today)
rem         scripts\run_init.bat 20261001 20261031   (fixed range)
rem Double-click also works: it asks for the start date.
rem Output goes to the screen; start time and exit codes are appended to data\init.log
rem (Comments are in English on purpose: cmd may misread UTF-8 Chinese in .bat files.)

cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set UV_LINK_MODE=copy

set START=%~1
set END=%~2
if "%START%"=="" set /p START=Start date (YYYYMMDD):
if "%START%"=="" (
    echo No start date given.
    exit /b 1
)
set END_ARG=
if not "%END%"=="" set END_ARG=--end %END%

if not exist data mkdir data
echo ===== %date% %time% init %START% %END% >> data\init.log
uv run python main.py init --start %START% %END_ARG%
set EXIT_CODE=%errorlevel%
echo exit code %EXIT_CODE% >> data\init.log
echo exit code %EXIT_CODE%
if not "%EXIT_CODE%"=="0" exit /b 1
