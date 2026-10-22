# Demo video

The demo video is made from code, with no manual recording or editing. It's a screen recording of the deployed app with the real agent (Amazon Nova Pro on Bedrock), plus narration and subtitles.

1. **`record.py`** books the two 10:30 AM haircuts the waitlist scene needs (through the MCP endpoint), then drives the deployed site in a headed Chromium on a virtual display and captures it with `ffmpeg` (x11grab). It types each customer line into the phone line, waits for Front Desk's real answer, and writes `timeline.json`: every segment of the recording with the speech that goes over it, including Nova's actual replies.
2. **`edit.py`** generates every voice with [Kokoro](https://github.com/hexgrad/kokoro) (Apache 2.0, runs locally) through `tts.py`, holds each shot exactly as long as its line, adds the board close-ups, burns in subtitles and normalizes the audio. No music, no stock footage.
3. **`diagram.html`** is the "How it works" slide.

```bash
pip install playwright mcp                       # recorder
python3.12 -m venv kvenv && kvenv/bin/pip install kokoro soundfile   # voices (Kokoro needs Python 3.12 or earlier)
xvfb-run -a -s "-screen 0 1920x1280x24" python scripts/video/record.py https://18-190-98-99.sslip.io run
KOKORO_PYTHON=kvenv/bin/python python scripts/video/edit.py run front-desk-demo.mp4
```

Recording uses the public demo business, so run it right after an update (`deploy/update.cmd` resets the demo data) and on the business's morning, when "this morning" still has open times.
