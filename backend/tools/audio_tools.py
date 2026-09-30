"""Music and speech through Audio Foundry, for MCP clients.

Both tools call the backend's Audio Foundry routes, the same ones the Studio's
Audio page uses, so a song or a voice line made from an agent lands in the
library like any other. Voice cloning is not offered here: it needs a consent
record for the reference clip, which only the Audio Studio's consent step
writes, after the person confirms they have the right to clone that voice.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

STUDIO_URL = "/audio"
MAX_SPEECH_CHARS = 3000


def _kokoro_voice_ids() -> list[str]:
    from backend.services.audio_foundry_models import kokoro_voice_ids
    return kokoro_voice_ids()


def _post(path: str, payload: dict, read_timeout: float) -> tuple[Optional[dict], Optional[str]]:
    from backend.utils.backend_http import BackendError, request_json
    try:
        reply = request_json("POST", path, payload=payload, read_timeout=read_timeout)
    except BackendError as e:
        if e.kind == "plugin_offline":
            return None, ("Audio Foundry is not running. Start it from the Studio's Plugins page "
                          "(or POST /api/plugins/audio_foundry/start), then try again.")
        return None, str(e)
    body = reply.body if isinstance(reply.body, dict) else {}
    return body, None


def _file_entry(result: dict) -> dict:
    """What a caller needs to find a finished audio file: its name, library
    document and a download link on the backend."""
    from backend.utils.backend_http import backend_base_url

    entry: dict[str, Any] = {"file": Path(str(result.get("path") or "audio")).name}
    if result.get("duration_s") is not None:
        entry["duration_s"] = round(float(result["duration_s"]), 1)
    doc_id = result.get("document_id")
    if doc_id:
        entry["document_id"] = doc_id
        entry["url"] = f"/api/files/document/{doc_id}/download"
        entry["download"] = f"{backend_base_url()}{entry['url']}"
    elif result.get("registration_error"):
        entry["note"] = "Saved, but not yet in the library: " + str(result["registration_error"])
    return entry


class GenerateMusicTool(BaseTool):
    """Queue a song (ACE-Step) in Audio Foundry."""

    name = "generate_music"
    read_only = False
    destructive = False
    description = (
        "Make a song on this machine with ACE-Step in Guaardvark's Audio Foundry: a style prompt "
        "(genre, instruments, mood, tempo, voice) and optional lyrics, up to 240 seconds, sung or "
        "instrumental. Returns at once with a job_id; a 30-second song usually takes one to two "
        "minutes. Poll get_generation_status with that job_id; when it reports complete it gives the "
        "file name, its library document id and a download link. The song is also listed on the "
        "Studio's Audio page. Needs the Audio Foundry plugin running (it answers that it is not "
        "running otherwise). For a spoken line use generate_speech; for a music video set to a "
        "song, generate_music_video."
    )
    parameters = {
        "style": ToolParameter(
            name="style", type="string", required=True,
            description="The sound in plain tags, e.g. 'upbeat synthwave, analog bass, female vocals, 110 bpm'. "
                        "Concrete genre, instrument and tempo words work better than 'professional' or 'epic'.",
        ),
        "lyrics": ToolParameter(
            name="lyrics", type="string", required=False,
            description="Words to sing. Section tags like [verse] and [chorus] on their own lines help. "
                        "Omit for an instrumental, or set instrumental=true.",
        ),
        "seconds": ToolParameter(
            name="seconds", type="float", required=False, default=30.0, minimum=5, maximum=240,
            description="Length in seconds, 5-240 (default 30).",
        ),
        "instrumental": ToolParameter(
            name="instrumental", type="bool", required=False, default=False,
            description="True for no vocals; lyrics are then ignored.",
        ),
        "seed": ToolParameter(
            name="seed", type="int", required=False,
            description="Repeat a take exactly with the same seed and inputs. Omit for a new one.",
        ),
    }

    def execute(self, style: str = "", lyrics: str = None, seconds: float = None,
                instrumental: bool = None, seed: int = None, **kwargs) -> ToolResult:
        style = (style or "").strip()
        if not style:
            return ToolResult(success=False, error="style is required: describe the sound, e.g. 'lo-fi hip hop, soft piano, 80 bpm'")
        try:
            seconds = float(seconds if seconds is not None else 30.0)
        except (TypeError, ValueError):
            return ToolResult(success=False, error="seconds must be a number from 5 to 240")
        if not 5 <= seconds <= 240:
            return ToolResult(success=False, error="seconds must be from 5 to 240")

        payload: dict[str, Any] = {"style_prompt": style, "duration_s": seconds,
                                   "instrumental_only": bool(instrumental), "async": True}
        if lyrics and not instrumental:
            payload["lyrics"] = lyrics
        if seed is not None:
            payload["seed"] = int(seed)

        body, err = _post("/api/audio-foundry/generate/music", payload, read_timeout=60)
        if err:
            return ToolResult(success=False, error=err)
        job_id = body.get("job_id")
        if job_id:
            return ToolResult(
                success=True,
                output={
                    "job_id": job_id,
                    "status": body.get("status", "queued"),
                    "estimate_s": body.get("estimate_s"),
                    "studio_url": STUDIO_URL,
                    "next": "Poll get_generation_status with this job_id; the song is ready when it reports complete.",
                },
                metadata={"style": style, "seconds": seconds},
            )
        if body.get("path"):
            # Short requests can finish inline instead of queueing.
            return ToolResult(success=True, output={"status": "complete", **_file_entry(body)})
        return ToolResult(success=False, error=body.get("error") or "Audio Foundry did not start the song")


class GenerateSpeechTool(BaseTool):
    """Speak a line of text with a stock voice (Kokoro or Chatterbox)."""

    name = "generate_speech"
    read_only = False
    destructive = False
    description = (
        "Turn text into speech on this machine with Guaardvark's Audio Foundry. Waits for the file, "
        "usually seconds, and returns its name, library document id, length and a download link. "
        "Up to 3000 characters per call; split longer scripts. Voices: naming a voice (a Kokoro id "
        "such as 'af_heart' or 'bm_george') always speaks with Kokoro; with no voice, engine 'auto' "
        "uses Chatterbox's single stock voice when Chatterbox is installed and Kokoro's default "
        "voice otherwise. Chatterbox has no voice ids, so engine 'chatterbox' with a voice is "
        "refused. A model or voice pack that is not installed is refused with a pointer to Audio "
        "Studio → Manage models; nothing is downloaded. Needs the Audio Foundry plugin running. "
        "Cloning a real person's voice (a Chatterbox reference clip) is not available here; it "
        "needs the person to confirm in Audio Studio that they have the right to clone that "
        "voice. For a song use generate_music."
    )
    parameters = {
        "text": ToolParameter(
            name="text", type="string", required=True,
            description="What to say, as it should be spoken (spell out numbers and names the way they sound).",
        ),
        "voice": ToolParameter(
            name="voice", type="string", required=False,
            description="A Kokoro voice id: accent and gender prefix plus a name, e.g. 'af_heart' "
                        "(American female, the default), 'am_michael', 'bf_emma', 'bm_george', "
                        "'ef_dora' (Spanish). Naming one selects Kokoro. An unknown id is refused "
                        "with the full list. Omit for the engine's default voice.",
        ),
        "engine": ToolParameter(
            name="engine", type="string", required=False, default="auto",
            enum=["auto", "kokoro", "chatterbox"],
            description="'auto' (default): Kokoro when voice is set; otherwise Chatterbox when it is "
                        "installed, falling back to Kokoro. 'kokoro': the named voice or af_heart. "
                        "'chatterbox': its one stock voice; do not combine with voice.",
        ),
    }

    def execute(self, text: str = "", voice: str = None, engine: str = None, **kwargs) -> ToolResult:
        text = (text or "").strip()
        if not text:
            return ToolResult(success=False, error="text is required")
        if len(text) > MAX_SPEECH_CHARS:
            return ToolResult(success=False, error=(
                f"text is {len(text)} characters; the limit is {MAX_SPEECH_CHARS} per call. "
                "Split it at sentence ends and call once per part."))
        engine = (engine or "auto").strip().lower()
        if engine not in ("auto", "kokoro", "chatterbox"):
            return ToolResult(success=False, error="engine must be auto, kokoro or chatterbox")
        voice = (voice or "").strip()
        if voice:
            # Validated here as well as in the plugin: Kokoro reads a value
            # ending in .pt as a file path and a comma as a blend of voices.
            valid = _kokoro_voice_ids()
            if voice not in valid:
                return ToolResult(success=False, error=(
                    f"Unknown voice {voice!r}. Kokoro voices: {', '.join(valid)}. "
                    "Omit voice for the default."))
            if engine == "chatterbox":
                return ToolResult(success=False, error=(
                    f"Chatterbox has no built-in voices, so voice {voice!r} cannot be used with "
                    "engine 'chatterbox'. Use engine 'kokoro' (or 'auto') for that voice, or omit "
                    "voice to hear Chatterbox's stock voice."))
            # The plugin's auto mode tries Chatterbox first, and Chatterbox
            # ignores voice ids, so a named voice must go to Kokoro explicitly.
            engine = "kokoro"
        payload: dict[str, Any] = {"text": text, "backend": engine}
        if voice:
            payload["voice_id"] = voice

        body, err = _post("/api/audio-foundry/generate/voice", payload, read_timeout=110)
        if err:
            return ToolResult(success=False, error=err)
        if not body.get("path"):
            return ToolResult(success=False, error=body.get("error") or body.get("detail") or "No audio came back")
        meta = body.get("meta") or {}
        return ToolResult(
            success=True,
            output={"status": "complete", **_file_entry(body),
                    "engine": meta.get("backend") or meta.get("engine"),
                    "voice": meta.get("voice") or meta.get("voice_id")},
        )
