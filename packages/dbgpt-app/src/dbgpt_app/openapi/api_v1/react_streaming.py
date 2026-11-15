"""Helpers for streaming the user-facing result from ReAct responses."""

import re


class ReActFinalResultStreamParser:
    """Incrementally decode only ``terminate``'s JSON ``result`` string.

    ReAct model responses contain internal Thought/Action text before the final
    answer. This parser waits until it has seen a terminal Action and its
    Action Input, then streams only the value of the top-level ``result`` key.
    """

    _action_line = re.compile(
        r"(?im)^[ \t]*(?:[-*]\s*)?(?:\*\*)?Action[:：](?:\*\*)?"
        r"[ \t]*(?P<action>[^\r\n]*?)[ \t]*(?:\*\*)?[ \t]*\r?\n"
    )
    _action_input_line = re.compile(
        r"(?im)^[ \t]*(?:[-*]\s*)?(?:\*\*)?Action Input[:：](?:\*\*)?[ \t]*"
    )
    _action_input_hint = re.compile(r"(?i)Action\s+Input\s*[:：]")
    _result_key = re.compile(r'"result"')
    _result_string = re.compile(r'"result"\s*:\s*"')
    _header_tail_limit = 256
    _simple_escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }

    def __init__(self) -> None:
        self._header_parts: list[str] | None = []
        self._header_tail = ""
        self._header_length = 0
        self._action_input_end: int | None = None
        self._started = False
        self._closed = False
        self._escaped = False
        self._unicode_digits: str | None = None
        self._pending_high_surrogate: int | None = None

    def feed(self, delta: str) -> str:
        """Return newly decoded final-answer text from a model output delta."""
        if not delta or self._closed:
            return ""

        if not self._started:
            assert self._header_parts is not None
            scan_text = self._header_tail + delta
            self._header_parts.append(delta)
            self._header_length += len(delta)
            self._header_tail = scan_text[-self._header_tail_limit :]

            if self._action_input_end is None:
                if not self._action_input_hint.search(scan_text):
                    return ""

                raw_header = "".join(self._header_parts)
                action_input_state, action_input_end = self._locate_action_input(
                    raw_header
                )
                if action_input_state == "non_terminal":
                    self._close_header()
                    self._closed = True
                    return ""
                if action_input_end is None:
                    return ""

                self._action_input_end = action_input_end
                result_match = self._result_string.search(
                    raw_header, self._action_input_end
                )
                if result_match is not None:
                    return self._start_result_stream(raw_header, result_match.end())
                return ""

            tail_start = self._header_length - len(self._header_tail)
            tail_after_input = self._header_tail[
                max(0, self._action_input_end - tail_start) :
            ]
            if not self._result_key.search(tail_after_input):
                return ""

            raw_header = "".join(self._header_parts)
            result_match = self._result_string.search(
                raw_header, self._action_input_end
            )
            if result_match is None:
                return ""
            return self._start_result_stream(raw_header, result_match.end())

        return self._decode_result_text(delta)

    def _start_result_stream(self, raw_header: str, result_start: int) -> str:
        self._started = True
        initial_content = raw_header[result_start:]
        self._close_header()
        return self._decode_result_text(initial_content)

    def _close_header(self) -> None:
        self._header_parts = None
        self._header_tail = ""

    def _decode_result_text(self, text: str) -> str:
        output: list[str] = []
        for char in text:
            if self._closed:
                break
            if self._unicode_digits is not None:
                if char in "0123456789abcdefABCDEF":
                    self._unicode_digits += char
                    if len(self._unicode_digits) == 4:
                        self._emit_code_unit(int(self._unicode_digits, 16), output)
                        self._unicode_digits = None
                else:
                    output.append("\\u" + self._unicode_digits + char)
                    self._unicode_digits = None
                continue

            if self._escaped:
                self._escaped = False
                if char == "u":
                    self._unicode_digits = ""
                else:
                    decoded = self._simple_escapes.get(char)
                    if decoded is None:
                        self._flush_high_surrogate(output)
                        output.extend(("\\", char))
                    else:
                        self._emit_decoded_char(decoded, output)
                continue

            if char == "\\":
                self._escaped = True
            elif char == '"':
                self._flush_high_surrogate(output)
                self._closed = True
            else:
                self._flush_high_surrogate(output)
                output.append(char)

        return "".join(output)

    def _locate_action_input(self, raw: str) -> tuple[str | None, int | None]:
        for input_match in self._action_input_line.finditer(raw):
            action_matches = list(
                self._action_line.finditer(raw, 0, input_match.start())
            )
            if not action_matches:
                continue
            action_name = action_matches[-1].group("action").strip().strip("`'\"*")
            if action_name.lower() != "terminate":
                return "non_terminal", None
            return "terminal", input_match.end()
        return None, None

    def _emit_code_unit(self, code_unit: int, output: list[str]) -> None:
        pending = self._pending_high_surrogate
        if pending is not None:
            if 0xDC00 <= code_unit <= 0xDFFF:
                code_point = (
                    0x10000 + ((pending - 0xD800) << 10) + (code_unit - 0xDC00)
                )
                output.append(chr(code_point))
                self._pending_high_surrogate = None
                return
            self._flush_high_surrogate(output)

        if 0xD800 <= code_unit <= 0xDBFF:
            self._pending_high_surrogate = code_unit
        elif 0xDC00 <= code_unit <= 0xDFFF:
            output.append("\ufffd")
        else:
            self._emit_decoded_char(chr(code_unit), output)

    def _emit_decoded_char(self, char: str, output: list[str]) -> None:
        self._flush_high_surrogate(output)
        output.append(char)

    def _flush_high_surrogate(self, output: list[str]) -> None:
        if self._pending_high_surrogate is not None:
            output.append("\ufffd")
            self._pending_high_surrogate = None
