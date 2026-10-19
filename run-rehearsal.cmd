@echo off
rem Front Desk in rehearsal mode: no Bedrock, to test the demo while the Nova quota is not available.
cd /d "%~dp0"
title Front Desk - rehearsal mode
set FRONTDESK_AGENT=rehearsal
echo Installing dependencies...
python -m pip install -q -r requirements.txt
echo Opening the demo in Edge in a few seconds...
start "" cmd /c "timeout /t 4 >nul & start msedge http://127.0.0.1:8000/demo"
python -m frontdesk.server
pause
