@echo off
rem Front Desk em modo ensaio: sem Bedrock, para testar a demo enquanto a cota do Nova nao sai.
cd /d "%~dp0"
title Front Desk - modo ensaio
set FRONTDESK_AGENTE=ensaio
echo Instalando dependencias...
python -m pip install -q -r requirements.txt
echo Abrindo a demo no Edge em alguns segundos...
start "" cmd /c "timeout /t 4 >nul & start msedge http://127.0.0.1:8000/demo"
python -m frontdesk.server
pause
