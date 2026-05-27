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

Decision: implement Phase 3 as a retrieval architecture pass, not a prompt
patch pass. Retrieval should become more reliable without making Agent turns
feel slower or turning Chat Only into hidden memory mode.

Confirmed design choices:

1. Add SQLite FTS5/BM25 as an additive retrieval layer.
   - Create an `entities_fts` virtual table over `subject`, `aliases`, `tags`,
     and `description`, with `entity_id` as an unindexed lookup column.
   - Use `unicode61 remove_diacritics 2` tokenization.
   - Do not use stemming in the first pass; personal names, codenames,
     acronyms, and aliases matter more than stemming.
   - Keep the existing field-aware keyword fallback. If FTS5 is unavailable,
     retrieval must still work.

2. Maintain FTS in Python, not SQLite triggers.
   - Add helpers such as `_ensure_fts()`, `_upsert_fts_entity(...)`,
     `_delete_fts_entity(...)`, `rebuild_fts_index()`, and
     `fts_search_entities(...)`.
   - Wire FTS maintenance into `save_entity()`, `update_entity()`,
     `delete_entity()`, `delete_all_entities()`, and batch rebuild paths.
   - Prefer visible Python-managed sync because the graph and FAISS indexes are
     already maintained this way.

3. Include document/resource memories in FTS candidate retrieval by default.
   - Do not hide documents from candidate retrieval.
   - Continue to gate resource/document injection in `memory_policy.py` unless
     the query anchors to the document, upload, title/source, or document topic.
   - Explicit `search_memory` remains broader and may show resource memories
     when the user asks to search memory.

4. Fuse candidates by entity ID across retrieval sources.
   - Sources: semantic, FTS/BM25, exact field-aware keyword, and graph-expanded.
   - Preserve raw signals on each candidate:
     - `semantic_score`
     - `lexical_score`
     - `bm25_score`
     - `field_score`
     - `decay_multiplier`
     - `via`
     - `retrieval_debug`
   - If multiple sources hit the same entity, preserve source details in
     `retrieval_debug.sources`. Keep compatibility for callers that expect
     `via` to be a string by using `"hybrid"` when combined.

5. Keep exact field matches strongest.
   - Exact normalized subject or alias matches bypass semantic seed
     requirements.
   - Tag matches are medium-high confidence.
   - Subject/alias partial matches outrank description-only matches.
   - Description-only BM25/LIKE hits are candidates, not automatic context.

6. Add graph expansion from high-confidence lexical hits.
   - Continue graph expansion from semantic seeds.
   - Also expand from exact subject/alias hits and strong subject/title FTS hits.
   - Do not expand weak description-only FTS hits.
   - Include relation confidence and connecting relation metadata.

7. Tighten auto-recall policy validation.
   - Add query specificity scoring.
   - Require stronger score and clearer top-candidate margin for broad queries.
   - Require higher confidence for description-only matches.
   - Preserve status/supersession/resource filters already added in Phase 1.

8. Add a rolling recall trace journal.
   - Keep structured logging.
   - Also persist recent recall decisions under app data, capped to a small
     rolling JSON file.
   - Store decision reason, candidate count, selected count, top scores,
     injected block size, and retrieval sources.
   - Do not build a UI trace panel in Phase 3.

9. Latency and UX constraints.
   - Auto-recall should remain foreground-cheap: target single-digit
     milliseconds for SQL/FTS candidate work on normal personal-memory sizes,
     excluding embedding latency already paid by semantic search.
   - Bound all retrieval limits before scoring and graph expansion.
   - Run FTS after hard policy guards so greetings, runtime/status requests,
     and file-only current-turn analysis do not pay unnecessary retrieval cost.
   - Graph expansion must be limited to high-confidence seeds and low hop
     counts.
   - If FTS maintenance fails, log and continue with existing retrieval rather
     than blocking the user's turn.
   - The user-facing effect should be better answers when prior context is
     clearly relevant, and no visible slowdown or unrelated memory leakage when
     it is not.

10. User-experience acceptance criteria.
    - Ordinary turns should not feel like a different mode. If memory is not
      clearly useful, the user should see no trace of recall.
    - Relevant memory should make answers feel naturally informed, not like the
      assistant is reciting database rows.
    - Auto-recall must not make current-turn file/image analysis worse by
      injecting unrelated personal facts.
    - When recall is skipped, no user-visible apology or explanation is needed.
      The decision should be observable in logs/journal only.
    - When recall is injected, the block must stay small enough that tool
      context, file context, and the latest request remain dominant.
    - Exact personal facts should feel dependable: names, aliases, codenames,
      preferences, projects, and relationships should be found without the user
      needing to know the exact phrasing.
    - Bad recall is worse than no recall. Prefer skipping weak candidates over
      injecting plausible but irrelevant memory.

11. Tests for Phase 3.
    - FTS table creation and rebuild.
    - FTS unavailable fallback.
    - Exact subject/alias beats description-only hit.
    - FTS finds no-semantic-seed results.
    - Candidate retrieval remains no-touch.
    - Explicit `search_memory` touches only displayed/filtered results.
    - Auto-recall rejects broad or weak memory matches.
    - Document/resource memories are candidates but not injected unless
      anchored.
    - Graph expansion from exact/strong lexical hits works.
    - Recall trace journal records skip and inject decisions.

Expected result:

- Exact subject/alias/tag matches are reliable.
- Semantic near-misses are filtered better.
- Document/resource memories do not leak into unrelated chat.
- Recall feels helpful, not intrusive or slow.

Implementation progress:

- Done: optional FTS5/BM25 index helpers and Python-managed sync for entity
  save/update/delete, legacy migration, and bulk clear/rebuild paths.
- Done: candidate fusion across semantic, FTS, keyword, and graph sources with
  source metadata preserved in `retrieval_debug.sources`.
- Done: graph expansion from semantic seeds and strong lexical subject/alias/tag
  hits, while avoiding weak description-only expansion.
- Done: policy-side rejection of weak description-only matches and rolling
  `memory_recall_trace.json` journal entries.
- Done: focused tests for FTS rebuild/search, no-touch retrieval, lexical graph
  expansion, Agent injection/touch behavior, extraction without graph
  construction, and trace journaling.
- Done: real-graph no-touch recall diagnostic against the user's live knowledge
  graph. Sampled exact-subject, alias, tag, description-derived, guardrail, and
  resource-anchor queries. Exact-subject probes reached 100% top-1 after
  punctuation-insensitive phrase matching; alias probes reached 100% top-3.
  Guardrail probes skipped recall and all retrieval probes preserved
  `recalled_at`.
- Remaining quality edge: broad shared tags and description-only probes remain
  inherently ambiguous. Keep them candidate-visible but conservative for
  automatic injection.

### Phase 4 - Memory Integrity And Provenance

Decision: implement Phase 4 as a conservative integrity pass, not a broad
"memory evolution" scoring system. The goal is to prevent wrong recalls,
preserve provenance, and respect document/wiki/manual authority without adding
low-trust heat/importance machinery.

Confirmed design direction:

1. Keep Phase 4 intentionally small.
   - Do not add a broad heat model in this phase.
   - Do not add automatic importance ranking.
   - Do not let the dream cycle archive, delete, or supersede user facts.
   - Do not add a new DB table unless a simple properties/journal approach
     cannot satisfy tests.
   - Prefer deterministic helpers over foreground LLM decisions.

2. Add `memory_evolution.py` as a small integrity helper module.
   - Centralize property normalization and status transitions.
   - Keep storage in existing `entities.properties` for compatibility.
   - Keep journal writes best-effort and non-blocking.
   - Public helpers should be usable by:
     - `tools/memory_tool.py`
     - `memory_extraction.py`
     - `document_extraction.py`
     - `wiki_vault.py`
     - `dream_cycle.py`
     - `memory_policy.py`

3. Standardize minimal memory properties.
   - Supported `status` values:
     - `active`: default; eligible for recall.
     - `archived`: hidden from auto-recall; explicit search may show it.
     - `superseded`: replaced by a newer memory; hidden from auto-recall
       unless the user asks for old/history/previous information.
     - `needs_review`: conflict detected; hidden from auto-recall.
   - Supported provenance/status properties:
     - `confidence`: 0.0-1.0, source confidence when available.
     - `memory_tier`: `core`, `semantic`, `episodic`, or `resource`.
     - `source_context`: compact dict for thread/document/wiki provenance.
     - `evidence`: capped list of short snippets or evidence records.
     - `evidence_count`: integer.
     - `superseded_by`: entity ID.
     - `supersedes`: capped list of entity IDs.
     - `review_reason`: short conflict/review reason.
     - `last_user_modified_at`: ISO timestamp for manual memory or wiki edits.
     - `last_evolved_at`: ISO timestamp for helper-driven status changes.

4. Add a rolling `memory_evolution_journal.json`.
   - Store under the same app data directory as memory/dream journals.
   - Cap to a small rolling window, for example 200 entries.
   - Record:
     - timestamp
     - action
     - entity ID(s)
     - actor: `manual`, `wiki`, `extraction`, `document_extraction`,
       `dream`, or `system`
     - reason
     - source
     - old/new status where relevant
   - Journal failures must never break memory save, extraction, recall, or wiki
     sync.

5. Contradiction handling must be safe by default.
   - Existing contradiction checks in `tools/memory_tool.py` and
     `memory_extraction.py` should call a shared helper when a conflict is
     detected.
   - On conflict:
     - do not overwrite the existing memory,
     - mark the existing entity or related candidate as `needs_review` only
       when there is a concrete conflict reason,
     - store `review_reason`,
     - append a journal entry,
     - return/report enough detail for the user or future UI to resolve it.
   - Extraction should not silently create a contradictory replacement for a
     live/manual/wiki-edited memory.

6. Supersession should be explicit and conservative.
   - Auto-supersede only when intent is clear:
     - manual memory update by ID,
     - explicit correction language already routed through the memory tool,
     - wiki vault import from an externally edited file,
     - reprocessing/replacing the same document source.
   - Do not supersede from vague semantic similarity alone.
   - Do not let document extraction supersede live/personal memories unless
     the match is same source or a manual/wiki edit confirms it.
   - Supersession behavior:
     - old entity gets `status="superseded"` and `superseded_by=<new_id>`.
     - new entity gets `supersedes=[old_id]`.
     - both changes are journaled.

7. Manual memory tool integration.
   - `_save_memory()`:
     - Existing exact-subject merge still works.
     - If contradiction is detected, mark `needs_review` and avoid overwrite.
     - New live memories should get `source_context.actor="manual"` and
       `last_user_modified_at`.
   - `_update_memory()`:
     - Treat as high-authority user intent.
     - Preserve existing APIs.
     - Set/update `last_user_modified_at`.
     - If replacing a conflicting/superseded state, restore status to `active`
       unless caller asks otherwise.
   - `_search_memory()`:
     - Continue broad explicit search.
     - Include `status`, `confidence`, `review_reason`, and supersession fields
       in JSON output when present.
     - Still touch only shown results.

8. Thread/background extraction integration.
   - `_dedup_and_save()` should preserve source/provenance properties already
     added in earlier phases.
   - When extraction sees a conflict:
     - call the shared review helper,
     - do not overwrite high-authority `manual`/`wiki` memories,
     - do not mark unrelated document/resource memories as conflicting just
       because semantic similarity is high.
   - For same-subject, same-source extraction updates:
     - merge evidence/provenance,
     - keep status `active`,
     - update `confidence` if supplied.

9. Document extraction and map-reduce compatibility.
   - `document_extraction.extract_from_document()` creates a `source_label` of
     `document:<display_name>`; keep this as the authority boundary.
   - The document hub media entity should store:
     - `memory_tier="resource"`
     - `source_context.kind="document"`
     - `source_context.display_name`
     - map/reduce summary metadata where available, such as window count.
   - Extracted document entities should store:
     - `source=document:<display_name>`
     - `memory_tier="resource"` unless clearly promoted elsewhere later.
     - `source_context.document_title`
     - `source_context.display_name`
     - optional chunk/window/page metadata if available.
   - Reprocessing the same document source may update or supersede memories
     from the same `source_label`.
   - Reprocessing a document must not supersede live/manual/wiki memories.
   - Existing `delete_entities_by_source()` and
     `delete_entities_by_source_prefix()` remain authoritative for document
     cleanup.

10. Wiki vault external edit compatibility.
    - `wiki_vault.check_vault_sync()` remains the detector for external edits.
    - `wiki_vault.import_from_vault()` treats vault changes as high-authority
      user edits.
    - On import:
      - update entity fields from markdown as today,
      - merge/normalize properties,
      - set `last_user_modified_at`,
      - set `source_context.actor="wiki"`,
      - restore `status="active"` unless the markdown explicitly encodes a
        supported status,
      - append an evolution journal entry.
    - Export should preserve status/provenance properties in frontmatter/JSON
      if the existing format supports it, without breaking current markdown
      parsing.
    - Thoth-generated exports must continue stamping mtime to avoid false
      external-edit detection.

11. Dream cycle integration.
    - Dream may continue duplicate merges, enrichment, relation inference, and
      relation confidence decay.
    - Dream may append evolution journal observations.
    - Dream may mark inferred relations stale/decayed as it already does.
    - Dream must not:
      - archive user memories,
      - supersede personal/live/wiki memories,
      - mark core/manual/wiki memories stale,
      - override `last_user_modified_at` protections.

12. Recall policy integration.
    - Auto-recall skips:
      - `archived`
      - `needs_review`
      - `superseded`, unless the query asks for history/old/previous.
    - Explicit `search_memory` may show all statuses with labels.
    - Candidate retrieval remains no-touch.
    - Touching selected/shown memories must not change status.

13. Scope control and non-goals for Phase 4.
    - No Phase 5 UI controls yet.
    - No automatic deletion.
    - No broad heat/importance score.
    - No foreground LLM router.
    - No hidden Chat Only memory behavior.
    - No schema rewrite.
    - No brittle prompt patches for specific questions.

Implementation chunks:

1. Add `memory_evolution.py`.
   - Property normalization helpers.
   - Status transition helpers.
   - Evidence/provenance merge helpers.
   - Journal append/load helpers.
   - Focused unit tests.
   - Status: done.

2. Wire manual memory paths.
   - Update `_save_memory()` conflict handling to call evolution review helper.
   - Update `_update_memory()` to mark high-authority user edits.
   - Add status/provenance fields to explicit search output.
   - Focused tests for conflict, update, search output, and no-touch behavior.
   - Status: done.

3. Wire extraction paths.
   - Update `memory_extraction._dedup_and_save()` to use provenance merge
     helpers and review helper on contradictions.
   - Preserve existing extraction journals and confidence gates.
   - Focused tests for no overwrite of manual/wiki memory and same-source
     provenance merge.
   - Status: done.

4. Wire document extraction paths.
   - Add document source context/properties to the hub entity and extracted
     entities.
   - Allow same-document source update/supersession only within the same
     `source_label`.
   - Preserve delete-by-source cleanup behavior.
   - Focused tests for map-reduce provenance, reprocess same source, and
     cleanup.
   - Status: done.

5. Wire wiki vault import/export.
   - Preserve existing markdown compatibility.
   - Treat external edits as high-authority user edits.
   - Preserve mtime stamping behavior for Thoth exports.
   - Focused tests for `check_vault_sync()`, `import_from_vault()`, property
     preservation, and no false sync after export.
   - Status: done.

6. Wire recall/dream compatibility.
   - Ensure `memory_policy.py` status checks include `needs_review`.
   - Confirm dream cycle does not mutate protected statuses.
   - Add static/source checks in `tests/test_suite.py`.
   - Status: done for recall policy and protected-status tests; dream remains
     conservative by policy and existing behavior.

7. Run verification.
   - Focused tests after each chunk.
   - Compile touched modules.
   - Real-graph no-touch recall diagnostic.
   - Full `tests/test_suite.py`.
   - Status: done.

Implementation progress:

- Done: `memory_evolution.py` with property normalization, status transitions,
  provenance/evidence merge helpers, supersession helpers, and rolling journal.
- Done: manual memory tool saves/updates now mark high-authority user edits,
  conflict detections now mark `needs_review`, and explicit search includes
  status/provenance fields while still touching only shown results.
- Done: thread extraction preserves nested provenance, marks concrete conflicts
  for review, and avoids overwriting high-authority memories on conflict.
- Done: document map-reduce extraction stores document/resource provenance on
  hub and extracted entities while preserving source-label cleanup boundaries.
- Done: wiki vault import treats external markdown edits as high-authority user
  intent, preserves properties, restores active status unless explicitly set,
  and keeps export mtime behavior compatible with `check_vault_sync()`.
- Done: auto-recall skips `needs_review` and `superseded` memories by default,
  while history queries can still consider superseded memories.
- Done: focused Phase 4 tests, focused recall tests, compile checks, full suite,
  and live no-touch recall sanity check.

Expected result:

- Contradictory memories are not silently merged or recalled.
- External wiki edits are treated as user intent.
- Document map-reduce provenance remains attached to generated memories.
- Document deletion/reprocessing remains predictable.
- Dream cycle stays useful but conservative.
- Auto-recall becomes safer without adding low-value complexity.

Real-world validation plan:

1. Live no-touch recall audit.
   - Run against the real app data directory.
   - Do not call mutation paths such as `touch_recalled()`,
     `graph_enhanced_recall()`, manual save/update, extraction save, wiki
     import, or delete.
   - Sample real subjects and aliases.
   - Measure hit rate, top-1, top-3, and no-touch behavior.
   - Confirm greeting/runtime/file-only guardrails skip recall.

2. Cloned-real mutation suite.
   - Copy/backup the real knowledge graph into a workspace-local clone.
   - Set `THOTH_DATA_DIR` to the clone before importing Thoth modules.
   - Create only prefixed test memories/documents/wiki files.
   - Run all mutating Phase 4 touchpoints in the clone.
   - Clean up prefixed test entities and cloned wiki artifacts at the end.

3. Manual memory evolution cases.
   - Create `__PHASE4_TEST__` memories.
   - Verify contradiction handling marks `needs_review` and does not overwrite.
   - Verify explicit search shows status/review fields and only touches shown
     results.
   - Verify manual update restores `active`, sets `last_user_modified_at`, and
     records actor `manual`.

4. Recall integration cases.
   - Active memory can be selected for direct query.
   - `needs_review` is skipped by auto-recall.
   - `superseded` is skipped by default.
   - `superseded` can be considered for history/old/previous queries.
   - Candidate retrieval remains no-touch.

5. Extraction/document cases.
   - `_dedup_and_save()` preserves provenance and avoids overwriting
     high-authority memories on conflict.
   - Document map-reduce creates a hub with resource provenance.
   - Extracted document entities keep `source=document:<name>` and resource
     provenance.
   - Delete-by-source removes document test entities.

6. Wiki external edit cases.
   - Export a cloned test memory to a cloned wiki vault.
   - Modify markdown externally.
   - `check_vault_sync()` detects the edit.
   - `import_from_vault()` updates the DB as high-authority wiki intent.
   - A fresh Thoth export does not create false sync.

7. Journal/audit cases.
   - `memory_evolution_journal.json` records conflict review, manual update,
     wiki import, and supersession.
   - Journal writes remain bounded and best-effort.

8. Quality gates.
   - Live exact-subject top-1 should be at least 90%.
   - Live alias top-3 should be at least 90%.
   - All live audit checks must be no-touch.
   - Deterministic clone mutation assertions should pass at 100%.
   - Cleanup must leave zero prefixed test entities in the clone.

Real-world validation results:

- Done: automated harness saved at `.tmp/run_phase4_real_world_tests.py`.
- Done: real `~/.thoth/memory.db` was copied via SQLite backup into
  `.tmp/phase4_real_fixture`; mutation tests ran only against the clone.
- Done: results saved at `.tmp/phase4_real_world_results.json` and
  `.tmp/phase4_real_world_results.md`.
- Result: 22/22 checks passed.
- Recall quality on cloned real memories:
  - exact subject: 12/12 hit, 12/12 top-1, no-touch true.
  - alias: 8/8 hit, 7/8 top-1, 8/8 top-3, no-touch true.
- Passed touchpoint checks:
  - guardrail recall skips
  - manual conflict review
  - explicit search status output and shown-result touch path
  - manual high-authority update
  - active recall selection
  - `needs_review` recall skip
  - `superseded` default skip and history-query availability
  - extraction conflict protection
  - document/resource provenance
  - document map-reduce provenance and delete-by-source cleanup
  - wiki external edit detection/import
  - evolution journal entries
  - prefixed test entity cleanup
- Issue found and fixed during validation: manual memory updates set
  high-authority properties but did not append a `user_modified` journal entry.
- Harness adjustment: synthetic active-recall probe now uses a unique anchor
  subject to avoid colliding with real city/place memories.

### Phase 5 - Knowledge Audit UX

Final design choices:

- Keep Phase 5 inside Settings > Knowledge. Do not add a new top-level page.
- Use the existing shared entity editor for both Settings and graph edits.
- Add a compact needs-review queue instead of a full conflict/diff console.
- Do not add pinning yet. Pinning should wait until recall policy has a clear
  pinned-memory behavior; otherwise it creates fake control.
- Make recall trace visible but collapsed by default.
- Show provenance/evidence as readable summaries, not raw JSON.
- Keep graph changes minimal: enrich the graph detail card with status/source
  metadata when cheap, but avoid status colors or dense graph visual changes in
  this phase.
- Put quick actions in list/detail views for Archive, Restore, and Resolve
  Review. Keep Supersede in the editor because it needs a target memory ID and
  should feel deliberate.

Goal:

- Let the user answer: "What does Thoth know, why does it know it, did it use
  it, and how can I correct it?"

Current touch-point findings:

- `ui/settings.py::_build_knowledge_tab()` is the main Knowledge UI surface.
  It already has graph metrics, wiki vault controls, category/search browsing,
  bulk delete, per-memory details, Delete, and Edit.
- `ui/settings.py` already opens `ui.entity_editor.open_entity_editor()` from
  the Browse knowledge details card.
- `ui/graph_panel.py` already has a hidden `graph-edit-trigger` button. The
  graph detail card stores the clicked node ID in `window._thothGraph` and opens
  the same `ui.entity_editor.open_entity_editor()` function. Therefore graph
  edits and Settings edits can share one upgraded editor.
- `ui/entity_editor.py` currently edits subject, type, description, aliases,
  tags, and relations. It saves directly through `knowledge_graph.update_entity`,
  so Phase 5 must preserve normal field editing while also marking manual/high
  authority changes through `memory_evolution.mark_user_modified()` or an
  equivalent helper.
- `memory_evolution.py` already provides statuses, provenance merge helpers,
  `set_status`, `mark_needs_review`, `mark_superseded`, `mark_user_modified`,
  and `get_journal`.
- `memory_policy.py` already writes compact recall traces to
  `memory_recall_trace.json`.
- `memory.py` remains a compatibility wrapper around `knowledge_graph.py`; UI
  code can keep using it for browsing but should parse entity properties through
  audit helpers rather than duplicating JSON logic inline.

Implementation plan:

1. Add `ui/knowledge_audit.py` as a small pure-helper module.
   - Parse memory/entity `properties` safely.
   - Normalize display status, tier, source bucket, confidence, recalled time,
     evidence count, source context, supersession fields, and review reason.
   - Provide `filter_memories(memories, status, source, tier, query)` for the
     Knowledge tab.
   - Provide `audit_summary(entity_or_memory)` for row chips/details.
   - Provide `load_recent_recall_traces(limit=10)` by reading
     `memory_policy._RECALL_TRACE_FILE` defensively.
   - Provide `load_recent_evolution_journal(limit=20)` via
     `memory_evolution.get_journal()`.

2. Update Settings > Knowledge overview.
   - Add status metric chips: Active, Needs review, Superseded, Archived.
   - Add source/tier summary chips when values exist.
   - Keep the existing graph and wiki vault sections intact.

3. Update Browse knowledge filters and rows.
   - Keep category and search.
   - Add Status filter: All, Active, Needs review, Superseded, Archived.
   - Add Source filter: All, Manual/live, Extraction, Document, Wiki/dream,
     Other.
   - Add Tier filter: All, Core, Semantic, Episodic, Resource.
   - Render compact chips on each memory row for status, tier, source bucket,
     confidence, and review state.
   - Style `needs_review` as attention-worthy and `archived`/`superseded` as
     muted, but keep the layout calm.

4. Add a Needs Review queue above Browse knowledge.
   - Show only `needs_review` memories, capped to a small number with a count.
   - Show subject, type, reason, source, and compact candidate/provenance info.
   - Actions: Edit, Resolve Review, Archive.
   - Resolve Review should set status to `active` and clear review fields through
     the evolution layer.

5. Upgrade `ui/entity_editor.py`.
   - Keep it as the single shared full editor used by Settings and the graph.
   - Add an Audit/Provenance expansion with status, source, tier, confidence,
     source context, evidence count/snippets, recalled_at, recall_count,
     supersedes/superseded_by, and review reason.
   - Add action buttons: Archive, Restore, Resolve Review.
   - Add a deliberate Supersede field/action where the user enters the
     replacement memory ID.
   - On normal Save, call `kg.update_entity` for the visible fields, then call
     `memory_evolution.mark_user_modified(actor="manual", source_context=...)`
     so manual edits preserve provenance and become high-authority corrections.
   - After status/evolution actions, refresh the loaded entity in the dialog and
     call `on_saved` so Settings refreshes.

6. Add recall trace and evolution journal sections.
   - Add collapsible "Recent recall decisions" in Settings > Knowledge.
   - Show timestamp, allowed/skipped, reason, selected count, block size, and top
     scores/rejections compactly.
   - Add collapsible "Memory change log" showing recent evolution journal rows:
     action, actor, reason, status transition, and entity IDs.
   - These views are observational only; they must not touch `recalled_at`.

7. Lightly enrich the graph detail card.
   - Extend `knowledge_graph.graph_to_vis_json()` node extras with normalized
     status, tier, confidence, review reason, superseded_by, and recalled_at.
   - Show those fields in the existing graph detail card only as compact
     metadata.
   - Keep the existing Edit link. It already opens the shared editor, so the
     full editing/provenance controls will be available from graph details after
     the editor upgrade.

8. Testing plan for Phase 5.
   - Add focused tests for `ui/knowledge_audit.py` filtering and summaries.
   - Add tests that the shared editor source imports/uses `memory_evolution` and
     preserves the graph/settings single-editor path.
   - Add tests that Settings exposes status/source/tier filters, needs-review
     queue, recall trace, and evolution journal sections.
   - Add tests that `graph_to_vis_json()` includes audit metadata without
     breaking existing node fields.
   - Run targeted tests first:
     `pytest tests/test_memory_evolution.py tests/test_memory_recall_uplift.py`
   - Then run full validation:
     `python tests/test_suite.py`

Implementation progress:

- Done: added `ui/knowledge_audit.py` with pure helpers for property parsing,
  audit summaries, status/source/tier filtering, status counts, recall trace
  loading, and evolution journal loading.
- Done: added focused tests for audit summaries, filters, trace loading, graph
  metadata, and Phase 5 UI/editor source wiring.
- Done: Settings > Knowledge now shows status summary chips, status/source/tier
  filters, a compact Needs Review queue, per-memory audit chips/details,
  Archive/Restore/Resolve actions, Recent recall decisions, and Memory change
  log. Status chips refresh after list/editor correction actions.
- Done: `ui.entity_editor.open_entity_editor()` remains the shared editor used
  by both Settings and graph details, now with an Audit and Provenance panel,
  Archive/Restore/Resolve controls, deliberate Supersede-by-ID, and manual
  save provenance through `memory_evolution.mark_user_modified()`.
- Done: `knowledge_graph.graph_to_vis_json()` now includes compact audit
  metadata for graph nodes, and the graph detail card displays the metadata
  while preserving the existing shared Edit path.
- Verified: `pytest tests/test_knowledge_audit.py --basetemp
  .tmp/pytest-phase5-audit` passed 5/5.
- Verified: `pytest tests/test_knowledge_audit.py tests/test_memory_evolution.py
  tests/test_memory_recall_uplift.py --basetemp .tmp/pytest-phase5-targeted`
  passed 30/30.
- Verified: touched modules compile.
- Verified: full `python tests/test_suite.py` passed with 1911 PASS, 0 FAIL,
  4 WARN.
- Issue found and fixed during validation: `ui/knowledge_audit.py` needed to be
  added to `installer/thoth_setup.iss` so packaged builds include the new UI
  helper.
- Superseded runtime validation note: Normal Chat auto-mode briefly used a
  hidden send-time Ollama tool probe and recoverable Chat Only fallback for
  local tool-schema failures. That was later found to make the visible Agent
  Mode badge diverge from the actual runtime. The Runtime Mode Repair section
  below is now authoritative: visible readiness and send-time readiness share
  the same decision, and Agent turns no longer silently downgrade to Chat Only.

Expected result:

- The user can inspect why Thoth remembered something, see whether recall used
  it, and correct/archive/supersede it from both Settings and graph detail
  entry points without changing Chat Only semantics or regressing Agent recall.

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

## Runtime Mode Repair Implementation Memory

### 2026-05-26 Diagnosis

Observed failure thread: `5cf5a899dd27`.

Symptoms:

- The UI banner showed `Agent Mode` for `model:ollama:qwen3.6:27b`.
- The actual send path routed the turn through dedicated Chat Only runtime.
- The model then obeyed the Chat Only system prompt and told the user tools were
  unavailable.
- A later turn in the same thread used `thoth_status`, proving the thread could
  later run with tools enabled and that the behavior was genuinely inconsistent.

Root causes:

1. Normal chat sends `runtime_mode="auto"`, so the visible mode is not the
   actual runtime contract.
2. The model banner evaluates readiness without an Ollama tool probe.
3. The send path evaluates readiness with an Ollama tool probe.
4. For large local Ollama models such as `qwen3.6:27b`, the live tool probe can
   time out; timeout was treated as Chat Only rather than unknown/failed Agent
   verification.
5. `stream_agent()` silently fell back from Agent-capable auto mode to Chat Only
   and injected the Chat Only prompt.
6. Chat Only prompt behavior could claim "committed to memory" even when
   long-term memory tools were unavailable.

Implementation requirements:

- Make the visible runtime mode and send-time runtime mode share one decision.
- Do not silently downgrade a visible Agent Mode turn to Chat Only.
- Treat Ollama probe timeout as an Agent verification failure/unknown state, not
  as proof the model should be run as Chat Only.
- If Chat Only is selected, the UI must honestly show Chat Only before the user
  sends.
- Chat Only must not claim long-term memory writes. It may only say the detail
  can remain in the current thread.
- If a thread transitions from Chat Only to Agent Mode, Agent Mode must include
  an authoritative current-runtime statement so stale Chat Only claims in
  transcript history do not override tool availability.
- Add tests for banner/send agreement, no silent downgrade, Chat Only memory
  wording, and Agent transition authority.

### 2026-05-26 Implementation Progress

- Done: normal-chat `auto` runtime now uses the same readiness path as the
  visible model banner (`probe_ollama_tools=False`), removing the hidden
  stricter send-time Ollama probe.
- Done: `stream_agent()` no longer silently falls back from an Agent-capable
  `auto` turn into Chat Only after a tool-schema/tool-support error.
- Done: Agent Mode injects a current-runtime system context that says Agent
  Mode is active, tools are available through the provided interface, and stale
  Chat Only claims in earlier transcript messages must not override the current
  turn.
- Done: Chat Only prompt now explicitly says memory/save/forget requests cannot
  be written to long-term memory and can only be kept in the current thread.
- Done: Ollama probe timeout is now treated as an inconclusive/blocked Agent
  verification result, not proof that the model should be routed to Chat Only.
- Done: focused `tests/test_chat_only_runtime.py` coverage updated for no
  hidden Ollama probe, no silent Chat Only fallback, Chat Only memory wording,
  and Agent transition authority.
- Verified: `pytest tests/test_agent_readiness.py tests/test_chat_only_runtime.py
  --basetemp .tmp/pytest-runtime-readiness` passed 26/26.
- Verified: `pytest tests/test_model_picker_regressions.py
  tests/test_agent_readiness.py tests/test_chat_only_runtime.py --basetemp
  .tmp/pytest-runtime-mode-2` passed 37/37.
- Verified: `pytest tests/test_provider_runtime.py tests/test_context_policy.py
  --basetemp .tmp/pytest-runtime-provider` passed 54/54.
- Verified: full `python tests/test_suite.py` passed with 1912 PASS, 0 FAIL,
  4 WARN.

### 2026-05-27 Conservative Runtime Truth Follow-Up

Observed failure thread: `c6b594623608`.

Symptoms:

- The first turn correctly used the real `thoth_status` tool in Agent Mode.
- After switching the thread to a Chat Only model, later checkpoints were
  written by the dedicated Chat Only runtime.
- Later assistant messages nevertheless claimed to use a non-existent
  `thoth_check` tool and produced "latest news" prose without a web-search
  tool call.

Conservative fix scope:

- Improve structured runtime logging so every send records the requested
  runtime, selected runtime, effective model, thread override, readiness
  reason, context window, enabled tool count, and whether tools are bound.
- Review and update `thoth_status` so model/runtime/tool reporting matches the
  current runtime architecture, including thread overrides and Chat Only
  readiness.
- Lightly harden Chat Only history construction by removing rich historical
  Agent tool result bodies from the Chat Only prompt context and replacing them
  with neutral historical markers.
- Keep Agent Mode behavior unchanged: no broad Agent prompt edits, no
  brittle intent routing, and no post-generation rewrite layer.
- Add focused tests for Chat Only history sanitization, runtime logging shape,
  status model/runtime reporting, and existing Agent tool binding behavior.

UX goals:

- The visible banner, status tool, logs, and checkpoint source should agree
  about the effective runtime.
- Chat Only should remain honest and lightweight without becoming hidden Agent
  Mode.
- Debugging future mode/tool issues should not require reconstructing state
  from checkpoint internals.

Implementation progress:

- Done: added lightweight runtime context tracking and structured runtime
  decision logs for Agent, Chat Only, blocked, invoke, and resume paths without
  changing Agent prompts or tool routing.
- Done: Chat Only history now keeps neutral markers for earlier Agent tool use
  instead of injecting full historical tool result bodies.
- Done: `thoth_status` model reporting now includes runtime model, provider,
  readiness, active turn runtime, and clearer override/default wording.
- Done: `thoth_status` skill reporting now separates manual skills from tool
  guides in user-facing status output.
- Verified: `pytest tests/test_chat_only_runtime.py
  tests/test_developer_studio_phase10.py --basetemp .tmp/pytest-runtime-truth`
  passed 54/54.
- Verified: `pytest tests/test_agent_readiness.py tests/test_provider_runtime.py
  tests/test_context_policy.py --basetemp .tmp/pytest-runtime-related`
  passed 67/67.
- Verified: `python tests/test_suite.py` passed with 1908 PASS, 0 FAIL, 5
  WARN.
- Cross-check: implementation stayed within conservative scope. It added
  observability, status accuracy, and Chat Only context sanitization only; no
  Agent prompt expansion, explicit tool-intent router, or post-generation
  rewrite layer was added.

### 2026-05-27 Thinking Bubble Retention Follow-Up

Observed symptom:

- Thinking/reasoning tokens stream live from local thinking models, but the
  collapsed Thinking disclosure can disappear after the final answer renders or
  after the transcript refreshes.

Likely root cause:

- `consume_generation()` accumulates streamed reasoning in `gen.thinking_text`,
  but the final assistant UI message appended to `state.messages` does not
  persist that text as `message["thinking"]`.
- Reattach paths reconstruct in-memory assistant messages without carrying
  `_reattach_gen.thinking_text`.

Conservative fix scope:

- Persist `gen.thinking_text.strip()` into final assistant UI message dicts as
  `thinking`.
- Carry thinking through active/done reattach paths.
- Keep model/runtime behavior unchanged; no prompt edits and no reasoning
  extraction changes unless tests show a backend drop.
- Add focused tests for final UI message retention and reattach-message
  reconstruction.

Implementation progress:

- Done: added `ui.helpers.attach_thinking_to_message()` and used it when final
  streaming messages are appended to `state.messages`.
- Done: carried `_reattach_gen.thinking_text` through active and completed
  generation reattach paths so transcript refreshes keep the collapsed
  Thinking disclosure.
- Done: added `tests/test_thinking_retention.py` covering message attachment,
  final streaming persistence, reattach preservation, and render-path support.
- Verified: `pytest tests/test_thinking_retention.py --basetemp
  .tmp/pytest-thinking-retention` passed 5/5.
- Verified: `pytest tests/test_thinking_retention.py
  tests/test_chat_only_runtime.py tests/test_developer_studio_phase10.py
  --basetemp .tmp/pytest-thinking-related` passed 59/59.
- Verified: `python tests/test_suite.py` passed with 1908 PASS, 0 FAIL, 5
  WARN.
- Cross-check: implementation is scoped to UI state retention and reattach
  rendering. No model runtime, prompt, or reasoning-token extraction behavior
  was changed.

### 2026-05-27 Reasoning-Only Completion Follow-Up

Observed failure threads: `e59a7689d153` and `2513540578a1`.

Symptoms:

- The local thinking model entered the Reasoning state, then the UI moved to
  Done without showing any final answer.
- Checkpoints contain an AI message with empty `content` and populated
  `additional_kwargs.reasoning_content`.
- Logs show no API error and no `generation.token/Writing` transition for the
  two newest runs.

Root cause:

- Some thinking-model completions can end with reasoning content only and no
  final answer text.
- The UI finalization path only considered final answer text, tools, charts,
  images, and videos as output. Reasoning-only generations were not appended to
  `state.messages`, so transcript refreshes made them disappear.

Conservative fix scope:

- Treat non-empty `gen.thinking_text` as final output for UI persistence.
- Persist it through the existing `thinking` message field.
- Keep model/runtime behavior unchanged.

Implementation progress:

- Done: `_has_final_output` now treats `gen.thinking_text` as output so
  reasoning-only completions are appended to `state.messages`.
- Done: completed-generation reattach now reconstructs a message when either
  final answer text or thinking text exists.
- Done: `tests/test_thinking_retention.py` covers the reasoning-only final
  output case.
- Verified: `pytest tests/test_thinking_retention.py --basetemp
  .tmp/pytest-thinking-retention-2` passed 6/6.
- Verified: `pytest tests/test_thinking_retention.py
  tests/test_chat_only_runtime.py tests/test_developer_studio_phase10.py
  --basetemp .tmp/pytest-thinking-related-2` passed 60/60.
- Verified: `python tests/test_suite.py` passed with 1908 PASS, 0 FAIL, 5
  WARN.
- Cross-check: implementation is scoped to UI persistence and completed-run
  reattach behavior. No model/runtime behavior, prompts, tool policy, or
  reasoning-token extraction logic was changed.

### 2026-05-27 Reasoning Completion Diagnostics and Harness

Goal:

- Do not add the recovery fix yet.
- First add observability and a repeatable live harness to determine whether
  reasoning-only stops are caused by Ollama/Qwen, LangChain streaming, tool
  binding, or the full Thoth Agent path.

Implementation:

- Done: added structured stream-completion diagnostics in `agent.py`.
- Diagnostics now log answer chars/chunks, reasoning chars/chunks, tool call
  count, tool-result count, finish/done reason, checkpoint answer/reasoning
  chars, eval count, prompt eval count, loop/browser/user-stop flags, and
  runtime diagnostics.
- Done: added `scripts/reasoning_completion_harness.py` for live local-model
  matrix testing.
- Harness cases:
  - direct Ollama streaming API with `think=true`.
  - LangChain `ChatOllama` streaming with no tools.
  - LangChain `ChatOllama` streaming with one simple bound tool.
  - LangChain `ChatOllama` streaming with all enabled Thoth tools bound.
  - Full Thoth `stream_agent()` with real tools, memory recall, checkpoints,
    and disposable diagnostic threads.

Automated live results:

- One full matrix pass: 5/5 answered, 0 reasoning-only stops, 0 errors.
- Three repeated full matrix passes: 15/15 answered, 0 reasoning-only stops,
  0 errors.
- Five focused full-Agent passes: 5/5 emitted visible answer text, 0 exact
  reasoning-only stops, 0 errors.

Notable finding:

- One focused full-Agent run produced only a short visible pre-tool statement,
  then ended with an empty final AI message whose `reasoning_content` contained
  malformed tool-call-like text:
  `<function=wiki_read> <parameter=subject> Dark archives</parameter> ...`.
- That run had multiple tool calls and a large prompt (`prompt_eval_count`
  around 42k). It is not the exact original no-token failure, but it strongly
  suggests Qwen can drift into tool-call syntax inside the thinking channel
  after tool use, then stop normally with `done_reason=stop`.

Current interpretation:

- Direct Ollama and plain LangChain are healthy for this prompt.
- Binding many tools and full Agent context did not consistently reproduce
  the original failure, but full Agent runs show higher variance, occasional
  unnecessary tool use, and at least one malformed thinking-channel tool-call
  stop.
- Next planning should focus on conservative detection/recovery for empty
  final answers and malformed reasoning-channel tool-call stops, plus whether
  the Agent should avoid tool use for purely conceptual prompts.

Verification:

- `py_compile agent.py scripts/reasoning_completion_harness.py
  tests/test_thinking_retention.py` passed.
- `pytest tests/test_thinking_retention.py tests/test_chat_only_runtime.py
  tests/test_agent_readiness.py --basetemp .tmp/pytest-reasoning-diagnostics`
  passed 35/35.
- `python tests/test_suite.py` passed with 1912 PASS, 0 FAIL, 4 WARN.

### 2026-05-27 Knowledge Edit Crash Investigation

Manual test context:

- Settings > Knowledge.
- Search Browse Knowledge for `MANUALQA-20260527`.
- Click Edit.
- Expected: editor opens with normal fields plus Audit/Provenance.
- Observed: editor took a while, flashed once, then the Settings dialog
  collapsed/crashed without an obvious Python traceback.

Investigation findings:

- Main app logs and window logs show no backend traceback around the crash.
- No explicit client-error crash report was found.
- The two matching `MANUALQA-20260527` entities are small and structurally
  normal:
  - simple fact entities.
  - small descriptions.
  - simple audit properties.
  - no relations.
- The notable log clue is memory pressure: RSS jumps from roughly 1.76 GB to
  roughly 6.31 GB near the crash window.
- Current Knowledge editor eagerly builds:
  - Audit/Provenance.
  - Relations.
  - Add Relation controls.
  - A peer selector backed by up to 500 entity options on every editor open.
- The Browse Knowledge page also renders matching memory rows as expansion
  components, and the editor opens as a nested modal above the Settings dialog.

Current interpretation:

- The issue is probably not corrupted `MANUALQA` data.
- Most likely causes are client/UI render pressure, NiceGUI component
  reconciliation, or nested-dialog lifecycle issues.
- Eager relation-selector construction is a low-value, high-risk render cost.

Proposed next implementation:

- Add editor-open observability first:
  - log start/success/failure.
  - include entity id, render timings, relation count, peer-option count.
  - add stability snapshots before/after Knowledge tab render and editor open.
  - wrap edit callbacks so UI failures are persisted through
    `record_ui_callback_error`.
- Reduce eager UI work:
  - do not load peer relation options on editor open.
  - lazy-load peer options only when Add Relation is expanded.
  - consider paging/virtualization for Browse Knowledge if render pressure
    remains.
- Harden nested dialog behavior:
  - keep the editor safe when opened from Settings.
  - avoid parent Settings refresh/rebuild while the editor is open.
  - show an error notification instead of collapsing Settings on editor
    failure.
- Add tests:
  - MANUALQA-shaped entity editor smoke test.
  - audit/provenance rendering with recalled/user-modified props.
  - relation options are lazy and not loaded until Add Relation is opened.
  - browser/manual harness for Settings > Knowledge > search > Edit > save tag.

## UI Performance Overhaul Plan

### Context

Performance issues are now visible in multiple surfaces, not only the
Knowledge editor:

- Settings tabs can render large component trees synchronously.
- Knowledge Browse renders many expansion components and nested details.
- Entity editor opens a nested modal and eagerly builds relation controls.
- Model/provider settings can spend seconds collecting catalogs/options.
- Streaming/transcript refresh paths already have special detached behavior,
  which means UI lifecycle complexity is growing.
- Logs show event-loop lag and memory spikes, including an RSS jump from
  roughly 1.76 GB to roughly 6.31 GB around the Knowledge edit crash window.

The goal is a whole-app UI performance architecture, not one-off local
optimizations.

### Design Principles

1. **Shell first, data later**
   - UI surfaces must open immediately with lightweight skeletons or
     placeholders.
   - Expensive data is loaded asynchronously after the shell renders.
   - Users should see progress within ~100-200 ms even if data takes seconds.

2. **Lazy by default**
   - Heavy sections render only when opened or when their data is visible.
   - Details, relation pickers, provenance, graph neighborhoods, logs, traces,
     and model catalogs should not load on initial tab/dialog render.

3. **Bounded rendering**
   - No UI path should render unbounded rows/components.
   - Lists use paging, incremental loading, or virtualization.
   - Expansions render their body only when opened.

4. **Cancellable and generation-safe async work**
   - Every long UI load gets a generation/request id.
   - If the user switches tabs, searches again, or closes a dialog, stale
     results are ignored.
   - If possible, long background work is cancellable.

5. **Single owner per modal surface**
   - Avoid nested full-screen modal rebuilds.
   - Prefer a drawer/detail panel inside an existing settings surface, or a
     top-level editor modal managed independently.
   - Parent Settings should not rebuild while a child editor is open.

6. **Observable performance**
   - Important UI surfaces log render/query timings, row counts, payload sizes,
     memory snapshots, and client-side errors.
   - Slow thresholds produce warnings that identify the exact surface and
     phase.

7. **Graceful degradation**
   - A failed heavy subsection shows a local error state.
   - Settings/dialogs should not collapse because one subsection failed.
   - Core edit/save actions remain available when optional audit/provenance
     panels fail.

### Phase 1: Shared Performance Infrastructure

Scope:

- Add a small UI performance helper module, likely `ui/performance.py`.
- Integrate with existing `stability.log_performance_snapshot()` and
  `stability.record_ui_callback_error()`.

Implementation tasks:

1. Add timing helpers:
   - `timed_ui_section(name, **metadata)` context manager.
   - `log_ui_perf(name, elapsed_ms, rows=None, components=None, payload_chars=None, **metadata)`.
   - `warn_if_slow(name, elapsed_ms, threshold_ms=750, **metadata)`.

2. Add generation token helper:
   - lightweight `LoadGeneration` object with `next()`, `current`, and
     `is_current(token)`.
   - Use in Settings tab loading, search refreshes, and async loaders.

3. Add safe callback wrappers:
   - `safe_ui_callback(context, fn)` for sync callbacks.
   - `safe_ui_task(context, coro)` alignment with existing
     `ui.timer_utils.safe_ui_task`.
   - Persist failures through `record_ui_callback_error`.

4. Add client-side error verification:
   - Confirm existing browser-side JS error reporting is installed on main UI.
   - Add route/log metadata so client errors identify active surface/tab when
     possible.

5. Define standard thresholds:
   - UI shell render warning: > 500 ms.
   - Data load warning: > 1000 ms.
   - Component count warning: > 250 components in one surface.
   - Payload warning: > 250 KB serialized UI data for a single refresh, if
     measurable.

Tests:

- Unit tests for timing/generation helpers.
- Safe callback wrapper records errors without raising into the UI loop.
- Existing tests continue to pass.

### Phase 2: Settings Shell And Tab Lifecycle

Scope:

- `ui/settings.py` overall dialog shell and lazy tab loading.

Implementation tasks:

1. Keep Settings shell instant:
   - Render tabs and an empty content panel first.
   - Keep current lazy tab loading but add timings and generation safety.
   - Show skeleton/loading row immediately.

2. Prevent stale tab loads:
   - Increment generation on every tab switch and Settings close/reopen.
   - Old deferred callbacks must no-op if generation is stale.

3. Localize tab render failures:
   - On builder exception, render an inline tab error panel.
   - Do not close Settings.
   - Persist diagnostic with active tab name.

4. Avoid parent rebuild while child editor is open:
   - Track `settings_child_modal_open`.
   - Status/list refresh callbacks should defer or skip full `_reopen()` while
     child modal is active.

5. Instrument:
   - `settings.open.shell`.
   - `settings.tab.load.<tab>`.
   - `settings.tab.render.<tab>`.
   - memory snapshot on slow tab render or after large render.

Tests:

- Settings tab builder exceptions produce inline error.
- Stale generation token prevents old tab from overwriting current tab.
- Child modal open suppresses parent reopen.

### Phase 3: Knowledge Tab Performance Redesign

Scope:

- `ui/settings.py` Knowledge tab.
- `ui/entity_editor.py`.
- `ui/knowledge_audit.py`.
- `knowledge_graph.py` / `memory.py` list APIs as needed.

Implementation tasks:

1. Add lightweight list API:
   - `knowledge_graph.list_entity_summaries(...)` or equivalent.
   - Returns only fields needed for browse rows:
     `id`, `subject`, `entity_type`, `source`, `tags`, `updated_at`,
     audit summary fields, maybe a short description preview.
   - Does not load heavy relation/provenance details.

2. Paginate Browse Knowledge:
   - Initial page size: 25 or 50.
   - Add "Load more" button.
   - Search/filter resets to first page.
   - Display total/visible count.

3. Lazy expansion body:
   - Row header renders summary badges only.
   - Description/provenance/relations load only when expansion opens.
   - Relation previews are capped.

4. Debounce search:
   - 250-350 ms debounce.
   - Use generation token so stale searches do not overwrite newer results.

5. Editor modal performance:
   - Add `entity_editor.open.start/success/failure` logs.
   - Render core fields immediately.
   - Render Audit/Provenance as a lazy expansion body.
   - Render Relations as a lazy section.
   - Do not load Add Relation peer options until Add Relation expansion opens.
   - Cap peer search results and use searchable async query instead of 500
     eager options.

6. Nested dialog hardening:
   - Editor owns its own error boundary.
   - If audit/relations fail, show local section error and keep editor open.
   - Parent Knowledge callbacks after save refresh visible rows only, not the
     whole Settings dialog.

7. Memory pressure checks:
   - Snapshot before/after Knowledge tab render.
   - Snapshot before/after editor open.
   - Warning if RSS increases by > 250 MB during a UI action.

Tests:

- MANUALQA-shaped entity editor smoke test.
- Browse Knowledge initially renders only first page.
- Search for `MANUALQA-20260527` returns expected rows.
- Relation options are not loaded until Add Relation is opened.
- Editor save with tag `manual-qa` updates entity and refreshes row.
- Browser test/manual harness: Settings > Knowledge > search > Edit > save.

### Phase 4: Model And Provider Settings Performance

Scope:

- `ui/settings.py` Models/Providers tab.
- Provider catalog/cache paths.

Implementation tasks:

1. Split model settings into shell + async data blocks:
   - Current selection card renders immediately.
   - Catalog/cache status loads in background.
   - Large model option maps load only when picker or catalog section opens.

2. Reuse cached snapshots:
   - Never perform model catalog refresh synchronously during tab render.
   - Show "last refreshed" plus explicit refresh action.

3. Add progressive disclosure:
   - Everyday model picker first.
   - Advanced catalog/pinning section lazy.
   - Context size controls render from cached/current values.

4. Instrument:
   - `settings.models.collect`.
   - `settings.models.render`.
   - local Ollama list timing.
   - cloud catalog cache timing.
   - option count and build time.

Tests:

- Models tab renders without network/catalog refresh.
- Catalog section lazy-loads when opened.
- Existing model picker behavior remains compatible.

### Phase 5: Graph Panel And Knowledge Map

Scope:

- `ui/graph_panel.py`.
- Graph JSON generation in `knowledge_graph.py`.
- Entity editor entry point from graph details.

Implementation tasks:

1. Use neighborhood loading:
   - Initial graph loads bounded nodes/edges.
   - Search/select entity loads a local neighborhood.
   - Large full graph requires explicit user action.

2. Cap graph payload:
   - Include payload size/count diagnostics.
   - Warn if graph JSON exceeds threshold.

3. Details panel:
   - Render summary first.
   - Load relations/provenance lazily.
   - Edit button opens the same optimized editor.

4. Client-side safety:
   - Capture JS errors from graph rendering.
   - Show a graph-local error state instead of breaking Settings/app shell.

Tests:

- Graph JSON respects node cap.
- Detail edit uses shared editor.
- Large graph warning path covered.

### Phase 6: Chat Transcript And Streaming UI

Scope:

- `ui/streaming.py`.
- `ui/chat.py`.
- transcript render helpers.

Implementation tasks:

1. Preserve current detached-generation behavior.
2. Add instrumentation:
   - transcript render time.
   - message count.
   - media count.
   - thinking chars and answer chars on finalization.
3. Bound expensive render work:
   - Avoid full main rebuild after detached finalization where possible.
   - Lazy render heavy media/transcript attachments.
   - Keep thinking bubble collapse cheap and capped.
4. Stale generation safety:
   - Ensure reattach/finalize does not update deleted clients or stale threads.

Tests:

- Existing thinking retention tests.
- Detached finalization remains stable.
- Large thinking text remains capped.

### Phase 7: Documents, Wiki, And Memory Change Logs

Scope:

- Document status panels.
- Wiki vault status/check.
- Recent recall decisions.
- Memory change log.

Implementation tasks:

1. Lazy-load logs and traces:
   - Recent recall decisions loads when expansion opens.
   - Memory change log loads when expansion opens.
   - Wiki modification check is explicit or cached; avoid heavy filesystem scan
     during tab render unless user asks.

2. Cache lightweight status:
   - Use last-known counts/status for immediate display.
   - Refresh button updates status asynchronously.

3. Bound filesystem scans:
   - Cap displayed rows.
   - Log scan time and file count.

Tests:

- Logs/traces are not read during initial Knowledge tab render.
- Opening expansion loads bounded rows.
- Wiki check failures stay local to section.

### Phase 8: UX Standards For Loading And Errors

Scope:

- App-wide loading/error behavior.

Implementation tasks:

1. Standard skeletons:
   - Small inline skeleton for section data.
   - Larger centered skeleton for full tab first load.
   - Use consistent copy and spinner styling.

2. Local error panels:
   - "Could not load this section" with Retry button.
   - Keep the rest of the tab usable.

3. Slow-load affordance:
   - If loading > 2s, show more specific status text.
   - If still loading > 8s, show retry/cancel.

4. Avoid surprise full rebuilds:
   - Prefer local refresh of affected section.
   - Full `_reopen()` only when truly necessary.

Tests:

- Error panel component can retry.
- Long-load state appears after threshold in testable helper logic.

### Phase 9: Automated Performance Harness

Scope:

- New `scripts/ui_performance_harness.py` or Playwright/browser-based harness.

Implementation tasks:

1. Define repeatable scenarios:
   - Open Settings shell.
   - Switch to Knowledge.
   - Search `MANUALQA-20260527`.
   - Open Edit.
   - Save harmless tag.
   - Open Models tab.
   - Open Graph panel/details/edit.
   - Render chat transcript with thinking bubble.

2. Collect metrics:
   - elapsed time per action.
   - browser console errors.
   - client error crash reports.
   - RSS before/after.
   - number of rows/components where measurable.

3. Add budgets:
   - Settings shell visible < 300 ms target.
   - Knowledge initial shell < 500 ms target.
   - Search response < 500 ms target for typical local DB.
   - Editor core fields visible < 500 ms target.
   - No UI action increases RSS by > 250 MB unless explicitly expected.

4. Keep harness optional:
   - Not part of default unit suite.
   - Run manually or in local diagnostic mode.

### Implementation Order

1. Add shared `ui/performance.py` helpers and tests.
2. Instrument Settings shell/tab lifecycle.
3. Fix Knowledge editor eager work:
   - lazy Add Relation peer options.
   - editor-open diagnostics.
   - error boundary around edit callbacks.
4. Add Knowledge pagination/lazy expansion body.
5. Add browser/manual harness for the exact MANUALQA edit workflow.
6. Optimize Models/Providers tab collection.
7. Optimize Graph panel payload/detail loading.
8. Lazy-load logs/traces/wiki checks.
9. Add app-wide loading/error UX components.
10. Run focused tests, browser harness, then full `tests/test_suite.py`.

### Non-Goals And Guardrails

- Do not remove existing functionality.
- Do not hide data permanently; use progressive loading instead.
- Do not change memory semantics while optimizing UI.
- Do not make Chat Only behave like Agent Mode.
- Do not introduce network-dependent default tests.
- Avoid broad visual redesign; keep existing style but make it faster and more
  resilient.

### Done Criteria

- Settings opens reliably without heavy synchronous work.
- Knowledge search/edit flow for `MANUALQA-20260527` completes without dialog
  collapse.
- Editor core fields appear quickly, with audit/relations loaded lazily.
- Large lists are paginated or virtualized.
- Slow UI paths produce actionable diagnostics.
- Client-side errors are captured in crash reports.
- Full test suite passes.
- Optional UI performance harness produces a clear before/after report.

### 2026-05-27 UI Performance Overhaul Implementation Progress

Branch: `fix/ui-performance-overhaul`

Completed:

- Added shared UI performance helpers in `ui/performance.py`:
  `timed_ui_section`, `log_ui_perf`, slow-section warnings,
  `LoadGeneration`, `safe_ui_callback`, and `safe_ui_task`.
- Instrumented Settings shell and tab lifecycle:
  `settings.open.shell`, `settings.tab.load.<tab>`, and
  `settings.tab.render.<tab>`.
- Hardened Settings tab loading with generation-safe deferred renders,
  immediate loading placeholders, retryable inline error panels, and a
  child-modal guard so entity editor saves do not collapse the Settings
  dialog.
- Added lightweight knowledge summary APIs:
  `knowledge_graph.list_entity_summaries()` and
  `memory.list_memory_summaries()`.
- Bounded Browse Knowledge initial rendering to 25 rows with a Load More
  path, generation-safe debounced filter/search refresh, and summary-based
  row loading.
- Made Recent recall decisions and Memory change log lazy so they do not read
  logs/traces during the initial Knowledge tab render.
- Changed Wiki Vault sync detection from an automatic first-paint filesystem
  scan to an explicit `Check vault sync` action with local error display.
- Optimized entity editor open path:
  core fields render first; Audit/Provenance, Relations, and Add Relation
  peer lookup are lazy; relation peer search is capped at 50 and loaded only
  on demand.
- Optimized entity editor save path after manual QA showed a brief client
  disconnect: the save commit now runs off the UI loop, the Save button enters
  a loading state, and the Knowledge tab refresh is staged after the editor
  closes instead of rebuilding all Knowledge surfaces inside the click handler.
- Added editor open/render observability:
  `entity_editor.open.start`, `entity_editor.open.success`,
  `entity_editor.render.core`, `entity_editor.render.audit`,
  `entity_editor.render.relations`, and `entity_editor.save.commit`.
- Added Knowledge after-save observability:
  `settings.knowledge.after_save.data`.
- Followed up on tray quit reliability after manual logs showed frequent
  `Thoth graceful shutdown timed out` warnings: the launcher now gives app
  cleanup a 30-second graceful exit window, moves the hard quit watchdog to 75
  seconds, records graceful shutdown duration, and the app shutdown marker now
  preserves the shutdown reason.
- Improved Recent recall decisions readability without changing recall
  semantics: top-score rows now display memory subjects when available, with a
  bounded subject lookup for older trace entries that only recorded raw IDs.
- Improved Memory change log readability without changing journal semantics:
  rows now use friendly action/reason labels, show `status: active` when no old
  status was recorded, and resolve memory subjects through a bounded lookup
  instead of showing raw IDs by default.
- Fixed graph detail edit UX: the Edit control is now a real button in the
  detail header instead of a bottom `href="#"` link, avoiding the global
  external-link handler opening the Thoth home page in a separate browser.
- Added graph payload instrumentation and capped the initial graph panel
  request at 250 nodes while preserving local refocus behavior.
- Added transcript and streaming observability:
  `chat.transcript.render` and `streaming.consume_generation`, including
  message/update counts and thinking/answer character counts.
- Added optional `scripts/ui_performance_harness.py` with a launch-smoke mode
  and saved local run output to `.tmp/ui_performance_harness.json`.
- Updated the Windows installer manifest to include `ui/performance.py`.

Tests run:

- `.venv\Scripts\python.exe -m pytest tests/test_ui_performance.py tests/test_ui_performance_overhaul.py`
  - Passed: 11 tests after the save-path follow-up.
- `.venv\Scripts\python.exe -m pytest tests/test_knowledge_audit.py`
  - Passed: 5 tests with `TMP`/`TEMP` pointed at `.tmp`.
- `.venv\Scripts\python.exe -m pytest tests/test_app_port.py tests/test_app_stability_hardening.py`
  - Passed: 24 tests with `TMP`/`TEMP` pointed at `.tmp` after the tray quit
    follow-up.
- `.venv\Scripts\python.exe tests\test_suite.py`
  - Passed after allowing the suite to fetch the missing `tiktoken` encoding
    cache.
- `.venv\Scripts\python.exe scripts\ui_performance_harness.py --launch --port 8080 --timeout 70 --output .tmp\ui_performance_harness.json`
  - Passed: temporary app launched, `/api/launcher-ping` responded, `GET /`
    returned 200, app process terminated.

Remaining gaps and manual checks:

- Browser-level measurement of the full Settings > Knowledge >
  `MANUALQA-20260527` > Edit > add `manual-qa` tag > Save scenario should be
  repeated against a real user data set to confirm the follow-up removes the
  brief disconnect observed during manual QA.
- Models/Providers already use async/cached loading in the current code; this
  pass added shared instrumentation but did not deeply restructure provider
  catalog internals.
- Graph panel initial payload is bounded, but deeper server-side neighborhood
  fetching can be expanded later if very large graphs still feel heavy.
- Browse Knowledge rows are paginated, but row expansion bodies still render
  when NiceGUI opens the expansion; future work can move every detail body to a
  fully async row-detail loader if needed.

### 2026-05-27 Transcript Stability and Large Thread Performance Plan

Manual QA on a large existing thread showed a temporary disconnect banner,
slow initial transcript render, and a brief empty flash before the transcript
recovered. Local logs identified the latest updated thread as
`pt_399c8586ac` (`Daily AI Trends Report — May 27, 09:02 AM`). Loading
messages from storage was fast (`load_thread_messages total=0.010s`,
`raw=310`, `ui=168`), but UI rendering and follow-up refresh work were slow:

- `chat.transcript.render` took about 17.9 seconds for 168 UI rows.
- a second transcript render took about 4.4 seconds.
- event-loop lag reached about 11.7 seconds.
- token counter refreshes took about 4.1 seconds and 3.2 seconds.

Root cause and constraints:

- The detached-generation rerender was introduced for a real reliability bug:
  when a generation detaches because UI handles become stale, finalization must
  still show the completed assistant message if the user is on that thread.
- The detached finalization path must preserve optimistic `state.messages`
  entries and must not reload the active thread from checkpoint, because the
  checkpoint can lag behind the visible user message.
- The current scoped refresh avoids rebuilding the whole main area, but it
  still clears `p.chat_container` and synchronously rebuilds every transcript
  message. On large threads this causes the empty flash, duplicate render, and
  event-loop starvation.
- Fixes must preserve Agent Mode behavior, Chat Only semantics, streaming
  thinking retention, tool rendering, media persistence, and checkpoint/cache
  semantics.

Implementation-ready plan:

1. Keep the detached-finalize behavior but replace the mechanism:
   append the finalized assistant message to `state.messages` and synchronize
   only the missing transcript tail when possible.
2. Add lightweight per-client transcript render state:
   active thread id, render generation, rendered message keys, visible window
   bounds, and whether older messages are collapsed.
3. Replace full `refresh_chat_messages()` clear/rebuild with a safe
   `sync_chat_messages()` path:
   append missing tail messages for same-thread updates; use generation-safe
   progressive reconcile only when the rendered state is stale.
4. Bound large transcript first paint:
   render the latest window first for medium/large threads and expose a
   `Load earlier` path. Keep full `state.messages` unchanged.
5. Make transcript rendering cooperative:
   use adaptive, time-budgeted chunks; abort stale chunks when the thread or
   render generation changes.
6. Lazy-render heavy message internals where safe:
   collapse very large tool outputs, thinking blocks, charts, and huge
   markdown/code bodies behind user-opened sections.
7. Reduce token-counter/render contention:
   debounce/coalesce exact token counting by thread generation, discard stale
   results, and avoid repeated expensive counts during first paint.
8. Add resilience guardrails after the real fix:
   consider desktop-friendly NiceGUI reconnect/message-history settings, but
   do not rely on wider timeouts as the primary solution.
9. Add focused tests and harness coverage:
   detached finalize appends without full rebuild, optimistic messages remain
   intact, live finalization does not duplicate assistant messages, large
   transcripts are initially bounded, stale render generations abort, token
   counter jobs coalesce, and the real-world harness reports no empty flash or
   disconnect state on a large transcript.

Completed implementation:

- Added `ui/transcript.py` with transcript window selection, stable rendered
  message keys, generation-safe match checks, and shared transcript chunk
  budgets.
- Added per-client transcript render state to `ui/state.P`.
- Updated `ui/chat.py` so large transcripts render the latest bounded window
  first (`60` messages by default), expose a `Load earlier messages` control,
  and render remaining visible rows in adaptive time-budgeted chunks.
- Replaced the heavy detached-finalize clear/rebuild path in `app.py` with
  append-oriented transcript synchronization. Same-thread finalization now
  appends only missing tail messages when the rendered window matches state;
  the fallback remains scoped and bounded to the latest transcript window.
- Added live render-state marking for normal attached streaming finalization
  so the next send does not trigger a stale transcript reconcile just because
  the assistant placeholder became a persisted message.
- Added lazy preview rendering for very large markdown message bodies in
  `ui/render.py`; full content remains available through an explicit
  `Show full message` action.
- Debounced and generation-guarded token counter refreshes so expensive exact
  counts do not stack up during thread-open first paint.
- Extended `scripts/ui_performance_harness.py` with
  `--profile-transcript THREAD_ID|latest`, which profiles real local
  transcript loading and bounded-window selection from the Thoth data store.
- Updated the Windows installer manifest to include `ui/transcript.py`.

Tests and harness:

- `.venv\Scripts\python.exe -m compileall app.py ui\chat.py ui\render.py ui\streaming.py ui\state.py ui\transcript.py`
  - Passed.
- `.venv\Scripts\python.exe -m pytest tests\test_ui_performance.py tests\test_ui_performance_overhaul.py`
  - Passed: 18 tests.
- `.venv\Scripts\python.exe -m pytest tests\test_developer_studio_phase10.py::test_detached_finalize_uses_scoped_transcript_refresh_not_main_rebuild tests\test_developer_studio_phase10.py::test_active_detached_finalize_preserves_optimistic_user_messages`
  - Passed: 2 tests.
- With `TMP` and `TEMP` pointed at `.tmp`:
  `.venv\Scripts\python.exe -m pytest tests\test_knowledge_audit.py`
  - Passed: 5 tests.
- With `TMP` and `TEMP` pointed at `.tmp`:
  `.venv\Scripts\python.exe -m pytest tests\test_app_port.py tests\test_app_stability_hardening.py`
  - Passed: 24 tests.
- With `TMP` and `TEMP` pointed at `.tmp`:
  `.venv\Scripts\python.exe -m pytest tests\test_developer_studio_phase10.py tests\test_thinking_retention.py tests\test_transcript_loading.py`
  - Passed: 50 tests.
  - A previous run without repo-local temp failed due Windows temp directory
    permission errors, not code failures.
- With `TMP` and `TEMP` pointed at `.tmp`:
  `.venv\Scripts\python.exe tests\test_suite.py`
  - Passed.
- Real-world harness:
  `.venv\Scripts\python.exe scripts\ui_performance_harness.py --launch --port 8080 --timeout 70 --profile-transcript latest --output .tmp\ui_performance_harness.json`
  - Passed launch smoke.
  - Latest transcript profile:
    `pt_399c8586ac`, 170 UI rows, message load `23.0ms`, bounded initial
    window `110:170`, visible rows `60`, RSS delta about `5.1MB`.

Remaining manual verification:

- Reopen `pt_399c8586ac` in the UI and confirm first paint no longer attempts
  to render all 170 rows.
- Send a follow-up message in that large thread and confirm the final assistant
  answer appears without an empty transcript flash.
- While a generation is running, switch away and back to the same thread; when
  the detached generation finalizes, confirm only the missing tail is appended
  and optimistic user messages remain visible.
- Open an older section with `Load earlier messages` and confirm the expanded
  history remains ordered and responsive.

### 2026-05-27 Blank Thread Chat Shell Performance Plan

Manual QA after the large-transcript fix showed that creating a new blank
thread can still take several seconds, briefly show the disconnect banner, and
flash blank before eventually loading an empty chat. The last two blank threads
created during testing were:

- `02f8196f8fca` (`Thread May 27, 13:35`), created
  `2026-05-27T13:35:22.386805`.
- `b11d6125b1fa` (`Thread May 27, 13:35`), created
  `2026-05-27T13:35:33.419022`.

Both threads had `0` UI rows, but `chat.transcript.render` logged slow
durations:

- `02f8196f8fca`: about `1956ms`, then another about `1979ms`.
- `b11d6125b1fa`: about `11669ms`, event-loop lag about `11.3s`, then
  another about `1973ms`.

Current diagnosis:

- The existing `chat.transcript.render` timing starts at the top of
  `ui.chat.build_chat()`, so it measures the whole chat shell, not just
  transcript rows.
- `ui.chat.build_chat()` still performs several synchronous first-paint tasks
  before the composer is usable:
  - `skills.load_skills()` scans/parses bundled, tool-guide, and user skills
    and writes `skills_config.json`.
  - `skills.get_enabled_skills()` can walk active tool guides and tool
    registry state.
  - `_model_surface()` resolves provider config and evaluates runtime
    readiness synchronously.
  - `_build_inline_model_picker()` calls `list_model_choice_options()`, which
    validates quick choices and can refresh capability snapshots.
  - chat upload/clipboard JavaScript hooks and composer setup are included in
    the misleading transcript timer.
- New-thread creation in `ui.sidebar._new_thread()` appears cheap: metadata is
  saved off-loop and `state.messages` is empty. The slow path begins when the
  chat shell hydrates.
- The second blank render likely comes from the skeleton/deferred hydration
  pattern and/or a second rebuild around thread-list refresh. This needs
  explicit rebuild reason/generation instrumentation before changing behavior.

Constraints:

- Preserve the large-thread transcript windowing and detached-finalize append
  sync.
- Preserve per-thread skill override semantics.
- Preserve Agent Mode and Chat Only runtime semantics.
- Preserve provider/model runtime behavior; move UI display work out of first
  paint without changing readiness decisions at send time.
- Keep visual design consistent with the current chat shell.

Implementation-ready plan:

1. Add accurate chat-shell observability:
   - `chat.shell.render` for the full `build_chat()` call.
   - `chat.header.render`.
   - `chat.skills.snapshot` or `chat.skills.menu.load`.
   - `chat.model_surface.render`.
   - `chat.model_surface.resolve`.
   - `chat.model_picker.render`.
   - `chat.model_picker.options`.
   - `chat.transcript.render` starting only immediately before transcript row
     rendering.
   - `chat.composer.render`.
   - `_rebuild_main` reason, view type, generation id, immediate/deferred
     mode, skeleton time, and hydration time.
2. Update rebuild API:
   - allow callers to pass `reason=` and possibly `immediate=`.
   - call `rebuild_main(reason="new_thread", immediate=True)` for newly
     created blank threads once shell first paint is cheap.
   - preserve the existing deferred skeleton path for heavier navigations.
   - ensure stale hydrations still abort when a newer generation starts.
3. Remove synchronous skills loading from chat first paint:
   - add a cached skills snapshot helper such as
     `skills.get_enabled_manual_skills_snapshot()` that never scans or writes.
   - render the skills control from cache when available; otherwise render a
     lightweight `Skills` button.
   - load/refresh skills off the UI loop on menu open or deferred background
     refresh.
   - update the menu contents generation-safely after the async load returns.
4. Defer model-surface readiness:
   - render an immediate model banner from cheap saved/default model state.
   - update provider label, local/cloud styling, Agent Mode/Chat Only badge,
     and scroll tint after a generation-safe async readiness check.
   - keep send-time runtime readiness untouched.
5. Defer inline model picker options:
   - render the picker immediately with `Default`, the current override if
     any, and `More models`.
   - populate quick-choice options asynchronously via `run.io_bound`.
   - avoid `validate_quick_choices_for_surface()` on first paint unless the
     cached options are already warm.
   - keep model-switch validation and context-cap warnings unchanged after a
     user actually selects a model.
6. Make composer and upload hooks cheap:
   - install drag/drop and paste singleton JavaScript once per client, not on
     every chat rebuild.
   - update only the current upload id when the hidden upload element changes.
   - instrument composer setup separately.
7. Reduce duplicate blank-thread rebuilds:
   - inspect rebuild logs after instrumentation.
   - if thread-list refresh causes a second main build, separate sidebar list
     refresh from main hydration.
   - if stale deferred hydration causes it, add stricter generation checks or
     choose immediate build for `new_thread`.
8. Tests:
   - source/focused tests that `build_chat()` no longer calls
     `skills.load_skills()` synchronously.
   - tests for cached/deferred skills menu loading.
   - tests for deferred model picker options.
   - tests for model banner immediate placeholder plus async update hooks.
   - tests for `_rebuild_main(reason=...)` stale-generation behavior.
   - tests ensuring detached-finalize and transcript-windowing code remains
     intact.
9. Harness:
   - extend `scripts/ui_performance_harness.py` with a blank-thread scenario
     or profile mode that creates/selects an empty thread and captures:
     shell first-paint timing, rebuild count, event-loop lag markers,
     transcript rows, and memory delta.
   - run the harness against the real local data store after implementation.
10. Acceptance criteria:
    - Creating three blank threads in a row produces no disconnect banner and
      no visible blank flash after first paint.
    - Blank-thread `chat.shell.render` is below 500ms on a warm process.
    - Blank-thread `chat.transcript.render` is near-zero and reports `rows=0`
      honestly.
    - No synchronous skills scan/config write occurs during blank-thread first
      paint.
    - Model/skill controls remain available and update progressively.
    - Sending the first message in the new thread still streams and finalizes
      correctly.

### 2026-05-27 Blank Thread Chat Shell Implementation Progress

Completed chunk 1 of the blank-thread stability/performance plan:

- Added per-client chat shell generation state so deferred chat-shell work can
  detect stale renders.
- Added cheap skill snapshot helpers in `skills.py` and removed synchronous
  `skills.load_skills()` from chat first paint. Skill discovery now warms
  off the UI loop after initial render.
- Added rebuild reason/timing instrumentation for main-view skeleton,
  hydration, immediate rebuilds, and real-view builds.
- Changed new blank-thread creation to use
  `rebuild_main(immediate=True, reason="new_thread")`, avoiding the skeleton
  plus deferred hydration flash for a freshly empty chat.
- Split chat timing into `chat.header.render`, `chat.transcript.render`,
  `chat.composer.render`, and `chat.shell.render`; transcript timing now starts
  immediately before transcript row/window work instead of at the top of the
  full chat builder.
- Replaced synchronous model readiness banner resolution during first paint
  with a cheap placeholder plus stale-safe deferred readiness resolution.
- Replaced synchronous inline model option catalog loading during first paint
  with a minimal current/default picker plus deferred stale-safe option loading.
- Extended the optional UI performance harness with `--profile-blank-thread`,
  which checks the latest real local blank thread without creating more user
  data.
- Gated chat drag/drop and paste hook installation so subsequent chat rebuilds
  only update the current hidden upload element id instead of resending both
  full listener installers.

Focused verification:

- `.venv\Scripts\python.exe -m py_compile ui\chat.py ui\chat_components.py app.py skills.py ui\state.py scripts\ui_performance_harness.py`
- `.venv\Scripts\python.exe -m pytest tests/test_ui_performance_overhaul.py -q`
  passed: 14 tests.
- `.venv\Scripts\python.exe -m pytest tests/test_ui_performance.py tests/test_developer_studio_phase10.py tests/test_thinking_retention.py tests/test_transcript_loading.py -q`
  passed: 55 tests.
- `.venv\Scripts\python.exe scripts\ui_performance_harness.py --profile-transcript latest --profile-blank-thread --output .tmp\ui_performance_harness_blank_thread.json`
  passed. Latest local blank thread `b11d6125b1fa` loaded 0 rows in 0.05ms
  for the blank-thread profile; latest transcript profile loaded 0 rows in
  8.08ms.
- `.venv\Scripts\python.exe -m pytest tests/test_ui_performance_overhaul.py tests/test_ui_performance.py tests/test_developer_studio_phase10.py tests/test_thinking_retention.py tests/test_transcript_loading.py -q`
  passed: 69 tests.
- `.venv\Scripts\python.exe -m pytest tests/test_knowledge_audit.py tests/test_app_port.py tests/test_app_stability_hardening.py -q`
  passed: 29 tests.
- `.venv\Scripts\python.exe scripts\ui_performance_harness.py --launch --port 8080 --timeout 70 --profile-transcript latest --profile-blank-thread --output .tmp\ui_performance_harness_blank_thread_launch_fresh.json`
  passed after the app was shut down. Fresh process launch took 7069ms,
  `/api/launcher-ping` responded, `GET /` returned 200, and the process
  terminated. Latest blank-thread profile remained 0 rows in 0.03ms.
- `.venv\Scripts\python.exe tests\test_suite.py` passed when run as the
  script-style comprehensive suite. Running it through pytest collects 0 tests
  because it is not a pytest test module.
- `git diff --check` passed with only expected CRLF conversion warnings.

Follow-up fix after manual QA:

- Symptom: every assistant response appeared twice in the active chat.
- Root cause: optional post-render JavaScript for highlight.js/mermaid could
  raise a runtime error after the live assistant row had already rendered.
  The consumer treated that cosmetic JavaScript failure as a deleted-client
  signal and marked the generation detached. Detached finalization then ran the
  scoped transcript refresh and appended the persisted assistant message,
  leaving both the original live row and the recovered persisted row visible.
- Fix: post-render JavaScript failures are now logged as cosmetic and do not
  detach the generation after the final row has rendered.
- Verification:
  `.venv\Scripts\python.exe -m pytest tests/test_developer_studio_phase10.py tests/test_thinking_retention.py tests/test_transcript_loading.py -q`
  passed: 51 tests.

Mermaid export follow-up:

- Manual QA confirmed Mermaid diagrams render correctly after the duplicate
  response fix, but they lacked the save affordance available on generated and
  attached images.
- Added a Mermaid render wrapper with a download button that converts the
  rendered SVG to PNG in the browser and passes the PNG bytes through Thoth's
  existing export/save path.
- Verification:
  `.venv\Scripts\python.exe -m pytest tests/test_ui_performance_overhaul.py tests/test_thinking_retention.py tests/test_transcript_loading.py -q`
  passed: 26 tests.

Code-block first-paint follow-up:

- Manual QA showed fenced code blocks appeared as plain/preformatted text
  immediately after a response, then gained the proper code-block styling and
  syntax highlighting only after another message was sent or the thread was
  reloaded.
- Root cause: the explicit highlight.js call could run before NiceGUI's DOM
  patch containing the final rendered `<pre><code>` nodes had landed in the
  browser.
- Fix: added a page-level `thothHighlightCodeBlocks` helper and
  `MutationObserver` in `ui/head_html.py`. It debounces new code-block DOM
  insertions and highlights them after the browser has applied the patch.
  Streaming and stored-message render paths now call that helper when
  available, with a delayed fallback for older pages.
- Verification:
  `.venv\Scripts\python.exe -m pytest tests/test_ui_performance_overhaul.py tests/test_developer_studio_phase10.py tests/test_thinking_retention.py tests/test_transcript_loading.py -q`
  passed: 65 tests.

Live-code flashing follow-up:

- Manual QA showed the mutation-driven highlighter fixed delayed code-block
  rendering but was too eager during streaming: partial fenced-code DOM nodes
  were highlighted repeatedly as tokens arrived, causing visible flashing.
- Fix: live streaming assistant markdown is now marked with
  `thoth-live-stream`. The page-level code and Mermaid mutation observers skip
  nodes inside that live container, and the explicit fallback JS paths use the
  same guard. Once the response finalizes, the final replacement render is not
  marked live and gets highlighted/rendered once.
- Verification:
  `.venv\Scripts\python.exe -m pytest tests/test_ui_performance_overhaul.py tests/test_developer_studio_phase10.py tests/test_thinking_retention.py tests/test_transcript_loading.py -q`
  passed: 65 tests.

Mermaid size/export follow-up:

- Manual QA showed Mermaid diagrams rendered and exported too small because
  PNG export used the visible on-screen SVG box.
- Updated Mermaid display CSS to use the available message width better while
  preserving horizontal scroll for wide diagrams.
- Updated PNG export to use intrinsic SVG/viewBox dimensions, export at a
  higher independent scale with padding, and cap either side at 4096px.
- Verification:
  `.venv\Scripts\python.exe -m pytest tests/test_ui_performance_overhaul.py tests/test_thinking_retention.py tests/test_transcript_loading.py -q`
  passed: 26 tests.

Mermaid crop/export follow-up:

- Manual QA showed the larger Mermaid display could crop diagrams in narrow
  chat columns, and PNG export could time out after a browser `SecurityError`
  from `canvas.toDataURL()` on a tainted canvas.
- Fix: Mermaid rendering now disables HTML labels, normalizes each rendered
  SVG to a padded real bounding-box `viewBox`, and no longer centers a wide
  SVG inside a narrower scroll area.
- Fix: PNG export now sanitizes the cloned SVG before rasterization, removes
  `foreignObject` and external references, inlines key computed SVG styles,
  uses a data-URI image source, catches `toDataURL()` security failures inside
  the image callback, and uses a bounded JavaScript timeout.
- Verification:
  `.venv\Scripts\python.exe -m pytest tests/test_ui_performance_overhaul.py tests/test_thinking_retention.py tests/test_transcript_loading.py -q --basetemp .tmp\pytest-mermaid-export`
  passed: 26 tests.
  `.venv\Scripts\python.exe -m py_compile ui\head_html.py ui\render.py tests\test_ui_performance_overhaul.py`
  passed.
  `git diff --check -- ui\head_html.py ui\render.py tests\test_ui_performance_overhaul.py`
  passed with expected CRLF warnings only.

Mermaid PNG label/style follow-up:

- Manual QA showed exported PNGs could still miss most node text and render
  some Mermaid boxes as light/white shapes. This happened because the safer
  export path removed `foreignObject` HTML labels entirely, and serialized SVG
  style inheritance was not stable across the canvas rasterization step.
- Fix: Mermaid PNG export now converts `foreignObject` labels into plain SVG
  `<text>/<tspan>` labels before rasterizing. The export clone also receives a
  dark, export-specific SVG stylesheet so nodes, edges, markers, and labels are
  legible in the PNG regardless of Mermaid's internal class styling.
- Verification:
  `.venv\Scripts\python.exe -m pytest tests/test_ui_performance_overhaul.py tests/test_thinking_retention.py tests/test_transcript_loading.py -q --basetemp .tmp\pytest-mermaid-export-labels`
  passed: 26 tests.
  `.venv\Scripts\python.exe -m py_compile ui\render.py tests\test_ui_performance_overhaul.py`
  passed.
  `git diff --check -- ui\render.py tests\test_ui_performance_overhaul.py docs\memory-recall-uplift-implementation-plan.md`
  passed with expected CRLF warnings only.

OpenRouter Agent-readiness follow-up plan:

- Manual QA showed multiple OpenRouter models, including
  `qwen/qwen3.7-max` and Opus routed through OpenRouter, display and run as
  Chat Only even though their OpenRouter catalog entries support tools.
- Root cause found so far: OpenRouter intentionally fails closed for Agent
  Mode unless `tool_calling=True` comes from explicit metadata. The persisted
  cloud catalog has that metadata, but runtime readiness currently falls back
  to `model_info_from_metadata(provider_id, model_id, {})` when no snapshot is
  passed, which makes OpenRouter tool support inconclusive. Quick-choice
  snapshot refresh can also overwrite richer OpenRouter catalog metadata with
  model-id-only inferred metadata.
- Plan:
  1. Make runtime/readiness snapshot resolution consult persisted provider
     catalog/cache metadata before model-id-only inference.
  2. Preserve fail-closed behavior when OpenRouter metadata is genuinely
     missing or explicitly says tools are unsupported.
  3. Prevent quick-choice refresh from clobbering richer OpenRouter cached
     snapshots with inconclusive inferred snapshots.
  4. Add focused regression tests for OpenRouter cached tools, missing tools,
     and quick-choice snapshot preservation.
  5. Review all supported providers against their current API contracts and
     identify similar capability/readiness drift risks.
  6. Cross-check the final implementation against this memory section and run
     focused provider/readiness tests plus required smoke suites.

OpenRouter Agent-readiness follow-up implementation:

- Completed: runtime/readiness snapshot resolution now consults the persisted
  provider catalog/cache before falling back to model-id-only inference. This
  lets OpenRouter see explicit catalog metadata such as
  `tool_calling=True` for `qwen/qwen3.7-max`, while still failing closed when
  metadata is absent or says tools are unsupported.
- Completed: quick-choice capability refresh now preserves richer cached
  provider snapshots before inference, so pinned OpenRouter models are not
  demoted to inconclusive tool metadata.
- Completed: runtime chat compatibility checks now use cached provider
  snapshots too, preventing non-chat catalog entries from being accepted just
  because bare model-id inference would otherwise look chat-capable.
- Provider contract review:
  - OpenRouter: current Models API exposes `supported_parameters`; `tools` and
    `tool_choice` are the correct explicit tool-support signal. The existing
    fail-closed policy remains appropriate.
  - OpenAI: `/v1/models` exposes basic model identity/ownership, not complete
    tool capability metadata, so trusted-provider family classification remains
    the current practical contract.
  - Anthropic: list models is basic, but Claude Messages tool use is a core API
    contract for current Claude models; trusted-provider handling remains
    appropriate.
  - Google Gemini: `models.list` exposes supported generation methods and token
    limits; function-calling support is documented by model family. No
    OpenRouter-style metadata loss was found. Keep watching model-specific
    function-calling exceptions in future catalog updates.
  - xAI: `/v1/language-models` is still the richer endpoint for modalities;
    function calling is documented for Grok via the tools contract. No cache
    loss issue found.
  - MiniMax: supported M2-family models and the Anthropic-compatible endpoint
    explicitly support `tools`/`tool_choice`; static catalog remains aligned
    with current docs.
  - Ollama/Ollama Cloud: local Ollama remains allowlist/probe guarded;
    Ollama's API exposes model details/capabilities and supports tool calling.
    No change made to the conservative local probe path.
- Verification:
  - `.venv\Scripts\python.exe -m pytest tests/test_agent_readiness.py tests/test_provider_catalog.py tests/test_provider_selection.py tests/test_provider_runtime.py -q --basetemp .tmp\pytest-provider-readiness`
    passed: 113 tests.
  - `.venv\Scripts\python.exe -m pytest tests/test_provider_runtime.py tests/test_provider_custom.py tests/test_provider_media.py tests/test_provider_subscription_auth.py tests/test_model_picker_regressions.py tests/test_openai_compatible_transport.py tests/test_thoth_status_media.py -q --basetemp .tmp\pytest-provider-broad`
    passed: 123 tests.
  - `.venv\Scripts\python.exe -m py_compile providers\readiness.py providers\selection.py providers\runtime.py models.py ui\chat.py ui\settings.py`
    passed.
  - `.venv\Scripts\python.exe -m pytest tests/test_knowledge_audit.py -q --basetemp .tmp\pytest-knowledge-audit`
    passed: 5 tests.
  - `.venv\Scripts\python.exe tests\test_suite.py` passed. The live launch
    section skipped because port 8080 was already in use.
  - `git diff --check` passed with only expected CRLF conversion warnings.
- Manual expectation after restart: OpenRouter models with cached catalog
  `tool_calling=True`, including `qwen/qwen3.7-max` and routed Claude/Gemini
  models, should show Agent Mode and run with tools. OpenRouter entries whose
  metadata is missing or explicitly lacks tools should remain Chat Only.

Remaining chunks:

- Cross-check logs from a real manual new-thread attempt for
  `chat.shell.render`, `chat.transcript.render rows=0`, and absence of long
  event-loop lag.

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
