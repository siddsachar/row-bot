"""A quick look at a page's inline scripts before the person sees it (B423): a bracket left open or closed by
the wrong one stops the whole script, and the page shows empty sections.

This isn't a JavaScript parser: it walks each script past strings, template literals, comments and regular
expressions, and reports the first bracket that doesn't pair, with its line and column in the page. The model
reads it as a warning and fixes the page; nothing is refused.
"""
from __future__ import annotations

import re

_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_DATA_TYPES = re.compile(r"\btype\s*=\s*[\"']?(?:application/(?:ld\+)?json|text/(?:template|html|plain)|"
                         r"importmap|speculationrules)", re.IGNORECASE)
_PAIRS = {")": "(", "]": "[", "}": "{"}
# After these, a "/" starts a regular expression rather than dividing.
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}
_REGEX_WORDS = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do",
                "else", "yield", "await"}


def _position(html: str, index: int) -> tuple[int, int]:
    line = html.count("\n", 0, index) + 1
    return line, index - (html.rfind("\n", 0, index) + 1) + 1


def _first_problem(code: str) -> tuple[int, str] | None:
    """(offset in *code*, words) for the first bracket that doesn't pair; None when they all do."""
    stack: list[tuple[str, int]] = []  # Opening brackets; "`" marks a template literal's ${…} return point.
    i, n = 0, len(code)
    last = ""  # The last significant character (or word), to tell a regular expression from a division.
    while i < n:
        c = code[i]
        if c in " \t\r\n":
            i += 1
            continue
        if code.startswith("//", i):
            end = code.find("\n", i)
            i = n if end < 0 else end
            continue
        if code.startswith("/*", i):
            end = code.find("*/", i + 2)
            if end < 0:
                return i, "a /* comment is never closed"
            i = end + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and code[j] != c:
                if code[j] == "\\":
                    j += 1
                elif code[j] == "\n":
                    return i, f"a {c}…{c} string runs past the end of its line"
                j += 1
            if j >= n:
                return i, f"a {c}…{c} string is never closed"
            i, last = j + 1, "a"
            continue
        if c == "`" or (c == "}" and stack and stack[-1][0] == "`"):
            if c == "}":
                stack.pop()
            start = i
            j = i + 1
            while j < n and code[j] != "`":
                if code[j] == "\\":
                    j += 1
                elif code.startswith("${", j):
                    break
                j += 1
            if j >= n:
                return start, "a `template` is never closed"
            if code[j] == "`":
                i, last = j + 1, "a"
            else:
                stack.append(("`", j))
                i, last = j + 2, "{"
            continue
        if c == "/" and (last in _REGEX_AFTER or last in _REGEX_WORDS):
            j, in_class = i + 1, False
            while j < n and (code[j] != "/" or in_class):
                if code[j] == "\\":
                    j += 1
                elif code[j] == "[":
                    in_class = True
                elif code[j] == "]":
                    in_class = False
                elif code[j] == "\n":
                    break
                j += 1
            i = j + 1
            while i < n and code[i].isalpha():
                i += 1
            last = "a"
            continue
        if c in "([{":
            stack.append((c, i))
            i, last = i + 1, c
            continue
        if c in ")]}":
            if not stack:
                return i, f"'{c}' closes nothing"
            if stack[-1][0] != _PAIRS[c]:
                # Where the fix goes: the bracket left open, not the one that ran into it.
                opened, at = stack[-1]
                return (at, f"a template's ${{…}} is never closed before a '{c}'") if opened == "`" else (
                    at, f"'{opened}' is never closed before a '{c}'")
            stack.pop()
            i, last = i + 1, c
            continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (code[j].isalnum() or code[j] in "_$"):
                j += 1
            word = code[i:j]
            i, last = j, word if word in _REGEX_WORDS else "a"
            continue
        i, last = i + 1, c
    if stack:
        opened, at = stack[-1]
        return at, ("a template's ${…} is never closed" if opened == "`" else f"'{opened}' is never closed")
    return None


def problems(html: str) -> list[str]:
    """Each inline script that won't run, in plain words with where."""
    found = []
    number = 0
    for match in _SCRIPT.finditer(html or ""):
        attributes, code = match.group(1), match.group(2)
        if re.search(r"\bsrc\s*=", attributes, re.IGNORECASE) or _DATA_TYPES.search(attributes):
            continue
        number += 1
        problem = _first_problem(code)
        if problem is not None:
            line, column = _position(html, match.start(2) + problem[0])
            found.append(f"script {number}, line {line}, column {column}: {problem[1]}")
    return found
