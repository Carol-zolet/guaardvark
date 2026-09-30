"""GET /outputs/<path>: personal-record folders need localhost or the API key.

Builds its own outputs tree in a temp folder and drives the real blueprint
through Flask's test client; no backend, GPU or network.
"""

import os

import pytest
from flask import Flask

from backend.routes.download_route import download_bp
from backend.utils import auth_guard

REMOTE = "192.0.2.10"  # TEST-NET-1: never one of this machine's addresses
LOCAL = "127.0.0.1"

MEDIA = "generated_images/cat.png"
RECORDS = [
    "chat-exports/chats-20260101-000000/index.json",
    "screenshots/agent_capture_1.webp",
    "consent/abc123.consent",
    "training/demo/s00.wav",
    "generated_images/cat.png.consent",
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    for rel in [MEDIA] + RECORDS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    # Pin the machine's own addresses so no interface probe runs.
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    app = Flask(__name__)
    app.config["OUTPUT_DIR"] = str(root)
    app.register_blueprint(download_bp)
    test_client = app.test_client()
    test_client.outputs_root = root
    return test_client


def _get(client, rel, addr, headers=None):
    return client.get(f"/outputs/{rel}", environ_base={"REMOTE_ADDR": addr}, headers=headers or {})


def test_generated_media_stays_open_to_other_hosts(client):
    assert _get(client, MEDIA, REMOTE).status_code == 200


@pytest.mark.parametrize("rel", RECORDS)
def test_records_are_refused_to_other_hosts_without_a_key(client, rel):
    assert _get(client, rel, REMOTE).status_code == 403


@pytest.mark.parametrize("rel", RECORDS)
def test_records_are_served_to_localhost_without_a_key(client, rel):
    assert _get(client, rel, LOCAL).status_code == 200


def test_a_missing_record_is_refused_not_reported_missing(client):
    # A 404 here would let a remote host enumerate export timestamps.
    assert _get(client, "chat-exports/chats-20990101-000000/index.json", REMOTE).status_code == 403
    assert _get(client, "generated_images/missing.png", REMOTE).status_code == 404


@pytest.mark.parametrize("rel", [
    "generated_images/%2E%2E/chat-exports/chats-20260101-000000/index.json",
    "generated_images/../chat-exports/chats-20260101-000000/index.json",
    "chat%2Dexports/chats-20260101-000000/index.json",
    "./screenshots/agent_capture_1.webp",
    "CHAT-EXPORTS/chats-20260101-000000/index.json",
])
def test_encoded_dotted_or_recased_paths_do_not_dodge_the_rule(client, rel):
    assert _get(client, rel, REMOTE).status_code == 403


def test_a_symlink_from_a_media_folder_into_records_is_protected(client):
    root = client.outputs_root
    os.symlink(root / "chat-exports" / "chats-20260101-000000" / "index.json",
               root / "generated_images" / "looks_like_media.json")
    assert _get(client, "generated_images/looks_like_media.json", REMOTE).status_code == 403
    assert _get(client, "generated_images/looks_like_media.json", LOCAL).status_code == 200


def test_a_lan_device_through_the_local_proxy_counts_as_remote(client):
    response = _get(client, RECORDS[0], LOCAL, headers={"X-Forwarded-For": REMOTE})
    assert response.status_code == 403


def test_with_an_api_key_set_records_need_the_key(client, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "k-test")
    assert _get(client, RECORDS[0], REMOTE).status_code == 401
    assert _get(client, RECORDS[0], REMOTE, headers={"X-API-Key": "wrong"}).status_code == 401
    assert _get(client, RECORDS[0], REMOTE, headers={"X-API-Key": "k-test"}).status_code == 200
    # Same rule as every protected route: with a key configured, localhost sends it too.
    assert _get(client, RECORDS[0], LOCAL).status_code == 401
    assert _get(client, MEDIA, REMOTE).status_code == 200


@pytest.mark.parametrize("rel,protected", [
    ("chat-exports/a.md", True),
    ("screenshots/a.png", True),
    ("consent/x.consent", True),
    ("training/x/s00.wav", True),
    ("generated_images/a.png.CONSENT", True),
    ("generated_images/a.png", False),
    ("videos/training/a.mp4", False),
    ("chat-exports-old/a.md", False),
    ("", False),
])
def test_is_protected_output(rel, protected):
    assert auth_guard.is_protected_output(rel) is protected
