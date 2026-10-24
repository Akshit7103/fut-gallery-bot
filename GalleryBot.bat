@echo off
title FUT Gallery Tokens Bot
cd /d "%~dp0"
echo ============================================================
echo   FUT Gallery Tokens Bot
echo   Close FC 27 and the companion app first (one EA session).
echo ============================================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get Python 3.10 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup. Then run this file again.
  pause
  exit /b 1
)
python -c "import playwright" >nul 2>nul
if errorlevel 1 (
  echo First run: installing the one library the bot needs ^(Playwright^)...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Install failed - see the message above.
    pause
    exit /b 1
  )
)
if not exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" if not exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" if not exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (
  echo Google Chrome is required ^(https://www.google.com/chrome/^). Install it and run this file again.
  pause
  exit /b 1
)
python run.py %*
echo.
pause
