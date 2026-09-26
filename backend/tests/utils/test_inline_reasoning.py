"""split_inline_reasoning and InlineReasoningStream, on the content forms Ollama
0.33.3 returned with think:false (2026-09-26)."""
import pytest

from backend.utils.inline_reasoning import (
    REASONING, RETRACT, VISIBLE, InlineReasoningStream, split_inline_reasoning,
)

R = "3:40 plus 95 minutes is 5:15."
A = "The train arrives at 5:15."


@pytest.mark.parametrize("text, expected", [
    (A, ("", A)),
    (f"<think>\n{R}\n</think>\n{A}", (R, A)),          # lfm2.5
    (f"{R}\n</think>\n{A}", (R, A)),                   # granite4.2: no opening tag
    (f"<THINK>{R}</Think>{A}", (R, A)),                # tag case
    (f"<think>{R}", (R, "")),                          # never closed: all reasoning
    (f"<think>{R}</think>{A} <think>more</think> end", (R + "more", A + " end")),
    (f"<think>{R}</think>{A} and </think> stays", (R, f"{A} and </think> stays")),
    ("a < b and x <th y", ("", "a < b and x <th y")),  # tag-like text that is not a tag
    ("", ("", "")),
])
def test_split(text, expected):
    assert split_inline_reasoning(text) == expected


def _events(text, size):
    stream = InlineReasoningStream()
    out = []
    for i in range(0, len(text), size):
        out += stream.feed(text[i:i + size])
    return out + stream.finish()


@pytest.mark.parametrize("size", [1, 2, 3, 7, 1000])
@pytest.mark.parametrize("text", [
    A, f"<think>\n{R}\n</think>\n{A}", f"{R}\n</think>\n{A}", f"<think>{R}",
])
def test_stream_matches_split_at_any_chunk_size(text, size):
    reasoning, answer = [], []
    for kind, piece in _events(text, size):
        if kind == VISIBLE:
            answer.append(piece)
        elif kind == REASONING:
            reasoning.append(piece)
        else:
            answer.clear()
            reasoning.append(piece)
    assert ("".join(reasoning).strip(), "".join(answer).strip()) == split_inline_reasoning(text)


def test_stream_never_shows_a_tag():
    for kind, piece in _events(f"<think>{R}</think>{A}", 1):
        if kind == VISIBLE:
            assert "<" not in piece and ">" not in piece


def test_lone_closing_tag_retracts_what_was_shown():
    events = _events(f"{R}</think>{A}", 4)
    kinds = [k for k, _ in events]
    assert RETRACT in kinds
    retracted = next(p for k, p in events if k == RETRACT)
    assert R.startswith(retracted) and retracted
