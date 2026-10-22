"""Records the Front Desk demo with Playwright and writes a timeline of segments for the editor.

Usage (Linux, headed Chromium on a virtual display, captured with ffmpeg x11grab):
    xvfb-run -a -s "-screen 0 1920x1280x24" python scripts/video/record.py https://your-front-desk OUT_DIR
Each segment is a time range of the recorded video plus the speech that goes over it.
"""
import asyncio, json, os, subprocess, sys, time
from pathlib import Path
from mcp import Client
from playwright.async_api import async_playwright

BASE = sys.argv[1].rstrip("/")
OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
W, H = 1920, 1080
WPS = 2.25  # words per second of the narration voice, used to hold the screen long enough

N = {
    "n1": "Small businesses lose bookings every day: the phone rings mid-appointment, nobody picks up, and the customer goes elsewhere.",
    "n2": "Front Desk is a voice receptionist for any business that runs on appointments, built as an MCP server for Alexa plus.",
    "n3": "Every answer is a real MCP tool call, and the owner's board updates live.",
    "n4": "Regulars are recognized by caller ID.",
    "n5": "When someone cancels, the next person on the waitlist gets the slot automatically.",
    "n6": "The owner runs the shop by voice too.",
    "n7a": "And everything the receptionist does, the owner can also do by hand.",
    "n7b": "The calendar shows how each visit was booked: by voice, from the waitlist, or by hand.",
    "n7c": "A walk-in takes seconds, with open times suggested.",
    "n7d": "Every caller becomes a customer with a visit history.",
    "n7e": "Each business gets its own MCP endpoint, ready for Alexa plus.",
    "n7f": "Salon, clinic or tutor: pick your type of business and you're set up in minutes.",
    "n8": "Under the hood: a self-hosted MCP server over Streamable HTTP, and an agent on Amazon Bedrock with Amazon Nova Pro, calling the same tools Alexa plus would. "
          "All deployed on EC2 with HTTPS, a Nova-only IAM role, and CloudFormation.",
    "n9": "Front Desk. Every call answered. Every chair filled.",
}
segments: list[dict] = []
t0 = 0.0


def now() -> float:
    return time.monotonic() - t0


def est(text: str, wps: float = WPS) -> float:
    return len(text.split()) / wps + 0.6


def seg(start, end, speech=None, crop=None, name=""):
    segments.append({"name": name, "start": round(start, 3), "end": round(end, 3), "speech": speech or [], "crop": crop})


async def narrate(page, key, crop=None, extra=0.0):
    s = now()
    await page.wait_for_timeout(int((est(N[key]) + extra) * 1000))
    seg(s, now(), [{"voice": "narrator", "text": N[key]}], crop, key)


async def setup_waitlist_scene():
    """Off camera: Leo and Max take both 10:30 AM haircuts, so Nick has to join the waitlist."""
    async with Client(BASE + "/mcp", mode="legacy") as c:
        for name, phone in (("Leo", "+13105550177"), ("Max", "+13105550178")):
            r = await c.call_tool("book_appointment", {"customer_name": name, "customer_phone": phone, "service": "haircut",
                                                       "date": "today", "time": "10:30 AM"})
            print("setup:", name, "booked at 10:30 AM")


async def main():
    global t0
    await setup_waitlist_scene()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=[f"--window-size={W},{H + 200}", "--window-position=0,0",
                                                                 "--disable-infobars", "--hide-scrollbars", "--force-device-scale-factor=1"])
        ctx = await browser.new_context(viewport={"width": W, "height": H})
        # no browser voice: the editor adds the real voices
        await ctx.add_init_script("""(() => { const stub = { speak(u) { setTimeout(() => u.onend && u.onend(), 30); }, cancel() {}, getVoices() { return []; }, onvoiceschanged: null };
          Object.defineProperty(window, 'speechSynthesis', { value: stub, configurable: true }); })();""")
        page = await ctx.new_page()
        await page.set_content("<body style='margin:0;background:#000'></body>"); await page.wait_for_timeout(1500)
        top = await page.evaluate("window.outerHeight - window.innerHeight")
        raw = OUT / "raw.mp4"
        grab = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-framerate", "30", "-video_size", f"{W}x{H}",
                                 "-draw_mouse", "0", "-i", f"{os.environ.get('DISPLAY', ':99')}+0,{top}", "-c:v", "libx264", "-preset", "ultrafast",
                                 "-crf", "16", "-pix_fmt", "yuv420p", str(raw)], stdin=subprocess.PIPE)
        await page.wait_for_timeout(2000)
        t0 = time.monotonic()
        # sync mark: the screen turns red at t = 0 of the timeline; the editor finds that frame
        await page.set_content("<body style='margin:0;background:#ff0000'></body>"); await page.wait_for_timeout(800)
        replies = []

        # 1. The problem (home page)
        await page.goto(BASE + "/"); await page.wait_for_timeout(1500)
        await narrate(page, "n1")

        # 2. What it is (/demo)
        await page.goto(BASE + "/demo"); await page.wait_for_timeout(2500)
        phone = page.frame_locator("#phone")
        await narrate(page, "n2")

        async def caller(value=None, other=None):
            if other:
                await phone.locator("#caller").select_option("other")
                await phone.locator("#caller-other").fill(other)
                await phone.locator("#caller-other").dispatch_event("change")
            else:
                await phone.locator("#caller").select_option(value or "")
            await page.wait_for_timeout(500)

        async def turn(text, voice, scene):
            reply = phone.locator("#reply")
            before = await reply.inner_text()
            s = now()
            words = len(text.split())
            delay = int(max(25, (words / 2.6) * 1000 / max(1, len(text))))
            await phone.locator("#text").click()
            await phone.locator("#text").press_sequentially(text, delay=delay)
            await page.wait_for_timeout(250)
            await phone.locator("#text").press("Enter")
            sent = now()
            seg(s, sent + 0.2, [{"voice": voice, "text": text}], None, scene + ":customer")
            for _ in range(600):
                await page.wait_for_timeout(100)
                state = await phone.locator("#screen").get_attribute("data-state")
                cur = await reply.inner_text()
                if state == "idle" and cur != before and now() - sent > 0.3:
                    break
            got = now()
            answer = (await reply.inner_text()).strip()
            replies.append({"scene": scene, "you": text, "front_desk": answer})
            seg(max(sent + 0.2, got - 0.6), got, [], None, scene + ":thinking")
            s2 = now()
            await page.wait_for_timeout(int((len(answer.split()) / 2.6 + 0.8) * 1000))
            seg(s2, now(), [{"voice": "frontdesk", "text": answer}], None, scene + ":reply")
            return answer

        BOARD = [0.45, 0.0, 1.0, 0.55]  # crop of the right side (x0, y0, x1, y1 as fractions)

        # 3. A new customer books
        await caller(other="(310) 555-0199")
        await turn("Hi! Do you have anything for a haircut this morning?", "jordan", "3")
        await turn("9:30 works. My name is Jordan.", "jordan", "3")
        await narrate(page, "n3", crop=BOARD, extra=0.5)

        # 4. A regular comes back
        await caller("+13105550142")
        await turn("Hey, it's Sam.", "sam", "4")
        await turn("Yes, today at 11.", "sam", "4")
        await narrate(page, "n4")

        # 5. Waitlist
        await caller(other="(310) 555-0188")
        await turn("Can I get a haircut today at 10:30? I'm Nick.", "nick", "5")
        await turn("Waitlist, please.", "nick", "5")
        await caller(other="(310) 555-0177")
        await turn("Hi, this is Leo. I need to cancel my appointment.", "leo", "5b")
        await narrate(page, "n5", crop=BOARD, extra=0.5)

        # 6. The owner by voice
        await caller("")
        await turn("Block Diego's afternoon, he has a dentist appointment.", "owner", "6")

        # 7. The owner app
        await page.goto(BASE + "/login"); await page.wait_for_timeout(1200)
        await page.locator("#demo").click()
        await page.wait_for_url("**/app**"); await page.wait_for_timeout(2000)
        await narrate(page, "n7a")
        await page.locator('[data-section="calendar"]').click(); await page.wait_for_timeout(1500)
        await narrate(page, "n7b")
        s = now()
        await page.get_by_role("button", name="New appointment").first.click(); await page.wait_for_timeout(700)
        dlg = page.locator("#dialog")
        await dlg.locator('input[name="customer_name"]').press_sequentially("Ana Lopez", delay=70)
        await dlg.locator('input[name="phone"]').press_sequentially("(310) 555-0123", delay=50)
        await page.wait_for_timeout(600)
        slot = dlg.locator(".slots button").nth(3)
        await slot.click(); await page.wait_for_timeout(500)
        await dlg.get_by_role("button", name="Book appointment").click(); await page.wait_for_timeout(1800)
        seg(s, now(), [{"voice": "narrator", "text": N["n7c"]}], None, "n7c")
        await page.locator('[data-section="customers"]').click(); await page.wait_for_timeout(1200)
        s = now()
        await page.locator("tr.clickable", has_text="Sam Torres").first.click(); await page.wait_for_timeout(int(est(N["n7d"]) * 1000))
        seg(s, now(), [{"voice": "narrator", "text": N["n7d"]}], None, "n7d")
        await page.keyboard.press("Escape"); await page.wait_for_timeout(400)
        await page.locator('[data-section="phone"]').click(); await page.wait_for_timeout(1200)
        await narrate(page, "n7e")
        await page.goto(BASE + "/signup"); await page.wait_for_timeout(1500)
        await narrate(page, "n7f")

        # 8. How it works
        await page.goto((Path(__file__).resolve().parent / "diagram.html").as_uri()); await page.wait_for_timeout(1200)
        await narrate(page, "n8", extra=0.8)

        # 9. Close
        await page.goto(BASE + "/demo"); await page.wait_for_timeout(2500)
        await narrate(page, "n9", extra=1.0)

        grab.communicate(b"q", timeout=60)
        path = raw
        await ctx.close()
        await browser.close()
    (OUT / "timeline.json").write_text(json.dumps({"video": str(path), "segments": segments, "replies": replies}, indent=1))
    for r in replies:
        print(f"[{r['scene']}] YOU: {r['you']}\n     FD: {r['front_desk']}")


asyncio.run(main())
