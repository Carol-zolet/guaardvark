"""Turn the media reference a tool was handed into a local file, or say why not.

The image, video, film and music tools take an input as a URL an earlier tool
returned, a ``guaardvark://outputs/`` resource URI, a library document id
(where the tool says so) or a file path. ``resolve_media_ref`` applies the same
rules to every form:

* A file named like a key or credential (``path_safety.is_sensitive``) is
  refused for every caller, whichever form named it and wherever a symlink
  points.
* A served URL maps back to the directory that serves it; one that climbs out
  of it (``/api/outputs/../..``) is refused.
* For an MCP client (``mcp=True``) only files inside the uploads and outputs
  folders are accepted, symlinks resolved. That is decided before the file is
  looked at, so a refusal says nothing about what exists. Chat and Studio
  callers may also name an existing file by path.

Nothing here downloads a remote URL.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Callable, NamedTuple, Optional
from urllib.parse import unquote, urlparse

from backend.utils.path_guard import PathEscapesRoot, contained_path
from backend.utils.path_safety import is_sensitive, is_within

logger = logging.getLogger(__name__)

RESOURCE_URI_PREFIX = "guaardvark://outputs/"
_OUTPUTS_URL = "/api/outputs/"
_BATCH_IMAGE_URL = re.compile(r"^/api/batch-image/image/([^/]+)/([^/]+)$")
_DOCUMENT_URL = re.compile(r"^/api/files/document/(\d+)/download/?$")
# "127.0.0.1:5000/api/..." written without a scheme.
_BARE_HOST = re.compile(r"^[A-Za-z0-9.\-]+(?::\d+)?(?=/api/)")

SENSITIVE_REFUSAL = (
    "files named like keys or credentials (.env, .env.*, *.pem, *.key, id_rsa*, "
    "credentials, .netrc and similar) are never read"
)
MCP_REFUSAL = "over MCP only files in Guaardvark's uploads and outputs folders are read"

DocumentPath = Callable[[int], Optional[str]]


class MediaRef(NamedTuple):
    """What ``resolve_media_ref`` found: ``path`` on success, else ``error``.

    ``refused`` is True when the reference names something the rules forbid,
    as opposed to a file that is not there.
    """

    path: Optional[str] = None
    error: Optional[str] = None
    refused: bool = False


def accepted_forms(*, mcp: bool, documents: bool = False, within_install: bool = False) -> str:
    """One sentence naming the input forms a tool accepts from this caller."""
    forms = [
        "a media URL a Guaardvark tool returned (/api/outputs/<path> or "
        "/api/batch-image/image/<batch>/<file>, with or without http://host:port in front)",
        "a guaardvark://outputs/<path> resource URI",
    ]
    if documents:
        forms.append("a library document id or /api/files/document/<id>/download link")
    if mcp:
        forms.append("a path inside Guaardvark's uploads or outputs folder")
    elif within_install:
        forms.append("a path inside Guaardvark's uploads, outputs or install folder")
    else:
        forms.append("the path of an existing file")
    where = "Accepted over MCP" if mcp else "Accepted"
    return f"{where}: {', '.join(forms[:-1])}, or {forms[-1]}."


def mcp_media_roots() -> list[str]:
    """Folders an MCP client's media inputs may come from.

    The image and video batch folders live in uploads; they are listed on their
    own so a batch folder symlinked to another disk still counts.
    """
    from backend import config

    uploads, outputs = str(config.UPLOAD_DIR), str(config.OUTPUT_DIR)
    return [uploads, outputs, os.path.join(uploads, "Images"), os.path.join(uploads, "Videos")]


def resources_root() -> str:
    """The folder ``guaardvark://outputs/`` URIs name, as the MCP resources provider serves it."""
    root = "data/outputs"
    try:
        from backend.mcp.config import load_config

        root = load_config().resources.outputs_root or root
    except Exception as e:  # noqa: BLE001 — an unreadable config falls back to the default root
        logger.debug("MCP config not read for the resources root: %s", e)
    if not os.path.isabs(root):
        root = os.path.join(Path(__file__).resolve().parents[2], root)
    return os.path.realpath(root)


def _served_path(text: str) -> Optional[str]:
    """The decoded URL path when ``text`` is an http(s) URL or an ``/api/`` path, else None."""
    parsed = urlparse(text)
    if parsed.scheme.lower() in ("http", "https"):
        return unquote(parsed.path)
    host = _BARE_HOST.match(text)
    rest = text[host.end():] if host else text
    if rest.startswith("/api/"):
        return unquote(rest.split("?", 1)[0].split("#", 1)[0])
    return None


def document_id_from_ref(ref) -> Optional[int]:
    """The document id in a bare id or an ``/api/files/document/<id>/download`` link, else None."""
    text = str(ref or "").strip()
    if text.isdigit():
        return int(text)
    match = _DOCUMENT_URL.match(_served_path(text) or "")
    return int(match.group(1)) if match else None


def check_media_file(path: str, *, mcp: bool, label: str = "file", shown: Optional[str] = None,
                     accepted: str = "", extra_roots: tuple = ()) -> MediaRef:
    """Apply the rules to a local path a tool already has (a document's file, say).

    Returns the path on success: symlinks resolved for an MCP client, as given
    otherwise, so chat keeps the path it named.
    """
    shown = shown or path
    real = os.path.realpath(path)
    if is_sensitive(path) or is_sensitive(real):
        return MediaRef(error=f"{label} '{shown}' was refused: {SENSITIVE_REFUSAL}.", refused=True)
    if mcp and not is_within(real, [*mcp_media_roots(), *extra_roots]):
        tail = f" {accepted}" if accepted else ""
        return MediaRef(error=f"{label} '{shown}' was refused: {MCP_REFUSAL}.{tail}", refused=True)
    if not os.path.isfile(real):
        tail = f" {accepted}" if accepted else ""
        return MediaRef(error=f"{label} not found: {shown}.{tail}")
    return MediaRef(path=real if mcp else path)


def resolve_media_ref(ref, *, mcp: bool, label: str = "file",
                      document_path: Optional[DocumentPath] = None,
                      within_install: bool = False, accepted: Optional[str] = None) -> MediaRef:
    """Resolve ``ref`` to a local file under the module's rules.

    ``document_path`` maps a document id to its file; without it document ids
    and download links are not accepted. ``within_install`` limits a chat
    caller's plain paths to the uploads, outputs and install folders.
    ``accepted`` replaces the sentence that error messages end with. An empty
    ``ref`` returns an empty ``MediaRef`` (no path, no error).
    """
    text = str(ref or "").strip()
    if not text:
        return MediaRef()
    if accepted is None:
        accepted = accepted_forms(mcp=mcp, documents=document_path is not None, within_install=within_install)
    if "\x00" in text:
        return MediaRef(error=f"{label} is not a valid path or URL. {accepted}", refused=True)

    def check(path: str, shown: str = text, extra_roots: tuple = ()) -> MediaRef:
        return check_media_file(path, mcp=mcp, label=label, shown=shown, accepted=accepted,
                                extra_roots=extra_roots)

    def from_document(doc_id: int) -> MediaRef:
        if document_path is None:
            return MediaRef(error=f"{label}: a document link is not accepted here. {accepted}")
        try:
            path = document_path(doc_id)
        except Exception as e:  # noqa: BLE001 — a lookup failure is a plain "not found"
            logger.info("document %s did not resolve: %s", doc_id, e)
            path = None
        if not path:
            return MediaRef(error=f"{label} document {doc_id} was not found, or its file is missing.")
        return check(str(path), shown=f"document {doc_id}")

    if text.lower().startswith(RESOURCE_URI_PREFIX):
        root = resources_root()
        rel = text[len(RESOURCE_URI_PREFIX):].split("?", 1)[0].split("#", 1)[0]
        try:
            path = contained_path(root, "/".join(unquote(seg) for seg in rel.split("/")))
        except PathEscapesRoot:
            return MediaRef(error=f"{label} '{text}' leaves the outputs folder. {accepted}", refused=True)
        return check(path, extra_roots=(root,))

    url_path = _served_path(text)
    if url_path is not None:
        from backend import config

        doc = _DOCUMENT_URL.match(url_path)
        if doc:
            return from_document(int(doc.group(1)))
        if url_path.startswith(_OUTPUTS_URL):
            try:
                path = contained_path(config.OUTPUT_DIR, url_path[len(_OUTPUTS_URL):])
            except PathEscapesRoot:
                return MediaRef(error=f"{label} '{text}' leaves the outputs folder. {accepted}", refused=True)
            return check(path)
        batch = _BATCH_IMAGE_URL.match(url_path)
        if batch:
            base = (Path(config.UPLOAD_DIR) / "Images").resolve()
            candidate = (base / batch.group(1) / "images" / batch.group(2)).resolve()
            if not candidate.is_relative_to(base):
                return MediaRef(error=f"{label} '{text}' leaves the image batch folder. {accepted}",
                                refused=True)
            return check(str(candidate))
        return MediaRef(error=(f"{label} '{text}' is not a URL Guaardvark serves media from, and "
                               f"remote files are never downloaded. {accepted}"))

    if text.isdigit() and document_path is not None:
        return from_document(int(text))
    return _from_path(text, mcp=mcp, label=label, accepted=accepted,
                      within_install=within_install, check=check)


def _from_path(text: str, *, mcp: bool, label: str, accepted: str, within_install: bool,
               check: Callable[..., MediaRef]) -> MediaRef:
    from backend import config

    candidate = os.path.expanduser(text)
    if mcp or within_install:
        bases = [config.UPLOAD_DIR, config.OUTPUT_DIR]
        if not mcp:
            bases.append(config.GUAARDVARK_ROOT)
        options = []
        for base in bases:
            try:
                options.append(contained_path(base, candidate))
            except PathEscapesRoot:
                continue
        if mcp and not options and not os.path.isabs(candidate):
            return MediaRef(error=(f"{label} '{text}' was refused: a relative path may not leave the "
                                   f"uploads or outputs folder. {accepted}"), refused=True)
        if mcp and not options:
            options = [candidate]  # check() refuses it without looking at the disk
        for option in options:
            found = check(option)
            if found.path or found.refused:
                return found
        if not mcp and not options and os.path.isfile(candidate):
            return MediaRef(error=(f"{label} '{text}' was refused: it must be inside the uploads or "
                                   f"outputs directory or the install root. {accepted}"), refused=True)
        return MediaRef(error=f"{label} not found: {text}. {accepted}")
    return check(candidate)
