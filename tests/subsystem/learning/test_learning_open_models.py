"""C1 Part 4 (O138): any open model. Reading Hugging Face (faked), what Row-Bot can try, licences, and the
automatic check on this computer (its steps faked): what passes, what fails and why, and what is offered."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from row_bot.learning.training import open_models

pytestmark = [pytest.mark.subsystem]

REV = "40c069824f4251a91eefaf281ebe4c544efd3e18"
CONFIG = b'{"architectures": ["Qwen3ForCausalLM"], "model_type": "qwen3"}'


def _card(repo: str = "Qwen/Qwen3-14B", **extra: Any) -> dict[str, Any]:
    card = {"id": repo, "sha": REV, "gated": False, "pipeline_tag": "text-generation", "library_name": "transformers",
            "config": {"architectures": ["Qwen3ForCausalLM"], "model_type": "qwen3"},
            "cardData": {"license": "apache-2.0"}, "safetensors": {"total": 14_768_307_200}, "downloads": 11_000_000,
            "tags": ["qwen3", "license:apache-2.0"]}
    card.update(extra)
    return card


def _tree() -> list[dict[str, Any]]:
    return [
        {"type": "file", "path": ".gitattributes", "size": 1570, "oid": "x"},
        {"type": "file", "path": "README.md", "size": 16670, "oid": "x"},
        {"type": "file", "path": "LICENSE", "size": 5, "oid": open_models.git_blob_id(b"terms")},
        {"type": "file", "path": "config.json", "size": len(CONFIG), "oid": open_models.git_blob_id(CONFIG)},
        {"type": "file", "path": "model-00001-of-00002.safetensors", "size": 3_841_788_544,
         "oid": "y", "lfs": {"oid": "a" * 64, "size": 3_841_788_544}},
        {"type": "file", "path": "model-00002-of-00002.safetensors", "size": 1_000, "oid": "y",
         "lfs": {"oid": "b" * 64, "size": 1_000}},
        {"type": "directory", "path": "original", "oid": "z"},
    ]


class FakeHub:
    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, url: str, max_bytes: int) -> bytes:
        self.asked.append(url)
        for key, value in self.pages.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        raise open_models.HubError("not_found")


def _hub(card: dict[str, Any] | None = None) -> FakeHub:
    card = card or _card()
    return FakeHub({f"/api/models/{card['id']}/tree/{REV}": _tree(), f"/api/models/{card['id']}": card,
                    f"/{card['id']}/resolve/{REV}/LICENSE": b"terms",
                    f"/{card['id']}/resolve/{REV}/config.json": CONFIG})


# ── Reading Hugging Face ────────────────────────────────────────────────────


def test_search_asks_the_hub_for_chat_models_most_downloaded_first_and_reads_each_result():
    results = [_card("Qwen/Qwen3-14B"), {"id": "not a repo"}, _card("Qwen/Qwen3-30B-A3B", config=None,
                                                                    tags=["qwen3_moe", "license:apache-2.0"])]
    hub = FakeHub({"/api/models?": results})

    found = open_models.search("  qwen3  ", reader=hub)

    assert [model.repo for model in found] == ["Qwen/Qwen3-14B", "Qwen/Qwen3-30B-A3B"]
    assert "search=qwen3" in hub.asked[0] and "pipeline_tag=text-generation" in hub.asked[0]
    assert "sort=downloads" in hub.asked[0] and hub.asked[0].startswith("https://huggingface.co/api/models?")
    assert found[0].unsupported is None and found[0].family == "Qwen" and found[0].params_b == 14.8
    assert found[1].unsupported == ("Qwen3-30B-A3B is a mixture-of-experts model. Row-Bot’s training doesn’t adapt "
                                    "those yet.")
    assert open_models.search("q", reader=hub) == []  # too short: nothing asked
    assert len(hub.asked) == 1


def test_a_model_s_card_and_files_at_its_revision_only_what_training_needs():
    model, files = open_models.inspect("Qwen/Qwen3-14B", reader=_hub())

    assert model.revision == REV and model.licence == "Apache 2.0" and model.licence_kind == "permissive"
    assert [item["name"] for item in files] == ["LICENSE", "config.json", "model-00001-of-00002.safetensors",
                                                "model-00002-of-00002.safetensors"]
    assert files[2]["sha256"] == "a" * 64 and files[1]["blob"] == open_models.git_blob_id(CONFIG)
    assert model.download_bytes == 5 + len(CONFIG) + 3_841_788_544 + 1_000


@pytest.mark.parametrize(("card", "words"), [
    (_card("microsoft/Phi-4-mini-instruct", config={"architectures": ["Phi3ForCausalLM"]}),
     "Phi-4-mini-instruct packs its attention layers in a way Row-Bot’s training doesn’t adapt yet."),
    (_card("org/Some-GGUF", library_name="gguf", config=None, tags=[]),
     "Some-GGUF has only GGUF files. Row-Bot trains from a model’s original weights."),
    (_card("org/embedder", pipeline_tag="feature-extraction"),
     "embedder isn’t a chat model, so Row-Bot can’t learn from your chats with it."),
    (_card("org/Falcon-7B", config={"architectures": ["FalconForCausalLM"]}),
     "Row-Bot can’t train Falcon models yet."),
])
def test_what_row_bot_can_t_train_is_said_plainly(card, words):
    assert open_models.describe(card).unsupported == words


@pytest.mark.parametrize(("card", "kind", "name", "first_term"), [
    (_card(cardData={"license": "mit"}), "permissive", "MIT", None),
    (_card("meta-llama/Llama-3.1-8B-Instruct", gated="manual", cardData={"license": "llama3.1"},
           config={"architectures": ["LlamaForCausalLM"]}), "gated", "Llama Community License", "You may use"),
    (_card("unsloth/Llama-3.1-8B-Instruct", cardData={"license": "llama3.1"},
           config={"architectures": ["LlamaForCausalLM"]}), "terms", "Llama Community License", "You may use"),
    (_card("org/research", cardData={"license": "cc-by-nc-4.0"}), "terms", "a non-commercial licence",
     "For personal and research use only"),
    (_card("org/odd", cardData={"license": "other", "license_name": "odd-licence"}), "terms", "odd-licence",
     "Read the licence on Hugging Face"),
])
def test_licences_permissive_show_others_have_key_terms_and_gated_ones_link_to_request_access(
        card, kind, name, first_term):
    model = open_models.describe(card)

    assert (model.licence_kind, model.licence) == (kind, name)
    assert (model.terms[0].startswith(first_term) if first_term else model.terms == ())
    assert model.licence_url.startswith("https://huggingface.co/")


def test_a_hub_that_can_t_be_read_says_so():
    with pytest.raises(open_models.HubError) as raised:
        open_models.inspect("Qwen/Qwen3-14B", reader=FakeHub({"/api/models/": open_models.HubError("unreachable")}))
    assert raised.value.code == "unreachable"
    with pytest.raises(open_models.HubError):
        open_models.inspect("../etc", reader=FakeHub({}))


def test_a_gated_or_unsupported_model_never_gets_a_check():
    gated = open_models.describe(_card(gated="auto"))
    phi = open_models.describe(_card(config={"architectures": ["Phi3ForCausalLM"]}))

    for model in (gated, phi):
        with pytest.raises(ValueError):
            open_models.new_record(model, [])


# ── The check ───────────────────────────────────────────────────────────────


class FakeSteps(open_models.Steps):
    def __init__(self, tmp: Path, *, fits: bool = True, short_fits: bool = True,
                 losses=(2.1, 1.9, 1.8, 1.6), grads=(1.2, 0.9, 0.8, 0.7), export_ok: bool = True,
                 tools=(True, True, False), card_mib: int = 32_607, stop_at: str | None = None,
                 checker: list | None = None) -> None:
        self.tmp, self.fits, self.short_fits = tmp, fits, short_fits
        self.losses, self.grads, self.export_ok, self.tools = losses, grads, export_ok, tools
        self.card_mib, self.stop_at, self.calls = card_mib, stop_at, []
        self.checker = checker

    def _maybe_stop(self, name: str) -> None:
        if self.stop_at == name and self.checker:
            self.checker[0].stop()

    def pin(self, record, check):
        self.calls.append("pin")
        return [{"name": "config.json", "size": 10, "sha256": "c" * 64},
                {"name": "model.safetensors", "size": 100, "sha256": "d" * 64}]

    def download(self, model, progress, check):
        self.calls.append(("download", model.repo, model.revision))
        self._maybe_stop("download")
        check()
        progress(110)
        return self.tmp / "weights"

    def card(self):
        return self.card_mib, 0.8

    def fit(self, weights_dir, *, max_seq, cap_share, work, check):
        self.calls.append(("fit", max_seq))
        check()
        ran = self.fits if max_seq == open_models.FULL_SEQ else self.short_fits
        return open_models.FitResult(ran=ran, peak_mib=21_712 if ran else 26_044, cap_mib=int(self.card_mib * cap_share),
                                     losses=tuple(self.losses) if ran else (), grad_norms=tuple(self.grads) if ran else (),
                                     step_seconds=31.7, adapter=work / "adapter")

    def export(self, weights_dir, adapter, work, check):
        self.calls.append("export")
        self._maybe_stop("export")
        check()
        if not self.export_ok:
            raise RuntimeError("convert")
        gguf = work / "row-bot-check-Q4_K_M.gguf"
        gguf.write_bytes(b"g" * 2048)
        return gguf

    def tool_sample(self, gguf, work, check):
        self.calls.append("tools")
        return [{"id": item["id"], "prompt": item["prompt"], "passed": ok, "tools": ["calculator"] if ok else [],
                 "answered": ok} for item, ok in zip(open_models.TOOL_SAMPLE, self.tools)]


def _record(tmp_path: Path, card: dict[str, Any] | None = None, *, agreed: bool = True) -> open_models.Record:
    model, files = open_models.inspect((card or _card())["id"], reader=_hub(card))
    record = open_models.new_record(model, files)
    if agreed and record.licence_ok_at is None:
        record = open_models.write(replace(record, licence_ok_at=1.0), tmp_path)
    return open_models.write(record, tmp_path)


def _check(tmp_path: Path, steps: FakeSteps, record: open_models.Record) -> open_models.Record:
    checker = open_models.Checker(tmp_path, steps=steps, clock=lambda: 100.0, start_threads=False)
    if steps.checker is not None:
        steps.checker.append(checker)
    started = checker.start(record)
    assert (started.state, started.stage) == ("downloading", "download")
    return checker.run(record.repo)


def test_a_model_that_fits_learns_converts_and_calls_tools_passes_and_says_what_was_checked(tmp_path):
    steps = FakeSteps(tmp_path)

    found = _check(tmp_path, steps, _record(tmp_path))

    assert (found.state, found.trains_here, found.hugging_face_flavor, found.failure) == ("passed", True, "l40sx1", None)
    assert [found.item(name).state for name in open_models.CHECK_ITEMS] == ["passed"] * 4
    assert found.item("fits").words == "Trains on this card: peak 21.2 GB of the 25.5 GB it may use."
    assert found.item("steps").words == "Loss 2.10 → 1.60 over 4 steps; gradients finite."
    assert found.item("tools").words == "It called Row-Bot’s tools right 2 of 3 times."
    assert [item["prompt"] for item in found.sample] == [item["prompt"] for item in open_models.TOOL_SAMPLE]
    assert steps.calls[:2] == ["pin", ("download", "Qwen/Qwen3-14B", REV)]
    assert ("fit", open_models.FULL_SEQ) in steps.calls and ("fit", open_models.SHORT_SEQ) not in steps.calls
    assert open_models.read("Qwen/Qwen3-14B", tmp_path) == found  # saved as it ended
    assert not list(open_models.folder(tmp_path).glob(".work-*"))  # the trial's files are gone


def test_too_big_for_this_card_still_passes_to_train_on_hugging_face_after_a_short_trial(tmp_path):
    steps = FakeSteps(tmp_path, fits=False)

    found = _check(tmp_path, steps, _record(tmp_path))

    assert (found.state, found.trains_here, found.hugging_face_flavor) == ("passed", False, "l40sx1")
    assert found.item("fits").words.startswith("Too big to train on this card (it needs more than 25.5 GB); "
                                               "trains on Hugging Face on l40sx1.")
    assert ("fit", open_models.SHORT_SEQ) in steps.calls


@pytest.mark.parametrize(("steps_kwargs", "failed", "code", "words"), [
    ({"losses": (2.0, math.nan, 1.0, 1.0)}, "steps", "steps", "The loss or the gradients weren’t finite numbers."),
    ({"grads": (0.0, 0.0, 0.0, 0.0)}, "steps", "steps", "The gradients were zero: the model wouldn’t learn."),
    ({"losses": (1.0, 1.4, 1.9, 2.4)}, "steps", "steps", "The loss grew over the trial steps."),
    ({"export_ok": False}, "export", "export", "Its trained copy didn’t convert to run on Row-Bot’s own server."),
    ({"tools": (True, False, False)}, "tools", "tools",
     "It called Row-Bot’s tools right 1 of 3 times; it needs at least 2."),
    ({"fits": False, "short_fits": False}, "steps", "no_room",
     "Even a short trial doesn’t fit this card, so Row-Bot can’t check the rest here."),
])
def test_a_check_that_fails_says_which_part_and_why_and_skips_the_rest(tmp_path, steps_kwargs, failed, code, words):
    found = _check(tmp_path, FakeSteps(tmp_path, **steps_kwargs), _record(tmp_path))

    assert (found.state, found.failure, found.item(failed).words) == ("failed", code, words)
    later = open_models.CHECK_ITEMS[open_models.CHECK_ITEMS.index(failed) + 1:]
    assert all(found.item(name).state == "skipped" for name in later)
    assert open_models.as_choice(found, 32_607) is None  # never offered


def test_a_model_too_big_for_any_single_card_fails_at_fits(tmp_path):
    record = _record(tmp_path, _card("org/Huge", safetensors={"total": 400_000_000_000}))

    found = _check(tmp_path, FakeSteps(tmp_path, fits=False), record)

    assert (found.state, found.failure) == ("failed", "too_big")
    assert found.item("fits").words == "Too big to train on this card or on one of Hugging Face’s."


def test_stopping_a_check_keeps_what_it_learned_and_says_stopped(tmp_path):
    checker: list = []
    steps = FakeSteps(tmp_path, stop_at="export", checker=checker)

    found = _check(tmp_path, steps, _record(tmp_path))

    assert (found.state, found.stage) == ("stopped", None)
    assert found.item("steps").state == "passed" and "tools" not in steps.calls


def test_a_licence_with_terms_needs_the_person_s_ok_before_its_check(tmp_path):
    card = _card("unsloth/Llama-3.1-8B-Instruct", cardData={"license": "llama3.1"},
                 config={"architectures": ["LlamaForCausalLM"]})
    record = _record(tmp_path, card, agreed=False)
    checker = open_models.Checker(tmp_path, steps=FakeSteps(tmp_path), start_threads=False)

    with pytest.raises(PermissionError):
        checker.start(record)
    agreed = open_models.write(replace(record, licence_ok_at=5.0), tmp_path)
    assert checker.start(agreed).state == "downloading"
    with pytest.raises(RuntimeError):  # one check at a time
        checker.start(agreed)


def test_a_passed_model_is_offered_uncertified_with_its_checks_only_where_the_card_runs_it(tmp_path):
    found = _check(tmp_path, FakeSteps(tmp_path), _record(tmp_path))

    choice = open_models.as_choice(found, 32_607)

    assert choice is not None and not choice.certified and choice.id == "hf:Qwen/Qwen3-14B"
    assert choice.checks["tools"]["state"] == "passed" and choice.pinned is not None
    assert [item.sha256 for item in choice.pinned.files] == ["c" * 64, "d" * 64]
    assert open_models.as_choice(found, 8_192) is None  # an 8 GB card can't run a 14.8B model after (O131)


def test_small_files_are_pinned_by_reading_them_and_checking_the_hub_s_blob_id(tmp_path):
    record = _record(tmp_path)
    steps = open_models.EngineSteps(tmp_path, reader=_hub())

    pinned = steps.pin(record, lambda: None)

    by_name = {item["name"]: item for item in pinned}
    assert by_name["config.json"]["sha256"] == hashlib.sha256(CONFIG).hexdigest()
    assert by_name["model-00001-of-00002.safetensors"]["sha256"] == "a" * 64
    tampered = _hub()
    tampered.pages[f"/Qwen/Qwen3-14B/resolve/{REV}/config.json"] = b'{"architectures": ["Other"]}'
    with pytest.raises(open_models.HubError):
        open_models.EngineSteps(tmp_path, reader=tampered).pin(record, lambda: None)


def test_records_list_newest_first_and_forget_removes_one(tmp_path):
    first = open_models.write(replace(_record(tmp_path), started_at=1.0), tmp_path)
    second = open_models.write(replace(_record(tmp_path, _card("Qwen/Qwen3-8B")), started_at=2.0), tmp_path)

    assert [item.repo for item in open_models.records(tmp_path)] == [second.repo, first.repo]
    assert open_models.forget(first.repo, tmp_path) and [item.repo for item in open_models.records(tmp_path)] == [
        second.repo]


@pytest.mark.parametrize(("params", "flavor"), [(9.0, "l40sx1"), (27.0, "l40sx1"), (70.0, "a100-large"),
                                                (400.0, None)])
def test_hugging_face_s_card_for_a_model_follows_its_size(params, flavor):
    assert open_models.hugging_face_flavor(params) == flavor


def test_the_serving_estimate_matches_the_measured_9b_and_27b_within_a_tenth():
    for params, measured in ((9.0, 7_680), (27.0, 20_480)):
        assert abs(open_models.serve_estimate(params).need_mib - measured) / measured < 0.1
