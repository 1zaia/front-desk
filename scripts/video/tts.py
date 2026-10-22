"""Generates speech with Kokoro. Input: JSON list of {id, voice, text}; writes <id>.wav at 24 kHz."""
import json, sys
from pathlib import Path
import numpy as np, soundfile as sf
from kokoro import KPipeline
VOICES = {"narrator": ("am_michael", 1.1), "frontdesk": ("af_heart", 1.12), "jordan": ("af_sarah", 1.05),
          "sam": ("am_eric", 1.05), "nick": ("am_puck", 1.05), "leo": ("am_echo", 1.05), "owner": ("am_liam", 1.0)}
items = json.loads(Path(sys.argv[1]).read_text()); out = Path(sys.argv[2]); out.mkdir(exist_ok=True)
pipe = KPipeline(lang_code="a")
for it in items:
    f = out / f"{it['id']}.wav"
    if f.exists() and f.with_suffix(".txt").exists() and f.with_suffix(".txt").read_text() == it["voice"] + "|" + it["text"]:
        continue
    v, speed = VOICES[it["voice"]]
    audio = np.concatenate([a for _, _, a in pipe(it["text"], voice=v, speed=speed)])
    sf.write(f, audio, 24000); f.with_suffix(".txt").write_text(it["voice"] + "|" + it["text"])
    print(it["id"], round(len(audio) / 24000, 2), flush=True)
