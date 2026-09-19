"""The retained UI reports partial removal and retries the canonical receipt."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from tests.integration.wiki_vault.test_import_review import _Element

pytestmark = pytest.mark.subsystem


@pytest.fixture
def view(monkeypatch):
    from row_bot.ui import document_removal as owner

    elements, pending, calls, outcomes, saved = [], [], [], [], []

    class Element(_Element):
        def clear(self):
            pass

    class UI:
        def __getattr__(self, name):
            def create(*args, **kwargs):
                element = Element(name, *args, **kwargs)
                elements.append(element)
                return element

            return create

    def operation(*args, **kwargs):
        calls.append((args, kwargs))
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def io_bound(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setitem(
        sys.modules,
        "row_bot.documents",
        SimpleNamespace(
            remove_document_details=operation, clear_documents_details=operation
        ),
    )
    monkeypatch.setattr(owner, "ui", UI())
    monkeypatch.setattr(owner, "run", SimpleNamespace(io_bound=io_bound))
    monkeypatch.setattr(
        owner, "safe_ui_task", lambda name, callback: pending.append(callback)
    )

    def button(text):
        return next(e for e in elements if e.kind == "button" and e.text == text)

    return SimpleNamespace(
        owner=owner,
        elements=elements,
        pending=pending,
        calls=calls,
        outcomes=outcomes,
        saved=saved,
        button=button,
        open=lambda identity="doc-one": owner.open_document_removal(
            identity, "Synthetic document", lambda: saved.append(True)
        ),
    )


def result(status="complete"):
    return {
        "removal_id": "saved-removal",
        "status": status,
        "derived_entities_removed": 2,
        "stages": {"index": "complete"},
        "retained_copies": [{"kind": "source", "path": "PRIVATE_PATH"}],
        "failures": [] if status == "complete" else [{"message": "PRIVATE_FAILURE"}],
    }


@pytest.mark.parametrize("identity", [None, "doc-one"])
@pytest.mark.parametrize("status", ["pending", "partial"])
def test_partial_retries_same_receipt_and_refreshes_only_after_complete(
    view, identity, status
):
    view.outcomes.extend([result(status), result()])
    view.open(identity)
    asyncio.run(view.pending[0]())
    assert view.saved == []
    assert view.button("Retry removal").enabled
    assert not any("Removed Synthetic" in e.text for e in view.elements)
    asyncio.run(view.button("Retry removal").callback())
    assert view.calls == [
        ((() if identity is None else (identity,)), {"removal_id": None}),
        ((() if identity is None else (identity,)), {"removal_id": "saved-removal"}),
    ]
    assert not view.button("Retry removal").enabled
    asyncio.run(view.button("Retry removal").callback())
    assert len(view.calls) == 2
    assert view.saved == []
    view.button("Close").callback()
    view.button("Close").callback()
    assert view.saved == [True]
    assert any("retained" in e.text for e in view.elements)
    assert all("PRIVATE" not in str(e.text) for e in view.elements)


def test_unconfirmed_exception_is_redacted_and_resumes_default_saved_operation(view):
    view.outcomes.extend([OSError("PRIVATE_PATH"), result()])
    view.open()
    asyncio.run(view.pending[0]())
    assert any("could not be confirmed" in e.text for e in view.elements)
    assert view.saved == []
    assert all("PRIVATE_PATH" not in str(e.text) for e in view.elements)
    asyncio.run(view.button("Retry removal").callback())
    assert view.calls[1] == (("doc-one",), {"removal_id": None})


def test_busy_operation_rejects_duplicate_click_and_close(view, monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(fn, *args, **kwargs):
            entered.set()
            await release.wait()
            return fn(*args, **kwargs)

        monkeypatch.setattr(view.owner.run, "io_bound", slow)
        view.outcomes.append(result())
        view.open()
        task = asyncio.create_task(view.pending[0]())
        await entered.wait()
        assert not view.button("Close").enabled
        await view.button("Retry removal").callback()
        view.button("Close").callback()
        assert next(e for e in view.elements if e.kind == "dialog").opened
        release.set()
        await task
        assert len(view.calls) == 1

    asyncio.run(scenario())


def test_closed_partial_dialog_does_not_retry_in_background(view):
    view.outcomes.append(result("partial"))
    view.open()
    asyncio.run(view.pending[0]())
    view.button("Close").callback()
    asyncio.run(view.button("Retry removal").callback())
    assert len(view.calls) == 1 and view.saved == []
