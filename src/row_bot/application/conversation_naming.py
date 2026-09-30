"""Conversations name themselves (B230).

The first message admitted to a conversation that is still named
automatically names it at once from its first words, so the sidebar never
shows "New conversation". Once the first reply has finished and the
conversation is idle, one background call asks the conversation's own model
for a short title and replaces the first-words name, unless the person renamed
the conversation meanwhile. The call is capped (a one-line instruction, the
trimmed first message and the start of the reply; no tools, memory, skills or
history; about 24 output tokens; reasoning as low as the model allows), gets
one attempt and 15 seconds, and a failure keeps the first-words name silently.
Names the person gave, workflow runs and delegated agents' threads are never
changed, and nothing here logs message text.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from contextlib import closing
from typing import Any

_LOG = logging.getLogger(__name__)

_TITLE_LIMIT = 60
TITLE_TIMEOUT_SECONDS = 15.0
_MAX_TOKENS = 24
_TEMPERATURE = 0.2
_MESSAGE_LIMIT = 1500
_REPLY_LIMIT = 500
_INSTRUCTION = (
    "Write a title of 3 to 6 words for this conversation, in the language of the user's message. "
    "Answer with the title only, on one line, without quotes or final punctuation."
)
_LABEL = re.compile(r"^title\s*:\s*", re.IGNORECASE)
_WRAPPING = "\"'`“”‘’«»*_# "
_TRAILING = ".!?,;:。！？…"

# The first message of each conversation still waiting for its first finished reply.
_AWAITING_REPLY: dict[str, str] = {}


def _at_word(text: str) -> str:
    """Whitespace collapsed; longer text cut after the last whole word within the limit."""
    text = " ".join(str(text or "").split())
    if len(text) > _TITLE_LIMIT:
        head = text[:_TITLE_LIMIT + 1]
        text = head.rsplit(" ", 1)[0] if " " in head else text[:_TITLE_LIMIT]
    return text


def _first_words(text: str) -> str:
    """The instant name: the message's first words, at most 60 characters."""
    return _at_word(text).rstrip(" ,;:-")


def _clean_title(raw: str) -> str:
    """One line without quotes, a "Title:" label or final punctuation, at most 60 characters."""
    line = next((part for part in str(raw or "").splitlines() if part.strip()), "")
    line = _LABEL.sub("", line.strip(_WRAPPING)).strip(_WRAPPING)
    return _at_word(line).rstrip(_TRAILING + _WRAPPING)


def _automatic(conversation_id: str) -> bool:
    """Named automatically, and neither a workflow run nor a delegated agent's thread."""
    from row_bot import agent_runs, threads

    with closing(sqlite3.connect(threads.DB_PATH)) as conn:
        row = conn.execute("SELECT COALESCE(name_source,'') FROM thread_meta WHERE thread_id=?",
                           (conversation_id,)).fetchone()
    return (row is not None and row[0] != threads.THREAD_NAME_SOURCE_MANUAL
            and conversation_id not in threads.get_workflow_thread_ids()
            and agent_runs.get_agent_run_for_thread(conversation_id) is None)


def _write(conversation_id: str, name: str, *, replacing: str | None = None) -> bool:
    """Write an automatic name unless the person has named the conversation; with
    ``replacing``, only while the name is still the one read before the title call."""
    from row_bot import threads

    query = "UPDATE thread_meta SET name=? WHERE thread_id=? AND COALESCE(name_source,'')<>?"
    params = [name, conversation_id, threads.THREAD_NAME_SOURCE_MANUAL]
    if replacing is not None:
        query += " AND name=?"
        params.append(replacing)
    with closing(sqlite3.connect(threads.DB_PATH)) as conn, conn:
        return conn.execute(query, params).rowcount == 1


def name_first_message(conversation_id: str, text: str) -> None:
    """Name a conversation from its first admitted message while it is named automatically."""
    try:
        if not _automatic(conversation_id):
            return
        _AWAITING_REPLY[conversation_id] = text
        name = _first_words(text)
        if name:
            _write(conversation_id, name)
    except Exception:
        _LOG.warning("Conversation %s could not be named from its first message", conversation_id, exc_info=True)


def after_turn(service: Any, conversation_id: str, *, status: str, reply: str, model_ref: str) -> None:
    """Ask for the smart name once the first reply has finished and nothing else runs."""
    try:
        if status != "completed" or service.registry.active(conversation_id):
            return
        message = _AWAITING_REPLY.pop(conversation_id, None)
        if message is None:
            return
        threading.Thread(target=_smart_name, args=(service, conversation_id, message, reply, model_ref),
                         daemon=True, name="conversation-naming").start()
    except Exception:
        _LOG.warning("The smart name could not start for %s", conversation_id, exc_info=True)


def _smart_name(service: Any, conversation_id: str, message: str, reply: str, model_ref: str) -> None:
    from row_bot import threads

    try:
        with closing(sqlite3.connect(threads.DB_PATH)) as conn:
            row = conn.execute("SELECT name, COALESCE(name_source,'') FROM thread_meta WHERE thread_id=?",
                               (conversation_id,)).fetchone()
        if row is None or row[1] == threads.THREAD_NAME_SOURCE_MANUAL:
            return
        title = _clean_title(_ask(model_ref, message, reply))
        if not title:
            _LOG.info("Conversation %s keeps its first-words name: the title was empty", conversation_id)
            return
        if title != row[0] and _write(conversation_id, title, replacing=row[0]):
            _LOG.info("Conversation %s was named after its first reply", conversation_id)
            service.conversation_changed(conversation_id)
    except Exception as exc:
        _LOG.info("Conversation %s keeps its first-words name: the title call failed (%s)",
                  conversation_id, type(exc).__name__)


def _ask(model_ref: str, message: str, reply: str) -> str:
    """One capped call to the conversation's own model, abandoned after the timeout."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from row_bot.cancellation import CancellationScope, use_cancellation_scope

    prompt = [SystemMessage(content=_INSTRUCTION), HumanMessage(content=(
        f"User's message:\n{message[:_MESSAGE_LIMIT]}\n\nStart of the reply:\n{reply[:_REPLY_LIMIT]}"))]
    scope = CancellationScope()
    outcome: list[Any] = []

    def call() -> None:
        with use_cancellation_scope(scope):
            try:
                outcome.append(_title_model(model_ref).invoke(prompt))
            except Exception as exc:
                outcome.append(exc)

    worker = threading.Thread(target=call, daemon=True, name="conversation-title")
    worker.start()
    worker.join(TITLE_TIMEOUT_SECONDS)
    if not outcome:
        scope.cancel("title_timeout")
        raise TimeoutError("The title call timed out.")
    if isinstance(outcome[0], Exception):
        raise outcome[0]
    return str(outcome[0].text)


def _title_model(model_ref: str) -> Any:
    """The conversation's own model with the title caps."""
    from row_bot.models import get_llm_for
    from row_bot.providers.reasoning import (
        ReasoningRequestPlan,
        ReasoningSelection,
        canonical_reasoning_model_ref,
        resolve_reasoning_capabilities_for_ref,
    )
    from row_bot.providers.resolution import resolve_provider_config

    resolved = resolve_provider_config(model_ref, allow_legacy_local=True)
    canonical = canonical_reasoning_model_ref(resolved.provider_id, resolved.runtime_model)
    capabilities = resolve_reasoning_capabilities_for_ref(canonical)
    # A reasoning model would otherwise spend hundreds of tokens thinking about a title.
    if capabilities is not None and capabilities.can_disable and not capabilities.mandatory:
        selection = ReasoningSelection(kind="off")
    elif capabilities is not None and capabilities.supported_efforts:
        selection = ReasoningSelection(kind="effort", effort=capabilities.supported_efforts[0])
    else:
        selection = ReasoningSelection()
    llm = get_llm_for(resolved.selection_ref, reasoning_plan=ReasoningRequestPlan(canonical, selection, capabilities))
    # A cool temperature, but only where no reasoning runs: reasoning requests refuse one.
    sampling = {"temperature": _TEMPERATURE} if capabilities is None or selection.kind == "off" else {}
    if resolved.provider_id == "google":
        return llm.bind(max_output_tokens=_MAX_TOKENS, **sampling)
    if resolved.provider_id == "ollama":
        # Ollama replaces all its options with these, so keep the model's context allocation.
        options = {"num_predict": _MAX_TOKENS, **sampling}
        num_ctx = int(getattr(llm, "num_ctx", 0) or 0)
        if num_ctx > 0:
            options["num_ctx"] = num_ctx
        return llm.bind(options=options)
    return llm.bind(max_tokens=_MAX_TOKENS, **sampling)
