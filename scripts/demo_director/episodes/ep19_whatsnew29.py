"""Episode 19 — Everything New in 2.9 (≈5:00).

What landed since Ep 13, shown on the real product: thumbs that say what they
taught, photo editing inside chat (edit, outpaint, cut-out), a consent card in
front of any likeness, the MCP client connecting an outside server and chat
calling it, and a closer on the running version.

Every countable thing is read when this file loads or checked in `verify`:
the version from /api/health, the MCP server's tool count from the connect
response, the "taught" note from what the page rendered.

GPU cast: Ollama (chat beats) + Audio Foundry (narration); ComfyUI for the
photo beats (Qwen-Image-Edit pack installed). One heavy service per take:
the photo beats run after the chat beats.

Assets (made on this box, no real person): data/demo_assets/ep19/
cafe_street.png and portrait_fictional.png, Z-Image renders.

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
    REPO, api_get, close_dialogs, goto as st_goto, require, set_nav_chrome,
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
    time.sleep(2.0)
    up = assistant_rows(st).last
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
    st.glide_click(st.page.locator("button:has([data-testid='ThumbUpIcon'])").last, dur=0.7)
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
        ask(st, text)
        with st.fast_forward():
            wait_new_image(st, before, expect=2 if k == 0 else 1)
        st.hover_over(chat_images(st).last, dur=0.9)
        time.sleep(2.5)


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
    st.hover_over(card, dur=0.9)
    time.sleep(3.0)
    st.glide_click(card.get_by_role("button", name="I have the right to use this likeness"), dur=0.8)
    with st.fast_forward():
        wait_new_image(st, before, expect=2, timeout=600)
    st.hover_over(chat_images(st).last, dur=0.9)
    time.sleep(3.0)


def v_consent(st: Stage):
    verify_path(st, "/chat")


# -------------------------------------------------------------- beat 4: mcp

_MCP = {"tools": None}


def reset_mcp(st: Stage):
    close_dialogs(st)
    rq.post(f"{API}/api/automation/mcp/disconnect", json={"server": MCP_SERVER}, timeout=20)
    set_nav_chrome(st, "software", path="/agents/mcp")
    st.page.get_by_text(MCP_SERVER, exact=True).first.wait_for(state="visible", timeout=30_000)
    check_scale(st)


def act_mcp(st: Stage):
    row = st.page.locator("tr").filter(has_text=MCP_SERVER).first
    st.glide_click(row.get_by_role("button", name="Connect"), dur=0.9)
    st.page.get_by_text("connected", exact=True).first.wait_for(state="visible", timeout=60_000)
    time.sleep(2.5)
    st.glide_click(row.get_by_role("button", name="Tools"), dur=0.8)
    time.sleep(5.0)
    close_dialogs(st)
    time.sleep(1.5)


def v_mcp(st: Stage):
    body = rq.get(f"{API}/api/automation/mcp/servers", timeout=10).json()
    srv = [s for s in body.get("servers", []) if s.get("name") == MCP_SERVER]
    require(srv and srv[0].get("connected"), f"{MCP_SERVER} not connected")
    _MCP["tools"] = srv[0].get("tool_count")
    verify_no_private_names(st)


def spoken_version(v: str) -> str:
    return " point ".join(v.split("."))


BEATS = [
    Beat(
        name="teach",
        narration=[
            "Everything since episode thirteen. Start with the smallest button "
            "in the product.",
            "",
            "A thumb used to be a vote. Now it says what it taught.",
            "The memories that built this reply get the credit. Click it again, "
            "and it is taken back.",
        ],
        action=act_teach, verify=v_teach, reset=reset_teach,
    ),
    Beat(
        name="photo",
        narration=[
            "Drop a photo into chat, and say what you want.",
            "",
            "Night. Wider. Background gone.",
            "Three edits, each one rendered on this card, in the same chat "
            "that answers your questions. The waits are sped up; the counter "
            "in the corner says by how much.",
        ],
        action=act_photo, verify=v_photo, reset=reset_photo,
    ),
    Beat(
        name="consent",
        narration=[
            "Put a real face in a new scene, and it stops first.",
            "",
            "Nothing renders until you say you have the right to use that "
            "likeness. This face was generated on this machine for the demo.",
        ],
        action=act_consent, verify=v_consent, reset=reset_consent,
    ),
    Beat(
        name="mcp",
        narration=[
            "Episode sixteen plugged Guard-vark into other agents. This is the "
            "other direction.",
            "",
            "Point it at any tool server that speaks the protocol. Connect, "
            "and its tools join Guard-vark's own, under the same rules.",
        ],
        action=act_mcp, verify=v_mcp, reset=reset_mcp,
    ),
]


ASSETS_OUT = REPO / "data" / "demo_assets" / "ep19"
FINAL = REPO / "data" / "outputs" / "demos" / "EP19_FINAL.mp4"


def finish(ep: Episode, body: Path) -> Path:
    """Cold open + the recorded episode + a narrated end card."""
    import subprocess
    from director import generate_narration
    tool = str(Path(__file__).resolve().parents[1] / "assets" / "coldopen.py")
    opener = ASSETS_OUT / "coldopen.mp4"
    if not opener.exists():
        subprocess.run([sys.executable, tool, str(opener), "GUAARDVARK 2.9", "WHAT'S NEW"],
                       check=True)
    wav = ep.dir / "endcard.wav"
    generate_narration([f"Guard-vark {spoken_version(N['version'])}.",
                        "One machine. No cloud."], wav)
    end = ep.dir / "endcard.mp4"
    subprocess.run([sys.executable, tool, "--endcard", str(end), str(wav),
                    "ONE MACHINE. NO CLOUD.", f"GUAARDVARK {N['version']}"], check=True)
    bedded = ep.dir / "body_bed.mp4"
    subprocess.run([sys.executable, tool, "--bed", str(body), str(bedded)], check=True)
    subprocess.run([sys.executable, tool, "--join", str(FINAL), str(opener), str(bedded),
                    str(end)], check=True)
    return FINAL


def main():
    require(N["version"], "no version from /api/health")
    ep = Episode("ep19_whatsnew29", BEATS, out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        for warm in ("/", "/chat", "/agents/mcp"):
            st_goto(stage, warm)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        body = ep.produce(stage)
    finally:
        stage.close()
    print(f"\nEP19 COMPLETE: {finish(ep, Path(body))}")


if __name__ == "__main__":
    main()
