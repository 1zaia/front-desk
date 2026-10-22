# Front Desk

A voice receptionist for any business that runs on appointments (barbershops, salons, clinics, studios, groomers, tutors), built as an MCP server for the **Alexa+** track of *Build, Ship, Shape: Amazon Developer Hackathon*.

A customer calls and says "Anything for a haircut this morning?". Front Desk checks the real schedule, asks for a name and phone number in one go, books the slot, and the appointment shows up on the owner's calendar on the spot. When someone cancels, the slot goes straight to the next person on the waitlist. And the owner can run everything by hand from the Front Desk app.

**Live:** https://18-190-98-99.sslip.io (Chrome or Edge for voice) · **Demo call:** `/demo` · **MCP endpoint per business:** `https://18-190-98-99.sslip.io/b/<business>/mcp`

**Demo video (2:56):** VIDEO_LINK

## What's in it
- **Many businesses, one server.** Owners sign up, pick a type of business and get a working setup: services, team, opening hours and the right words ("stylists" and "clients" for a salon, "groomers" for a pet shop, "patients" for a clinic). Each business keeps its schedule in its own database, and one account can run several businesses.
- **Owner accounts.** Email and password (scrypt), HttpOnly session cookies, rate-limited sign-in. A shared demo account opens the sample barbershop.
- **Voice receptionist.** An agent on **Amazon Bedrock** (Amazon Nova, Converse API with tool use) calls the business's MCP tools. Every booking needs a **name and a phone number**, asked for together. Caller ID recognizes returning customers ("Welcome back, Sam! Want the usual?"), and "first available" books the earliest open slot.
- **The owner app** (`/app`), to do by hand everything the receptionist does:
  - **Today**: the day's agenda, revenue, how many bookings came from the voice assistant, waitlist.
  - **Calendar**: a day view by team member. Click an empty spot to book, click an appointment to move it, change service or person, mark it completed or no-show, cancel it or add notes. Block time off.
  - **Customers**: everyone saved by phone number, with visits, no-shows, spend, notes and full history.
  - **Waitlist**: who's waiting, add people by hand, and the slots it filled.
  - **Services, team and opening hours**: add, edit and archive. Archived items stay in past appointments.
  - **Phone line**: the business's public phone page, its MCP address and the owner API key.
  - **Settings**: name, type of business and time zone.
- **Live board** (`/b/<business>/board`): the week by team member for a screen at the front desk. New bookings, cancellations and waitlist fills flash as they happen.

## MCP tools
Each business has its own endpoint: `POST /b/<business>/mcp` (Streamable HTTP, spec 2025-11-25). `POST /mcp` is the public demo barbershop.

| Tool | What it does | Example request |
|---|---|---|
| `list_services` | Services, duration and price, and who works there | "How much is a haircut?" |
| `check_availability` | Open slots by day, period and staff member | "Anything open Friday morning?" |
| `book_appointment` | Books the slot after checking hours and conflicts; needs **name and phone**; takes "first available" and "the usual" | "Book a haircut Friday at 9 for John" |
| `cancel_appointment` | Cancels the customer's next appointment, found by phone (or name); the slot goes to the waitlist | "Cancel my appointment" |
| `reschedule_appointment` | Moves it, keeping the same person when possible | "Can you move it to 4 PM?" |
| `join_waitlist` | Waitlist for a day, a period or an exact time; **booked automatically when a slot opens** | "Put me on the waitlist for 10:30" |
| `customer_history` | Recognizes a returning customer by phone: visits, usual service and person, upcoming appointments | caller ID → "Welcome back, Sam! Want the usual?" |
| `daily_summary` | **Owner only:** customers, expected revenue, next appointment, waitlist and blocked time | "How's my day looking?" |
| `block_time` | **Owner only:** takes time off someone's schedule and flags appointments already in it | "Block Diego's afternoon, he has the dentist" |

Customer tools are open, like a phone line. Owner tools need the business's API key (`Authorization: Bearer <key>`, created under **Phone line** in the app); on the public demo they're open.
Every result has a `speech` field with the short sentence to say, plus structured data (`structuredContent` with an `outputSchema`). Tools are annotated (`readOnlyHint`, `destructiveHint`, `title`).
Dates like "today", "tomorrow", "Friday", "next Monday", "October 23", "10/23"; times like "9", "9 AM", "9:30 p.m.", "noon", "half past 9"; phone numbers in any common format, typed or spoken.

## How it works
```
Customer (browser phone line /b/<business>) ──> Agent: Amazon Nova on Bedrock (Converse + tool use)
                                                   │ tools/list, tools/call
Alexa+ / MCP client ──POST /b/<business>/mcp──> MCP server (one tenant per request) ──> that business's SQLite
Owner ──/app (session cookie)──> owner API /api/b/<business>/... ──────────────────────────┘     └──> live board
```
The platform database (`platform.db`) holds accounts, businesses, sessions and API key hashes. The business being served is a context variable set per request, so the same tools serve every business without mixing their data.

## Pages
| Page | What it is |
|---|---|
| `/` | Product page |
| `/signup`, `/login` | Create a business, sign in, or open the demo |
| `/app` | The owner app |
| `/b/<business>` | The business's phone line (voice simulator) with caller ID |
| `/b/<business>/board` | Live board (owner only, except the demo) |
| `/demo` | Demo phone line and live board side by side, the framing used in the video |

The phone line simulates Alexa+ in the browser (the Alexa+ developer tools are in closed preview, which the hackathon rules allow): Web Speech API in en-US, hands-free with pause detection, push-to-talk with <kbd>Space</kbd>, follow-up listening, and a "Behind the voice" list of every MCP call with its latency.

## Run locally
Requires Python 3.10 or later.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m frontdesk.server
```

Open `http://127.0.0.1:8000` in Chrome or Edge. For the Bedrock agent, run `aws login` first and set `AWS_PROFILE`; to try it without AWS, set `FRONTDESK_AGENT=rehearsal`. On Windows, `run-demo.cmd` (Bedrock) and `run-rehearsal.cmd` (no Bedrock) do all of this with a double click.

Settings:
- `AWS_PROFILE` and `AWS_REGION` (default `us-east-2`). With `aws login`, botocore needs its `crt` extra (already in `requirements.txt`).
- `FRONTDESK_MODEL`: Bedrock models or inference profiles, in order of preference (default: Nova Pro, then Nova Lite, Nova Micro and Nova 2 Lite). If one has no quota or no access, the agent moves on to the next.
- `FRONTDESK_AGENT`: `bedrock` (default), `rehearsal` (rule-based, no model, same MCP tools; the screen says "rehearsal mode") or `auto` (Bedrock, falling back to rehearsal mode with a notice; used by the deployment).
- `FRONTDESK_DB`: the demo business's database (default `frontdesk.db`). `platform.db` and the `businesses/` folder live next to it.
- `FRONTDESK_DEMO=0`: no sample appointments in the demo business.
- `FRONTDESK_NOW=2026-10-23T08:40`: pins the clock (for recording the demo). `FRONTDESK_TZ` overrides every business's time zone.
- `FRONTDESK_HOSTS`: public domains accepted behind a proxy (otherwise the SDK only accepts `localhost` and answers 421).
- `FRONTDESK_RATE_PER_IP` and `FRONTDESK_DAILY_LIMIT`: utterances per IP every 10 minutes (default 40) and per day (default 600), to protect the AWS credits.
- `FRONTDESK_MCP_URL`: run the agent against another MCP server (default: the in-process server of the business being served).

## Deploy to AWS
One EC2 instance with HTTPS (Caddy) and an IAM role limited to Amazon Nova, created by CloudFormation. Steps, costs and cleanup in [`deploy/README.md`](deploy/README.md). `deploy/update.cmd` updates it in place; the demo business is reset on each update, owner accounts and their businesses are kept.

## Test
```bash
python -m pytest -q
python scripts/check_video_script.py rehearsal   # the demo video script, line by line (or "bedrock" for Amazon Nova)
```

The demo video is recorded and edited from code against the deployed app with the real Amazon Nova agent; see [`scripts/video/README.md`](scripts/video/README.md).

To inspect the MCP server with the MCP Inspector, connect to `http://127.0.0.1:8000/b/main-street-barbers/mcp` (Streamable HTTP).

## License
MIT
