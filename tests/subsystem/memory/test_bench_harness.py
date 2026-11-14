"""The public-benchmark harness behaves without any provider: caps, traces, network hook, bookings,
the memory benchmarks' tasks and judges, and ingestion through Row-Bot's real memory path.

The live runs are opt-in (``tests/e2e/test_memory_bench.py``, marker ``e2e``). These use
fakes, made-up benchmark files and no network.
"""
from __future__ import annotations

import json
import socket
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import scipy.stats  # noqa: F401 - imported once, at collection: BEAM's event ordering uses its Kendall tau
from langchain_core.messages import AIMessage

import row_bot.agent as agent  # once, at collection: the turn test drives and patches it
from tests.e2e.bench import booking, netguard, orchestrate
from tests.e2e.bench.ledger import BudgetStop, CallEntry, CostLedger, LedgerCaps, UsageBook, totals
from tests.e2e.bench.memory import full_context, v2_metrics
from tests.e2e.bench.memory import judge as scoring
from tests.e2e.bench.memory.datasets import (
    MemoryTask,
    Question,
    Session,
    Turn,
    audit_review,
    beam_tasks,
    locomo_tasks,
    longmemeval_tasks,
    longmemeval_v2_tasks,
    parse_longmemeval_date,
)
from tests.e2e.bench.memory.protocols import ProtocolError, load as load_protocol, load_locomo, load_longmemeval
from tests.e2e.bench.runtime import BenchConfig, BenchEnvironmentError, check_environment, child_env
from tests.e2e.bench.trajectory import Step, Trajectory, append_jsonl, read_jsonl, to_atif
from tests.e2e.live_eval.pricing import Rates

pytestmark = pytest.mark.subsystem


def _ledger(tmp_path: Path, *, day: str = "2026-11-14", **caps) -> CostLedger:
    book = UsageBook(tmp_path / "book.json", today=lambda: day)
    return CostLedger(LedgerCaps(**caps), book, rates={"openai:gpt-4o-mini": Rates(input=0.15, output=0.6)},
                      path=tmp_path / "ledger.jsonl")


# ── Ledger ──────────────────────────────────────────────────────────────────


def test_a_paid_call_is_refused_while_the_paid_cap_is_zero(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    with pytest.raises(BudgetStop, match="paid cap"):
        ledger.check("openai", "gpt-4o-mini", 1_000, 100)
    ledger.check("ollama", "qwen3.6:27b", 10_000_000, 10_000)  # local: free, never held
    assert ledger.refused and "openai:gpt-4o-mini" in ledger.refused[0]


def test_a_paid_call_needs_a_rate_and_room_under_both_caps(tmp_path) -> None:
    ledger = _ledger(tmp_path, max_usd=1.0, project_usd=100.0)
    ledger.check("openai", "gpt-4o-mini", 100_000, 1_000)  # about $0.016
    with pytest.raises(BudgetStop, match="no rate"):
        ledger.check("anthropic", "claude-opus-5-5", 10, 10)
    with pytest.raises(BudgetStop, match="estimated"):
        ledger.check("openai", "gpt-4o-mini", 10_000_000, 0)  # $1.50 > $1.00
    ledger.record(CallEntry("openai", "gpt-4o-mini", input_tokens=1_000_000, output_tokens=0))
    assert ledger.entries[-1].cost_usd == pytest.approx(0.15)
    # The project cap holds across runs: a new ledger on the same book has $0.05 left.
    later = CostLedger(LedgerCaps(max_usd=10.0, project_usd=0.2), ledger.book,
                       rates={"openai:gpt-4o-mini": Rates(input=0.15, output=0.6)})
    assert later.paid_room() == pytest.approx(0.05)
    with pytest.raises(BudgetStop):
        later.check("openai", "gpt-4o-mini", 400_000, 0)


def test_plan_calls_cost_nothing_are_never_capped_and_are_counted_per_day(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    ledger.check("codex", "gpt-6.1-sol", 50_000_000, 1_000)  # no plan cap (the owner's choice)
    entry = ledger.record(CallEntry("codex", "gpt-6.1-sol", input_tokens=6_000, output_tokens=1_000))
    assert (entry.billing, entry.cost_usd) == ("plan", 0.0)
    assert ledger.book.plan_today() == 7_000 and ledger.book.paid_total() == 0.0
    assert UsageBook(tmp_path / "book.json", today=lambda: "2026-11-15").plan_today() == 0
    rows = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["billing"] == "plan" and rows[0]["input_tokens"] == 6_000
    assert totals(ledger.entries)["plan"]["input_tokens"] == 6_000


def test_caps_come_from_the_environment_and_default_to_no_paid_calls() -> None:
    assert LedgerCaps.from_env({}).max_usd == 0.0
    caps = LedgerCaps.from_env({"ROW_BOT_BENCH_MAX_USD": "12.5", "ROW_BOT_BENCH_PROJECT_USD": "50"})
    assert (caps.max_usd, caps.project_usd) == (12.5, 50.0)
    with pytest.raises(ValueError):
        LedgerCaps.from_env({"ROW_BOT_BENCH_MAX_USD": "-1"})


# ── Trajectories ────────────────────────────────────────────────────────────


def test_trajectories_round_trip_as_jsonl_are_redacted_and_export_to_atif(tmp_path) -> None:
    trajectory = Trajectory("locomo_0_a", "locomo", config={"model": "gpt-6.1-sol"})
    ingest = Step(kind="ingest", name="session_1", seconds=2.5,
                  calls=[CallEntry("codex", "gpt-6.1-sol", purpose="extraction", input_tokens=900, output_tokens=80)])
    turn = Step(kind="turn", name="q1", prompt="When did Ana move?", answer="In May 2023 (secret-token).",
                tool_calls=[{"name": "search_memory", "args": {"query": "Ana move"}}], seconds=4.0,
                calls=[CallEntry("codex", "gpt-6.1-sol", purpose="chat", input_tokens=12_000, output_tokens=150,
                                 cache_read_tokens=9_000)])
    trajectory.steps += [ingest, turn]
    path = tmp_path / "trajectories.jsonl"
    append_jsonl(path, trajectory, redact=lambda text: text.replace("secret-token", "[redacted]"))
    (loaded,) = read_jsonl(path)
    assert loaded.steps[1].answer == "In May 2023 ([redacted])."
    assert loaded.summary()["by_step_kind"]["ingest"]["input_tokens"] == 900
    assert loaded.summary()["by_billing"]["plan"]["calls"] == 2
    atif = to_atif(loaded)
    assert [step["source"] for step in atif["steps"]] == ["user", "agent"]
    assert atif["steps"][1]["tool_calls"][0]["function_name"] == "search_memory"
    assert atif["final_metrics"]["prompt_tokens"] == 12_900


# ── Network hook ────────────────────────────────────────────────────────────


def test_the_network_policy_blocks_answer_sites_and_canaries_and_undoes_itself() -> None:
    policy = netguard.NetworkPolicy(blocked_hosts=("huggingface.co",), canaries=("CANARY-1234",))
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok")))
    undo = netguard.install(policy)
    try:
        with pytest.raises(netguard.NetworkBlocked):
            client.get("https://datasets.huggingface.co/locomo")
        with pytest.raises(netguard.NetworkBlocked, match="canary"):
            client.post("https://api.example.test/v1", content=b"the answer is canary-1234")
        assert client.post("https://api.example.test/v1", content=b"fine").text == "ok"
        with pytest.raises(netguard.NetworkBlocked):
            socket.getaddrinfo("huggingface.co", 443)
    finally:
        undo()
    assert client.get("https://huggingface.co/x").text == "ok"
    assert policy.to_dict() == {"blocked_hosts": ["huggingface.co"], "canaries": 1, "allow_hosts": None,
                                "refused": 3}


def test_an_allow_list_refuses_every_other_host_but_loopback() -> None:
    policy = netguard.NetworkPolicy(allow_hosts=("chatgpt.com",))
    assert policy.host_reason("chatgpt.com") == ""
    assert policy.host_reason("127.0.0.1") == ""
    assert "allow list" in policy.host_reason("github.com")


# ── Bookings ────────────────────────────────────────────────────────────────


BOOKINGS = """\
08:28 lane-freeze EXCLUSIVE START v2 freeze run (expect ~9 h)
08:30 lane-memory note: paused
14:29 lane-freeze EXCLUSIVE END
15:07 lane-B START SA3 stale skills (expect up to 40 min, 12 GB)
15:10 lane-L START ChatGPT eval-profile replay batch (expect 30 min)
"""


def test_open_bookings_are_read_from_the_shared_file(tmp_path) -> None:
    held = {(item.lane, item.kind) for item in booking.open_bookings(BOOKINGS)}
    assert held == {("lane-B", booking.GPU), ("lane-L", booking.EVAL_PROFILE)}
    path = tmp_path / "gpu-booking.txt"
    path.write_bytes(BOOKINGS.encode())
    book = booking.BookingFile(path, now=lambda: datetime(2026, 11, 14, 16, 0))
    assert [item.lane for item in book.blockers(booking.EVAL_PROFILE)] == ["lane-L"]
    path.write_bytes((BOOKINGS + "15:40 lane-L END ChatGPT eval-profile replay batch\n").encode())
    assert book.blockers(booking.EVAL_PROFILE) == []
    book.start(booking.EVAL_PROFILE, "locomo smoke", "20 min")
    book.end(booking.EVAL_PROFILE, "locomo smoke")
    tail = path.read_bytes().decode().splitlines()[-2:]
    assert tail == ["16:00 lane-bench START ChatGPT eval-profile locomo smoke (expect 20 min)",
                    "16:00 lane-bench END ChatGPT eval-profile locomo smoke"]
    assert b"\r\n" not in path.read_bytes()
    assert booking.open_bookings(path.read_text()) and all(item.lane != "lane-bench"
                                                            for item in booking.open_bookings(path.read_text()))


def test_waiting_for_the_card_respects_other_lanes_quiet_hours_and_the_stop_file(tmp_path) -> None:
    path = tmp_path / "gpu-booking.txt"
    path.write_bytes(b"10:00 lane-B EXCLUSIVE START training\n")
    moments = iter([datetime(2026, 11, 14, 2, 0)] * 3 + [datetime(2026, 11, 14, 9, 0)] * 10)
    book = booking.BookingFile(path, now=lambda: next(moments))
    said: list[str] = []
    polls = []

    def sleep(_seconds: float) -> None:
        polls.append(1)
        if len(polls) == 1:
            path.write_bytes(path.read_bytes() + b"10:30 lane-B EXCLUSIVE END\n")

    assert book.wait_for(booking.GPU, stop=lambda: False, sleep=sleep, log=said.append)
    assert said[0].startswith("waiting: lane-B") and "quiet hours" in said[1]
    assert not book.wait_for(booking.GPU, stop=lambda: True, sleep=sleep, log=said.append)


# ── Datasets ────────────────────────────────────────────────────────────────


def _locomo_file(tmp_path: Path) -> tuple[Path, Path]:
    sample = {
        "sample_id": "conv-x",
        "conversation": {
            "speaker_a": "Ana", "speaker_b": "Ben",
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [
                {"speaker": "Ana", "dia_id": "D1:1", "text": "I joined a pottery class yesterday."},
                {"speaker": "Ben", "dia_id": "D1:2", "text": "Nice! I adopted a dog.", "blip_caption": "a puppy"},
            ],
            "session_2_date_time": "9:55 am on 22 October, 2023",
            "session_2": [{"speaker": "Ben", "dia_id": "D2:1", "text": "Bennie and Ana went hiking."},
                          {"speaker": "Ana", "dia_id": "D2:2", "text": "It was a long hike."}],
        },
        "qa": [
            {"question": "When did Ana join a pottery class?", "answer": "7 May 2023", "evidence": ["D1:1"],
             "category": 2},
            {"question": "What did Ben adopt?", "answer": "A cat", "evidence": ["D1:2"], "category": 4},
            {"question": "Did Ben and Ana hike?", "answer": "Yes", "evidence": ["D2:1"], "category": 1},
            {"question": "What is the weather?", "answer": "Sunny", "evidence": [], "category": 3},
            {"question": "Did Ana adopt a dog?", "adversarial_answer": "Yes", "evidence": [], "category": 5},
        ],
    }
    errors = [
        {"question_id": "locomo_0_qa0", "error_type": "WRONG_CITATION", "correct_answer": "7 May 2023 (cite D1:1)"},
        {"question_id": "locomo_0_qa1", "error_type": "HALLUCINATION", "correct_answer": "A dog"},
    ]
    data, key = tmp_path / "locomo10.json", tmp_path / "errors.json"
    data.write_text(json.dumps([sample]), encoding="utf-8")
    key.write_text(json.dumps(errors), encoding="utf-8")
    return data, key


def test_locomo_reads_each_speaker_as_the_person_and_routes_questions_by_name(tmp_path) -> None:
    data, key = _locomo_file(tmp_path)
    ana, ben = locomo_tasks(data, key)
    assert (ana.person, ben.person) == ("Ana", "Ben")
    assert [turn.role for turn in ana.sessions[0].turns] == ["user", "assistant"]
    assert [turn.role for turn in ben.sessions[0].turns] == ["assistant", "user"]
    assert ben.sessions[0].turns[1].text == "Nice! I adopted a dog. [shared an image: a puppy]"
    assert ana.sessions[0].when == datetime(2023, 5, 8, 13, 56)
    assert ana.ask_at == datetime(2023, 10, 23, 9, 55)
    # "Ben and Ana" names Ben first; "Bennie" is not Ben; no name goes to speaker A; category 5 is left out.
    assert [item.question for item in ana.questions] == ["When did Ana join a pottery class?", "What is the weather?"]
    assert [item.question_id for item in ben.questions] == ["locomo_0_qa1", "locomo_0_qa2"]
    # The audited key: a citation-only error keeps the answer, every other error replaces it.
    assert ana.questions[0].audited_answer == "7 May 2023"
    assert ben.questions[0].audited_answer == "A dog" and ben.questions[0].answer == "A cat"
    (only_a,) = locomo_tasks(data, key, perspectives="a", categories=(1, 2, 3, 4, 5))
    assert len(only_a.questions) == 5 and only_a.questions[-1].abstention
    assert MemoryTask.from_dict(json.loads(json.dumps(ana.to_dict()))) == ana


def test_longmemeval_gives_each_question_its_own_dated_haystack(tmp_path) -> None:
    instance = {
        "question_id": "q7_abs", "question_type": "temporal-reasoning", "question": "How long ago did I move?",
        "answer": "The move was never mentioned.", "question_date": "2023/06/01 (Thu) 10:00",
        "haystack_session_ids": ["s2", "s1"], "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/01 (Mon) 09:05"],
        "haystack_sessions": [[{"role": "user", "content": "later"}, {"role": "assistant", "content": "ok"}],
                              [{"role": "user", "content": "earlier", "has_answer": True}]],
        "answer_session_ids": ["s1"],
    }
    path = tmp_path / "longmemeval_s_cleaned.json"
    path.write_text(json.dumps([instance, {**instance, "question_id": "q8"}]), encoding="utf-8")
    (task,) = longmemeval_tasks(path, limit=1)
    assert [session.session_id for session in task.sessions] == ["s1", "s2"]  # in time order
    assert task.ask_at == datetime(2023, 6, 1, 10, 0)
    assert task.questions[0].abstention and task.questions[0].category == "temporal-reasoning"
    assert [item.task_id for item in longmemeval_tasks(path, question_ids=["q8"])] == ["lme_q8"]
    with pytest.raises(ValueError):
        parse_longmemeval_date("May 1")


# ── Judges ──────────────────────────────────────────────────────────────────


def test_the_locomo_judge_is_read_from_the_benchmarks_own_file(tmp_path) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text("llm_judge:\n  system_prompt: |\n    Grade it.\n  user_prompt: |\n"
                    "    Q: {question}\n    Gold: {golden_answer}\n    Got: {generated_answer}\n", encoding="utf-8")
    protocol = load_locomo(path)
    assert protocol.messages(question="When?", gold="May", response="In May") == [
        ("system", "Grade it."), ("user", "Q: When?\nGold: May\nGot: In May\n")]
    assert protocol.verdict('It matches.\n{"label": "CORRECT"}') is True
    assert protocol.verdict("WRONG") is False and protocol.verdict("no idea") is None
    assert len(protocol.sha256) == 64 and protocol.published_judge == "openai:gpt-4o-mini"
    path.write_text("llm_judge:\n  system_prompt: x\n  user_prompt: 'no fields'\n", encoding="utf-8")
    with pytest.raises(ProtocolError):
        load_locomo(path)


LONGMEMEVAL_SOURCE = '''
def get_anscheck_prompt(task, question, answer, response, abstention=False):
    if not abstention:
        if task in ['single-session-user', 'single-session-assistant', 'multi-session']:
            template = "BASIC {} | {} | {}"
            prompt = template.format(question, answer, response)
        elif task == 'temporal-reasoning':
            template = "TEMPORAL {} | {} | {}"
            prompt = template.format(question, answer, response)
        elif task == 'knowledge-update':
            template = "UPDATE {} | {} | {}"
            prompt = template.format(question, answer, response)
        elif task == 'single-session-preference':
            template = "PREFERENCE {} | {} | {}"
            prompt = template.format(question, answer, response)
        else:
            raise NotImplementedError
    else:
        template = "ABSTAIN {} | {} | {}"
        prompt = template.format(question, answer, response)
    return prompt

raise SystemExit("never run")
'''


def test_the_longmemeval_judge_templates_are_parsed_never_run(tmp_path) -> None:
    path = tmp_path / "evaluate_qa.py"
    path.write_text(LONGMEMEVAL_SOURCE, encoding="utf-8")
    protocol = load_longmemeval(path)
    assert protocol.messages(question="Q", gold="G", response="R", category="multi-session") == [
        ("user", "BASIC Q | G | R")]
    assert protocol.messages(question="Q", gold="G", response="R", category="knowledge-update",
                             abstention=True)[0][1] == "ABSTAIN Q | G | R"
    assert (protocol.temperature, protocol.max_tokens) == (0.0, 10)
    assert protocol.verdict("Yes.") is True and protocol.verdict("no") is False
    path.write_text(LONGMEMEVAL_SOURCE.replace("elif task == 'knowledge-update'", "elif task == 'other'"),
                    encoding="utf-8")
    with pytest.raises(ProtocolError, match="knowledge-update"):
        load_longmemeval(path)


def test_answers_are_judged_on_both_keys_and_unfinished_turns_count_as_wrong(tmp_path) -> None:
    (tmp_path / "prompts.yaml").write_text(
        "llm_judge:\n  system_prompt: s\n  user_prompt: '{question}|{golden_answer}|{generated_answer}'\n",
        encoding="utf-8")
    protocol = load_locomo(tmp_path / "prompts.yaml")
    questions = {
        "q1": Question("q1", "What did Ben adopt?", "A cat", "single-hop", audited_answer="A dog"),
        "q2": Question("q2", "When?", "May", "temporal", audited_answer="May"),
        "q3": Question("q3", "Where?", "Rome", "single-hop", audited_answer="Rome"),
    }
    answers = [scoring.Answer("q1", "A dog"), scoring.Answer("q2", "In May"), scoring.Answer("q3", "", status="budget")]
    calls: list[str] = []

    def judge(messages):
        _question, gold, response = messages[-1][1].split("|")
        calls.append(gold)
        return '{"label": "CORRECT"}' if gold.split()[-1].lower() in response.lower() else '{"label": "WRONG"}'

    found = scoring.judge_answers(answers, questions, protocol, judge, judge="model:codex:x",
                                  keys=("original", "audited"))
    assert calls == ["A cat", "A dog", "May"]  # q2's gold is the same on both keys: judged once; q3 not at all
    original = scoring.accuracy(found, key="original", judge="model:codex:x")
    audited = scoring.accuracy(found, key="audited", judge="model:codex:x")
    assert (original["accuracy"], audited["accuracy"]) == (0.3333, 0.6667)
    assert original["unanswered_judgments"] == 1
    assert audited["by_category"]["single-hop"] == {"questions": 2, "accuracy": 0.5}


def test_the_second_judge_runs_on_a_fixed_subset_and_reports_agreement() -> None:
    ids = [f"q{index}" for index in range(50)]
    assert scoring.subset(ids, 10) == scoring.subset(list(reversed(ids)), 10)
    assert len(scoring.subset(ids, 10)) == 10
    judgments = []
    for index, question_id in enumerate(ids[:10]):
        judgments.append(scoring.Judgment(question_id, "original", "A", 0, float(index < 6)))
        judgments.append(scoring.Judgment(question_id, "original", "B", 0, 0.5 if index < 4 or index == 9 else 0.0))
    result = scoring.sensitivity(judgments, key="original", primary="A", secondary="B")
    assert result["questions"] == 10 and result["agreement"] == 0.7
    assert (result["primary_accuracy"], result["secondary_accuracy"]) == (0.6, 0.5)
    assert result["cohen_kappa"] == pytest.approx(0.4, abs=1e-4)


def test_the_audit_review_lists_every_answer_the_audited_key_changes(tmp_path) -> None:
    data, key = _locomo_file(tmp_path)
    (row,) = audit_review(data, key)  # the citation-only entry changes no answer
    assert (row["question_id"], row["original"], row["audited"], row["error_type"]) == (
        "locomo_0_qa1", "A cat", "A dog", "HALLUCINATION")
    assert row["reviewed"] is None


# ── BEAM ────────────────────────────────────────────────────────────────────


def _beam_chats(tmp_path: Path) -> Path:
    root = tmp_path / "chats" / "1M"
    folder = root / "1"
    (folder / "probing_questions").mkdir(parents=True)
    chat = [
        {"batch_number": 1, "time_anchor": None, "turns": [[
            {"role": "user", "id": 0, "time_anchor": "March-01-2024", "content": "I started a translation API."},
            {"role": "assistant", "id": 1, "content": "Here is how to call it."}]]},
        {"batch_number": 2, "time_anchor": None, "turns": [[
            {"role": "user", "id": 2, "time_anchor": "March-12-2024", "content": "Now I cache its results."},
            {"role": "assistant", "id": 3, "content": "Good idea."}]]},
    ]
    probing = {
        "event_ordering": [{"question": "In what order did I work on these?", "answer": "API, then cache",
                            "rubric": ["translation API", "caching", "deployment"]}],
        "information_extraction": [{"question": "What did I build first?", "answer": "A translation API",
                                    "rubric": ["translation API", "error handling"]},
                                   {"question": "Second?", "answer": "x", "rubric": ["y"]}],
    }
    (folder / "chat.json").write_text(json.dumps(chat), encoding="utf-8")
    (folder / "probing_questions" / "probing_questions.json").write_text(json.dumps(probing), encoding="utf-8")
    return root


BEAM_PROMPTS = 'other = 1\nunified_llm_judge_base_prompt = """Rubric item: <rubric_item>\nResponse: <llm_response>\n' \
               'Reply as JSON {"score": 1.0 or 0.5 or 0.0, "reason": "..."}"""\n'
BEAM_METRICS = '''
def llm_equivalence(first_paragraph, second_paragraph, llm):
    system_msg = {"role": "system", "content": """Reply YES or NO."""}
    user_msg = {"role": "user", "content": f"""First: {first_paragraph} \\n Second: {second_paragraph}"""}
    raise SystemExit("never run")
'''


def test_beam_reads_each_conversations_dated_batches_and_rubric_questions(tmp_path) -> None:
    (task,) = beam_tasks(_beam_chats(tmp_path), questions_per_category=1)
    assert [(session.session_id, session.when) for session in task.sessions] == [
        ("batch_1", datetime(2024, 3, 1, 9)), ("batch_2", datetime(2024, 3, 12, 9))]
    assert [turn.role for turn in task.sessions[0].turns] == ["user", "assistant"]
    assert task.ask_at == datetime(2024, 3, 13, 9)
    assert [(item.question_id, item.category) for item in task.questions] == [
        ("beam_1_event_ordering_0", "event_ordering"), ("beam_1_information_extraction_0", "information_extraction")]
    assert task.questions[1].rubric == ["translation API", "error handling"]


def test_the_beam_judge_scores_rubric_items_and_orders_events_by_kendall_tau(tmp_path) -> None:
    (tmp_path / "prompts.py").write_text(BEAM_PROMPTS, encoding="utf-8")
    (tmp_path / "compute_metrics.py").write_text(BEAM_METRICS, encoding="utf-8")
    protocol = load_protocol("beam-1m", f"{tmp_path / 'prompts.py'},{tmp_path / 'compute_metrics.py'}")
    assert protocol.headline == "categories" and protocol.published_judge == "openai:gpt-4.1-mini"
    task = beam_tasks(_beam_chats(tmp_path))[0]
    ordering, extraction = task.questions[0], task.questions[1]

    def judge(messages):
        if messages[0][0] == "system":  # the equivalence check
            first, second = (part.split(":", 1)[1].strip() for part in messages[1][1].split("\n"))
            return "YES" if first == second else "NO"
        item = messages[0][1].split("Rubric item: ", 1)[1].split("\n", 1)[0]
        return '{"score": 1.0, "reason": "ok"}' if item == "translation API" else '```json\n{"score": 0.5}\n```'

    verdict = protocol.judge(extraction, "I built a translation API.", "", judge)
    assert verdict.score == 0.75 and verdict.detail["rubric_scores"] == [1.0, 0.5]
    ordered = protocol.judge(ordering, "caching\ntranslation API\ndeployment", "", judge)
    assert ordered.detail["f1"] == 1.0  # every line matched one rubric event
    assert ordered.score == pytest.approx(2 / 3)  # tau-b 1/3 for one swapped pair, normalised
    assert ordered.detail["llm_judge_score"] == pytest.approx((1.0 + 0.5 + 0.5) / 3)


# ── LongMemEval-V2 ──────────────────────────────────────────────────────────


def _v2_root(tmp_path: Path) -> Path:
    root = tmp_path / "lme-v2"
    root.mkdir()
    questions = [
        {"id": "w1", "domain": "web", "question_type": "static-environment", "question": "Which filters? \\boxed{}",
         "image": None, "answer": "Incident Mobile, Incident Portal",
         "eval_function": "norm_phrase_set_match|lower=true|strip_punct=true|separators=,;|require_non_empty=true"},
        {"id": "w2", "domain": "web", "question_type": "static-environment-abs", "question": "Why is X red?",
         "image": "question_screenshots/w2.png", "answer": "X is never red.", "eval_function": "llm_abstention_checker"},
        {"id": "e1", "domain": "enterprise", "question_type": "procedure", "question": "Pick one.", "image": None,
         "answer": "B", "eval_function": "mc_choice_match"},
    ]
    (root / "questions.jsonl").write_text("\n".join(json.dumps(item) for item in questions), encoding="utf-8")
    (root / "haystacks").mkdir()
    (root / "haystacks" / "lme_v2_small.json").write_text(json.dumps(
        {"w1": ["t2", "t1"], "w2": ["t2", "t1"], "e1": ["t3"]}), encoding="utf-8")
    trajectories = [{"id": f"t{index}", "domain": "web" if index < 3 else "enterprise", "goal": f"goal {index}",
                     "outcome": "success", "start_url": "https://shop.test/",
                     "states": [{"state_index": 0, "step": 0, "url": "https://shop.test/", "action": None,
                                 "thought": "", "accessibility_tree": f"RootWebArea 'page {index}'"},
                                {"state_index": 1, "step": 1, "url": "https://shop.test/b", "action": "click('7')",
                                 "thought": "open it", "accessibility_tree": "[7] button 'Filters'"}]}
                    for index in (1, 2, 3, 4)]
    (root / "trajectories.jsonl").write_text("\n".join(json.dumps(item) for item in trajectories), encoding="utf-8")
    return root


V2_METRICS = '''
_ABSTENTION_JUDGE_SYSTEM_PROMPT = ("You grade flawed-premise answers. " "Be strict.")
_GOTCHAS_JUDGE_SYSTEM_PROMPT = "You grade gotchas."
def _build_abstention_judge_messages(*, question_text, reference_answer, model_full_response, model_final_answer):
    user_prompt = (
        "Q:\\n" f"{question_text}\\n" "Ref:\\n" f"{reference_answer}\\n" "Full:\\n" f"{model_full_response}\\n"
        "Final:\\n" f"{model_final_answer}\\n" 'Output JSON only: {"label": 0 or 1, "reason": "..."}'
    )
def _build_gotchas_judge_messages(*, question_text, reference_answer, model_full_response, model_final_answer):
    user_prompt = (f"{question_text}|{reference_answer}|{model_full_response}|{model_final_answer}")
def norm_phrase_set_match(): pass
def norm_phrase_set_match_ordered(): pass
def mc_choice_match(): pass
def mc_choice_set_match(): pass
def extract_boxed_answer(): pass
def normalize_phrase(): pass
def split_phrases(): pass
raise SystemExit("never run")
'''


def test_longmemeval_v2_reads_one_task_per_haystack_with_trajectories_as_conversations(tmp_path) -> None:
    web, enterprise = longmemeval_v2_tasks(_v2_root(tmp_path))
    assert (web.task_id, enterprise.task_id) == ("lmev2_small_web", "lmev2_small_enterprise")
    assert [session.session_id for session in web.sessions] == ["t2", "t1"]  # haystack order
    assert [item.question_id for item in web.questions] == ["w1", "w2"]
    assert web.questions[1].abstention and web.questions[1].image == "question_screenshots/w2.png"
    turns = web.sessions[0].turns
    assert turns[0].role == "user" and turns[0].text.startswith("Task: goal 2")
    assert "Action: click('7')" in turns[2].text and "[7] button 'Filters'" in turns[2].text
    assert turns[-1].text == "Outcome: success"
    assert [session.session_id for session in enterprise.sessions] == ["t3"]


def test_the_longmemeval_v2_scorers_match_the_published_ones(tmp_path) -> None:
    (tmp_path / "qa_eval_metrics.py").write_text(V2_METRICS, encoding="utf-8")
    protocol = load_protocol("longmemeval-v2", tmp_path / "qa_eval_metrics.py")
    web, enterprise = longmemeval_v2_tasks(_v2_root(tmp_path))
    phrase, abstain, choice = web.questions[0], web.questions[1], enterprise.questions[0]
    never = lambda _messages: pytest.fail("a deterministic scorer called the judge")  # noqa: E731
    assert protocol.judge(phrase, "It shows \\boxed{incident-portal; Incident Mobile}", phrase.answer, never).score == 1.0
    assert protocol.judge(phrase, "\\boxed{Incident Mobile}", phrase.answer, never).score == 0.0
    assert protocol.judge(phrase, "\\boxed{UNKNOWN}", phrase.answer, never).detail["unknown"]
    assert protocol.judge(choice, "I'd say \\boxed{Option B.}", "B", never).score == 1.0
    seen: list[list[tuple[str, str]]] = []

    def judge(messages):
        seen.append(messages)
        return '```\n{"label": 1, "reason": "names the flaw"}\n```'

    verdict = protocol.judge(abstain, "The premise is wrong: \\boxed{X is never red}", abstain.answer, judge)
    assert verdict.score == 1.0
    assert seen[0][0] == ("system", "You grade flawed-premise answers. Be strict.")
    assert "Final:\nX is never red\n" in seen[0][1][1] and '{"label": 0 or 1' in seen[0][1][1]
    assert protocol.judge(abstain, "no idea", abstain.answer, lambda _m: "no verdict").score is None
    assert v2_metrics.norm_phrase_set_match_ordered("reports then problems", "Reports;Problems", separators=";")
    assert not v2_metrics.norm_phrase_set_match_ordered("problems then reports", "Reports;Problems", separators=";")
    assert v2_metrics.mc_choice_set_match("Answer: A and C", "C, A")
    assert v2_metrics.extract_boxed_answer("a \\boxed{x} b \\boxed{{y}}") == "{y}"
    assert v2_metrics.parse_spec("norm_phrase_set_match|lower=true|separators=,;")[1] == {
        "lower": True, "separators": [",", ";"]}


# ── The full-context baseline ───────────────────────────────────────────────


class _CharTokens:
    """A tokenizer double: one token per character (the real one is tiktoken's o200k_base)."""

    def encode(self, text, disallowed_special=()):
        return list(text)

    def decode(self, tokens):
        return "".join(tokens)


def _history_task(benchmark: str) -> MemoryTask:
    sessions = [Session(f"s{index}", datetime(2023, 5, index, 10), [Turn("user", f"fact {index} " + "x" * 80)])
                for index in (1, 2, 3)]
    return MemoryTask("t", benchmark, sessions, [Question("q1", "What was fact 3?", "3", "multi-session")],
                      ask_at=datetime(2023, 6, 1, 9))


def test_the_full_context_history_is_cut_by_each_benchmarks_own_rule(monkeypatch) -> None:
    monkeypatch.setattr(full_context, "_encoding", lambda: _CharTokens())
    text, info = full_context.build_history(_history_task("locomo"), 100_000)
    assert not info["truncated"] and text.count("### Session") == 3 and "user: fact 1" in text
    latest, info = full_context.build_history(_history_task("beam-1m"), 260)
    assert info["truncated"] and info["kept_sessions"] == 2 and "fact 1" not in latest and "fact 3" in latest
    earliest, info = full_context.build_history(_history_task("longmemeval-v2"), 260)
    assert info["kept_sessions"] == 2 and "fact 1" in earliest and "fact 3" not in earliest
    prefix, info = full_context.build_history(_history_task("longmemeval-s"), 150)
    assert len(prefix) == 150 and prefix.startswith("### Session 1") and info["kept_tokens"] == 150


def test_the_full_context_baseline_asks_the_same_model_with_the_history_and_no_memory(monkeypatch) -> None:
    monkeypatch.setattr(full_context, "_encoding", lambda: _CharTokens())
    prompts: list[str] = []

    def call(prompt: str) -> str:
        prompts.append(prompt)
        return "Fact 3."

    runtime = SimpleNamespace(drain_calls=lambda step, phase="": [])
    (step,) = full_context.ask(runtime, _history_task("longmemeval-s"), call, context_tokens=200_000)
    assert step.answer == "Fact 3." and step.metrics["mode"] == "full-context" and not step.metrics["truncated"]
    assert step.metrics["template_sha256"] == full_context.TEMPLATE_SHA256
    assert prompts[0].endswith("Current date: Thursday, 01 June 2023, 09:00\nQuestion: What was fact 3?")
    assert prompts[0].index("fact 1") < prompts[0].index("fact 3")

    def limited(_prompt: str) -> str:
        raise RuntimeError("429 Too Many Requests: usage limit")

    from tests.e2e.bench.runtime import ProviderLimit

    with pytest.raises(ProviderLimit):
        full_context.ask(runtime, _history_task("longmemeval-s"), limited, context_tokens=200_000)


def test_the_local_model_is_the_stock_qwen_and_its_digest_is_recorded() -> None:
    from tests.e2e.bench.runtime import LOCAL_MODEL, ollama_identity

    calls: list[str] = []

    class Ollama:
        def get(self, url):
            calls.append(url)
            return SimpleNamespace(json=lambda: {"models": [
                {"name": "ofix-personal-27b:latest", "digest": "f" * 64},
                {"name": LOCAL_MODEL, "model": LOCAL_MODEL, "digest": "a" * 64, "size": 17_000_000_000}]})

        def post(self, url, json):
            calls.append(f"{url} {json['model']}")
            return SimpleNamespace(json=lambda: {"details": {"family": "qwen3", "parameter_size": "27B",
                                                             "quantization_level": "Q4_K_M", "format": "gguf"}})

    found = ollama_identity(LOCAL_MODEL, host="127.0.0.1:11434", client=Ollama())
    assert (found["tag"], found["digest"], found["parameter_size"]) == (LOCAL_MODEL, "a" * 64, "27B")
    assert calls == ["http://127.0.0.1:11434/api/tags", f"http://127.0.0.1:11434/api/show {LOCAL_MODEL}"]
    with pytest.raises(BenchEnvironmentError, match="stock weights"):
        ollama_identity("ofix-personal-27b", client=Ollama())


# ── Worker environment and lanes ────────────────────────────────────────────


def test_a_worker_refuses_any_environment_that_could_touch_real_data_or_learning(tmp_path) -> None:
    good = {"ROW_BOT_DATA_DIR": str(tmp_path / "data"), "ROW_BOT_TEST_MODE": "true", "ROW_BOT_INSTALL_COUNT": "0"}
    assert check_environment(good) == tmp_path / "data"
    for change, needle in (({"ROW_BOT_INSTALL_COUNT": "1"}, "INSTALL_COUNT"),
                           ({"ROW_BOT_LEARNING_PREVIEW": "1"}, "LEARNING_PREVIEW"),
                           ({"ROW_BOT_TEST_MODE": ""}, "TEST_MODE"),
                           ({"ROW_BOT_DATA_DIR": str(Path.home() / ".row-bot")}, "everyday"),
                           ({"ROW_BOT_LIVE_EVAL_CODEX_PROFILE": str(tmp_path)}, "eval profile")):
        with pytest.raises(BenchEnvironmentError, match=needle):
            check_environment({**good, **change})
    env = child_env(tmp_path / "d", tmp_path / "w", {"ROW_BOT_LEARNING_PREVIEW": "1", "PATH": "x"})
    assert env["ROW_BOT_INSTALL_COUNT"] == "0" and "ROW_BOT_LEARNING_PREVIEW" not in env
    assert env["ROW_BOT_DATA_DIR"] == str(tmp_path / "d") and env["ROW_BOT_TEST_MODE"]
    check_environment(env)


def test_lanes_need_a_paid_cap_an_eval_profile_and_a_booking_file(tmp_path) -> None:
    caps = LedgerCaps()
    assert "paid cap is $0" in orchestrate.lane_problem("openai", caps, {})
    assert orchestrate.lane_problem("openai", LedgerCaps(max_usd=5), {}) == ""
    assert "ROW_BOT_LIVE_EVAL_CODEX_PROFILE" in orchestrate.lane_problem("codex", caps, {})
    assert "ROW_BOT_BENCH_BOOKING_FILE" in orchestrate.lane_problem("ollama", caps, {})
    booked = {"ROW_BOT_BENCH_BOOKING_FILE": str(tmp_path / "b.txt")}
    assert orchestrate.lane_problem("ollama", caps, booked, model="qwen3.8:27b") == ""
    for other in ("ofix-personal-27b", "row-bot-personal-v3", "gpt-oss:20b", "qwen3.6:27b"):
        assert "stock weights" in orchestrate.lane_problem("ollama", caps, booked, model=other)
    assert orchestrate.split_ref("ollama:qwen3.8:27b") == ("ollama", "qwen3.8:27b")
    assert orchestrate.main(["run", "--benchmark", "locomo", "--data", "x", "--protocol", "y",
                             "--model", "codex:auto"]) == 2  # ROW_BOT_BENCH is not set


def test_the_report_gives_both_keys_tokens_and_time_from_a_run_folder(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({
        "benchmark": "locomo", "git_commit": "abc", "dream": False,
        "config": {"provider": "codex", "reasoning_effort": "provider default", "tools": ["memory"]},
        "protocol": {"name": "locomo", "sha256": "f" * 64, "published_judge": "openai:gpt-4o-mini"}}),
        encoding="utf-8")
    (run_dir / "questions.json").write_text(json.dumps([vars(Question("q1", "?", "a", "temporal", "b"))]),
                                            encoding="utf-8")
    trajectory = Trajectory("locomo_0_a", "locomo", config={"kind": "memory", "model": "gpt-6.1-sol"}, status="done")
    trajectory.steps = [
        Step(kind="ingest", name="s1", seconds=10.0, metrics={"saved": 3},
             calls=[CallEntry("codex", "gpt-6.1-sol", input_tokens=4_000, output_tokens=300, cost_usd=0.0)]),
        Step(kind="turn", name="q1", seconds=6.0,
             calls=[CallEntry("codex", "gpt-6.1-sol", input_tokens=20_000, output_tokens=200, cost_usd=0.0)]),
    ]
    stopped = Trajectory("locomo_0_a", "locomo", config={"kind": "memory"}, status="stopped",
                         steps=[Step(kind="turn", name="q1", seconds=1.0)])
    append_jsonl(run_dir / "trajectories.jsonl", stopped)  # an earlier attempt: the newest one counts
    append_jsonl(run_dir / "trajectories.jsonl", trajectory)
    with (run_dir / "judgments.jsonl").open("w", encoding="utf-8") as handle:
        for key, score in (("original", 0.0), ("audited", 1.0)):
            handle.write(json.dumps(vars(scoring.Judgment("q1", key, "model:codex:gpt-6.1-sol", 0, score,
                                                          category="temporal"))) + "\n")
    scores = orchestrate.write_report(run_dir)
    assert [(item["key"], item["accuracy"], item["published_judge"]) for item in scores["scores"]] == [
        ("original", 0.0, False), ("audited", 1.0, False)]
    assert scores["ingestion"]["input_tokens"] == 4_000 and scores["ingestion"]["memories_saved"] == 3
    assert scores["questions"]["tokens_per_question"]["mean"] == 20_200.0
    assert scores["questions"]["asked"] == 1 and scores["tasks"]["other"] == []
    assert any(row["system"] == "Mem0" and row["source"].startswith("https://") for row in scores["published"])
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "self-run, gpt-6.1-sol" in report and "not the published judge" in report
    assert "context, not head-to-head" in report


def test_each_attempt_at_a_task_gets_a_fresh_scratch_folder(tmp_path, monkeypatch) -> None:
    started: list[dict[str, str]] = []

    def fake_run(argv, *, env, **_kwargs):
        started.append(env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(orchestrate.subprocess, "run", fake_run)
    spec = {"kind": "memory", "task_id": "locomo_0_a"}
    assert orchestrate.run_worker(tmp_path, spec) == 0 and orchestrate.run_worker(tmp_path, spec) == 0
    folders = [Path(env["ROW_BOT_DATA_DIR"]).name for env in started]
    assert folders == ["locomo_0_a-1", "locomo_0_a-2"]
    assert all(env["ROW_BOT_INSTALL_COUNT"] == "0" and "ROW_BOT_LEARNING_PREVIEW" not in env for env in started)
    assert json.loads((tmp_path / "specs" / "locomo_0_a-2.json").read_text(encoding="utf-8"))["run_dir"] == str(tmp_path)


# ── The call gate and turns ─────────────────────────────────────────────────


def test_the_call_gate_refuses_a_paid_call_before_it_is_sent(tmp_path) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import HumanMessage

    from row_bot.providers.usage_ledger import attach_usage_ledger
    from tests.e2e.bench.runtime import install_call_gate

    replies = iter([AIMessage(content="sent")])
    model = attach_usage_ledger(GenericFakeChatModel(messages=replies), provider_id="openai", model_name="gpt-4o-mini")
    ledger = _ledger(tmp_path)
    undo = install_call_gate(ledger)
    try:
        with pytest.raises(BudgetStop):
            model.invoke([HumanMessage(content="judge this")])
    finally:
        undo()
    assert next(replies).content == "sent"  # the provider never answered: the call was not sent
    assert len(ledger.refused) == 1


def test_a_turn_declines_what_stops_for_a_person_on_the_pinned_date(tmp_path, monkeypatch) -> None:
    from tests.e2e.bench.runtime import BenchRuntime, ProviderLimit, pinned_clock

    seen: dict[str, object] = {}

    def stream_agent(prompt, tools, config, **_kwargs):
        seen["now"] = agent._datetime.now()
        seen["tools"], seen["approval"] = tools, config["configurable"]["approval_mode"]
        yield "tool_call", SimpleNamespace(runtime_name="search_memory", args={"query": prompt})
        yield "interrupt", [{"__interrupt_id": "i1", "tool": "save_memory"}]

    def resume_stream_agent(tools, config, approved, interrupt_ids=None, **_kwargs):
        seen["approved"], seen["ids"] = approved, interrupt_ids
        yield "done", "In May 2023."

    monkeypatch.setattr(agent, "stream_agent", stream_agent)
    monkeypatch.setattr(agent, "resume_stream_agent", resume_stream_agent)
    runtime = BenchRuntime(BenchConfig("codex", "gpt-6.1-sol", tools=("memory",)), _ledger(tmp_path))
    monkeypatch.setattr(runtime, "drain_calls", lambda step, phase="": [])
    with pinned_clock(datetime(2023, 10, 23, 9, 55)):
        step = runtime.turn("thread-1", "When did Ana move?", name="q1")
    assert seen["now"] == datetime(2023, 10, 23, 9, 55)
    assert agent._datetime is datetime  # the clock is real again
    assert (seen["tools"], seen["approval"], seen["approved"], seen["ids"]) == (["memory"], "full", False, ["i1"])
    assert (step.status, step.answer, step.metrics["interrupts_declined"]) == ("done", "In May 2023.", 1)
    assert step.tool_calls == [{"name": "search_memory", "args": {"query": "When did Ana move?"}}]

    def limited(*_args, **_kwargs):
        yield "error", "429 Too Many Requests: usage limit reached"

    monkeypatch.setattr(agent, "stream_agent", limited)
    with pytest.raises(ProviderLimit):
        runtime.turn("thread-1", "again?")


# ── Ingestion through the real memory path ──────────────────────────────────


class _ExtractionModel:
    """The chat model as background extraction sees it: answers one memory, records each prompt."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def bind(self, **_kwargs):
        return self

    def with_config(self, *_args, **_kwargs):
        return self

    def invoke(self, messages, *_args, **_kwargs):
        prompt = str(messages[-1].content)
        self.prompts.append(prompt)
        if "CONVERSATION:" not in prompt:
            return AIMessage(content='{"decision": "new"}')
        return AIMessage(content=json.dumps({"memories": [
            {"category": "preference", "subject": "Pottery class", "aliases": "",
             "content": "User joined a pottery class on 7 May 2023."}]}))


def test_sessions_are_ingested_through_extraction_on_their_own_dates(tmp_path, monkeypatch,
                                                                       reload_for_data_dir) -> None:
    data_dir = tmp_path / "data"
    threads, kg, memory, memory_extraction = reload_for_data_dir(
        data_dir, "row_bot.threads", "row_bot.knowledge_graph", "row_bot.memory", "row_bot.memory_extraction")
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(kg, "repair_projections", lambda **_kwargs: {"complete": True})
    from row_bot import models
    from row_bot.providers import background

    model = _ExtractionModel()
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    monkeypatch.setattr(models, "get_llm_for", lambda _ref, num_ctx=None, *, reasoning_plan=None: model)
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: None)
    monkeypatch.setattr(threads, "get_workflow_thread_ids", lambda: set())

    from tests.e2e.bench.memory import adapter
    from tests.e2e.bench.runtime import BenchRuntime

    data, key = _locomo_file(tmp_path)
    task = locomo_tasks(data, key)[0]
    runtime = BenchRuntime(BenchConfig("openai", "gpt-5", tools=("memory",)), _ledger(tmp_path), task_id=task.task_id)
    steps: list[Step] = []
    adapter.ingest(runtime, task, on_step=steps.append)

    extraction_prompts = [prompt for prompt in model.prompts if "CONVERSATION:" in prompt]
    assert len(extraction_prompts) == 2  # one window per session, each read once
    assert "Today is 08 May 2023" in extraction_prompts[0] and "Today is 22 October 2023" in extraction_prompts[1]
    assert "User: I joined a pottery class yesterday." in extraction_prompts[0]
    assert "Assistant: Nice! I adopted a dog. [shared an image: a puppy]" in extraction_prompts[0]
    assert [step.kind for step in steps] == ["ingest", "ingest"]
    assert steps[0].metrics["turns"] == 2 and steps[0].metrics["extraction_runs"] == 1
    assert memory.find_by_subject(None, "Pottery class") is not None
    assert steps[-1].metrics["memories_total"] >= 1
    # Nothing is left unread: another extraction run finds nothing due.
    assert memory_extraction.run_extraction() == 0
    with pytest.raises(adapter.StopRequested):
        adapter.ingest(runtime, task, stop=lambda: True)
