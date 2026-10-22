@echo off
rem Renews the AWS CLI sign-in (profile juan) and then updates the server.
cd /d "%~dp0"
echo Signing in to AWS. A browser tab opens: confirm the session there.
aws login --profile juan --region us-east-2
if errorlevel 1 (
  echo The AWS sign-in did not finish.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
pause
