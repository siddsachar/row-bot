"""``python -m tests.e2e.live_eval.worker``: one provider's eval tasks in a fresh process.

The parent (``orchestrate.run_provider``) starts this process with
``ROW_BOT_DATA_DIR`` pointing at a new folder inside the run folder and
``ROW_BOT_TEST_MODE`` set, so every Row-Bot module binds to that folder and the
OS keychain stays closed. Provider keys arrive in the inherited environment,
never on the command line. The ChatGPT lane's sign-in (``--credentials stdin``)
arrives on stdin and lives in an in-memory keyring; tokens it refreshes go back
to the parent on stdout (``eval_profile``). Results are written after every
task, so a crash keeps what ran.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path


def _parse_cap(text: str | None, kind: type) -> object:
    value = str(text or "").strip().lower()
    return None if value in {"", "none", "off"} else kind(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tasks", required=True, help="comma-separated task ids, in run order")
    parser.add_argument("--out", required=True, help="worker result JSON")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--log", default="")
    parser.add_argument("--variant", default="")
    parser.add_argument("--max-usd", default="")
    parser.add_argument("--max-tokens", default="")
    parser.add_argument("--credentials", choices=("env", "stdin"), default="env",
                        help="stdin: an OAuth sign-in handed over by the parent (the ChatGPT lane)")
    args = parser.parse_args(argv)

    data_dir = os.environ.get("ROW_BOT_DATA_DIR", "")
    if not data_dir:
        parser.error("ROW_BOT_DATA_DIR must name the run's own data folder")
    from .ledger import everyday_data_folder

    if everyday_data_folder(Path(data_dir)):
        parser.error("refusing to use the everyday Row-Bot data folder")
    if not os.environ.get("ROW_BOT_TEST_MODE"):
        parser.error("ROW_BOT_TEST_MODE must be set (it keeps the OS keychain closed)")

    from . import variants
    from .budget import BudgetGuard, Caps
    from .fixtures import FixtureServer
    from .pricing import load_rates
    from .tasks import Context, by_id

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    from .report import redact

    if args.credentials == "stdin":
        from .eval_profile import seed_worker

        seed_worker(json.loads(sys.stdin.read() or "{}"))
    if args.log:

        class Redacting(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return redact(super().format(record))

        handler = logging.FileHandler(args.log, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(Redacting("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger("row_bot").addHandler(handler)

    caps = Caps(max_usd=_parse_cap(args.max_usd, float), max_tokens=_parse_cap(args.max_tokens, int))
    rates = load_rates()
    variant = variants.parse(args.variant)
    known = by_id()
    tasks = [known[name] for name in args.tasks.split(",") if name]
    result: dict = {
        "provider": args.provider,
        "model": args.model,
        "variant": variant.describe(),
        "status": "starting",
        "setup_notes": [],
        "variant_changes": [],
        "tasks": [],
        "spent": {},
    }

    def save() -> None:
        out.write_bytes((redact(json.dumps(result, indent=2, default=str)) + "\n").encode("utf-8"))

    save()
    from .runner import Runtime, run_tasks

    with FixtureServer() as server:
        runtime = Runtime(
            provider_id=args.provider,
            model_id=args.model,
            workspace_root=Path(args.workspace),
            caps=caps,
            rates=rates,
            context=Context.from_env(fixture_url=server.url),
        )
        guard = BudgetGuard(caps)
        try:
            result["variant_changes"] = runtime.setup(variant)
            result["setup_notes"] = runtime.setup_notes
            result["model"] = runtime.model_id
            result["status"] = "running"
            save()

            def on_record(record) -> None:
                result["tasks"].append(asdict(record))
                result["spent"] = asdict(guard.spent)
                save()

            run_tasks(runtime, tasks, guard, label=variant.label, on_record=on_record)
            result["status"] = "ran"
        except Exception as exc:
            result["status"] = "error"
            result["reason"] = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            result["spent"] = asdict(guard.spent)
            result["budget_refusals"] = runtime.gate.refused if runtime.gate else 0
            runtime.close()
            save()
    return 0 if result["status"] == "ran" else 1


if __name__ == "__main__":
    sys.exit(main())
