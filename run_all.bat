@echo off
title Granite CRM DB - ALL REGIONS

echo ============================================
echo  Granite CRM DB
echo  Mode: ALL (resume from checkpoints)
echo  Date: %date% %time%
echo ============================================
echo.
echo  Logic:
echo    no data     - full cycle
echo    scraped     - continue from last phase
echo    enriched    - skip (export only)
echo.

python cli.py run all

echo.
echo ============================================
echo  All regions processed.
echo  Date: %date% %time%
echo ============================================
pause
