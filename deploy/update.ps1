# Updates the Front Desk instance to the latest main branch without recreating it (same IP and address).
# Runs the commands in update-commands.json on the instance through AWS Systems Manager Run Command.
$ErrorActionPreference = "Stop"
$region = "us-east-2"
$awsProfile = "juan"
$result = Join-Path $PSScriptRoot "update-result.txt"
Start-Transcript -Path $result -Force | Out-Null
try {
    $instance = aws cloudformation describe-stacks --stack-name front-desk --region $region --profile $awsProfile `
        --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text
    if ($LASTEXITCODE -ne 0) {
        throw "The AWS CLI failed (exit code $LASTEXITCODE). If the sign-in expired, run login-and-update.cmd instead: it runs aws login first."
    }
    if (-not $instance -or $instance -eq "None") { throw "Could not find the front-desk stack instance." }
    Write-Host "Instance: $instance"

    $commands = Join-Path $PSScriptRoot "update-commands.json"
    $commandId = aws ssm send-command --region $region --profile $awsProfile --instance-ids $instance `
        --document-name AWS-RunShellScript --comment "Front Desk: update to main" `
        --parameters "file://$commands" --query Command.CommandId --output text
    Write-Host "Command sent: $commandId. Waiting for it to finish..."

    $status = "Pending"
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        $status = aws ssm get-command-invocation --region $region --profile $awsProfile `
            --command-id $commandId --instance-id $instance --query Status --output text 2>$null
        if ($status -in @("Success", "Failed", "Cancelled", "TimedOut")) { break }
    }
    Write-Host "Status: $status"
    Write-Host "----- output -----"
    aws ssm get-command-invocation --region $region --profile $awsProfile --command-id $commandId `
        --instance-id $instance --query StandardOutputContent --output text
    Write-Host "----- errors -----"
    aws ssm get-command-invocation --region $region --profile $awsProfile --command-id $commandId `
        --instance-id $instance --query StandardErrorContent --output text
    if ($status -ne "Success") { throw "The update did not finish successfully ($status)." }
    Write-Host ""
    Write-Host "Done. Open the demo again (Ctrl+F5) to load the new version."
}
catch {
    Write-Host "ERROR: $_"
}
finally {
    Stop-Transcript | Out-Null
}
