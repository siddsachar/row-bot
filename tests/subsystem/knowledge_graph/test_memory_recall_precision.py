"""Auto-recall shows what the latest request is about, and little else.

Measured on a real profile (12 Nov 2026): every turn got five memories, mostly the same project records,
because a keyword hit was also counted as the meaning score, three messages were matched at once, and
recency and tier carried a third of the bar. These tests pin the behaviour that replaced it.
"""

from __future__ import annotations


def _modules(tmp_path, monkeypatch, reload_for_data_dir):
    (kg,) = reload_for_data_dir(tmp_path / "data", "row_bot.knowledge_graph")
    (policy,) = reload_for_data_dir(tmp_path / "data", "row_bot.memory_policy")
    kg._skip_reindex = True
    return kg, policy


def _meaning(kg, monkeypatch, scores: dict[str, float], *, mean: float = 0.45):
    """Matching by meaning, faked: these memories at these similarities; every other memory at ``mean``."""
    def semantic_search(query, top_k=5, threshold=0.5, *, for_auto_recall=False, stats=None):
        if stats is not None:
            stats["mean"] = mean
        hits = []
        for entity_id, score in sorted(scores.items(), key=lambda item: -item[1]):
            if score >= threshold:
                entity = kg.get_entity(entity_id)
                entity["score"] = score
                hits.append(entity)
        return hits[:top_k]

    monkeypatch.setattr(kg, "semantic_search", semantic_search)


def _recall(policy, latest: str, recent: list[str] | None = None):
    return policy.build_auto_recall(latest, recent or [], runtime_surface="agent", context_window=200_000)


def test_a_memory_found_by_meaning_and_by_its_name_keeps_its_meaning_score(tmp_path, monkeypatch, reload_for_data_dir):
    kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    theme = kg.save_entity("preference", "App recordings theme", "Recordings of the app are made in dark theme.")
    _meaning(kg, monkeypatch, {theme["id"]: 0.77}, mean=0.37)

    decision = _recall(policy, "use dark theme for app recordings")

    assert [m["id"] for m in decision.selected] == [theme["id"]]
    shown = decision.selected[0]["policy_debug"]
    assert shown["semantic"] > 0.5 and shown["lexical"] > 0


def test_a_name_in_the_message_alone_does_not_bring_its_record_in(tmp_path, monkeypatch, reload_for_data_dir):
    kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    kg.save_entity("project", "Atlas", "A long record of everything about the Atlas project.")
    kg.save_entity("organisation", "X", "The social network the user posts on.")
    _meaning(kg, monkeypatch, {})

    decision = _recall(policy, "Atlas: post the morning thread on X at ten and the evening one at four")

    assert decision.allowed is False
    assert decision.reason == "no_valid_candidates"
    assert decision.selected == []


def test_a_specific_name_still_finds_its_memory_when_meaning_misses_it(tmp_path, monkeypatch, reload_for_data_dir):
    kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    trends = kg.save_entity("project", "Trends workflow", "Daily research run; its credits ran out on Monday.")
    _meaning(kg, monkeypatch, {})

    decision = _recall(policy, "we need to uplift the trends workflow, it failed again")

    assert [m["id"] for m in decision.selected] == [trends["id"]]


def test_without_meaning_search_words_alone_decide(tmp_path, monkeypatch, reload_for_data_dir):
    kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    code = kg.save_entity("fact", "Rosebud", "The user's code word.")

    def unavailable(*args, **kwargs):
        raise kg.MemorySemanticUnavailable("local_model_missing", "The search model isn't downloaded.")

    monkeypatch.setattr(kg, "semantic_search", unavailable)
    monkeypatch.setattr(policy, "_publish_fallback_notice", lambda **kwargs: {})

    decision = _recall(policy, "what is my rosebud")

    assert [m["id"] for m in decision.selected] == [code["id"]]


def test_a_meaning_score_near_the_query_average_is_not_relevance(tmp_path, monkeypatch, reload_for_data_dir):
    kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    japan = kg.save_entity("project", "Japan trip", "Planning a two-week trip around northern Japan.")
    context = kg.save_entity("concept", "Context window", "How many tokens a model reads at once.")
    # Both clear the bar on raw similarity; only one stands above what every memory scores for this query.
    _meaning(kg, monkeypatch, {japan["id"]: 0.68, context["id"]: 0.52}, mean=0.46)

    decision = _recall(policy, "compare Sapporo, Fukuoka and Sendai for a winter visit")

    assert [m["id"] for m in decision.selected] == [japan["id"]]
    assert any(r["id"] == context["id"] for r in decision.trace["rejected"])


def test_memories_far_below_the_best_one_are_left_out(tmp_path, monkeypatch, reload_for_data_dir):
    kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    exact = kg.save_entity("preference", "Video captions", "No captions on launch films; narration only.")
    loose = [kg.save_entity("project", f"Old film {n}", f"An earlier film, number {n}.") for n in range(4)]
    _meaning(kg, monkeypatch, {exact["id"]: 0.90, **{e["id"]: 0.68 for e in loose}}, mean=0.40)

    decision = _recall(policy, "why do you keep writing captions on the launch film")

    assert [m["id"] for m in decision.selected] == [exact["id"]]


def test_earlier_messages_are_read_only_for_a_short_follow_up(tmp_path, monkeypatch, reload_for_data_dir):
    _kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    earlier = "render the launch film again with the new music track"

    assert policy._build_query("make the bicycle in the picture blue please", [earlier]) == (
        "make the bicycle in the picture blue please"
    )
    assert earlier in policy._build_query("try now", [earlier, "something older"])
    assert "something older" not in policy._build_query("try now", [earlier, "something older"])


def test_a_task_that_names_a_tool_still_gets_memory(tmp_path, monkeypatch, reload_for_data_dir):
    kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    skill = kg.save_entity("project", "Promotional video skill", "How the owner's launch films are styled.")
    _meaning(kg, monkeypatch, {skill["id"]: 0.85}, mean=0.40)

    task = _recall(policy, "update the promotional video skill with the steps you used for this final version")
    status = _recall(policy, "what model are you using?")

    assert [m["id"] for m in task.selected] == [skill["id"]]
    assert status.reason == "runtime_status_request"


def test_two_records_with_one_name_are_shown_once(tmp_path, monkeypatch, reload_for_data_dir):
    kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    first = kg.save_entity("project", "Launch film", "The 5.3.3 launch film, full screen.")
    second = kg.save_entity("project", "launch film", "Older notes on the launch film.")
    _meaning(kg, monkeypatch, {first["id"]: 0.78, second["id"]: 0.72}, mean=0.40)

    decision = _recall(policy, "how is the launch film coming along")

    assert [m["id"] for m in decision.selected] == [first["id"]]


def test_a_learning_lesson_is_recalled_on_the_turn_it_applies_to(tmp_path, monkeypatch, reload_for_data_dir):
    kg, policy = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    lesson = kg.save_entity(
        "self_knowledge",
        "Video: verify narration against its transcript",
        "Before delivering a narrated video, read its transcript and check every scene is voiced.",
        source="learning",
        properties={"learning_change": "change-1"},
    )
    garden = kg.save_entity("project", "Garden beds", "Raised beds planned for the spring.")
    # A lesson is found by meaning like any memory: a lesson in use stays in the turns it applies to.
    _meaning(kg, monkeypatch, {lesson["id"]: 0.72, garden["id"]: 0.47}, mean=0.42)

    decision = _recall(policy, "render the narrated launch video and check the voice-over before sending it")

    assert [m["id"] for m in decision.selected] == [lesson["id"]]
