"""Builds the final video from record.py's timeline: real voices (Kokoro), screen held while each line is spoken,
close-ups, burned-in subtitles. Usage: python edit.py RUN_DIR OUT.mp4"""
import json, os, subprocess, sys, wave
from pathlib import Path

RUN = Path(sys.argv[1]); OUT = Path(sys.argv[2])
KPY = os.environ.get("KOKORO_PYTHON", sys.executable)  # a Python with kokoro and soundfile (3.12 or earlier)
TTS = str(Path(__file__).resolve().parent / "tts.py")
W, H, FPS = 1920, 1080, 30
GAP = 0.25     # silence between two lines in one segment
TAIL = 0.35    # silence after the last line of a segment
SPEAKER = {"narrator": "", "frontdesk": "Front Desk", "jordan": "Jordan", "sam": "Sam", "nick": "Nick", "leo": "Leo", "owner": "Owner"}

tl = json.loads((RUN / "timeline.json").read_text())
raw = tl["video"]
segs = tl["segments"]
work = RUN / "edit"; work.mkdir(exist_ok=True)

# sync: the recorder turned the screen red at t = 0 of the timeline
probe = subprocess.run(["ffmpeg", "-loglevel", "error", "-t", "12", "-i", raw, "-vf", "scale=4:4,fps=30", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
SYNC = None
for n in range(len(probe) // 48):
    r, g, b = probe[n * 48 + 15], probe[n * 48 + 16], probe[n * 48 + 17]
    if r > 200 and g < 70 and b < 70:
        SYNC = n / 30; break
if SYNC is None:
    sys.exit("sync mark not found")
print(f"sync mark at {SYNC:.3f}s of the raw video")
for s in segs:
    s["start"] += SYNC; s["end"] += SYNC
tts_dir = work / "tts"

items = []
for i, s in enumerate(segs):
    for j, sp in enumerate(s["speech"]):
        sp["id"] = f"s{i:03d}_{j}"
        items.append({"id": sp["id"], "voice": sp["voice"], "text": sp["text"]})
(work / "tts.json").write_text(json.dumps(items))
subprocess.run([KPY, TTS, str(work / "tts.json"), str(tts_dir)], check=True)


def wav_len(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(" ".join(cmd)); print(r.stderr[-3000:]); sys.exit(1)


def subtitle(text):
    return text.replace("Alexa plus", "Alexa+")


pieces, cues, t = [], [], 0.0
for i, s in enumerate(segs):
    vlen = max(0.04, s["end"] - s["start"])
    lines = [(sp, tts_dir / f"{sp['id']}.wav") for sp in s["speech"]]
    alen = sum(wav_len(f) for _, f in lines) + GAP * max(0, len(lines) - 1) + (TAIL if lines else 0)
    target = (max(vlen, alen) if s["name"].endswith(":customer") else alen) if lines else vlen  # screen held for the speech
    vf = []
    if s.get("crop"):
        x0, y0, x1, y1 = s["crop"]
        vf.append(f"crop={int((x1 - x0) * W)}:{int((y1 - y0) * H)}:{int(x0 * W)}:{int(y0 * H)}")
    vf += [f"scale={W}:{H}:flags=lanczos", f"fps={FPS}", "setsar=1"]
    if target > vlen + 0.01:
        vf.append(f"tpad=stop_mode=clone:stop_duration={target - vlen:.3f}")
    vf.append(f"trim=duration={target:.3f}")
    cmd = ["ffmpeg", "-y", "-ss", f"{s['start']:.3f}", "-t", f"{vlen:.3f}", "-i", raw]
    filt = [f"[0:v]{','.join(vf)}[v]"]
    off, mix = 0.0, []
    for k, (sp, f) in enumerate(lines):
        cmd += ["-i", str(f)]
        ms = int(off * 1000)
        filt.append(f"[{k + 1}:a]aresample=48000,adelay={ms}|{ms}[a{k}]")
        mix.append(f"[a{k}]")
        d = wav_len(f)
        who = SPEAKER[sp["voice"]]
        cues.append((t + off, t + off + d + 0.15, (f"{who}: " if who else "") + subtitle(sp["text"]), sp["voice"] == "narrator"))
        off += d + GAP
    if mix:
        filt.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0,apad,atrim=duration={target:.3f}[a]")
    else:
        cmd += ["-f", "lavfi", "-t", f"{target:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        filt.append(f"[{len(lines) + 1}:a]atrim=duration={target:.3f}[a]")
    piece = work / f"p{i:03d}.mp4"
    cmd += ["-filter_complex", ";".join(filt), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(piece)]
    run(cmd)
    pieces.append(piece)
    t += target
    print(f"{i:3d} {s['name']:<14} video {vlen:5.2f}s  speech {alen:5.2f}s  -> {target:5.2f}s  (total {t:6.1f}s)", flush=True)

(work / "list.txt").write_text("".join(f"file '{p.resolve()}'\n" for p in pieces))
run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(work / "joined.mp4")])


def ts(x):
    h, m = int(x // 3600), int(x % 3600 // 60)
    return f"{h}:{m:02d}:{x % 60:05.2f}"


ass = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
       "[V4+ Styles]",
       "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
       "Style: Narr,Inter,42,&H00FFFFFF,&H00FFFFFF,&H00000000,&H9A10262B,0,0,0,0,100,100,0,0,3,14,0,2,220,220,48,1",
       "Style: Talk,Inter,40,&H00E8F6F7,&H00FFFFFF,&H00000000,&H9A10262B,0,1,0,0,100,100,0,0,3,14,0,2,220,220,48,1",
       "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
for a, b, text, narr in cues:
    ass.append(f"Dialogue: 0,{ts(a)},{ts(b)},{'Narr' if narr else 'Talk'},,0,0,0,,{text}")
(work / "subs.ass").write_text("\n".join(ass) + "\n")
total = t
run(["ffmpeg", "-y", "-i", str(work / "joined.mp4"), "-vf",
     f"ass={work / 'subs.ass'},fade=t=in:st=0:d=0.4,fade=t=out:st={total - 0.6:.2f}:d=0.6",
     "-af", f"afade=t=out:st={total - 0.6:.2f}:d=0.6,loudnorm=I=-16:TP=-1.5:LRA=11",
     "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
     "-movflags", "+faststart", str(OUT)])
print(f"done: {OUT} ({total:.1f}s)")
