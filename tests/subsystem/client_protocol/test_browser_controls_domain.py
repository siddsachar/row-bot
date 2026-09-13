from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import uuid

import pytest

from row_bot.application import client_browser_controls as controls
from row_bot.runtime import admissions


CONVERSATION = "conversation-browser-1"
OWNER = "owner-browser-1"
AUTHORITY = "authority-browser-1"


@pytest.fixture(autouse=True)
def no_real_admission_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every review provider-free and outside the configured user DB."""

    def digest(value: dict, *, read_only: bool = False) -> str:
        del read_only
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    monkeypatch.setattr(admissions, "keyed_digest", digest)


class FakeBackend:
    def __init__(self, *, active: bool = False, url: str = "") -> None:
        self.value = {
            "engine": "browser",
            "surface": "browser",
            "active": active,
            "paused": False,
            "thread_id": CONVERSATION,
            "state": "observing" if active else "idle",
            "target": "Private page title",
            "site": "untrusted site label",
            "url": url,
            "last_action": "Opened website" if active else "",
            "has_thumbnail": True,
            "revision": 7 if active else 0,
        }
        self.effects: list[tuple[str, dict, str]] = []

    def status(self, conversation_id: str) -> dict:
        assert conversation_id == CONVERSATION
        return deepcopy(self.value)

    def execute(self, action: str, payload: dict, conversation_id: str) -> None:
        self.effects.append((action, deepcopy(payload), conversation_id))
        self.value["revision"] += 1
        if action == "browser.navigate":
            self.value.update(
                active=True,
                state="observing",
                url=payload["url"],
                last_action="Opened website",
            )
        elif action == "browser.take_over":
            self.value.update(paused=True, state="waiting_user")
        elif action == "browser.check":
            self.value.update(last_action="Checked current page")
        elif action == "browser.back":
            self.value.update(
                url="https://example.test/previous", last_action="Went back"
            )
        elif action == "browser.end":
            self.value.update(
                active=False,
                paused=False,
                state="idle",
                url="",
                last_action="",
            )


class FakeAdmissions:
    def __init__(self) -> None:
        self.by_command: dict[tuple[str, str], dict] = {}
        self.by_key: dict[tuple[str, str], str] = {}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(admissions, "read_command_metadata", self.metadata)
        monkeypatch.setattr(admissions, "read_command_receipt", self.receipt)
        monkeypatch.setattr(admissions, "claim_command", self.claim)
        monkeypatch.setattr(admissions, "complete_command", self.complete)
        monkeypatch.setattr(admissions, "keyed_digest", self.digest)

    @staticmethod
    def digest(value: dict, *, read_only: bool = False) -> str:
        del read_only
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def metadata(self, owner_id: str, command_id: str) -> dict | None:
        row = self.by_command.get((owner_id, command_id))
        if row is None:
            return None
        return {
            "key": row["key"],
            "target": row["target"],
            "type": row["command"]["type"],
            "status": row["status"],
        }

    def receipt(self, owner_id: str, command_id: str) -> dict | None:
        row = self.by_command.get((owner_id, command_id))
        if row is None:
            return None
        return {
            "command_id": command_id,
            "status": row["status"],
            **deepcopy(row["saved"]),
        }

    def claim(
        self,
        owner_id: str,
        key: str,
        command: dict,
        target: str,
        *,
        exclusive_target: bool = False,
        initial_result: dict | None = None,
    ) -> dict | None:
        del exclusive_target
        command_key = (owner_id, command["command_id"])
        existing = self.by_command.get(command_key)
        if existing is not None:
            if existing["key"] != key or existing["command"] != command:
                raise admissions.AdmissionError("idempotency_mismatch")
            if existing["status"] == "completed":
                return deepcopy(existing["saved"])
            raise admissions.AdmissionError("operation_uncertain")
        prior_id = self.by_key.get((owner_id, key))
        if prior_id is not None:
            raise admissions.AdmissionError("idempotency_mismatch")
        self.by_key[(owner_id, key)] = command["command_id"]
        self.by_command[command_key] = {
            "key": key,
            "target": target,
            "command": deepcopy(command),
            "status": "admitting",
            "saved": deepcopy(initial_result or {}),
        }
        return None

    def complete(self, owner_id: str, key: str, result: dict) -> dict:
        command_id = self.by_key[(owner_id, key)]
        row = self.by_command[(owner_id, command_id)]
        row["status"] = "completed"
        row["saved"] = deepcopy(result)
        return result


def _reviewed_command(
    backend: FakeBackend,
    action: str,
    payload: dict,
) -> tuple[dict, dict]:
    review = controls.review_browser_command(
        action, payload, CONVERSATION, validate=lambda: None, backend=backend
    )
    command = {
        "command_id": str(uuid.uuid4()),
        "type": action,
        "payload": {**payload, "nonce": "signed-review"},
    }
    return review, command


def test_passive_status_is_sanitized_and_does_not_expose_page_content() -> None:
    backend = FakeBackend(
        active=True,
        url="https://user:password@example.test/private/path?token=secret#account",
    )

    result = controls.read_browser_controls(
        CONVERSATION, validate=lambda: None, backend=backend
    )

    assert result["url"] == "https://example.test/private/path"
    assert result["site"] == "example.test"
    assert "target" not in result
    assert "has_thumbnail" not in result
    assert "secret" not in json.dumps(result)
    assert result["availability"]["browser.navigate"] == {
        "state": "available",
        "code": None,
    }
    assert result["availability"]["browser.screenshot"] == {
        "state": "unavailable",
        "code": "private_preview_export_unavailable",
    }


def test_idle_read_keeps_navigation_check_on_use_and_other_controls_unavailable() -> (
    None
):
    result = controls.read_browser_controls(
        CONVERSATION, validate=lambda: None, backend=FakeBackend()
    )

    assert result["state"] == "idle"
    assert result["availability"]["browser.navigate"]["state"] == "check_on_use"
    for action in ("browser.take_over", "browser.check", "browser.back", "browser.end"):
        assert result["availability"][action] == {
            "state": "unavailable",
            "code": "browser_session_inactive",
        }


def test_navigation_review_uses_canonical_policy_and_redacts_query_values() -> None:
    backend = FakeBackend(active=True, url="https://origin.test/start")
    status = controls.read_browser_controls(
        CONVERSATION, validate=lambda: None, backend=backend
    )
    review = controls.review_browser_command(
        "browser.navigate",
        {
            "revision": status["revision"],
            "url": "https://destination.test/path?token=private-value",
        },
        CONVERSATION,
        validate=lambda: None,
        backend=backend,
    )

    assert review["policy_action"] == "browser_navigate"
    assert review["policy_decision"] == "ask"
    assert review["approval_required"] is True
    assert review["origin_and_path"] == "https://destination.test/path"
    assert review["query_present"] is True
    assert "private-value" not in json.dumps(review)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///private/file",
        "https://user:password@example.test/",
        "example.test/no-implicit-scheme",
    ],
)
def test_navigation_review_blocks_ambiguous_or_unsafe_urls(url: str) -> None:
    backend = FakeBackend()
    status = controls.read_browser_controls(
        CONVERSATION, validate=lambda: None, backend=backend
    )

    with pytest.raises(controls.ClientBrowserControlError) as caught:
        controls.review_browser_command(
            "browser.navigate",
            {"revision": status["revision"], "url": url},
            CONVERSATION,
            validate=lambda: None,
            backend=backend,
        )

    assert caught.value.code in {"invalid_browser_url", "browser_navigation_denied"}
    assert backend.effects == []


def test_unsupported_target_and_preview_actions_are_explicitly_unavailable() -> None:
    backend = FakeBackend(active=True)
    snapshot = controls.read_browser_controls(
        CONVERSATION, validate=lambda: None, backend=backend
    )

    with pytest.raises(controls.ClientBrowserControlError) as caught:
        controls.review_browser_command(
            "browser.click",
            {"revision": snapshot["revision"]},
            CONVERSATION,
            validate=lambda: None,
            backend=backend,
        )

    assert caught.value.code == "exact_page_target_contract_required"
    assert backend.effects == []


def test_execute_revalidates_policy_review_and_runs_exact_command_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_admissions = FakeAdmissions()
    fake_admissions.install(monkeypatch)
    backend = FakeBackend()
    snapshot = controls.read_browser_controls(
        CONVERSATION, validate=lambda: None, backend=backend
    )
    payload = {
        "revision": snapshot["revision"],
        "url": "https://example.test/path?ordinary=value",
    }
    review, command = _reviewed_command(backend, "browser.navigate", payload)
    policy_calls: list[str] = []
    review_calls: list[tuple[dict, dict]] = []

    def validate_review(command_value: dict, review_value: dict) -> None:
        review_calls.append((deepcopy(command_value), deepcopy(review_value)))
        assert review_value["action_digest"] == review["action_digest"]

    first = controls.execute_browser_command(
        command,
        CONVERSATION,
        owner_id=OWNER,
        authority_id=AUTHORITY,
        key="browser-command-key-1",
        validate=lambda: None,
        validate_action=policy_calls.append,
        validate_review=validate_review,
        backend=backend,
    )
    second = controls.execute_browser_command(
        command,
        CONVERSATION,
        owner_id=OWNER,
        authority_id=AUTHORITY,
        key="browser-command-key-1",
        validate=lambda: None,
        validate_action=policy_calls.append,
        validate_review=validate_review,
        backend=backend,
    )

    assert first == second
    assert first["status"] == "completed"
    assert first["browser_control"]["url"] == "https://example.test/path"
    assert "ordinary=value" not in json.dumps(first)
    assert backend.effects == [
        (
            "browser.navigate",
            {"revision": payload["revision"], "url": payload["url"]},
            CONVERSATION,
        )
    ]
    assert policy_calls == ["browser_navigate", "browser_navigate", "browser_navigate"]
    assert len(review_calls) == 3


def test_stale_review_is_rejected_before_admission_or_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_admissions = FakeAdmissions()
    fake_admissions.install(monkeypatch)
    backend = FakeBackend(active=True, url="https://example.test/one")
    snapshot = controls.read_browser_controls(
        CONVERSATION, validate=lambda: None, backend=backend
    )
    _review, command = _reviewed_command(
        backend, "browser.back", {"revision": snapshot["revision"]}
    )
    backend.value["revision"] += 1

    with pytest.raises(controls.ClientBrowserControlError) as caught:
        controls.execute_browser_command(
            command,
            CONVERSATION,
            owner_id=OWNER,
            authority_id=AUTHORITY,
            key="browser-command-key-stale",
            validate=lambda: None,
            validate_action=lambda _action: None,
            validate_review=lambda _command, _review: None,
            backend=backend,
        )

    assert caught.value.code == "browser_revision_conflict"
    assert fake_admissions.by_command == {}
    assert backend.effects == []


def test_current_profile_policy_can_deny_before_admission_or_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_admissions = FakeAdmissions()
    fake_admissions.install(monkeypatch)
    backend = FakeBackend(active=True, url="https://example.test/")
    snapshot = controls.read_browser_controls(
        CONVERSATION, validate=lambda: None, backend=backend
    )
    _review, command = _reviewed_command(
        backend, "browser.check", {"revision": snapshot["revision"]}
    )

    def deny(_action: str) -> None:
        raise controls.ClientBrowserControlError("browser_action_denied")

    with pytest.raises(controls.ClientBrowserControlError) as caught:
        controls.execute_browser_command(
            command,
            CONVERSATION,
            owner_id=OWNER,
            authority_id=AUTHORITY,
            key="browser-command-key-denied",
            validate=lambda: None,
            validate_action=deny,
            validate_review=lambda _command, _review: None,
            backend=backend,
        )

    assert caught.value.code == "browser_action_denied"
    assert fake_admissions.by_command == {}
    assert backend.effects == []


def test_read_receipt_requires_the_same_authority_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_admissions = FakeAdmissions()
    fake_admissions.install(monkeypatch)
    backend = FakeBackend(active=True, url="https://example.test/")
    snapshot = controls.read_browser_controls(
        CONVERSATION, validate=lambda: None, backend=backend
    )
    _review, command = _reviewed_command(
        backend, "browser.check", {"revision": snapshot["revision"]}
    )
    controls.execute_browser_command(
        command,
        CONVERSATION,
        owner_id=OWNER,
        authority_id=AUTHORITY,
        key="browser-command-key-receipt",
        validate=lambda: None,
        validate_action=lambda _action: None,
        validate_review=lambda _command, _review: None,
        backend=backend,
    )

    with pytest.raises(controls.ClientBrowserControlError) as caught:
        controls.read_browser_receipt(
            owner_id=OWNER,
            authority_id="different-authority",
            conversation_id=CONVERSATION,
            command_id=command["command_id"],
            validate=lambda: None,
        )

    assert caught.value.code == "browser_receipt_unavailable"


def test_canonical_passive_read_does_not_create_a_browser_session() -> None:
    class Manager:
        def __init__(self) -> None:
            self.status_calls = 0
            self.session_calls = 0

        @staticmethod
        def has_active_session() -> bool:
            return False

        def status_snapshot(self, _conversation_id: str) -> dict:
            self.status_calls += 1
            raise AssertionError("inactive manager must not construct a status service")

        def get_session(self, _conversation_id: str):
            self.session_calls += 1
            raise AssertionError("passive read must not create a session")

    manager = Manager()
    result = controls.read_browser_controls(
        CONVERSATION,
        validate=lambda: None,
        backend=controls.CanonicalBrowserControlBackend(manager),
    )

    assert result["state"] == "idle"
    assert manager.status_calls == 0
    assert manager.session_calls == 0
