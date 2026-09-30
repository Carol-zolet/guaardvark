"""Outreach tools on the MCP path: arguments are checked before anything runs.

No database, network or model: the backend HTTP client and the persona are
replaced with recorders.
"""

from __future__ import annotations

import pytest

from backend.mcp.tools_adapter import _tool_input_schema
from backend.services.social_outreach import transitions
from backend.tools import outreach_tools as ot
from backend.utils.backend_http import BackendResponse


def _mcp(tool):
    tool.set_context({"transport": "mcp"})
    return tool


def _row(row_id, status, created):
    return {"id": row_id, "platform": "reddit", "action": "comment", "status": status,
            "grade_score": 0.8, "target_url": "u", "draft_text": f"d{row_id}", "created_at": created}


class _Backend:
    """Answers request_json from a path -> rows table and records every call."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, payload))
        if method == "POST":
            data = {"id": 1, "status": "drafted"}
            return BackendResponse(status=201, body=data, data=data)
        rows = self.answers.get(path, [])
        return BackendResponse(status=200, body=rows, data=rows)


@pytest.fixture
def backend(monkeypatch):
    fake = _Backend({
        # GET /approved answers oldest first, the order the posting tick takes.
        "/api/social-outreach/approved": [
            _row(1, "approved", "2026-09-01T10:00:00"),
            _row(2, "approved", "2026-09-02T10:00:00"),
            _row(3, "approved", "2026-09-03T10:00:00"),
        ],
        "/api/social-outreach/queue": [
            _row(6, "drafted", "2026-09-06T10:00:00"),
            _row(5, "drafted", "2026-09-05T10:00:00"),
        ],
        "/api/social-outreach/audit": [
            _row(9, "posted", "2026-09-09T10:00:00"),
            _row(8, "rejected", "2026-09-08T10:00:00"),
            _row(7, "posted", "2026-09-07T10:00:00"),
        ],
    })
    monkeypatch.setattr(ot, "request_json", fake)
    return fake


@pytest.fixture
def persona_modes(monkeypatch):
    modes = []

    def draft(**kwargs):
        modes.append(kwargs["mode"])
        return {"draft": "a draft", "grade": 0.9, "reason": "r"}

    monkeypatch.setattr(ot.persona, "draft_outreach_text", draft)
    return modes


# ---- outreach_draft_post: mode ---------------------------------------------------

@pytest.mark.parametrize("mode", ["comments", "reply", "post", "shares"])
def test_draft_refuses_a_mode_it_does_not_have(backend, persona_modes, mode):
    result = _mcp(ot.OutreachDraftPostTool()).execute(
        platform="reddit", mode=mode, thread_context="a thread about local models")

    assert not result.success
    assert "mode must be one of" in result.error
    assert persona_modes == []   # nothing was drafted
    assert backend.calls == []   # nothing was queued


@pytest.mark.parametrize("mode", ["comment", "Comment", " comment ", "", None])
def test_draft_comment_mode_queues_a_comment(backend, persona_modes, mode):
    result = _mcp(ot.OutreachDraftPostTool()).execute(
        platform="reddit", mode=mode, thread_context="a thread about local models")

    assert result.success, result.error
    assert persona_modes == ["comment"]
    assert [payload["action"] for _, _, payload in backend.calls] == ["comment"]


def test_draft_mode_is_published_as_an_enum():
    schema = _tool_input_schema(ot.OutreachDraftPostTool())

    assert schema["properties"]["mode"]["enum"] == ["comment", "share"]


# ---- outreach_list_queue: status and order -----------------------------------------

@pytest.mark.parametrize("status", ["pending", "queued", "done"])
def test_list_refuses_a_status_that_does_not_exist(backend, status):
    result = _mcp(ot.OutreachListQueueTool()).execute(status=status)

    assert not result.success
    assert "status must be one of" in result.error
    assert backend.calls == []


def test_list_refuses_an_unknown_status_in_the_backend_too():
    # The chat path would otherwise run a query that can only match nothing.
    result = ot.OutreachListQueueTool().execute(status="pending")

    assert not result.success
    assert "status must be one of" in result.error


def test_list_approved_is_oldest_first_like_the_posting_tick(backend):
    result = _mcp(ot.OutreachListQueueTool()).execute(status="approved", limit=2)

    assert result.success
    assert [row["id"] for row in result.output["rows"]] == [1, 2]
    assert result.output["order"] == "oldest_first"


@pytest.mark.parametrize("status, ids", [("drafted", [6, 5]), ("posted", [9, 7]), ("rejected", [8])])
def test_list_other_statuses_are_newest_first(backend, status, ids):
    result = _mcp(ot.OutreachListQueueTool()).execute(status=status)

    assert [row["id"] for row in result.output["rows"]] == ids
    assert result.output["order"] == "newest_first"


def test_list_status_is_published_as_the_real_statuses():
    schema = _tool_input_schema(ot.OutreachListQueueTool())

    assert schema["properties"]["status"]["enum"] == list(transitions.KNOWN_STATUSES)
    assert schema["properties"]["status"]["default"] == "drafted"
