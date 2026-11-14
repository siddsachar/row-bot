"""The published judges, read from the benchmarks' own files, so a run uses their exact words.

No judge prompt is copied into this repository. Each protocol is loaded at run time from
the downloaded source file, parsed as data (YAML, or Python's ``ast``: never imported or
run), and the file's SHA-256 goes into the run's config:

- **LoCoMo**: the ``llm_judge`` prompts of ``evaluation/config/prompts.yaml`` in
  dial481/locomo-audit (from EverMemOS, Apache-2.0; the CORRECT/WRONG judge of the published
  LoCoMo numbers). Published judge GPT-4o-mini, temperature 0.
- **LongMemEval** (v1, the S split): the five templates of ``get_anscheck_prompt`` in
  ``src/evaluation/evaluate_qa.py``. Published judge ``gpt-4o-2024-08-06``, temperature 0,
  ``max_tokens`` 10, correct when ``"yes"`` is in the reply.
- **BEAM**: ``unified_llm_judge_base_prompt`` in ``src/prompts.py`` (each rubric item judged on
  its own, 1 / 0.5 / 0, the question's score their mean) and, for event ordering, the YES/NO
  equivalence prompts of ``llm_equivalence`` in ``src/evaluation/compute_metrics.py`` (each
  answer line matched to a rubric event, then Kendall's tau-b). Pass both files, comma
  separated. Published judge ``gpt-4.1-mini``, temperature 0. A category's score is the
  rubric mean, except event ordering's, which is the normalised tau-b (``report_results.py``);
  the overall score is the mean of the ten categories.
- **LongMemEval-V2**: ``evaluation/qa_eval_metrics.py``: the abstention and gotchas judges'
  prompts (published judge ``gpt-5.2``, medium reasoning) and the names of the deterministic
  scorers, which ``v2_metrics`` ports (they are a few lines of matching each; they are not run
  from the download). The final answer is the last ``\\boxed{...}``; ``UNKNOWN`` scores 0.

Which judge *model* runs is the operator's choice (``--judge``); a run whose judge is not the
published one is labelled so in the report.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from . import v2_metrics

LOCOMO_JUDGE = "openai:gpt-4o-mini"
LONGMEMEVAL_JUDGE = "openai:gpt-4o-2024-08-06"
BEAM_JUDGE = "openai:gpt-4.1-mini"
LONGMEMEVAL_V2_JUDGE = "openai:gpt-5.2 (reasoning medium)"
LONGMEMEVAL_TYPES = ("single-session-user", "single-session-assistant", "multi-session", "temporal-reasoning",
                     "knowledge-update", "single-session-preference")
ABSTENTION = "abstention"
EVENT_ORDERING = "event_ordering"

Messages = list[tuple[str, str]]
JudgeCall = Callable[[Messages], str]


class ProtocolError(ValueError):
    """A protocol file is missing or is not the shape this loader knows."""


@dataclass
class Verdict:
    """One answer's score in [0, 1] (None: the judge's reply carried no verdict), the judge's replies
    and the protocol's own details (rubric items, alignment, the parsed final answer)."""

    score: float | None
    replies: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgeProtocol:
    name: str
    source: str
    sha256: str
    published_judge: str
    temperature: float | None = 0.0
    max_tokens: int | None = None
    system: str = ""
    templates: dict[str, str] = field(default_factory=dict)
    #: "questions": the mean over questions; "categories": the mean of the categories' means (BEAM).
    headline: str = "questions"

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "source": self.source, "sha256": self.sha256,
                "published_judge": self.published_judge, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "headline": self.headline}

    # -- the judges --------------------------------------------------------

    def messages(self, *, question: str, gold: str, response: str, category: str = "",
                 abstention: bool = False) -> Messages:
        """LoCoMo's and LongMemEval's one judge call, filled exactly as the source fills it."""
        if self.name == "locomo":
            user = self.templates["user"].format(question=question, golden_answer=gold, generated_answer=response)
            return [("system", self.system), ("user", user)]
        key = ABSTENTION if abstention else category
        if key not in self.templates:
            raise ProtocolError(f"LongMemEval has no judge template for question type {category!r}")
        return [("user", self.templates[key].format(question, gold, response))]

    def verdict(self, reply: str) -> bool | None:
        """LoCoMo's / LongMemEval's verdict, as the source decides it."""
        text = str(reply or "")
        if self.name == "longmemeval":
            return "yes" in text.strip().lower()
        match = re.search(r"\{[^{}]*\"label\"\s*:\s*\"(CORRECT|WRONG)\"[^{}]*\}", text, re.IGNORECASE)
        if match:
            return match.group(1).upper() == "CORRECT"
        labels = re.findall(r"\b(CORRECT|WRONG)\b", text.upper())
        return labels[-1] == "CORRECT" if labels else None

    def judge(self, question: Any, response: str, gold: str, call: JudgeCall) -> Verdict:
        """Score one answer (*question* is a ``datasets.Question``)."""
        if self.name in {"locomo", "longmemeval"}:
            reply = call(self.messages(question=question.question, gold=gold, response=response,
                                       category=question.category, abstention=question.abstention))
            verdict = self.verdict(reply)
            return Verdict(None if verdict is None else float(verdict), [reply])
        if self.name == "beam":
            return self._beam(question, response, call)
        if self.name == "longmemeval-v2":
            return self._v2(question, response, gold, call)
        raise ProtocolError(f"no judge for {self.name!r}")

    def _beam(self, question: Any, response: str, call: JudgeCall) -> Verdict:
        replies: list[str] = []
        items: list[float | None] = []
        for item in question.rubric:
            reply = call([("user", self.templates["judge"].replace("<rubric_item>", item)
                           .replace("<llm_response>", response))])
            replies.append(reply)
            items.append(_beam_score(reply))
        if not items:
            return Verdict(None, replies, {"reason": "no rubric"})
        rubric_mean = sum(value or 0.0 for value in items) / len(items)
        detail: dict[str, Any] = {"rubric_scores": items, "llm_judge_score": rubric_mean,
                                  "unparsed_items": sum(value is None for value in items)}
        if question.category != EVENT_ORDERING:
            return Verdict(rubric_mean, replies, detail)

        def same(first: str, second: str) -> bool:
            user = self.templates["equivalence_user"].format(first_paragraph=first, second_paragraph=second)
            reply = call([("system", self.templates["equivalence_system"]), ("user", user)])
            replies.append(reply)
            return "yes" in reply.lower()

        ordering = event_ordering_score(list(question.rubric), response.split("\n"), same)
        detail.update(ordering)
        return Verdict(ordering["tau_norm"], replies, detail)

    def _v2(self, question: Any, response: str, gold: str, call: JudgeCall) -> Verdict:
        parsed = v2_metrics.extract_boxed_answer(response)
        unknown = v2_metrics.is_unknown(parsed)
        name, options = v2_metrics.parse_spec(question.eval_function)
        detail: dict[str, Any] = {"eval_function": name, "parsed": parsed[:300], "unknown": unknown}
        replies: list[str] = []
        if name in v2_metrics.LLM_EVAL_FUNCTIONS:
            prediction, final = response.strip(), parsed.strip() or response.strip()
            if options.get("require_non_empty", True) and (not prediction or not gold.strip() or not final):
                return Verdict(0.0, replies, detail)
            key = "abstention" if name == "llm_abstention_checker" else "gotchas"
            user = self.templates[f"{key}_user"].format(question_text=question.question.strip(),
                                                        reference_answer=gold.strip(),
                                                        model_full_response=prediction, model_final_answer=final)
            reply = call([("system", self.templates[f"{key}_system"]), ("user", user)])
            replies.append(reply)
            label = v2_metrics.parse_binary_judgement(reply)
            if label is None:
                return Verdict(None, replies, detail)
            passed = label == 1
        else:
            passed = v2_metrics.score(name, options, parsed, gold)
        return Verdict(0.0 if unknown else float(passed), replies, detail)


def _beam_score(reply: str) -> float | None:
    """BEAM's judge answers JSON with "score" (1.0, 0.5 or 0.0); its own parser finds the first object."""
    text = str(reply or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    candidates += [text] + re.findall(r"\{.*?\}", text, re.DOTALL)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and "score" in value:
            try:
                return float(value["score"])
            except (TypeError, ValueError):
                return None
    match = re.search(r'"score"\s*:\s*([01](?:\.\d+)?)', text)
    return float(match.group(1)) if match else None


def event_ordering_score(reference: list[str], system: list[str], same: Callable[[str, str], bool]) -> dict[str, Any]:
    """BEAM's event-ordering score (``compute_metrics.event_ordering_score`` with ``align_type="llm"``):
    each answer line takes the first unused rubric event the judge calls the same; precision, recall and
    F1 over the aligned sets; Kendall's tau-b between the two orders (unmatched items share the last
    rank), normalised to [0, 1]."""
    from scipy.stats import kendalltau

    used: set[int] = set()
    aligned: list[str] = []
    for line in system:
        match = next((index for index, event in enumerate(reference) if index not in used and same(event, line)), None)
        if match is None:
            aligned.append(line)
        else:
            aligned.append(reference[match])
            used.add(match)
    true_positive = len(set(reference) & set(aligned))
    false_positive = len([item for item in aligned if item not in reference])
    false_negative = len([item for item in reference if item not in aligned])
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    union = list(dict.fromkeys(reference + aligned))
    tie = len(union) + 1

    def ranks(sequence: list[str]) -> list[int]:
        positions = {item: index + 1 for index, item in enumerate(sequence)}
        return [positions.get(item, tie) for item in union]

    tau, _p = kendalltau(ranks(reference), ranks(aligned), variant="b", method="auto")
    # BEAM's own code turns a NaN tau (every rank tied) into NaN; it counts as 0 here, and the trace says so.
    tau_norm = float((tau + 1) / 2) if tau is not None and tau == tau else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tau_norm": tau_norm, "final_score": tau_norm * f1,
            "tau_was_nan": not (tau is not None and tau == tau)}


# ── Loading ────────────────────────────────────────────────────────────────


def _sha256(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise ProtocolError(f"{path}: not readable Python ({type(exc).__name__})") from exc


def _template(node: ast.AST) -> str:
    """A string or f-string expression as a ``str.format`` template: literal braces doubled, each
    ``{name}`` kept. Anything else is refused (the loader never evaluates code)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.replace("{", "{{").replace("}", "}}")
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value).replace("{", "{{").replace("}", "}}"))
            elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name) \
                    and value.conversion == -1 and value.format_spec is None:
                parts.append("{" + value.value.id + "}")
            else:
                raise ProtocolError("an f-string part is not a plain name")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _template(node.left) + _template(node.right)
    raise ProtocolError("not a string expression")


def _constant(tree: ast.AST, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name
                                                for target in node.targets):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            if isinstance(value, ast.BinOp):
                return _template(value).replace("{{", "{").replace("}}", "}")
    raise ProtocolError(f"no string constant {name}")


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    found = next((node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name), None)
    if found is None:
        raise ProtocolError(f"no function {name}")
    return found


def load_locomo(prompts_yaml: Path) -> JudgeProtocol:
    try:
        document = yaml.safe_load(Path(prompts_yaml).read_text(encoding="utf-8"))
        judge = document["llm_judge"]
        system, user = str(judge["system_prompt"]).strip(), str(judge["user_prompt"])
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ProtocolError(f"{prompts_yaml}: no llm_judge system_prompt/user_prompt ({type(exc).__name__})") from exc
    for name in ("{question}", "{golden_answer}", "{generated_answer}"):
        if name not in user:
            raise ProtocolError(f"{prompts_yaml}: the judge prompt has no {name}")
    return JudgeProtocol("locomo", str(prompts_yaml), _sha256(prompts_yaml), LOCOMO_JUDGE, 0.0, None, system,
                         {"user": user})


def _branch_keys(test: ast.expr) -> list[str]:
    """``task in [...]`` / ``task == '...'`` -> the question types it names."""
    if isinstance(test, ast.Compare) and len(test.comparators) == 1:
        right = test.comparators[0]
        if isinstance(test.ops[0], ast.Eq) and isinstance(right, ast.Constant):
            return [str(right.value)]
        if isinstance(test.ops[0], ast.In) and isinstance(right, (ast.List, ast.Tuple)):
            return [str(item.value) for item in right.elts if isinstance(item, ast.Constant)]
    return []


def _template_of(body: list[ast.stmt]) -> str | None:
    for statement in body:
        if (isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "template"
                                                      for target in statement.targets)
                and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str)):
            return statement.value.value
    return None


def load_longmemeval(evaluate_qa: Path) -> JudgeProtocol:
    """The templates of ``get_anscheck_prompt``, found by walking its if/elif chain (never run)."""
    function = _function(_parse(evaluate_qa), "get_anscheck_prompt")
    top = next((node for node in function.body if isinstance(node, ast.If)), None)
    if top is None:
        raise ProtocolError(f"{evaluate_qa}: get_anscheck_prompt has no abstention branch")
    templates: dict[str, str] = {}
    abstention = _template_of(top.orelse)
    if abstention:
        templates[ABSTENTION] = abstention
    node: Any = next((item for item in top.body if isinstance(item, ast.If)), None)
    while isinstance(node, ast.If):
        template = _template_of(node.body)
        for key in _branch_keys(node.test):
            if template:
                templates[key] = template
        node = node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
    missing = [key for key in (*LONGMEMEVAL_TYPES, ABSTENTION) if key not in templates]
    if missing:
        raise ProtocolError(f"{evaluate_qa}: no judge template for {missing}")
    for key, template in templates.items():
        if template.count("{}") != 3:
            raise ProtocolError(f"{evaluate_qa}: the {key} template does not take three values")
    return JudgeProtocol("longmemeval", str(evaluate_qa), _sha256(evaluate_qa), LONGMEMEVAL_JUDGE, 0.0, 10, "",
                         templates)


def load_beam(prompts_py: Path, compute_metrics_py: Path) -> JudgeProtocol:
    judge = _constant(_parse(prompts_py), "unified_llm_judge_base_prompt")
    for marker in ("<rubric_item>", "<llm_response>"):
        if marker not in judge:
            raise ProtocolError(f"{prompts_py}: the judge prompt has no {marker}")
    contents = [value for node in ast.walk(_function(_parse(compute_metrics_py), "llm_equivalence"))
                if isinstance(node, ast.Dict)
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and key.value == "content"]
    if len(contents) != 2:
        raise ProtocolError(f"{compute_metrics_py}: llm_equivalence has no system and user message")
    system = _template(contents[0]).replace("{{", "{").replace("}}", "}")
    user = _template(contents[1])
    if "{first_paragraph}" not in user or "{second_paragraph}" not in user:
        raise ProtocolError(f"{compute_metrics_py}: the equivalence prompt does not name both snippets")
    return JudgeProtocol("beam", f"{prompts_py},{compute_metrics_py}", _sha256(prompts_py, compute_metrics_py),
                         BEAM_JUDGE, 0.0, None, "",
                         {"judge": judge, "equivalence_system": system, "equivalence_user": user},
                         headline="categories")


def load_longmemeval_v2(metrics_py: Path) -> JudgeProtocol:
    tree = _parse(metrics_py)
    templates: dict[str, str] = {}
    for key, system_name, builder in (("abstention", "_ABSTENTION_JUDGE_SYSTEM_PROMPT", "_build_abstention_judge_messages"),
                                      ("gotchas", "_GOTCHAS_JUDGE_SYSTEM_PROMPT", "_build_gotchas_judge_messages")):
        templates[f"{key}_system"] = _constant(tree, system_name)
        assigned = [node.value for node in ast.walk(_function(tree, builder)) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "user_prompt" for target in node.targets)]
        if len(assigned) != 1:
            raise ProtocolError(f"{metrics_py}: {builder} has no user_prompt")
        templates[f"{key}_user"] = _template(assigned[0])
        for name in ("question_text", "reference_answer", "model_full_response", "model_final_answer"):
            if "{" + name + "}" not in templates[f"{key}_user"]:
                raise ProtocolError(f"{metrics_py}: the {key} judge prompt has no {name}")
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    missing = sorted(set(v2_metrics.DETERMINISTIC) - defined)
    if missing:
        raise ProtocolError(f"{metrics_py}: the scorers {missing} are not in this version; check the port")
    return JudgeProtocol("longmemeval-v2", str(metrics_py), _sha256(metrics_py), LONGMEMEVAL_V2_JUDGE, None, 4096,
                         "", templates)


def load(benchmark: str, source: str | Path) -> JudgeProtocol:
    from .datasets import BEAM_1M, LOCOMO, LONGMEMEVAL_S, LONGMEMEVAL_V2

    if benchmark == LOCOMO:
        return load_locomo(Path(source))
    if benchmark == LONGMEMEVAL_S:
        return load_longmemeval(Path(source))
    if benchmark == BEAM_1M:
        parts = [Path(item) for item in str(source).split(",") if item]
        if len(parts) != 2:
            raise ProtocolError("BEAM's protocol is two files: src/prompts.py,src/evaluation/compute_metrics.py")
        return load_beam(parts[0], parts[1])
    if benchmark == LONGMEMEVAL_V2:
        return load_longmemeval_v2(Path(source))
    raise ProtocolError(f"no judge protocol for {benchmark!r}")


def protocol_to_json(protocol: JudgeProtocol) -> str:
    return json.dumps({**protocol.describe(), "system": protocol.system, "templates": protocol.templates})


def protocol_from_json(text: str) -> JudgeProtocol:
    data = json.loads(text)
    return JudgeProtocol(data["name"], data["source"], data["sha256"], data["published_judge"],
                         data["temperature"], data["max_tokens"], data["system"], dict(data["templates"]),
                         data.get("headline", "questions"))
