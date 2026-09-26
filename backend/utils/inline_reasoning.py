"""Reasoning a model writes into its answer text, told apart from the answer.

Ollama moves reasoning into ``message.thinking`` when it can. Some models write
it into ``message.content`` instead, and do so most often when asked not to
think (``think: false``). The forms seen on a live Ollama 0.33.3, all asked the
same one-line question with ``think: false`` (2026-09-26):

* ``lfm2.5:8b``: ``<think>`` reasoning ``</think>`` answer.
* ``granite4.2:8b``: reasoning ``</think>`` answer, with no opening tag.
* ``magistral:24b``: untagged reasoning and no answer. Nothing in the text
  marks it, so no splitter can separate it; that model needs thinking left on.

:class:`InlineReasoningStream` sorts a content stream token by token;
:func:`split_inline_reasoning` does the same for a finished text. Both treat
text before a closing tag that has no opening tag as reasoning, and an opening
tag that is never closed as reasoning to the end.
"""
from __future__ import annotations

from typing import List, Tuple

OPEN_TAG = "<think>"
CLOSE_TAG = "</think>"

# Events returned by InlineReasoningStream.feed / finish.
VISIBLE = "visible"      # answer text, safe to show
REASONING = "reasoning"  # reasoning text, for the reasoning channel
RETRACT = "retract"      # the answer text already shown was reasoning; the
                         # payload is that text, now to be treated as reasoning


def _partial_tag_len(text: str) -> int:
    """Length of the longest tail of ``text`` that could still grow into a tag."""
    lower = text.lower()
    for n in range(min(len(lower), len(CLOSE_TAG) - 1), 0, -1):
        tail = lower[-n:]
        if OPEN_TAG.startswith(tail) or CLOSE_TAG.startswith(tail):
            return n
    return 0


class InlineReasoningStream:
    """Sort streamed answer text into visible text and reasoning.

    ``feed`` takes each content token and returns ``(kind, text)`` events.
    Only a tail that could be the start of a tag is held back, so visible text
    streams without delay. ``finish`` releases whatever is still held.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False
        self._seen_tag = False
        self._shown = ""  # visible text released so far, for RETRACT

    def feed(self, token: str) -> List[Tuple[str, str]]:
        self._buf += token or ""
        events: List[Tuple[str, str]] = []
        while True:
            lower = self._buf.lower()
            if self._inside:
                end = lower.find(CLOSE_TAG)
                if end < 0:
                    hold = _partial_tag_len(self._buf)
                    self._emit(events, REASONING, self._buf[: len(self._buf) - hold])
                    self._buf = self._buf[len(self._buf) - hold:]
                    return events
                self._emit(events, REASONING, self._buf[:end])
                self._buf = self._buf[end + len(CLOSE_TAG):].lstrip()
                self._inside = False
                continue

            start = lower.find(OPEN_TAG)
            end = lower.find(CLOSE_TAG) if not self._seen_tag else -1
            if end >= 0 and (start < 0 or end < start):
                # A closing tag with no opening one: everything before it,
                # including what was already shown, was reasoning.
                self._seen_tag = True
                if self._shown:
                    events.append((RETRACT, self._shown))
                    self._shown = ""
                self._emit(events, REASONING, self._buf[:end])
                self._buf = self._buf[end + len(CLOSE_TAG):].lstrip()
                continue
            if start >= 0:
                self._seen_tag = True
                self._emit(events, VISIBLE, self._buf[:start])
                self._buf = self._buf[start + len(OPEN_TAG):]
                self._inside = True
                continue

            hold = _partial_tag_len(self._buf)
            self._emit(events, VISIBLE, self._buf[: len(self._buf) - hold])
            self._buf = self._buf[len(self._buf) - hold:]
            return events

    def finish(self) -> List[Tuple[str, str]]:
        events: List[Tuple[str, str]] = []
        self._emit(events, REASONING if self._inside else VISIBLE, self._buf)
        self._buf = ""
        return events

    def _emit(self, events: List[Tuple[str, str]], kind: str, text: str) -> None:
        if not text:
            return
        if kind == VISIBLE:
            self._shown += text
        events.append((kind, text))


def split_inline_reasoning(text: str) -> Tuple[str, str]:
    """``(reasoning, answer)`` from a finished text, both stripped."""
    stream = InlineReasoningStream()
    reasoning: List[str] = []
    answer: List[str] = []
    for kind, piece in stream.feed(text or "") + stream.finish():
        if kind == VISIBLE:
            answer.append(piece)
        elif kind == REASONING:
            reasoning.append(piece)
        else:  # RETRACT
            answer.clear()
            reasoning.append(piece)
    return "".join(reasoning).strip(), "".join(answer).strip()
