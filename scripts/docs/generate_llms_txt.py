"""Generate llms.txt and llms-full.txt for the public docs site."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.docs.schemas import clean_public_text, public_route_for_doc


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
    return public_route_for_doc(path, docs_root)


def _clean_body(body: str) -> str:
    lines = []
    skip_component = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "export ")):
            continue
        if stripped.startswith("<") and stripped.endswith("/>"):
            continue
        if stripped.startswith("<") and not stripped.startswith(("<table", "<thead", "<tbody", "<tr", "<td", "<th")):
            skip_component = not stripped.startswith("</")
            continue
        if skip_component:
            if stripped.startswith("</"):
                skip_component = False
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\{\/\*.*?\*\/\}", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return clean_public_text(cleaned)


def _sidebar_order(docs_root: Path) -> list[str]:
    sidebars = docs_root.parent / "sidebars.ts"
    if not sidebars.exists():
        return []
    text = _read(sidebars)
    ids = re.findall(r"'([^']+)'", text) + re.findall(r'"([^"]+)"', text)
    return [doc_id for doc_id in ids if not doc_id.startswith(("/", "http"))]


def _doc_id_for(path: Path, docs_root: Path) -> str:
    rel = path.relative_to(docs_root).with_suffix("")
    return str(rel).replace("\\", "/")


def _docs_pages(docs_root: Path) -> list[tuple[Path, str, dict[str, str], str]]:
    paths = sorted(docs_root.rglob("*.md")) + sorted(docs_root.rglob("*.mdx"))
    order = {doc_id: index for index, doc_id in enumerate(_sidebar_order(docs_root))}

    def key(path: Path) -> tuple[int, str]:
        doc_id = _doc_id_for(path, docs_root)
        return (order.get(doc_id, 10_000), doc_id)

    pages: list[tuple[Path, str, dict[str, str], str]] = []
    for path in sorted(paths, key=key):
        meta, body = _split_frontmatter(_read(path))
        route = _route_for(path, docs_root)
        pages.append((path, route, meta, _clean_body(body)))
    return pages


def generate(docs_root: Path, out_dir: Path) -> None:
    pages = _docs_pages(docs_root)
    if not pages:
        raise RuntimeError(f"No docs pages found under {docs_root}")

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
        title = clean_public_text(meta.get("title") or path.stem.replace("-", " ").title())
        description = clean_public_text(meta.get("description", ""))
        index_lines.append(f"- [{title}]({route})")
        if description:
            index_lines.append(f"  Route: {route}. {description}")
        else:
            index_lines.append(f"  Route: {route}.")
        full_lines.extend([f"## {title}", "", f"Route: {route}", ""])
        if description:
            full_lines.extend([description, ""])
        if body:
            full_lines.extend([body, ""])

    out_dir.mkdir(parents=True, exist_ok=True)
    docs_subdir = out_dir / "docs"
    docs_subdir.mkdir(parents=True, exist_ok=True)
    llms = "\n".join(index_lines).rstrip() + "\n"
    full = "\n".join(full_lines).rstrip() + "\n"
    if len(llms.splitlines()) < 5 or len(full.splitlines()) < 10:
        raise RuntimeError("Generated LLM docs output is unexpectedly empty")
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
