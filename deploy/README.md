# Deploying Front Desk to AWS

One **EC2** instance (`t3.micro`, Amazon Linux 2023) runs the Front Desk server behind **Caddy**, which handles HTTPS with a Let's Encrypt certificate on a `<ip>.sslip.io` domain. The instance has an **IAM role** that can only call the **Amazon Nova** models on Bedrock, so there are no access keys anywhere. Everything is created by a **CloudFormation** template (`front-desk.yaml`).

```
Browser ──HTTPS──> Caddy (443) ──> Front Desk (127.0.0.1:8000)
                                     ├── /mcp        MCP server (Streamable HTTP)
                                     ├── /demo       simulator + dashboard
                                     └── agent ──> Amazon Bedrock (Amazon Nova, instance role)
```

Why EC2: App Runner is not available on the Free plan of the new AWS experience; the server keeps the schedule in SQLite and needs **a single instance** so the agent, the MCP server and the dashboard see the same data. EC2 solves that simply and cheaply.

## Publish
Prerequisites: the code is on `main` on GitHub (the instance clones the repository) and `aws login --region us-east-2 --profile juan` is done.

On Windows, double-click `deploy\publish.cmd`, or run:

```powershell
aws cloudformation deploy --template-file deploy/front-desk.yaml --stack-name front-desk --capabilities CAPABILITY_IAM --region us-east-2 --profile juan
aws cloudformation describe-stacks --stack-name front-desk --region us-east-2 --profile juan --query "Stacks[0].Outputs" --output table
```

The output shows `DemoUrl`, `McpUrl` and `DashboardUrl`. HTTPS is ready 3 to 5 minutes after the stack finishes (the instance installs everything and requests the certificate).

Optional parameters (add `--parameter-overrides Name=value`):
- `AgentMode`: `auto` (default: Bedrock, and if it fails, rehearsal mode with a notice), `bedrock` or `rehearsal`.
- `Branch`: GitHub branch to deploy (default `main`).
- `InstanceType`: `t3.micro` (default) or `t3.small`.

## Update after a merge
Double-click `deploy\update.cmd`. It uses **Systems Manager Run Command** (the instance role already allows it) to pull the latest `main`, install dependencies and restart the service, **keeping the same IP and address**. The result is saved to `deploy\update-result.txt`. The schedule database is recreated with the demo data. If the `aws login` session has expired, use `deploy\login-and-update.cmd`, which signs in first (confirm the session in the browser tab it opens) and then runs the update.

To start from scratch instead (new IP and address):

```powershell
aws cloudformation delete-stack --stack-name front-desk --region us-east-2 --profile juan
aws cloudformation wait stack-delete-complete --stack-name front-desk --region us-east-2 --profile juan
deploy\publish.cmd
```

## Test the published MCP endpoint
```bash
npx @modelcontextprotocol/inspector --cli https://<ip>.sslip.io/mcp --transport http --method tools/list
```

## Troubleshooting
- **The page doesn't open right after deploying:** wait 5 minutes. To see the install log, open the instance in the EC2 console > Connect > Session Manager and run `sudo tail -n 80 /var/log/cloud-init-output.log`, `systemctl status front-desk caddy` and `journalctl -u front-desk -n 50`.
- **The simulator shows "rehearsal mode" with a quota notice:** the account has no daily Bedrock quota for Nova (see `FRICTION-LOG.md`). The demo keeps working in rehearsal mode until the quota is granted.

## Costs and cleanup
- `t3.micro` + public IP + 8 GB disk: about US$12 per month, covered by the Free plan credits. Nova Lite calls cost a fraction of a cent per conversation.
- The server limits utterances per IP and per day (`FRONTDESK_RATE_PER_IP`, `FRONTDESK_DAILY_LIMIT`) to protect the credits.
- Keep it running until judging ends (November 20, 2026). Then delete everything with `aws cloudformation delete-stack --stack-name front-desk --region us-east-2 --profile juan`.
