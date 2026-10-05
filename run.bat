@echo off
title Onion Route Planner
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Python is not installed or not on PATH.
  echo  Install it from https://www.python.org/downloads/  and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

if not exist ".env" (
  echo.
  echo  No .env file found - copying .env.example to .env
  echo  Open .env in Notepad, set APP_PASSWORD and (optionally) DATABASE_URL, then run this again.
  copy ".env.example" ".env" >nul
  notepad ".env"
)

echo  Installing / checking Python packages (first time takes a few minutes)...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo  Package installation failed - check the internet connection and try again.
  pause
  exit /b 1
)

echo.
echo  Starting the planner... keep this window open while people use it.
echo  This PC:        http://localhost:5000
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo  Other devices:  http://%%a:5000  (same Wi-Fi)
echo.
start "" http://localhost:5000
python app.py
pause
