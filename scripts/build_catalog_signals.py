"""Write the quality signals shipped beside the MCP Registry snapshot (a release-preparation step).

Run it at release preparation, beside scripts/cut_release.py, once the snapshot is current:

    GITHUB_TOKEN=<token> python scripts/build_catalog_signals.py

The "Catalog signals" workflow (.github/workflows/catalog-signals.yml) runs it with the workflow's
own read-only GITHUB_TOKEN and uploads the file to commit in the release-preparation pull request;
locally, `gh auth token` gives a token.

It reads, for every GitHub repository the snapshot names, its stars, last push, archived flag, the
repository it was forked from and its licence (GitHub GraphQL, 50 repositories a request); and for
every Registry record, when its first version was published and how many versions it has (one
read-only, paginated pass over every Registry version). Row-Bot itself never makes these calls:
the app reads only the file this writes.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from row_bot.integrations import facts  # noqa: E402
from row_bot.mcp_client import registry_signals, registry_snapshot  # noqa: E402

GRAPHQL = "https://api.github.com/graphql"
BATCH = 50
_FIELDS = "stargazerCount pushedAt isArchived parent { nameWithOwner } licenseInfo { spdxId }"


def _epoch(value: object) -> int:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def github_repositories(entries: Iterable) -> list[str]:
    """Every GitHub repository the records name, normalised (``github.com/owner/repo``)."""
    return sorted({name for name in (facts.repository(entry.url) for entry in entries) if name.startswith("github.com/")})


def query(names: list[str]) -> str:
    """One GraphQL request for a batch of repositories, each under its own alias."""
    parts = [f"r{i}: repository(owner: {json.dumps(name.split('/')[1])}, name: {json.dumps(name.split('/')[2])}) {{ {_FIELDS} }}"
             for i, name in enumerate(names)]
    return "query { rateLimit { remaining resetAt } " + " ".join(parts) + " }"


def read_repositories(names: list[str], post: Callable[[str], dict], *, sleep: Callable[[float], None] = time.sleep,
                      pause: float = 0.5) -> dict[str, registry_signals.Repository]:
    """Signals for every repository GitHub still has; a missing or private one is left out."""
    found = {}
    for start in range(0, len(names), BATCH):
        batch = names[start:start + BATCH]
        data = post(query(batch)).get("data") or {}
        for i, name in enumerate(batch):
            repo = data.get(f"r{i}")
            if not isinstance(repo, dict):
                continue
            parent = ((repo.get("parent") or {}).get("nameWithOwner") or "").lower()
            licence = ((repo.get("licenseInfo") or {}).get("spdxId") or "")
            found[name] = registry_signals.Repository(
                stars=int(repo.get("stargazerCount") or 0), pushed=_epoch(repo.get("pushedAt") or ""),
                archived=repo.get("isArchived") is True, parent="github.com/" + parent if parent else "",
                licence="" if licence == "NOASSERTION" else licence[:64])
        limit = data.get("rateLimit") or {}
        if int(limit.get("remaining", 1000)) < 50:  # Wait for the hour's allowance rather than fail.
            sleep(min(max(_epoch(limit.get("resetAt", "")) - time.time(), 1), 3600))
        sleep(pause)
    return found


def read_records(names: set[str], get: Callable[[str], dict], *, sleep: Callable[[float], None] = time.sleep,
                 pause: float = 0.25, max_pages: int = 5000) -> dict[str, tuple[int, int]]:
    """For each named Registry record: when its first version was published, and how many versions it has."""
    first: dict[str, int] = {}
    count: dict[str, int] = {}
    cursor, seen = "", set()
    for _ in range(max_pages):
        data = get(registry_snapshot.SOURCE + "?" + urlencode({"limit": "100", **({"cursor": cursor} if cursor else {})}))
        for envelope in data.get("servers") or []:
            name = (envelope.get("server") or {}).get("name")
            official = (envelope.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
            if name not in names or official.get("status") == "deleted":
                continue
            published = _epoch(official.get("publishedAt") or "")
            count[name] = count.get(name, 0) + 1
            if published and (name not in first or published < first[name]):
                first[name] = published
        cursor = (data.get("metadata") or {}).get("nextCursor") or ""
        if not cursor:
            return {name: (first.get(name, 0), total) for name, total in count.items()}
        if cursor in seen:
            raise ValueError("invalid_registry_cursor")
        seen.add(cursor)
        sleep(pause)
    raise ValueError("registry_versions_too_large")


def _registry_get(url: str) -> dict:
    """One Registry page, retried politely as the snapshot build does."""
    import httpx
    for attempt in range(5):
        try:
            return json.loads(registry_snapshot._get(url, {"Accept": "application/json"}, {}))
        except (httpx.HTTPStatusError, httpx.TransportError, ConnectionError, TimeoutError) as exc:
            wait = registry_snapshot._retry_after(exc, attempt)
            if wait is None:
                raise
            time.sleep(wait)
    raise ValueError("registry_unavailable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", type=Path, default=registry_snapshot.SHIPPED)
    parser.add_argument("--output", type=Path, default=registry_signals.SHIPPED)
    parser.add_argument("--pause", type=float, default=0.5, help="seconds between requests")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("Set GITHUB_TOKEN (the workflow's own token, or `gh auth token` locally).")
    import httpx
    snapshot = registry_snapshot.read_snapshot(args.snapshot)
    names = github_repositories(snapshot["entries"])
    with httpx.Client(timeout=60, headers={"Authorization": "Bearer " + token, "User-Agent": "row-bot-release"}) as client:
        def post(text: str) -> dict:
            for attempt in range(5):
                response = client.post(GRAPHQL, json={"query": text})
                if response.status_code in {502, 503, 504} and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                response.raise_for_status()
                return response.json()
            raise ValueError("github_unavailable")
        started = time.time()
        repositories = read_repositories(names, post, pause=args.pause)
    records = read_records({entry.metadata["canonical_name"] for entry in snapshot["entries"]}, _registry_get,
                           pause=args.pause / 2)
    data = registry_signals.encode(captured_at=round(started, 3), snapshot_digest=snapshot["digest"],
                                   repositories=repositories, records=records)
    args.output.write_bytes(data)
    print(json.dumps({"output": str(args.output), "bytes": len(data), "repositories": len(repositories),
                      "of": len(names), "records": len(records)}, indent=2))


if __name__ == "__main__":
    main()
