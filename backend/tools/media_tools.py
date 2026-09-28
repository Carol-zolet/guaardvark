#!/usr/bin/env python3
"""
Media Player Tools
Tools for controlling music playback, volume, and checking current track info.
"""

import logging
import re

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.media_player_service import get_media_service, MEDIA_CONTROL_ENABLED

logger = logging.getLogger(__name__)


def _disabled_result():
    return ToolResult(
        success=False,
        error="Media control disabled. Set GUAARDVARK_MEDIA_CONTROL=true to enable."
    )


class MediaPlayTool(BaseTool):
    """Play music by searching for songs/artists/albums, or resume playback."""

    name = "media_play"
    read_only = False
    destructive = False
    description = (
        "Play local music files in VLC on the machine running this Guaardvark server. query "
        "searches the music folder set in Settings (~/Music by default) for audio files whose file "
        "name plus the name of the folder they are in contains every query word (case-insensitive; "
        "parent folders are not searched, so in an Artist/Album/track layout the artist name does "
        "not match) and plays up to 50 of them, returning e.g. \"playing (12 tracks) matching "
        "'jazz'\", or an error when none match. query 'music' (also 'all', 'songs', 'everything') "
        "plays the whole music folder, always shuffled; directory plays a folder you name instead. "
        "Matches and directory play in order unless shuffle is true. Starting new music replaces the "
        "VLC that Guaardvark started before; players the user opened are left alone, and VLC is "
        "started with its one-instance mode off so it never hands the music to a VLC already open. "
        "With neither query nor directory it sends Play to the first media player D-Bus lists. "
        "Pause or skip with media_control, set loudness with media_volume, see the current track "
        "with media_status."
    )
    parameters = {
        "query": ToolParameter(
            name="query", type="string", required=False,
            description="Words that must each appear in a file's name or the name of the folder it "
                        "is in, e.g. 'Alice in Chains' or 'jazz'. Use 'music' to play everything. "
                        "Omit, with no directory, to resume playback."
        ),
        "shuffle": ToolParameter(
            name="shuffle", type="bool", required=False,
            description="Play in random order (default false). query 'music' always shuffles.", default=False
        ),
        "directory": ToolParameter(
            name="directory", type="string", required=False,
            description="Absolute path of a folder to play in full, e.g. '~/Music/Live' or '/mnt/music/Live'; "
                        "overrides query. It must be an existing folder. Refused: '/', system folders "
                        "(/etc, /usr, /var, /proc, /sys, /dev, /boot, /root, /bin, /sbin, /lib*, and /run "
                        "except /run/media) and hidden folders such as ~/.ssh (any part starting with "
                        "'.'), unless inside the Settings music folder. Other folders, e.g. on /mnt or "
                        "/media, are allowed."
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not MEDIA_CONTROL_ENABLED:
            return _disabled_result()

        service = get_media_service()
        # str(): parameters may arrive as non-strings (e.g. a song called "1999")
        query = str(kwargs.get("query") or "").strip()
        shuffle = kwargs.get("shuffle", False)
        directory = str(kwargs.get("directory") or "").strip()

        if directory:
            result = service.launch_vlc(directory=directory, shuffle=shuffle)
        elif query:
            result = service.play_music(query, shuffle=shuffle)
        else:
            # Resume playback
            result = service.play()

        if result.get("success"):
            output = result.get("action", "playing")
            if result.get("file_count"):
                output += f" ({result['file_count']} tracks)"
            if result.get("query"):
                output += f" matching '{result['query']}'"
            if result.get("directory"):
                output += f" in {result['directory']}"
            if result.get("shuffle"):
                output += " (shuffled)"
            return ToolResult(success=True, output=output, metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error", "Unknown error"))


class MediaControlTool(BaseTool):
    """Control media playback: pause, stop, next, previous, toggle."""

    name = "media_control"
    read_only = False
    destructive = False
    description = (
        "Send play, pause, stop, next, previous or toggle to a media player already open on the "
        "machine running this Guaardvark server, over MPRIS2 on the D-Bus session bus. Works on any "
        "MPRIS2 player, not only the VLC that media_play starts; without player it acts on the first "
        "one D-Bus lists. player 'vlc' means whichever VLC owns org.mpris.MediaPlayer2.vlc, which "
        "may be one the user opened. Returns e.g. 'Paused on vlc', or an error when no player is "
        "running. play, pause and stop are safe to repeat; next, previous and toggle act again on "
        "every call. Start music with media_play, change loudness with media_volume, read the "
        "current track with media_status."
    )
    parameters = {
        "action": ToolParameter(
            name="action", type="string", required=True,
            enum=["play", "pause", "stop", "next", "previous", "toggle"],
            description="play resumes a paused player; pause; stop; next and previous change track; "
                        "toggle switches between playing and paused."
        ),
        "player": ToolParameter(
            name="player", type="string", required=False,
            description="Player to control: the part of its D-Bus name after 'org.mpris.MediaPlayer2.', "
                        "as media_status's Player line shows it, e.g. 'vlc' or 'spotify'; the full bus "
                        "name fails. Default: the first player D-Bus lists."
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not MEDIA_CONTROL_ENABLED:
            return _disabled_result()

        service = get_media_service()
        action = str(kwargs.get("action") or "").strip().lower()
        player = kwargs.get("player")

        action_map = {
            "play": (service.play, "Playing"),
            "resume": (service.play, "Playing"),
            "pause": (service.pause, "Paused"),
            "stop": (service.stop, "Stopped"),
            "next": (service.next_track, "Skipped to next track"),
            "previous": (service.previous_track, "Went to previous track"),
            "prev": (service.previous_track, "Went to previous track"),
            "toggle": (service.play_pause, "Toggled playback"),
        }

        if action not in action_map:
            return ToolResult(
                success=False,
                error=f"Unknown action '{action}'. Use: play, pause, stop, next, previous, toggle"
            )

        func, label = action_map[action]
        result = func(player)
        if result.get("success"):
            output = label
            if result.get("player"):
                output += f" on {result['player']}"
            return ToolResult(success=True, output=output, metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error", "Unknown error"))


class MediaVolumeTool(BaseTool):
    """Get or set the system audio volume."""

    name = "media_volume"
    read_only = False
    destructive = False
    description = (
        "Read or change the system output volume of the machine running this Guaardvark server: "
        "the ALSA 'Master' control via amixer, which affects every application, not only the "
        "player. Omit level to read it without changing anything (returns 'Volume: 30%', with "
        "' (muted)' when muted). Set it with level as text: '50' (percent, 0-100), '+10' or '-10' "
        "(points up or down, at most 100), 'mute' or 'unmute'; returns 'Volume set to N%'. "
        "Absolute, mute and unmute give the same result on repeat; relative steps add up. Use "
        "media_control to pause or skip, media_status for the current track."
    )
    parameters = {
        "level": ToolParameter(
            name="level", type="string", required=False,
            description="Text, e.g. '50' (percent, 0-100), '+10' or '-10' (step), 'mute' or 'unmute'; "
                        "omit to read the volume. Over MCP a number such as 50 fails schema validation; "
                        "send '50'."
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not MEDIA_CONTROL_ENABLED:
            return _disabled_result()

        service = get_media_service()
        # MCP sends text only (the schema refuses numbers); chat's reflex passes an
        # int for "volume 50", which is always an absolute level. Only text with an
        # explicit leading '+' or '-' is a step.
        raw_level = kwargs.get("level")
        if isinstance(raw_level, bool) or (isinstance(raw_level, int) and raw_level < 0):
            return ToolResult(success=False, error=f"level {raw_level!r} is not a percentage 0-100; "
                                                   "for a step send text such as '-10'")
        level = "" if raw_level is None else str(raw_level).strip()

        if not level:
            result = service.get_volume()
            if result.get("success"):
                vol = result.get("volume")
                muted = result.get("muted", False)
                status = f"Volume: {vol}%" + (" (muted)" if muted else "")
                return ToolResult(success=True, output=status, metadata=result)
            else:
                return ToolResult(success=False, error=result.get("error"))

        if level.lower() not in ("mute", "unmute"):
            if not re.fullmatch(r"[+-]?[0-9]{1,3}", level):
                return ToolResult(success=False, error=f"level '{level}' is not a percentage, a +/- step, 'mute' or 'unmute'")
            if int(level.lstrip("+-")) > 100:
                return ToolResult(success=False, error="level must be 0-100, and a step at most 100")
        result = service.set_volume(level)
        if result.get("success"):
            vol = result.get("volume")
            muted = result.get("muted", False)
            output = f"Volume set to {vol}%" + (" (muted)" if muted else "")
            return ToolResult(success=True, output=output, metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error"))


class MediaStatusTool(BaseTool):
    """Get current playback status and track info."""

    name = "media_status"
    read_only = True
    # A status poll: repeating it changes nothing.
    idempotent = True
    description = (
        "Report what a media player on the machine running this Guaardvark server is playing, read "
        "over MPRIS2 (D-Bus) without changing anything. Returns text lines: Player, Status (Playing, "
        "Paused or Stopped), Title (the file name without extension when the track has no title "
        "tag or an empty one), Artist, Album ('Unknown' when the player gives none), Length m:ss "
        "and the player's own volume when known. Without player it reads the first player D-Bus "
        "lists. When no player is running it says 'No media player is running.' Start music with "
        "media_play; for system volume use media_volume; to pause or skip use media_control."
    )
    parameters = {
        "player": ToolParameter(
            name="player", type="string", required=False,
            description="Player to read: the part of its D-Bus name after 'org.mpris.MediaPlayer2.', as "
                        "the Player line shows it, e.g. 'vlc'; the full bus name fails. Default: the "
                        "first player D-Bus lists."
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not MEDIA_CONTROL_ENABLED:
            return _disabled_result()

        service = get_media_service()
        player = kwargs.get("player")

        result = service.get_status(player)
        if not result.get("success") and "No media player" in (result.get("error") or ""):
            # Nothing playing is an answer, not a failure.
            return ToolResult(success=True, output="No media player is running.", metadata=result)
        if result.get("success"):
            track = result.get("track", {})
            status = result.get("status", "Unknown")
            output = (
                f"Player: {result.get('player', 'unknown')}\n"
                f"Status: {status}\n"
                f"Title: {track.get('title', 'Unknown')}\n"
                f"Artist: {track.get('artist', 'Unknown')}\n"
                f"Album: {track.get('album', 'Unknown')}"
            )
            if track.get("length_seconds"):
                mins, secs = divmod(track["length_seconds"], 60)
                output += f"\nLength: {mins}:{secs:02d}"
            if track.get("player_volume") is not None:
                output += f"\nPlayer volume: {track['player_volume']}%"
            return ToolResult(success=True, output=output, metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error"))
