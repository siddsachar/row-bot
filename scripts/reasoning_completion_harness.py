from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


DEFAULT_PROMPT = "Think carefully and explain the tradeoffs of archival preservation versus public access."
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass
class RunResult:
    case: str
    iteration: int
    prompt: str
    model: str
    elapsed_s: float = 0.0
    content_chars: int = 0
    reasoning_chars: int = 0
    token_chunks: int = 0
    reasoning_chunks: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    done_seen: bool = False
    done_reason: str = ""
    finish_reason: str = ""
    eval_count: int | None = None
    prompt_eval_count: int | None = None
    thread_id: str = ""
    checkpoint_content_chars: int = 0
    checkpoint_reasoning_chars: int = 0
    checkpoint_done_reason: str = ""
    checkpoint_eval_count: int | None = None
    checkpoint_prompt_eval_count: int | None = None
    error: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def reasoning_only_stop(self) -> bool:
        return (
            not self.error
            and self.reasoning_chars + self.checkpoint_reasoning_chars > 0
            and self.content_chars + self.checkpoint_content_chars == 0
        )

    @property
    def answered(self) -> bool:
        return self.content_chars + self.checkpoint_content_chars > 0

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["answered"] = self.answered
        data["reasoning_only_stop"] = self.reasoning_only_stop
        return data


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                if item.get("type") in {"text", "output_text"}:
                    parts.append(str(item.get("text") or ""))
                elif "text" in item:
                    parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(value)


def _apply_metadata(result: RunResult, metadata: dict[str, Any] | None) -> None:
    metadata = metadata or {}
    result.done_reason = str(metadata.get("done_reason") or result.done_reason or "")
    result.finish_reason = str(metadata.get("finish_reason") or result.finish_reason or "")
    result.eval_count = metadata.get("eval_count", result.eval_count)
    result.prompt_eval_count = metadata.get("prompt_eval_count", result.prompt_eval_count)


def _runtime_model_name(model: str) -> str:
    raw = str(model or "").strip()
    if raw.startswith("model:ollama:"):
        return raw.split(":", 2)[2]
    return raw


def run_direct_ollama(prompt: str, model: str, ctx: int, iteration: int) -> RunResult:
    from models import _ollama_base_url

    runtime_model = _runtime_model_name(model)
    result = RunResult("direct_ollama_stream", iteration, prompt, runtime_model)
    payload = {
        "model": runtime_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "think": True,
        "options": {"num_ctx": ctx},
    }
    req = urllib.request.Request(
        f"{_ollama_base_url().rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=240) as response:
            for raw_line in response:
                if not raw_line.strip():
                    continue
                item = json.loads(raw_line.decode("utf-8"))
                msg = item.get("message") if isinstance(item.get("message"), dict) else {}
                content = _safe_text(msg.get("content"))
                thinking = _safe_text(msg.get("thinking") or msg.get("reasoning_content"))
                if content:
                    result.content_chars += len(content)
                    result.token_chunks += 1
                if thinking:
                    result.reasoning_chars += len(thinking)
                    result.reasoning_chunks += 1
                if item.get("done"):
                    result.done_seen = True
                    _apply_metadata(result, item)
    except Exception as exc:
        result.error = str(exc)
    result.elapsed_s = round(time.perf_counter() - started, 3)
    return result


def _collect_langchain_stream(prompt: str, llm: Any, case: str, model: str, iteration: int) -> RunResult:
    result = RunResult(case, iteration, prompt, _runtime_model_name(model))
    started = time.perf_counter()
    try:
        for chunk in llm.stream(prompt):
            content = _safe_text(getattr(chunk, "content", ""))
            additional_kwargs = getattr(chunk, "additional_kwargs", None) or {}
            reasoning = _safe_text(additional_kwargs.get("reasoning_content"))
            if content:
                result.content_chars += len(content)
                result.token_chunks += 1
            if reasoning:
                result.reasoning_chars += len(reasoning)
                result.reasoning_chunks += 1
            tool_chunks = getattr(chunk, "tool_call_chunks", None) or []
            tool_calls = getattr(chunk, "tool_calls", None) or []
            if tool_chunks or tool_calls:
                result.tool_calls += max(len(tool_chunks), len(tool_calls), 1)
            metadata = getattr(chunk, "response_metadata", None) or {}
            if metadata:
                result.done_seen = bool(metadata.get("done", result.done_seen))
                _apply_metadata(result, metadata)
    except Exception as exc:
        result.error = str(exc)
    result.elapsed_s = round(time.perf_counter() - started, 3)
    return result


def run_langchain_plain(prompt: str, model: str, ctx: int, iteration: int) -> RunResult:
    from langchain_ollama import ChatOllama
    from models import _ollama_base_url

    llm = ChatOllama(
        model=_runtime_model_name(model),
        base_url=_ollama_base_url(),
        num_ctx=ctx,
        reasoning=True,
    )
    return _collect_langchain_stream(prompt, llm, "langchain_plain_stream", model, iteration)


def run_langchain_one_tool(prompt: str, model: str, ctx: int, iteration: int) -> RunResult:
    from langchain_core.tools import tool
    from langchain_ollama import ChatOllama
    from models import _ollama_base_url

    @tool
    def thoth_probe(value: str) -> str:
        """Return the requested value."""
        return value

    llm = ChatOllama(
        model=_runtime_model_name(model),
        base_url=_ollama_base_url(),
        num_ctx=ctx,
        reasoning=True,
    ).bind_tools([thoth_probe])
    return _collect_langchain_stream(prompt, llm, "langchain_one_tool_stream", model, iteration)


def run_langchain_enabled_tools(prompt: str, model: str, ctx: int, iteration: int) -> RunResult:
    from langchain_ollama import ChatOllama
    from models import _ollama_base_url
    from tools import registry as tool_registry

    lc_tools = []
    for tool_obj in tool_registry.get_enabled_tools():
        lc_tools.extend(tool_obj.as_langchain_tools())
    llm = ChatOllama(
        model=_runtime_model_name(model),
        base_url=_ollama_base_url(),
        num_ctx=ctx,
        reasoning=True,
    ).bind_tools(lc_tools)
    result = _collect_langchain_stream(prompt, llm, "langchain_enabled_tools_stream", model, iteration)
    result.notes.append(f"bound_tools={len(lc_tools)}")
    return result


def _fill_checkpoint_stats(result: RunResult) -> None:
    if not result.thread_id:
        return
    try:
        from threads import get_latest_checkpoint_messages

        messages = get_latest_checkpoint_messages(result.thread_id)
        for msg in reversed(messages):
            if getattr(msg, "type", None) != "ai":
                continue
            additional_kwargs = getattr(msg, "additional_kwargs", None) or {}
            metadata = getattr(msg, "response_metadata", None) or {}
            result.checkpoint_content_chars = len(_safe_text(getattr(msg, "content", "")))
            result.checkpoint_reasoning_chars = len(_safe_text(additional_kwargs.get("reasoning_content")))
            result.checkpoint_done_reason = str(metadata.get("done_reason") or metadata.get("finish_reason") or "")
            result.checkpoint_eval_count = metadata.get("eval_count")
            result.checkpoint_prompt_eval_count = metadata.get("prompt_eval_count")
            break
    except Exception as exc:
        result.notes.append(f"checkpoint_read_error={exc}")


def run_thoth_agent(prompt: str, model: str, ctx: int, iteration: int, *, cleanup: bool) -> RunResult:
    from agent import clear_agent_cache, stream_agent
    from threads import _delete_thread, _save_thread_meta
    from tools import registry as tool_registry

    del ctx
    result = RunResult("thoth_agent_stream", iteration, prompt, model)
    thread_id = f"diag{uuid.uuid4().hex[:8]}"
    result.thread_id = thread_id
    _save_thread_meta(thread_id, f"Reasoning diagnostic {thread_id}")
    config = {
        "configurable": {
            "thread_id": thread_id,
            "runtime_surface": "normal_chat",
            "runtime_mode": "auto",
            "model_override": model,
        },
        "recursion_limit": 50,
    }
    enabled_tool_names = [tool.name for tool in tool_registry.get_enabled_tools()]
    started = time.perf_counter()
    try:
        clear_agent_cache()
        for event_type, payload in stream_agent(prompt, enabled_tool_names, config):
            if event_type == "thinking_token":
                text = _safe_text(payload)
                result.reasoning_chars += len(text)
                result.reasoning_chunks += 1
            elif event_type == "token":
                text = _safe_text(payload)
                result.content_chars += len(text)
                result.token_chunks += 1
            elif event_type == "tool_call":
                result.tool_calls += 1
            elif event_type == "tool_done":
                result.tool_results += 1
            elif event_type == "done":
                result.done_seen = True
            elif event_type == "error":
                result.error = _safe_text(payload)
    except Exception as exc:
        result.error = str(exc)
    result.elapsed_s = round(time.perf_counter() - started, 3)
    _fill_checkpoint_stats(result)
    if cleanup:
        try:
            _delete_thread(thread_id)
        except Exception as exc:
            result.notes.append(f"cleanup_error={exc}")
    return result


CASES: dict[str, Callable[..., RunResult]] = {
    "direct": run_direct_ollama,
    "lc_plain": run_langchain_plain,
    "lc_one_tool": run_langchain_one_tool,
    "lc_enabled_tools": run_langchain_enabled_tools,
}


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run live reasoning-completion diagnostics.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", default="")
    parser.add_argument("--ctx", type=int, default=65536)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--cases",
        default="direct,lc_plain,lc_one_tool,lc_enabled_tools,thoth_agent",
        help="Comma-separated cases: direct,lc_plain,lc_one_tool,lc_enabled_tools,thoth_agent",
    )
    parser.add_argument("--keep-threads", action="store_true")
    args = parser.parse_args()

    if not args.model:
        from models import get_current_model

        args.model = get_current_model()

    selected_cases = [item.strip() for item in args.cases.split(",") if item.strip()]
    results: list[RunResult] = []
    for iteration in range(1, max(1, args.repeat) + 1):
        for case in selected_cases:
            if case == "thoth_agent":
                result = run_thoth_agent(
                    args.prompt,
                    args.model,
                    args.ctx,
                    iteration,
                    cleanup=not args.keep_threads,
                )
            else:
                runner = CASES.get(case)
                if runner is None:
                    result = RunResult(case, iteration, args.prompt, args.model, error="unknown case")
                else:
                    try:
                        result = runner(args.prompt, args.model, args.ctx, iteration)
                    except Exception as exc:
                        result = RunResult(case, iteration, args.prompt, args.model, error=str(exc))
            results.append(result)
            _print_json({"type": "run", **result.to_dict()})

    total = len(results)
    answered = sum(1 for item in results if item.answered)
    reasoning_only = sum(1 for item in results if item.reasoning_only_stop)
    errors = sum(1 for item in results if item.error)
    by_case: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = by_case.setdefault(item.case, {"total": 0, "answered": 0, "reasoning_only_stop": 0, "errors": 0})
        bucket["total"] += 1
        bucket["answered"] += int(item.answered)
        bucket["reasoning_only_stop"] += int(item.reasoning_only_stop)
        bucket["errors"] += int(bool(item.error))
    _print_json({
        "type": "summary",
        "total": total,
        "answered": answered,
        "reasoning_only_stop": reasoning_only,
        "errors": errors,
        "by_case": by_case,
    })
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
