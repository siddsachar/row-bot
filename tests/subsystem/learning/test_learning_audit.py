"""The audit vault (E10): sealed storage of hidden tasks, aggregates only, a novelty yes/no,
every look logged, refreshed by time, and reached by deletion and opt-out. The seal itself
(drafting, practice, training and strategy code can't read it) is a pull-request-lane test."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from row_bot import learning
from row_bot.learning import audit, evaluation
from row_bot.learning.evaluation import Arm, collect_replay
from row_bot.learning.packs import visible_tasks
from row_bot.learning.sealing import SealError
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import learning_files
from tests.fixtures.learning_eval import ScriptedClient, capture_decision, results_of, says

pytestmark = pytest.mark.subsystem

ME = __name__


def _work(writer, chats, clock, count: int, *, thread: str = "chat-1", start: int = 0) -> list[str]:
    """``count`` captured decisions, one a minute, in the order they were made."""

    made = []
    for index in range(start, start + count):
        clock.advance(60)
        made.append(capture_decision(writer, chats, thread, person=f"Errand {index}: book room {index} for Friday.",
                                     answer=AIMessage(content=f"Booked room {index}.")))
    return made


def _vault_file(root: Path) -> Path:
    return Path(root) / "learning" / "audit.db"


def test_the_vault_is_its_own_file_made_only_while_learning_is_on(data_dir, monkeypatch):
    with pytest.raises(evaluation.EvaluationError, match="learning_off"):
        evaluation.refresh_audit(asked_by="test")
    assert learning_files(data_dir) == []

    from tests.fixtures.learning import save_learning_settings

    save_learning_settings(data_dir, enabled=True)
    report = evaluation.refresh_audit(asked_by="test")
    assert report == {"considered": 0, "added": 0, "retired": 0, "held": 0, "corrections_added": 0, "corrections_held": 0, "corrections_waiting": 0}
    assert _vault_file(data_dir).is_file() and not (Path(data_dir) / "learning.db").exists()
    summary = evaluation.audit_summary(asked_by="test")
    assert summary == {"pack_tasks": 32, "replay_tasks": 0, "correction_tasks": 0, "retired_replay_tasks": 0}


def test_new_work_moves_into_the_vault_by_time_and_out_of_everything_else(writer, chats, clock):
    made = _work(writer, chats, clock, 12)
    report = evaluation.refresh_audit(asked_by="nightly")
    # Every fourth eligible decision, in the order they were made.
    assert report == {"considered": 12, "added": 3, "retired": 0, "held": 3, "corrections_added": 0, "corrections_held": 0, "corrections_waiting": 0}
    store = LearningStore(clock=clock)
    assert store.held_out_ids() == {made[3], made[7], made[11]}
    offered = {case.decision_id for case in collect_replay(store).cases}
    assert offered == set(made) - {made[3], made[7], made[11]}

    later = _work(writer, chats, clock, 5, start=12)
    assert evaluation.refresh_audit(asked_by="nightly")["added"] == 1
    assert store.held_out_ids() == {made[3], made[7], made[11], later[3]}
    # Nothing already considered is looked at again.
    assert evaluation.refresh_audit(asked_by="nightly") == {"considered": 0, "added": 0, "retired": 0, "held": 4,
                                                             "corrections_added": 0, "corrections_held": 0, "corrections_waiting": 0}


def test_the_oldest_held_tasks_retire_and_stay_held_out(writer, chats, clock, monkeypatch):
    monkeypatch.setattr(audit, "MAX_REPLAY_TASKS", 2)
    made = _work(writer, chats, clock, 16)
    assert evaluation.refresh_audit(asked_by="nightly") == {"considered": 16, "added": 4, "retired": 2, "held": 2,
                                                             "corrections_added": 0, "corrections_held": 0, "corrections_waiting": 0}
    assert evaluation.audit_summary(asked_by="t") == {"pack_tasks": 32, "replay_tasks": 2, "correction_tasks": 0,
                                                       "retired_replay_tasks": 2}
    # A retired task never comes back as a visible test or as training data.
    store = LearningStore(clock=clock)
    assert store.held_out_ids() == {made[3], made[7], made[11], made[15]}
    assert not store.held_out_ids() & {case.decision_id for case in collect_replay(store).cases}


def test_an_audit_check_hands_back_aggregates_only(writer, chats, clock):
    _work(writer, chats, clock, 8)
    evaluation.refresh_audit(asked_by="nightly")
    store = LearningStore(clock=clock)
    held = store.held_out_ids()
    client = ScriptedClient(says("Booked."))
    result = evaluation.audit_check([Arm("current"), Arm("candidate", guidance=("Be brief.",))], asked_by="strategy trial",
                                    client=client, meter=TokenMeter(10**9), packs=["document_lookups"], store=store)

    assert result.suite == "audit" and result.repeats == 3
    for arm in result.arms:
        assert arm.cases == []  # no task, id or text comes back
        # Each problem counts once (its pass fraction); each ran three times (O88).
        assert set(arm.by_kind) <= {"Document lookups", "Your work"} and sum(
            total for _, total in arm.by_kind.values()) == 8 + 2
        assert arm.total == 3 * (8 + 2)
        assert arm.run.source_thread_ids == () and arm.run.suite == "audit"
        body = results_of(store, arm.run)
        assert "cases" not in body and not any(item in str(body) for item in held)
    # Only aggregates of the paired analysis leave the vault: no problem, id or chat.
    assert not result.clusters and not any(item in json.dumps(result.analysis) for item in held)
    # Eight hidden document tasks and two held decisions, three times for each arm.
    assert len(client.calls) == 2 * 10 * 3


def test_a_proposed_practice_task_gets_a_novelty_yes_or_no(writer, chats, clock):
    _work(writer, chats, clock, 4)
    evaluation.refresh_audit(asked_by="nightly")
    assert evaluation.is_novel("Errand 3: book room 3 for Friday.", asked_by="practice") is False
    assert evaluation.is_novel("Errand 3: please book room 3 for Friday", asked_by="practice") is False
    assert evaluation.is_novel("Sort these invoices by amount and write totals.csv", asked_by="practice") is True
    # The visible packs were written apart from the hidden ones.
    assert all(evaluation.is_novel(task, asked_by="practice") for task in visible_tasks())


def test_every_look_is_logged_with_who_asked_and_what_they_got(writer, chats, clock):
    _work(writer, chats, clock, 4)
    evaluation.refresh_audit(asked_by="nightly")
    evaluation.is_novel("Something new", asked_by="practice generation")
    evaluation.audit_summary(asked_by="settings")
    looks = evaluation.audit_looks()
    assert [(look.kind, look.answer) for look in looks[:3]] == [("summary", "counts"), ("novelty", "yes"),
                                                                ("refresh", "added 1")]
    assert looks[1].asked_by == f"{ME}: practice generation" and looks[1].caller == "row_bot.learning.evaluation"
    assert all(look.at > 0 for look in looks)


def test_deleting_or_opting_out_a_chat_takes_its_tasks_out_of_the_vault(writer, chats, clock):
    from row_bot.learning import scope

    _work(writer, chats, clock, 4, thread="chat-a")
    _work(writer, chats, clock, 4, thread="chat-b", start=4)
    _work(writer, chats, clock, 4, thread="chat-c", start=8)
    evaluation.refresh_audit(asked_by="nightly")
    assert evaluation.audit_summary(asked_by="t")["replay_tasks"] == 3

    store = LearningStore(clock=clock)
    report = store.purge_thread("chat-a")
    assert report.removed_of("audit_task") == 1
    assert evaluation.audit_summary(asked_by="t")["replay_tasks"] == 2
    scope.set_chat_opt_out("chat-b", True, store=store)
    assert evaluation.audit_summary(asked_by="t")["replay_tasks"] == 1
    # A conversation that vanished without its Learning purge (a restore, a crash) is swept.
    assert store.purge_missing_threads(existing=[], older_than=clock() + 1) >= 1
    assert evaluation.audit_summary(asked_by="t")["replay_tasks"] == 0


def test_delete_learning_data_removes_the_vault(writer, chats, clock, learning_on):
    _work(writer, chats, clock, 4)
    evaluation.refresh_audit(asked_by="nightly")
    assert _vault_file(learning_on).is_file()
    report = LearningStore(clock=clock).delete_all()
    assert report.deleted and not _vault_file(learning_on).exists()
    assert not any(name.startswith("learning/audit") for name in learning_files(learning_on))


def test_every_hidden_task_is_checked_by_its_own_verifier(learning_on):
    counts = evaluation.audit_selfcheck(asked_by="pack check", include_code=False)
    assert counts == {"tasks": 24, "references_pass": 24, "decoys_fail": 24}


@pytest.mark.slow
@pytest.mark.platform
def test_every_hidden_coding_task_is_checked_by_its_tests_in_the_sandbox(learning_on):
    counts = evaluation.audit_selfcheck(asked_by="pack check", backend="local")
    assert counts == {"tasks": 32, "references_pass": 32, "decoys_fail": 32}


# ── The seal (E10): drafting, practice, training and strategy code can't read the vault ──

DENIED = ("row_bot.learning.diagnosis", "row_bot.learning.practice.generate", "row_bot.learning.training.dataset",
          "row_bot.learning.improver.meta")


def _run_as(module_name: str, code: str, **names: object) -> dict[str, object]:
    module = types.ModuleType(module_name)
    module.__dict__.update(names)
    exec(compile(code, f"<{module_name}>", "exec"), module.__dict__)  # noqa: S102 - the test plays that module
    return module.__dict__


@pytest.mark.parametrize("module_name", DENIED)
def test_drafting_practice_training_and_strategy_code_cannot_read_the_vault(module_name, learning_on):
    evaluation.refresh_audit(asked_by="nightly")
    for code in ("from row_bot.learning.audit import AuditVault",
                 "import row_bot.learning.audit as vault\nvault.AuditVault",
                 "from row_bot.learning import audit\naudit.forget_threads(['x'])",
                 "from row_bot.learning.packs.hidden import TASKS",
                 "import row_bot.learning.packs.hidden as hidden\nhidden.TASKS"):
        with pytest.raises(SealError) as refused:
            _run_as(module_name, code)
        assert refused.value.caller == module_name
    # A reader taken out of the module some other way still refuses them.
    with pytest.raises(SealError):
        _run_as(module_name, "AuditVault()", AuditVault=vars(audit)["AuditVault"])
    assert any(look.kind == "refused" and look.caller == module_name for look in evaluation.audit_looks())


@pytest.mark.parametrize("module_name", DENIED)
def test_a_first_import_from_denied_code_fails(module_name, monkeypatch):
    for sealed in ("row_bot.learning.audit", "row_bot.learning.packs.hidden"):
        monkeypatch.delitem(sys.modules, sealed, raising=False)
        with pytest.raises(ImportError):
            _run_as(module_name, f"import {sealed}")
        assert sealed not in sys.modules


def test_other_code_may_only_delete_and_evaluation_may_read(learning_on):
    with pytest.raises(SealError):
        _run_as("row_bot.learning.runner", "from row_bot.learning.audit import AuditVault")
    with pytest.raises(SealError):
        audit.AuditVault  # noqa: B018 - this test module is not a reader
    assert _run_as("row_bot.learning.runner", "from row_bot.learning import audit\nn = audit.forget_threads(['x'])")[
        "n"] == 0
    assert audit.vault_threads() == [] and audit.forget_threads(["x"]) == 0
    # What drafting code may ask evaluation for is aggregates and a yes/no.
    namespace = _run_as("row_bot.learning.practice.generate",
                        "from row_bot.learning import evaluation\nanswer = evaluation.is_novel('Count the vowels in a "
                        "word list and print them', asked_by='practice')")
    assert namespace["answer"] is True


def test_learning_settings_off_keeps_the_vault_closed_but_readable_for_deletion(writer, chats, clock, learning_on):
    from tests.fixtures.learning import save_learning_settings

    _work(writer, chats, clock, 4)
    evaluation.refresh_audit(asked_by="nightly")
    save_learning_settings(learning_on, enabled=False)
    assert not learning.is_enabled()
    # Turning Learning off keeps the data: the vault still answers, and deletion still reaches it.
    assert evaluation.audit_summary(asked_by="t")["replay_tasks"] == 1
    LearningStore(clock=clock).purge_thread("chat-1")
    assert evaluation.audit_summary(asked_by="t")["replay_tasks"] == 0
