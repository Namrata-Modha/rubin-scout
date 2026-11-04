"""Read CLASS_INFO out of frontend/src/lib/cosmos.js for the knowledge base.

CLASS_INFO is a JavaScript object literal: some keys are bare identifiers
(``SNIa: {``), some are quoted (``"CV/Nova": {``), the fields inside are
bare identifiers, and there are ``//`` comments between entries. The old
regex approach only understood quoted keys, so ten classes silently produced
no chunk and the rest produced a chunk with only their name.

This is a small recursive-descent parser for exactly that subset of
JavaScript (objects, arrays, strings, numbers, true/false/null, comments,
trailing commas). Node was not used because the knowledge-base build runs in
a Python-only environment (python:3.11-slim); anything the parser does not
understand raises ClassInfoError, so a change to cosmos.js that it cannot read
fails the build instead of quietly dropping content.

No heavy imports on purpose: tests import this without langchain installed.
"""

import re

_MARKER = re.compile(r"export\s+const\s+CLASS_INFO\s*=\s*")
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "0": "\0",
            "\\": "\\", '"': '"', "'": "'", "/": "/"}

# Fields a visitor can read, in the order they appear in a chunk. emoji and
# color are presentation only and are left out.
_TEXT_FIELDS = (
    ("name", "Name"),
    ("short", "Summary"),
    ("tooltip", "Tooltip"),
    ("description", "Description"),
    ("danger", "Danger note"),
)


class ClassInfoError(RuntimeError):
    """CLASS_INFO could not be read, or what was read cannot be trusted."""


class _Parser:
    def __init__(self, text: str, pos: int):
        self.s = text
        self.i = pos

    def _fail(self, msg: str):
        line = self.s.count("\n", 0, self.i) + 1
        raise ClassInfoError(f"cosmos.js line {line}: {msg}")

    def _skip(self) -> None:
        s = self.s
        while self.i < len(s):
            if s[self.i].isspace():
                self.i += 1
            elif s.startswith("//", self.i):
                nl = s.find("\n", self.i)
                self.i = len(s) if nl == -1 else nl + 1
            elif s.startswith("/*", self.i):
                end = s.find("*/", self.i + 2)
                if end == -1:
                    self._fail("unterminated /* comment")
                self.i = end + 2
            else:
                return

    def _string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out = []
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == quote:
                self.i += 1
                return "".join(out)
            if ch == "\n":
                self._fail("newline inside a string")
            if ch == "\\":
                self.i += 1
                esc = self.s[self.i : self.i + 1]
                if esc == "u":
                    hex4 = self.s[self.i + 1 : self.i + 5]
                    try:
                        out.append(chr(int(hex4, 16)))
                    except ValueError:
                        self._fail(f"bad \\u escape {hex4!r}")
                    self.i += 5
                    continue
                if esc not in _ESCAPES:
                    self._fail(f"unsupported escape \\{esc}")
                out.append(_ESCAPES[esc])
                self.i += 1
                continue
            out.append(ch)
            self.i += 1
        self._fail("unterminated string")

    def value(self):
        self._skip()
        ch = self.s[self.i : self.i + 1]
        if ch in ('"', "'"):
            return self._string()
        if ch == "`":
            self._fail("template literals are not supported")
        if ch == "{":
            return self._object()
        if ch == "[":
            return self._array()
        m = _NUMBER.match(self.s, self.i)
        if m:
            self.i = m.end()
            text = m.group(0)
            return float(text) if re.search(r"[.eE]", text) else int(text)
        m = _IDENT.match(self.s, self.i)
        if m and m.group(0) in ("true", "false", "null"):
            self.i = m.end()
            return {"true": True, "false": False, "null": None}[m.group(0)]
        self._fail(f"unsupported value starting at {self.s[self.i : self.i + 20]!r}")

    def _key(self) -> str:
        self._skip()
        ch = self.s[self.i : self.i + 1]
        if ch in ('"', "'"):
            return self._string()
        m = _IDENT.match(self.s, self.i)
        if not m:
            self._fail(f"expected an object key, found {self.s[self.i : self.i + 20]!r}")
        self.i = m.end()
        return m.group(0)

    def _object(self) -> dict:
        self.i += 1  # {
        result: dict = {}
        while True:
            self._skip()
            if self.s[self.i : self.i + 1] == "}":
                self.i += 1
                return result
            key = self._key()
            self._skip()
            if self.s[self.i : self.i + 1] != ":":
                self._fail(f"expected ':' after key {key!r}")
            self.i += 1
            if key in result:
                self._fail(f"duplicate key {key!r}")
            result[key] = self.value()
            self._skip()
            sep = self.s[self.i : self.i + 1]
            if sep == ",":
                self.i += 1
            elif sep != "}":
                self._fail(f"expected ',' or '}}' after the value of {key!r}")

    def _array(self) -> list:
        self.i += 1  # [
        result: list = []
        while True:
            self._skip()
            if self.s[self.i : self.i + 1] == "]":
                self.i += 1
                return result
            result.append(self.value())
            self._skip()
            sep = self.s[self.i : self.i + 1]
            if sep == ",":
                self.i += 1
            elif sep != "]":
                self._fail("expected ',' or ']' in an array")


def _count_top_level_entries(text: str, start: int) -> int:
    """Count ``key: {`` entries at depth 1 of the object starting at ``start``.

    Deliberately a different mechanism from the parser (brace depth over a
    copy with strings and comments collapsed), so the two cross-check each
    other.
    """
    flat: list[str] = []
    depth = 0
    seen_open = False
    i = start
    while i < len(text):
        if text.startswith("//", i):
            nl = text.find("\n", i)
            i = len(text) if nl == -1 else nl
            continue
        if text.startswith("/*", i):
            i = text.find("*/", i + 2) + 2
            continue
        ch = text[i]
        if ch in ('"', "'"):
            i += 1
            while text[i] != ch:
                i += 2 if text[i] == "\\" else 1
            i += 1
            flat.append("S")  # a string (e.g. a quoted key) becomes one token
            continue
        flat.append(ch)
        i += 1
        if ch == "{":
            depth += 1
            seen_open = True
        elif ch == "}":
            depth -= 1
            if seen_open and depth == 0:
                break
    count, depth = 0, 0
    for m in re.finditer(r"[{}]|(?:[A-Za-z_$][\w$]*|S)\s*:\s*\{", "".join(flat)):
        tok = m.group(0)
        if tok == "}":
            depth -= 1
            continue
        if tok != "{" and depth == 1:
            count += 1
        depth += 1
    return count


def parse_class_info(source: str) -> dict[str, dict]:
    """The CLASS_INFO object from cosmos.js source text, as plain dicts.

    Raises ClassInfoError if it cannot be found or read, if any class has no
    description, or if the parser and an independent entry count disagree.
    """
    marker = _MARKER.search(source)
    if not marker:
        raise ClassInfoError("could not find 'export const CLASS_INFO =' in cosmos.js")
    start = marker.end()
    parser = _Parser(source, start)
    parser._skip()
    if source[parser.i : parser.i + 1] != "{":
        parser._fail("CLASS_INFO is not an object literal")
    parsed = parser.value()

    expected = _count_top_level_entries(source, start)
    if len(parsed) != expected:
        raise ClassInfoError(
            f"parsed {len(parsed)} CLASS_INFO classes but counted {expected} "
            "top-level entries in the file"
        )
    for key, entry in parsed.items():
        if not isinstance(entry, dict):
            raise ClassInfoError(f"CLASS_INFO[{key!r}] is not an object")
        description = entry.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ClassInfoError(f"CLASS_INFO[{key!r}] has no description")
    if not parsed:
        raise ClassInfoError("CLASS_INFO is empty")
    return parsed


def class_info_texts(class_info: dict[str, dict]) -> list[tuple[str, str]]:
    """One (class key, chunk text) per class: its name, summary, description and
    any other plain-language field. Each chunk stands on its own."""
    texts = []
    for key, entry in class_info.items():
        lines = [f"Transient class: {key}"]
        for field, label in _TEXT_FIELDS:
            value = entry.get(field)
            if isinstance(value, str) and value.strip():
                lines.append(f"{label}: {value.strip()}")
        texts.append((key, "\n".join(lines)))
    return texts
