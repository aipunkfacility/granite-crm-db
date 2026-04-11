@echo off
chcp 65001 >nul
echo ============================================
echo   Messenger propagation
echo   raw_companies -^> companies + enriched
echo ============================================
echo.
echo DB: data\granite.db
echo.

set SCRIPT_DIR=%~dp0
set PROJECT=%SCRIPT_DIR%..\granite-crm-db

echo 1 - Run
echo 2 - Dry run (no writes)
echo 3 - Exit
echo.
set /p choice="Choice (1-3): "

if "%choice%"=="1" goto run
if "%choice%"=="2" goto dryrun
if "%choice%"=="3" goto end
goto end

:run
echo Running propagation...
cd /d "%PROJECT%"
python scripts/propagate_messengers.py
goto done

:dryrun
echo Running DRY RUN...
cd /d "%PROJECT%"
python scripts/propagate_messengers.py --dry-run
goto done

:done
echo.
pause
goto end

:end
