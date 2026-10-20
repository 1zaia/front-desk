"""Runs every line of the demo video script against the agent and prints what Front Desk answers.

Usage:
    python scripts/check_video_script.py            # agent on Amazon Bedrock (Amazon Nova)
    python scripts/check_video_script.py rehearsal  # rehearsal mode, no Bedrock

Uses an in-process MCP server with a throwaway database and the clock pinned to the
recording time (Friday, October 23, 2026, 8:40 AM), so it never touches a running server.
Exits with code 1 if a scene doesn't call the tools it should.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("FRONTDESK_NOW", "2026-10-23T08:40")
os.environ["FRONTDESK_DB"] = str(Path(tempfile.mkdtemp()) / "video.db")
os.environ["FRONTDESK_DEMO"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import Client  # noqa: E402

from frontdesk import agent, rehearsal, schedule, tenancy  # noqa: E402
from frontdesk.server import _conn, mcp  # noqa: E402

# (scene, caller ID, [(line, tools that must be called, what the script expects Front Desk to say)])
SCENES = [
    ("3. A new customer books", "+13105550199", [
        ("Hi! Do you have anything for a haircut this morning?", {"check_availability"},
         "For a haircut this morning, I have 9 AM, 9:30 AM, or 10 AM. Which works best?"),
        ("9:30 works. My name is Jordan.", {"book_appointment"},
         "You're all set, Jordan! A haircut today at 9:30 AM with Marcus."),
    ]),
    ("4. A regular comes back", "+13105550142", [
        ("Hey, it's Sam.", {"customer_history"},
         "Welcome back, Sam! Last time was a beard trim with Diego on October 13. Want the usual?"),
        ("Yes, today at 11.", {"book_appointment"},
         "You're all set, Sam! Your usual beard trim today at 11 AM with Diego."),
    ]),
    ("5. Full? Join the waitlist", "+13105550188", [
        ("Can I get a haircut today at 10:30? I'm Nick.", set(),
         "Sorry, 10:30 AM is already taken. I can check other times, or put you on the waitlist."),
        ("Waitlist, please.", {"join_waitlist"},
         "Got it, Nick. You're on the waitlist for a haircut today at 10:30 AM. If a spot opens up, I'll book it for you automatically."),
    ]),
    ("5b. Leo cancels", "+13105550177", [
        ("Hi, this is Leo. I need to cancel my appointment.", {"cancel_appointment"},
         "Done. I cancelled Leo's haircut today at 10:30 AM with Marcus."),
    ]),
    ("6. The owner", "", [
        ("Block Diego's afternoon, he has a dentist appointment.", {"block_time"},
         "Done. Diego is blocked today from noon to 5 PM. Heads up: Kevin at 3:30 PM is still booked in that time."),
        ("How's my day looking?", {"daily_summary"},
         "Today you have 6 customers, with $210 expected. The waitlist already filled 1 cancelled slot. Diego is blocked from noon to 5 PM."),
    ]),
]


async def setup() -> None:
    """Off camera before scene 5: Leo and Max take both 10:30 AM haircuts."""
    async with Client(mcp) as c:
        for name, phone in (("Leo", "+13105550177"), ("Max", "+13105550178")):
            await c.call_tool("book_appointment", {"customer_name": name, "customer_phone": phone, "service": "haircut",
                                                   "date": "today", "time": "10:30 AM"})


async def main(mode: str) -> int:
    respond = rehearsal.respond if mode == "rehearsal" else agent.respond
    problems = 0
    info = tenancy.demo_business().info()
    for scene, caller, lines in SCENES:
        if scene.startswith("5."):
            await setup()
        print(f"\n=== {scene}")
        session = None
        known = None
        if caller:
            with _conn() as conn:
                known = schedule.customer_by_phone(conn, caller)
        context = {**info, "caller_phone": caller, "caller_name": known["name"] if known else None, "who": "either"}
        for text, tools, expected in lines:
            r = await respond(text, session, server=mcp, business=info["name"], context=context)
            session = r["session"]
            called = [c["tool"] for c in r["calls"]]
            missing = tools - set(called)
            failed = [c["tool"] for c in r["calls"] if not c["ok"] and c["tool"] in tools]
            mark = "OK " if not missing and not failed else "!! "
            problems += mark == "!! "
            print(f"{mark}YOU:      {text}")
            print(f"   FRONT DESK: {r['reply']}")
            if r["reply"] != expected:
                print(f"   (script:    {expected})")
            used = ", ".join(f"{c['tool']}{c['arguments']}" for c in r["calls"]) or "-"
            print(f"   tools: {used}")
            print(f"   model: {r.get('model')} | {r.get('ms_total')} ms")
    print(f"\n{'All scenes called the right tools.' if not problems else f'{problems} line(s) need attention.'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "bedrock")))
