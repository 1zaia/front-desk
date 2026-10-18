@echo off
rem Publica o Front Desk na AWS (EC2 + HTTPS) com CloudFormation, perfil juan em us-east-2.
rem Antes: o codigo precisa estar na main do GitHub (a instancia clona o repositorio).
cd /d "%~dp0"
echo Criando ou atualizando a pilha front-desk (leva uns 3 minutos)...
aws cloudformation deploy --template-file front-desk.yaml --stack-name front-desk --capabilities CAPABILITY_IAM --region us-east-2 --profile juan %*
if errorlevel 1 (
  echo.
  echo A publicacao falhou. Veja os eventos com:
  echo aws cloudformation describe-stack-events --stack-name front-desk --region us-east-2 --profile juan --max-items 15
  pause
  exit /b 1
)
echo.
echo Enderecos (o HTTPS fica pronto 3 a 5 minutos depois que a instancia sobe):
aws cloudformation describe-stacks --stack-name front-desk --region us-east-2 --profile juan --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
pause
