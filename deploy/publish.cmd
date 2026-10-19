@echo off
rem Creates the Front Desk stack on AWS (EC2 + HTTPS) with CloudFormation, profile juan in us-east-2.
rem Before: the code must be on the main branch on GitHub (the instance clones the repository).
rem To update a stack that is already running, use update.cmd instead (keeps the same address).
cd /d "%~dp0"
echo Creating or updating the front-desk stack (takes about 3 minutes)...
aws cloudformation deploy --template-file front-desk.yaml --stack-name front-desk --capabilities CAPABILITY_IAM --region us-east-2 --profile juan %*
if errorlevel 1 (
  echo.
  echo Publishing failed. See the events with:
  echo aws cloudformation describe-stack-events --stack-name front-desk --region us-east-2 --profile juan --max-items 15
  pause
  exit /b 1
)
echo.
echo Addresses (HTTPS is ready 3 to 5 minutes after the instance starts):
aws cloudformation describe-stacks --stack-name front-desk --region us-east-2 --profile juan --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
pause
