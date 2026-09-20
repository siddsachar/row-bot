from __future__ import annotations

from dataclasses import replace

import pytest

from row_bot.application.client_platform import ClientPlatformError
from row_bot.application.folder_selections import FolderSelectionScope, FolderSelections

pytestmark = pytest.mark.subsystem


def _scope() -> FolderSelectionScope:
    return FolderSelectionScope(
        session_id="session-1",
        instance_id="instance-1",
        window_id="window-1",
        window_epoch=7,
        intent="open-existing",
        conversation_id="conversation-1",
        destination="workspace",
        authority_grant="native-grant-1",
        policy_revision="policy-1",
    )


def test_exact_folder_grant_is_opaque_bound_and_one_shot(tmp_path) -> None:
    selected = tmp_path / "Private selected folder"
    selected.mkdir()
    source = selected / "retained.txt"
    source.write_bytes(b"retained fixture bytes")
    selections = FolderSelections(clock=lambda: 10.0)
    scope = _scope()
    intent_id = selections.begin_exact(scope)
    validations = []

    result = selections.complete_exact(
        intent_id, scope, selected, lambda: validations.append("valid"),
    )

    assert result["status"] == "selected"
    assert result["name"] == selected.name
    assert str(selected) not in str(result)
    assert validations == ["valid", "valid"]
    authorized = selections.consume_exact(
        result["grant_id"], scope, lambda: validations.append("consume"),
    )
    assert authorized.path == selected
    assert validations[-2:] == ["consume", "consume"]
    with pytest.raises(ClientPlatformError, match="capability_revoked"):
        selections.consume_exact(result["grant_id"], scope, lambda: None)
    assert source.read_bytes() == b"retained fixture bytes"
    assert not (selected / ".git").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", "session-2"),
        ("instance_id", "instance-2"),
        ("window_id", "window-2"),
        ("window_epoch", 8),
        ("intent", "create-empty"),
        ("conversation_id", "conversation-2"),
        ("destination", "clone-parent"),
        ("authority_grant", "native-grant-2"),
        ("policy_revision", "policy-2"),
    ],
)
def test_exact_grant_rejects_every_scope_mismatch_without_consuming_valid_grant(
    tmp_path, field: str, value: object,
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    selections = FolderSelections(clock=lambda: 10.0)
    scope = _scope()
    intent_id = selections.begin_exact(scope)
    grant_id = selections.complete_exact(
        intent_id, scope, selected, lambda: None,
    )["grant_id"]

    with pytest.raises(ClientPlatformError, match="capability_revoked"):
        selections.consume_exact(grant_id, replace(scope, **{field: value}), lambda: None)
    assert selections.consume_exact(grant_id, scope, lambda: None).path == selected


def test_cancel_and_window_revocation_mint_no_grant(tmp_path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    selections = FolderSelections(clock=lambda: 10.0)
    scope = _scope()

    cancelled = selections.begin_exact(scope)
    assert selections.cancel_exact(cancelled, scope)
    with pytest.raises(ClientPlatformError, match="capability_revoked"):
        selections.complete_exact(cancelled, scope, selected, lambda: None)

    revoked = selections.begin_exact(scope)
    selections.revoke_window(
        instance_id=scope.instance_id,
        session_id=scope.session_id,
        window_id=scope.window_id,
    )
    with pytest.raises(ClientPlatformError, match="capability_revoked"):
        selections.complete_exact(revoked, scope, selected, lambda: None)


def test_late_policy_revocation_consumes_intent_and_mints_no_grant(tmp_path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    selections = FolderSelections(clock=lambda: 10.0)
    scope = _scope()
    intent_id = selections.begin_exact(scope)
    calls = 0

    def validate() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ClientPlatformError("capability_revoked")

    with pytest.raises(ClientPlatformError, match="capability_revoked"):
        selections.complete_exact(intent_id, scope, selected, validate)
    with pytest.raises(ClientPlatformError, match="capability_revoked"):
        selections.complete_exact(intent_id, scope, selected, lambda: None)


def test_expired_intent_and_grant_fail_closed(tmp_path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    now = [0.0]
    selections = FolderSelections(clock=lambda: now[0])
    scope = _scope()
    intent_id = selections.begin_exact(scope)
    now[0] = 121.0
    with pytest.raises(ClientPlatformError, match="capability_revoked"):
        selections.complete_exact(intent_id, scope, selected, lambda: None)

    now[0] = 200.0
    intent_id = selections.begin_exact(scope)
    grant_id = selections.complete_exact(
        intent_id, scope, selected, lambda: None,
    )["grant_id"]
    now[0] = 501.0
    with pytest.raises(ClientPlatformError, match="capability_revoked"):
        selections.consume_exact(grant_id, scope, lambda: None)


def test_invalid_scope_never_opens_or_mutates_any_resource() -> None:
    selections = FolderSelections()
    with pytest.raises(ClientPlatformError, match="invalid_request"):
        selections.begin_exact(replace(_scope(), authority_grant=""))
