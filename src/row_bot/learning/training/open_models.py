"""Any open model, on this computer and on Hugging Face (C1 Part 4, O138).

The picker has three levels: **Recommended** (the certified models, ``catalog.CERTIFIED``), **Other models** (any
model the person finds on Hugging Face) and, for each of those, what Row-Bot checked. A model found on Hugging Face
is offered for use only after an **automatic check on this computer** passes (:class:`Checker`):

1. **fits**: a short trial on the card (four optimizer steps of 16K-token rows at the tier's memory cap, the same
   settings training uses) measures its peak memory; a model that doesn't fit here can still train on Hugging Face
   if a single card there holds it, and the trial then runs at a short length for the next checks;
2. **steps**: those steps have a finite loss and finite, non-zero gradients;
3. **export**: the trial's adapter merges and converts to a GGUF that Row-Bot's own server runs;
4. **tools**: served on this computer, it calls Row-Bot's tools on a small sample the person can read
   (:data:`TOOL_SAMPLE`).

Each model says what was checked. Architectures Row-Bot's training doesn't adapt are said plainly and never
attempted (:func:`describe`). Licences: permissive ones (Apache, MIT) just show; any other shows its key terms and
needs the person's OK once (:data:`TERMS`); a gated model links to Hugging Face to request access (Row-Bot downloads
only what anyone can download, never with a token, D13).

Reading Hugging Face is free and needs no sign-in: search and a model's card go through Row-Bot's one safe fetch
path to ``huggingface.co`` only. Records live in ``learning_models/checked/`` (never the model's files, which the
download keeps with the other starting models).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from row_bot.learning.training import catalog, weights

logger = logging.getLogger(__name__)

HUB = "https://huggingface.co"
HOSTS = ("huggingface.co",)
SEARCH_LIMIT = 20
API_BYTES = 4_000_000
SMALL_FILE_BYTES = 40_000_000
FORMAT = 1
FOLDER = "checked"
ID_PREFIX = "hf:"
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")

# Architectures whose layers Row-Bot's training adapts (``learning_engine.trainer.TARGETS``) and whose weights
# llama.cpp's converter and Row-Bot's own server run. Anything else is said plainly, never attempted.
SUPPORTED: dict[str, str] = {
    "Qwen3_5ForConditionalGeneration": "Qwen", "Qwen3_5ForCausalLM": "Qwen", "Qwen3ForCausalLM": "Qwen",
    "Qwen2ForCausalLM": "Qwen", "LlamaForCausalLM": "Llama", "MistralForCausalLM": "Mistral",
    "Gemma3ForConditionalGeneration": "Gemma", "Gemma3ForCausalLM": "Gemma", "Gemma2ForCausalLM": "Gemma",
}
# Kinds Row-Bot knows it can't train yet, and why (in words for the person).
MIXTURE = ("Moe", "Mixtral", "Jamba", "Dbrx", "Deepseek")
FUSED = ("Phi3", "Phi4", "ChatGLM", "Glm4")

# Key terms of the licences most open models use; every other licence shows its own text on Hugging Face.
TERMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "llama": ("Llama Community License", (
        "You may use and change it, also commercially, unless your products have over 700 million monthly users.",
        "A model you share that is based on it must say “Built with Llama” and keep Meta’s licence with it.",
        "Meta’s acceptable use policy applies.")),
    "gemma": ("Gemma Terms of Use", (
        "You may use and change it, also commercially.",
        "Google’s prohibited use policy applies, and a model you share that is based on it carries these terms.")),
    "non_commercial": ("a non-commercial licence", (
        "For personal and research use only: not for commercial use.",)),
    "other": ("its own licence", (
        "Read the licence on Hugging Face before you agree: Row-Bot can’t summarize it.",)),
}

# The tool calls a checked model must get right, shown to the person as they ran.
TOOL_SAMPLE: tuple[dict[str, Any], ...] = (
    {"id": "multiply", "prompt": "Use the calculator to work out 1234 * 5678, then reply with just the result.",
     "tools": ("calculator",), "answer": "7006652"},
    {"id": "divide", "prompt": "Use the calculator to work out 98765 / 5, then reply with just the result.",
     "tools": ("calculator",), "answer": "19753"},
    {"id": "sum", "prompt": "Use the calculator to add 4821, 3907 and 1288, then reply with just the total.",
     "tools": ("calculator",), "answer": "10016"},
)
TOOL_PASSES = 2  # of the sample's three

# Files a starting model needs (its weights, configuration, tokenizer, chat template and licence).
_KEEP = re.compile(r"(?i)(\.safetensors|\.json|tokenizer\.model|merges\.txt|vocab\.txt|\.jinja|\.tiktoken)\Z|"
                   r"\A(LICENSE|LICENCE|NOTICE|USE_POLICY)(\.[a-z]+)?\Z")

CHECK_ITEMS = ("fits", "steps", "export", "tools")
# While a check downloads, clients hear of its progress at most this often.
ANNOUNCE_SECONDS = 3.0
STATES = ("not_checked", "downloading", "checking", "passed", "failed", "stopped")


class HubError(RuntimeError):
    """Hugging Face couldn't be read (``unreachable``), the model isn't there (``not_found``) or its card can't
    be used (``unreadable``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Reader = Callable[[str, int], bytes]


def safe_reader(url: str, max_bytes: int) -> bytes:
    """Row-Bot's one safe fetch path, to ``huggingface.co`` only (no token, no cookie)."""

    from row_bot.integrations import safe

    try:
        return safe.fetch(url, hosts=HOSTS, max_bytes=max_bytes, timeout=20, redirects=3,
                          headers={"Accept": "application/json", "User-Agent": "Row-Bot"},
                          refused="learning_model_source_refused", too_large="learning_model_card_too_large")
    except Exception as error:  # noqa: BLE001 - every failure is "unreachable" or "not found" to the person
        status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
        raise HubError("not_found" if status in (401, 404) else "unreachable") from error


def _json(reader: Reader, url: str) -> Any:
    try:
        return json.loads(reader(url, API_BYTES).decode("utf-8"))
    except HubError:
        raise
    except (ValueError, UnicodeDecodeError) as error:
        raise HubError("unreadable") from error


# ── What a model is ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OpenModel:
    """A model on Hugging Face as Row-Bot reads its card: what it is, its licence, and whether it can be tried."""

    repo: str
    name: str
    revision: str | None
    architecture: str | None
    family: str | None
    params_b: float | None
    licence: str
    licence_id: str
    # permissive · terms (needs the person's OK once) · gated (request access on Hugging Face)
    licence_kind: str
    licence_url: str
    terms: tuple[str, ...]
    gated: bool
    downloads: int
    # None when Row-Bot can try it; else why not, in words.
    unsupported: str | None
    download_bytes: int = 0

    @property
    def id(self) -> str:
        return model_id(self.repo)


def model_id(repo: str) -> str:
    return f"{ID_PREFIX}{repo}"


def repo_of(model_id_: str) -> str | None:
    repo = str(model_id_ or "")
    repo = repo[len(ID_PREFIX):] if repo.startswith(ID_PREFIX) else ""
    return repo if REPO.fullmatch(repo) else None


def _licence(card: Mapping[str, Any], tags: Iterable[str], repo: str) -> tuple[str, str, str, tuple[str, ...]]:
    """(name, id, url, key terms) from the model card; id is the Hub's licence tag (``apache-2.0``, ``llama3.1``)."""

    licence_id = str(card.get("license") or next((tag[8:] for tag in tags if str(tag).startswith("license:")), "")
                     or "unknown").lower()[:64]
    url = str(card.get("license_link") or f"{HUB}/{repo}")
    if not url.startswith(HUB + "/"):
        url = f"{HUB}/{repo}"
    if licence_id in catalog.PERMISSIVE:
        names = {"apache-2.0": "Apache 2.0", "mit": "MIT", "bsd-3-clause": "BSD 3-Clause", "bsd-2-clause": "BSD 2-Clause"}
        return names.get(licence_id, licence_id), licence_id, url, ()
    if licence_id.startswith("llama"):
        name, terms = TERMS["llama"]
    elif licence_id.startswith("gemma"):
        name, terms = TERMS["gemma"]
    elif "-nc" in licence_id or "non-commercial" in licence_id or "research" in licence_id:
        name, terms = TERMS["non_commercial"]
    else:
        name, terms = TERMS["other"]
        name = str(card.get("license_name") or name)[:80]
    return name, licence_id, url, terms


def _unsupported(info: Mapping[str, Any], name: str, architecture: str | None) -> str | None:
    pipeline = str(info.get("pipeline_tag") or "")
    library = str(info.get("library_name") or "")
    if library == "gguf" or (not architecture and library in ("", "gguf")):
        return f"{name} has only GGUF files. Row-Bot trains from a model’s original weights."
    if pipeline and pipeline not in ("text-generation", "image-text-to-text"):
        return f"{name} isn’t a chat model, so Row-Bot can’t learn from your chats with it."
    if architecture in SUPPORTED:
        return None
    kind = architecture or "this kind of"
    if any(mark in kind for mark in MIXTURE):
        return f"{name} is a mixture-of-experts model. Row-Bot’s training doesn’t adapt those yet."
    if any(kind.startswith(mark) for mark in FUSED):
        return f"{name} packs its attention layers in a way Row-Bot’s training doesn’t adapt yet."
    family = re.sub(r"(For[A-Z].*|Model)\Z", "", kind) or kind
    return f"Row-Bot can’t train {family} models yet."


def describe(info: Mapping[str, Any]) -> OpenModel:
    """A model from a Hub search result or its card (``/api/models/<repo>``)."""

    repo = str(info.get("id") or info.get("modelId") or "")
    if not REPO.fullmatch(repo):
        raise HubError("unreadable")
    name = repo.split("/", 1)[1]
    config = info.get("config") if isinstance(info.get("config"), Mapping) else {}
    architectures = config.get("architectures") if isinstance(config, Mapping) else None
    architecture = str(architectures[0])[:96] if isinstance(architectures, list) and architectures else None
    tags = [str(tag) for tag in info.get("tags") or () if isinstance(tag, str)]
    if architecture is None:  # a search result has no config: its tags name the kind
        kinds = {"qwen3_5": "Qwen3_5ForConditionalGeneration", "qwen3": "Qwen3ForCausalLM", "qwen2": "Qwen2ForCausalLM",
                 "llama": "LlamaForCausalLM", "mistral": "MistralForCausalLM", "gemma3": "Gemma3ForConditionalGeneration",
                 "gemma3_text": "Gemma3ForCausalLM", "gemma2": "Gemma2ForCausalLM", "qwen3_moe": "Qwen3MoeForCausalLM",
                 "mixtral": "MixtralForCausalLM", "phi3": "Phi3ForCausalLM"}
        architecture = next((kinds[tag] for tag in tags if tag in kinds), None)
    card = info.get("cardData") if isinstance(info.get("cardData"), Mapping) else {}
    licence, licence_id, url, terms = _licence(card, tags, repo)
    gated = bool(info.get("gated")) and str(info.get("gated")).lower() != "false"
    params = (info.get("safetensors") or {}).get("total") if isinstance(info.get("safetensors"), Mapping) else None
    params_b = round(float(params) / 1e9, 1) if isinstance(params, (int, float)) and params > 0 else None
    kind = "gated" if gated else ("permissive" if licence_id in catalog.PERMISSIVE else "terms")
    revision = str(info.get("sha") or "") or None
    return OpenModel(
        repo=repo, name=name, revision=revision if revision and REVISION.fullmatch(revision) else None,
        architecture=architecture, family=SUPPORTED.get(architecture or ""), params_b=params_b, licence=licence,
        licence_id=licence_id, licence_kind=kind, licence_url=url, terms=terms, gated=gated,
        downloads=int(info.get("downloads") or 0), unsupported=_unsupported(info, name, architecture),
    )


def search(query: str, *, reader: Reader = safe_reader) -> list[OpenModel]:
    """Chat models on Hugging Face matching ``query``, most downloaded first (free; no sign-in)."""

    text = " ".join(str(query or "").split())[:80]
    if len(text) < 2:
        return []
    params = urlencode({"search": text, "pipeline_tag": "text-generation", "sort": "downloads", "direction": "-1",
                        "limit": str(SEARCH_LIMIT)})
    found = _json(reader, f"{HUB}/api/models?{params}")
    if not isinstance(found, list):
        raise HubError("unreadable")
    models = []
    for item in found[:SEARCH_LIMIT]:
        try:
            models.append(describe(item))
        except HubError:
            continue
    return models


def inspect(repo: str, *, reader: Reader = safe_reader) -> tuple[OpenModel, list[dict[str, Any]]]:
    """A model's card at its current revision and the files Row-Bot would download (name, size, and the Hub's
    fingerprint: an LFS file's SHA-256 or a small file's git blob id)."""

    if not REPO.fullmatch(str(repo or "")):
        raise HubError("not_found")
    info = _json(reader, f"{HUB}/api/models/{quote(repo, safe='/')}")
    if not isinstance(info, Mapping):
        raise HubError("unreadable")
    model = describe(info)
    if model.revision is None:
        raise HubError("unreadable")
    tree = _json(reader, f"{HUB}/api/models/{quote(repo, safe='/')}/tree/{model.revision}")
    files = []
    for item in tree if isinstance(tree, list) else ():
        if not isinstance(item, Mapping) or item.get("type") != "file":
            continue
        path = str(item.get("path") or "")
        if "/" in path or not _KEEP.search(path):
            continue
        lfs = item.get("lfs") if isinstance(item.get("lfs"), Mapping) else None
        files.append({"name": path, "size": int(item.get("size") or 0),
                      "sha256": str(lfs.get("oid")) if lfs else None, "blob": None if lfs else str(item.get("oid"))})
    if not any(item["name"].endswith(".safetensors") for item in files):
        model = replace(model, unsupported=model.unsupported or
                        f"{model.name} has no safetensors weights. Row-Bot trains from those.")
    return replace(model, download_bytes=sum(item["size"] for item in files)), files


# ── Where it trains and runs ────────────────────────────────────────────────


def serve_estimate(params_b: float | None) -> catalog.ServeOption | None:
    """Its Q4_K_M GGUF and the graphics memory Row-Bot's own server needs for it (from the 9B's and 27B's measured
    needs, L2x and C1: about 1.25 x the file and 1 GB); the check's serving step confirms it."""

    if not params_b:
        return None
    gguf = int(params_b * 1e9 * 0.61)
    return catalog.ServeOption("Q4_K_M", gguf, int(gguf / 2**20 * 1.25) + 1024)


@dataclass(frozen=True)
class HfCard:
    """One of Hugging Face's single cards (``GET /api/jobs/hardware``): its memory, its hourly price when reviewed
    (Hugging Face's live price wins in the quote) and its time a training step against the A100's (the L40S's is
    the certified recipes' reviewed estimate; the H200 is taken as the A100, so its estimate errs long)."""

    flavor: str
    memory_gb: int
    price_per_hour: float
    step_time: float
    # As the check says it: "an Nvidia L40S (48 GB)".
    words: str


HF_CARDS: tuple[HfCard, ...] = (HfCard("l40sx1", 48, 1.80, 1.5, "an Nvidia L40S (48 GB)"),
                                HfCard("a100-large", 80, 2.50, 1.0, "an Nvidia A100 (80 GB)"),
                                HfCard("h200", 141, 5.00, 1.0, "an Nvidia H200 (141 GB)"))
# A checked model trains with the settings of the certified Hugging Face recipe nearest its size.
REMOTE_BASES: tuple[tuple[float, str], ...] = ((18.0, "hf-9b"), (math.inf, "hf-27b"))


def hugging_face_flavor(params_b: float | None) -> str | None:
    """The smallest single card on Hugging Face that trains it (4-bit, 16K rows): about 0.7 GB a billion
    parameters and 12 GB of activations (the 27B on an L40S: 46 GB, ``recipes/hf-27b.json``)."""

    if not params_b:
        return None
    need = params_b * 0.7 + 12
    return next((card.flavor for card in HF_CARDS if need <= card.memory_gb * 0.95), None)


def _per_billion(key: str) -> float:
    """Seconds a billion parameters take on the A100 for ``key`` (a step, or one pass's export): the larger of the
    certified Hugging Face recipes' measured rates, so a checked model's estimate errs long."""

    from row_bot.learning.training import recipes

    rates = []
    for model in catalog.CERTIFIED:
        if not model.remote_recipe:
            continue
        remote = recipes.load(model.remote_recipe).remote or {}
        speed = next((card.step_time for card in HF_CARDS if card.flavor == remote.get("flavor")), 1.0)
        rates.append(float(remote[key]) / (speed if key == "seconds_per_step" else 1.0) / model.params_b)
    return max(rates)


def remote_recipe(record: Record) -> Any:
    """The Hugging Face recipe that trains a checked model (O138), or None when no single card there holds it: the
    certified recipe nearest its size (its settings, passes and serving shape), never certified, on the card the
    check chose with the next larger card as the alternative, and times scaled from the certified recipes'
    measured runs by the model's size and each card's speed. Its quote says the times and price are estimates."""

    from row_bot.learning.training import recipes

    serve = serve_estimate(record.params_b)
    first = next((index for index, card in enumerate(HF_CARDS) if card.flavor == record.hugging_face_flavor), None)
    if record.state != "passed" or first is None or serve is None or not record.params_b:
        return None
    params = float(record.params_b)
    base = recipes.load(next(recipe for limit, recipe in REMOTE_BASES if params <= limit))
    step = _per_billion("seconds_per_step") * params
    cards = HF_CARDS[first:first + 2]  # its card, and the next larger one (as the certified recipes offer)
    remote = {**dict(base.remote or {}), "flavor": cards[0].flavor, "price_per_hour": cards[0].price_per_hour,
              "seconds_per_step": round(step * cards[0].step_time, 1),
              "export_seconds_per_pass": round(_per_billion("export_seconds_per_pass") * params),
              "alternatives": [{"flavor": card.flavor, "price_per_hour": card.price_per_hour,
                                "seconds_per_step": round(step * card.step_time, 1)} for card in cards[1:]]}
    return recipes.for_starting_model(
        base, record.id, title=f"{record.name} on Hugging Face", quantization=serve.quantization, remote=remote,
        evidence=(f"Not certified. {record.name} at {record.revision} passed Row-Bot's check on this computer; it "
                  f"trains with {base.title}'s settings, and its times are estimates from that model's size."))


# ── The checks' records ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckItem:
    # waiting · running · passed · failed · skipped
    state: str = "waiting"
    words: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Record:
    """One model the person chose: what Row-Bot read of it, the OK to its licence, and its checks."""

    repo: str
    name: str
    revision: str
    family: str
    architecture: str
    params_b: float | None
    licence: str
    licence_id: str
    licence_kind: str
    licence_url: str
    terms: tuple[str, ...]
    download_bytes: int
    files: tuple[Mapping[str, Any], ...]
    state: str = "not_checked"
    stage: str | None = None
    items: Mapping[str, CheckItem] = field(default_factory=dict)
    licence_ok_at: float | None = None
    downloaded_bytes: int = 0
    # Where it trains once checked: "here" on this computer's card, and/or on Hugging Face (its card).
    trains_here: bool | None = None
    hugging_face_flavor: str | None = None
    failure: str | None = None
    sample: tuple[Mapping[str, Any], ...] = ()
    checked_at: float | None = None
    started_at: float | None = None
    # The model's files as downloaded, each with its SHA-256 (the pin a training or a job downloads and checks).
    pinned: tuple[Mapping[str, Any], ...] = ()

    @property
    def id(self) -> str:
        return model_id(self.repo)

    def item(self, name: str) -> CheckItem:
        return self.items.get(name) or CheckItem()


def folder(root: Path | None = None) -> Path:
    from row_bot.learning.training import jobs as jobs_module

    return jobs_module.models_dir(root) / FOLDER


def _slug(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "--", repo)[:180]


def _path(repo: str, root: Path | None) -> Path:
    return folder(root) / f"{_slug(repo)}.json"


def _to_json(record: Record) -> dict[str, Any]:
    data = asdict(record)
    data["format"] = FORMAT
    return data


def _from_json(data: Mapping[str, Any]) -> Record | None:
    try:
        known = {key: data[key] for key in Record.__dataclass_fields__ if key in data}
        known["terms"] = tuple(known.get("terms") or ())
        known["files"] = tuple(known.get("files") or ())
        known["sample"] = tuple(known.get("sample") or ())
        known["pinned"] = tuple(known.get("pinned") or ())
        known["items"] = {name: CheckItem(**dict(value)) for name, value in dict(known.get("items") or {}).items()
                          if name in CHECK_ITEMS}
        record = Record(**known)
    except (TypeError, ValueError, KeyError):
        return None
    return record if REPO.fullmatch(record.repo) and record.state in STATES else None


def read(repo: str, root: Path | None = None) -> Record | None:
    try:
        return _from_json(json.loads(_path(repo, root).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def write(record: Record, root: Path | None = None) -> Record:
    """Save the record whole. A client reading it while the check saves its next step holds it open for a moment
    on Windows, so the swap waits that out (``atomic``) instead of failing the check."""

    from row_bot.learning import atomic

    atomic.write_text(_path(record.repo, root), json.dumps(_to_json(record), indent=1))
    return record


def records(root: Path | None = None) -> list[Record]:
    """Every model the person chose to check, newest first."""

    found = []
    for path in sorted(folder(root).glob("*.json")) if folder(root).is_dir() else ():
        try:
            record = _from_json(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            record = None
        if record is not None:
            found.append(record)
    return sorted(found, key=lambda item: -(item.started_at or item.checked_at or 0.0))


def forget(repo: str, root: Path | None = None) -> bool:
    try:
        _path(repo, root).unlink()
        return True
    except OSError:
        return False


def new_record(model: OpenModel, files: list[dict[str, Any]]) -> Record:
    if model.unsupported or model.gated or model.revision is None or model.architecture is None:
        raise ValueError("a model Row-Bot can't try")
    return Record(
        repo=model.repo, name=model.name, revision=model.revision, family=model.family or "",
        architecture=model.architecture, params_b=model.params_b, licence=model.licence, licence_id=model.licence_id,
        licence_kind=model.licence_kind, licence_url=model.licence_url, terms=model.terms,
        download_bytes=model.download_bytes, files=tuple(files),
        licence_ok_at=None if model.licence_kind == "terms" else 0.0,
    )


def pinned_model(record: Record) -> weights.PinnedModel | None:
    """The model's pin once its files were downloaded and fingerprinted (None before)."""

    if not record.pinned:
        return None
    return weights.PinnedModel(
        id=record.id.replace("/", "--").replace(":", "-"), name=record.name, repo=record.repo,
        revision=record.revision, licence=record.licence, licence_id=record.licence_id,
        files=tuple(weights.WeightFile(str(item["name"]), int(item["size"]), str(item["sha256"]))
                    for item in record.pinned))


def _choice(record: Record) -> catalog.ModelChoice | None:
    serve = serve_estimate(record.params_b)
    if record.state != "passed" or serve is None:
        return None
    remote = remote_recipe(record)
    return catalog.ModelChoice(
        id=record.id, name=record.name, repo=record.repo, revision=record.revision, licence=record.licence,
        licence_id=record.licence_id, params_b=float(record.params_b or 0.0), certified=False,
        # The certified Hugging Face recipe it trains with there (``remote_recipe`` adapts it to this model).
        remote_recipe=remote.id if remote is not None else None,
        serve=(serve,), pinned=pinned_model(record),
        checks={name: {"state": record.item(name).state, "words": record.item(name).words} for name in CHECK_ITEMS},
    )


def as_choice(record: Record, card_mib: int | None) -> catalog.ModelChoice | None:
    """A checked model as one more model to choose (O138): uncertified, with what was checked. None until its
    checks passed, or when this card can't run it afterwards (O131)."""

    choice = _choice(record)
    return choice if choice is not None and choice.serve_for(card_mib) is not None else None


def checked_choice(model_id: str, root: Path | None = None) -> catalog.ModelChoice | None:
    """The checked model ``model_id`` names (``hf:<repo>``) once its check passed: what a run already under way
    trains (offering it is :func:`as_choice`'s, for this card)."""

    repo = repo_of(model_id)
    record = read(repo, root) if repo else None
    return _choice(record) if record is not None else None


def hugging_face_choices(card_mib: int | None, root: Path | None = None) -> tuple[catalog.ModelChoice, ...]:
    """The checked models Hugging Face can train for this computer (O138), newest first: passed, a single card
    there holds them, and this card runs them afterwards (O131)."""

    found = (as_choice(record, card_mib) for record in records(root))
    return tuple(choice for choice in found if choice is not None and choice.remote_recipe)


# ── The check ───────────────────────────────────────────────────────────────


class Stopped(RuntimeError):
    """The person stopped the check (or Learning turned off)."""


@dataclass(frozen=True)
class FitResult:
    ran: bool
    peak_mib: int = 0
    cap_mib: int = 0
    losses: tuple[float, ...] = ()
    grad_norms: tuple[float, ...] = ()
    step_seconds: float | None = None
    adapter: Path | None = None
    error: str | None = None


class Steps:
    """What a check does on this computer (the engine, the export, Row-Bot's server); tests pass fakes."""

    def pin(self, record: Record, check: Callable[[], None]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def download(self, model: weights.PinnedModel, progress: Callable[[int], None],
                 check: Callable[[], None]) -> Path:
        raise NotImplementedError

    def fit(self, weights_dir: Path, *, max_seq: int, cap_share: float, work: Path,
            check: Callable[[], None]) -> FitResult:
        raise NotImplementedError

    def export(self, weights_dir: Path, adapter: Path, work: Path, check: Callable[[], None]) -> Path:
        raise NotImplementedError

    def tool_sample(self, gguf: Path, work: Path, check: Callable[[], None]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def card(self) -> tuple[int | None, float]:
        """This card's memory (MiB) and the share of it its tier's training may use."""

        raise NotImplementedError


SHORT_SEQ = 2048
# A personal model is served with this context (the certified recipes' ``serve_context``): a model built for
# less would read past what it learned, so its check fails.
SERVE_CONTEXT = 32768


def context_limit(weights_dir: Path) -> int | None:
    """The longest input the model was built for, from its ``config.json`` (None: it doesn't say)."""

    try:
        config = json.loads((Path(weights_dir) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(config, Mapping):
        return None
    for section in (config, config.get("text_config")):
        value = section.get("max_position_embeddings") if isinstance(section, Mapping) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None
FULL_SEQ = 16384


def sane(fit: FitResult) -> tuple[bool, str]:
    """A few steps' loss and gradients: every loss and gradient norm finite, the norms above zero (it learns),
    and the loss not running away (the last at most 1.5 times the first)."""

    if not fit.losses or not fit.grad_norms:
        return False, "The trial didn’t report its loss and gradients."
    if not all(math.isfinite(value) for value in (*fit.losses, *fit.grad_norms)):
        return False, "The loss or the gradients weren’t finite numbers."
    if not all(value > 0 for value in fit.grad_norms):
        return False, "The gradients were zero: the model wouldn’t learn."
    if fit.losses[-1] > fit.losses[0] * 1.5:
        return False, "The loss grew over the trial steps."
    return True, (f"Loss {fit.losses[0]:.2f} → {fit.losses[-1]:.2f} over {len(fit.losses)} steps; "
                  f"gradients finite.")


class Checker:
    """Runs one model's check in the background (one at a time), saving its record after every step."""

    def __init__(self, root: Path | None = None, *, steps: Steps | None = None,
                 clock: Callable[[], float] = time.time, start_threads: bool = True) -> None:
        self._root = root
        self._steps = steps
        self._clock = clock
        self._threads = start_threads
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._running: str | None = None
        self._announced = -math.inf

    @property
    def running(self) -> str | None:
        return self._running

    def start(self, record: Record) -> Record:
        """Start ``record``'s check (its licence agreed where it needs that). One check at a time."""

        with self._lock:
            if self._running is not None:
                raise RuntimeError("busy")
            if record.licence_kind == "terms" and record.licence_ok_at is None:
                raise PermissionError("licence")
            self._running = record.repo
            self._stop.clear()
        record = self._save(replace(record, state="downloading", stage="download", failure=None,
                                    started_at=self._clock(), items={name: CheckItem() for name in CHECK_ITEMS},
                                    sample=()))
        if self._threads:
            threading.Thread(target=self.run, args=(record.repo,), name="learning-model-check", daemon=True).start()
        return record

    def stop(self) -> None:
        self._stop.set()

    def _save(self, record: Record, *, progress: bool = False) -> Record:
        """Save the check's record, and tell every open client to read Learning again (O127): at each step, and
        while it downloads at most every few seconds."""

        write(record, self._root)
        now = self._clock()
        if not progress or now - self._announced >= ANNOUNCE_SECONDS:
            self._announced = now
            try:
                from row_bot import learning

                learning.changed()
            except Exception:  # noqa: BLE001 - a client reads it next time instead
                logger.debug("Learning couldn't announce a model check's progress", exc_info=True)
        return record

    def _check(self) -> None:
        if self._stop.is_set():
            raise Stopped()

    def run(self, repo: str) -> Record | None:
        record = read(repo, self._root)
        try:
            if record is None:
                return None
            return self._run(record)
        except Stopped:
            found = read(repo, self._root) or record
            if found is None:
                return None
            items = {name: found.item(name) if found.item(name).state in ("passed", "failed") else CheckItem("skipped")
                     for name in CHECK_ITEMS}
            return self._save(replace(found, state="stopped", stage=None, items=items))
        except Exception:  # noqa: BLE001 - a broken check fails, it never breaks Row-Bot
            logger.warning("Learning's model check failed unexpectedly", exc_info=True)
            found = read(repo, self._root) or record
            return self._fail(found, found.stage or "fits", "Something went wrong during the check.") \
                if found is not None else None
        finally:
            with self._lock:
                self._running = None

    def _item(self, record: Record, name: str, state: str, words: str = "", **data: Any) -> Record:
        items = dict(record.items)
        items[name] = CheckItem(state, words, data)
        stage = name if state == "running" else record.stage
        return self._save(replace(record, items=items, stage=stage))

    def _fail(self, record: Record, name: str, words: str, code: str | None = None) -> Record:
        record = self._item(record, name, "failed", words)
        items = dict(record.items)
        for later in CHECK_ITEMS[CHECK_ITEMS.index(name) + 1:] if name in CHECK_ITEMS else ():
            items[later] = CheckItem("skipped")
        return self._save(replace(record, items=items, state="failed", stage=None, failure=code or name,
                                  checked_at=self._clock()))

    def _run(self, record: Record) -> Record:
        steps = self._steps or EngineSteps(self._root)
        work = folder(self._root) / f".work-{_slug(record.repo)}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            pinned = list(record.pinned) or steps.pin(record, self._check)
            record = self._save(replace(record, pinned=tuple(pinned)))
            model = pinned_model(record)
            if model is None:
                return self._fail(record, "fits", "Row-Bot couldn’t read the model’s files.", "download_failed")

            def moved(done: int) -> None:
                nonlocal record
                if done - record.downloaded_bytes >= 64 * 2**20 or done >= record.download_bytes:
                    record = self._save(replace(record, downloaded_bytes=done), progress=True)

            try:
                weights_dir = steps.download(model, moved, self._check)
            except Stopped:
                raise
            except Exception:  # noqa: BLE001
                return self._fail(record, "fits", "The download didn’t finish. Check the connection and try again.",
                                  "download_failed")
            record = self._save(replace(record, state="checking", downloaded_bytes=model.download_bytes))
            card_mib, share = steps.card()
            record = self._item(record, "fits", "running")
            limit = context_limit(weights_dir)
            if limit is not None and limit < SERVE_CONTEXT:
                return self._fail(record, "fits", f"It was built to read {limit:,} tokens at once; a personal "
                                  f"model needs {SERVE_CONTEXT:,} for Row-Bot’s tools and your chat.",
                                  "context_too_short")
            fit = steps.fit(weights_dir, max_seq=FULL_SEQ, cap_share=share, work=work, check=self._check)
            flavor = hugging_face_flavor(record.params_b)
            here = fit.ran
            if here:
                record = self._item(record, "fits", "passed",
                                    f"Trains on this card: peak {fit.peak_mib / 1024:.1f} GB of the "
                                    f"{fit.cap_mib / 1024:.1f} GB it may use.",
                                    peak_mib=fit.peak_mib, cap_mib=fit.cap_mib, seconds_per_step=fit.step_seconds)
            elif flavor is not None:
                card = next(item.words for item in HF_CARDS if item.flavor == flavor)
                record = self._item(record, "fits", "passed",
                                    f"Too big to train on this card (it needs more than {fit.cap_mib / 1024:.1f} GB); "
                                    f"it trains on Hugging Face on {card}.", peak_mib=fit.peak_mib,
                                    cap_mib=fit.cap_mib)
                fit = steps.fit(weights_dir, max_seq=SHORT_SEQ, cap_share=share, work=work, check=self._check)
                if not fit.ran:
                    return self._fail(record, "steps", "Even a short trial doesn’t fit this card, so Row-Bot can’t "
                                      "check the rest here.", "no_room")
            else:
                return self._fail(record, "fits", "Too big to train on this card or on one of Hugging Face’s.",
                                  "too_big")
            record = self._save(replace(record, trains_here=here, hugging_face_flavor=flavor))
            record = self._item(record, "steps", "running")
            ok, words = sane(fit)
            if not ok:
                return self._fail(record, "steps", words, "steps")
            record = self._item(record, "steps", "passed", words, losses=list(fit.losses),
                                grad_norms=list(fit.grad_norms))
            record = self._item(record, "export", "running")
            try:
                gguf = steps.export(weights_dir, fit.adapter or work / "adapter", work, self._check)
            except Stopped:
                raise
            except Exception:  # noqa: BLE001
                return self._fail(record, "export", "Its trained copy didn’t convert to run on Row-Bot’s own server.",
                                  "export")
            size = gguf.stat().st_size if gguf.is_file() else 0
            record = self._item(record, "export", "passed",
                                f"Converted to a {size / 1e9:.1f} GB file at Q4_K_M.",
                                gguf_bytes=size)
            record = self._item(record, "tools", "running")
            sample = steps.tool_sample(gguf, work, self._check)
            passed = sum(1 for item in sample if item.get("passed"))
            record = self._save(replace(record, sample=tuple(sample)))
            if passed < TOOL_PASSES:
                return self._fail(record, "tools", f"Right {passed} of {len(sample)} times; it needs at least "
                                  f"{TOOL_PASSES}.", "tools")
            record = self._item(record, "tools", "passed",
                                f"Right {passed} of {len(sample)} times.")
            return self._save(replace(record, state="passed", stage=None, failure=None, checked_at=self._clock()))
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)


# ── The real steps ───────────────────────────────────────────────────────────


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


class EngineSteps(Steps):
    """The check on this computer: the pinned download, the training engine's trial and export, and Row-Bot's own
    server with the tool sample."""

    def __init__(self, root: Path | None = None, *, reader: Reader = safe_reader) -> None:
        self._root = root
        self._reader = reader

    def pin(self, record: Record, check: Callable[[], None]) -> list[dict[str, Any]]:
        """Every file with its SHA-256: an LFS file's from the Hub, a small file's by reading it (and checking it
        against the Hub's git blob id first)."""

        pinned = []
        for item in record.files:
            check()
            if item.get("sha256"):
                pinned.append({"name": item["name"], "size": int(item["size"]), "sha256": str(item["sha256"])})
                continue
            if int(item.get("size") or 0) > SMALL_FILE_BYTES:
                raise HubError("unreadable")
            data = self._reader(f"{HUB}/{record.repo}/resolve/{record.revision}/{quote(str(item['name']))}",
                                SMALL_FILE_BYTES)
            if git_blob_id(data) != item.get("blob"):
                raise HubError("unreadable")
            pinned.append({"name": item["name"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return pinned

    def download(self, model: weights.PinnedModel, progress: Callable[[int], None],
                 check: Callable[[], None]) -> Path:
        from row_bot.learning.training import fetch

        def checked() -> None:
            try:
                check()
            except Stopped as error:
                raise fetch.Stopped() from error

        try:
            return weights.download(model, root=self._root, progress=progress, check=checked)
        except fetch.Stopped as error:
            raise Stopped() from error

    def card(self) -> tuple[int | None, float]:
        from row_bot.learning.training import hardware, recipes

        found = hardware.check()
        memory = int(found.gpu.memory_mib) if found.gpu is not None else None
        try:
            share = float(recipes.current(self._root).settings.get("vram_cap", 0.8))
        except Exception:  # noqa: BLE001 - no tier: the default cap
            share = 0.8
        return memory, share

    def _engine(self) -> Any:
        from row_bot.learning.training import engine

        found = engine.status(self._root)
        if getattr(found, "python", None) is None or getattr(found, "site", None) is None:
            raise RuntimeError("engine_missing")
        return found

    def fit(self, weights_dir: Path, *, max_seq: int, cap_share: float, work: Path,
            check: Callable[[], None]) -> FitResult:
        from row_bot.learning.training import engine
        from row_bot.learning.training.worker import TELEMETRY_ENV

        found = self._engine()
        out = work / f"fit-{max_seq}.json"
        adapter = work / f"adapter-{max_seq}"
        home = work / f"home-{uuid.uuid4().hex[:6]}"
        home.mkdir(parents=True, exist_ok=True)
        env = {**engine._base_environment(home), **engine.engine_variables(self._root), **TELEMETRY_ENV}  # noqa: SLF001
        argv = [str(found.python), "-I", "-S", str(engine.ENTRY), "fit", "--site", str(found.site),
                "--model", str(weights_dir), "--target-tokens", str(max_seq - 256), "--max-seq", str(max_seq),
                "--bits", "4", "--steps", "4", "--accum", "2", "--vram-cap", str(cap_share), "--out", str(out),
                "--adapter-out", str(adapter)]
        engine.run_owned(argv, cwd=home, env=env, timeout=40 * 60, check=check)
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"ran": False, "error": "no_result"}
        card_mib, _share = self.card()
        return FitResult(
            ran=bool(data.get("ran")), peak_mib=int(data.get("peak_reserved_mib") or 0),
            cap_mib=int((card_mib or 0) * cap_share), losses=tuple(float(x) for x in data.get("losses") or ()),
            grad_norms=tuple(float(x) for x in data.get("grad_norms") or ()),
            step_seconds=data.get("step_seconds_median"),
            adapter=adapter if (adapter / "adapter_config.json").is_file() else None, error=data.get("error"))

    def export(self, weights_dir: Path, adapter: Path, work: Path, check: Callable[[], None]) -> Path:
        from row_bot.learning.training import engine, export

        found = engine.status(self._root)
        size = sum(item.stat().st_size for item in weights_dir.glob("*.safetensors"))
        result = export.export_gguf(engine=found, weights=weights_dir, weights_bytes=size, adapter=adapter,
                                    folder=work / "gguf", name="row-bot-check", quantization="Q4_K_M", check=check)
        return result.gguf

    def tool_sample(self, gguf: Path, work: Path, check: Callable[[], None]) -> list[dict[str, Any]]:
        from row_bot.learning.training import llama_server, serving

        outcome = serving.serve(serving.ROW_BOT, version=0, gguf=gguf, serve_context=FULL_SEQ, folder=work,
                                root=self._root, tag=CHECK_NAME, tool_turn=False)
        try:
            if outcome.state != "ready" or not outcome.model_ref:
                return [{"id": item["id"], "prompt": item["prompt"], "passed": False, "tools": [],
                         "answered": False, "reason": outcome.reason or "not_ready"} for item in TOOL_SAMPLE]
            results = []
            for item in TOOL_SAMPLE:
                check()
                turn = serving.agent_tool_turn(outcome.model_ref, prompt=item["prompt"], tools=item["tools"],
                                               expected=item["answer"])
                results.append({"id": item["id"], "prompt": item["prompt"], "passed": bool(turn.get("ok")),
                                "tools": list(turn.get("tools") or ())[:3], "answered": bool(turn.get("answered"))})
            return results
        finally:
            try:
                server = llama_server.get(self._root)
                server.unload(CHECK_NAME)
                server.unregister(CHECK_NAME)
            except Exception:  # noqa: BLE001
                logger.debug("Learning couldn't take the checked model off its server", exc_info=True)


CHECK_NAME = "row-bot-model-check"

_CHECKER: Checker | None = None
_CHECKER_LOCK = threading.Lock()


def checker(root: Path | None = None) -> Checker:
    """This profile's one checker (a check uses the graphics card: one at a time)."""

    global _CHECKER
    with _CHECKER_LOCK:
        if _CHECKER is None:
            _CHECKER = Checker(root)
        return _CHECKER


def reset_checker(value: Checker | None = None) -> None:
    global _CHECKER
    with _CHECKER_LOCK:
        _CHECKER = value


__all__ = [
    "CHECK_ITEMS", "Checker", "CheckItem", "EngineSteps", "FitResult", "HF_CARDS", "HfCard", "HubError", "OpenModel",
    "Record", "Steps", "Stopped", "TERMS", "TOOL_SAMPLE", "as_choice", "checked_choice", "checker", "describe",
    "forget", "hugging_face_choices", "hugging_face_flavor", "inspect", "model_id", "new_record", "pinned_model",
    "read", "records", "remote_recipe", "repo_of", "reset_checker", "sane", "search", "serve_estimate", "write",
]
