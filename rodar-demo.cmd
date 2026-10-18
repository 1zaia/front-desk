@echo off
rem Front Desk com o agente no Amazon Bedrock (Amazon Nova), perfil juan em us-east-2.
cd /d "%~dp0"
title Front Desk - Bedrock
set FRONTDESK_AGENTE=
set AWS_PROFILE=juan
set AWS_REGION=us-east-2
echo Instalando dependencias...
python -m pip install -q -r requirements.txt
echo Se der erro de credencial, rode: aws login --region us-east-2 --profile juan
start "" cmd /c "timeout /t 4 >nul & start msedge http://127.0.0.1:8000/demo"
python -m frontdesk.server
pause
