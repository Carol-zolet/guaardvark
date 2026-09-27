"""Cinematic cold open for an episode: launch-video plates cut on the beat of
the launch track, ending on a title card. Output is a 1920x1080 30 fps clip
with stereo AAC, the same shape the director's beats are, so it concatenates
in front of an assembled episode without re-timing.

    venv/bin/python assets/coldopen.py OUT.mp4 "GUAARDVARK 2.9" "WHAT'S NEW"
    venv/bin/python assets/coldopen.py --endcard OUT.mp4 NARRATION.wav "LINE ONE" "line two"
    venv/bin/python assets/coldopen.py --join OUT.mp4 OPEN.mp4 EPISODE.mp4 END.mp4

Plates: data/demo_assets/launch/plates/final_plates.json (Wan 2.2 14B I2V,
made on this box). Track and its beat grid: the launch cut's take 009.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parents[3]
PLATES = REPO / "data" / "demo_assets" / "launch" / "plates" / "final_plates.json"
TRACK = REPO / "data" / "demo_assets" / "launch" / "music" / "one_machine_009.wav"
TRACK_START = 97.48      # the downbeat the launch cut opens on
BEAT = 0.5333            # 112.5 bpm, from the launch cut's beat grid
W, H, FPS = 1920, 1080, 30
F_TITLE = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
F_SUB = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
CYAN, MAG = (34, 225, 255), (255, 43, 214)

# (plate key, beats). The last shot carries the title.
SHOTS = [
    ("00_intro", 4), ("03_your_gpu", 2), ("06_eleven_models", 2),
    ("09_train_a_face", 2), ("08_any_voice", 2), ("10_twenty_agents", 4),
    ("12_loop_grid", 8),
]


def run(cmd: list[str]):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def title_card(title: str, sub: str, out: Path):
    """Transparent 1920x1080 PNG: tracked title with a colour glow, subtitle
    below, on a soft dark band so both read over a busy plate. The title
    shrinks to fit 86% of the width."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    size = 150
    while True:
        ft = ImageFont.truetype(F_TITLE, size)
        track = size * 0.12
        width = sum(ft.getlength(c) for c in title) + track * (len(title) - 1)
        if width <= W * 0.86 or size <= 60:
            break
        size -= 6
    fs = ImageFont.truetype(F_SUB, 44)
    y = H * 0.36

    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(band).rectangle([0, y - 50, W, y + size + 120], fill=(0, 0, 0, 150))
    img.alpha_composite(band.filter(ImageFilter.GaussianBlur(30)))

    def tracked(draw, y, text, fnt, fill, track):
        widths = [fnt.getlength(c) for c in text]
        x = (W - (sum(widths) + track * (len(text) - 1))) / 2
        for c, w in zip(text, widths):
            draw.text((x, y), c, font=fnt, fill=fill)
            x += w + track

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    tracked(ImageDraw.Draw(glow), y, title, ft, MAG + (255,), track)
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    tracked(d, y, title, ft, (255, 255, 255, 255), track)
    if sub:
        tracked(d, y + size + 40, sub, fs, CYAN + (255,), 10)
    img.save(out)


def build(out: Path, title: str, sub: str):
    plates = {k: v["clip"] for k, v in json.loads(PLATES.read_text()).items()}
    total = sum(b for _, b in SHOTS) * BEAT
    with tempfile.TemporaryDirectory(prefix="coldopen_") as tmp:
        tmp = Path(tmp)
        card = tmp / "title.png"
        title_card(title, sub, card)
        cmd = ["ffmpeg", "-y"]
        fl, labels = [], []
        for k, (key, beats) in enumerate(SHOTS):
            dur = beats * BEAT
            cmd += ["-i", plates[key]]
            # Plates run 2-5 s at 736-960 px; start 0.4 s in (past the I2V
            # settle), scale up to the frame, and hold the last frame if short.
            fl.append(
                f"[{k}:v]trim=start=0.4,setpts=PTS-STARTPTS,"
                f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H},fps={FPS},tpad=stop_mode=clone:stop_duration={dur},"
                f"trim=duration={dur:.4f},setpts=PTS-STARTPTS,setsar=1[s{k}]")
            labels.append(f"[s{k}]")
        n = len(SHOTS)
        title_at = total - SHOTS[-1][1] * BEAT + 2 * BEAT
        cmd += ["-loop", "1", "-i", str(card)]
        cmd += ["-ss", f"{TRACK_START}", "-t", f"{total:.3f}", "-i", str(TRACK)]
        fl.append(f"{''.join(labels)}concat=n={n}:v=1:a=0[cut]")
        fl.append(f"[{n}:v]format=rgba,fade=t=in:st={title_at:.3f}:d=0.35:alpha=1,"
                  f"trim=duration={total:.3f}[card]")
        fl.append(f"[cut][card]overlay=0:0:shortest=1,"
                  f"fade=t=out:st={total - 0.5:.3f}:d=0.5,format=yuv420p[v]")
        # 4 dB under full scale so the open does not jump above the narration.
        fl.append(f"[{n + 1}:a]volume=0.63,afade=t=out:st={total - 1.2:.3f}:d=1.2,"
                  f"aformat=sample_rates=44100:channel_layouts=stereo[a]")
        cmd += ["-filter_complex", ";".join(fl), "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-b:a", "160k", "-r", str(FPS), str(out)]
        run(cmd)
    print(f"cold open: {out} ({total:.2f}s)")


def endcard(out: Path, narration: Path, line1: str, line2: str, plate: str = "00_intro"):
    """Closing card: a launch plate held under two lines of type, the narrator
    over the tail of the launch track. Runs as long as the narration plus a beat."""
    plates = {k: v["clip"] for k, v in json.loads(PLATES.read_text()).items()}
    nar = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(narration)], capture_output=True, text=True).stdout)
    total = nar + 2.0
    with tempfile.TemporaryDirectory(prefix="endcard_") as tmp:
        card = Path(tmp) / "card.png"
        title_card(line1, line2, card)
        # Music from the track's last eight bars, under the voice.
        music_at = TRACK_START + 64 * BEAT
        run(["ffmpeg", "-y", "-i", plates[plate], "-loop", "1", "-i", str(card),
             "-i", str(narration), "-ss", f"{music_at:.3f}", "-t", f"{total:.3f}", "-i", str(TRACK),
             "-filter_complex",
             f"[0:v]trim=start=0.4,setpts=PTS-STARTPTS,"
             f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},"
             f"fps={FPS},tpad=stop_mode=clone:stop_duration={total},trim=duration={total:.3f},"
             f"setsar=1[bg];"
             f"[1:v]format=rgba,fade=t=in:st=0.3:d=0.4:alpha=1,trim=duration={total:.3f}[t];"
             f"[bg][t]overlay=0:0:shortest=1,fade=t=in:st=0:d=0.4,"
             f"fade=t=out:st={total - 0.8:.3f}:d=0.8,format=yuv420p[v];"
             f"[2:a]adelay=500|500,apad[n];[3:a]volume=0.35,afade=t=in:st=0:d=0.6,"
             f"afade=t=out:st={total - 1.2:.3f}:d=1.2[m];"
             f"[n][m]amix=inputs=2:duration=longest:normalize=0,atrim=duration={total:.3f},"
             f"aformat=sample_rates=44100:channel_layouts=stereo[a]",
             "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
             "-crf", "18", "-c:a", "aac", "-b:a", "160k", "-r", str(FPS), str(out)])
    print(f"end card: {out} ({total:.2f}s)")


def bed(body: Path, out: Path, level: float = 0.09):
    """The launch track looped quietly under an episode body, so the stretches
    where the picture runs past the narration are not dead air. At 0.09 the
    bed sits about 15 dB under the narrator."""
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(body)], capture_output=True, text=True).stdout)
    run(["ffmpeg", "-y", "-i", str(body),
         "-stream_loop", "-1", "-ss", f"{TRACK_START}", "-i", str(TRACK),
         "-filter_complex",
         f"[1:a]volume={level},afade=t=in:st=0:d=1.5,afade=t=out:st={dur - 2:.3f}:d=2,"
         f"atrim=duration={dur:.3f}[m];"
         f"[0:a][m]amix=inputs=2:duration=first:normalize=0,"
         f"aformat=sample_rates=44100:channel_layouts=stereo[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", str(out)])
    print(f"bed: {out}")


def join(out: Path, parts: list[Path]):
    """Clips in order through the concat filter, each normalised to one shape."""
    fl = "".join(
        f"[{k}:v]scale={W}:{H},fps={FPS},setsar=1[v{k}];"
        f"[{k}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{k}];"
        for k in range(len(parts)))
    fl += "".join(f"[v{k}][a{k}]" for k in range(len(parts)))
    fl += f"concat=n={len(parts)}:v=1:a=1[v][a]"
    cmd = ["ffmpeg", "-y"]
    for p in parts:
        cmd += ["-i", str(p)]
    run(cmd + ["-filter_complex", fl, "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    print(f"joined: {out}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--bed":
        bed(Path(a[1]), Path(a[2]))
    elif a and a[0] == "--join":
        join(Path(a[1]), [Path(p) for p in a[2:]])
    elif a and a[0] == "--endcard":
        endcard(Path(a[1]), Path(a[2]), a[3], a[4] if len(a) > 4 else "")
    else:
        build(Path(a[0]), a[1] if len(a) > 1 else "GUAARDVARK",
              a[2] if len(a) > 2 else "")
