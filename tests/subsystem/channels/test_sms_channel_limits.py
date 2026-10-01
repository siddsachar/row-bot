from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.subsystem


class FakeSMSRequest:
    def __init__(self, *, sender: str, body: str, sid: str = "sid-1", headers: dict | None = None) -> None:
        self.headers = {"content-length": "0", **(headers or {})}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.url = "https://example.invalid/sms"
        self._form = {"From": sender, "Body": body, "MessageSid": sid}

    async def form(self) -> dict[str, str]:
        return dict(self._form)


async def _signed(_request, _client_ip):
    return None


def _running_sms(monkeypatch: pytest.MonkeyPatch, *, token: str = "fixture-auth-token"):
    """The SMS channel running, with every step after the signature check recorded."""
    from row_bot.channels import sms

    reached: list[str] = []
    monkeypatch.setattr(sms, "_running", True)
    monkeypatch.setattr(sms, "_get_auth_token", lambda: token)
    monkeypatch.setattr(sms, "_webhook_public_url", "https://row-bot.example.invalid")
    monkeypatch.setattr(sms, "_is_authorised", lambda phone: reached.append(phone) or False)
    monkeypatch.setattr(sms.ch_auth, "verify_pairing_code", lambda *_args: False)
    sms._rate_limits.clear()
    sms._seen_sids.clear()
    return sms, reached


def test_inbound_sms_without_a_saved_auth_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """B212: the route is reachable through the tunnel, so no token means no trust."""
    sms, reached = _running_sms(monkeypatch, token="")

    response = asyncio.run(sms._handle_inbound_sms(FakeSMSRequest(sender="+15551234567", body="hi")))

    assert response.status_code == 403
    assert reached == []


def test_inbound_sms_is_refused_when_the_signature_check_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    sms, reached = _running_sms(monkeypatch)
    monkeypatch.setitem(sys.modules, "twilio.request_validator", None)

    response = asyncio.run(sms._handle_inbound_sms(FakeSMSRequest(sender="+15551234567", body="hi")))

    assert response.status_code == 503
    assert reached == []


def test_inbound_sms_with_a_wrong_signature_is_refused_and_a_valid_one_is_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from twilio.request_validator import RequestValidator

    sms, reached = _running_sms(monkeypatch)
    form = {"From": "+15551234567", "Body": "hi", "MessageSid": "sid-signed"}
    good = RequestValidator("fixture-auth-token").compute_signature("https://row-bot.example.invalid/sms", form)

    wrong = asyncio.run(sms._handle_inbound_sms(
        FakeSMSRequest(sender="+15551234567", body="hi", sid="sid-signed", headers={"X-Twilio-Signature": "forged"})))
    assert wrong.status_code == 403
    assert reached == []

    signed = asyncio.run(sms._handle_inbound_sms(
        FakeSMSRequest(sender="+15551234567", body="hi", sid="sid-signed", headers={"X-Twilio-Signature": good})))
    assert signed.status_code == 200
    assert reached == ["+15551234567"]


def test_inbound_sms_with_a_malformed_length_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    sms, reached = _running_sms(monkeypatch)

    response = asyncio.run(sms._handle_inbound_sms(
        FakeSMSRequest(sender="+15551234567", body="hi", headers={"content-length": "twelve"})))

    assert response.status_code == 400
    assert reached == []


def test_sms_capabilities_stay_final_text_only() -> None:
    from row_bot.channels.sms import SMSChannel

    caps = SMSChannel().capabilities

    assert caps.buttons is False
    assert caps.streaming is False
    assert caps.typing is False
    assert caps.reactions is False
    assert caps.slash_commands is True


def test_sms_split_keeps_chunks_within_limit() -> None:
    from row_bot.channels.sms import SMS_MAX_LEN, _split_sms

    chunks = _split_sms("word " * 500)

    assert len(chunks) > 1
    assert all(len(chunk) <= SMS_MAX_LEN for chunk in chunks)
    assert "".join(chunk.replace("\n", "") for chunk in chunks).replace(" ", "").startswith("word")


def test_sms_pending_interrupt_rejects_unrecognized_text_without_agent_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from row_bot.channels import sms

    replies: list[str] = []

    monkeypatch.setattr(sms, "_refuse_unsigned", _signed)
    monkeypatch.setattr(sms, "_running", True)
    monkeypatch.setattr(sms, "_is_authorised", lambda _phone: True)
    monkeypatch.setattr(sms, "_send_reply", lambda _phone, text: replies.append(text))
    monkeypatch.setattr(
        sms,
        "_run_agent_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("agent should not run")),
    )
    sms._rate_limits.clear()
    sms._seen_sids.clear()
    with sms._pending_lock:
        sms._pending_interrupts.clear()
        sms._pending_interrupts["+15551234567"] = {
            "data": {"tool": "shell", "description": "Run command"},
            "config": {"configurable": {"thread_id": "sms-thread"}},
        }

    response = asyncio.run(
        sms._handle_inbound_sms(
            FakeSMSRequest(sender="+15551234567", body="maybe", sid="sid-pending")
        )
    )

    assert response.status_code == 200
    assert replies == ["Approval pending. Reply YES or NO."]
    with sms._pending_lock:
        assert "+15551234567" in sms._pending_interrupts

    with sms._pending_lock:
        sms._pending_interrupts.clear()


def test_sms_preserves_compaction_notice_as_separate_delivery(monkeypatch) -> None:
    from row_bot.channels import sms
    from row_bot.tools import registry as tool_registry

    fake_agent = SimpleNamespace(
        stream_agent=lambda *_args, **_kwargs: iter([
            ("compaction_succeeded", {"event_id": 17, "display_copy": "Context compacted"}),
            ("token", "answer"),
            ("done", "answer"),
        ])
    )
    monkeypatch.setattr(sms, "_agent_mod", lambda: fake_agent)
    monkeypatch.setattr(tool_registry, "get_enabled_tools", lambda: [])

    answer, interrupt, notices = sms._run_agent_sync(
        "hello",
        {"configurable": {"thread_id": "sms-thread"}},
    )

    assert answer == "answer"
    assert interrupt is None
    assert notices == [{"event_id": 17, "display_copy": "Context compacted"}]
