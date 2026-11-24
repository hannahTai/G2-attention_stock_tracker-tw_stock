@echo off
rem Backfill a date range: both lines, stock line first (TWSE -> transform -> analyze),
rem then the news line (MOPS, FinMind news -> transform -> ai; it reads the notices the stock line loaded).
rem Runs main.py backfill: a crawler failure does not stop the later steps; a transform failure skips that line's last step.
rem Usage:  scripts\run_backfill.bat 20261001            (20261001 to today)
rem         scripts\run_backfill.bat 20261001 20261031   (fixed range)
rem Double-click also works: it asks for the start date.
rem Output goes to the screen; start time and exit codes are appended to data\backfill.log
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
echo ===== %date% %time% backfill %START% %END% >> data\backfill.log
uv run python main.py backfill --start %START% %END_ARG%
set EXIT_CODE=%errorlevel%
echo exit code %EXIT_CODE% >> data\backfill.log
echo exit code %EXIT_CODE%
if not "%EXIT_CODE%"=="0" exit /b 1
