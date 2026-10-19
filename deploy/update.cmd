@echo off
rem Updates the running instance to the latest main branch, keeping the same IP and address.
rem Uses AWS Systems Manager (Run Command); the result is saved to deploy\update-result.txt.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
pause
