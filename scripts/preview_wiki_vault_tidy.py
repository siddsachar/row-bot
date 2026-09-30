"""Preview the wiki vault's one-time naming tidy (B256) without writing anything.

The tidy runs by itself before the app's first write to a vault. This preview
reads the saved memories through a read-only connection and the vault's files,
and prints what the tidy would adopt in place, create or refresh under readable
names, move to ``raw/.row-bot-retired/hashed-names-<date>/``, and leave for
review. It never opens the knowledge graph for writing and never writes to the
vault.

Usage (PowerShell), with the app closed:

    $env:ROW_BOT_DATA_DIR = "$HOME\\.row-bot"   # the app data folder to read
    uv run python scripts/preview_wiki_vault_tidy.py              # the configured vault
    uv run python scripts/preview_wiki_vault_tidy.py --vault "D:\\Notes\\Vault" --json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", type=pathlib.Path, help="vault folder (default: the configured one)")
    parser.add_argument("--json", action="store_true", help="print the whole plan as JSON")
    args = parser.parse_args()

    from row_bot import wiki_vault

    if args.vault is not None:
        vault = args.vault.expanduser().absolute()
        wiki_vault.get_vault_path = lambda: vault  # read this folder; the saved setting is untouched
    plan = wiki_vault.plan_vault_tidy()
    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    print(f"Vault: {plan['vault']}")
    print("Tidy needed." if plan["needed"] else "Already tidied: the app will not run the tidy again.")
    for key, label in (("adopt", "Adopt in place (no change)"), ("refresh", "Refresh from the current memory"),
                       ("create", "Create a readable article"), ("rollups", "Refresh index pages")):
        print(f"{label}: {len(plan[key])}")
        for relative in plan[key][:20]:
            print(f"    wiki/{relative}")
    print(f"Move hashed copies: {len(plan['move'])}")
    for move in plan["move"][:20]:
        print(f"    {move['from']} -> {move['to']}")
    print(f"Leave for review: {len(plan['review'])}")
    for item in plan["review"]:
        print(f"    wiki/{item['relative']} ({item['reason']})")
    print("Lists show the first 20; --json prints everything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
