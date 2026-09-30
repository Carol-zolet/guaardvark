"""The read-only code tools as an MCP client sees them: read_code returns a
large file in pages and a line range on request.

Each test works on a small tree under tmp_path, which is not a git checkout.
No backend, database, GPU or network."""
import pytest

import backend.tools.agent_tools.code_manipulation_tools as cmt
import backend.tools.llama_code_tools as lct


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(lct, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _mcp(tool_class):
    tool = tool_class()
    tool.set_context({"transport": "mcp"})
    return tool


def _body(output):
    return output.split(cmt._CONTENT_START, 1)[1].rsplit(cmt._CONTENT_END, 1)[0]


# --- read_code ----------------------------------------------------------

def test_a_small_file_without_a_range_is_returned_as_before(checkout):
    (checkout / "small.py").write_text("a = 1\nb = 2\n")
    result = _mcp(cmt.ReadCodeTool).execute(filepath="small.py")
    assert result.success
    assert result.output == lct.read_code("small.py", allow_external=False)
    assert "Showing lines" not in result.output
    assert result.metadata["complete"] is True


def test_a_large_file_comes_back_in_pages_that_add_up_to_the_file(checkout, monkeypatch):
    monkeypatch.setattr(cmt, "READ_CODE_PAGE_CHARS", 200)
    source = "".join(f"line_{i:03d} = {i}\n" for i in range(1, 101))
    (checkout / "big.py").write_text(source)
    tool = _mcp(cmt.ReadCodeTool)

    first = tool.execute(filepath="big.py")
    assert first.success and first.metadata["complete"] is False
    assert "Showing lines 1-" in first.output and "of 100" in first.output
    assert f"start_line={first.metadata['next_start_line']}" in first.output

    pages, next_line = [_body(first.output)], first.metadata["next_start_line"]
    while next_line:
        page = tool.execute(filepath="big.py", start_line=next_line)
        assert page.success
        pages.append(_body(page.output))
        next_line = page.metadata["next_start_line"]
    assert len(pages) > 2
    assert all(len(page) <= 200 for page in pages)
    assert "\n".join(pages) + "\n" == source


def test_a_line_range_returns_exactly_those_lines(checkout):
    (checkout / "mod.py").write_text("".join(f"l{i}\n" for i in range(1, 21)))
    tool = _mcp(cmt.ReadCodeTool)

    result = tool.execute(filepath="mod.py", start_line=5, end_line=7)
    assert _body(result.output) == "l5\nl6\nl7"
    assert "Showing lines 5-7 of 20" in result.output
    assert "start_line=8" in result.output
    assert (result.metadata["start_line"], result.metadata["end_line"]) == (5, 7)

    to_the_end = tool.execute(filepath="mod.py", start_line=19, end_line=500)
    assert _body(to_the_end.output) == "l19\nl20"
    assert to_the_end.metadata["next_start_line"] is None

    as_strings = tool.execute(filepath="mod.py", start_line="2", end_line="2")
    assert _body(as_strings.output) == "l2"


@pytest.mark.parametrize("arguments,expected", [
    ({"start_line": 21}, "past the end"),
    ({"start_line": 3, "end_line": 1}, "before start_line"),
    ({"start_line": 0}, "1 or more"),
    ({"end_line": "many"}, "whole number"),
])
def test_a_range_outside_the_file_is_an_error(checkout, arguments, expected):
    (checkout / "mod.py").write_text("".join(f"l{i}\n" for i in range(1, 21)))
    result = _mcp(cmt.ReadCodeTool).execute(filepath="mod.py", **arguments)
    assert not result.success
    assert expected in result.error


def test_one_line_longer_than_a_page_is_cut_and_said_so(checkout, monkeypatch):
    monkeypatch.setattr(cmt, "READ_CODE_PAGE_CHARS", 50)
    (checkout / "min.js").write_text("x" * 500 + "\nvar y = 1;\n")
    result = _mcp(cmt.ReadCodeTool).execute(filepath="min.js")
    assert result.success
    assert _body(result.output) == "x" * 50
    assert "is cut at 50" in result.output and "start_line=2" in result.output
