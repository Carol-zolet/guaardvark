"""Privacy staging for a shoot: put the clone in its on-camera state and back.

    venv/bin/python staging.py status
    venv/bin/python staging.py up      # before the first take
    venv/bin/python staging.py down    # after the last take

`up` records what it changed in a state file under docs/local-workspace-only/,
and `down` reverses exactly that, so the restore never depends on memory:

- the recording display (Xvfb on DEMO_DISPLAY, default :98) is started;
- the system name is set to the public product name (the top bar prints it);
- every extension this clone does not track in git is parked under an
  underscore name, which neither the frontend glob nor the backend loader reads;
- with DEMO_CHAT_MODEL set, the active chat model is switched to it for the
  shoot and switched back on `down`.

Parking or unparking changes what the backend and Vite load, so both need a
restart afterwards. This script says so and stops there: restarts are a
separate, deliberate step, taken after checking for work in flight.

Extension names are never printed; counts only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests as rq

REPO = Path(__file__).resolve().parents[2]
API = os.environ.get("GUAARDVARK_API", "http://localhost:5000")
DISPLAY = os.environ.get("DEMO_DISPLAY", ":98")
PUBLIC_NAME = "Guaardvark"
EXT_DIR = REPO / "extensions"
STATE_FILE = REPO / "docs" / "local-workspace-only" / "demo_staging_state.json"
XVFB_LOG = REPO / "docs" / "local-workspace-only" / "demo_xvfb.log"


def display_up() -> bool:
    return Path(f"/tmp/.X11-unix/X{DISPLAY.lstrip(':')}").exists()


def system_name() -> str:
    r = rq.get(f"{API}/api/settings/branding", timeout=10)
    r.raise_for_status()
    return r.json()["data"].get("system_name") or ""


def set_system_name(name: str):
    r = rq.post(f"{API}/api/settings/branding", data={"system_name": name}, timeout=10)
    r.raise_for_status()


def chat_model() -> str:
    r = rq.get(f"{API}/api/model/status", timeout=10)
    r.raise_for_status()
    return r.json()["data"].get("text_model") or ""


def set_chat_model(name: str):
    r = rq.post(f"{API}/api/model/set", json={"model": name}, timeout=30)
    r.raise_for_status()
    for _ in range(60):
        if chat_model() == name:
            return
        time.sleep(1)
    raise RuntimeError(f"chat model did not switch to {name}")


def tracked_extensions() -> set[str]:
    out = subprocess.run(["git", "ls-files", "extensions"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    return {Path(p).parts[1] for p in out.splitlines() if len(Path(p).parts) > 2}


def private_extensions() -> tuple[list[Path], list[Path]]:
    """(live, parked): untracked extension folders carrying an extension.json."""
    tracked = tracked_extensions()
    live, parked = [], []
    for p in sorted(EXT_DIR.iterdir()):
        if not p.is_dir() or p.name in tracked or p.name.lstrip("_") in tracked:
            continue
        if not (p / "extension.json").exists():
            continue
        (parked if p.name.startswith("_") else live).append(p)
    return live, parked


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def status() -> int:
    live, parked = private_extensions()
    name = system_name()
    print(f"display {DISPLAY}: {'up' if display_up() else 'down'}")
    print(f"system name public: {name == PUBLIC_NAME}")
    print(f"private extensions: {len(live)} loaded, {len(parked)} parked")
    print(f"chat model: {chat_model()}")
    st = load_state()
    print(f"staged by this script: {bool(st)}"
          + (f" (since {st.get('at')})" if st else ""))
    ready = display_up() and name == PUBLIC_NAME and not live
    print("READY TO SHOOT" if ready else "NOT READY")
    return 0 if ready else 1


def up() -> int:
    st = load_state()
    if st:
        print(f"already staged (since {st.get('at')}); run `down` first or shoot")
        return status()
    st = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "display_started": False,
          "old_system_name": None, "parked": []}
    if not display_up():
        XVFB_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(XVFB_LOG, "ab") as log:
            subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "1920x1080x24",
                              "-nolisten", "tcp"], stdout=log, stderr=log,
                             start_new_session=True)
        for _ in range(20):
            if display_up():
                break
            time.sleep(0.25)
        st["display_started"] = display_up()
    name = system_name()
    if name != PUBLIC_NAME:
        st["old_system_name"] = name
        set_system_name(PUBLIC_NAME)
    want = os.environ.get("DEMO_CHAT_MODEL")
    if want and chat_model() != want:
        st["old_chat_model"] = chat_model()
        set_chat_model(want)
    live, _ = private_extensions()
    for p in live:
        target = p.with_name("_" + p.name)
        if target.exists() or target.is_symlink():
            print("an extension's parked name is already taken; left it loaded")
            continue
        p.rename(target)
        st["parked"].append(target.name)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=2))
    if st["parked"]:
        print(f"parked {len(st['parked'])} extension(s): restart Vite and the backend "
              "before the first take (check for running jobs first)")
    return status()


def down() -> int:
    st = load_state()
    if not st:
        print("nothing staged by this script; nothing to restore")
        return status()
    for parked in st.get("parked", []):
        src = EXT_DIR / parked
        dst = EXT_DIR / parked[1:]
        if dst.exists() or dst.is_symlink():
            print("an extension's live name is taken; left it parked")
            continue
        if src.exists() or src.is_symlink():
            src.rename(dst)
    if st.get("old_chat_model"):
        set_chat_model(st["old_chat_model"])
    if st.get("old_system_name") is not None:
        set_system_name(st["old_system_name"])
    if st.get("display_started"):
        # Only the Xvfb this script started: match its exact display argument.
        subprocess.run(["pkill", "-f", f"^Xvfb {DISPLAY} "], check=False)
    STATE_FILE.unlink()
    if st.get("parked"):
        print(f"unparked {len(st['parked'])} extension(s): restart Vite and the backend "
              "to load them again (check for running jobs first)")
    status()
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    sys.exit({"status": status, "up": up, "down": down}[cmd]())
