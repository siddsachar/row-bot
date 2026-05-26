# Memory And Recall Uplift Implementation Plan

Date: 2026-05-26
Branch context: `codex/local-provider-compatibility`

This plan captures the memory/recall redesign for Thoth after the Chat Only
runtime work. It is intended to be carried into a new implementation thread.

## Goals

- Make memory recall robust enough for a personal assistant that knows the user.
- Avoid brittle prompt patches or one-off phrase heuristics.
- Keep Agent Mode strict and stable.
- Keep Chat Only free of hidden tools and hidden memory injection unless a
  future explicit Chat Only memory design is added.
- Preserve existing memory, extraction, document, dream, wiki, and tool flows.
- Make transcript and memory-loading paths independent of model/provider
  readiness.
- Improve recall precision before broadening recall coverage.

## External Design References

Use these as design inspiration, not as dependencies:

- A-MEM: Agentic Memory for LLM Agents, arXiv:2502.12110.
  Key idea: write-time memory organization, dynamic links, and memory evolution.
- Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory,
  arXiv:2504.19413.
  Key idea: extract, consolidate, retrieve salient information efficiently;
  graph memory helps multi-hop and temporal recall.
- MIRIX: Multi-Agent Memory System for LLM-Based Agents, arXiv:2507.07957.
  Key idea: separate memory types: Core, Episodic, Semantic, Procedural,
  Resource, Knowledge Vault.
- MemoryOS, EMNLP 2025.
  Key idea: hierarchical storage and heat-based updates across short-term,
  mid-term, and long-term personal memory.
- Generative Dense Retrieval: Memory Can Be a Burden, arXiv:2401.10487.
  Key warning: memory/retrieval noise can degrade answers.

## Current Codebase Facts

### Knowledge Graph

Main file: `knowledge_graph.py`

Current entity schema:

- `id`
- `entity_type`
- `subject`
- `description`
- `aliases`
- `tags`
- `properties`
- `source`
- `created_at`
- `updated_at`

Current relation schema:

- `id`
- `source_id`
- `target_id`
- `relation_type`
- `confidence`
- `properties`
- `source`
- `created_at`
- `updated_at`

Useful existing primitives:

- `save_entity(...)`
- `update_entity(...)`
- `delete_entity(...)`
- `list_entities(...)`
- `search_entities(...)`
- `semantic_search(...)`
- `find_by_subject(...)`
- `find_duplicate(...)`
- `add_relation(...)`
- `get_relations(...)`
- `get_neighbors(...)`
- `graph_enhanced_recall(...)`
- `_decay_multiplier(...)`
- `_touch_recalled(...)`

Existing source tracking:

- live/manual memory writes use `source="live"`.
- background conversation extraction uses `source="extraction"`.
- document extraction uses `source="document:<display_name>"`.
- dream merge uses `source="dream_merge"`.
- dream relation inference uses `source="dream_infer"`.
- graph UI already uses `_source` and `_updated_at` for source filters and
  recency styling.

Existing recency/access signal:

- `_decay_multiplier(entity)` reads `properties.recalled_at` and `updated_at`.
- `_touch_recalled(entity_ids)` writes `properties.recalled_at`.
- Current score after semantic search is multiplied by this decay factor.

Current retrieval issue:

- `graph_enhanced_recall()` mutates memory by touching every returned result.
  This is wrong for candidate retrieval because rejected candidates should not
  be reinforced.
- `graph_enhanced_recall()` returns early when semantic seeds are empty, so the
  documented SQL keyword fallback does not run in no-seed cases.
- `search_entities()` is SQL `LIKE`, not FTS/BM25.

### Memory Compatibility Layer

Main file: `memory.py`

It maps old memory names to graph names:

- `category` -> `entity_type`
- `content` -> `description`

It should remain backward compatible. Any new metadata should be optional.

### Memory Tool

Main file: `tools/memory_tool.py`

Explicit memory search currently calls:

```python
kg.graph_enhanced_recall(query, top_k=10, threshold=0.3)
```

Explicit search should remain broader than auto-recall, because the user asked
to search memory. It should still touch memories that are actually returned to
the user/tool result.

### Background Extraction

Main file: `memory_extraction.py`

Strengths:

- Extraction prompt is conservative.
- Assistant messages are truncated to avoid extracting from AI output.
- Dedup checks exact subject and high-threshold semantic matches.
- Cross-source document/non-document merges require tighter score.
- Relation confidence below 0.80 is rejected.
- Vague relation types are rejected.

Current issue:

- `_get_thread_messages()` calls `get_agent_graph()`.
- This creates an unnecessary dependency on provider/model readiness and Agent
  graph construction.
- It should read checkpoint messages directly, similar to
  `tools/conversation_search_tool.py` or `threads.get_latest_checkpoint_messages()`.

### Document Extraction

Main file: `document_extraction.py`

Existing behavior:

- Creates/updates a `media` hub entity with `source="document:<display_name>"`.
- Links `User -> uploaded -> document hub`.
- Extracted entities are saved with document source.
- Extracted entities are linked to the document hub via `extracted_from`.

This already supports Resource memory. Do not break it.

### Dream Cycle

Main file: `dream_cycle.py`

Existing behavior:

- Merges probable duplicate entities.
- Enriches thin descriptions from conversation evidence.
- Infers relations with confidence and evidence in relation properties.
- Decays stale inferred relations.
- Rebuilds FAISS once after batch work.

This is the right place for heavier memory evolution later. Avoid adding
expensive LLM work to the foreground Agent turn.

### Agent Recall Injection

Main file: `agent.py`

Current issue:

- `_pre_model_trim()` does many unrelated things:
  - trimming
  - base64 redaction
  - browser snapshot compression
  - tool result compaction
  - summary insertion
  - date/platform/self-knowledge/skills/plugin injections
  - auto-recall
  - wind-down notices
  - provider-specific system message consolidation
- Auto-recall currently runs whenever entities exist:

```python
memories = graph_enhanced_recall(query, top_k=8, threshold=0.35, hops=1)
```

- There is no candidate validation, recall budget, trace, or skip reason.
- The recall block wording is too strong: recalled facts can become an implied
  task instead of background.

## Target Architecture

### Memory Tiers

Do not add new tables in the first pass. Use existing `entity_type`, `source`,
and `properties`.

Represent tiers with `properties.memory_tier` where useful:

- `core`: stable user profile, preferences, durable facts.
- `episodic`: conversation/event summaries and thread-level references.
- `semantic`: normal KG entities and relations.
- `procedural`: skills/workflow knowledge. Mostly lives in `skills.py` today.
- `resource`: documents, media, uploaded files.
- `working`: current thread/files/project. This stays out of long-term memory.

Initial mapping:

- `media` or `source.startswith("document:")` -> resource.
- `source.startswith("dream_")` -> semantic, generated/evolved.
- `entity_type in {"preference", "person", "fact"}` with subject `User` or
  relation to `User` -> likely core.
- `event`, `project`, `place`, `organisation`, `concept`, `skill` -> semantic
  unless marked otherwise.

### Memory Policy Module

Add `memory_policy.py`.

This module owns auto-recall policy and keeps Agent prompt assembly from
directly making recall decisions.

Suggested public API:

```python
@dataclass
class MemoryRecallDecision:
    allowed: bool
    reason: str
    query: str
    selected: list[dict]
    candidates_seen: int
    trace: dict


def build_auto_recall(
    latest_user_text: str,
    recent_user_texts: list[str],
    *,
    thread_id: str = "",
    runtime_surface: str = "normal_chat",
    context_window: int | None = None,
) -> MemoryRecallDecision:
    ...


def format_recall_block(memories: list[dict]) -> str:
    ...
```

The policy should use retrieval plus validation, not an LLM call in the
foreground path.

### Retrieval Without Mutation

In `knowledge_graph.py`, split candidate retrieval from touch.

Add one of these:

```python
def retrieve_memory_candidates(
    query: str,
    *,
    top_k: int = 8,
    threshold: float = 0.30,
    hops: int = 1,
    max_results: int = 20,
    include_keyword: bool = True,
) -> list[dict]:
    ...
```

Or add `touch: bool = True` to `graph_enhanced_recall(...)` while preserving
the old default.

Preferred shape:

- New no-touch function for policy.
- `graph_enhanced_recall()` remains compatibility wrapper and touches returned
  memories by default.
- Add public `touch_recalled(entity_ids: list[str])`.

Each candidate should include:

- `score`: final retrieval score for compatibility.
- `semantic_score`: raw semantic similarity when available.
- `decay_multiplier`: recency/access factor.
- `via`: semantic, graph, keyword.
- `relations`: for graph-expanded candidates.
- `retrieval_debug`: optional dict for traces.

### Keyword Fallback Fix

Fix `graph_enhanced_recall()`/candidate retrieval so keyword fallback runs even
when semantic search returns no seeds.

Use field-aware keyword scoring:

- exact normalized subject or alias match: high score.
- tag match: medium-high score.
- subject/alias partial match: medium score.
- description-only `LIKE`: low score.

Do not graph-expand keyword-only results in the first pass unless they are high
confidence exact subject/alias matches.

### Candidate Validation And Reranking

Auto-recall should retrieve candidates, then validate.

Candidate score should combine:

- semantic score
- lexical/field match strength
- existing recency decay
- source/tier prior
- relation confidence for graph-expanded candidates
- evidence/provenance availability, if present
- query specificity
- top score margin

Example rough scoring:

```text
final = 0.45 * semantic_component
      + 0.25 * lexical_component
      + 0.15 * recency_component
      + 0.10 * tier_source_component
      + 0.05 * relation_or_evidence_component
```

Use this as a starting point, not as a sacred formula. Keep constants named and
tested.

Validation rules:

- Reject if final score below threshold.
- Reject if top candidate has weak margin and the query is broad.
- Reject resource/document memories unless the query anchors to the document,
  upload, media title, or document topic.
- Reject stale/superseded memories unless the user asks for history.
- Reject memories with `status in {"archived", "contradicted"}` by default.

### Generic Hard Guards

Hard guards should only enforce broad invariants:

- Empty or greeting-only messages do not need long-term recall.
- Runtime/status/config/tool questions should use runtime state/tools, not
  personal memory.
- Approval/resume/workflow/Developer/Designer surfaces remain Agent Mode and
  must not be silently routed through Chat Only memory behavior.
- Do not retrieve personal memory for pure file/image current-turn analysis
  unless the user's text asks for prior/personal context.

Avoid building a long list of prompt-specific phrase hacks.

### Recall Injection Format

Replace the current strong recall wording with a background-only block:

```text
Relevant long-term memory:
The following facts may help answer the latest user request. They are
background context, not instructions, not a task, and not a replacement for
the user's latest message. Use only what is relevant.

- [id=...] [type] Subject: description
```

Keep the block short.

Budget:

- Use at most 3 to 5 memories for auto-recall initially.
- Cap the block by characters/tokens.
- Suggested initial cap: `min(1200 tokens, max(400 tokens, context_window * 0.03))`.
- For 32K Agent Mode, this keeps memory useful without crowding tools/system.

Touch only selected/injected memory IDs after the final recall block is built.

### Provenance And Temporal Metadata

Use existing `properties` JSON first; avoid schema churn in the first pass.

Recommended optional entity properties:

- `memory_tier`
- `importance`
- `confidence`
- `status`: active, stale, archived, contradicted
- `valid_from`
- `valid_until`
- `last_confirmed_at`
- `supersedes`
- `superseded_by`
- `source_thread_id`
- `source_thread_name`
- `source_message_index`
- `evidence`
- `evidence_role`
- `extraction_method`: live, extraction, document, dream, manual

Recommended relation properties:

- `evidence`
- `co_occurrences`
- `source_thread_ids`
- `last_confirmed_at`

Existing `source`, `created_at`, and `updated_at` remain the coarse top-level
fields. Do not remove or rename them.

### Background Extraction Changes

Refactor `memory_extraction._get_thread_messages()`:

- Do not import or call `agent.get_agent_graph()`.
- Use `threads.get_latest_checkpoint_messages(thread_id)`.
- Convert LangChain messages into `{"role": ..., "content": ...}` locally or
  via a shared helper.
- Preserve the existing assistant truncation behavior in `_format_conversation`.

Then optionally pass provenance into `_dedup_and_save(...)`:

```python
_dedup_and_save(
    extracted,
    source="extraction",
    source_context={
        "thread_id": tid,
        "thread_name": name,
    },
)
```

Add optional `properties` support to `memory.save_memory(...)` and
`memory.update_memory(...)` so extraction can store source context without
bypassing the compatibility layer.

### Explicit Memory Tool Behavior

Keep explicit memory tools broader than auto-recall:

- `search_memory` should retrieve broadly because the user asked for memory.
- It can use the compatibility wrapper or the no-touch retrieval plus explicit
  `touch_recalled()` after formatting results.
- It should include relevance, updated time, and maybe source/tier in results.

Do not make explicit memory search obey the same strict auto-recall injection
thresholds.

### Dream Cycle Future Work

Do not put heavy LLM memory evolution into the foreground turn.

Use the dream cycle for:

- write-time or batch memory evolution
- supersession of stale facts
- strengthening or weakening importance
- promoting stable preferences/facts into core memory
- relation cleanup
- contradiction review

The first implementation pass should only add enough metadata and policy
hooks to make this future work straightforward.

## Implementation Phases

### Phase 1 - Stabilize Auto-Recall

1. Add no-touch candidate retrieval in `knowledge_graph.py`.
2. Fix keyword fallback so it runs without semantic seeds.
3. Add `touch_recalled(...)` public wrapper.
4. Add `memory_policy.py`.
5. Replace Agent auto-recall block in `_pre_model_trim()` with policy call.
6. Add context trace logging.
7. Adjust recall block wording and token budget.

Expected result:

- Fresh generic prompts do not inject unrelated memory.
- Concrete memory prompts still recall relevant facts.
- Rejected candidates do not update `recalled_at`.

### Phase 2 - Provider-Free Memory Loading And Provenance

1. Refactor `memory_extraction._get_thread_messages()` to direct checkpoint
   reads.
2. Add optional `properties` to `memory.save_memory()` and
   `memory.update_memory()`.
3. Add optional `source_context` to `_dedup_and_save()`.
4. Store source thread metadata and extraction method in `properties`.
5. Preserve existing document and dream source behavior.

Expected result:

- Extraction does not depend on Agent graph/model/provider readiness.
- New extracted memories carry better provenance.

### Phase 3 - Retrieval Quality

1. Add field-aware keyword scoring.
2. Consider SQLite FTS5/BM25 for `subject`, `aliases`, `tags`,
   `description`.
3. Add candidate reranking tests.
4. Add memory-source/tier filtering for auto-recall.

Expected result:

- Exact subject/alias/tag matches are reliable.
- Semantic near-misses are filtered better.
- Document/resource memories do not leak into unrelated chat.

### Phase 4 - Memory Evolution

1. Add memory status/supersession properties.
2. Add dream-cycle promotion/staleness logic.
3. Add contradiction review hooks.
4. Add importance/heat adjustment based on confirmed recalls and updates.

Expected result:

- Memories evolve over time instead of accumulating contradictions.

### Phase 5 - UI And Auditability

1. Surface provenance/source/evidence in memory detail views.
2. Add recall trace view or debug log toggle.
3. Add filters for tier/status/source.
4. Add user controls to pin/archive/supersede memories.

Expected result:

- The user can inspect why Thoth remembered something and correct it.

## Test Plan

Add focused tests before broad full-suite runs.

### Knowledge Graph Tests

- Candidate retrieval can run without touching `recalled_at`.
- Explicit touch updates `properties.recalled_at`.
- `graph_enhanced_recall()` preserves backward-compatible touch behavior.
- Keyword fallback returns exact subject/alias matches even without semantic
  seeds.
- Graph-expanded candidates include relation metadata and relation confidence.

### Memory Policy Tests

- Empty/greeting-only latest turn skips auto-recall.
- Runtime/status/tool/config latest turn skips personal-memory injection.
- Concrete memory query selects the intended memory.
- Weak irrelevant candidates are rejected.
- Resource/document memory is rejected unless the query is anchored.
- Superseded/stale memories are rejected by default.
- Selected memory IDs are the only touched IDs.
- Trace includes decision reason, candidate count, selected count, and top
  scores.

### Agent Context Tests

- `_pre_model_trim()` uses memory policy instead of direct
  `graph_enhanced_recall()`.
- Fresh generic Agent turn does not insert a recall block when memories exist.
- Concrete recall Agent turn inserts bounded background recall.
- Recall block appears before the latest human message only when selected.
- Anthropic system-message consolidation still works.
- Existing tool-call repair/trimming tests still pass.

### Extraction Tests

- `memory_extraction._get_thread_messages()` does not call `get_agent_graph()`.
- Extraction reads checkpoint messages directly.
- Extracted memories can store provenance properties.
- Existing dedup, contradiction, relation confidence, and source behavior
  remains intact.

### Integration/E2E Tests

- Explicit `search_memory` still works and returns broad results.
- Auto-recall finds a stored secret/code word when asked directly.
- Auto-recall finds a one-hop relation when asked directly.
- Auto-recall does not answer unrelated fresh prompts from memory.
- Document memories do not appear in ordinary chat unless anchored.
- Dream cycle tests continue to pass.
- Full `tests/test_suite.py` passes.

## Non-Goals For First Pass

- Do not add an LLM router for recall in the foreground path.
- Do not redesign the entire memory database schema.
- Do not remove existing memory APIs.
- Do not make Chat Only silently use Agent memory.
- Do not add broad prompt hacks for specific user phrases.
- Do not change Developer, Designer, workflows, approvals, or Agent Mode
  runtime strictness.

## First Implementation Prompt

Use this prompt in a new implementation thread:

```text
We are working in D:\Code\Thoth on branch codex/local-provider-compatibility.

Implement the memory and recall uplift plan saved in:

- docs/memory-recall-uplift-implementation-plan.md

Important constraints:
- Do not regress Agent Mode behavior.
- Do not change Chat Only into a hidden Agent/memory mode.
- Do not add brittle prompt patches for specific user questions.
- Preserve existing memory APIs and compatibility in memory.py.
- Preserve existing document extraction, dream cycle, wiki vault, graph UI,
  memory tool, and Agent trimming behavior unless explicitly scoped by the plan.
- Auto-recall must be adaptive, candidate-validated, bounded, and observable.
- Candidate retrieval must not mutate recalled_at unless candidates are actually
  injected or explicitly shown to the user.
- Background memory extraction must not call get_agent_graph() or require
  provider/model readiness to load thread messages.

Start by reading:
- docs/memory-recall-uplift-implementation-plan.md
- knowledge_graph.py
- memory.py
- tools/memory_tool.py
- memory_extraction.py
- document_extraction.py
- dream_cycle.py
- agent.py
- prompts.py
- tools/conversation_search_tool.py
- threads.py
- ui/helpers.py
- tests/test_memory_e2e.py
- tests/test_suite.py sections around memory/recall/context management

Implementation order:
1. Add no-touch memory candidate retrieval in knowledge_graph.py while
   preserving graph_enhanced_recall() compatibility.
2. Fix keyword fallback so exact keyword/subject/alias matches can return even
   when semantic search has no seed.
3. Add public touch_recalled() or equivalent and ensure only selected/shown
   memories are touched.
4. Add memory_policy.py with deterministic adaptive auto-recall policy:
   candidate retrieval, scoring, validation, token budgeting, trace metadata,
   and recall-block formatting.
5. Replace direct auto-recall logic in agent._pre_model_trim() with the policy
   call, keeping system-message ordering and provider consolidation safe.
6. Add context trace logging for memory decisions and injected context sizes.
7. Refactor memory_extraction._get_thread_messages() to read checkpoint
   messages directly without get_agent_graph().
8. Add optional properties/source_context plumbing for extracted memory
   provenance, preserving existing callers.
9. Add focused tests for retrieval/touch, policy decisions, Agent recall
   injection, extraction without graph construction, and keyword fallback.
10. Run targeted tests, compile checks, then full tests/test_suite.py.

Keep changes in manageable chunks. After each chunk, run focused tests and
update the implementation memory. At the end, cross-check the implementation
against docs/memory-recall-uplift-implementation-plan.md and report remaining
gaps or edge cases.
```
*** End Patch
