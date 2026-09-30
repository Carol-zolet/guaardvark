"""Settings → API key: GET /api/auth/status, and creating, replacing and
removing the key.

Drives the real auth blueprint and the tools blueprint behind the real auth
hook through Flask's test client, with a stand-in tool registry and a
temporary .env; no backend, GPU or network.
"""

import stat
from types import SimpleNamespace

import pytest
from flask import Flask

from backend import profiles as P
from backend.api.auth_api import auth_bp
from backend.api.tools_api import tools_bp
from backend.services.agent_tools import ToolResult
from backend.utils import auth_guard

REMOTE = "192.0.2.10"  # TEST-NET-1: never one of this machine's addresses
LOCAL = "127.0.0.1"
EXEC = {"tool_name": "echo", "parameters": {}}
ENV_NAMES = ("GUAARDVARK_API_KEY", "GUAARDVARK_PROTECT_TOOL_ENDPOINTS", "GUAARDVARK_DOCKER")


@pytest.fixture
def env_root(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    env = root / ".env"
    env.write_text("DATABASE_URL=postgresql://x\n")
    env.chmod(0o600)
    monkeypatch.setattr(P, "repo_root", lambda: root)
    return root


@pytest.fixture
def client(env_root, monkeypatch):
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    for name in ENV_NAMES:
        # setenv first so teardown removes whatever the routes put in
        # os.environ during the test; delenv alone records nothing when the
        # variable is absent.
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    app = Flask(__name__)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(auth_bp)
    app.register_blueprint(tools_bp)
    app.tool_registry = SimpleNamespace(
        get_tool=lambda name: SimpleNamespace(parameters={}),
        execute_tool=lambda name, **params: ToolResult(success=True, output="ran"),
    )
    return app.test_client()


def _call(client, method, path, addr, key=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if key is not None:
        headers["X-API-Key"] = key
    return client.open(path, method=method, environ_base={"REMOTE_ADDR": addr}, headers=headers, **kwargs)


def _status(client, addr, key=None):
    return _call(client, "GET", "/api/auth/status", addr, key).get_json()


def _create(client):
    response = _call(client, "POST", "/api/auth/key", LOCAL, json={})
    assert response.status_code == 201
    return response.get_json()["key"]


def _env_keys(root):
    return [line for line in (root / ".env").read_text().splitlines() if line.startswith("GUAARDVARK_API_KEY=")]


def test_status_says_where_protected_actions_work_without_a_key(client):
    here = _call(client, "GET", "/api/auth/status", LOCAL)
    assert here.headers["Cache-Control"] == "no-store"
    body = here.get_json()
    assert body["key_required"] is False and body["this_machine"] is True and body["key_ok"] is False
    assert body["can_run_protected"] is True and body["can_manage_key"] is True
    assert "Running tools directly (Tools page) and their jobs" in body["protected"]
    elsewhere = _status(client, REMOTE)
    assert elsewhere["this_machine"] is False
    assert elsewhere["can_run_protected"] is False and elsewhere["can_manage_key"] is False


def test_create_writes_env_and_applies_at_once(client, env_root, monkeypatch):
    key = _create(client)
    assert len(key) >= 43
    assert _env_keys(env_root) == [f"GUAARDVARK_API_KEY={key}"]
    assert "DATABASE_URL=postgresql://x" in (env_root / ".env").read_text()
    assert stat.S_IMODE((env_root / ".env").stat().st_mode) == 0o600
    # No restart: this machine now needs the key too.
    refused = _call(client, "POST", "/api/tools/execute", LOCAL, json=EXEC)
    assert refused.status_code == 401 and refused.get_json()["code"] == auth_guard.API_KEY_CODE
    assert _call(client, "POST", "/api/tools/execute", LOCAL, key, json=EXEC).status_code == 200
    assert _call(client, "POST", "/api/tools/execute", REMOTE, key, json=EXEC).status_code == 200


def test_status_answers_the_key_check_and_never_carries_the_key(client):
    key = _create(client)
    response = _call(client, "GET", "/api/auth/status", LOCAL)
    assert key not in response.get_data(as_text=True)
    body = response.get_json()
    assert body["key_required"] is True and body["key_ok"] is False and body["can_run_protected"] is False
    right = _status(client, REMOTE, key)
    assert right["key_ok"] is True and right["can_run_protected"] is True and right["can_manage_key"] is True
    assert _status(client, REMOTE, "wrong")["key_ok"] is False


def test_create_answers_only_this_machine_and_only_json(client):
    refused = _call(client, "POST", "/api/auth/key", REMOTE, json={})
    assert refused.status_code == 403 and refused.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE
    # A form post needs no preflight, so a page on another site could send it.
    assert _call(client, "POST", "/api/auth/key", LOCAL, data="x").status_code == 415


def test_create_twice_is_refused(client):
    key = _create(client)
    assert _call(client, "POST", "/api/auth/key", LOCAL, json={}).status_code == 401
    again = _call(client, "POST", "/api/auth/key", LOCAL, key, json={})
    assert again.status_code == 409 and again.get_json()["code"] == "key_exists"


def test_replace_needs_the_current_key_and_retires_it(client, env_root):
    old = _create(client)
    assert _call(client, "PUT", "/api/auth/key", REMOTE, json={}).status_code == 401
    replaced = _call(client, "PUT", "/api/auth/key", REMOTE, old, json={})
    assert replaced.status_code == 200 and replaced.headers["Cache-Control"] == "no-store"
    new = replaced.get_json()["key"]
    assert new != old
    assert _env_keys(env_root) == [f"GUAARDVARK_API_KEY={new}"]
    assert _call(client, "POST", "/api/tools/execute", REMOTE, old, json=EXEC).status_code == 401
    assert _call(client, "POST", "/api/tools/execute", REMOTE, new, json=EXEC).status_code == 200


def test_remove_needs_the_current_key_and_returns_to_this_machine_only(client, env_root):
    key = _create(client)
    assert _call(client, "DELETE", "/api/auth/key", LOCAL).status_code == 401
    removed = _call(client, "DELETE", "/api/auth/key", REMOTE, key)
    assert removed.status_code == 200 and removed.get_json()["removed"] is True
    assert _env_keys(env_root) == []
    assert "DATABASE_URL=postgresql://x" in (env_root / ".env").read_text()
    refused = _call(client, "POST", "/api/tools/execute", REMOTE, key, json=EXEC)
    assert refused.status_code == 403 and refused.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE
    assert _call(client, "POST", "/api/tools/execute", LOCAL, json=EXEC).status_code == 200
    assert _call(client, "DELETE", "/api/auth/key", LOCAL).get_json()["removed"] is False


def test_a_key_set_outside_env_is_changed_where_it_was_set(client, env_root, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "from-the-environment")
    body = _status(client, LOCAL, "from-the-environment")
    assert body["key_ok"] is True and body["can_manage_key"] is False
    assert "outside Settings" in body["manage_note"]
    refused = _call(client, "PUT", "/api/auth/key", LOCAL, "from-the-environment", json={})
    assert refused.status_code == 409 and refused.get_json()["code"] == "key_not_manageable"
    assert _env_keys(env_root) == []


def test_docker_names_the_compose_env(client, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_DOCKER", "1")
    monkeypatch.setenv("GUAARDVARK_API_KEY", "compose-key")
    body = _status(client, REMOTE, "compose-key")
    assert body["docker"] is True and body["can_manage_key"] is False
    assert "docker-compose.yml" in body["manage_note"]


def test_a_key_in_env_that_is_not_loaded_asks_for_a_restart(client, env_root):
    (env_root / ".env").write_text("GUAARDVARK_API_KEY=written-by-hand\n")
    body = _status(client, LOCAL)
    assert body["key_required"] is False and body["restart_needed"] is True
    assert body["can_manage_key"] is False and "Restart" in body["manage_note"]
    assert _call(client, "POST", "/api/auth/key", LOCAL, json={}).status_code == 409


def test_the_cluster_proxy_does_not_pass_this_installs_key_on():
    from backend.services.cluster_proxy import HttpProxyForwarder

    incoming = {"X-API-Key": "this-install", "Content-Type": "application/json", "Connection": "keep-alive"}
    out = HttpProxyForwarder()._sanitize_headers(incoming, SimpleNamespace(api_key="node-key"), None)
    assert "X-API-Key" not in out and "Connection" not in out
    assert out["Content-Type"] == "application/json"
    assert out["X-Guaardvark-API-Key"] == "node-key"
