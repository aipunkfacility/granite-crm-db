@echo off
setlocal enabledelayedexpansion

:: Granite CRM - DB Backup Script
:: Usage: backup_db.bat [path_to_db]

set "DB_PATH=%~1"
if "%DB_PATH%"=="" set "DB_PATH=data\granite.db"

:: Check DB exists
if not exist "%DB_PATH%" (
    echo [ERROR] Database not found: %DB_PATH%
    exit /b 1
)

:: Create backup directory
if not exist "backups" mkdir backups

:: Timestamp: YYYYMMDD_HHMMSS
for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set "d=%%c%%a%%b"
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set "t=%%a%%b"
set "TIMESTAMP=%d%_%t%"

:: Backup filename
set "BACKUP_FILE=backups\granite_%TIMESTAMP%.db"

:: Copy
echo [INFO] Backing up: %DB_PATH% -^> %BACKUP_FILE%
copy /Y "%DB_PATH%" "%BACKUP_FILE%" >nul

:: Verify
if exist "%BACKUP_FILE%" (
    for %%F in ("%BACKUP_FILE%") do set "SIZE=%%~zF"
    echo [OK] Backup created: %BACKUP_FILE% (%SIZE% bytes)
) else (
    echo [ERROR] Backup failed
    exit /b 1
)

:: Cleanup: keep only last 20 backups
set "COUNT=0"
for /f %%F in ('dir /b /o-d "backups\granite_*.db" 2^>nul') do (
    set /a "COUNT+=1"
    if !COUNT! gtr 20 (
        echo [CLEANUP] Removing old backup: backups\%%F
        del "backups\%%F" >nul 2>&1
    )
)

echo [INFO] Total backups kept: 20 max
echo Done.
