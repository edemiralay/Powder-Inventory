@echo off
cd /d "%~dp0"
echo Starting the powder inventory app. Leave this window open while using the app.
python powder_inventory_app.py %*
pause
