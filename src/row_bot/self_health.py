"""What needs attention in Row-Bot, as Monitor and Needs you show it, for Row-Bot itself (self system).

Row-Bot reads the same problems, each with its one fix, as the person sees on Home: Monitor's kept checks
and the attention list. It never reads raw logs for this. ``fix`` runs the checks again when the person asks
(they contact providers and accounts, as Monitor's Run diagnosis does) and hands every other fix to the
person with the link to it: restarting a channel, signing in again, choosing a folder. Channels run on their
own event loops and sign-ins are the person's, so Row-Bot doesn't do those itself.
"""

from __future__ import annotations

from dataclasses import dataclass

ALL = "all"


@dataclass(frozen=True)
class Problem:
    id: str
    title: str
    detail: str
    level: str  # "error" or "warn"
    fix: dict | None


def problems() -> list[Problem]:
    """What needs attention now, errors first: the attention list's problems, then Monitor's warnings."""
    from row_bot.application.client_diagnosis import ATTENTION_ELSEWHERE, read_system_health
    from row_bot.application.client_monitor import read_attention

    found: dict[str, Problem] = {}
    for item in read_attention(include_update=False)["problems"]:
        found[str(item["id"])] = Problem(str(item["id"]), str(item["title"]), str(item["detail"]), "error",
                                         item.get("fix"))
    for check in read_system_health()["checks"]:
        problem_id = f"health:{check['id']}"
        # The attention list reports these areas from their own readers.
        if check["status"] != "warn" or problem_id in found or check["id"].startswith(ATTENTION_ELSEWHERE):
            continue
        found[problem_id] = Problem(problem_id, f"{check['name']}: {check['detail']}", str(check["detail"]), "warn",
                                    check.get("fix"))
    return sorted(found.values(), key=lambda problem: problem.level != "error")


def fix_words(fix: dict | None) -> str:
    """The fix as its button says it, with the link to where it is done."""
    if not fix:
        return ""
    name = str(fix.get("name") or "it")
    verb = {
        "restart_channel": f"Restart {name}",
        "reconnect_account": f"Reconnect {name}",
        "choose_model": "Choose a model",
        "choose_folder": "Choose a folder",
        "check_again": f"Check {name} again",
    }.get(str(fix.get("kind")), f"Open {name}")
    href = str(fix.get("href") or "")
    return f"[{verb}]({href})" if href else verb


def describe() -> str:
    """row_bot_status 'health'."""
    found = problems()
    if not found:
        return ("**What needs attention**\nNothing: Monitor's checks and Needs you are clear. To check again now "
                "(it contacts the person's providers and accounts), call row_bot_fix with problem 'all'.")
    lines = [f"**What needs attention** ({len(found)}, as Monitor and Needs you show it)"]
    for problem in found:
        text = problem.title
        if problem.detail and problem.detail not in text:
            text += f": {problem.detail}"
        fix = fix_words(problem.fix)
        lines.append(f"- [{problem.level}] {text.rstrip('.')}." + (f" Fix: {fix}." if fix else "")
                     + f" (problem '{problem.id}')")
    lines.append("row_bot_fix with a problem's id runs its fix or says what the person does; 'all' checks again.")
    return "\n".join(lines)


def fix(problem_id: str) -> str:
    """row_bot_fix: check again, or hand the person their fix with its link."""
    wanted = (problem_id or "").strip()
    before = {problem.id: problem for problem in problems()}
    problem = before.get(wanted)
    if wanted != ALL and problem is None:
        return f"That problem isn't there any more.\n{describe()}"
    if wanted == ALL or (problem.fix or {}).get("kind") == "check_again":
        from row_bot.application.client_diagnosis import run_system_diagnosis

        run_system_diagnosis()
        if wanted == ALL:
            return "Checked everything again.\n" + describe()
        after = {item.id: item for item in problems()}
        if wanted not in after:
            return f"Checked again: {problem.title.split(':')[0]} is fine now."
        return f"Checked again: {after[wanted].title}. {fix_words(after[wanted].fix) or ''}".rstrip()
    kind = (problem.fix or {}).get("kind")
    if kind == "choose_model":
        return ("The main model needs choosing. Change it with row_bot_update_setting, setting 'default_model' "
                "(row_bot_status 'model' lists the pinned choices), or the person opens "
                "[Main model](/settings/models#default-model).")
    fix_text = fix_words(problem.fix) or "Open Monitor"
    return (f"{problem.title}. This one is the person's to do: {fix_text}. Tell them in a sentence, with that link; "
            "Row-Bot doesn't restart channels, sign in or choose folders for them.")
