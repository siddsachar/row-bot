"""A failure taxonomy over tau2 simulations (a run's ``results.json``): what failed and what the agent did.

Standard library only. For each simulation:

- **outcome**: the reward and its parts (``DB``: the final database is the gold one; ``COMMUNICATE``: the
  required facts reached the customer; ``NL_ASSERTION``), and the termination reason;
- **bucket** of a failure, first match wins: ``harness`` (an agent error or infrastructure error: ours, not
  the model's), ``ran out`` (max steps or too many tool errors), ``wrong write`` (a gold write action the
  agent never made with the same arguments: the database is wrong), ``extra write`` (every gold action made,
  yet the database is wrong: a write the policy didn't call for), ``not communicated`` (the database is
  right; a required fact never reached the customer), ``judged`` (the NL assertions failed);
- **behaviour**: agent messages, how many were JSON-wrapped (``{"message": ...}``), how many ended the turn
  on a progress line ("I'm checking...", "Next I'll...") without asking the customer anything, tool calls,
  tool errors, transfers to a human.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterable

WRITE = "write"
_PROGRESS = re.compile(
    r"\b(?:next|now|then),?\s+I(?:'|’)ll\b|\bI(?:'|’)ll\s+(?:now|next|then|finish|review|check|look|verify|confirm"
    r"|proceed|continue|calculate|process)\b|\bI(?:'|’)m\s+(?:now\s+)?(?:checking|reviewing|looking|verifying|going to"
    r"|about to|processing|calculating)\b|\bbefore (?:determining|totaling|resolving|proceeding)\b"
    r"|\bwill (?:review|check|verify) the remaining\b", re.IGNORECASE)
_ASKS = re.compile(r"\?|\b(?:please (?:confirm|reply|provide|let me know)|reply yes|confirm)\b", re.IGNORECASE)


def unwrap(text: str) -> tuple[str, bool]:
    """A message's text, and whether it came wrapped as a JSON object with a ``message`` field."""
    stripped = str(text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            value = json.loads(stripped)
        except ValueError:
            return stripped, False
        if isinstance(value, dict) and isinstance(value.get("message"), str):
            return value["message"], True
    return stripped, False


def progress_stop(text: str) -> bool:
    """The agent ended its turn announcing work it hadn't done, and asked the customer nothing."""
    body, _ = unwrap(text)
    return bool(_PROGRESS.search(body)) and not _ASKS.search(body)


def behaviour(messages: list[dict[str, Any]]) -> dict[str, Any]:
    agent_texts = [m.get("content") or "" for m in messages if m.get("role") == "assistant" and not m.get("tool_calls")]
    calls = [call for m in messages if m.get("role") == "assistant" for call in m.get("tool_calls") or []]
    tool_errors = sum(1 for m in messages if m.get("role") == "tool" and (
        m.get("error") or str(m.get("content") or "").lower().startswith("error")))
    return {"agent_messages": len(agent_texts), "json_wrapped": sum(unwrap(text)[1] for text in agent_texts[1:]),
            "progress_stops": sum(progress_stop(text) for text in agent_texts[1:]), "tool_calls": len(calls),
            "tool_errors": tool_errors,
            "transfers": sum(1 for call in calls if call.get("name") == "transfer_to_human_agents")}


def bucket(simulation: dict[str, Any]) -> str:
    """The failure's bucket ('' for a success)."""
    info = simulation.get("reward_info") or {}
    reason = str(simulation.get("termination_reason") or "")
    if float(info.get("reward") or 0.0) >= 1.0 - 1e-6:
        return ""
    if reason in {"agent_error", "infrastructure_error"} or "error" in reason and reason != "too_many_errors":
        return "harness"
    if reason in {"max_steps", "too_many_errors"}:
        return "ran out"
    parts = info.get("reward_breakdown") or {}
    if float(parts.get("DB", 1.0)) < 1.0:
        missed = [check for check in info.get("action_checks") or []
                  if check.get("tool_type") == WRITE and not check.get("action_match")]
        return "wrong write" if missed else "extra write"
    if float(parts.get("COMMUNICATE", 1.0)) < 1.0:
        return "not communicated"
    if float(parts.get("NL_ASSERTION", 1.0)) < 1.0:
        return "judged"
    return "other"


def detail(simulation: dict[str, Any]) -> dict[str, Any]:
    info = simulation.get("reward_info") or {}
    missed = [f"{check['action']['name']}({json.dumps(check['action'].get('arguments'), sort_keys=True)[:160]})"
              for check in info.get("action_checks") or [] if not check.get("action_match")]
    unsaid = [str(check.get("info")) for check in info.get("communicate_checks") or [] if not check.get("met")]
    return {"task_id": str(simulation.get("task_id")), "trial": simulation.get("trial"),
            "reward": info.get("reward"), "termination": simulation.get("termination_reason"),
            "bucket": bucket(simulation), "missed_actions": missed, "not_communicated": unsaid,
            **behaviour(simulation.get("messages") or [])}


def taxonomy(simulations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [detail(item) for item in simulations]
    failures = [row for row in rows if row["bucket"]]
    total = lambda key: sum(int(row[key]) for row in rows)  # noqa: E731
    return {"conversations": len(rows), "failures": len(failures),
            "buckets": dict(Counter(row["bucket"] for row in failures).most_common()),
            "behaviour": {key: total(key) for key in ("agent_messages", "json_wrapped", "progress_stops", "tool_calls",
                                                       "tool_errors", "transfers")},
            "progress_stops_in_failures": sum(row["progress_stops"] for row in failures),
            "progress_stops_in_successes": sum(row["progress_stops"] for row in rows if not row["bucket"]),
            "rows": failures}


def render(found: dict[str, Any], *, title: str) -> str:
    lines = [f"# {title}", "", f"{found['failures']} failure(s) in {found['conversations']} conversation(s).", "",
             "| bucket | count |", "|---|---|"]
    lines += [f"| {name} | {count} |" for name, count in found["buckets"].items()]
    behaviour_totals = found["behaviour"]
    lines += ["", "Behaviour over all conversations: " + ", ".join(f"{key} {value}"
                                                                  for key, value in behaviour_totals.items()),
              f"Progress-line stops: {found['progress_stops_in_failures']} in failures, "
              f"{found['progress_stops_in_successes']} in successes.", "",
              "| task | bucket | termination | missed actions | not communicated | progress stops |",
              "|---|---|---|---|---|---|"]
    for row in found["rows"]:
        lines.append(f"| {row['task_id']} | {row['bucket']} | {row['termination']} | "
                     f"{'; '.join(row['missed_actions']) or '-'} | {', '.join(row['not_communicated']) or '-'} | "
                     f"{row['progress_stops']} |")
    return "\n".join(lines) + "\n"
