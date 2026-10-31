"""A turn whose model's sign-in can't be used says to sign in again, not "interrupted · Resume" (B351)."""
from __future__ import annotations

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, fixture_id, platform, submit  # noqa: F401

pytestmark = pytest.mark.subsystem


class _SignInExpired:
    def stream(self, *_args, **_kwargs):
        from row_bot.providers.runtime import ProviderSignInRequired

        raise ProviderSignInRequired("codex", "ChatGPT", "Codex subscription runtime needs an in-app ChatGPT login.")
        yield  # A generator, as a real stream is.

    def resume(self, *_args, **_kwargs):
        return self.stream()


def test_a_turn_refused_for_an_expired_sign_in_names_whose_sign_in(platform, monkeypatch):  # noqa: F811
    published: list[tuple[str, dict]] = []
    original = platform.projection.publish

    def recording(conversation_id, kind, payload, *args, **kwargs):
        published.append((kind, dict(payload)))
        return original(conversation_id, kind, payload, *args, **kwargs)

    monkeypatch.setattr(platform.projection, "publish", recording)

    accepted = submit(platform, _SignInExpired(), "sign-in-expired")
    assert platform.registry.get(accepted["execution_id"]).producer_done.wait(10)

    errors = [payload for kind, payload in published if kind == "generation.error"]
    assert errors == [{"code": "sign_in_required", "provider": "ChatGPT"}]


class _RefusedMidReply:
    """The real agent loop over a graph whose model call the provider refuses part-way through a reply."""

    def __init__(self, refusal: Exception) -> None:
        self.refusal = refusal

    def stream(self, *_args, **_kwargs):
        from row_bot import agent

        refusal = self.refusal

        class Graph:
            def stream(self, *_args, **_kwargs):
                raise RuntimeError("the model call failed") from refusal
                yield  # A generator, as a real stream is.

        return agent._stream_graph(Graph(), {}, {"configurable": {}})

    def resume(self, *_args, **_kwargs):
        return self.stream()


def _errors_after(platform, monkeypatch, stream) -> list[dict]:  # noqa: F811
    published: list[tuple[str, dict]] = []
    original = platform.projection.publish

    def recording(conversation_id, kind, payload, *args, **kwargs):
        published.append((kind, dict(payload)))
        return original(conversation_id, kind, payload, *args, **kwargs)

    monkeypatch.setattr(platform.projection, "publish", recording)
    accepted = submit(platform, stream, "refused-mid-reply")
    assert platform.registry.get(accepted["execution_id"]).producer_done.wait(10)
    return [payload for kind, payload in published if kind == "generation.error"]


def test_a_plan_limit_mid_reply_reaches_the_chat_in_the_providers_words(platform, monkeypatch):  # noqa: F811
    from row_bot.providers.claude_subscription import ClaudePlanUnavailable

    words = "Claude is limiting requests right now. Wait a moment, then try again."
    errors = _errors_after(platform, monkeypatch, _RefusedMidReply(ClaudePlanUnavailable(words, state="limited")))
    assert errors == [{"code": "provider_unavailable", "message": words}]


def test_a_sign_out_mid_reply_asks_to_sign_in_again(platform, monkeypatch):  # noqa: F811
    from row_bot.providers.runtime import ProviderSignInRequired

    signed_out = ProviderSignInRequired("claude_subscription", "Claude", "Claude Code is signed out.")
    errors = _errors_after(platform, monkeypatch, _RefusedMidReply(signed_out))
    assert errors == [{"code": "sign_in_required", "provider": "Claude"}]


def _send_to_claude_plan(platform, monkeypatch, status) -> list[dict]:  # noqa: F811
    """Send through the real agent to the Claude plan, with Claude Code reporting ``status``."""
    from row_bot.providers import claude_subscription

    monkeypatch.setattr(claude_subscription, "claude_plan_status", lambda *_args, **_kwargs: status)
    published: list[tuple[str, dict]] = []
    original = platform.projection.publish

    def recording(conversation_id, kind, payload, *args, **kwargs):
        published.append((kind, dict(payload)))
        return original(conversation_id, kind, payload, *args, **kwargs)

    monkeypatch.setattr(platform.projection, "publish", recording)
    accepted = platform.execute(
        owner_id="fixture-owner", idempotency_key=fixture_id("claude-plan:key"), target="conversation-a",
        command=command("conversation.submit", "claude-plan", {
            "submission_id": fixture_id("claude-plan"), "text": "hello", "attachment_refs": [],
            "model_selection": {"provider_id": "claude_subscription",
                                "model_ref": "model:claude_subscription:claude-opus-4-8"}}))
    assert platform.registry.get(accepted["execution_id"]).producer_done.wait(30)
    return [payload for kind, payload in published if kind == "generation.error"]


def test_a_send_to_a_signed_out_claude_plan_asks_to_sign_in_again(platform, monkeypatch):  # noqa: F811
    from row_bot.providers.claude_subscription import ClaudePlanStatus

    errors = _send_to_claude_plan(platform, monkeypatch, ClaudePlanStatus(state="signed_out"))
    assert errors == [{"code": "sign_in_required", "provider": "Claude"}]


def test_a_send_without_claude_code_says_what_claude_code_needs(platform, monkeypatch):  # noqa: F811
    from row_bot.providers.claude_subscription import ClaudePlanStatus, unavailable_message

    status = ClaudePlanStatus(state="not_installed")
    errors = _send_to_claude_plan(platform, monkeypatch, status)
    assert errors == [{"code": "provider_unavailable", "message": unavailable_message(status)}]
