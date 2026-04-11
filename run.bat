@echo off
chcp 65001 >nul
title Granite Workshops DB

set CITY=Севастополь

:: set FORCE=--force
:: set RE_ENRICH=--re-enrich

echo ============================================
echo  Granite Workshops DB
echo  Город: %CITY%
echo  Дата:  %date% %time%
echo ============================================
echo.

python cli.py run %CITY% %FORCE% %RE_ENRICH%

echo.
echo ============================================
echo  Готово. Логи: data\logs\granite.log
echo ============================================
pause
