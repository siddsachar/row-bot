"""Explicit bounded subscription checks over retained transports and flow ownership."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from row_bot.application.subscription_controls import SubscriptionFlows, SubscriptionError, _BoundedClient, _expiry
from row_bot.providers.config import ProviderConfigError, load_provider_config, provider_config_revision, update_provider_config
from row_bot.providers.selection import model_ref as qualified_ref, parse_model_ref
from row_bot.runtime import admissions

PROVIDER_KINDS = {"codex": ("tokens",), "claude_subscription": ("tokens", "runtime"), "xai_oauth": ("tokens", "runtime", "vision")}


@dataclass(frozen=True)
class SubscriptionProbeResult:
    provider_id: str
    kind: str
    model_ref: str | None
    status: str
    chat_ok: bool | None
    tool_calling: bool | None
    tool_round_trip: bool | None
    vision_ok: bool | None
    checked_at: str | None


@dataclass(frozen=True)
class SubscriptionProbeSnapshot:
    schema_version: int
    revision: str
    items: tuple[SubscriptionProbeResult, ...]


def _result(provider: str, kind: str, raw: dict) -> SubscriptionProbeResult:
    model = raw.get("model_id") or raw.get("model")
    reference = qualified_ref(provider, model) if isinstance(model, str) and model and len(model.encode("utf-8", errors="surrogatepass")) <= 512 and not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in model) else None
    status = raw.get("status") if kind == "tokens" else "passed" if raw.get("ok") is True else "failed" if raw.get("ok") is False else "unavailable"
    if status not in {"passed", "failed", "missing", "expired", "unavailable"}:
        status = "unavailable"
    def flag(name: str) -> bool | None:
        value = raw.get(name)
        return value if type(value) is bool else None
    return SubscriptionProbeResult(provider, kind, reference, status, flag("chat_ok"), flag("tool_calling"),
        flag("tool_round_trip"), raw.get("ok") if kind == "vision" and type(raw.get("ok")) is bool else flag("vision_ok"),
        _expiry(raw.get("probed_at")))


def read_probes(*, validate: Callable[[], None] = lambda: None) -> SubscriptionProbeSnapshot:
    validate()
    cfg = load_provider_config(strict=True)
    items = []
    for provider, kinds in PROVIDER_KINDS.items():
        entry = cfg.get("providers", {}).get(provider, {})
        for kind in kinds:
            raw = entry.get("last_token_check" if kind == "tokens" else f"last_{kind}_probe", {})
            items.append(_result(provider, kind, raw if isinstance(raw, dict) else {}))
    result = SubscriptionProbeSnapshot(1, provider_config_revision(cfg), tuple(items))
    validate()
    return result


def _intent(provider: str, revision: str, kind: str, reference: str | None) -> dict:
    if not isinstance(provider, str) or not isinstance(kind, str) or kind not in PROVIDER_KINDS.get(provider, ()):
        raise ProviderConfigError("invalid_subscription_probe")
    if not isinstance(revision, str) or len(revision) != 64 or any(char not in "0123456789abcdef" for char in revision):
        raise ProviderConfigError("invalid_subscription_probe")
    if kind == "tokens":
        if reference is not None:
            raise ProviderConfigError("invalid_subscription_probe")
    else:
        parsed = parse_model_ref(reference) if isinstance(reference, str) and len(reference) <= 647 else None
        if not parsed or parsed[0] != provider or qualified_ref(*parsed) != reference or not parsed[1] or len(parsed[1].encode("utf-8", errors="surrogatepass")) > 512 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in parsed[1]):
            raise ProviderConfigError("invalid_subscription_probe")
    return {"provider_id": provider, "provider_revision": revision, "kind": kind, "model_ref": reference}


def review_probe(provider_id: str, provider_revision: str, kind: str, model_ref: str | None, *, validate: Callable[[], None]) -> dict:
    intent = _intent(provider_id, provider_revision, kind, model_ref)
    snapshot = read_probes(validate=validate)
    if snapshot.revision != provider_revision:
        raise ProviderConfigError("revision_conflict")
    if model_ref:
        from row_bot.application.provider_default_model import _saved_catalog
        from row_bot.providers.model_catalog import build_saved_model_catalog_rows
        saved = _saved_catalog()
        rows = build_saved_model_catalog_rows(cloud_cache=saved.cloud_cache, ollama_rows=saved.ollama_rows,
            provider_config=load_provider_config(strict=True))
        if not any(row.selection_ref == model_ref and "chat" in row.categories for row in rows):
            raise ProviderConfigError("model_configuration_unavailable")
    validate()
    return {**intent, "action_digest": admissions.keyed_digest(intent)}


def _make_model(provider, model_id, captured, validate, bounded, stack):
    import httpx
    if provider == "xai_oauth":
        from row_bot.providers import xai_oauth
        from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses
        base_url = xai_oauth._validated_xai_base_url(str(captured[1].get("base_url") or xai_oauth.XAI_OAUTH_BASE_URL))
        model = ChatXAIOAuthResponses(model_name=model_id, base_url=base_url, http_client=bounded, timeout=30)
    else:
        from row_bot.providers import claude_subscription
        from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages
        class BoundedTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                validate()
                body = request.read()
                if len(body) > 64 * 1024:
                    raise ProviderConfigError("subscription_probe_limit")
                return bounded._request(request.method, str(request.url), headers=dict(request.headers), content=body, timeout=30)
        client = stack.enter_context(httpx.Client(transport=BoundedTransport(), follow_redirects=False))
        credentials = claude_subscription.claude_subscription_runtime_credentials(refresh_if_needed=False, _snapshot=captured)
        import anthropic
        sdk = claude_subscription.claude_subscription_sdk_client(credentials.access_token, timeout=30, allow_cli_probe=False,
            client_factory=lambda **kwargs: anthropic.Anthropic(**kwargs, http_client=client, max_retries=0))
        stack.callback(sdk.close)
        model = ChatClaudeSubscriptionMessages(model_name=model_id, anthropic_client=sdk, max_tokens=96)
    model.bind_probe_credentials(captured, validate)
    return model


def _check_tokens(provider, proof, revision, validate):
    from row_bot.providers import codex, claude_subscription, xai_oauth
    module = {"codex": codex, "claude_subscription": claude_subscription, "xai_oauth": xai_oauth}[provider]
    validate()
    health = getattr(module, f"check_{provider}_token_health")(refresh_if_needed=False)
    validate()
    status = "passed" if health.runnable else "missing" if health.status == "missing" else "expired" if health.status == "expired" else "unavailable"
    raw = {"status": status, "probed_at": datetime.now(timezone.utc).isoformat()}
    def publish(cfg: dict) -> None:
        validate()
        if provider_config_revision(cfg) != revision:
            raise ProviderConfigError("revision_conflict")
        entry = cfg.setdefault("providers", {}).setdefault(provider, {})
        entry["last_token_check"] = raw
        entry["subscription_probe_command"] = proof
        validate()
    update_provider_config(publish)
    return _result(provider, "tokens", raw)


def execute_probe(*, flows: SubscriptionFlows, owner_id: str, key: str, command: dict,
                  validate: Callable[[], None], validate_review: Callable[[dict], None],
                  model_factory: Callable = _make_model) -> dict:
    payload = command.get("payload")
    if command.get("type") != "provider.subscription.probe" or not isinstance(payload, dict):
        raise ProviderConfigError("invalid_command")
    intent = _intent(payload.get("provider_id"), payload.get("provider_revision"), payload.get("kind"), payload.get("model_ref"))
    provider, kind, revision = intent["provider_id"], intent["kind"], intent["provider_revision"]
    command_id = command["command_id"]
    proof = {"owner_id": owner_id, "key": key, "command_id": command_id}
    with flows.probe_operation(owner_id=owner_id, command_id=command_id, provider_id=provider, revision=revision, validate=validate) as (flow, guard):
        try:
            replay = admissions.claim_command(owner_id, key, command, f"subscription_probe:{provider}")
        except admissions.AdmissionError as exc:
            cfg = load_provider_config(strict=True)
            entry = cfg.get("providers", {}).get(provider, {})
            if str(exc) != "operation_uncertain" or entry.get("subscription_probe_command") != proof:
                raise ProviderConfigError(str(exc)) from None
            raw = entry.get("last_token_check" if kind == "tokens" else f"last_{kind}_probe", {})
            result = {"command_id": command_id, "status": "completed", "result": asdict(_result(provider, kind, raw))}
            flow.probe_result = result["result"]
            validate()
            try:
                admissions.complete_command(owner_id, key, result)
            except Exception:
                raise ProviderConfigError("subscription_probe_unconfirmed") from None
            return result
        if replay is not None:
            validate()
            flow.probe_result = replay["result"]
            return replay
        try:
            guard()
            reviewed = review_probe(provider, revision, kind, intent["model_ref"], validate=guard)
            validate_review(reviewed)
        except Exception:
            admissions.reject_command(owner_id, key, "subscription_probe_review_invalid")
            raise ProviderConfigError("subscription_probe_review_invalid") from None
        try:
            if kind == "tokens":
                result = _check_tokens(provider, proof, revision, guard)
            else:
                from row_bot.providers import auth_store, claude_subscription, xai_oauth
                captured = auth_store.read_provider_oauth_bundle_snapshot(provider)
                guard()
                if captured[2] != revision:
                    raise ProviderConfigError("revision_conflict")
                flow.client = _BoundedClient(guard)
                with ExitStack() as stack:
                    model_id = parse_model_ref(intent["model_ref"])[1]
                    model = model_factory(provider, model_id, captured, guard, flow.client, stack)
                    guard()
                    runner = claude_subscription.run_claude_subscription_runtime_probe if provider == "claude_subscription" else xai_oauth.run_xai_oauth_vision_probe if kind == "vision" else xai_oauth.run_xai_oauth_runtime_probe
                    raw = runner(model_id, chat_model=model, strict=True, expected_revision=revision, validate=guard, command_proof=proof)
                result = _result(provider, kind, raw)
            flow.probe_result = asdict(result)
            public = {"command_id": command_id, "status": "completed", "result": asdict(result)}
            admissions.complete_command(owner_id, key, public)
            validate()
            return public
        except (ProviderConfigError, SubscriptionError):
            raise
        except Exception:
            raise ProviderConfigError("subscription_probe_unconfirmed") from None


def read_probe_receipt(*, owner_id: str, command_id: str, validate: Callable[[], None]) -> dict | None:
    validate()
    row = admissions.read_command_metadata(owner_id, command_id)
    if row is None or row["type"] != "provider.subscription.probe" or row["target"] not in {f"subscription_probe:{provider}" for provider in PROVIDER_KINDS}:
        validate()
        return None
    cfg = load_provider_config(strict=True)
    provider = row["target"].removeprefix("subscription_probe:")
    published = cfg.get("providers", {}).get(provider, {}).get("subscription_probe_command") == {"owner_id": owner_id, "key": row["key"], "command_id": command_id}
    status = "completed" if row["status"] == "completed" or published else "rejected" if row["status"] == "rejected" else "uncertain"
    result = {"command_id": command_id, "provider_id": provider, "status": status, "published": published}
    validate()
    return result
