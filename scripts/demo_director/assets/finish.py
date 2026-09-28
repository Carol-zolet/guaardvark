"""The edit: movement on the recorded beats, a montage ending, and the assembly
as a Shotcut (MLT) project rendered with melt.

    venv/bin/python assets/finish.py RUN_DIR OUT.mp4 --open OPEN.mp4 --shots SHOTS.json \\
        --title "GUAARDVARK 2.9.1" --tagline "ONE MACHINE. NO CLOUD." \\
        --cta "Free and open source · guaardvark.com" --signoff SIGNOFF.wav

RUN_DIR is a director run (beat_NN_<name>.mp4 plus the .cues.json each take
wrote). Beside OUT it leaves OUT.mlt, the project melt rendered, which opens in
Shotcut for further work.

Movement: every beat drifts in slowly, and pushes in on what each narration
line is about (the focus boxes the take recorded) while the line plays. UI
stays legible: the push is capped so text is never cut mid-word at the frame
edge by more than the box itself.

Ending: quick cuts on the launch track's beat grid (launch plates made on this
box and this episode's own results), each with a Ken Burns move, a soft glow
and a vignette, joined by varied transitions, landing on an animated title.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
REPO = Path(__file__).resolve().parents[3]
TRACK = REPO / "data" / "demo_assets" / "launch" / "music" / "one_machine_009.wav"
BEAT = 0.5333                      # 112.5 bpm, the launch cut's grid
DROP_AT = 97.48 + 32 * BEAT        # a downbeat in the track's full section
F_TITLE = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
F_SUB = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
CYAN, MAG = (34, 225, 255), (255, 43, 214)

MLT_PROFILE = "atsc_1080p_30"   # 1920x1080, 30 fps, square pixels
RAMP = 0.45          # seconds to ease into and out of a push-in
BASE_DRIFT = 0.025   # the whole beat drifts in by 2.5%
MAX_PUSH = 1.45      # never closer than this: context stays on screen
MIN_PUSH = 1.12


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-1500:]}")


def duration(path: Path) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True).stdout)


# ------------------------------------------------------------------ movement

def _push_exprs(events: list[dict], total: float) -> tuple[str, str, str]:
    """ffmpeg expressions for zoom, centre x and centre y over time.

    Each event is a smooth pulse: ease in over RAMP, hold, ease out. Pulses do
    not overlap (later ones are trimmed), so the weights never sum past 1.
    """
    pulses = []
    last_end = -1.0
    for ev in sorted(events, key=lambda e: e["t"]):
        a = max(ev["t"], last_end)
        b = min(ev["t"] + max(ev["hold"], 2 * RAMP + 0.3), total - 0.05)
        if b - a < 2 * RAMP:
            continue
        x, y, w, h = ev["box"]
        cx, cy = x + w / 2, y + h / 2
        z = min(W / max(w * 2.4, 1), H / max(h * 2.4, 1))
        z = max(MIN_PUSH, min(MAX_PUSH, z))
        pulses.append((a, b, z, cx, cy))
        last_end = b
    zb = f"(1+{BASE_DRIFT}*t/{total:.3f})"
    if not pulses:
        return zb, f"{W / 2}", f"{H / 2}"
    zoom_terms, cx_terms, cy_terms = [], [], []
    for a, b, z, cx, cy in pulses:
        p = f"clip(min((t-{a:.3f})/{RAMP},({b:.3f}-t)/{RAMP}),0,1)"
        s = f"({p}*{p}*(3-2*{p}))"
        zoom_terms.append(f"{z - 1:.4f}*{s}")
        cx_terms.append(f"{cx - W / 2:.1f}*{s}")
        cy_terms.append(f"{cy - H / 2:.1f}*{s}")
    zoom = f"{zb}*(1+{'+'.join(zoom_terms)})"
    cx = f"({W / 2}+{'+'.join(cx_terms)})"
    cy = f"({H / 2}+{'+'.join(cy_terms)})"
    return zoom, cx, cy


def push(clip: Path, cues: Path | None, out: Path) -> Path:
    """Render one beat with its drift and push-ins; audio untouched."""
    total = duration(clip)
    events = json.loads(cues.read_text()).get("focus", []) if cues and cues.exists() else []
    zoom, cx, cy = _push_exprs(events, total)
    vf = (f"scale=w='trunc({W}*{zoom}/2)*2':h='trunc({H}*{zoom}/2)*2':eval=frame:flags=lanczos,"
          f"crop={W}:{H}:x='clip({cx}*iw/{W}-{W / 2},0,iw-{W})':"
          f"y='clip({cy}*ih/{H}-{H / 2},0,ih-{H})',setsar=1,fps={FPS}")
    run(["ffmpeg", "-y", "-i", str(clip), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)])
    return out


# ------------------------------------------------------------------ ending

def _grade() -> str:
    """Soft glow, a touch more colour, a vignette: one look for every cut."""
    return ("split[g0][g1];[g1]gblur=sigma=18,format=yuv420p[gb];"
            "[g0][gb]blend=all_mode=screen:all_opacity=0.28,"
            "eq=saturation=1.18:contrast=1.04,vignette=PI/5")


def _flatten(src: str, out: Path) -> str:
    """Stills with transparency (a background cut-out) go onto a dark
    gradient; video codecs would otherwise show whatever the RGB holds."""
    im = Image.open(src)
    if im.mode not in ("RGBA", "LA", "P"):
        return src
    im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size)
    d = ImageDraw.Draw(bg)
    for y in range(im.height):
        k = y / max(1, im.height - 1)
        d.line([(0, y), (im.width, y)], fill=(int(18 + 30 * k), int(10 + 8 * k), int(40 + 40 * k), 255))
    bg.alpha_composite(im)
    bg.convert("RGB").save(out)
    return str(out)


def shot(src: str, dur: float, out: Path, move: str) -> Path:
    """One montage shot: a still or a clip, Ken Burns move, the grade."""
    is_still = src.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    if is_still:
        src = _flatten(src, out.with_suffix(".flat.png"))
    frames = int(round(dur * FPS))
    z0, z1 = {"in": (1.0, 1.14), "out": (1.14, 1.0), "left": (1.08, 1.08),
              "right": (1.08, 1.08)}[move]
    # zoompan's window is iw/zoom wide: position it inside the source, never past it.
    pan = {"left": ("(iw-iw/zoom)*(1-on/{n})", "(ih-ih/zoom)/2"),
           "right": ("(iw-iw/zoom)*on/{n}", "(ih-ih/zoom)/2")}.get(
        move, ("(iw-iw/zoom)/2", "(ih-ih/zoom)/2"))
    z = f"{z0}+({z1}-{z0})*on/{frames}"
    # Upscale first so the per-frame crop moves smoothly.
    # zoompan emits one frame per input frame, so feed it exactly FPS frames a
    # second: stills default to 25 fps and the launch plates run at their own rate.
    src_in = (["-framerate", str(FPS), "-loop", "1", "-t", f"{dur:.3f}", "-i", src]
              if is_still else ["-i", src])
    pre = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase:flags=lanczos,"
           f"crop={W * 2}:{H * 2}")
    zp = (f"zoompan=z='{z}':x='{pan[0].format(n=frames)}':y='{pan[1].format(n=frames)}':"
          f"d=1:s={W}x{H}:fps={FPS}")
    trim = "" if is_still else (f"trim=start=0.4,setpts=PTS-STARTPTS,fps={FPS},"
                                f"tpad=stop_mode=clone:stop_duration={dur + 0.5:.3f},"
                                f"trim=duration={dur + 0.5:.3f},")
    fc = f"[0:v]{trim}{pre},{zp},trim=duration={dur:.3f},setpts=PTS-STARTPTS,{_grade()},setsar=1,format=yuv420p[v]"
    run(["ffmpeg", "-y", *src_in, "-filter_complex", fc, "-map", "[v]", "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS), str(out)])
    return out


def title_frames(tmp: Path, title: str, tagline: str, cta: str, seconds: float) -> Path:
    """The closing title, animated: the title lands (scale 1.25 -> 1.0 with a
    fade), the tagline and the call to action follow a beat later."""
    n = int(round(seconds * FPS))
    ft = ImageFont.truetype(F_TITLE, 150)
    fs = ImageFont.truetype(F_SUB, 54)
    fc = ImageFont.truetype(F_SUB, 40)
    frames_dir = tmp / "title"
    frames_dir.mkdir(exist_ok=True)

    def ease(x):
        x = max(0.0, min(1.0, x))
        return 1 - (1 - x) ** 3

    def text_layer(txt, font, fill, glow):
        bb = font.getbbox(txt)
        tw, th = bb[2] - bb[0] + 60, bb[3] - bb[1] + 60
        layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.text((30 - bb[0], 30 - bb[1]), txt, font=font, fill=glow)
        layer = layer.filter(ImageFilter.GaussianBlur(14))
        d = ImageDraw.Draw(layer)
        d.text((30 - bb[0], 30 - bb[1]), txt, font=font, fill=fill)
        return layer

    t_layer = text_layer(title, ft, (255, 255, 255, 255), CYAN + (230,))
    s_layer = text_layer(tagline, fs, CYAN + (255,), MAG + (200,))
    c_layer = text_layer(cta, fc, (235, 235, 245, 255), (0, 0, 0, 0))
    for i in range(n):
        t = i / FPS
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        a = ease(t / 0.5)
        sc = 1.25 - 0.25 * a
        lw, lh = int(t_layer.width * sc), int(t_layer.height * sc)
        tl = t_layer.resize((lw, lh), Image.LANCZOS)
        tl.putalpha(tl.getchannel("A").point(lambda v, a=a: int(v * a)))
        im.alpha_composite(tl, ((W - lw) // 2, H // 2 - 170 - lh // 2 + 40))
        for layer, start, y in ((s_layer, 2 * BEAT, H // 2 + 20), (c_layer, 4 * BEAT, H // 2 + 130)):
            b = ease((t - start) / 0.4)
            if b > 0:
                l2 = layer.copy()
                l2.putalpha(l2.getchannel("A").point(lambda v, b=b: int(v * b)))
                im.alpha_composite(l2, ((W - l2.width) // 2, y + int(20 * (1 - b))))
        im.save(frames_dir / f"f{i:04d}.png")
    return frames_dir


def outro(shots: list[dict], title: str, tagline: str, cta: str, signoff: Path | None,
          out: Path, tmp: Path, title_bg: str | None = None) -> Path:
    """Montage on the beat, then the animated title over the last plate."""
    moves = ["in", "left", "out", "right"]
    transitions = ["circleopen", "slideleft", "zoomin", "smoothright", "radial", "wipeleft",
                   "fadewhite", "slideup"]
    xf = 0.18                                        # quick transitions
    clips, durs = [], []
    for k, s in enumerate(shots):
        d = s.get("beats", 2) * BEAT + xf
        clips.append(shot(s["src"], d, tmp / f"shot_{k:02d}.mp4", s.get("move", moves[k % 4])))
        durs.append(d)
    title_s = max(4.5, (duration(signoff) + 1.2) if signoff else 0)
    bg = shot(title_bg or shots[-1]["src"], title_s + xf, tmp / "title_bg.mp4", "in")
    frames = title_frames(tmp, title, tagline, cta, title_s + xf)
    card = tmp / "title_card.mp4"
    run(["ffmpeg", "-y", "-i", str(bg), "-framerate", str(FPS), "-i", str(frames / "f%04d.png"),
         "-filter_complex", "[0:v]eq=brightness=-0.26:saturation=0.8,gblur=sigma=10[b];"
         "[b][1:v]overlay=0:0:shortest=1,format=yuv420p[v]",
         "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS),
         str(card)])
    clips.append(card)
    durs.append(title_s + xf)

    # Chain the shots with xfade, each transition landing on the beat.
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    durs = [duration(c) for c in clips]          # the rendered lengths, not the plan
    fl, prev, offset = [], "[0:v]", 0.0
    for k in range(1, len(clips)):
        offset += durs[k - 1] - xf
        tr = transitions[(k - 1) % len(transitions)] if k < len(clips) - 1 else "fadewhite"
        lab = f"[x{k}]"
        fl.append(f"{prev}[{k}:v]xfade=transition={tr}:duration={xf}:offset={offset:.3f}{lab}")
        prev = lab
    total = offset + durs[-1]
    fl.append(f"{prev}fade=t=out:st={total - 0.6:.3f}:d=0.6,format=yuv420p[v]")
    # Music from a downbeat in the full section; the sign-off over the title.
    title_at = total - durs[-1]
    cmd += ["-ss", f"{DROP_AT:.3f}", "-t", f"{total:.3f}", "-i", str(TRACK)]
    mi = len(clips)
    audio = f"[{mi}:a]volume=0.62,afade=t=in:st=0:d=0.15,afade=t=out:st={total - 1.4:.3f}:d=1.4[m]"
    if signoff:
        cmd += ["-i", str(signoff)]
        ms = int((title_at + 0.35) * 1000)
        audio += (f";[{mi + 1}:a]adelay=delays={ms}:all=1,apad[n];"
                  f"[m]volume=0.8[m2];[n][m2]amix=inputs=2:duration=longest:normalize=0,"
                  f"atrim=duration={total:.3f},aformat=sample_rates=44100:channel_layouts=stereo[a]")
    else:
        audio += ";[m]aformat=sample_rates=44100:channel_layouts=stereo[a]"
    fl.append(audio)
    cmd += ["-filter_complex", ";".join(fl), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", str(out)]
    run(cmd)
    return out


# ------------------------------------------------------------------ assembly

def luma(path: Path, kind: str) -> Path:
    """A wipe pattern for MLT's luma transition (dark reveals first)."""
    im = Image.new("L", (W, H))
    px = im.load()
    for y in range(H):
        for x in range(W):
            if kind == "iris":
                v = math.hypot(x - W / 2, y - H / 2) / math.hypot(W / 2, H / 2)
            else:  # diagonal wipe, left to right
                v = (x / W) * 0.8 + (y / H) * 0.2
            px[x, y] = int(max(0.0, min(1.0, v)) * 255)
    im.save(path)
    return path


def assemble(parts: list[Path], mixes: list[tuple[int, str | None]], out: Path) -> Path:
    """Parts in order on one track, each join a luma transition (dissolve when
    no pattern) with an audio crossfade. Writes OUT.mlt and renders it."""
    # A fixed profile: left to guess, melt wrote 16:15 pixels on the first pass
    # and the next pass letterboxed the whole body into 16:9.
    cmd = ["melt", "-silent", "-profile", MLT_PROFILE]
    for k, p in enumerate(parts):
        cmd.append(str(p))
        if k > 0:
            frames, pattern = mixes[k - 1]
            cmd += ["-mix", str(frames), "-mixer", "luma"]
            if pattern:
                cmd += [f"resource={pattern}", "softness=0.25"]
            cmd += ["-mixer", "mix:-1"]
    project = out.with_suffix(".mlt")
    run(cmd + ["-consumer", f"xml:{project}"])
    run(["melt", "-silent", "-profile", MLT_PROFILE, str(project), "-consumer", f"avformat:{out}",
         "vcodec=libx264", "crf=19", "preset=medium", "acodec=aac", "ab=160k",
         f"width={W}", f"height={H}", f"frame_rate_num={FPS}", "frame_rate_den=1",
         "progressive=1", "movflags=+faststart"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--open", type=Path, required=True)
    ap.add_argument("--shots", type=Path, required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--tagline", default="")
    ap.add_argument("--cta", default="")
    ap.add_argument("--signoff", type=Path)
    ap.add_argument("--bed", type=float, default=0.09)
    a = ap.parse_args()

    beats = sorted(p for p in a.run_dir.glob("beat_[0-9][0-9]_*.mp4")
                   if not p.name.endswith((".raw.mp4", ".ff.mp4", ".pad.mp4")) and ".raw" not in p.name)
    work = a.out.parent / (a.out.stem + "_edit")
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="finish_") as tmp:
        tmp = Path(tmp)
        moved = []
        for b in beats:
            cues = b.parent / (b.stem + ".cues.json")
            moved.append(push(b, cues, work / f"{b.stem}.moved.mp4"))
            print(f"  moved {b.name}")
        # Body: beats joined with short dissolves, then the quiet music bed.
        body = assemble(moved, [(12, None)] * (len(moved) - 1), work / "body.mp4")
        from coldopen import bed as add_bed
        body_bed = work / "body_bed.mp4"
        add_bed(body, body_bed, a.bed)
        spec = json.loads(a.shots.read_text())
        end = outro(spec["shots"], a.title, a.tagline, a.cta, a.signoff, work / "outro.mp4", tmp,
                    title_bg=spec.get("title_bg"))
        print(f"  outro {duration(end):.1f}s")
        iris = luma(work / "iris.pgm", "iris")
        wipe = luma(work / "wipe.pgm", "wipe")
        mixed = work / "mixed.mp4"
        assemble([a.open, body_bed, end], [(15, str(iris)), (12, str(wipe))], mixed)
        # -16 LUFS, the usual level for web video; the mix itself sat near -19.
        run(["ffmpeg", "-y", "-i", str(mixed), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-movflags", "+faststart", str(a.out)])
        project = mixed.with_suffix(".mlt")
        project.replace(a.out.with_suffix(".mlt"))
    print(f"finished: {a.out} ({duration(a.out):.1f}s), project {a.out.with_suffix('.mlt')}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
