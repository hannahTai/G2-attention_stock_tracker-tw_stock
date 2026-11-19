@echo off
rem Rebuild stage and app from the raw already saved (no API calls): tests -> transform (raw -> stage) -> analyze (stage -> app).
rem Use it after changing the rules or the transform code.
rem Usage:  scripts\run_rebuild.bat 20260701            (20260701 to today)
rem         scripts\run_rebuild.bat 20260701 20261031   (fixed range)
rem Output is appended to data\rebuild.log
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
rem No end date: today, worked out once so transform and analyze use the same range even past midnight
if "%END%"=="" for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set END=%%d
set END_ARG=--end %END%

if not exist data mkdir data
echo ===== %date% %time% rebuild %START% %END% >> data\rebuild.log
uv run pytest -q >> data\rebuild.log 2>&1
if errorlevel 1 (
    echo tests failed, nothing rebuilt >> data\rebuild.log
    exit /b 1
)
uv run python -m transform.producer backfill --start %START% %END_ARG% >> data\rebuild.log 2>&1
if errorlevel 1 (
    echo transform failed, analyze skipped >> data\rebuild.log
    exit /b 1
)
uv run python -m analyze.producer backfill --start %START% %END_ARG% >> data\rebuild.log 2>&1
echo exit code %errorlevel% >> data\rebuild.log
