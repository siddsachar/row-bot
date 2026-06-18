"""Generate llms.txt and llms-full.txt for the public docs site."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    frontmatter = text[3:end].strip()
    body = text[end + 4 :].lstrip()
    data: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data, body


def _route_for(path: Path, docs_root: Path) -> str:
    rel = path.relative_to(docs_root).with_suffix("")
    parts = list(rel.parts)
    if parts == ["index"]:
        return "/docs/"
    if parts[-1] == "index":
        return "/docs/" + "/".join(parts[:-1]) + "/"
    return "/docs/" + "/".join(parts)


def _clean_body(body: str) -> str:
    lines = []
    for line in body.splitlines():
        if line.startswith("import "):
            continue
        if line.strip().startswith("<") and line.strip().endswith("/>"):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _docs_pages(docs_root: Path) -> list[tuple[Path, str, dict[str, str], str]]:
    paths = sorted(docs_root.rglob("*.md")) + sorted(docs_root.rglob("*.mdx"))
    pages: list[tuple[Path, str, dict[str, str], str]] = []
    for path in paths:
        meta, body = _split_frontmatter(_read(path))
        route = _route_for(path, docs_root)
        pages.append((path, route, meta, _clean_body(body)))
    return pages


def generate(docs_root: Path, out_dir: Path) -> None:
    pages = _docs_pages(docs_root)
    index_lines = [
        "# Row-Bot Docs",
        "",
        "Public documentation index for Row-Bot.",
        "",
    ]
    full_lines = [
        "# Row-Bot Docs Full Text",
        "",
        "Concatenated public documentation for Row-Bot.",
        "",
    ]
    for path, route, meta, body in pages:
        title = meta.get("title") or path.stem.replace("-", " ").title()
        description = meta.get("description", "")
        index_lines.append(f"- [{title}]({route})")
        if description:
            index_lines.append(f"  {description}")
        full_lines.extend([f"## {title}", "", f"Route: {route}", ""])
        if description:
            full_lines.extend([description, ""])
        full_lines.extend([body, ""])

    out_dir.mkdir(parents=True, exist_ok=True)
    docs_subdir = out_dir / "docs"
    docs_subdir.mkdir(parents=True, exist_ok=True)
    llms = "\n".join(index_lines).rstrip() + "\n"
    full = "\n".join(full_lines).rstrip() + "\n"
    for target in (out_dir / "llms.txt", docs_subdir / "llms.txt"):
        target.write_text(llms, encoding="utf-8")
    for target in (out_dir / "llms-full.txt", docs_subdir / "llms-full.txt"):
        target.write_text(full, encoding="utf-8")
    print(f"Wrote llms.txt and llms-full.txt to {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate llms.txt docs indexes")
    parser.add_argument("--docs-root", default="docs-site/docs")
    parser.add_argument("--out-dir", default="docs-site/static")
    args = parser.parse_args()
    generate((ROOT / args.docs_root).resolve(), (ROOT / args.out_dir).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
