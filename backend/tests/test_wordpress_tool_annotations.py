"""The WordPress page tools call the local LLM for minutes, so they are not
advertised as read-only: an MCP client is offered an idempotency key, and the
timeout text does not invite a blind retry.
"""

import pytest

from backend.mcp.tools_adapter import (
    IDEMPOTENCY_KEY,
    _annotations,
    _timeout_message,
    _tool_input_schema,
)
from backend.tools.content_tools import EnhancedWordPressContentTool, WordPressContentTool


@pytest.mark.parametrize("tool_class", [WordPressContentTool, EnhancedWordPressContentTool])
def test_wordpress_tools_are_not_read_only_and_not_destructive(tool_class):
    hints = _annotations(tool_class())

    assert hints.read_only_hint is False
    assert hints.destructive_hint is False


@pytest.mark.parametrize("tool_class", [WordPressContentTool, EnhancedWordPressContentTool])
def test_wordpress_tools_offer_an_idempotency_key(tool_class):
    tool = tool_class()
    schema = _tool_input_schema(tool, None, accepts_idempotency_key=tool.read_only is not True)

    assert IDEMPOTENCY_KEY in schema["properties"]
    assert IDEMPOTENCY_KEY in tool.description


@pytest.mark.parametrize("tool_class", [WordPressContentTool, EnhancedWordPressContentTool])
def test_a_timeout_does_not_call_a_retry_safe(tool_class):
    tool = tool_class()
    message = _timeout_message(tool.name, 120, tool.read_only is True)

    assert "calling it again is safe" not in message
    assert "would start a second run" in message
