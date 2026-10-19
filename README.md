# Front Desk

A voice receptionist for small businesses, built as an MCP server for the **Alexa+** track of *Build, Ship, Shape: Amazon Developer Hackathon*.

A customer says "I need a haircut Friday morning", the server checks the schedule, books the slot, and the owner's dashboard updates on the spot.

**Live demo:** https://18-190-98-99.sslip.io/demo (Chrome or Edge) · **MCP endpoint:** `https://18-190-98-99.sslip.io/mcp` (Streamable HTTP)

## MCP tools
| Tool | What it does | Example request |
|---|---|---|
| `list_services` | Services, duration and price | "How much is a haircut?" |
| `check_availability` | Open slots by day, period and barber | "Anything open Friday morning?" |
| `book_appointment` | Books the slot, checking opening hours and conflicts | "Book a haircut Friday at 9 for John" |
| `cancel_appointment` | Cancels the customer's next appointment | "Cancel my appointment" |
| `reschedule_appointment` | Moves it to another day or time, keeping the same barber when possible | "Can you move it to 4 PM?" |
| `daily_summary` | Owner's summary: customers, expected revenue, next appointment | "How's my day looking?" |

Every result has a `speech` field with the short sentence the voice assistant should say, plus structured data (`structuredContent` with an `outputSchema`). Tools are annotated (`readOnlyHint`, `destructiveHint`, `title`) so Alexa+ knows which ones change data.
The server understands dates like "today", "tomorrow", "Friday", "next Monday", "October 23" and "10/23", and times like "9", "9 AM", "9:30 p.m.", "noon" and "half past 9".

There is also `GET /api/schedule?date=YYYY-MM-DD`, with one day of appointments as JSON, and `GET /api/week`, with the next 7 days for the owner's dashboard.

## Voice simulator and owner dashboard
The Alexa+ developer tools are in closed preview, so the demo uses a simulated Alexa+ experience in the browser (accepted by the hackathon rules):

- `/simulator`: a voice assistant screen. The customer speaks (Web Speech API, en-US) or types; each utterance goes to `POST /api/conversation`.
  - **Hands-free:** tap the mic and speak; the turn ends after a short pause, not at the first breath. If the browser stops the recognizer on its own, it restarts and keeps what was already heard.
  - **Push-to-talk:** hold <kbd>Space</kbd> while talking and release to send.
  - **Follow-up mode:** when the assistant asks a question, it listens again on its own, like Alexa.
  - A level meter shows the mic is really hearing you, and the assistant never listens while it is speaking.
- `POST /api/conversation`: the agent on **Amazon Bedrock** (Amazon Nova, Converse API with tool use) reads the MCP server's tools (`tools/list`), calls the ones it needs (`tools/call`) and returns the spoken reply plus the list of calls with arguments and latency.
- `/dashboard`: the week's schedule by barber, refreshing every 1.5 s; new bookings, reschedules and cancellations flash on screen.
- `/demo`: simulator and dashboard side by side, the framing used in the video.

The agent uses the default AWS credentials. If you sign in with `aws login`, botocore needs its `crt` extra (already in `requirements.txt` as `boto3[crt]`). Settings:
- `AWS_PROFILE` and `AWS_REGION` (default `us-east-2`).
- `FRONTDESK_MODEL`: Bedrock models or inference profiles, comma-separated, in order of preference (default: Nova Lite, Nova Micro and Nova 2 Lite). If one has no quota or no access, the agent moves on to the next.
- `FRONTDESK_AGENT`: `bedrock` (default), `rehearsal` (a simple rule-based agent without the model, to test the demo; it uses the same MCP tools and the screen says "rehearsal mode") or `auto` (Bedrock, and if it fails, rehearsal mode with a notice on screen; this is what the deployment uses).
- `FRONTDESK_HOSTS`: public domains the MCP server accepts when it runs behind a proxy (e.g. `front-desk.example.com`). Without it, the SDK only accepts `localhost` and answers 421.
- `FRONTDESK_RATE_PER_IP` and `FRONTDESK_DAILY_LIMIT`: utterances per IP every 10 minutes (default 40) and per day (default 600), to protect the AWS credits.
- `FRONTDESK_MCP_URL`: the MCP server the agent uses (default: this same server).

## How it works
```
Browser (simulator) ──POST /api/conversation──> Agent (Amazon Nova on Bedrock, Converse + tool use)
                                                   │  tools/list, tools/call (Streamable HTTP)
                                                   ▼
Alexa+ / MCP Inspector ──────────POST /mcp──────> MCP server ──> SQLite schedule <── /dashboard (live)
```
The agent talks to the MCP server over HTTP exactly like Alexa+ would, so the demo shows the real MCP round trip.

## Status
- [x] MCP server at `POST /mcp` (Streamable HTTP, MCP spec 2025-11-25)
- [x] SQLite schedule with a fictional barbershop
- [x] Six booking tools, with tests
- [x] Voice simulator with the agent on Amazon Bedrock
- [x] Live owner dashboard
- [x] AWS deployment (EC2 + HTTPS + IAM role for Nova), published

## Run locally
Requires Python 3.10 or later.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m frontdesk.server
```

The server starts at `http://127.0.0.1:8000/mcp` with a health check at `http://127.0.0.1:8000/health`.
Open `http://127.0.0.1:8000/demo` in Chrome or Edge to see the simulator and the dashboard (for the Bedrock agent, run `aws login` first and set `AWS_PROFILE`; to try it without AWS, set `FRONTDESK_AGENT=rehearsal`).
On Windows, `run-demo.cmd` (Bedrock) and `run-rehearsal.cmd` (no Bedrock) do all of this with a double click.

Optional settings:
- `FRONTDESK_DB`: path of the SQLite database (default: `frontdesk.db` in the repository root).
- `FRONTDESK_DEMO=0`: don't create the sample appointments.
- `FRONTDESK_NOW=2026-10-23T08:00`: pins the clock, handy for recording the demo.
- `FRONTDESK_TZ`: the business time zone (default `America/Los_Angeles`).

## Deploy to AWS
One EC2 instance with HTTPS (Caddy) and an IAM role limited to Amazon Nova, created by CloudFormation. Steps, costs and cleanup in [`deploy/README.md`](deploy/README.md).

## Test
```bash
python -m pytest -q
```

To inspect it with the MCP Inspector, connect to `http://127.0.0.1:8000/mcp` using the Streamable HTTP transport.

## License
MIT
