"""Filesystem tool — sandboxed file operations within a user-configured workspace."""

from __future__ import annotations

import os
from pathlib import Path

from row_bot.data_paths import is_private_data_path
from row_bot.tools.base import BaseTool
from row_bot.tools import registry

# Operations the user can enable, grouped by risk level
_SAFE_OPS = ["read_file", "list_directory", "file_search"]
_WRITE_OPS = ["write_file", "copy_file", "export_to_pdf"]
_DESTRUCTIVE_OPS = ["move_file", "file_delete"]
ALL_OPERATIONS = _SAFE_OPS + _WRITE_OPS + _DESTRUCTIVE_OPS

# Default: every operation. Moving and deleting ask by the approval level like any change (5.3.1 A1).
DEFAULT_OPERATIONS = ALL_OPERATIONS
# The operations workspace_find runs: with no pattern it lists a folder, with one it searches beneath it.
_FIND_OPS = ("list_directory", "file_search")
# Said once, on the tool that saves a new file; the turn context names the workspace folder (B1).
_SCOPE_NOTE = (
    "(Paths are relative to the workspace folder. An absolute path elsewhere on this "
    "computer works too, after the person's approval.)"
)

class FileSystemTool(BaseTool):

    @property
    def name(self) -> str:
        return "filesystem"

    @property
    def display_name(self) -> str:
        return "📁 Filesystem"

    @property
    def description(self) -> str:
        return (
            "Read, write, search, copy, move, and delete files within a "
            "sandboxed workspace folder. Use this when the user asks to "
            "create files, read local files, organise folders, save notes, "
            "or manage files on disk."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    @property
    def config_schema(self) -> dict[str, dict]:
        return {
            "workspace_root": {
                "label": "Workspace folder",
                "type": "folder",
                "default": "",
            },
            "selected_operations": {
                "label": "Allowed operations",
                "type": "multicheck",
                "default": DEFAULT_OPERATIONS,
                "options": ALL_OPERATIONS,
            },
        }

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"workspace_move_file", "workspace_file_delete"}

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {"workspace_read_file"}

    # ── Build the toolkit tools ──────────────────────────────────────────────
    def _get_workspace_root(self) -> str:
        import os
        root = self.get_config("workspace_root", "")
        if not root:
            from row_bot.data_paths import default_workspace_root

            root = str(default_workspace_root())
            self.set_config("workspace_root", root)
        os.makedirs(root, exist_ok=True)
        return root

    def _get_selected_operations(self) -> list[str]:
        ops = self.get_config("selected_operations", DEFAULT_OPERATIONS)
        return [op for op in ops if op in ALL_OPERATIONS]

    def as_langchain_tools(self) -> list:
        """Return the selected FileManagementToolkit tools, sandboxed to
        the configured workspace root.  The default ``read_file`` tool is
        replaced with a custom version that can also read PDF files."""
        from langchain_community.agent_toolkits import FileManagementToolkit

        root = self._get_workspace_root()

        selected = self._get_selected_operations()
        if not selected:
            return []

        # Separate custom tools from LangChain FileManagementToolkit tools. Listing and searching are
        # one tool, workspace_find; each stays its own allowed operation.
        _CUSTOM_OPS = {"export_to_pdf", *_FIND_OPS}
        lc_selected = [op for op in selected if op not in _CUSTOM_OPS]

        tools = []
        if lc_selected:
            toolkit = FileManagementToolkit(
                root_dir=root,
                selected_tools=lc_selected,
            )
            tools = toolkit.get_tools()

        # Wrap each built-in tool to normalise paths, add sandbox
        # redirect for out-of-workspace paths, rename to workspace_*,
        # and enrich descriptions with scope info.
        tools = [_wrap_tool_with_path_fix(t, root) for t in tools]

        # Replace the default read_file with our PDF-aware version
        if "read_file" in selected:
            tools = [t for t in tools if t.name != "workspace_read_file"]
            tools.append(_make_pdf_aware_read_tool(root))

        finding = [op for op in _FIND_OPS if op in selected]
        if finding:
            tools.append(_make_find_tool(root, finding))

        # Add export_to_pdf sub-tool
        if "export_to_pdf" in selected:
            tools.append(_make_export_to_pdf_tool(root))

        return tools

    def execute(self, query: str) -> str:
        # Not used — as_langchain_tools() provides individual tools directly
        return "Use the individual file operations instead."


def _within(path: Path, root: str | Path) -> bool:
    """True when *path* is *root* or inside it; a sibling such as ``Row-Bot2`` beside ``Row-Bot`` is not."""
    import os

    resolved = os.path.normcase(str(Path(path).resolve()))
    base = os.path.normcase(str(Path(root).resolve()))
    return resolved == base or resolved.startswith(base.rstrip(os.sep) + os.sep)


def _normalise_path(file_path: str, root_dir: str) -> str:
    """Strip the workspace folder name from the front of *file_path* if the
    LLM redundantly included it, and convert absolute paths that fall inside
    the workspace to relative ones.

    Examples (root_dir = ``D:\\Row-BotWorkspace``):
    - ``Row-BotWorkspace/notes.txt``  →  ``notes.txt``
    - ``D:\\Row-BotWorkspace\\notes.txt``  →  ``notes.txt``
    - ``notes.txt``  →  ``notes.txt``  (unchanged)
    """
    import os
    from pathlib import Path

    fp = file_path.replace("\\", "/").strip().strip("/")
    root_name = Path(root_dir).name  # e.g. "Row-BotWorkspace"

    # Strip leading workspace folder name (case-insensitive)
    if fp.lower().startswith(root_name.lower() + "/"):
        fp = fp[len(root_name) + 1:]
    elif fp.lower() == root_name.lower():
        fp = "."

    # Handle absolute paths inside the workspace. The stripping above drops a POSIX path's leading "/", so an
    # absolute path resolves from what was given. The turn context names the workspace in its ``~`` form.
    try:
        given = Path(file_path.strip()).expanduser()
        resolved = (given if given.is_absolute() else Path(fp)).resolve()
        root_resolved = Path(root_dir).resolve()
        if _within(resolved, root_resolved):
            rel = os.path.relpath(resolved, root_resolved)
            fp = rel.replace("\\", "/")
    except (OSError, ValueError):
        pass

    return fp


def _is_outside_workspace(value: str, root_dir: str) -> bool:
    """Return True if *value* looks like an absolute path that falls outside
    the workspace *root_dir*.  Relative paths are assumed to resolve inside; ``~`` is the home folder."""
    import os
    from pathlib import Path

    v = os.path.expanduser(value.strip()).replace("\\", "/")
    # Only flag absolute paths (Windows drive letter or Unix /)
    if not (v.startswith("/") or (len(v) >= 2 and v[1] == ":")):
        return False
    try:
        resolved = Path(v).resolve()
        root_resolved = Path(root_dir).resolve()
        return not _within(resolved, root_resolved)
    except (OSError, ValueError):
        return False


PRIVATE_DATA_PATH_REFUSAL = (
    "Error: this path is inside Row-Bot's private data folder (conversations, settings, keys), which agents "
    "can't read or change."
)


# File operations that only read: outside the workspace they still ask, since they reach the person's
# other files, but the card says reading.
_READING_OPERATIONS = {"read_file", "list_directory", "file_search"}


def outside_workspace_refusal(operation: str, paths: list[Path], *, tool: str = "") -> tuple[str | None, str]:
    """Ask by the approval level before a file operation reaches outside the workspace (5.3.1 B1).

    Returns the refusal (None when it may run) and the approval line for the result. Row-Bot's data
    folder stays refused at every level.
    """
    from row_bot.tools.approval_gate import approval_check

    if any(is_private_data_path(path) for path in paths):
        return PRIVATE_DATA_PATH_REFUSAL, ""
    reading = operation in _READING_OPERATIONS
    names = [str(path) for path in paths]
    payload = {
        "tool": tool or f"workspace_{operation}",
        "label": "Read outside the workspace?" if reading else "Change files outside the workspace?",
        # The card asks by the tool ("Write a file?"); this line says where.
        "description": "Outside the workspace: " + ", ".join(names),
        "args": {"paths": names},
    }
    return approval_check(
        payload,
        risks=("reaches files outside the workspace",),
        blocked_message=f"BLOCKED: {', '.join(names)} is outside the workspace, and this chat is on Block.",
        cancelled_message="Cancelled: nothing outside the workspace was read or changed.",
    )


def _absolute(value: str, root_dir: str) -> Path:
    given = Path(value.strip()).expanduser()
    return (given if given.is_absolute() else Path(root_dir) / _normalise_path(value, root_dir)).resolve()


# The operations that leave a file somewhere, and the argument naming where.
_SAVED_TO = {"write_file": "file_path", "copy_file": "destination_path", "move_file": "destination_path"}


def _make_folder_for(relative: str, root_dir: str) -> None:
    """Make the workspace folder a file is saved into before the file tool checks its path (S2): on Windows a
    parallel write creating the same new folder made that check read the path as outside the workspace. Only a
    folder that lies inside the workspace by its name alone; anything else is left to the tool to refuse."""
    import os

    base = os.path.normcase(os.path.normpath(root_dir))
    folder = os.path.normcase(os.path.normpath(os.path.join(root_dir, os.path.dirname(relative))))
    if folder != base and folder.startswith(base.rstrip(os.sep) + os.sep):
        try:
            os.makedirs(os.path.join(root_dir, os.path.dirname(relative)), exist_ok=True)
        except OSError:
            pass  # The tool's own write says what went wrong.


def _where_saved(operation: str, result, kwargs: dict, target: Path, root_dir: str):
    """A write, copy or move result that says where the file now is (A2), so the reply can name it.

    The place is shown as the turn context names folders: ``~/…`` under the home folder, as it is elsewhere.
    A refusal or error is returned unchanged.
    """
    from row_bot.data_paths import display_path

    if not isinstance(result, str) or result.startswith("Error") or not target.is_file():
        return result
    inside = _within(target, root_dir)
    shown = os.path.relpath(target, Path(root_dir).resolve()).replace("\\", "/") if inside else target.name
    if operation == "write_file":
        lead = f"Added to {shown}" if kwargs.get("append") else f"Saved {shown}"
    else:
        source = str(kwargs.get("source_path") or "").strip()
        lead = f"{'Copied' if operation == 'copy_file' else 'Moved'} {source} to {shown}"
    if inside:
        return f"{lead} in the workspace ({display_path(target)})."
    return f"{lead} outside the workspace: {display_path(target)}."


def _wrap_tool_with_path_fix(tool, root_dir: str, *, card_tool: str = ""):
    """Return a copy of *tool* that:
    1. Rejects absolute paths outside the workspace with a redirect hint.
    2. Normalises paths the LLM provides (strips redundant workspace prefix).
    3. Renames the tool to ``workspace_<name>`` so the LLM knows the scope.
    4. Appends workspace-scope notice to the description.
    """
    from langchain_core.tools import StructuredTool

    # Resolve the callable — class-based tools use _run, function tools use func
    original_func = getattr(tool, "func", None) or getattr(tool, "_run", None)
    if original_func is None:
        return tool  # can't wrap tools without a callable

    import inspect
    sig = inspect.signature(original_func)
    # Identify which parameters look like paths
    _path_params = [
        p.name for p in sig.parameters.values()
        if "path" in p.name.lower() or "dir" in p.name.lower()
           or p.name in ("source", "destination")
    ]
    if not _path_params:
        # list_directory uses no named path param — its first arg is dir_path
        if tool.name == "list_directory":
            _path_params = list(sig.parameters.keys())[:1]

    operation = tool.name.removeprefix("workspace_")

    def _outside(**kwargs):
        """A path elsewhere on this computer: ask by the level, then run the same tool rooted there."""
        import os

        keys = [key for key in _path_params if isinstance(kwargs.get(key), str)]
        paths = {key: _absolute(kwargs[key], root_dir) for key in keys}
        try:
            # The folder holding every path; a list_directory path is the folder itself.
            folders = [path if operation == "list_directory" else path.parent for path in paths.values()]
            common = Path(os.path.commonpath([str(folder) for folder in folders]))
        except ValueError:
            return "Error: these paths are on different drives; copy them with run_command instead."
        # The decision is recorded on the result by the agent's tool wrapper.
        refusal, _line = outside_workspace_refusal(operation, list(paths.values()), tool=card_tool)
        if refusal:
            return refusal
        rooted = type(tool)(root_dir=str(common))
        given = dict(kwargs)
        for key, path in paths.items():
            kwargs[key] = os.path.relpath(path, common).replace("\\", "/")
        result = rooted._run(**kwargs)
        if operation in _SAVED_TO and _SAVED_TO[operation] in paths:
            return _where_saved(operation, result, given, paths[_SAVED_TO[operation]], root_dir)
        return result

    def _wrapped(**kwargs):
        # A path outside the workspace asks first (5.3.1 B1); inside it, paths are normalised.
        if any(isinstance(kwargs.get(key), str) and _is_outside_workspace(kwargs[key], root_dir)
               for key in _path_params):
            return _outside(**kwargs)
        for key in _path_params:
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = _normalise_path(kwargs[key], root_dir)
                if is_private_data_path(Path(root_dir) / kwargs[key]):
                    return PRIVATE_DATA_PATH_REFUSAL
        saving = kwargs.get(_SAVED_TO.get(operation, ""))
        if isinstance(saving, str):
            _make_folder_for(saving, root_dir)
        result = original_func(**kwargs)
        target = kwargs.get(_SAVED_TO.get(operation, ""))
        if isinstance(target, str):
            return _where_saved(operation, result, kwargs, (Path(root_dir) / target).resolve(), root_dir)
        return result

    # Rename to workspace_*; the scope note goes once, on the tool that saves a new file.
    new_name = f"workspace_{tool.name}"
    new_desc = f"{tool.description} {_SCOPE_NOTE}" if tool.name == "write_file" else tool.description

    # Preserve the original input schema so StructuredTool passes kwargs
    # correctly.  Class-based LangChain tools expose their schema via
    # get_input_schema(); without this, **kwargs eats everything.
    original_schema = None
    if hasattr(tool, "get_input_schema"):
        original_schema = tool.get_input_schema()

    return StructuredTool.from_function(
        func=_wrapped,
        name=new_name,
        description=new_desc,
        args_schema=original_schema,
    )


def _make_pdf_aware_read_tool(root_dir: str):
    """Create a ``read_file`` StructuredTool that can read both text and PDF
    files.  Paths are resolved relative to *root_dir* and validated to stay
    within the sandbox.  Text is read a page of lines at a time, with fixed
    budgets (not scaled to the model's context), so every part of a long file
    can be reached with ``offset``."""
    from pathlib import Path
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel as _BM, Field as _F

    from row_bot.tool_outputs import PAGE_CHARS, PAGE_LINES, page_lines

    class _ReadFileInput(_BM):
        file_path: str = _F(description="Relative to the workspace, or a full path. Excel: add '::Sheet'.")
        offset: int = _F(
            default=0,
            description="First line (1-based); 0: the top, or a CSV or JSON file's summary.",
        )
        limit: int = _F(default=PAGE_LINES, description="Most lines.")

    def _page(text: str, offset: int, limit: int) -> str:
        page, footer = page_lines(
            text, offset=max(1, offset), limit=limit, max_chars=PAGE_CHARS, tool_name="workspace_read_file"
        )
        return f"{page}\n{footer}" if footer else page

    def read_file(file_path: str, offset: int = 0, limit: int = PAGE_LINES) -> str:
        """Read the contents of a file. For PDF files, extracts all text
        from every page. For CSV/Excel/JSON files, returns schema, stats,
        and a preview. The file_path is relative to the workspace root.
        For Excel files, append '::SheetName' to read a specific sheet
        (e.g. 'data.xlsx::Sheet2')."""
        # Parse optional sheet specifier for Excel files
        sheet_name = ""
        if "::" in file_path:
            file_path, sheet_name = file_path.rsplit("::", 1)

        if _is_outside_workspace(file_path, root_dir):
            # A file elsewhere on this computer: read after asking by the level (5.3.1 B1).
            resolved = Path(file_path.strip()).expanduser().resolve()
            refusal, _line = outside_workspace_refusal("read_file", [resolved])
            if refusal:
                return refusal
        else:
            file_path = _normalise_path(file_path, root_dir)
            resolved = (Path(root_dir) / file_path).resolve()
            # Sandbox check — a relative path must stay within root
            if not _within(resolved, root_dir):
                return (
                    f"Error: path '{file_path}' climbs out of the workspace folder ({root_dir}); "
                    f"give the file's full path instead."
                )
            if is_private_data_path(resolved):
                return PRIVATE_DATA_PATH_REFUSAL

        if not resolved.exists() and "/" not in file_path.replace("\\", "/"):
            received = Path(root_dir) / "Received Files" / file_path
            if received.resolve().exists():
                resolved = received.resolve()
                file_path = f"Received Files/{file_path}"

        if not resolved.exists():
            return f"Error: file not found: {file_path}"

        # ── Image files — what they are, never their bytes as text ────
        from row_bot.file_context import IMAGE_EXTENSIONS
        if resolved.suffix.lower() in IMAGE_EXTENSIONS:
            try:
                size_kb = resolved.stat().st_size / 1024
                if size_kb >= 1024:
                    size_str = f"{size_kb / 1024:.1f} MB"
                else:
                    size_str = f"{size_kb:.0f} KB"
                return (
                    f"Image: {file_path} ({size_str}). "
                    f"To look at it, use analyze_image with source='file' and file_path='{file_path}'; "
                    f"to show it to the person, use present."
                )
            except Exception as exc:
                return f"Error reading image '{file_path}': {exc}"

        # ── Structured data files (CSV, Excel, JSON) ────────────────────
        from row_bot.data_reader import can_read_data_files, is_data_file, read_data_file
        if is_data_file(resolved.name) and can_read_data_files() and (
                offset <= 0 or resolved.suffix.lower() in {".xlsx", ".xls"}):
            return read_data_file(resolved, sheet=sheet_name, max_chars=PAGE_CHARS)

        if resolved.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(resolved))
                pages = []
                for i, page in enumerate(reader.pages, 1):
                    text = page.extract_text() or ""
                    if text.strip():
                        pages.append(f"--- Page {i} ---\n{text}")
                if not pages:
                    return f"PDF file '{file_path}' contains no extractable text."
                return _page("\n\n".join(pages), offset, limit)
            except Exception as exc:
                return f"Error reading PDF '{file_path}': {exc}"
        else:
            try:
                text = resolved.read_text(encoding="utf-8", errors="replace")
                return _page(text, offset, limit)
            except Exception as exc:
                return f"Error reading '{file_path}': {exc}"

    return StructuredTool.from_function(
        func=read_file,
        name="workspace_read_file",
        description=(
            "Read a file in pages of lines: text, PDF, CSV, Excel or JSON (an image gives its size only). A long "
            "file ends with the offset to read on from."
        ),
        args_schema=_ReadFileInput,
    )


def _make_find_tool(root_dir: str, operations: list[str]):
    """``workspace_find``: with no pattern it lists a folder, with one it finds files beneath it (B1).

    Each half is the LangChain tool it was before, wrapped the same way, so a path outside the
    workspace asks first and Row-Bot's data folder is refused. The person's Allowed operations still
    say whether listing, searching or both may run.
    """
    from langchain_community.tools.file_management import FileSearchTool, ListDirectoryTool
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel as _BM, Field as _F

    listing = (_wrap_tool_with_path_fix(ListDirectoryTool(root_dir=root_dir), root_dir, card_tool="workspace_find")
               if "list_directory" in operations else None)
    searching = (_wrap_tool_with_path_fix(FileSearchTool(root_dir=root_dir), root_dir, card_tool="workspace_find")
                 if "file_search" in operations else None)

    class _FindInput(_BM):
        path: str = _F(default=".", description="A folder; relative to the workspace, or a full path.")
        pattern: str = _F(default="", description="e.g. '*.pdf': find files beneath path. Empty lists path.")

    def find(path: str = ".", pattern: str = "") -> str:
        if str(pattern or "").strip():
            if searching is None:
                return "Error: searching files is switched off in this workspace's Allowed operations."
            return searching.func(dir_path=path, pattern=pattern)
        if listing is None:
            return "Error: listing folders is switched off in this workspace's Allowed operations."
        return listing.func(dir_path=path)

    return StructuredTool.from_function(
        func=find,
        name="workspace_find",
        description="List a folder, or find files beneath it by a name pattern.",
        args_schema=_FindInput,
    )


# ── Export to PDF ────────────────────────────────────────────────────────────

def _make_export_to_pdf_tool(root_dir: str):
    """Return a tool that exports text/markdown content to a PDF file."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel as _BM, Field as _F

    class _ExportPdfInput(_BM):
        content: str = _F(
            description=(
                "The text or markdown content to render as a PDF. "
                "Plain text and basic markdown are supported."
            ),
        )
        filename: str = _F(
            description=(
                "Output filename (e.g. 'report.pdf'). Saved in the workspace folder. "
                "The .pdf extension is added automatically if missing."
            ),
        )

    def export_to_pdf(content: str, filename: str) -> str:
        """Render markdown/text content as a PDF in the workspace."""
        import concurrent.futures
        import re as _re
        from pathlib import Path as _Path

        fname = filename.strip()
        if not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        out_path = _Path(root_dir) / fname
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Try Playwright (headless Chromium) for full Unicode/styling
        try:
            from playwright.sync_api import sync_playwright
            import markdown2

            html_body = markdown2.markdown(
                content,
                extras=["fenced-code-blocks", "tables", "code-friendly"],
            )
            css = (
                "@page { size: A4; margin: 20mm 18mm; }"
                "body { font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;"
                "  font-size: 11pt; line-height: 1.5; color: #222; margin: 0; padding: 0; }"
                "pre { background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px;"
                "  padding: 8px 10px; font-size: 9pt; white-space: pre-wrap; word-break: break-all;"
                "  font-family: Consolas, 'Courier New', monospace; }"
                "code { background: #f0f0f0; padding: 1px 4px; border-radius: 3px;"
                "  font-size: 9.5pt; font-family: Consolas, 'Courier New', monospace; }"
                "pre code { background: none; padding: 0; }"
                "table { border-collapse: collapse; margin: 8px 0; font-size: 10pt; }"
                "th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; }"
                "th { background: #f0f0f0; font-weight: 600; }"
                "blockquote { border-left: 3px solid #ccc; margin: 8px 0; padding: 4px 12px; color: #555; }"
                "img { max-width: 100%; height: auto; }"
            )
            html = (
                f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<style>{css}</style></head><body>{html_body}</body></html>"
            )

            def _render_in_worker() -> None:
                pw = sync_playwright().start()
                try:
                    browser = pw.chromium.launch(headless=True)
                    page = browser.new_page()
                    page.set_content(html, wait_until="networkidle")
                    page.pdf(
                        path=str(out_path),
                        format="A4",
                        margin={
                            "top": "20mm",
                            "right": "18mm",
                            "bottom": "20mm",
                            "left": "18mm",
                        },
                        print_background=True,
                    )
                    page.close()
                    browser.close()
                finally:
                    pw.stop()

            # Playwright sync API must run outside the app's asyncio loop.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_render_in_worker).result()
            return f"PDF saved to: {out_path}"

        except Exception:
            pass  # fall through to fpdf2

        # Fallback: fpdf2 (basic text, no Unicode emoji support)
        try:
            from fpdf import FPDF
        except ImportError:
            return "Error: fpdf2 is not installed. Run: pip install fpdf2"

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.add_page()

        def _safe(text: str) -> str:
            return text.encode("latin-1", errors="replace").decode("latin-1")

        # Strip markdown syntax for plain-text fallback
        text = content
        text = _re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        text = _re.sub(r'```[\s\S]*?```', '[code block]', text)
        text = _re.sub(r'#{1,6}\s+', '', text)

        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _safe(text))

        pdf.output(str(out_path))
        return f"PDF saved to: {out_path}"

    return StructuredTool.from_function(
        func=export_to_pdf,
        name="export_to_pdf",
        description=(
            "Export text or markdown content to a PDF file in the workspace. "
            "Returns the absolute file path — useful for sending the "
            "PDF via Telegram or email. "
            "(WORKSPACE ONLY — saves to the configured workspace folder.)"
        ),
        args_schema=_ExportPdfInput,
    )


registry.register(FileSystemTool())
