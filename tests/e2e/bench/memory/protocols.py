"""The published judges, read from the benchmarks' own files, so a run uses their exact words.

No judge prompt is copied into this repository. Each protocol is loaded at run time from
the downloaded source file, parsed as data (YAML, or Python's ``ast`` without running
anything), and its SHA-256 goes into the run's config:

- **LoCoMo**: the ``llm_judge`` system and user prompts of ``evaluation/config/prompts.yaml``
  in dial481/locomo-audit (from EverMemOS, Apache-2.0; the same CORRECT/WRONG judge Mem0's
  LoCoMo numbers use). Published setting: GPT-4o-mini, temperature 0, the label as JSON.
- **LongMemEval**: the five templates of ``get_anscheck_prompt`` in
  ``src/evaluation/evaluate_qa.py`` (xiaowu0162/LongMemEval): one per question type, and one
  for abstention. Published setting: ``gpt-4o-2024-08-06``, temperature 0, ``max_tokens``
  10, correct when ``"yes"`` is in the reply.

Which judge *model* runs is the operator's choice (``--judge``); a run whose judge is not
the published one is labelled diagnostic in the report.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

LOCOMO_JUDGE = "openai:gpt-4o-mini"
LONGMEMEVAL_JUDGE = "openai:gpt-4o-2024-08-06"
LONGMEMEVAL_TYPES = ("single-session-user", "single-session-assistant", "multi-session", "temporal-reasoning",
                     "knowledge-update", "single-session-preference")
ABSTENTION = "abstention"


class ProtocolError(ValueError):
    """A protocol file is missing or is not the shape this loader knows."""


@dataclass(frozen=True)
class JudgeProtocol:
    name: str
    source: str
    sha256: str
    published_judge: str
    temperature: float = 0.0
    max_tokens: int | None = None
    system: str = ""
    templates: dict[str, str] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "source": self.source, "sha256": self.sha256,
                "published_judge": self.published_judge, "temperature": self.temperature,
                "max_tokens": self.max_tokens}

    def messages(self, *, question: str, gold: str, response: str, category: str = "",
                 abstention: bool = False) -> list[tuple[str, str]]:
        """The judge's messages as (role, text) pairs, filled exactly as the source fills them."""
        if self.name == "locomo":
            user = self.templates["user"].format(question=question, golden_answer=gold, generated_answer=response)
            return [("system", self.system), ("user", user)]
        key = ABSTENTION if abstention else category
        if key not in self.templates:
            raise ProtocolError(f"LongMemEval has no judge template for question type {category!r}")
        return [("user", self.templates[key].format(question, gold, response))]

    def verdict(self, reply: str) -> bool | None:
        """True / False as the source decides it; None when a LoCoMo reply carries no label."""
        text = str(reply or "")
        if self.name == "longmemeval":
            return "yes" in text.strip().lower()
        match = re.search(r"\{[^{}]*\"label\"\s*:\s*\"(CORRECT|WRONG)\"[^{}]*\}", text, re.IGNORECASE)
        if match:
            return match.group(1).upper() == "CORRECT"
        labels = re.findall(r"\b(CORRECT|WRONG)\b", text.upper())
        return labels[-1] == "CORRECT" if labels else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
    try:
        tree = ast.parse(Path(evaluate_qa).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise ProtocolError(f"{evaluate_qa}: not readable Python ({type(exc).__name__})") from exc
    function = next((node for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef) and node.name == "get_anscheck_prompt"), None)
    if function is None:
        raise ProtocolError(f"{evaluate_qa}: no get_anscheck_prompt")
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


def load(benchmark: str, source: Path) -> JudgeProtocol:
    from .datasets import LOCOMO, LONGMEMEVAL_S

    if benchmark == LOCOMO:
        return load_locomo(source)
    if benchmark == LONGMEMEVAL_S:
        return load_longmemeval(source)
    raise ProtocolError(f"no judge protocol for {benchmark!r}")


def protocol_to_json(protocol: JudgeProtocol) -> str:
    return json.dumps({**protocol.describe(), "system": protocol.system, "templates": protocol.templates})


def protocol_from_json(text: str) -> JudgeProtocol:
    data = json.loads(text)
    return JudgeProtocol(data["name"], data["source"], data["sha256"], data["published_judge"],
                         float(data["temperature"]), data["max_tokens"], data["system"], dict(data["templates"]))
