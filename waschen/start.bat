@echo off
cd /d "%~dp0"
python waschbot.py %*
:loop
if %errorlevel%==0 goto done
echo.
echo The bot crashed - restarting in 10 seconds (close this window to cancel)...
timeout /t 10 >nul
python waschbot.py
goto loop
:done
pause
