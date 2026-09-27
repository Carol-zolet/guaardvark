"""Social trailer from an episode's recorded takes: 16:9 and 9:16, cut on the
beat of the launch track, captions burned in (social plays muted).

    venv/bin/python assets/trailer.py PICKS.json OUT_DIR

PICKS.json:
    {"run_dir": "data/outputs/demos/ep19_whatsnew29_<ts>",
     "open": ["00_intro", "03_your_gpu"],            # launch plates, 2 beats each
     "picks": [{"src": "beat_00_teach.raw.mp4", "t": 12.0, "beats": 6,
                "caption": "A THUMB SAYS WHAT IT TAUGHT",
                "zoom": [x, y, w, h]},              # optional 16:9 region, source px
               ...],
     "end": ["ONE MACHINE. NO CLOUD.", "guaardvark.com"]}

Take picks only from beats whose verify passed in that run: the privacy check
ran on those frames, and nothing else in this pipeline sees them.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coldopen import BEAT, CYAN, F_SUB, F_TITLE, FPS, MAG, PLATES, TRACK, TRACK_START  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
SHAPES = {"wide": (1920, 1080), "tall": (1080, 1920)}


def run(cmd: list[str]):
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])


def text_png(lines: list[tuple[str, str, int, tuple]], size: tuple[int, int], y0: float,
             out: Path, band: bool):
    """Centered lines (text, font, px, colour) from y0 (fraction of height); an
    optional dark band behind them so they read over any frame."""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fonts = [ImageFont.truetype(f, px) for _, f, px, _ in lines]
    heights = [f.getbbox("Ag")[3] + 18 for f in fonts]
    y = int(h * y0)
    if band:
        d.rectangle([0, y - 28, w, y + sum(heights) + 16], fill=(0, 0, 0, 170))
    for (text, _, _, colour), fnt, lh in zip(lines, fonts, heights):
        # Wrap to the frame width.
        words, rows, row = text.split(), [], ""
        for word in words:
            trial = f"{row} {word}".strip()
            if fnt.getlength(trial) > w * 0.9 and row:
                rows.append(row)
                row = word
            else:
                row = trial
        rows.append(row)
        for r in rows:
            glow = Image.new("RGBA", size, (0, 0, 0, 0))
            ImageDraw.Draw(glow).text(((w - fnt.getlength(r)) / 2, y), r, font=fnt,
                                      fill=MAG + (200,))
            img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
            d.text(((w - fnt.getlength(r)) / 2, y), r, font=fnt, fill=colour + (255,))
            y += lh
    img.save(out)


def segment(src: str, start: float, dur: float, shape: str, zoom, caption_png: Path | None,
            out: Path):
    w, h = SHAPES[shape]
    region = ""
    if zoom:
        x, y, zw, zh = zoom
        region = f"crop={zw}:{zh}:{x}:{y},"
    if shape == "wide":
        vf = (f"[0:v]trim=start={start}:duration={dur:.4f},setpts=PTS-STARTPTS,{region}"
              f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={w}:{h},fps={FPS},setsar=1[base]")
    else:
        # Picture across the full width in the middle, a blurred copy behind it.
        vf = (f"[0:v]trim=start={start}:duration={dur:.4f},setpts=PTS-STARTPTS,{region}"
              f"split[a][b];"
              f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"boxblur=30:2,eq=brightness=-0.25[bg];"
              f"[b]scale={w}:-2:flags=lanczos[fg];"
              f"[bg][fg]overlay=0:(H-h)/2,fps={FPS},setsar=1[base]")
    cmd = ["ffmpeg", "-y", "-i", src]
    if caption_png:
        cmd += ["-i", str(caption_png)]
        vf += ";[base][1:v]overlay=0:0,format=yuv420p[v]"
    else:
        vf += ";[base]format=yuv420p[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-an", "-c:v", "libx264",
            "-preset", "medium", "-crf", "18", "-t", f"{dur:.4f}", str(out)]
    run(cmd)


def build(picks_file: Path, out_dir: Path):
    spec = json.loads(picks_file.read_text())
    run_dir = REPO / spec["run_dir"]
    plates = {k: v["clip"] for k, v in json.loads(PLATES.read_text()).items()}
    out_dir.mkdir(parents=True, exist_ok=True)
    for shape, (w, h) in SHAPES.items():
        with tempfile.TemporaryDirectory(prefix="trailer_") as tmp:
            tmp = Path(tmp)
            parts: list[Path] = []
            big = 64 if shape == "wide" else 76
            for k, key in enumerate(spec.get("open", [])):
                p = tmp / f"open_{k}.mp4"
                segment(plates[key], 0.4, 2 * BEAT, shape, None, None, p)
                parts.append(p)
            for k, pick in enumerate(spec["picks"]):
                cap = tmp / f"cap_{k}.png"
                text_png([(pick["caption"], F_TITLE, big, (255, 255, 255))], (w, h),
                         0.80 if shape == "wide" else 0.14, cap, band=shape == "wide")
                p = tmp / f"pick_{k}.mp4"
                segment(str(run_dir / pick["src"]), pick["t"], pick["beats"] * BEAT, shape,
                        pick.get("zoom"), cap, p)
                parts.append(p)
            end_png = tmp / "end.png"
            l1, l2 = spec.get("end", ["ONE MACHINE. NO CLOUD.", "guaardvark.com"])
            text_png([(l1, F_TITLE, big + 16 if shape == "wide" else big - 6, (255, 255, 255)),
                      (l2, F_SUB, big - 14, CYAN)],
                     (w, h), 0.42, end_png, band=False)
            p = tmp / "end.mp4"
            segment(plates["12_loop_grid"], 0.4, 8 * BEAT, shape, None, end_png, p)
            parts.append(p)

            total = sum(float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                 "csv=p=0", str(q)], capture_output=True, text=True).stdout) for q in parts)
            lst = tmp / "list.txt"
            lst.write_text("".join(f"file '{q}'\n" for q in parts))
            out = out_dir / f"trailer_{shape}.mp4"
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                 "-ss", f"{TRACK_START}", "-t", f"{total:.3f}", "-i", str(TRACK),
                 "-filter_complex",
                 f"[0:v]fade=t=out:st={total - 0.6:.3f}:d=0.6[v];"
                 f"[1:a]afade=t=out:st={total - 1.5:.3f}:d=1.5[a]",
                 "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
                 "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart", str(out)])
            print(f"{shape}: {out} ({total:.1f}s)")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
