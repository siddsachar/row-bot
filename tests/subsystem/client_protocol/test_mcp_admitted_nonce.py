"""MCP launch approval survives only its exact physical owner/discovery delta."""

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.api.v1.security import ClientSecurity, ProtocolError
from row_bot.application.capability_configuration_controls import _server_id
from row_bot.mcp_client import runtime
from tests.subsystem.client_protocol.test_protocol_security import context

pytestmark = pytest.mark.subsystem


@pytest.fixture
def launch(monkeypatch):
    now = [10.0]
    policy = {
        "native": ["enabled"],
        "plugins": [],
        "global_config": {"approval": True},
        "mcp": {
            "enabled": True,
            "servers": [["Synthetic", True, {"safe": {"requires_approval": True}}]],
            "registrations": [],
            "effects": [
                ["Synthetic", "cached_tool", True, True],
                ["Other", "read", False, False],
            ],
        },
    }
    original = copy.deepcopy(policy)
    security = ClientSecurity("fixture", clock=lambda: now[0], policy=lambda: policy)
    current = security.handshake(context())
    target = _server_id("Synthetic")
    nonce = security.approval_nonce(
        current,
        "settings:mcp-runtime:" + target,
        "revision",
        "digest",
        ttl=10,
        mcp_server_id=target,
    )
    owner = SimpleNamespace(runtime_id=str(uuid4()))
    monkeypatch.setattr(runtime, "_servers", {})

    def ordinary():
        security.consume_nonce(
            current,
            "settings:mcp-runtime:" + target,
            "revision",
            "digest",
            nonce,
            "command",
        )

    def reserve():
        runtime._servers["Synthetic"] = owner
        policy["mcp"]["registrations"].append(["Synthetic", id(owner)])
        policy["mcp"]["effects"].append(
            ["Synthetic", "newly_discovered_tool", True, True]
        )

    def admitted(**kwargs):
        security.consume_admitted_mcp_nonce(
            current,
            "settings:mcp-runtime:" + target,
            "revision",
            "digest",
            nonce,
            kwargs.get("command", "command"),
            server_id=kwargs.get("server", target),
            runtime_id=kwargs.get("runtime_id", owner.runtime_id),
        )

    return SimpleNamespace(
        security=security,
        current=current,
        now=now,
        policy=policy,
        original=original,
        owner=owner,
        target=target,
        nonce=nonce,
        ordinary=ordinary,
        reserve=reserve,
        admitted=admitted,
    )


def test_same_command_retains_scoped_nonce_and_only_its_own_discovery_is_normalized(
    launch,
):
    launch.ordinary()
    launch.ordinary()
    launch.reserve()
    before = copy.deepcopy(launch.policy)
    with pytest.raises(ProtocolError, match="approval_expired"):
        launch.ordinary()  # The ordinary policy did change; this check is not weakened.
    launch.admitted()
    launch.admitted()
    assert launch.policy == before
    assert launch.original["mcp"]["registrations"] == []


def test_admitted_nonce_requires_original_ordinary_approval(launch):
    launch.reserve()
    with pytest.raises(ProtocolError, match="approval_expired"):
        launch.admitted()


def test_first_admitted_owner_is_pinned_even_if_a_later_callback_supplies_replacement_identity(
    launch,
):
    launch.ordinary()
    launch.reserve()
    launch.admitted()
    replacement = SimpleNamespace(runtime_id=str(uuid4()))
    runtime._servers["Synthetic"] = replacement
    launch.policy["mcp"]["registrations"] = [["Synthetic", id(replacement)]]
    with pytest.raises(ProtocolError, match="approval_expired"):
        launch.admitted(runtime_id=replacement.runtime_id)


@pytest.mark.parametrize(
    "change",
    [
        "wrong_command",
        "wrong_server",
        "wrong_runtime",
        "missing_owner",
        "replacement_owner",
        "other_registration",
        "other_discovery",
        "saved_target_tool_policy",
        "global_policy",
        "plugin_policy",
        "expired",
        "session_removed",
    ],
)
def test_unrelated_policy_or_owner_changes_and_deadlines_still_reject(launch, change):
    launch.ordinary()
    launch.reserve()
    kwargs = {}
    if change == "wrong_command":
        kwargs["command"] = "different-command"
    elif change == "wrong_server":
        kwargs["server"] = _server_id("Other")
    elif change == "wrong_runtime":
        kwargs["runtime_id"] = str(uuid4())
    elif change == "missing_owner":
        runtime._servers.clear()
    elif change == "replacement_owner":
        runtime._servers["Synthetic"] = SimpleNamespace(runtime_id=str(uuid4()))
    elif change == "other_registration":
        launch.policy["mcp"]["registrations"].append(["Other", 123])
    elif change == "other_discovery":
        launch.policy["mcp"]["effects"].append(["Other", "new", True, True])
    elif change == "saved_target_tool_policy":
        launch.policy["mcp"]["servers"][0][2]["safe"]["requires_approval"] = False
    elif change == "global_policy":
        launch.policy["global_config"]["approval"] = False
    elif change == "plugin_policy":
        launch.policy["plugins"].append(["another", "changed"])
    elif change == "expired":
        launch.now[0] = 21.0
    elif change == "session_removed":
        launch.security._sessions.clear()
    with pytest.raises(ProtocolError, match="approval_expired"):
        launch.admitted(**kwargs)


def test_regular_nonce_behavior_and_scoped_pruning_remain_compatible(launch):
    nonce = launch.security.approval_nonce(launch.current, "ordinary", "1", "effect")
    launch.security.consume_nonce(
        launch.current, "ordinary", "1", "effect", nonce, "one"
    )
    with pytest.raises(ProtocolError, match="approval_already_resolved"):
        launch.security.consume_nonce(
            launch.current, "ordinary", "1", "effect", nonce, "two"
        )
    launch.now[0] = 21
    launch.security._prune()
    assert len(launch.security._nonces) == 1
