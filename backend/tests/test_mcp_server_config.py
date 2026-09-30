"""The MCP server's own configuration: its on/off switch and per-call ceiling
(kept apart from the backend's MCP client settings, which share ``.env``), and
how ``data/config/mcp.json`` is read."""

import json
import logging
from pathlib import Path

import pytest

from backend.mcp import config as mcp_config
from backend.mcp import tools_adapter
from backend.mcp.config import MCPConfig

_ENV = ("GUAARDVARK_MCP_SERVER_ENABLED", "GUAARDVARK_MCP_SERVER_TIMEOUT",
        "GUAARDVARK_MCP_TIMEOUT", "GUAARDVARK_MCP_ENABLED")


@pytest.fixture
def mcp_json(tmp_path, monkeypatch):
    """``load_config`` reads a file in ``tmp_path`` and none of the MCP
    variables are set. Returns a function that writes the file."""
    path = tmp_path / "mcp.json"
    monkeypatch.setattr(mcp_config, "_config_path", lambda: path)
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)

    def write(document):
        path.write_text(document if isinstance(document, str) else json.dumps(document))

    return write


# ---- the on/off switch ------------------------------------------------------------------------
def test_the_clients_switch_does_not_stop_the_server(mcp_json, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_MCP_ENABLED", "false")

    assert mcp_config.load_config().enabled is True


def test_the_creator_profile_leaves_the_server_on(mcp_json, monkeypatch):
    """The profile turns the backend's MCP client off, and the server process
    loads the same profile."""
    profile = Path(mcp_config.__file__).resolve().parents[1] / "profiles" / "creator.json"
    env = json.loads(profile.read_text())["env"]
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))

    assert env["GUAARDVARK_MCP_ENABLED"] == "false"
    assert mcp_config.load_config().enabled is True


@pytest.mark.parametrize("value,enabled", [("false", False), ("0", False), ("off", False),
                                           ("true", True), ("1", True), ("maybe", True)])
def test_the_servers_own_switch(mcp_json, monkeypatch, value, enabled):
    monkeypatch.setenv("GUAARDVARK_MCP_SERVER_ENABLED", value)

    cfg = mcp_config.load_config()
    assert cfg.enabled is enabled
    assert cfg.disabled_by == ("" if enabled else "GUAARDVARK_MCP_SERVER_ENABLED")


def test_mcp_json_switches_the_server_off_and_the_environment_overrides_it(mcp_json, monkeypatch):
    mcp_json({"server": {"enabled": False}})
    cfg = mcp_config.load_config()
    assert cfg.enabled is False and "mcp.json" in cfg.disabled_by

    monkeypatch.setenv("GUAARDVARK_MCP_SERVER_ENABLED", "true")
    cfg = mcp_config.load_config()
    assert cfg.enabled is True and cfg.disabled_by == ""


def test_a_switched_off_server_is_not_built():
    from backend.mcp.server import MCPServerDisabled, build_server

    with pytest.raises(MCPServerDisabled, match="switched off by GUAARDVARK_MCP_SERVER_ENABLED"):
        build_server(MCPConfig(enabled=False, disabled_by="GUAARDVARK_MCP_SERVER_ENABLED"))


def test_the_refusal_goes_to_stderr_and_exits_non_zero(capsys, monkeypatch):
    # Importing the entrypoint marks the process as the MCP server; keep that
    # mark from outliving this test.
    monkeypatch.setenv("GUAARDVARK_MCP_PROCESS", "1")
    from backend.mcp.__main__ import _refuse_to_start

    assert _refuse_to_start(RuntimeError("switched off")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "switched off" in captured.err


# ---- the per-call ceiling ---------------------------------------------------------------------
@pytest.mark.parametrize("env,expected", [
    ({}, 120),
    ({"GUAARDVARK_MCP_TIMEOUT": "45"}, 45),
    ({"GUAARDVARK_MCP_SERVER_TIMEOUT": "600"}, 600),
    ({"GUAARDVARK_MCP_SERVER_TIMEOUT": "600", "GUAARDVARK_MCP_TIMEOUT": "30"}, 600),
    ({"GUAARDVARK_MCP_SERVER_TIMEOUT": "abc", "GUAARDVARK_MCP_TIMEOUT": "45"}, 45),
])
def test_the_servers_variable_wins_and_the_shared_one_is_the_fallback(mcp_json, monkeypatch, env, expected):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert mcp_config.load_config().timeout_seconds == expected


def test_the_timeout_text_names_the_servers_variable():
    text = tools_adapter._timeout_message("search_code", 120, read_only=True)

    assert "GUAARDVARK_MCP_SERVER_TIMEOUT" in text


@pytest.mark.parametrize("value", [0, -5, "0", True])
def test_a_timeout_below_the_floor_is_refused(mcp_json, value):
    mcp_json({"server": {"timeout_seconds": value}})

    assert mcp_config.load_config().timeout_seconds == MCPConfig().timeout_seconds


@pytest.mark.parametrize("name", ["GUAARDVARK_MCP_SERVER_TIMEOUT", "GUAARDVARK_MCP_TIMEOUT"])
def test_a_zero_timeout_in_the_environment_keeps_the_files_value(mcp_json, monkeypatch, name):
    mcp_json({"server": {"timeout_seconds": 300}})
    monkeypatch.setenv(name, "0")

    assert mcp_config.load_config().timeout_seconds == 300


def test_the_floor_itself_is_accepted(mcp_json, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_MCP_SERVER_TIMEOUT", str(mcp_config.MIN_TIMEOUT_SECONDS))

    assert mcp_config.load_config().timeout_seconds == mcp_config.MIN_TIMEOUT_SECONDS


# ---- data/config/mcp.json ---------------------------------------------------------------------
@pytest.mark.parametrize("document", [
    {"server": {"tools": None}},
    {"server": {"tools": {"deny_categories": None}}},
    {"server": []},
    [],
    {"server": {"resources": None}},
    "{ not json",
])
def test_a_section_of_the_wrong_type_falls_back_to_defaults_and_says_so(mcp_json, caplog, document):
    mcp_json(document)

    with caplog.at_level(logging.WARNING, logger=mcp_config.logger.name):
        cfg = mcp_config.load_config()

    assert cfg == MCPConfig()
    assert [record for record in caplog.records if record.name == mcp_config.logger.name]


def test_a_single_name_is_one_name_not_its_characters(mcp_json):
    mcp_json({"server": {"tools": {"allow": "system_command", "deny": "web_search"}}})

    cfg = mcp_config.load_config()
    assert cfg.tools.allow == ["system_command"] and cfg.tools.deny == ["web_search"]


def test_one_bad_value_does_not_drop_the_settings_around_it(mcp_json):
    mcp_json({"server": {
        "timeout_seconds": "90.5",
        "tools": {"deny": ["web_search"]},
        "resources": {"max_inline_bytes": "lots", "outputs_root_files": False},
    }})

    cfg = mcp_config.load_config()
    assert cfg.timeout_seconds == MCPConfig().timeout_seconds
    assert cfg.tools.deny == ["web_search"] and cfg.resources.outputs_root_files is False
    assert cfg.resources.max_inline_bytes == MCPConfig().resources.max_inline_bytes


def test_an_unreadable_safety_flag_keeps_the_safe_default(mcp_json):
    mcp_json({"server": {"tools": {"hide_dangerous": "", "hide_approval_required": None}}})

    cfg = mcp_config.load_config()
    assert cfg.tools.hide_dangerous is True and cfg.tools.hide_approval_required is True


def test_argument_defaults_merge_with_the_built_in_map(mcp_json):
    built_in = {"generate_image": {"wait_for_result": False}}
    assert MCPConfig().tools.argument_defaults == built_in

    mcp_json({"server": {"tools": {"argument_defaults": {"generate_video": {"audio": False}}}}})
    assert mcp_config.load_config().tools.argument_defaults == {**built_in, "generate_video": {"audio": False}}

    mcp_json({"server": {"tools": {"argument_defaults": {"generate_image": {"wait_for_result": True}}}}})
    assert mcp_config.load_config().tools.argument_defaults == {"generate_image": {"wait_for_result": True}}
    assert MCPConfig().tools.argument_defaults == built_in
