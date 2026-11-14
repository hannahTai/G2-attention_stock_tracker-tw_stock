@echo off
rem Re-run transform (raw -> stage) and analyze (stage -> app) for a date range after running the tests (e.g. after a rule changed).
rem Usage:  scripts\run_stage.bat 20260701            (20260701 to today)
rem         scripts\run_stage.bat 20260701 20261031   (fixed range)
rem Output is appended to data\stage.log
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
echo ===== %date% %time% stage %START% %END% >> data\stage.log
uv run pytest -q >> data\stage.log 2>&1
if errorlevel 1 (
    echo tests failed, stage not updated >> data\stage.log
    exit /b 1
)
uv run python -m transform.producer init --start %START% %END_ARG% >> data\stage.log 2>&1
if errorlevel 1 (
    echo transform failed, analyze skipped >> data\stage.log
    exit /b 1
)
uv run python -m analyze.producer init --start %START% %END_ARG% >> data\stage.log 2>&1
echo exit code %errorlevel% >> data\stage.log
