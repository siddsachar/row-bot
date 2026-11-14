"""Scoring: the published judge over Row-Bot's answers, a second judge on a subset, and the scores.

Each answer is judged against every key the benchmark has (LoCoMo: ``original`` and the
audit's ``audited``; LongMemEval: ``original``). A judge reply with no verdict counts as
wrong and is counted as unparsed. A question whose turn did not finish (budget, error)
counts as wrong and is counted apart, so a number never hides a broken run.

Judge sensitivity: a second judge model judges a fixed subset (the questions whose id
hashes lowest, so the subset is the same in every run), and the report gives both
judges' accuracy on it, their raw agreement and Cohen's kappa.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Sequence

from .datasets import Question
from .protocols import JudgeProtocol

Messages = list[tuple[str, str]]
JudgeCall = Callable[[Messages], str]
_REPLY_CHARS = 400


@dataclass
class Answer:
    question_id: str
    hypothesis: str
    status: str = "done"
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    task_id: str = ""


@dataclass
class Judgment:
    question_id: str
    key: str
    judge: str
    run: int
    correct: bool | None
    reply: str = ""
    category: str = ""
    answered: bool = True


def gold_for(question: Question, key: str) -> str:
    if key == "audited":
        return question.audited_answer if question.audited_answer is not None else question.answer
    return question.answer


def keys_for(questions: Iterable[Question]) -> tuple[str, ...]:
    return ("original", "audited") if any(item.audited_answer is not None for item in questions) else ("original",)


def judge_answers(answers: Sequence[Answer], questions: dict[str, Question], protocol: JudgeProtocol,
                  call: JudgeCall, *, judge: str, keys: Sequence[str] = ("original",), runs: int = 1,
                  on_judgment: Callable[[Judgment], None] = lambda item: None) -> list[Judgment]:
    """Judge every answer on every key. An answer the same for two keys is judged once per run
    and the verdict reused (the gold text is identical, so the judge's input is too)."""
    found: list[Judgment] = []
    for answer in answers:
        question = questions[answer.question_id]
        answered = answer.status == "done"
        cache: dict[tuple[str, int], tuple[bool | None, str]] = {}
        for key in keys:
            gold = gold_for(question, key)
            for run in range(runs):
                if not answered:
                    verdict, reply = False, ""
                elif (gold, run) in cache:
                    verdict, reply = cache[(gold, run)]
                else:
                    reply = call(protocol.messages(question=question.question, gold=gold,
                                                   response=answer.hypothesis, category=question.category,
                                                   abstention=question.abstention))
                    verdict = protocol.verdict(reply)
                    cache[(gold, run)] = (verdict, reply)
                item = Judgment(answer.question_id, key, judge, run, verdict, str(reply)[:_REPLY_CHARS],
                                question.category, answered)
                found.append(item)
                on_judgment(item)
    return found


def subset(question_ids: Iterable[str], size: int) -> list[str]:
    """A fixed subset: the *size* ids whose SHA-256 is lowest."""
    return sorted(question_ids, key=lambda item: hashlib.sha256(item.encode("utf-8")).hexdigest())[:max(0, size)]


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def accuracy(judgments: Iterable[Judgment], *, key: str, judge: str) -> dict[str, Any]:
    """Accuracy overall and per category; runs are averaged per question first."""
    per_question: dict[str, list[float]] = defaultdict(list)
    category: dict[str, str] = {}
    unparsed = unanswered = 0
    for item in judgments:
        if item.key != key or item.judge != judge:
            continue
        per_question[item.question_id].append(1.0 if item.correct else 0.0)
        category[item.question_id] = item.category
        unparsed += item.correct is None and item.answered
        unanswered += not item.answered
    scores = {question_id: sum(values) / len(values) for question_id, values in per_question.items()}
    by_category: dict[str, list[float]] = defaultdict(list)
    for question_id, score in scores.items():
        by_category[category[question_id]].append(score)
    return {
        "key": key, "judge": judge, "questions": len(scores), "accuracy": _mean(list(scores.values())),
        "by_category": {name: {"questions": len(values), "accuracy": _mean(values)}
                        for name, values in sorted(by_category.items())},
        "unparsed_verdicts": unparsed, "unanswered_judgments": unanswered,
    }


def sensitivity(judgments: Iterable[Judgment], *, key: str, primary: str, secondary: str) -> dict[str, Any]:
    """Both judges on the questions the second one judged: accuracy, agreement and Cohen's kappa."""
    first: dict[str, bool] = {}
    second: dict[str, bool] = {}
    for item in judgments:
        if item.key != key or item.run != 0:
            continue
        if item.judge == primary:
            first[item.question_id] = bool(item.correct)
        elif item.judge == secondary:
            second[item.question_id] = bool(item.correct)
    common = sorted(set(first) & set(second))
    if not common:
        return {"key": key, "questions": 0}
    a = [first[item] for item in common]
    b = [second[item] for item in common]
    agree = sum(x == y for x, y in zip(a, b)) / len(common)
    p_a, p_b = sum(a) / len(a), sum(b) / len(b)
    chance = p_a * p_b + (1 - p_a) * (1 - p_b)
    kappa = None if chance >= 1 else round((agree - chance) / (1 - chance), 4)
    return {"key": key, "questions": len(common), "primary": primary, "secondary": secondary,
            "primary_accuracy": round(p_a, 4), "secondary_accuracy": round(p_b, 4),
            "agreement": round(agree, 4), "cohen_kappa": kappa}


def to_dicts(items: Iterable[Any]) -> list[dict[str, Any]]:
    return [asdict(item) for item in items]
