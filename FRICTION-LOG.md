# Friction log

## MCP Python SDK 2.x renamed FastMCP, and the tutorials don't say so
- **Task:** create the MCP server skeleton in Python.
- **Steps:** `pip install mcp` and `from mcp.server.fastmcp import FastMCP`, as most tutorials show.
- **Expected:** FastMCP imports.
- **What happened:** `ModuleNotFoundError`. In 2.x the class became `mcp.server.MCPServer`, and options like `stateless_http` and `json_response` moved from the constructor to `streamable_http_app()`.
- **Severity:** medium
- **Workaround:** read the installed package's code and migrate to `MCPServer`.
- **Suggestion:** the hackathon resources could pin an SDK version and ship a minimal, up-to-date Streamable HTTP server example.

## Returning `dict` does not produce structuredContent
- **Task:** return the tool result in structured form.
- **Expected:** `structuredContent` filled in when the tool returns a `dict`.
- **What happened:** it came back as text only; `structuredContent` was empty.
- **Severity:** low
- **Workaround:** annotate the return type as `dict[str, Any]` instead of `dict`. With that, the SDK generates an `outputSchema` and fills `structuredContent`; with `structured_output=True` and a bare `-> dict` it rejects the function ("return type dict is not serializable for structured output").
- **Suggestion:** document the difference between `dict` and `dict[str, Any]` as a tool return type, or treat both the same way.

## MCP server answers 421 "Invalid Host header" behind a proxy
- **Task:** get the server ready to run behind Caddy (HTTPS) on a public domain.
- **Steps:** run the server on `127.0.0.1:8000` with `streamable_http_app()` and send an `initialize` with `Host: <public-domain>`.
- **Expected:** `200`, as with `localhost`.
- **What happened:** `421 Invalid Host header`. The SDK turns on DNS rebinding protection by itself when the host is `127.0.0.1` and only accepts `localhost`; the deployment docs don't mention it.
- **Severity:** medium (the published endpoint would not answer Alexa+ or the MCP Inspector)
- **Workaround:** pass `TransportSecuritySettings` with the public domain in `allowed_hosts` (the `FRONTDESK_HOSTS` variable), keeping the protection on. Covered by a test.
- **Suggestion:** mention the Host protection in the SDK deployment guide and on the Alexa+ endpoint requirements page, with an example behind a proxy.

## `aws login` didn't open the browser and timed out silently
- **Task:** authenticate the AWS CLI on Windows 11 with `aws login` (Agent Toolkit for AWS, step 3).
- **Steps:** run `aws login --region us-east-2 --profile juan` in PowerShell.
- **Expected:** the sign-in page opens in the default browser.
- **What happened:** the CLI said it would try to open the browser, but no tab opened. After 10 minutes it exited with code 255, with no message saying the time had run out.
- **Severity:** medium
- **Workaround:** copy the link printed by the CLI and open it manually; the "Choose AWS sessions" page reused the console session.
- **Suggestion:** warn when the browser doesn't open, show a countdown, and explain code 255 as a timeout.

## `aws agent-toolkit list-available-skills` breaks with an encoding error on Windows
- **Task:** verify the Agent Toolkit (setup step 6).
- **Steps:** capture the command output in PowerShell 5.1 (`$x = aws agent-toolkit list-available-skills ...`).
- **Expected:** JSON with the skills catalog.
- **What happened:** `aws: [ERROR]: 'charmap' codec can't encode character '→'`, exit code 255. The setup itself had worked.
- **Severity:** medium (looks like a failed install, but isn't)
- **Workaround:** set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` before the command; it returned 114 skills.
- **Suggestion:** force UTF-8 output in the CLI, or replace non-ASCII characters (like the arrow) in the descriptions.

## `aws login` credentials don't work in boto3 without the `crt` extra
- **Task:** call Amazon Nova through the Converse API (boto3) from the Front Desk server, using the profile created with `aws login` in the new AWS experience.
- **Steps:** `pip install boto3` (1.40+), `aws login --region us-east-2 --profile juan`, `$env:AWS_PROFILE = "juan"`, call `bedrock-runtime.converse`.
- **Expected:** boto3 uses the same credentials the AWS CLI already uses.
- **What happened:** `MissingDependencyException: Missing Dependency: Using the login credential provider requires an additional dependency. You will need to pip install "botocore[crt]" before proceeding.` The CLI works but the SDK doesn't, and nothing in the `aws login` flow or the Agent Toolkit says the Python SDK needs the extra. In our server the error also came wrapped in an `ExceptionGroup` from the MCP client, which hid the cause on the first attempt.
- **Severity:** medium (blocks the first Bedrock call from code; the final message is clear)
- **Workaround:** use `boto3[crt]` instead of `boto3` in `requirements.txt`.
- **Suggestion:** install `awscrt` as a default botocore dependency when the profile uses `login_session`, or have `aws login` say when it finishes: "to use these credentials in Python SDKs, install botocore[crt]". Worth adding to the new-experience guide and the Agent Toolkit skills.

## New account gets "Too many tokens per day" on the very first Amazon Nova call
- **Task:** first call to Amazon Nova Lite through the Converse API (boto3), Region us-east-2, inference profile `us.amazon.nova-lite-v1:0`, account created through the new AWS experience (Free plan, US$100 in credits).
- **Steps:** `aws login --region us-east-2 --profile juan`; start the server with `AWS_PROFILE=juan`; send a single short sentence through the simulator.
- **Expected:** a model response; the account's total usage so far was zero.
- **What happened:** `ThrottlingException: Too many tokens per day, please wait before trying again` on the very first call, with 3 automatic SDK retries, all the same. No message says which quota it is, what the applied value is, or how to request more. Confirmed with `aws service-quotas list-service-quotas --service-code bedrock --region us-east-2`: every "tokens per day" quota for the Nova models (Nova Lite, Micro, Pro, Premier, Nova 2 Lite, Nova 2 Omni, Nova 2 Pro Preview and the global profiles) shows an applied value of **0.0** on the account. Worse: in Service Quotas these quotas are marked **not adjustable**, with an AWS default of 5.76 billion tokens per day (11.52 billion for the Nova 2 Lite global profile) and an applied value of 0, so you can't even request an increase there (`request-service-quota-increase`). re:Post threads describe the same thing on new accounts, and the answer there is to open a support case.
- **Severity:** blocking for the AWS Builder mini challenge (the agent on Bedrock can't run)
- **Workaround:** the agent now tries other Nova models in sequence when one has no quota (Nova Lite, Nova Micro, Nova 2 Lite) and gained a "rehearsal mode" without Bedrock to test the rest of the demo; a case was opened with AWS Support (case 179236335500169, October 18) asking for the quota. The EC2 deployment confirmed the IAM role reaches Bedrock: the error is about quota, not permissions.
- **Suggestion:** new accounts, especially Free plan accounts with credits, should come with a minimum usable daily quota for the Nova models; the error message should name the Service Quotas quota and link to the increase request; and the Bedrock Playground/console should show "your daily quota is 0" before the developer writes any code.
- **Update (October 20):** the quota is now live. The deployed server answered with `us.amazon.nova-lite-v1:0` and ran a full check-availability → book → cancel conversation through the MCP tools (1.2–2.0 s per turn). It took about two days from the first call, with no notification on the case at the time of writing; for a hackathon that is a third of the build window.

## Nova Lite speaks its `<thinking>` reasoning as part of the answer
- **Task:** voice receptionist answering through the Converse API with tool use (`us.amazon.nova-lite-v1:0`, us-east-2); the final text is read aloud by the browser.
- **Steps:** ask "what services do you have?"; Nova calls the tool and then answers.
- **Expected:** only the sentence meant for the customer in the final text block.
- **What happened:** the final text starts with `<thinking> The customer wants... I will tell them. </thinking>` followed by the real answer, in the same `text` block. Nothing in the Converse response separates reasoning from the answer (no `reasoningContent` block), so a voice app reads the reasoning out loud. It showed up on every turn of our first real conversation.
- **Severity:** high for voice apps (the demo would speak the model's inner monologue)
- **Workaround:** strip `<thinking>...</thinking>` (including an unclosed tag) from the final text before speaking it; covered by tests.
- **Suggestion:** return Nova's reasoning as a separate `reasoningContent` block in the Converse API, or add an inference option to turn it off, and document the behavior in the Nova tool-use guide with a voice example.
