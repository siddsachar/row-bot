"""LongMemEval-V2's own protocol, with Row-Bot as the memory module (the matched setup, prereg C6).

The benchmark holds the reader fixed: a memory module returns a bounded memory context for each question, and
``Qwen/Qwen3.5-9B`` answers from it (``evaluation/harness.py``: ``build_messages``, the domain system prompts, a
200,000-token context cap; ``evaluation/run_eval.py``: thinking on, temperature 0.6, top-p 0.95, top-k 20, 20,000
output tokens). Row-Bot is the memory module the way the authors' Codex baseline is one: one turn per question on
Row-Bot's real memory path (recall, ``search_memory``, ``search_conversations``), asked as a retrieval module with
the Codex baseline's own rules, not as the answering model. The memory context is that turn's analysis, then the
memories recall showed, then every tool result in the order the turn got them.

Two stages, so each books only what it uses:

- ``bench.py run --mode controller`` (Row-Bot's model: the plan or the card) writes ``contexts.jsonl``;
- ``bench.py read`` (the fixed reader on the card) writes ``answers.jsonl``; ``bench.py judge`` scores them.

The reader gets text only, as every Row-Bot run here: a question's screenshot is not sent (29 of 451), and
tokens are counted with ``o200k_base``, not Qwen's processor; the report says both.
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from typing import Any, Callable

from ..trajectory import Step
from .datasets import MemoryTask
from .full_context import V2_MAX_TOKENS, count_tokens

MODE = "controller"
READER = "qwen3.5:9b"
#: ``run_eval.py``'s reader defaults (``--reader-temperature`` 0.6, ``--reader-top-p`` 0.95, ``--reader-top-k`` 20,
#: ``--max-completion-tokens`` 20000, ``--reader-enable-thinking`` on).
READER_OPTIONS = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "num_predict": 20_000}
READER_THINK = True
#: The reader's window: Qwen3.5-9B's, which the 200,000-token cap plus the answer fits in.
READER_MAX_CTX = 262_144
#: The Codex baseline's instructions to its memory module (``memory_modules/codex.py``: ``QUESTION_INSTRUCTIONS``),
#: with its file paths and JSON output turned into Row-Bot's own tools and a reply.
CONTROLLER_TEMPLATE = (
    "You are acting as a memory retrieval module, not as the final answering model.\n\n"
    "Question: {question}\n\n"
    "Search your memory and our past conversations for the evidence this question needs before you reply.\n"
    "Rules:\n"
    "- Do not answer the question directly.\n"
    "- Move fast and prefer targeted searches.\n"
    "- Put the most important evidence first.\n"
    "- Avoid repeating evidence when several conversations support the same point.\n\n"
    "Reply with two sections only: \"## Support Analysis\" and \"## Relevant Procedure and Hint Notes\". You may "
    "mention the likely answer when the evidence strongly supports it. If you find nothing useful, say so briefly."
)
MEMORY_HEADER = "## Memories recalled\n"
EVIDENCE_HEADER = "## Retrieved Evidence\n"


def domain_of(task: MemoryTask) -> str:
    """``lmev2_small_web`` -> ``web``."""
    return task.task_id.rsplit("_", 1)[-1]


def recall_text(context_text: str, header: str) -> str:
    """The recall block a turn context holds (its header and its ``- [id=...]`` lines), or ''."""
    start = context_text.find(header)
    if start < 0:
        return ""
    lines = context_text[start + len(header):].splitlines()
    kept = []
    for line in lines:
        if not line.startswith("- [id="):
            break
        kept.append(line)
    return "\n".join(kept)


def turn_evidence(thread_id: str) -> tuple[str, list[dict[str, str]]]:
    """What a question's turn saw: the memories recall showed, and each tool call with its whole result."""
    from row_bot import threads
    from row_bot.memory_policy import RECALL_BLOCK_HEADER
    from row_bot.turn_context import is_turn_context_message

    messages = threads.get_latest_checkpoint_messages(thread_id)
    recalled = "\n".join(filter(None, (recall_text(str(getattr(message, "content", "")), RECALL_BLOCK_HEADER)
                                        for message in messages if is_turn_context_message(message))))
    results = {str(getattr(message, "tool_call_id", "")): str(getattr(message, "content", ""))
               for message in messages if getattr(message, "type", "") == "tool"}
    calls = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            calls.append({"name": str(call.get("name") or ""),
                          "args": json.dumps(call.get("args") or {}, ensure_ascii=False, sort_keys=True),
                          "result": results.get(str(call.get("id") or ""), "")})
    return recalled, calls


def memory_context(reply: str, recalled: str, calls: list[dict[str, str]],
                   max_tokens: int = V2_MAX_TOKENS) -> tuple[list[str], dict[str, Any]]:
    """The items the reader gets, most important first, cut to the longest prefix of whole items within
    *max_tokens* (``harness.truncate_memory_context``)."""
    items = [reply.strip() + "\n"] if reply.strip() else []
    if recalled.strip():
        items.append(MEMORY_HEADER + recalled.strip() + "\n")
    evidence = [f"### {call['name']}({call['args']})\n{call['result'].strip()}\n" for call in calls
                if call["result"].strip()]
    if evidence:
        items += [EVIDENCE_HEADER, *evidence]
    sizes = [count_tokens(item) for item in items]
    kept, used = [], 0
    for item, size in zip(items, sizes):
        if used + size > max_tokens:
            break
        kept.append(item)
        used += size
    return kept, {"all_items": len(items), "kept_items": len(kept), "tokens": sum(sizes), "kept_tokens": used,
                  "truncated": len(kept) < len(items)}


def ask(runtime: Any, task: MemoryTask, *, stop: Callable[[], bool] = lambda: False,
        on_context: Callable[[Step, dict[str, Any]], None] = lambda step, row: None) -> list[Step]:
    """One controller turn per question (each in its own incognito chat, on the task's date), and its context."""
    from ..runtime import pinned_clock
    from .adapter import StopRequested

    steps: list[Step] = []
    with pinned_clock(task.ask_at):
        for question in task.questions:
            if stop():
                raise StopRequested(f"stopped before {question.question_id}")
            thread_id = runtime.new_thread(f"{task.benchmark} {question.question_id}", incognito=True)
            step = runtime.turn(thread_id, CONTROLLER_TEMPLATE.format(question=question.question),
                                name=question.question_id)
            step.metrics["category"] = question.category
            recalled, calls = turn_evidence(thread_id)
            items, info = memory_context(step.answer, recalled, calls)
            step.metrics["memory_context"] = info
            steps.append(step)
            on_context(step, {"question_id": question.question_id, "task_id": task.task_id,
                              "domain": domain_of(task), "status": step.status, "items": items, **info})
    return steps


# -- the fixed reader (no Row-Bot: the benchmark's reader on Ollama) -------------------------------------------

def domain_prompts(harness_file: Path) -> dict[str, str]:
    """``DOMAIN_SYSTEM_PROMPTS`` read from the benchmark's own ``evaluation/harness.py`` (parsed, never run)."""
    tree = ast.parse(Path(harness_file).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "DOMAIN_SYSTEM_PROMPTS"
                                                 for target in node.targets):
            found = ast.literal_eval(node.value)
            if isinstance(found, dict) and found:
                return {str(key): str(value) for key, value in found.items()}
    raise ValueError(f"{harness_file} has no DOMAIN_SYSTEM_PROMPTS")


def reader_messages(system_prompt: str, items: list[str], question: str) -> list[dict[str, str]]:
    """``harness.build_messages`` for text items: the memory context, then the question."""
    body = "### Memory context:\n" + ("".join(items) if items else "(empty)")
    return [{"role": "system", "content": system_prompt},
            {"role": "user", "content": body + f"\n\n### Question to answer:\n{question}"}]


def read_one(http: Any, base: str, messages: list[dict[str, str]], context_tokens: int) -> dict[str, Any]:
    """One answer from the fixed reader: its reply (the thinking apart), counts and time."""
    num_ctx = min(READER_MAX_CTX, max(8_192, context_tokens + READER_OPTIONS["num_predict"] + 2_048))
    started = time.monotonic()
    reply = http.post(f"{base}/api/chat", json={
        "model": READER, "messages": messages, "stream": False, "think": READER_THINK, "keep_alive": "10m",
        "options": {**READER_OPTIONS, "num_ctx": num_ctx}}, timeout=3_600).json()
    if reply.get("error"):
        raise RuntimeError(f"reader error: {reply['error']}"[:300])
    message = reply.get("message") or {}
    return {"hypothesis": str(message.get("content") or ""), "seconds": round(time.monotonic() - started, 3),
            "input_tokens": int(reply.get("prompt_eval_count") or 0), "output_tokens": int(reply.get("eval_count") or 0),
            "num_ctx": num_ctx}
