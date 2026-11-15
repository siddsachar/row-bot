"""Files a reply says it saved, checked on disk before the turn ends (A2 WP4).

A model can end a turn with "Saved the summary to report.md" when no tool ever wrote it. This module finds
the files a reply *claims as made* and returns those that aren't there, so the conversation can take one
more turn to make them or correct the reply.

It is deliberately conservative: a wrong hit costs the person a turn, a miss costs nothing new. A claim is a
past-tense verb right before the name ("saved it to `report.md`", "created notes.md") or a statement right
after it ("`report.md` is ready"). Hedged, negated, future or question sentences, code blocks, URLs and
names outside the given folders are ignored, and a name found anywhere under a folder counts as there.

Pure: it reads the disk under the given folders only and imports nothing from the app.
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath

# The kinds of file a reply names as made. Anything else ("Node.js", "v5.3") is not a file claim.
EXTENSIONS = frozenset({
    "md", "txt", "pdf", "docx", "xlsx", "csv", "json", "html", "py", "js", "ts", "png", "jpg", "jpeg", "svg",
    "mp4", "pptx", "zip"})
_EXT = "|".join(sorted(EXTENSIONS))
_MEDIA = frozenset({".png", ".jpg", ".jpeg", ".svg", ".mp4"})
MAX_NAMES = 5

_CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.DOTALL)
# A link or URL with a scheme of two letters or more (so a drive such as D:\ stays), or www.….
_URL = re.compile(r"\b[a-z][a-z0-9+.-]+:(?://|/)[^\s)\]>]*|\bwww\.[^\s)\]>]+", re.IGNORECASE)
_TICKED = re.compile(rf"`([^`\n]{{1,240}}?\.(?:{_EXT}))`", re.IGNORECASE)
_BARE = re.compile(
    rf"(?<![\w/\\.~:-])((?:~|[A-Za-z]:)?[\\/]?(?:[\w.-]+[\\/])*[\w-][\w.-]*\.(?:{_EXT}))(?![\w/\\-])",
    re.IGNORECASE)
_FRAMEWORK = re.compile(r"[A-Z][A-Za-z0-9]*\.js", re.ASCII)  # Node.js, Vue.js: a name, not a file.

_VERBS = (r"saved|wrote|written|created|exported|generated|made|stored|put|placed|rendered|produced|"
          r"downloaded|updated|added|copied|moved|converted")
_OPEN = r"[\s\"'`*\[(:]*"
_FILLER = r"(?:\s+(?!(?:from|of|using|with|by|on|for|about|based)\b)[\w'’-]+){0,5}?"
# "saved it to report.md", "created notes.md", "you'll find it in report.md", "it's at report.md".
_CLAIM_BEFORE = re.compile(
    rf"(?:\b(?:{_VERBS})\b{_FILLER}\s+(?:to|as|in|into|at|under|called|named)"
    rf"|\b(?:{_VERBS})"
    rf"|\byou(?:'|’)ll\s+find\s+(?:it|them|the\s+[\w-]+)\s+(?:at|in|as)"
    rf"|\b(?:it(?:'|’)s|they(?:'|’)re|(?:it|they|file|files)\s+(?:is|are))\s+(?:now\s+)?(?:at|in))"
    rf"{_OPEN}$", re.IGNORECASE)
# "report.md is ready", "notes.md has been saved".
_CLAIM_AFTER = re.compile(
    r"^[\"'`*)\]]*\s+(?:is|are|was|were|has\s+been|have\s+been)\s+(?:now\s+)?"
    r"(?:saved|written|created|exported|generated|ready|updated|stored|in\s+(?:your|the)\s+(?:workspace|folder))\b",
    re.IGNORECASE)
# Words that make a sentence not a statement of something done.
_HEDGE = re.compile(
    r"\b(?:not|never|no|cannot|can|could|would|will|shall|should|may|might|must|if|unless|once|when|whether|"
    r"want|wants|going|about|let|plan|planning|try|trying|tried|attempt|attempted|failed|unable|instead)\b"
    r"|n(?:'|’)t\b|(?:'|’)(?:ll|d)\b", re.IGNORECASE)
# A clause ends at a sentence's end or a line break (a dot inside a file name doesn't end it).
_CLAUSE_END = re.compile(r"[.!?;](?=\s|$)|\n")
# Where a sub-clause starts: "I couldn't read a.csv, so I created b.md" judges only "I created b.md".
_SUBCLAUSE = re.compile(r",|;|—|–|\b(?:and|but|so|then|while)\b", re.IGNORECASE)
# The person's other places: a bare name said to be there can't be checked under the given folders.
_ELSEWHERE = re.compile(r"\b(?:desktop|downloads|onedrive|dropbox|icloud|drive|server|repo|repository|bucket)\b",
                        re.IGNORECASE)

# A list item: its marker is all that comes before the name on its line ("- ", "2. ", "* **").
_BULLET = re.compile(r"\s*(?:[-*•+]|\d{1,3}[.)])\s+[*_`\"']*")
_BULLET_LINE = re.compile(r"(?:[-*•+]|\d{1,3}[.)])\s")
_WINDOW = 200  # Of the text before a name: where the words claiming it are.

# A folder search for a name said without its folder stays small.
_SKIP_DIRS = frozenset({"node_modules", "__pycache__", "venv", "dist", "build", "target", "site-packages"})
_MAX_DEPTH = 6
_MAX_ENTRIES = 20_000


def could_claim(reply: str) -> bool:
    """A cheap first look: the reply names something with a file extension at all."""
    return bool(_BARE.search(str(reply or "")) or _TICKED.search(str(reply or "")))


def claimed_missing(reply: str, roots: list[Path], *, known_names: frozenset[str] | set[str] = frozenset()
                    ) -> list[str]:
    """The files *reply* says it saved that exist under none of *roots*, as the reply names them.

    Relative names resolve against each root (the workspace, a bound code folder); a name without its folder
    is also looked for anywhere under them. Absolute and ``~`` paths are checked as given. *known_names* are
    files kept elsewhere that count as there (the chat's own files). At most ``MAX_NAMES`` are returned.
    """
    text = _URL.sub(" ", _CODE_BLOCK.sub(" ", str(reply or "")))
    known = {name.casefold() for name in known_names}
    found: list[str] = []
    seen: set[str] = set()
    for token, start, end in _mentions(text):
        if token.casefold() in seen or not _claimed(text, start, end, token):
            continue
        seen.add(token.casefold())
        if _present(token, roots, known) is False:
            found.append(token)
            if len(found) >= MAX_NAMES:
                break
    return found


def _mentions(text: str) -> list[tuple[str, int, int]]:
    """Every file-like name in *text* with its span; a backticked one counts once, as written in the ticks."""
    mentions = [(match.group(1).strip(), match.start(), match.end()) for match in _TICKED.finditer(text)]
    ticked = [(start, end) for _token, start, end in mentions]
    for match in _BARE.finditer(text):
        start, end = match.span(1)
        if any(start < t_end and end > t_start for t_start, t_end in ticked):
            continue
        token = match.group(1).strip()
        if _FRAMEWORK.fullmatch(token):
            continue
        mentions.append((token, start, end))
    return sorted(mentions, key=lambda mention: mention[1])


def _claimed(text: str, start: int, end: int, token: str) -> bool:
    """The sentence around the name says it was made, plainly: not hedged, negated, future or asked."""
    line_start = text.rfind("\n", 0, start) + 1
    if _BULLET.fullmatch(text[line_start:start]):
        claimed = _listed_under_a_claim(text, line_start)
        clause_start, clause_end = line_start, _line_end(text, end)
    else:
        boundaries = [match.end() for match in _CLAUSE_END.finditer(text, max(0, start - _WINDOW), start)]
        clause_start = boundaries[-1] if boundaries else max(0, start - _WINDOW)
        after_end = _CLAUSE_END.search(text, end)
        clause_end = after_end.start() if after_end else len(text)
        if after_end and text[after_end.start()] == "?":
            return False
        claimed = _plain_claim(text[clause_start:start], text[end:clause_end])
    if not claimed:
        return False
    # A bare name said to be on the Desktop or a drive can't be checked under the given folders.
    clause = text[clause_start:start] + " " + text[end:clause_end]
    return not (not re.search(r"[\\/]", token) and _ELSEWHERE.search(clause))


def _plain_claim(before: str, after: str) -> bool:
    claim = _CLAIM_BEFORE.search(before)
    if claim:
        judged = before
    elif _CLAIM_AFTER.match(after):
        judged = before + " " + after
    else:
        return False
    # Only the sub-clause holding the claim is judged: "I couldn't read a.csv, so I created b.md" is a claim.
    anchor = claim.start() if claim else len(before)
    cuts = [match.end() for match in _SUBCLAUSE.finditer(before, 0, anchor)]
    judged = judged[cuts[-1]:] if cuts else judged
    return not _HEDGE.search(judged)


def _line_end(text: str, index: int) -> int:
    newline = text.find("\n", index)
    return len(text) if newline < 0 else newline


def _listed_under_a_claim(text: str, line_start: int) -> bool:
    """A list item under a heading line that says the files were made ("Created these files:")."""
    position = line_start - 1
    while position > 0:
        heading_start = text.rfind("\n", 0, position) + 1
        heading = text[heading_start:position].strip()
        if heading and not _BULLET_LINE.match(heading):
            return bool(heading.endswith(":") and re.search(rf"\b(?:{_VERBS})\b", heading, re.IGNORECASE)
                        and not _HEDGE.search(heading))
        position = heading_start - 1
    return False


def _present(token: str, roots: list[Path], known: set[str]) -> bool | None:
    """True when the file is there, False when it isn't, None when it can't be told (ignored)."""
    cleaned = token.strip().strip("\"'*()[]<>").rstrip(".,;:!?")
    if not cleaned:
        return None
    if os.name != "nt" and PureWindowsPath(cleaned).drive:
        return None  # A Windows path named on another system.
    path = Path(cleaned).expanduser()
    if path.is_absolute():
        return path.exists()
    if path.name.casefold() in known or f"generated-{path.name}".casefold() in known:
        return True
    if path.suffix.lower() in _MEDIA and any(name.endswith(path.suffix.lower()) for name in known):
        return True  # A made picture or video is kept under its own name, which the reply may not repeat.
    inside_any = False
    for root in roots:
        root = Path(root)
        candidate = (root / path).resolve()
        if not _within(candidate, root):
            continue
        inside_any = True
        if candidate.exists():
            return True
    if not inside_any:
        return None  # It climbs out of every folder ("../x.md"): not one this can check.
    return _found_below(path, roots)


def _within(path: Path, root: Path) -> bool:
    resolved = os.path.normcase(str(path))
    base = os.path.normcase(str(root.resolve()))
    return resolved == base or resolved.startswith(base.rstrip(os.sep) + os.sep)


def _found_below(path: Path, roots: list[Path]) -> bool | None:
    """The file is somewhere under a root with this name (and these last folders), e.g. reports/q3.md for q3.md.

    None when the search stopped at its size limit before finding it: too big a folder to tell.
    """
    wanted = [part.casefold() for part in path.parts]
    seen = 0
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        depth0 = len(base.parts)
        for folder, dirs, files in os.walk(base):
            here = Path(folder)
            dirs[:] = [name for name in dirs if not name.startswith(".") and name not in _SKIP_DIRS
                       and len(here.parts) - depth0 < _MAX_DEPTH]
            seen += len(dirs) + len(files)
            for name in files:
                if name.casefold() != wanted[-1]:
                    continue
                parts = [part.casefold() for part in (here / name).parts]
                if parts[-len(wanted):] == wanted:
                    return True
            if seen > _MAX_ENTRIES:
                return None
    return False
