"""Current document worker authority stays in the canonical client session."""
from dataclasses import replace
from threading import Event, Thread

import pytest

from row_bot.api.v1.security import ClientSecurity, ProtocolError
from tests.subsystem.client_protocol.test_protocol_security import context

pytestmark = pytest.mark.subsystem


@pytest.fixture
def authority():
    now = [10.0]
    security = ClientSecurity("fixture", clock=lambda: now[0])
    current = security.handshake(context())
    return security, current, now


def test_missing_binding_and_unknown_session_fail_closed(authority):
    security, current, _ = authority
    for owner in (current.id, "missing"):
        with pytest.raises(ProtocolError, match="session_expired"):
            security.validate_worker(owner)


def test_same_session_request_keeps_original_live_validator(authority):
    security, current, _ = authority
    calls = []
    security.bind_worker_validation(current, lambda: calls.append("original"))
    security.bind_worker_validation(current, lambda: pytest.fail("Replaced authority"))
    security.validate_worker(current.id)
    security.validate_worker(current.id)
    assert calls == ["original", "original"]
    assert "worker_validation" not in repr(current)


@pytest.mark.parametrize("change", ["expired", "removed", "replaced"])
def test_binding_refuses_stale_session_object(authority, change):
    security, current, now = authority
    if change == "expired":
        now[0] = current.expires
    elif change == "removed":
        security._sessions.pop(current.id)
    else:
        security._sessions[current.id] = replace(current)
    with pytest.raises(ProtocolError, match="session_expired"):
        security.bind_worker_validation(current, lambda: None)


@pytest.mark.parametrize("failure", [ProtocolError("authentication_required", 401), RuntimeError("loop stopped"), TimeoutError("auth deadline")])
def test_current_auth_and_loop_failures_propagate(authority, failure):
    security, current, _ = authority
    def reject():
        raise failure
    security.bind_worker_validation(current, reject)
    with pytest.raises(type(failure)) as caught:
        security.validate_worker(current.id)
    assert caught.value is failure


@pytest.mark.parametrize("change", ["expired", "removed", "replaced", "cleared", "rebound"])
def test_inflight_callback_rechecks_exact_session_after_auth(authority, change):
    security, current, now = authority
    entered, release = Event(), Event()
    failures = []
    def validate():
        entered.set()
        assert release.wait(5)
    def run():
        try:
            security.validate_worker(current.id)
        except BaseException as error:
            failures.append(error)
    security.bind_worker_validation(current, validate)
    thread = Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        # This lock acquisition also proves the validator does not own _lock
        # while waiting on a middleware/event-loop callback.
        assert security._lock.acquire(timeout=1)
        try:
            if change == "expired":
                now[0] = current.expires
            elif change == "removed":
                security._sessions.pop(current.id)
            elif change == "replaced":
                security._sessions[current.id] = replace(current)
            else:
                security.clear_worker_validations()
                if change == "rebound":
                    security.bind_worker_validation(current, lambda: None)
        finally:
            security._lock.release()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], ProtocolError)
    assert failures[0].code == "session_expired"


def test_callback_can_validate_canonical_session_without_deadlock(authority):
    security, current, _ = authority
    def validate():
        assert security.session(context(), current.id, current.csrf) is current
    security.bind_worker_validation(current, validate)
    security.validate_worker(current.id)
    security.clear_worker_validations()
    with pytest.raises(ProtocolError, match="session_expired"):
        security.validate_worker(current.id)
    assert security.session(context(), current.id, current.csrf) is current


def test_restart_never_recreates_old_session_authority(authority):
    security, current, _ = authority
    security.bind_worker_validation(current, lambda: None)
    restarted = ClientSecurity("fixture")
    new = restarted.handshake(context())
    restarted.bind_worker_validation(new, lambda: None)
    with pytest.raises(ProtocolError, match="session_expired"):
        restarted.validate_worker(current.id)
    restarted.validate_worker(new.id)


def test_nonvalidating_callback_return_is_not_authority(authority):
    security, current, _ = authority
    security.bind_worker_validation(current, lambda: False)
    with pytest.raises(ProtocolError, match="authentication_required"):
        security.validate_worker(current.id)
