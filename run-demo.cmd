@echo off
rem Front Desk with the agent on Amazon Bedrock (Amazon Nova), profile juan in us-east-2.
cd /d "%~dp0"
title Front Desk - Bedrock
set FRONTDESK_AGENT=
set AWS_PROFILE=juan
set AWS_REGION=us-east-2
echo Installing dependencies...
python -m pip install -q -r requirements.txt
echo If you get a credentials error, run: aws login --region us-east-2 --profile juan
start "" cmd /c "timeout /t 4 >nul & start msedge http://127.0.0.1:8000/demo"
python -m frontdesk.server
pause
