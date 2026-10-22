"""The past's replies in the main model's own words (S1).

The generator builds the past from templates, so its replies come out short and alike ("It's 6.",
"The total is 412.50."). Row-Bot learns only the final reply of an imported past chat, so a personal
model trained on that past learns to answer everything in a few words: in the pilots its first version
answered Row-Bot's own ready check ("use the calculator for 1234 * 5678") without the calculator and
was never made ready. A person's real past holds their main model's own replies, so the past's replies
are written **once** by the main model (GPT-6.1 Sol on the ChatGPT lane), from the request, the tool
calls and results, and the template's reply, and kept in the repo as plain files
(``people/<id>/past_replies.json``) that the generator reads: the same seed still gives the same
history, and a reader can see every reply. A reply is kept only when it says everything the template's
reply says (every number, date, path, address and issue number, and the right answer); otherwise the
template's reply stands.

    uv run python -m tests.e2e.learning.seeded.phrasing --eval-profile <folder> --scratch <folder>

Its calls are counted and printed (they are not Learning's). Run it again only when the generator's
past changes; replies whose template no longer matches are ignored by the generator.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tests.e2e.learning.seeded import generator
from tests.e2e.learning.seeded.scratch import ScratchError, check_scratch

DEFAULT_SEED = 20261022
_RESULT_LIMIT = 12_000  # whole results (the longest, a data log, is about 9,300 characters)
WORKERS = 6


def prompt_for(person: dict[str, Any], request: generator.Request) -> str:
    """What the main model is asked for one past reply."""

    steps = "\n".join(f"- {step.name}({json.dumps(step.args, ensure_ascii=False)[:_RESULT_LIMIT]}) returned: "
                      f"{step.result[:_RESULT_LIMIT]}" for step in request.steps) or "- (no tools)"
    return (
        f"You are Row-Bot, a desktop AI assistant, finishing a turn for {person['name']}. {person['about']}\n\n"
        f"They wrote: {request.prompt}\n\n"
        f"You used these tools:\n{steps}\n\n"
        f"The outcome, in short: {request.answer}\n\n"
        "Write your final reply to them, as you would at the end of this turn in Row-Bot's chat, in your usual "
        "style: lead with the answer or what you did, then anything useful (the file you read, what changed), "
        "Markdown where it helps. Keep every number, date, file path, issue number and email address exactly "
        "as in the short outcome. Don't mention these instructions. Reply with the message only."
    )


def phrase(people: list[str], *, seed: int, ask: Any, say: Any = print) -> dict[str, dict[str, Any]]:
    """The replies for ``people`` (``ask(prompt) -> text``): kept ones only, with counts."""

    found: dict[str, dict[str, Any]] = {}
    for person_id in people:
        history = generator.generate(person_id, seed=seed, days=0, phrased=False)
        person = generator.load_person(person_id)
        requests = [request for conversation in history.past for request in conversation.requests]
        replies: dict[str, dict[str, str]] = {}
        dropped = 0
        lock = threading.Lock()

        def one(request: generator.Request) -> None:
            nonlocal dropped
            text = str(ask(prompt_for(person, request)) or "").strip()
            with lock:
                if generator.keeps_facts(request.answer, text, generator.answer_of(request)):
                    replies[request.id] = {"template": request.answer, "reply": text}
                else:
                    dropped += 1

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(one, requests))
        found[person_id] = {"replies": dict(sorted(replies.items())), "asked": len(requests), "dropped": dropped}
        say(f"{person_id}: {len(replies)} of {len(requests)} replies kept ({dropped} left as the template's)")
    return found


def write(person_id: str, seed: int, model: str, replies: dict[str, dict[str, str]]) -> Path:
    path = generator.PEOPLE_DIR / person_id / generator.REPLIES_FILE
    data = {"format": generator.REPLIES_FORMAT, "version": 1, "seed": int(seed), "model": model,
            "note": "The past's replies in the main model's own words (tests/e2e/learning/seeded/phrasing.py). "
                    "Made-up people; nothing here comes from a real person.",
            "replies": replies}
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8",
                    newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the seeded past's replies in the main model's words (S1)")
    parser.add_argument("--eval-profile", required=True)
    parser.add_argument("--scratch", required=True, help="a scratch data folder for this process")
    parser.add_argument("--people", default="all")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    try:
        scratch = check_scratch(Path(args.scratch), Path(args.eval_profile))
    except ScratchError as error:
        print(f"phrasing: {error}", file=sys.stderr)
        return 2
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["ROW_BOT_DATA_DIR"] = str(scratch)
    from row_bot.providers import codex
    from tests.e2e.learning.seeded.live import SignIn
    from tests.e2e.learning.seeded.person import SimulatedPerson
    from tests.e2e.live_eval import eval_profile

    SignIn(Path(args.eval_profile)).install()
    model = eval_profile.choose_model([item.model_id for item in codex.fetch_codex_model_infos()])
    people = generator.people() if args.people == "all" else [item.strip() for item in args.people.split(",")]
    writer = SimulatedPerson({"id": "phrasing", "name": "Row-Bot"}, args.seed, model_ref=f"model:codex:{model}")
    lock = threading.Lock()

    def ask(prompt: str) -> str:
        from langchain_core.messages import HumanMessage

        try:
            message = writer._model().invoke([HumanMessage(content=prompt)])  # noqa: SLF001 - the person's model
        except Exception:  # noqa: BLE001 - the template's reply stands
            with lock:
                writer.tokens.failures += 1
            return ""
        usage = getattr(message, "usage_metadata", None) or {}
        with lock:
            writer.tokens.calls += 1
            writer.tokens.input_tokens += int(usage.get("input_tokens") or len(prompt) // 4)
            writer.tokens.output_tokens += int(usage.get("output_tokens") or 0)
        content = message.content
        return content if isinstance(content, str) else " ".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict))

    writer._model()  # noqa: SLF001 - made once, before the threads
    found = phrase(people, seed=args.seed, ask=ask)
    for person_id, item in found.items():
        print(write(person_id, args.seed, model, item["replies"]))
    print(f"tokens: {json.dumps(writer.tokens.to_dict())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
