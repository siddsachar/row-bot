import base64
import json
from urllib.parse import parse_qs, urlparse

import row_bot.providers.config as provider_config
from row_bot.providers.auth_store import get_provider_secret, set_provider_secret
from row_bot.providers.claude_subscription import (
    CLAUDE_SUBSCRIPTION_PROVIDER_ID,
    DEFAULT_CLAUDE_OAUTH_AUTHORIZE_URL,
    DEFAULT_CLAUDE_OAUTH_CLIENT_ID,
    DEFAULT_CLAUDE_OAUTH_REDIRECT_URI,
    DEFAULT_CLAUDE_OAUTH_SCOPES,
    DEFAULT_CLAUDE_OAUTH_TOKEN_URL,
    ClaudeSubscriptionAuthorization,
    ClaudeSubscriptionTokenSet,
    check_claude_subscription_token_health,
    disconnect_claude_subscription_metadata,
    discover_claude_subscription_credentials,
    exchange_claude_subscription_authorization,
    external_reference_metadata,
    fallback_claude_subscription_model_infos,
    fetch_claude_subscription_model_infos,
    import_claude_subscription_setup_token,
    list_claude_subscription_model_infos,
    refresh_claude_subscription_token,
    run_claude_subscription_runtime_probe,
    save_external_reference,
    save_claude_subscription_oauth_tokens,
    seed_recommended_claude_subscription_quick_choices,
    start_claude_subscription_oauth_flow,
    summarize_claude_credentials_json,
)
from row_bot.providers.models import AuthMethod, TransportMode
from row_bot.providers.selection import add_quick_choice_for_model, list_quick_model_ids
from row_bot.secret_store import _set_backend_for_tests


class _MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _HttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("No fake response queued")
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("No fake response queued")
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def _jwt(claims):
    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).rstrip(b"=").decode("ascii")

    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}.sig"


def _valid_token(user_id="user-123", account_id="acct-123", plan_type="max"):
    return _jwt({
        "exp": 1893456000,
        "sub": user_id,
        "account_id": account_id,
        "plan_type": plan_type,
        "scope": "openid profile",
    })


def test_claude_subscription_provider_definition_and_routing_are_separate(tmp_path, monkeypatch):
    from row_bot.providers.catalog import get_provider_definition, infer_provider_id
    from row_bot.providers.resolution import resolve_provider_config
    from row_bot.providers.status import provider_status_cards

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    definition = get_provider_definition("claude_subscription")

    assert definition is not None
    assert definition.display_name == "Claude Subscription"
    assert definition.risk_label == "subscription"
    assert definition.default_transport == TransportMode.ANTHROPIC_MESSAGES
    assert AuthMethod.OAUTH_PKCE in definition.auth_methods
    assert get_provider_definition("anthropic").display_name == "Anthropic API"
    assert infer_provider_id("claude-sonnet-4-6") == "anthropic"

    resolved = resolve_provider_config("model:claude_subscription:claude-sonnet-4-6")
    assert resolved.provider_id == "claude_subscription"
    assert resolved.runtime_model == "claude-sonnet-4-6"

    card = next(card for card in provider_status_cards() if card["provider_id"] == "claude_subscription")
    assert card["group"] == "Subscription Accounts"
    assert card["runtime_enabled"] is False


def test_claude_subscription_credential_summary_is_metadata_only(tmp_path):
    credentials = tmp_path / ".credentials.json"
    credentials.write_text(json.dumps({
        "access_token": "claude-access-secret",
        "refresh_token": "claude-refresh-secret",
        "account": {"email": "person@example.test"},
        "expires_at": "2030-01-01T00:00:00+00:00",
    }), encoding="utf-8")

    summary = summarize_claude_credentials_json(credentials)
    encoded = json.dumps(summary)

    assert summary["exists"] is True
    assert summary["key_names"] == ["access_token", "account", "expires_at", "refresh_token"]
    assert summary["sensitive_key_names"] == ["access_token", "refresh_token"]
    assert summary["user_hash"] == "****test"
    assert "claude-access-secret" not in encoded
    assert "claude-refresh-secret" not in encoded
    assert "person@example.test" not in encoded


def test_claude_subscription_external_discovery_reports_shape_only(tmp_path):
    credentials = tmp_path / ".credentials.json"
    legacy = tmp_path / ".claude.json"
    credentials.write_text(json.dumps({"id_token": "id-secret", "mode": "claude-ai"}), encoding="utf-8")

    discovered = discover_claude_subscription_credentials(
        credentials_path=credentials,
        legacy_credentials_path=legacy,
        binary="definitely-missing-claude",
    )
    encoded = json.dumps(discovered)

    assert discovered["provider_id"] == "claude_subscription"
    assert discovered["source"] == "claude_code"
    assert discovered["exists"] is True
    assert discovered["cli_installed"] is False
    assert discovered["metadata_only"] is True
    assert discovered["auth_key_names"] == ["id_token", "mode"]
    assert discovered["auth_sensitive_key_names"] == ["id_token"]
    assert "id-secret" not in encoded


def test_external_credentials_includes_claude_metadata_without_values(tmp_path, monkeypatch):
    import row_bot.providers.external_credentials as external_credentials

    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    (claude_home / ".credentials.json").write_text(json.dumps({"access_token": "secret-value"}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

    entries = external_credentials.discover_external_credentials()
    claude = next(entry for entry in entries if entry["provider_id"] == "claude_subscription")
    encoded = json.dumps(claude)

    assert claude["exists"] is True
    assert claude["auth_key_names"] == ["access_token"]
    assert claude["metadata_only"] is True
    assert "secret-value" not in encoded


def test_claude_code_detection_is_not_implicit_runtime_configuration(tmp_path, monkeypatch):
    import row_bot.providers.runtime as runtime

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    credentials = tmp_path / ".credentials.json"
    credentials.write_text(json.dumps({"access_token": "external-secret"}), encoding="utf-8")
    monkeypatch.setattr("row_bot.providers.claude_subscription.discover_claude_subscription_credentials", lambda: {
        "label": str(credentials),
        "path_hash": "abc123",
        "exists": True,
        "cli_installed": True,
        "metadata_only": True,
    })
    _set_backend_for_tests(_MemoryKeyring())
    try:
        status = runtime.provider_status("claude_subscription")

        assert status["configured"] is False
        assert status["source"] == "external_cli_detected"
        assert status["external_reference_exists"] is True
        assert status["external_reference_metadata_only"] is True
        assert status["runtime_enabled"] is False
    finally:
        _set_backend_for_tests(None)


def test_claude_subscription_save_external_reference_persists_metadata_only(tmp_path, monkeypatch):
    import row_bot.providers.runtime as runtime

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    credentials = tmp_path / ".credentials.json"
    credentials.write_text(json.dumps({"access_token": "external-secret", "mode": "claude-ai"}), encoding="utf-8")

    saved = save_external_reference(credentials)
    status = runtime.provider_status("claude_subscription")
    encoded = json.dumps(saved)

    assert saved["configured"] is True
    assert saved["auth_method"] == AuthMethod.EXTERNAL_CLI.value
    assert saved["source"] == "external_cli"
    assert saved["external_reference_metadata_only"] is True
    assert status["runtime_enabled"] is False
    assert "external-secret" not in encoded


def test_claude_subscription_oauth_start_exchange_refresh_shapes(monkeypatch):
    for env_var in (
        "ROW_BOT_CLAUDE_SUBSCRIPTION_AUTHORIZE_URL",
        "ROW_BOT_CLAUDE_SUBSCRIPTION_TOKEN_URL",
        "ROW_BOT_CLAUDE_SUBSCRIPTION_CLIENT_ID",
        "ROW_BOT_CLAUDE_SUBSCRIPTION_REDIRECT_URI",
        "ROW_BOT_CLAUDE_SUBSCRIPTION_SCOPES",
    ):
        monkeypatch.delenv(env_var, raising=False)
    access_token = _valid_token()
    flow = start_claude_subscription_oauth_flow()
    parsed = urlparse(flow.authorization_url)
    query = parse_qs(parsed.query)
    exchange_client = _HttpClient([_Response(200, {
        "access_token": access_token,
        "refresh_token": "refresh-secret",
        "id_token": "id-secret",
    })])
    token_set = exchange_claude_subscription_authorization(
        ClaudeSubscriptionAuthorization(
            authorization_code=f"code-123#{flow.state}",
            code_verifier=flow.code_verifier,
            redirect_uri=flow.redirect_uri,
            token_url=flow.token_url,
            client_id=flow.client_id,
            state=flow.state,
        ),
        http_client=exchange_client,
    )
    refresh_client = _HttpClient([_Response(200, {"access_token": access_token})])
    refreshed = refresh_claude_subscription_token("refresh-secret", http_client=refresh_client)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == DEFAULT_CLAUDE_OAUTH_AUTHORIZE_URL
    assert flow.token_url == DEFAULT_CLAUDE_OAUTH_TOKEN_URL
    assert flow.client_id == DEFAULT_CLAUDE_OAUTH_CLIENT_ID
    assert flow.redirect_uri == DEFAULT_CLAUDE_OAUTH_REDIRECT_URI
    assert flow.scopes == DEFAULT_CLAUDE_OAUTH_SCOPES
    assert query["code"] == ["true"]
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [DEFAULT_CLAUDE_OAUTH_CLIENT_ID]
    assert query["redirect_uri"] == [DEFAULT_CLAUDE_OAUTH_REDIRECT_URI]
    assert query["scope"] == [" ".join(DEFAULT_CLAUDE_OAUTH_SCOPES)]
    assert query["state"] == [flow.state]
    assert query["code_challenge"] == [flow.code_challenge]
    assert query["code_challenge_method"] == ["S256"]
    assert token_set.access_token == access_token
    assert token_set.refresh_token == "refresh-secret"
    assert token_set.user_id == "user-123"
    assert refreshed.refresh_token == "refresh-secret"
    assert exchange_client.calls[0][0] == DEFAULT_CLAUDE_OAUTH_TOKEN_URL
    exchange_kwargs = exchange_client.calls[0][1]
    assert exchange_kwargs["json"] == {
        "grant_type": "authorization_code",
        "client_id": DEFAULT_CLAUDE_OAUTH_CLIENT_ID,
        "code": "code-123",
        "state": flow.state,
        "redirect_uri": DEFAULT_CLAUDE_OAUTH_REDIRECT_URI,
        "code_verifier": flow.code_verifier,
    }
    assert exchange_kwargs["headers"]["Content-Type"] == "application/json"
    assert exchange_kwargs["headers"]["User-Agent"].startswith("claude-cli/")
    assert refresh_client.calls[0][0] == "https://platform.claude.com/v1/oauth/token"
    refresh_kwargs = refresh_client.calls[0][1]
    assert refresh_kwargs["data"]["client_id"] == DEFAULT_CLAUDE_OAUTH_CLIENT_ID
    assert refresh_kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert refresh_kwargs["headers"]["User-Agent"].startswith("claude-cli/")


def test_claude_subscription_oauth_rejects_callback_state_mismatch():
    flow = start_claude_subscription_oauth_flow(
        authorize_url="https://claude.test/oauth/authorize",
        token_url="https://claude.test/oauth/token",
        client_id="client-123",
        redirect_uri="https://claude.test/callback",
    )
    client = _HttpClient([])

    try:
        exchange_claude_subscription_authorization(
            ClaudeSubscriptionAuthorization(
                authorization_code="code-123#wrong-state",
                code_verifier=flow.code_verifier,
                redirect_uri=flow.redirect_uri,
                token_url=flow.token_url,
                client_id=flow.client_id,
                state=flow.state,
            ),
            http_client=client,
        )
    except RuntimeError as exc:
        assert "state did not match" in str(exc)
    else:
        raise AssertionError("Expected OAuth state mismatch to fail")
    assert client.calls == []


def test_claude_subscription_oauth_token_save_status_and_no_secret_leak(tmp_path, monkeypatch):
    import row_bot.providers.runtime as runtime

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr("row_bot.providers.claude_subscription.discover_claude_subscription_credentials", lambda: {
        "label": "~/.claude/.credentials.json",
        "path_hash": "abc123",
        "exists": False,
        "cli_installed": False,
        "metadata_only": True,
    })
    backend = _MemoryKeyring()
    _set_backend_for_tests(backend)
    access_token = _valid_token(plan_type="pro")
    try:
        provider_config.save_provider_config({
            "providers": {
                "claude_subscription": {
                    "last_runtime_probe": {"ok": False, "errors": ["old failure"]},
                },
            },
        })
        saved = save_claude_subscription_oauth_tokens(ClaudeSubscriptionTokenSet(
            access_token=access_token,
            refresh_token="refresh-secret",
            id_token="id-secret",
        ))
        status = runtime.provider_status("claude_subscription")
        configured = runtime.list_configured_provider_ids()
        encoded = json.dumps(saved)

        assert saved["configured"] is True
        assert saved["auth_method"] == AuthMethod.OAUTH_PKCE.value
        assert saved["source"] == AuthMethod.OAUTH_PKCE.value
        assert saved["user_hash"] == "****-123"
        assert saved["account_id_hash"] == "****-123"
        assert saved["plan_type"] == "pro"
        assert "last_runtime_probe" not in saved
        assert status["runtime_enabled"] is True
        assert "claude_subscription" in configured
        assert get_provider_secret("claude_subscription", "access_token") == access_token
        assert "refresh-secret" not in encoded
        assert access_token not in encoded
    finally:
        _set_backend_for_tests(None)


def test_claude_subscription_expired_token_refreshes(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setenv("ROW_BOT_CLAUDE_SUBSCRIPTION_TOKEN_URL", "https://claude.test/oauth/token")
    monkeypatch.setenv("ROW_BOT_CLAUDE_SUBSCRIPTION_CLIENT_ID", "client-123")
    backend = _MemoryKeyring()
    _set_backend_for_tests(backend)
    expired_token = _jwt({"exp": 946684800, "sub": "old-user"})
    new_token = _valid_token(user_id="new-user")
    try:
        save_claude_subscription_oauth_tokens(ClaudeSubscriptionTokenSet(
            access_token=expired_token,
            refresh_token="refresh-secret",
        ))
        health = check_claude_subscription_token_health(
            http_client=_HttpClient([_Response(200, {"access_token": new_token})]),
        )

        assert health.status == "refreshed"
        assert health.runnable is True
        assert get_provider_secret("claude_subscription", "access_token") == new_token
    finally:
        _set_backend_for_tests(None)


def test_claude_subscription_import_setup_token_is_explicit_row_bot_owned(tmp_path, monkeypatch):
    import row_bot.providers.runtime as runtime

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    try:
        saved = import_claude_subscription_setup_token(_valid_token())
        status = runtime.provider_status("claude_subscription")

        assert saved["auth_method"] == AuthMethod.OAUTH_PKCE.value
        assert status["runtime_enabled"] is True
    finally:
        _set_backend_for_tests(None)


def test_claude_subscription_disconnect_removes_row_bot_secrets_only(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    backend = _MemoryKeyring()
    _set_backend_for_tests(backend)
    try:
        credentials = tmp_path / ".credentials.json"
        credentials.write_text(json.dumps({"access_token": "external-secret"}), encoding="utf-8")
        save_external_reference(credentials)
        set_provider_secret("claude_subscription", "access_token", "row-bot-owned-token", auth_method=AuthMethod.OAUTH_PKCE)

        disconnect_claude_subscription_metadata()

        assert "claude_subscription" not in provider_config.load_provider_config()["providers"]
        assert get_provider_secret("claude_subscription", "access_token") == ""
        assert credentials.exists()
    finally:
        _set_backend_for_tests(None)


def test_claude_subscription_runtime_probe_records_text_tool_and_round_trip(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage, ToolMessage

    class _ProbeModel:
        def __init__(self, *, bound=False):
            self.bound = bound

        def bind_tools(self, tools, tool_choice=None):
            assert [getattr(tool, "name", "") for tool in tools] == ["calculate"]
            assert tool_choice == "calculate"
            return _ProbeModel(bound=True)

        def invoke(self, messages):
            if self.bound:
                return AIMessage(content="", tool_calls=[{
                    "name": "calculate",
                    "args": {"expression": "1 + 1"},
                    "id": "toolu_probe",
                    "type": "tool_call",
                }])
            if any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage(content="done")
            return AIMessage(content="row-bot-claude-smoke-ok")

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")

    probe = run_claude_subscription_runtime_probe(chat_model=_ProbeModel())
    provider_entry = provider_config.load_provider_config()["providers"]["claude_subscription"]

    assert probe["ok"] is True
    assert probe["chat_ok"] is True
    assert probe["tool_calling"] is True
    assert probe["tool_round_trip"] is True
    assert provider_entry["last_runtime_probe"]["ok"] is True
    assert provider_entry["last_error"] == ""


def test_claude_subscription_model_catalog_live_fallback_and_bearer_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    client = _HttpClient([_Response(200, {
        "data": [
            {
                "id": "claude-live",
                "display_name": "Claude Live",
                "max_input_tokens": 500000,
                "capabilities": {"image_input": {"supported": True}},
            }
        ],
    })])

    infos = fetch_claude_subscription_model_infos(access_token="access-token", http_client=client)
    fallback = fallback_claude_subscription_model_infos()

    assert [info.model_id for info in infos] == ["claude-live"]
    assert infos[0].provider_id == "claude_subscription"
    assert infos[0].context_window == 500000
    assert "image" in infos[0].input_modalities
    assert client.calls[0][1]["headers"]["Authorization"] == "Bearer access-token"
    assert "oauth-2025-04-20" in client.calls[0][1]["headers"]["anthropic-beta"]
    assert client.calls[0][1]["headers"]["x-app"] == "cli"
    assert "x-api-key" not in client.calls[0][1]["headers"]
    assert {info.model_id for info in fallback} >= {"claude-sonnet-4-6", "claude-opus-4-8"}
    assert all(info.selection_ref.startswith("model:claude_subscription:") for info in fallback)


def test_claude_subscription_model_infos_cache_live_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    try:
        save_claude_subscription_oauth_tokens(ClaudeSubscriptionTokenSet(
            access_token=_valid_token(),
            refresh_token="refresh-secret",
        ))
        live = list_claude_subscription_model_infos(
            force_refresh=True,
            http_client=_HttpClient([_Response(200, {"data": [{"id": "claude-live", "display_name": "Claude Live"}]})]),
        )
        cached = list_claude_subscription_model_infos()
    finally:
        _set_backend_for_tests(None)

    assert [info.model_id for info in live] == ["claude-live"]
    assert [info.model_id for info in cached] == ["claude-live"]


def test_claude_subscription_quick_choice_seed_requires_runtime_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    try:
        assert seed_recommended_claude_subscription_quick_choices() == []

        save_claude_subscription_oauth_tokens(ClaudeSubscriptionTokenSet(access_token=_valid_token()))
        quick = seed_recommended_claude_subscription_quick_choices(max_choices=5)
    finally:
        _set_backend_for_tests(None)

    claude_refs = [
        choice.get("id")
        for choice in quick
        if isinstance(choice, dict) and choice.get("provider_id") == "claude_subscription"
    ]
    assert claude_refs == ["model:claude_subscription:claude-sonnet-4-6"]


def test_claude_subscription_quick_choices_are_hidden_until_runtime_enabled(tmp_path, monkeypatch):
    import row_bot.api_keys as api_keys

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    monkeypatch.setattr("row_bot.providers.runtime.provider_status", lambda provider_id: {
        "configured": True,
        "runtime_enabled": False,
    })

    add_quick_choice_for_model(
        "claude-sonnet-4-6",
        provider_id="claude_subscription",
        display_name="Claude Sonnet 4.6",
        capabilities_snapshot=fallback_claude_subscription_model_infos()[0].capability_snapshot(),
    )

    assert list_quick_model_ids("chat") == []
