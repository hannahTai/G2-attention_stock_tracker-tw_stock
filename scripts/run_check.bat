@echo off
rem Daily check: stock line done mark (trading days), latest news line run, API / web, latest backup.
rem Problems pop up as a Windows notification and the exit code is 1 (Task Scheduler shows it as the last run result).
rem Task Scheduler runs this every day at 22:30 (after the last stock line run at 22:00; see docs\flow.md).
rem Output is appended to data\logs\check-YYYY-MM-DD.log (files older than 30 days are deleted).
rem (Comments are in English on purpose: cmd may misread UTF-8 Chinese in .bat files.)

cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set UV_LINK_MODE=copy

for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
set LOG=data\logs\check-%TODAY%.log

if not exist data\logs mkdir data\logs
forfiles /p data\logs /m check-*.log /d -30 /c "cmd /c del @path" >nul 2>&1

echo ===== %date% %time% >> %LOG%
uv run python scripts\check_health.py --notify >> %LOG% 2>&1
set EXIT_CODE=%errorlevel%
echo exit code %EXIT_CODE% >> %LOG%
exit /b %EXIT_CODE%
