"""Shared media-input rules (backend/utils/media_inputs.py).

Served URLs and resource URIs map back to disk and cannot climb out of the
folder that serves them, credential-named files are refused for every caller,
and an MCP caller only reaches the uploads and outputs folders.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from backend import config
from backend.utils import media_inputs as mi


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "install"
    uploads, outputs = root / "data" / "uploads", root / "data" / "outputs"
    batch = uploads / "Images" / "ImageBatch_1" / "images" / "a.png"
    edit = outputs / "generated_images" / "edit_1.png"
    for p in (batch, edit):
        p.parent.mkdir(parents=True)
        p.write_bytes(b"png")
    (root / ".env").write_text("KEY=x")
    (uploads / "credentials.json").write_text("{}")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"png")
    notes = root / "notes.txt"
    notes.write_text("inside the install folder")
    os.symlink(outside, outputs / "generated_images" / "link.png")
    os.symlink(root / ".env", uploads / "innocent.png")
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", root)
    monkeypatch.setattr(mi, "resources_root", lambda: str(outputs.resolve()))
    return SimpleNamespace(root=root, uploads=uploads, outputs=outputs, batch=batch, edit=edit,
                           outside=outside, notes=notes)


SERVED = [
    ("/api/batch-image/image/ImageBatch_1/a.png", "batch"),
    ("http://127.0.0.1:5000/api/batch-image/image/ImageBatch_1/a.png", "batch"),
    ("/api/outputs/generated_images/edit_1.png", "edit"),
    ("http://127.0.0.1:5000/api/outputs/generated_images/edit_1.png?x=1", "edit"),
    ("127.0.0.1:5000/api/outputs/generated_images/edit_1.png", "edit"),
    ("guaardvark://outputs/generated_images/edit_1.png", "edit"),
    ("guaardvark://outputs/generated_images/edit%5F1.png", "edit"),
]


@pytest.mark.parametrize("mcp", [True, False])
@pytest.mark.parametrize("ref,which", SERVED)
def test_served_urls_and_resource_uris_map_to_disk(tree, mcp, ref, which):
    found = mi.resolve_media_ref(ref, mcp=mcp)
    assert found.error is None
    assert os.path.realpath(found.path) == str(getattr(tree, which).resolve())


@pytest.mark.parametrize("mcp", [True, False])
@pytest.mark.parametrize("ref", [
    "/api/outputs/../../../../../../etc/hostname",
    "/api/outputs/%2e%2e/%2e%2e/.env",
    "guaardvark://outputs/../../.env",
    "/api/batch-image/image/../../../x.png",
])
def test_urls_that_climb_out_are_refused(tree, mcp, ref):
    found = mi.resolve_media_ref(ref, mcp=mcp)
    assert found.path is None and found.refused
    assert "leaves the" in found.error


def test_encoded_separators_in_a_batch_url_do_not_resolve(tree):
    assert mi.resolve_media_ref("/api/batch-image/image/ImageBatch_1/..%2F..%2Fsecret", mcp=False).path is None


def test_a_remote_url_is_never_resolved(tree):
    found = mi.resolve_media_ref("https://example.com/cat.png", mcp=False)
    assert found.path is None and "never downloaded" in found.error


@pytest.mark.parametrize("mcp", [True, False])
@pytest.mark.parametrize("name", [".env", "uploads-credentials", "innocent-symlink", "outputs-url"])
def test_credential_files_are_refused_for_every_caller(tree, mcp, name):
    ref = {
        ".env": str(tree.root / ".env"),
        "uploads-credentials": str(tree.uploads / "credentials.json"),
        "innocent-symlink": str(tree.uploads / "innocent.png"),
        "outputs-url": "/api/outputs/.env",
    }[name]
    (tree.outputs / ".env").write_text("KEY=x")
    found = mi.resolve_media_ref(ref, mcp=mcp)
    assert found.path is None and found.refused
    assert "keys or credentials" in found.error


def test_mcp_reaches_only_uploads_and_outputs(tree):
    ok = mi.resolve_media_ref(str(tree.edit), mcp=True)
    assert ok.path == str(tree.edit.resolve())
    assert mi.resolve_media_ref("generated_images/edit_1.png", mcp=True).path == str(tree.edit.resolve())
    for ref in (str(tree.outside), str(tree.notes), "/api/outputs/generated_images/link.png", "~/.bashrc"):
        found = mi.resolve_media_ref(ref, mcp=True)
        assert found.path is None and found.refused, ref
        assert "uploads and outputs" in found.error
    escaped = mi.resolve_media_ref("../../../outside.png", mcp=True)
    assert escaped.refused and "may not leave" in escaped.error


def test_an_mcp_refusal_does_not_say_whether_the_file_exists(tree):
    present = mi.resolve_media_ref(str(tree.outside), mcp=True)
    absent = mi.resolve_media_ref(str(tree.outside.parent / "absent.png"), mcp=True)
    assert present.refused and absent.refused
    assert present.error.replace("outside.png", "X") == absent.error.replace("absent.png", "X")


def test_chat_keeps_any_existing_path(tree):
    found = mi.resolve_media_ref(str(tree.outside), mcp=False)
    assert found.path == str(tree.outside)
    missing = mi.resolve_media_ref(str(tree.outside.parent / "absent.png"), mcp=False)
    assert missing.path is None and not missing.refused and "not found" in missing.error


def test_within_install_limits_chat_paths_to_the_install(tree):
    inside = mi.resolve_media_ref(str(tree.notes), mcp=False, within_install=True)
    assert inside.path == str(tree.notes)
    outside = mi.resolve_media_ref(str(tree.outside), mcp=False, within_install=True)
    assert outside.refused and "install root" in outside.error
    missing = mi.resolve_media_ref("A fox steals a pie", mcp=False, within_install=True)
    assert missing.path is None and not missing.refused


def test_document_ids_and_links_go_through_the_same_rules(tree):
    files = {1: str(tree.edit), 2: str(tree.root / ".env"), 3: str(tree.outside)}
    lookup = files.get
    assert mi.resolve_media_ref("1", mcp=True, document_path=lookup).path == str(tree.edit.resolve())
    link = mi.resolve_media_ref("/api/files/document/1/download", mcp=True, document_path=lookup)
    assert link.path == str(tree.edit.resolve())
    assert mi.resolve_media_ref("2", mcp=False, document_path=lookup).refused
    assert mi.resolve_media_ref("3", mcp=True, document_path=lookup).refused
    assert mi.resolve_media_ref("3", mcp=False, document_path=lookup).path == str(tree.outside)
    missing = mi.resolve_media_ref("4", mcp=False, document_path=lookup)
    assert missing.path is None and "not found" in missing.error
    assert mi.resolve_media_ref("/api/files/document/1/download", mcp=True).path is None


def test_document_id_from_ref():
    assert mi.document_id_from_ref("12") == 12
    assert mi.document_id_from_ref("http://127.0.0.1:5000/api/files/document/12/download") == 12
    assert mi.document_id_from_ref("/api/outputs/12.png") is None


def test_error_messages_say_what_is_accepted(tree):
    over_mcp = mi.resolve_media_ref(str(tree.outside), mcp=True).error
    assert "Accepted over MCP" in over_mcp and "guaardvark://outputs/" in over_mcp
    assert "the path of an existing file" in mi.accepted_forms(mcp=False)
    assert "document id" in mi.accepted_forms(mcp=True, documents=True)


def test_empty_ref_is_neither_a_path_nor_an_error(tree):
    assert mi.resolve_media_ref("  ", mcp=True) == mi.MediaRef()
