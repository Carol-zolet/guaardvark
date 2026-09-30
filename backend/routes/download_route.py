# download_route.py   Version 1.000

import os

from flask import Blueprint, abort, current_app, request, send_from_directory
from backend.utils.auth_guard import is_protected_output, require_local_or_key
from backend.utils.path_guard import PathEscapesRoot, contained, contained_path

# The name must differ from backend/api/outputs_api.py's blueprint: discovery
# skips a name that is already registered, which left /outputs/<path> unserved.
download_bp = Blueprint("outputs_download", __name__)

_MARKUP_AS_TEXT = frozenset({".html", ".htm", ".xhtml", ".svg", ".xml"})


def _needs_local_or_key(outputs_dir: str, safe_path: str) -> bool:
    """True when the file is a personal record (auth_guard.PROTECTED_OUTPUT_*).

    Both the normalised path and the symlink-resolved one are checked, so a
    link in a media folder that points into chat-exports/ is protected too.
    """
    if is_protected_output(os.path.relpath(safe_path, outputs_dir)):
        return True
    real_root = os.path.realpath(outputs_dir)
    real_path = os.path.realpath(safe_path)
    try:
        contained_path(real_root, real_path)
    except PathEscapesRoot:
        return False  # a media folder symlinked to another disk
    return is_protected_output(os.path.relpath(real_path, real_root))


@download_bp.route("/outputs/<path:filename>", methods=["GET"])
def download_output(filename):
    outputs_dir = os.path.abspath(current_app.config["OUTPUT_DIR"])
    try:
        safe_path = contained_path(outputs_dir, filename)
    except PathEscapesRoot:
        abort(403, description="Invalid file path.")

    # Checked before the file lookup, so a remote host cannot learn which
    # chat-export or screenshot names exist from a 404 versus a 403.
    if _needs_local_or_key(outputs_dir, safe_path):
        denied = require_local_or_key()
        if denied is not None:
            return denied

    if not os.path.isfile(safe_path):
        abort(404, description="File not found.")

    rel_path = os.path.relpath(safe_path, outputs_dir)
    # ?inline=1 lets the browser display the file instead of saving it.
    inline = request.args.get("inline", "").strip().lower() in ("1", "true", "yes")
    # Generated markup is shown as text: an inline .html/.svg would otherwise run
    # its scripts on the API origin.
    mimetype = None
    if inline and os.path.splitext(safe_path)[1].lower() in _MARKUP_AS_TEXT:
        mimetype = "text/plain; charset=utf-8"
    return send_from_directory(
        outputs_dir, rel_path, as_attachment=not inline, mimetype=mimetype
    )
