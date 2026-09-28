"""Episode 19 — Everything New in 2.9 (≈5:00).

What landed since Ep 13, shown on the real product: thumbs that say what they
taught, a web page read at the passage that answers, the MCP client
connecting an outside server and chat using its tool, a Hugging Face model
looked up (and one refused by name) before anything downloads, photo editing
inside chat (edit, outpaint, cut-out), a face carried into a new scene, and a
MiniMax H3 clip with its own voice rendered from Video Gen with ComfyUI
started by the render itself.

Every countable thing is read when this file loads or checked in `verify`:
the version from /api/health, the MCP server's tool count from the connect
response, the "taught" note from what the page rendered, the H3 clip's line
from local speech-to-text on its soundtrack.

GPU cast: Ollama (chat beats) + Audio Foundry (narration); ComfyUI for the
photo and video beats (Qwen-Image-Edit, PuLID and MiniMax H3 installed). One
heavy service per take: chat beats first, the models look-up (no GPU), then
the ComfyUI beats; the video beat stops ComfyUI in its reset.

Assets (made on this box, no real person): data/demo_assets/ep19/
cafe_street.png and portrait_fictional.png, Z-Image renders. The web beat
reads the public Wikipedia page for the aardvark; the models beat looks up two
public Hugging Face repos and installs nothing.

Requires: `staging.py status` READY; zvec_grep's MCP server configured.

Run from scripts/demo_director/:  venv/bin/python episodes/ep19_whatsnew29.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests as rq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import API, Beat, Episode, Stage  # noqa: E402
from helpers import (  # noqa: E402
    REPO, api_get, close_dialogs, goto as st_goto, open_workspace, require, set_nav_chrome,
    verify_no_private_names, verify_path)

ASSETS = REPO / "data" / "demo_assets" / "ep19"
CAFE = ASSETS / "cafe_street.png"
PORTRAIT = ASSETS / "portrait_fictional.png"

CHAT_INPUT = "Type your message, paste an image, or use voice..."
# The whole episode renders at 125%: at 100% the thumb row and the taught note
# are 10 px type, unreadable in a 1080p frame. Chromium's device scale factor
# re-lays every page out (CSS zoom broke the 100vh chat layout; Ctrl+plus
# depended on X keyboard focus). Read by director.Stage at launch.
DEVICE_SCALE = os.environ.setdefault("DEMO_DEVICE_SCALE", "1.25")

TEACH_ASK = os.environ.get(
    "EP19_TEACH_ASK", "When does the AcmeCorp service agreement renew, and how much notice does cancelling take?")
EDIT_ASK = os.environ.get(
    "EP19_EDIT_ASK", "Make it night: street lamps on, warm light from the café windows, wet cobblestones.")
OUTPAINT_ASK = os.environ.get("EP19_OUTPAINT_ASK", "Extend the image to the left and right.")
CUTOUT_ASK = os.environ.get("EP19_CUTOUT_ASK", "Remove the background.")
IDENTITY_ASK = os.environ.get(
    "EP19_IDENTITY_ASK", "Put this person in a sunlit greenhouse full of ferns, same face.")
MCP_SERVER = "zvec_grep"
WEB_ASK = os.environ.get(
    "EP19_WEB_ASK", "What does an aardvark eat? Read https://en.wikipedia.org/wiki/Aardvark")
MCP_ASK = os.environ.get(
    "EP19_MCP_ASK", "Use zvec_grep to find the code that reads a web page.")
# Add new model: one Hugging Face repo the product can run (a single-file
# LTX-2.3 LoRA, Apache-2.0) and one it refuses by name. Looked up, never
# installed, on camera.
HF_OK = os.environ.get("EP19_HF_OK", "https://huggingface.co/joyfox/LTX-2.3-Transition-LORA")
HF_NO = os.environ.get("EP19_HF_NO", "https://huggingface.co/genmo/mochi-1-preview")
H3_LABEL = "MiniMax H3 (Int8, 16GB)"
VIDEO_ASK = os.environ.get(
    "EP19_VIDEO_ASK",
    "A cartoon aardvark chef in a tiny white hat stirs a steaming pot in a cozy kitchen, "
    "looks at the camera and says: \"Dinner is served. Ants, of course.\"")
# What the clip must be heard to say (local speech-to-text on its soundtrack).
VIDEO_LINE = ("dinner", "served", "ants")


def load_numbers() -> dict:
    health = rq.get(f"{API}/api/health", timeout=10).json()
    return {"version": health.get("version")}


N = load_numbers()


# ----------------------------------------------------------------- helpers

def press(st: Stage, key: str, settle: float = 0.6):
    st.cursor._xdo("key", key)
    time.sleep(settle)


def chat_box(st: Stage):
    # The placeholder reads "Ask about this image..." while a photo is attached.
    return st.page.locator(
        f"textarea[placeholder='{CHAT_INPUT}'], textarea[placeholder='Ask about this image...']"
    ).first


def check_scale(st: Stage):
    """Every page renders at DEVICE_SCALE (set before the Stage launches):
    the thumb row and the taught note are 10 px type at 100%."""
    got = st.page.evaluate("() => window.devicePixelRatio")
    require(abs(got - float(DEVICE_SCALE)) < 0.01,
            f"page renders at {got}, not {DEVICE_SCALE}: set DEMO_DEVICE_SCALE before the Stage")


def new_chat(st: Stage):
    # The tooltip labels the wrapping span, not the button inside it.
    btn = st.page.locator("[aria-label='Start a new chat session'] button")
    require(btn.count(), "no new-chat button")
    btn.first.click(timeout=10_000)
    time.sleep(1.5)
    require(st.page.get_by_text(re.compile(r"^\d+ msgs?$", re.IGNORECASE)).count() == 0,
            "the previous chat is still on screen")


def plugin_status(pid: str) -> str | None:
    ps = api_get("/api/plugins")
    plugins = ps.get("plugins", ps) if isinstance(ps, dict) else ps
    if isinstance(plugins, dict):
        plugins = list(plugins.values())
    for p in plugins:
        if isinstance(p, dict) and p.get("id") == pid:
            return p.get("status")
    return None


def fresh_chat(st: Stage):
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/chat")
    chat_box(st).wait_for(state="visible", timeout=60_000)
    new_chat(st)
    check_scale(st)
    require(plugin_status("ollama") == "running", "Ollama is not running")


def ask(st: Stage, text: str, delay_ms: int = 22):
    box = chat_box(st)
    st.glide_click(box, dur=0.7)
    # Keys typed with the focus anywhere else land on the page's single-letter
    # shortcuts (upload dialog, microphone); refuse to type blind.
    box.focus()
    time.sleep(0.2)
    require(st.page.evaluate("() => document.activeElement && document.activeElement.tagName") == "TEXTAREA",
            "chat box did not take focus")
    # Keys go to the focused element, not the X focus: a composer that
    # re-rendered after the last turn cannot drop them onto page shortcuts.
    box.press_sequentially(text, delay=delay_ms)
    time.sleep(0.4)
    box.press("Enter")
    time.sleep(0.5)


def assistant_rows(st: Stage):
    # Each finished assistant reply carries the thumb pair.
    return st.page.locator("button:has([data-testid='ThumbUpOutlinedIcon']),"
                           "button:has([data-testid='ThumbUpIcon'])")


def wait_reply(st: Stage, before: int, timeout: float = 150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if assistant_rows(st).count() > before:
            return
        time.sleep(0.5)
    raise RuntimeError("no finished assistant reply")


def wait_turn(st: Stage, before: int, timeout: float = 240):
    """A finished reply: one more thumbed reply than before and the composer
    enabled again (a tool card can land before the answer does)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if assistant_rows(st).count() > before and chat_box(st).is_enabled():
            time.sleep(1.0)
            return
        time.sleep(0.5)
    raise RuntimeError("no finished assistant reply")


def last_reply_text(st: Stage) -> str:
    return st.page.evaluate("""() => {
        const rows = [...document.querySelectorAll('button')].filter(b =>
            b.querySelector("[data-testid='ThumbUpOutlinedIcon'],[data-testid='ThumbUpIcon']"));
        let el = rows[rows.length - 1];
        for (let k = 0; el && k < 8; k++) {
            el = el.parentElement;
            if (el && el.innerText && el.innerText.length > 40) return el.innerText;
        }
        return "";
    }""")


def attach(st: Stage, path: Path):
    """Glide to the paperclip on camera, then hand the file to its hidden
    input: a native file dialog would open outside the kiosk frame."""
    clip = st.page.locator("button:has([data-testid='AttachFileIcon'])").first
    st.hover_over(clip, dur=0.7)
    st.page.locator("input[type='file'][accept*='image']").first.set_input_files(str(path))
    time.sleep(2.0)


def chat_images(st: Stage):
    return st.page.locator("img[src*='/api/']").filter(
        has_not=st.page.locator("[alt='Reference likeness']"))


def wait_new_image(st: Stage, before: int, expect: int = 1, timeout: float = 480):
    """Wait for the turn to finish (the composer is disabled while a tool
    runs) and for `expect` more images than before: 2 when the turn also
    carried the uploaded photo into the user's bubble."""
    time.sleep(3.0)
    deadline = time.monotonic() + timeout
    idle_since = None
    while time.monotonic() < deadline:
        if chat_images(st).count() >= before + expect and chat_box(st).is_enabled():
            time.sleep(2.0)
            return
        # The composer re-enables when the turn ends; a turn that ended
        # without its image (a tool error reply) fails the take now.
        if chat_box(st).is_enabled():
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since > 12:
                break
        else:
            idle_since = None
        time.sleep(1.0)
    raise RuntimeError(f"no result image: {chat_images(st).count() - before} new of {expect}")


# ------------------------------------------------------------ beat 1: teach

_TAUGHT = {"texts": []}


def reset_teach(st: Stage):
    fresh_chat(st)
    _TAUGHT["texts"] = []


def act_teach(st: Stage):
    before = assistant_rows(st).count()
    ask(st, TEACH_ASK)
    # The first reply after narration waits for the chat model to load back.
    with st.fast_forward():
        wait_reply(st, before, timeout=300)
    answer = st.page.get_by_text(re.compile(r"service-agreement")).last
    st.cue(1, focus=answer)
    time.sleep(4.0)                           # the line about the answer, over the answer
    up = assistant_rows(st).last
    st.cue(2, focus=up)
    st.glide_click(up, dur=0.9)
    st.cursor.glide(1500, 700, dur=0.6)       # off the note and its tooltip
    note = st.page.get_by_text(re.compile(r"memor(y|ies) credited|recipe .+ up|reinforced"))
    try:
        note.first.wait_for(state="visible", timeout=8_000)
        _TAUGHT["texts"].append(note.first.inner_text())
    except Exception:
        pass
    time.sleep(3.5)
    # Second click on the lit thumb withdraws it.
    lit = st.page.locator("button:has([data-testid='ThumbUpIcon'])").last
    st.cue(3, focus=lit)
    st.glide_click(lit, dur=0.7)
    st.cursor.glide(1500, 700, dur=0.6)
    withdrawn = st.page.get_by_text("feedback withdrawn")
    try:
        withdrawn.first.wait_for(state="visible", timeout=8_000)
        _TAUGHT["texts"].append(withdrawn.first.inner_text())
    except Exception:
        pass
    time.sleep(3.0)


def v_teach(st: Stage):
    print(f"  taught notes: {_TAUGHT['texts']}")
    require(len(_TAUGHT["texts"]) == 2,
            f"expected a taught note and a withdrawal, saw {_TAUGHT['texts']}")
    verify_path(st, "/chat")


# -------------------------------------------------------------- beat: web

_WEB = {"reply": ""}


def reset_web(st: Stage):
    fresh_chat(st)
    _WEB["reply"] = ""


def act_web(st: Stage):
    before = assistant_rows(st).count()
    st.cue(1)
    ask(st, WEB_ASK, delay_ms=18)
    with st.fast_forward():
        wait_turn(st, before)
    card = st.page.get_by_text("fetch_url").last
    st.cue(2, focus=card)
    st.hover_over(card, dur=0.8)
    time.sleep(2.0)
    answer = st.page.get_by_text(re.compile(r"termite", re.IGNORECASE)).last
    st.cue(3, focus=answer)
    time.sleep(3.0)
    _WEB["reply"] = last_reply_text(st)


def v_web(st: Stage):
    reply = _WEB["reply"].lower()
    print(f"  web reply: {_WEB['reply'][:200]!r}")
    require("ant" in reply and "termite" in reply, "the reply does not say what the page says it eats")
    require(st.page.get_by_text("fetch_url").count() > 0, "no fetch_url tool card on screen")
    verify_path(st, "/chat")


# ------------------------------------------------------------ beat 2: photo

def reset_photo(st: Stage):
    fresh_chat(st)
    require(CAFE.exists(), f"missing asset {CAFE.name}")
    ed = api_get("/api/batch-image/models").get("editing", [])
    require(any(p.get("installed") for p in ed), "no image-editing pack installed")


def act_photo(st: Stage):
    attach(st, CAFE)
    for k, text in enumerate((EDIT_ASK, OUTPAINT_ASK, CUTOUT_ASK)):
        before = chat_images(st).count()
        st.cue(1 + k)
        ask(st, text)
        with st.fast_forward():
            wait_new_image(st, before, expect=2 if k == 0 else 1)
        st.focus_on(chat_images(st).last, hold=3.0)
        st.hover_over(chat_images(st).last, dur=0.9)
        time.sleep(2.5)
    st.cue(4)
    time.sleep(1.0)


def v_photo(st: Stage):
    require(chat_images(st).count() >= 4, "expected the upload plus three results")
    verify_path(st, "/chat")


# ---------------------------------------------------------- beat 3: consent

CONSENT_DIR = REPO / "data" / "outputs" / "consent"


def _thumb(path):
    from PIL import Image
    with Image.open(path) as im:
        return list(im.convert("L").resize((32, 32)).getdata())


def forget_demo_consent() -> int:
    """Drop consent recorded for the demo portrait by an earlier take, so the
    card is on camera every take. Matched by pixels, not hash: the upload
    re-encodes the file. Only records whose image is the demo portrait go."""
    want = _thumb(PORTRAIT)
    removed = 0
    for rec in CONSENT_DIR.glob("*.consent"):
        try:
            img = Path(json.loads(rec.read_text()).get("path", ""))
            if not img.is_file():
                continue
            got = _thumb(img)
        except Exception:
            continue
        if sum(abs(a - b) for a, b in zip(want, got)) / len(want) < 4:
            rec.unlink()
            Path(str(img) + ".consent").unlink(missing_ok=True)
            removed += 1
    return removed


def reset_consent(st: Stage):
    fresh_chat(st)
    require(PORTRAIT.exists(), f"missing asset {PORTRAIT.name}")
    print(f"  consent records for the demo portrait removed: {forget_demo_consent()}")


def act_consent(st: Stage):
    attach(st, PORTRAIT)
    before = chat_images(st).count()
    ask(st, IDENTITY_ASK)
    card = st.page.locator("[data-testid='consent-approval-card']").last
    card.wait_for(state="visible", timeout=120_000)
    st.cue(1)
    # The card is part of the real product, so it is on screen; the episode
    # does not dwell on it (Dean's Ep 19 review).
    st.glide_click(card.get_by_role("button", name="I have the right to use this likeness"), dur=0.8)
    with st.fast_forward():
        wait_new_image(st, before, expect=2, timeout=600)
    st.cue(2, focus=chat_images(st).last)
    st.hover_over(chat_images(st).last, dur=0.9)
    time.sleep(3.0)


def v_consent(st: Stage):
    verify_path(st, "/chat")


# -------------------------------------------------------------- beat 4: mcp

_MCP = {"tools": None}


def reset_mcp(st: Stage):
    close_dialogs(st)
    _MCP["reply"] = ""
    rq.post(f"{API}/api/automation/mcp/disconnect", json={"server": MCP_SERVER}, timeout=20)
    set_nav_chrome(st, "software", path="/agents/mcp")
    st.page.get_by_text(MCP_SERVER, exact=True).first.wait_for(state="visible", timeout=30_000)
    check_scale(st)


def act_mcp(st: Stage):
    row = st.page.locator("tr").filter(has_text=MCP_SERVER).first
    st.cue(1, focus=row)
    st.glide_click(row.get_by_role("button", name="Connect"), dur=0.9)
    st.page.get_by_text("connected", exact=True).first.wait_for(state="visible", timeout=60_000)
    time.sleep(1.0)
    st.cue(2)
    st.glide_click(row.get_by_role("button", name="Tools"), dur=0.8)
    time.sleep(0.8)
    st.focus_on(st.page.get_by_text("zvec_grep_search", exact=True).first, hold=3.5)
    time.sleep(4.2)
    close_dialogs(st)
    time.sleep(1.0)
    # Into chat on camera, through the top bar.
    open_workspace(st, "Chat", "/chat")
    chat_box(st).wait_for(state="visible", timeout=30_000)
    new_chat(st)
    before = assistant_rows(st).count()
    st.cue(3)
    ask(st, MCP_ASK, delay_ms=18)
    approve = st.page.locator("[data-testid='tool-approval-card']").last
    deadline = time.monotonic() + 120
    with st.fast_forward():
        while time.monotonic() < deadline:
            if approve.count() and approve.is_visible():
                st.glide_click(approve.get_by_role("button").first, dur=0.7)
                break
            if assistant_rows(st).count() > before and chat_box(st).is_enabled():
                break
            time.sleep(0.5)
        wait_turn(st, before)
    answer = st.page.get_by_text(re.compile(r"web_tools\.py|web_search_api")).last
    st.cue(4, focus=answer)
    time.sleep(3.5)
    _MCP["reply"] = last_reply_text(st)


def v_mcp(st: Stage):
    body = rq.get(f"{API}/api/automation/mcp/servers", timeout=10).json()
    srv = [s for s in body.get("servers", []) if s.get("name") == MCP_SERVER]
    require(srv and srv[0].get("connected"), f"{MCP_SERVER} not connected")
    _MCP["tools"] = srv[0].get("tool_count")
    print(f"  mcp reply: {_MCP.get('reply', '')[:200]!r}")
    require(re.search(r"web_tools\.py|web_search_api", _MCP.get("reply", "")), "the reply does not name the file the tool found")
    verify_no_private_names(st)


# ----------------------------------------------------------- beat: models

_MODELS = {"ok": "", "no": ""}


def reset_models(st: Stage):
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/video")
    st.page.get_by_role("button", name="Manage models").first.wait_for(state="attached", timeout=30_000)
    check_scale(st)
    _MODELS.update(ok="", no="")


def _look_up(st: Stage, dlg, url: str):
    box = dlg.get_by_label("Hugging Face URL")
    st.glide_click(box, dur=0.6)
    box.fill("")
    box.press_sequentially(url, delay=12)
    time.sleep(0.4)
    st.glide_click(dlg.get_by_role("button", name="Look up"), dur=0.6)


def act_models(st: Stage):
    manage = st.page.get_by_role("button", name="Manage models").first
    manage.scroll_into_view_if_needed()
    time.sleep(0.8)
    st.cue(1, focus=manage)
    st.glide_click(manage, dur=0.8)
    st.page.get_by_role("button", name="Add new model").first.wait_for(state="visible", timeout=20_000)
    time.sleep(1.0)
    st.glide_click(st.page.get_by_role("button", name="Add new model").first, dur=0.8)
    dlg = st.page.locator("[role=dialog]").filter(has_text="Hugging Face URL").last
    dlg.wait_for(state="visible", timeout=10_000)
    st.cue(2)
    _look_up(st, dlg, HF_OK)
    licence = dlg.get_by_text(re.compile(r"licence apache-2.0"))
    licence.wait_for(state="visible", timeout=60_000)
    st.cue(3, focus=licence)
    time.sleep(3.0)
    _MODELS["ok"] = dlg.inner_text()
    _look_up(st, dlg, HF_NO)
    refusal = dlg.get_by_text(re.compile(r"cannot load"))
    refusal.wait_for(state="visible", timeout=60_000)
    st.cue(4, focus=refusal)
    time.sleep(3.0)
    _MODELS["no"] = dlg.inner_text()
    st.glide_click(dlg.get_by_role("button", name="Cancel"), dur=0.6)
    time.sleep(0.8)
    close_dialogs(st)


def v_models(st: Stage):
    ok, no = _MODELS["ok"], _MODELS["no"]
    require("LTX" in ok and "apache-2.0" in ok, f"the look-up did not name the family and licence: {ok[:300]!r}")
    require("Mochi" in no and "cannot load" in no, f"Mochi was not refused by name: {no[:300]!r}")
    verify_path(st, "/video")
    verify_no_private_names(st)


# ------------------------------------------------------------ beat: video

_VIDEO = {"before": set(), "batch": None, "clip": None, "heard": "", "comfy_before": None}
PROMPT_LABEL = "What do you want to see? (one prompt per line)"


def _batches() -> list:
    return rq.get(f"{API}/api/batch-video/list", timeout=10).json()["data"]["batches"]


def reset_video(st: Stage):
    close_dialogs(st)
    # ComfyUI off before the take: the take shows the render starting it.
    rq.post(f"{API}/api/plugins/comfyui/stop", timeout=120)
    for _ in range(60):
        if plugin_status("comfyui") != "running":
            break
        time.sleep(1)
    _VIDEO.update(before={b["batch_id"] for b in _batches()}, batch=None, clip=None, heard="",
                  comfy_before=plugin_status("comfyui"))
    require(_VIDEO["comfy_before"] != "running", "ComfyUI did not stop")
    _beat("video").audio_overlays.clear()
    set_nav_chrome(st, "software", path="/video")
    st.page.get_by_label(PROMPT_LABEL).wait_for(state="visible", timeout=30_000)
    model = st.page.get_by_role("combobox").filter(
        has_text=re.compile(r"Wan|LTX|MiniMax|Hunyuan|CogVideo")).first
    model.click()
    st.page.get_by_role("option").filter(has_text=H3_LABEL).first.click()
    time.sleep(1.5)
    require(H3_LABEL in model.inner_text(), f"model picker shows {model.inner_text()!r}")
    st.page.evaluate("() => window.scrollTo(0, 0)")
    time.sleep(1.0)
    check_scale(st)


def _clip_audio(clip: Path) -> Path:
    wav = ASSETS / "h3_clip_audio.wav"
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vn", "-ac", "2", "-ar", "48000",
                    str(wav)], check=True)
    return wav


def act_video(st: Stage):
    strip = st.page.get_by_text(re.compile(r"^MiniMax H3")).first
    st.cue(0, focus=strip)
    time.sleep(1.5)
    box = st.page.get_by_label(PROMPT_LABEL)
    st.cue(1, focus=box)
    st.glide_click(box, dur=0.7)
    # Word by word: this page re-renders on every key, and per-key typing of
    # the prompt ran 27 s of silence on camera.
    for k, word in enumerate(VIDEO_ASK.split(" ")):
        st.page.keyboard.insert_text(("" if k == 0 else " ") + word)
        time.sleep(0.06)
    time.sleep(0.6)
    add = st.page.get_by_role("button", name="Add to queue").first
    st.cue(2, focus=add)
    st.glide_click(add, dur=0.8)
    st.cursor.glide(1500, 700, dur=0.6)
    deadline = time.monotonic() + 900
    with st.fast_forward():
        while time.monotonic() < deadline:
            new = [b for b in _batches() if b["batch_id"] not in _VIDEO["before"]]
            if new and new[0]["status"] in ("completed", "error", "cancelled", "failed"):
                _VIDEO["batch"] = new[0]
                break
            time.sleep(2.0)
        time.sleep(3.0)
    require(_VIDEO["batch"] and _VIDEO["batch"]["status"] == "completed", f"render ended {_VIDEO['batch']}")
    status = api_get(f"/api/batch-video/status/{_VIDEO['batch']['batch_id']}")
    rel = next(r["video_path"] for r in status.get("results", []) if r.get("success"))
    clip = REPO / "data" / "uploads" / "Videos" / _VIDEO["batch"]["batch_id"] / rel
    _VIDEO["clip"] = clip
    wav = _clip_audio(clip)
    from director import ffprobe_duration
    clip_s = ffprobe_duration(clip)
    # The clip's own voice plays in a gap: the line before it has finished.
    plan, hits = getattr(st, "cue_plan", None), getattr(st, "cue_hits", {})
    if plan and 2 in hits:
        end2 = hits[2] + plan[2]["dur"] + 0.3
        while st.out_clock() < end2:
            time.sleep(0.05)
    play = st.page.get_by_role("button", name="Play").first
    play.scroll_into_view_if_needed()
    time.sleep(0.5)
    st.hover_over(play, dur=0.7)
    st.cursor.click()
    video = st.page.locator("video").last
    for _ in range(40):
        if video.count() and not video.evaluate("v => v.paused"):
            break
        time.sleep(0.1)
    started = st.out_clock()
    if getattr(st, "recorder", None) is not None:
        _beat("video").audio_overlays.append((str(wav), started, 1.0))
    st.focus_on(video, hold=clip_s)
    st.cursor.glide(1500, 1000, dur=0.6)
    time.sleep(clip_s + 0.6)
    st.cue(3, focus=video)
    time.sleep(3.0)


def v_video(st: Stage):
    require(_VIDEO["comfy_before"] != "running", "ComfyUI was running before the take")
    require(plugin_status("comfyui") == "running", "ComfyUI is not running after the render")
    clip = _VIDEO["clip"]
    require(clip and clip.exists(), f"no clip on disk: {clip}")
    import subprocess
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                              str(clip)], capture_output=True, text=True).stdout.split()
    require("audio" in streams, "the clip has no soundtrack")
    from director import _stt
    heard = _stt(ASSETS / "h3_clip_audio.wav").lower()
    _VIDEO["heard"] = heard
    print(f"  clip says: {heard!r}")
    require(all(w in heard for w in VIDEO_LINE), f"the clip does not say the line: {heard!r}")
    verify_path(st, "/video")
    verify_no_private_names(st)


def _beat(name: str) -> Beat:
    return next(b for b in BEATS if b.name == name)


def spoken_version(v: str) -> str:
    return " point ".join(v.split("."))


BEATS = [
    # Plain words throughout: a first-time viewer with no background in the
    # project must follow every line. Numbers in comments are the spoken-line
    # indexes the actions cue on (blank lines do not count).
    # GPU order: chat beats (Ollama), the models look-up (no GPU), then the
    # ComfyUI beats, the video last because its reset stops ComfyUI.
    Beat(
        name="teach",
        narration=[
            "Guard-vark two point nine. Here is what's new.",                       # 0
            "Ask about your own files, and it answers from them, and names the "
            "file it used.",                                                        # 1
            "Like an answer? Give it a thumbs up. Guard-vark keeps track of what "
            "helped, and uses it next time.",                                       # 2
            "Change your mind? Click it again, and that is undone.",                # 3
        ],
        action=act_teach, verify=v_teach, reset=reset_teach,
    ),
    Beat(
        name="web",
        narration=[
            "It can read web pages for you, too.",                                  # 0
            "Ask your question, and give it the page.",                             # 1
            "It opens the page, and finds the part that answers you.",              # 2
            "Ants and termites. Straight from the page.",                           # 3
        ],
        action=act_web, verify=v_web, reset=reset_web,
    ),
    Beat(
        name="mcp",
        narration=[
            "Guard-vark can also use tools from other programs, through M C P, "
            "a common way for A I apps to share tools.",                            # 0
            "Pick one, and click connect.",                                         # 1
            "Its tools show up right here.",                                        # 2
            "Then just ask. This one searches the code of this very project.",      # 3
            "It found the file that reads web pages, and says where it is.",        # 4
        ],
        action=act_mcp, verify=v_mcp, reset=reset_mcp,
    ),
    Beat(
        name="models",
        narration=[
            "Found a new video model online? You can add it yourself.",             # 0
            "Open Manage models, and choose add new model.",                        # 1
            "Paste its link from Hugging Face.",                                    # 2
            "Guard-vark checks what it is, its size and its license, before "
            "anything downloads.",                                                  # 3
            "And if it can't run a model, it tells you why, instead of "
            "downloading it for nothing.",                                          # 4
        ],
        action=act_models, verify=v_models, reset=reset_models,
    ),
    Beat(
        name="photo",
        narration=[
            "You can edit photos right in the chat, too.",                          # 0
            "Just say what you want. Make it night.",                               # 1
            "Make it wider.",                                                       # 2
            "Take out the background.",                                             # 3
            "Every edit ran right here, on this computer. We sped up the waiting.", # 4
        ],
        action=act_photo, verify=v_photo, reset=reset_photo,
    ),
    Beat(
        name="identity",
        narration=[
            "Got a photo of someone? Put them in any scene, and keep their face.",  # 0
            "This woman is not real. We made her on this computer for the demo.",   # 1
            "Same face. New place.",                                                # 2
        ],
        action=act_consent, verify=v_consent, reset=reset_consent,
    ),
    Beat(
        name="video",
        narration=[
            "And the big one. Video, with its own sound, from MiniMax H3.",         # 0
            "Describe the scene, and what the character says.",                     # 1
            "Add it to the queue. The video engine was switched off, so "
            "Guard-vark starts it for you.",                                        # 2
            "Picture, voice and kitchen sounds, all made on this computer.",        # 3
        ],
        action=act_video, verify=v_video, reset=reset_video,
    ),
]


ASSETS_OUT = REPO / "data" / "demo_assets" / "ep19"
FINAL = REPO / "data" / "outputs" / "demos" / "EP19_FINAL.mp4"


PLATES = REPO / "data" / "demo_assets" / "launch" / "plates" / "final_plates.json"
RESULTS = REPO / "data" / "outputs" / "generated_images"


def _newest(prefix: str, since: float) -> str | None:
    found = sorted((p for p in RESULTS.glob(f"{prefix}_*.png") if p.stat().st_mtime >= since),
                   key=lambda p: p.stat().st_mtime)
    return str(found[-1]) if found else None


def _newest_clip(since: float) -> str | None:
    """The H3 clip this run rendered: the one the video beat saw, else the
    newest MiniMax clip with a soundtrack written since the run started."""
    if _VIDEO.get("clip") and Path(_VIDEO["clip"]).exists():
        return str(_VIDEO["clip"])
    found = sorted((p for p in (REPO / "data" / "uploads" / "Videos").glob("*/*/videos/minimax_h3_*-audio.mp4")
                    if p.stat().st_mtime >= since), key=lambda p: p.stat().st_mtime)
    return str(found[-1]) if found else None


def finish(ep: Episode, body: Path) -> Path:
    """Cold open, the beats with movement, a montage ending on an animated title
    and a narrated sign-off: assets/finish.py, rendered from a Shotcut project."""
    import subprocess
    from director import generate_narration
    tools = Path(__file__).resolve().parents[1] / "assets"
    opener = ASSETS_OUT / "coldopen.mp4"
    if not opener.exists():
        subprocess.run([sys.executable, str(tools / "coldopen.py"), str(opener),
                        "GUAARDVARK 2.9", "WHAT'S NEW"], check=True)
    signoff = ep.dir / "signoff.wav"
    # "Free and open source" is on screen in the call to action; spoken, it held
    # the title for eight seconds.
    generate_narration([f"Guard-vark {spoken_version(N['version'])}.",
                        "One machine, no cloud."], signoff)
    plates = {k: v["clip"] for k, v in json.loads(PLATES.read_text()).items()}
    # The run started when its folder was named (slug_YYYYmmdd_HHMMSS); the
    # folder's mtime moves with every file written into it. Takes reused from
    # an earlier run (DEMO_RESUME_DIR) made their results when that run did.
    first = Path(os.environ.get("DEMO_RESUME_DIR") or ep.dir).name
    since = time.mktime(time.strptime(first[-15:], "%Y%m%d_%H%M%S"))
    results = [_newest("edit", since), _newest("outpaint", since), _newest("nobg", since),
               _newest("identity", since), _newest_clip(since)]
    require(all(results), f"missing a result for the ending: {results}")
    shots = [
        {"src": plates["01_one_machine"], "beats": 2, "move": "in"},
        {"src": results[0], "beats": 1, "move": "left"},
        {"src": plates["02_fifteen_skills"], "beats": 1, "move": "in"},
        {"src": results[4], "beats": 3, "move": "in"},
        {"src": results[1], "beats": 1, "move": "right"},
        {"src": plates["05_nothing_leaves"], "beats": 1, "move": "out"},
        {"src": results[2], "beats": 1, "move": "in"},
        {"src": plates["07_film_crew"], "beats": 1, "move": "left"},
        {"src": results[3], "beats": 2, "move": "in"},
    ]
    spec = ep.dir / "outro_shots.json"
    spec.write_text(json.dumps({"shots": shots, "title_bg": plates["13_loop_rain"]}, indent=1))
    subprocess.run([sys.executable, str(tools / "finish.py"), str(ep.dir), str(FINAL),
                    "--open", str(opener), "--shots", str(spec),
                    "--title", f"GUAARDVARK {N['version']}",
                    "--tagline", "ONE MACHINE. NO CLOUD.",
                    "--cta", "Free and open source  ·  guaardvark.com",
                    "--signoff", str(signoff)], check=True)
    return FINAL


def main():
    require(N["version"], "no version from /api/health")
    ep = Episode("ep19_whatsnew29", BEATS, out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        for warm in ("/", "/chat", "/agents/mcp", "/video"):
            st_goto(stage, warm)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        body = ep.produce(stage)
    finally:
        stage.close()
    print(f"\nEP19 COMPLETE: {finish(ep, Path(body))}")


if __name__ == "__main__":
    main()
