#!/usr/bin/env python3
"""
Media Player Service - MPRIS2 control via gdbus, VLC launch, and music file search.
"""

import fcntl
import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MEDIA_CONTROL_ENABLED = os.getenv("GUAARDVARK_MEDIA_CONTROL", "true").lower() == "true"

MUSIC_EXTENSIONS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".opus", ".wma"}

VLC_PATH = "/snap/bin/vlc"

MPRIS2_BUS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS2_OBJECT_PATH = "/org/mpris/MediaPlayer2"
MPRIS2_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
MPRIS2_PROPS_IFACE = "org.freedesktop.DBus.Properties"

# A GVariant string as gdbus prints it: single-quoted, or double-quoted when the
# text itself contains a single quote ("Don't Stop"), with backslash escapes.
_GV_STRING = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""

# GLib writes control characters as \n \t \r \a \b \f \v, other unprintable
# characters as \uXXXX or \UXXXXXXXX, and puts a backslash before \ ' and ".
_GV_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}
_GV_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|.)", re.DOTALL)


def _gv_unescape(raw: str) -> str:
    def one(match: "re.Match") -> str:
        esc = match.group(1)
        if len(esc) > 1:
            code = int(esc[1:], 16)
            return chr(code) if code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else "�"
        return _GV_ESCAPES.get(esc, esc)
    return _GV_ESCAPE.sub(one, raw)


def _gv_text(match: "re.Match", first_group: int = 1) -> str:
    raw = match.group(first_group) if match.group(first_group) is not None else match.group(first_group + 1)
    return _gv_unescape(raw or "")


def _vlc_pidfile() -> Path:
    """Where the VLC that Guaardvark started is recorded as "<pid> <starttime>",
    shared by the chat and MCP processes so either can replace it without touching
    other players. It lives in the git-ignored cache folder."""
    try:
        from backend.config import CACHE_DIR
        base = Path(CACHE_DIR)
    except Exception:
        base = Path(__file__).resolve().parents[2] / "data" / "cache"
    return base / "media" / "vlc.pid"


@contextmanager
def _vlc_launch_lock():
    """Serialise replacing and launching VLC across threads and processes, so two
    plays at once (chat and MCP) cannot both start a VLC and record only one."""
    lock_path = _vlc_pidfile().with_name("vlc.lock")
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a")
        fcntl.flock(handle, fcntl.LOCK_EX)
    except OSError as e:
        logger.debug(f"Could not take the VLC launch lock: {e}")
    try:
        yield
    finally:
        if handle:
            handle.close()


def _proc_stat(pid: int) -> Optional[Tuple[str, str]]:
    """(state, starttime) of a process, from fields 3 and 22 of /proc/<pid>/stat,
    or None when there is no such process. Field 2 (the command name) may contain
    spaces and ')', so the fields are counted from after the last ')'."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = stat[stat.rfind(")") + 1:].split()
    if len(fields) < 20:
        return None
    return fields[0], fields[19]


def _proc_is_vlc(pid: int) -> bool:
    """True when the process's executable or argv[0] is named exactly 'vlc'."""
    names = []
    try:
        names.append(os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)"))
    except OSError:
        pass
    try:
        argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0", 1)[0]
        names.append(argv0.decode(errors="replace"))
    except OSError:
        pass
    return any(os.path.basename(name) == "vlc" for name in names)


def _proc_gone(pid: int, started: str) -> bool:
    """True when the process recorded with this start time has exited (a zombie
    counts as exited) or its PID now belongs to another process."""
    stat = _proc_stat(pid)
    return stat is None or stat[1] != started or stat[0] in ("Z", "X")


# Top-level folders that hold the system rather than music. /run/media (removable
# drives) is allowed, and so is everything inside the home folder, which some
# systems keep under /var (/var/home).
_SYSTEM_TOP_FOLDERS = {"etc", "proc", "sys", "dev", "boot", "root", "run", "var", "usr", "bin", "sbin"}


def _folder_refusal(folder: Path) -> Optional[str]:
    """Why a resolved absolute folder may not be played, or None when it may."""
    home = Path.home().resolve()
    if home != Path("/") and (folder == home or home in folder.parents):
        inside = folder.relative_to(home).parts
    else:
        inside = folder.parts[1:]
        if not inside:
            return "is the root folder"
        top = inside[0]
        if (top in _SYSTEM_TOP_FOLDERS or top.startswith("lib")) and inside[:2] != ("run", "media"):
            return f"is in a system folder (/{top})"
    hidden = next((part for part in inside if part.startswith(".")), None)
    if hidden:
        return f"is in a hidden folder ({hidden})"
    return None


class MediaPlayerService:
    """Singleton service for media player control via gdbus + MPRIS2 and VLC."""

    _instance: Optional["MediaPlayerService"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    @classmethod
    def get_instance(cls) -> "MediaPlayerService":
        return cls()

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        logger.info("MediaPlayerService initialized")

    def _get_music_directory(self) -> str:
        """Read music directory from DB settings, fall back to ~/Music. The MCP
        server has no app database, so there it asks the backend."""
        from backend.utils.backend_http import in_mcp_process
        if in_mcp_process():
            try:
                from backend.utils.backend_http import request_json
                value = ((request_json("GET", "/api/settings/music_directory").data or {})
                         .get("music_directory") or "").strip()
                if value:
                    return value
            except Exception as e:
                logger.debug(f"Could not read music_directory from the backend: {e}")
        else:
            try:
                from backend.models import db, Setting
                setting = db.session.get(Setting, "music_directory")
                if setting and setting.value and setting.value.strip():
                    return setting.value.strip()
            except Exception as e:
                logger.debug(f"Could not read music_directory setting: {e}")
        return str(Path.home() / "Music")

    # ===== gdbus helpers =====

    def _gdbus_call(self, bus_name: str, method: str) -> subprocess.CompletedProcess:
        """Call an MPRIS2 Player method via gdbus."""
        return subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", bus_name,
             "--object-path", MPRIS2_OBJECT_PATH,
             "--method", f"{MPRIS2_PLAYER_IFACE}.{method}"],
            capture_output=True, text=True, timeout=5
        )

    def _gdbus_get_property(self, bus_name: str, iface: str, prop: str) -> str:
        """Get a D-Bus property via gdbus."""
        result = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", bus_name,
             "--object-path", MPRIS2_OBJECT_PATH,
             "--method", "org.freedesktop.DBus.Properties.Get",
             iface, prop],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return result.stdout.strip()

    def _find_player_bus(self, player_name: Optional[str] = None) -> str:
        """Find the MPRIS2 bus name for a player."""
        if player_name:
            return MPRIS2_BUS_PREFIX + player_name

        # List all bus names and find MPRIS2 players
        result = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.freedesktop.DBus",
             "--object-path", "/org/freedesktop/DBus",
             "--method", "org.freedesktop.DBus.ListNames"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list D-Bus names: {result.stderr.strip()}")

        for name in re.findall(r"'([^']+)'", result.stdout):
            if name.startswith(MPRIS2_BUS_PREFIX):
                return name

        raise RuntimeError("No media player is running.")

    def _player_display_name(self, bus_name: str) -> str:
        if bus_name.startswith(MPRIS2_BUS_PREFIX):
            return bus_name[len(MPRIS2_BUS_PREFIX):]
        return bus_name

    # ===== MPRIS2 Methods =====

    def list_players(self) -> Dict[str, Any]:
        """List all running MPRIS2 media players."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            result = subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.freedesktop.DBus",
                 "--object-path", "/org/freedesktop/DBus",
                 "--method", "org.freedesktop.DBus.ListNames"],
                capture_output=True, text=True, timeout=5
            )
            players = [
                name[len(MPRIS2_BUS_PREFIX):]
                for name in re.findall(r"'([^']+)'", result.stdout)
                if name.startswith(MPRIS2_BUS_PREFIX)
            ]
            return {"success": True, "players": players, "count": len(players)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _call_player_method(self, method: str, player_name: Optional[str] = None) -> Dict[str, Any]:
        """Call a method on the MPRIS2 Player interface."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            bus_name = self._find_player_bus(player_name)
            display = self._player_display_name(bus_name)
            result = self._gdbus_call(bus_name, method)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()}
            return {"success": True, "player": display, "action": method.lower()}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"MPRIS2 {method} failed: {e}"}

    def play(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Play", player_name)

    def pause(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Pause", player_name)

    def play_pause(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("PlayPause", player_name)

    def stop(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Stop", player_name)

    def next_track(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Next", player_name)

    def previous_track(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Previous", player_name)

    def get_status(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        """Get current playback status and track metadata."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            bus_name = self._find_player_bus(player_name)
            display = self._player_display_name(bus_name)

            # Get PlaybackStatus
            raw_status = self._gdbus_get_property(bus_name, MPRIS2_PLAYER_IFACE, "PlaybackStatus")
            status = re.search(r"'([^']+)'", raw_status)
            status = status.group(1) if status else "Unknown"

            # Get Metadata
            raw_meta = self._gdbus_get_property(bus_name, MPRIS2_PLAYER_IFACE, "Metadata")
            track_info = self._parse_metadata(raw_meta)

            # Get Volume
            try:
                raw_vol = self._gdbus_get_property(bus_name, MPRIS2_PLAYER_IFACE, "Volume")
                vol_match = re.search(r"([\d.]+)", raw_vol)
                if vol_match:
                    track_info["player_volume"] = round(float(vol_match.group(1)) * 100)
            except Exception:
                pass

            return {
                "success": True,
                "player": display,
                "status": status,
                "track": track_info,
            }
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Failed to get status: {e}"}

    def _parse_metadata(self, raw: str) -> Dict[str, Any]:
        """Parse gdbus metadata output into a dict."""
        info: Dict[str, Any] = {"title": "Unknown", "artist": "Unknown", "album": "Unknown"}

        # Extract title: 'xesam:title': <'Some Title'>
        title = re.search(r"'xesam:title':\s*<" + _GV_STRING, raw)
        title_text = _gv_text(title).strip() if title else ""
        if title_text:
            info["title"] = title_text

        # Extract artist: 'xesam:artist': <['Artist1', "Guns N' Roses"]>
        artist = re.search(r"'xesam:artist':\s*<\[((?:" + _GV_STRING + r"|[\s,])*)\]>", raw)
        if artist:
            artists = [_gv_text(m).strip() for m in re.finditer(_GV_STRING, artist.group(1))]
            artists = [a for a in artists if a]
            if artists:
                info["artist"] = ", ".join(artists)

        # Extract album
        album = re.search(r"'xesam:album':\s*<" + _GV_STRING, raw)
        album_text = _gv_text(album).strip() if album else ""
        if album_text:
            info["album"] = album_text

        # Extract length (microseconds)
        length = re.search(r"'mpris:length':\s*<(?:int64\s+|uint64\s+)?(\d+)>", raw)
        if length:
            info["length_seconds"] = int(length.group(1)) // 1_000_000

        # Extract art URL
        art = re.search(r"'mpris:artUrl':\s*<" + _GV_STRING, raw)
        if art:
            info["art_url"] = _gv_text(art)

        # With no title tag, or an empty one, the file name without its
        # extension is the next best thing.
        if not title_text:
            url = re.search(r"'xesam:url':\s*<" + _GV_STRING, raw)
            if url:
                from urllib.parse import unquote, urlparse
                name = Path(unquote(urlparse(_gv_text(url)).path)).stem
                if name:
                    info["title"] = name

        return info

    # ===== Volume Control =====

    def get_volume(self) -> Dict[str, Any]:
        """Get current system volume via amixer."""
        try:
            result = subprocess.run(
                ["amixer", "get", "Master"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()}
            match = re.search(r"\[(\d+)%\]", result.stdout)
            level = int(match.group(1)) if match else None
            mute_match = re.search(r"\[(on|off)\]", result.stdout)
            muted = mute_match.group(1) == "off" if mute_match else False
            return {"success": True, "volume": level, "muted": muted}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_volume(self, level: str) -> Dict[str, Any]:
        """Set system volume. Accepts '50', '+10', '-10', 'mute', 'unmute'."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            level = level.strip()
            if level.lower() == "mute":
                cmd = ["amixer", "set", "Master", "mute"]
            elif level.lower() == "unmute":
                cmd = ["amixer", "set", "Master", "unmute"]
            elif level.startswith("+") or level.startswith("-"):
                amount = level.lstrip("+-")
                direction = "+" if level.startswith("+") else "-"
                cmd = ["amixer", "set", "Master", f"{amount}%{direction}"]
            else:
                cmd = ["amixer", "set", "Master", f"{level}%"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()}

            current = self.get_volume()
            return {
                "success": True,
                "volume": current.get("volume"),
                "muted": current.get("muted", False),
                "action": f"set volume to {level}",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ===== Music File Search =====

    def find_music_files(self, query: str, search_dirs: Optional[List[str]] = None) -> Dict[str, Any]:
        """Search for music files matching a query."""
        if not search_dirs:
            search_dirs = [self._get_music_directory()]

        query_lower = query.lower()
        query_parts = query_lower.split()
        matches = []

        for search_dir in search_dirs:
            search_path = Path(search_dir).expanduser()
            if not search_path.is_dir():
                continue
            try:
                for root, dirs, files in os.walk(search_path):
                    for filename in files:
                        ext = Path(filename).suffix.lower()
                        if ext not in MUSIC_EXTENSIONS:
                            continue
                        search_text = (filename + " " + Path(root).name).lower()
                        if all(part in search_text for part in query_parts):
                            matches.append(os.path.join(root, filename))
                            if len(matches) >= 50:
                                break
                    if len(matches) >= 50:
                        break
            except PermissionError:
                continue

        matches.sort()
        return {
            "success": True,
            "files": matches,
            "count": len(matches),
            "query": query,
            "search_dirs": search_dirs,
        }

    # ===== VLC Launch =====

    def _kill_existing_vlc(self):
        """End the VLC that Guaardvark started last and wait up to 2 s for it to
        exit. The pidfile outlives VLC and reboots and PIDs are reused, so the
        process is signalled only while its start time matches the one recorded
        and its executable or argv[0] is vlc. Players the user started are left
        alone. Call with _vlc_launch_lock held."""
        pidfile = _vlc_pidfile()
        try:
            recorded = pidfile.read_text().strip()
        except OSError:
            return
        try:
            pidfile.unlink()
        except OSError:
            pass
        # "<pid> <starttime>"; anything else, such as a pid-only file, is stale.
        match = re.fullmatch(r"([0-9]+) ([0-9]+)", recorded)
        if not match:
            return
        pid, started = int(match.group(1)), match.group(2)

        # A VLC launched a moment ago may still be in its launcher (snap run, a
        # wrapper script) on the way to exec'ing vlc under the same PID.
        deadline = time.monotonic() + 2.0
        while not _proc_is_vlc(pid):
            if _proc_gone(pid, started) or time.monotonic() >= deadline:
                return
            time.sleep(0.05)
        if _proc_gone(pid, started):
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return

        # Wait for it to exit so the new VLC does not start beside it.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.waitpid(pid, os.WNOHANG)  # reaps it when this process started it
            except OSError:
                pass
            if _proc_gone(pid, started):
                return
            time.sleep(0.05)
        logger.debug(f"VLC {pid} was still running 2 s after SIGTERM")

    def _record_vlc(self, pid: int) -> None:
        stat = _proc_stat(pid)
        if not stat:
            return
        try:
            pidfile = _vlc_pidfile()
            pidfile.parent.mkdir(parents=True, exist_ok=True)
            pidfile.write_text(f"{pid} {stat[1]}")
        except OSError as e:
            logger.debug(f"Could not record the VLC pid: {e}")

    def launch_vlc(self, files: Optional[List[str]] = None, directory: Optional[str] = None,
                   shuffle: bool = False) -> Dict[str, Any]:
        """Start VLC on files or a folder, replacing the VLC Guaardvark started
        before. A directory must be an existing absolute folder, and not the root,
        a system or a hidden folder unless it is inside the Settings music folder."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}

        if directory:
            folder = Path(directory).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                return {"success": False, "error": f"'{directory}' is not an existing folder (give an absolute path)"}
            folder = folder.resolve()
            refusal = _folder_refusal(folder)
            if refusal:
                music = Path(self._get_music_directory()).expanduser().resolve()
                if folder != music and music not in folder.parents:
                    return {"success": False,
                            "error": f"'{directory}' {refusal}; name a folder of music instead"}
            directory = str(folder)

        vlc_cmd = VLC_PATH
        if not os.path.exists(vlc_cmd):
            vlc_cmd = "vlc"

        # --no-one-instance: with VLC's "Allow only one instance" preference on, a
        # new vlc hands its playlist to a VLC the user already has open and exits.
        # --no-metadata-network-access: no album-art or metadata lookups online.
        cmd = [vlc_cmd, "--no-one-instance", "--no-metadata-network-access"]
        if shuffle:
            cmd.append("--random")

        if directory:
            cmd.append(directory)
        elif files:
            cmd.extend(files)
        else:
            return {"success": False, "error": "No files or directory specified"}

        with _vlc_launch_lock():
            # Replace the VLC Guaardvark started before, so playlists do not pile up.
            self._kill_existing_vlc()
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except FileNotFoundError:
                return {"success": False, "error": "VLC not found. Install VLC to play music."}
            except Exception as e:
                return {"success": False, "error": f"Failed to launch VLC: {e}"}
            self._record_vlc(proc.pid)

        file_count = len(files) if files else 0
        return {
            "success": True,
            "pid": proc.pid,
            "file_count": file_count,
            "directory": directory,
            "shuffle": shuffle,
            "action": "launched VLC",
        }

    # ===== High-Level Play =====

    def play_music(self, query: str = "", shuffle: bool = False) -> Dict[str, Any]:
        """Find music files matching query and play them in VLC.
        If query is empty or generic, plays all music in the music directory."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}

        music_dir = self._get_music_directory()

        # For empty/generic queries, play the entire music directory
        if not query or query.lower() in ("music", "some music", "my music", "all", "everything", "anything", "songs"):
            return self.launch_vlc(directory=music_dir, shuffle=shuffle or True)

        search_result = self.find_music_files(query)
        if not search_result["success"]:
            return search_result

        files = search_result["files"]
        if not files:
            return {
                "success": False,
                "error": f"No music files found matching '{query}' in {music_dir}. "
                         f"Check that your music directory is set correctly in Settings.",
            }

        launch_result = self.launch_vlc(files=files, shuffle=shuffle)
        if not launch_result["success"]:
            return launch_result

        return {
            "success": True,
            "action": "playing",
            "query": query,
            "file_count": len(files),
            "shuffle": shuffle,
            "files": files[:10],
            "total_matches": len(files),
        }


def get_media_service() -> MediaPlayerService:
    return MediaPlayerService.get_instance()
