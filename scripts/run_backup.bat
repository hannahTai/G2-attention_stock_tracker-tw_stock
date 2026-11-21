@echo off
rem Database backup: pg_dump of raw / stage / app into data\backup\, keeps the latest 14.
rem Called by Windows Task Scheduler (weekdays 23:30, after run_daily.bat); output is appended to data\backup.log
rem (Comments are in English on purpose: cmd may misread UTF-8 Chinese in .bat files.)

cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set UV_LINK_MODE=copy

if not exist data mkdir data
echo ===== %date% %time% >> data\backup.log
uv run python scripts\backup_db.py >> data\backup.log 2>&1
echo exit code %errorlevel% >> data\backup.log
