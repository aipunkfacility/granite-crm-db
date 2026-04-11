@echo off
title JSprav Enrichment

echo ============================================
echo   JSprav - messenger enrichment
echo ============================================
echo.

cd /d "%~dp0"

set DB_PATH=data\granite.db

if not exist "%DB_PATH%" (
    echo [ERROR] DB not found: %DB_PATH%
    pause
    exit /b 1
)

echo DB: %DB_PATH%
echo.
echo   1 - Dry run
echo   2 - All cities
echo   3 - One city
echo   4 - Exit
echo.

set /p CHOICE="Choice (1-4): "

if "%CHOICE%"=="1" goto dry_run
if "%CHOICE%"=="2" goto all_cities
if "%CHOICE%"=="3" goto one_city
if "%CHOICE%"=="4" goto exit

echo Invalid choice.
goto end

:dry_run
echo [DRY RUN]...
python scripts\enrich_jsprav_messengers.py --db %DB_PATH% --dry-run
goto end

:all_cities
echo Running all cities...
python scripts\enrich_jsprav_messengers.py --db %DB_PATH%
goto end

:one_city
set /p CITY="City name: "
python scripts\enrich_jsprav_messengers.py --db %DB_PATH% --cities %CITY%
goto end

:exit
exit /b 0

:end
echo Done.
pause
