"""Reading the organisation policy: every source, precedence, the trust check on files, fail-closed lockdown for
anything present but unusable, and Ed25519 signatures. Every OS location is a fake under tmp_path; the real
machine's policy locations are never read."""

from __future__ import annotations

import os
import plistlib
import stat
import sys
import uuid
from pathlib import Path

import pytest

import row_bot.policy as policy_module
from row_bot.policy import LOCKDOWN, Lockdown, NoPolicy, OrgPolicy, effective_policy, reload_policy
from row_bot.policy import sources
from row_bot.policy.sources import REG_DWORD, REG_SZ, RegistryKey, load_policy, read_registry
from row_bot.policy.trust import posix_problem, trust_problem, windows_acl_problem
from tests.helpers.policy_fakes import (
    Signer, fake_system, pin, registry_from, windows_policy_file, write_bytes, write_policy)

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

ASK = {"schema_version": 1, "approvals": {"max_level": "approve"}}


def _linux_file(root: Path) -> Path:
    return root / "etc" / "row-bot" / "org-policy.json"


def _lockdown(policy: object, reason: str) -> Lockdown:
    assert isinstance(policy, Lockdown), policy
    assert policy.reason == reason, policy.detail
    assert policy.settings == LOCKDOWN
    return policy


# --- No policy ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_with_no_policy_anywhere_nothing_is_read_and_nothing_changes(tmp_path: Path, platform: str) -> None:
    checked: list[Path] = []

    def trust(path: Path, boundary: Path | None) -> str | None:
        checked.append(path)
        return None

    policy = load_policy(fake_system(tmp_path, platform, trust=trust))

    assert policy == NoPolicy()
    assert policy.kind == "none" and policy.source is None and policy.signer is None
    assert checked == []
    assert policy.limit_approval_level("full") == "full"
    assert policy.allows_provider("claude_subscription") and policy.allows_channel("whatsapp")
    assert policy.allows_feature("install_count")


def test_an_empty_registry_key_is_no_policy(tmp_path: Path) -> None:
    left_behind = RegistryKey({}, {"models": RegistryKey({}, {"allowed_providers": RegistryKey()})})

    assert load_policy(fake_system(tmp_path, "windows", registry=left_behind)) == NoPolicy()


def test_the_policy_is_read_once_per_process_until_reloaded(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "os_system", lambda: fake_system(tmp_path))
    monkeypatch.setattr(policy_module, "_cached", None)

    first = effective_policy()
    write_policy(_linux_file(tmp_path), ASK)

    assert first == NoPolicy() and effective_policy() is first
    reloaded = reload_policy()
    assert isinstance(reloaded, OrgPolicy) and effective_policy() is reloaded


# --- Sources ------------------------------------------------------------------------------------------------

def _write_source(root: Path, kind: str) -> tuple[str, dict[str, str], RegistryKey | None]:
    environ: dict[str, str] = {}
    registry = None
    if kind == "windows registry":
        registry = registry_from(ASK)
    elif kind == "windows file":
        write_policy(windows_policy_file(root), ASK)
    elif kind == "macos managed preferences":
        write_bytes(root / "Managed Preferences" / "ai.row-bot.plist", plistlib.dumps(ASK))
    elif kind == "macos file":
        write_policy(root / "Application Support" / "Row-Bot" / "org-policy.json", ASK)
    elif kind == "linux file":
        write_policy(_linux_file(root), ASK)
    else:
        environ[sources.POLICY_FILE_ENV] = str(write_policy(root / "server" / "policy.json", ASK))
    return kind.split()[0] if kind != "env file" else "linux", environ, registry


@pytest.mark.parametrize(("kind", "source_kind"), [
    ("windows registry", "registry"),
    ("windows file", "file"),
    ("macos managed preferences", "managed_preferences"),
    ("macos file", "file"),
    ("linux file", "file"),
    ("env file", "env_file"),
])
def test_each_source_is_read(tmp_path: Path, kind: str, source_kind: str) -> None:
    platform, environ, registry = _write_source(tmp_path, kind)

    policy = load_policy(fake_system(tmp_path, platform, registry=registry, environ=environ))

    assert isinstance(policy, OrgPolicy), policy
    assert policy.source.kind == source_kind
    assert policy.limit_approval_level("auto") == "approve"
    assert policy.ignored == ()


def test_the_env_file_is_read_on_every_platform_after_the_os_sources(tmp_path: Path) -> None:
    named = write_policy(tmp_path / "server" / "policy.json", ASK)

    for platform in ("windows", "macos", "linux"):
        policy = load_policy(fake_system(tmp_path, platform, environ={sources.POLICY_FILE_ENV: str(named)}))
        assert isinstance(policy, OrgPolicy) and policy.source.location == str(named)


@pytest.mark.parametrize(("platform", "kinds", "winner"), [
    ("windows", ["windows registry", "windows file", "env file"], "registry"),
    ("windows", ["windows file", "env file"], "file"),
    ("macos", ["macos managed preferences", "macos file", "env file"], "managed_preferences"),
    ("linux", ["linux file", "env file"], "file"),
])
def test_the_first_source_present_wins_and_the_rest_are_reported(tmp_path: Path, platform: str,
                                                                 kinds: list[str], winner: str) -> None:
    environ: dict[str, str] = {}
    registry = None
    for kind in kinds:
        _, more, tree = _write_source(tmp_path, kind)
        environ.update(more)
        registry = registry or tree

    policy = load_policy(fake_system(tmp_path, platform, registry=registry, environ=environ))

    assert isinstance(policy, OrgPolicy) and policy.source.kind == winner
    assert len(policy.ignored) == len(kinds) - 1
    assert policy.ignored[-1].source.kind == "env_file"


def test_a_broken_first_source_locks_down_rather_than_falling_back(tmp_path: Path) -> None:
    write_bytes(windows_policy_file(tmp_path), b"{not json")
    named = write_policy(tmp_path / "server" / "policy.json", ASK)

    policy = load_policy(fake_system(tmp_path, "windows", environ={sources.POLICY_FILE_ENV: str(named)}))

    _lockdown(policy, "invalid")
    assert [item.source.location for item in policy.ignored] == [str(named)]


def test_a_broken_policy_still_lets_the_fix_arrive_and_local_chat_run(tmp_path: Path) -> None:
    write_bytes(windows_policy_file(tmp_path), b'{"schema_version": 1, "approvals": {"max_level": "auto"},')

    policy = _lockdown(load_policy(fake_system(tmp_path, "windows")), "invalid")

    assert policy.allows_tool("row_bot_updater") and not policy.settings.updates.external
    assert policy.allows_tool("row_bot_status")
    assert policy.allows_model("model:ollama:qwen3:8b", base_url="http://127.0.0.1:11434")
    assert not policy.allows_provider("anthropic") and not policy.allows_model("model:openai:gpt-5")
    assert not policy.allows_tool("shell")


# Reviewed for H2 (POL-1): each stays inside Row-Bot and the conversation. row_bot_send_feedback saves a local
# draft (the person sends it), row_bot_docs reads the bundled guides offline, row_bot_check_for_updates and
# row_bot_install_update are the signed self-update lockdown keeps.
REVIEWED_LOCKDOWN_SUBTOOLS = frozenset({
    "calculate", "create_chart", "list_conversations", "search_conversations", "documents", "export_to_pdf",
    "workspace_copy_file", "workspace_file_delete", "workspace_find", "workspace_move_file", "workspace_read_file",
    "workspace_write_file", "goal_status", "goal_update", "media_bring", "media_find", "media_tag", "delete_memory",
    "explore_connections", "link_memories", "list_memories", "save_memory", "search_memory", "update_memory",
    "page_create", "page_read", "page_update", "present", "row_bot_apply_proposal", "row_bot_create_skill",
    "row_bot_docs", "row_bot_fix", "row_bot_patch_skill", "row_bot_reject_proposal", "row_bot_review_skill_library",
    "row_bot_send_feedback", "row_bot_status", "row_bot_update_setting", "row_bot_verify_proposal",
    "row_bot_check_for_updates", "row_bot_install_update", "tracker_delete", "tracker_log", "tracker_query",
})


# Imports every tool module, so nightly. Lockdown's tool list is an allow-list: a tool nobody classified yet is
# refused, so this keeps the lists in step rather than guarding the behaviour (the tests above do that).
@pytest.mark.slow
def test_lockdown_names_every_core_tool_as_kept_or_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    import row_bot.tools as tool_package
    from row_bot.tools import registry

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    modules = (*tool_package._CORE_TOOL_MODULES, *tool_package._OPTIONAL_TOOL_MODULES)
    for name in tool_package._OPTIONAL_TOOL_MODULES:
        importlib.import_module(name)
    core = {owner.name for owner in registry.get_all_tools() if type(owner).__module__ in modules}

    kept, refused = LOCKDOWN.tools.allowed, LOCKDOWN.tools.disabled
    assert not kept & refused
    assert core == kept | refused, "classify a new tool for lockdown in row_bot.policy.schema"
    # The model sees the sub-tools. A kept tool that grows one has it reviewed here before lockdown keeps it.
    subtools = {tool.name for owner in registry.get_all_tools() if owner.name in kept
                for tool in owner.as_langchain_tools()}
    assert subtools == REVIEWED_LOCKDOWN_SUBTOOLS


def test_a_named_policy_file_that_is_missing_locks_down(tmp_path: Path) -> None:
    missing = tmp_path / "server" / "policy.json"

    _lockdown(load_policy(fake_system(tmp_path, environ={sources.POLICY_FILE_ENV: str(missing)})), "unreadable")
    _lockdown(load_policy(fake_system(tmp_path, environ={sources.POLICY_FILE_ENV: "policy.json"})),
              "untrusted_location")


def test_a_registry_that_cant_be_read_locks_down(tmp_path: Path) -> None:
    def denied() -> RegistryKey:
        raise PermissionError(5, "Access is denied")

    _lockdown(load_policy(sources.System("windows", {}, registry=denied)), "unreadable")


def test_an_unexpected_failure_reading_a_present_policy_locks_down(tmp_path: Path) -> None:
    write_policy(_linux_file(tmp_path), ASK)

    def broken(path: Path, boundary: Path | None) -> str | None:
        raise RuntimeError("unexpected")

    _lockdown(load_policy(fake_system(tmp_path, trust=broken)), "unreadable")


def test_program_data_that_cant_be_found_locks_down(tmp_path: Path) -> None:
    def missing() -> Path:
        raise OSError("ProgramData known folder unavailable")

    _lockdown(load_policy(sources.System("windows", {}, program_data=missing)), "unreadable")


# --- Trust --------------------------------------------------------------------------------------------------

def test_a_policy_file_a_person_can_write_is_refused(tmp_path: Path) -> None:
    write_policy(_linux_file(tmp_path), ASK)
    named = write_policy(tmp_path / "server" / "policy.json", ASK)
    windows = write_policy(windows_policy_file(tmp_path), ASK)

    linux = load_policy(fake_system(tmp_path, trust=trust_problem))
    env = load_policy(fake_system(tmp_path / "none", trust=trust_problem,
                                  environ={sources.POLICY_FILE_ENV: str(named)}))

    for policy in (linux, env):
        _lockdown(policy, "untrusted_location")
    assert trust_problem(windows, tmp_path / "ProgramData")


def test_a_signature_file_a_person_can_write_is_refused(tmp_path: Path) -> None:
    path = write_policy(_linux_file(tmp_path), ASK)
    signer = Signer()
    pin(path, signer)
    signature = signer.sign(path)

    policy = load_policy(fake_system(tmp_path, trust=lambda p, b: "writable" if p == signature else None))

    _lockdown(policy, "untrusted_location")


def _stat(mode: int, uid: int = 0) -> os.stat_result:
    return os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))


def _posix_case(tmp_path: Path, **overrides: os.stat_result) -> str | None:
    file = tmp_path / "etc" / "row-bot" / "org-policy.json"
    named = {str(file): _stat(stat.S_IFREG | 0o644), **{str(tmp_path / key): value for key, value in overrides.items()}}

    def lstat(path: Path) -> os.stat_result:
        return named.get(str(path), _stat(stat.S_IFDIR | 0o755))

    return posix_problem(file, lstat=lstat, realpath=lambda path: str(path))


def test_posix_trusts_only_root_owned_files_no_one_else_can_change(tmp_path: Path) -> None:
    assert _posix_case(tmp_path) is None
    assert "not root" in _posix_case(tmp_path, **{"etc/row-bot/org-policy.json": _stat(stat.S_IFREG | 0o644, 501)})
    assert "other than root" in _posix_case(tmp_path, **{"etc/row-bot/org-policy.json": _stat(stat.S_IFREG | 0o664)})
    assert "other than root" in _posix_case(tmp_path, **{"etc/row-bot": _stat(stat.S_IFDIR | 0o777)})
    assert "not root" in _posix_case(tmp_path, **{"etc": _stat(stat.S_IFDIR | 0o755, 501)})
    assert "regular file" in _posix_case(tmp_path, **{"etc/row-bot/org-policy.json": _stat(stat.S_IFIFO | 0o644)})


def test_posix_follows_a_link_and_checks_where_it_leads(tmp_path: Path) -> None:
    link = tmp_path / "etc" / "row-bot" / "org-policy.json"
    target = tmp_path / "home" / "person" / "policy.json"
    named = {str(link): _stat(stat.S_IFLNK | 0o777), str(target): _stat(stat.S_IFREG | 0o644, 501)}

    problem = posix_problem(link, lstat=lambda path: named.get(str(path), _stat(stat.S_IFDIR | 0o755)),
                            realpath=lambda path: str(target))

    assert problem and "not root" in problem


SYSTEM, ADMINS, USERS, PERSON = "S-1-5-18", "S-1-5-32-544", "S-1-5-32-545", "S-1-5-21-1-2-3-1001"
READ, WRITE, ADD_FILE, DELETE_CHILD, FULL = 0x120089, 0x2, 0x2, 0x40, 0x1F01FF


def test_windows_trusts_files_only_administrators_can_change() -> None:
    admin_only = [(0, 0, FULL, SYSTEM), (0, 0, FULL, ADMINS), (0, 0, READ, USERS)]

    assert windows_acl_problem(ADMINS, admin_only, folder=False) is None
    assert windows_acl_problem(SYSTEM, admin_only, folder=False) is None
    assert "not an administrator" in windows_acl_problem(PERSON, admin_only, folder=False)
    assert PERSON in windows_acl_problem(ADMINS, [*admin_only, (0, 0, WRITE, PERSON)], folder=False)
    assert USERS in windows_acl_problem(ADMINS, [*admin_only, (0, 0, FULL, USERS)], folder=False)
    assert "everyone" in windows_acl_problem(ADMINS, None, folder=False)
    assert "can't check" in windows_acl_problem(ADMINS, [*admin_only, (9, 0, 0, "")], folder=False)


def test_windows_allows_what_program_data_grants_users_but_not_replacing_files() -> None:
    inherited = [(0, 0, FULL, SYSTEM), (0, 0, FULL, ADMINS), (0, 0, READ, USERS),
                 (0, 0, ADD_FILE | 0x4, USERS),  # ProgramData's (CI)(WD,AD) grant: add files and folders
                 (0, 0x08 | 0x01 | 0x02, FULL, USERS)]  # inherit-only: applies to children, not here

    assert windows_acl_problem(ADMINS, inherited, folder=True) is None
    assert USERS in windows_acl_problem(ADMINS, [*inherited, (0, 0, DELETE_CHILD, USERS)], folder=True)
    # On a file, the same grant would let Users change it.
    assert USERS in windows_acl_problem(ADMINS, inherited, folder=False)
    assert windows_acl_problem(ADMINS, [(0, 0, FULL, ADMINS), (1, 0, FULL, USERS)], folder=False) is None


@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows security of a real file")
def test_windows_refuses_a_file_this_person_created(tmp_path: Path) -> None:
    path = write_policy(tmp_path / "Row-Bot" / "policy" / "org-policy.json", ASK)

    assert trust_problem(path, tmp_path)


# --- Policies that can't be used ----------------------------------------------------------------------------

@pytest.mark.parametrize(("data", "reason"), [
    (b"", "invalid"),
    (b"{not json", "invalid"),
    (b"\xff\xfe{}", "invalid"),
    (b"[]", "invalid"),
    (b'{"schema_version": 1, "telemetry": {}}', "invalid"),
    (b'{"schema_version": 1, "models": {"allow_everything": true}}', "invalid"),
    (b'{"schema_version": 1, "updates": {"deferral_days": NaN}}', "invalid"),
    (b'{"schema_version": 1, "approvals": {"max_level": "full"}, "approvals": {"max_level": "auto"}}', "invalid"),
    (b'{"schema_version": 2}', "unsupported_schema_version"),
    (b'{"schema_version": 1, "padding": "' + b"x" * (300 * 1024) + b'"}', "invalid"),
], ids=["empty", "not json", "not utf-8", "not an object", "unknown section", "unknown setting", "NaN",
        "duplicate key", "later version", "too large"])
def test_a_policy_that_cant_be_used_locks_down(tmp_path: Path, data: bytes, reason: str) -> None:
    write_bytes(_linux_file(tmp_path), data)

    policy = _lockdown(load_policy(fake_system(tmp_path)), reason)

    assert policy.source.kind == "file" and policy.detail
    assert policy.limit_approval_level("auto") == "block" and not policy.allows_tool("shell")


def test_a_utf8_bom_is_accepted(tmp_path: Path) -> None:
    write_bytes(_linux_file(tmp_path), b'\xef\xbb\xbf{"schema_version": 1}')

    assert isinstance(load_policy(fake_system(tmp_path)), OrgPolicy)


@pytest.mark.parametrize("registry", [
    RegistryKey({"schema_version": (REG_DWORD, 1), "telemetry": (REG_DWORD, 1)}),
    RegistryKey({"schema_version": (REG_SZ, "1")}),
    RegistryKey({"schema_version": (REG_DWORD, 1)}, {"models": RegistryKey({"local_models": (REG_SZ, "true")})}),
    RegistryKey({"schema_version": (REG_DWORD, 1)}, {"models": RegistryKey({"local_models": (REG_DWORD, 2)})}),
    RegistryKey({"schema_version": (REG_DWORD, 1)}, {"approvals": RegistryKey({"max_level": (REG_DWORD, 3)})}),
    RegistryKey({"schema_version": (REG_DWORD, 1)}, {"tools": RegistryKey({"disabled": (REG_SZ, "shell")})}),
    RegistryKey({"schema_version": (REG_DWORD, 1)},
                {"tools": RegistryKey({}, {"disabled": RegistryKey({"1": (REG_DWORD, 1)})})}),
    RegistryKey({"schema_version": (REG_DWORD, 1)}, {"Telemetry": RegistryKey({"on": (REG_DWORD, 1)})}),
    RegistryKey({}, {"approvals": RegistryKey({"max_level": (REG_SZ, "auto")})}),
])
def test_registry_values_the_admx_would_not_write_lock_down(tmp_path: Path, registry: RegistryKey) -> None:
    _lockdown(load_policy(fake_system(tmp_path, "windows", registry=registry)), "invalid")


def test_an_empty_list_key_in_the_registry_is_not_set(tmp_path: Path) -> None:
    registry = RegistryKey({"schema_version": (REG_DWORD, 1)},
                           {"models": RegistryKey({}, {"allowed_providers": RegistryKey()})})

    policy = load_policy(fake_system(tmp_path, "windows", registry=registry))

    assert isinstance(policy, OrgPolicy) and policy.settings.models.allowed_providers is None


@pytest.mark.parametrize("empty_key", [False, True])
def test_a_list_marked_as_set_with_no_entries_is_an_empty_list(tmp_path: Path, empty_key: bool) -> None:
    subkeys = {"allowed": RegistryKey()} if empty_key else {}
    registry = RegistryKey({"schema_version": (REG_DWORD, 1)},
                           {"channels": RegistryKey({"allowed_set": (REG_DWORD, 1)}, subkeys)})

    policy = load_policy(fake_system(tmp_path, "windows", registry=registry))

    assert isinstance(policy, OrgPolicy) and policy.settings.channels.allowed == frozenset()
    assert not policy.allows_channel("slack")


def test_a_list_marked_as_set_keeps_its_entries(tmp_path: Path) -> None:
    registry = RegistryKey({"schema_version": (REG_DWORD, 1)}, {"tools": RegistryKey(
        {"disabled_set": (REG_DWORD, 1)}, {"disabled": RegistryKey({"1": (REG_SZ, "shell")})})})

    policy = load_policy(fake_system(tmp_path, "windows", registry=registry))

    assert isinstance(policy, OrgPolicy) and policy.settings.tools.disabled == frozenset({"shell"})


@pytest.mark.parametrize("values", [{"allowed_set": (REG_DWORD, 0)}, {"allowed_set": (REG_SZ, "1")}])
def test_a_bad_list_marker_locks_down(tmp_path: Path, values: dict) -> None:
    registry = RegistryKey({"schema_version": (REG_DWORD, 1)}, {"channels": RegistryKey(values)})

    _lockdown(load_policy(fake_system(tmp_path, "windows", registry=registry)), "invalid")


def test_a_marker_for_a_setting_that_is_not_a_list_locks_down(tmp_path: Path) -> None:
    registry = RegistryKey({"schema_version": (REG_DWORD, 1)},
                           {"models": RegistryKey({"local_models_set": (REG_DWORD, 1)})})

    _lockdown(load_policy(fake_system(tmp_path, "windows", registry=registry)), "invalid")


@pytest.mark.parametrize("platform", ["windows", "macos"])
def test_a_registry_or_profile_policy_that_requires_a_signature_locks_down(tmp_path: Path, platform: str) -> None:
    document = {"schema_version": 1, "require_signature": True}
    if platform == "macos":
        write_bytes(tmp_path / "Managed Preferences" / "ai.row-bot.plist", plistlib.dumps(document))

    policy = load_policy(fake_system(tmp_path, platform, registry=registry_from(document)))

    _lockdown(policy, "signature_missing")


def test_a_property_list_that_cant_be_read_locks_down(tmp_path: Path) -> None:
    write_bytes(tmp_path / "Managed Preferences" / "ai.row-bot.plist", b"<plist><dict><key>")

    _lockdown(load_policy(fake_system(tmp_path, "macos")), "invalid")


# --- Signatures ---------------------------------------------------------------------------------------------

def test_a_good_signature_is_accepted_and_names_its_signer(tmp_path: Path) -> None:
    path = write_policy(_linux_file(tmp_path), {**ASK, "require_signature": True})
    old, current = Signer("it-2026"), Signer("it-2027")
    pin(path, old, current)
    current.sign(path)

    policy = load_policy(fake_system(tmp_path))

    assert isinstance(policy, OrgPolicy) and policy.signer == "it-2027"
    assert policy.limit_approval_level("auto") == "approve"


def test_a_signed_policy_on_windows_is_accepted(tmp_path: Path) -> None:
    path = write_policy(windows_policy_file(tmp_path), ASK)
    signer = Signer()
    pin(path, signer)
    signer.sign(path)

    policy = load_policy(fake_system(tmp_path, "windows"))

    assert isinstance(policy, OrgPolicy) and policy.signer == signer.key_id


def test_a_policy_changed_after_signing_locks_down(tmp_path: Path) -> None:
    path = write_policy(_linux_file(tmp_path), ASK)
    signer = Signer()
    pin(path, signer)
    signer.sign(path)
    write_policy(path, {"schema_version": 1, "approvals": {"max_level": "full"}})

    _lockdown(load_policy(fake_system(tmp_path)), "signature_invalid")


def test_a_signature_by_a_key_that_is_not_pinned_locks_down(tmp_path: Path) -> None:
    path = write_policy(_linux_file(tmp_path), ASK)
    pin(path, Signer("it-2027"))
    Signer("someone-else").sign(path)

    _lockdown(load_policy(fake_system(tmp_path)), "signature_invalid")


@pytest.mark.parametrize("signature", [b"", b"not base64!", b"c2hvcnQ=", b"A" * 88])
def test_a_signature_that_is_not_one_locks_down(tmp_path: Path, signature: bytes) -> None:
    path = write_policy(_linux_file(tmp_path), ASK)
    pin(path, Signer())
    write_bytes(path.with_name(path.name + ".sig"), signature)

    _lockdown(load_policy(fake_system(tmp_path)), "signature_invalid")


def test_a_missing_signature_locks_down_when_the_policy_requires_one(tmp_path: Path) -> None:
    path = write_policy(_linux_file(tmp_path), {**ASK, "require_signature": True})

    _lockdown(load_policy(fake_system(tmp_path)), "signature_missing")
    pin(path, Signer())
    _lockdown(load_policy(fake_system(tmp_path)), "signature_missing")


def test_a_missing_signature_locks_down_while_keys_are_pinned(tmp_path: Path) -> None:
    path = write_policy(_linux_file(tmp_path), ASK)
    pin(path, Signer())

    _lockdown(load_policy(fake_system(tmp_path)), "signature_missing")


def test_a_signature_without_pinned_keys_locks_down(tmp_path: Path) -> None:
    path = write_policy(_linux_file(tmp_path), ASK)
    Signer().sign(path)

    _lockdown(load_policy(fake_system(tmp_path)), "signature_key_missing")


@pytest.mark.parametrize("keys", [
    b"{",
    b'{"schema_version": 1, "keys": []}',
    b'{"schema_version": 2, "keys": [{"id": "a", "ed25519": "' + b"A" * 43 + b'="}]}',
    b'{"schema_version": 1, "keys": [{"id": "a", "ed25519": "short"}]}',
    b'{"schema_version": 1, "keys": [{"id": "a", "ed25519": "' + b"A" * 43 + b'=", "rsa": "x"}]}',
])
def test_a_keys_file_that_cant_be_used_locks_down(tmp_path: Path, keys: bytes) -> None:
    path = write_policy(_linux_file(tmp_path), ASK)
    Signer().sign(path)
    write_bytes(path.parent / sources.KEYS_FILE, keys)

    _lockdown(load_policy(fake_system(tmp_path)), "keys_invalid")


# --- The real registry reader -------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="the Windows registry")
def test_the_registry_reader_reads_values_and_list_keys() -> None:
    import winreg

    path = rf"Software\Row-Bot-Tests\policy-{uuid.uuid4().hex}"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path) as root:
            winreg.SetValueEx(root, "schema_version", 0, winreg.REG_DWORD, 1)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path + r"\approvals") as key:
            winreg.SetValueEx(key, "max_level", 0, winreg.REG_SZ, "approve")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path + r"\tools\disabled") as key:
            winreg.SetValueEx(key, "1", 0, winreg.REG_SZ, "shell")

        tree = read_registry(path, winreg.HKEY_CURRENT_USER)
        policy = load_policy(fake_system(Path("unused"), "windows", registry=tree))

        assert read_registry(path + r"\absent", winreg.HKEY_CURRENT_USER) is None
        assert isinstance(policy, OrgPolicy)
        assert policy.limit_approval_level("full") == "approve" and not policy.allows_tool("shell")
    finally:
        for child in (r"\tools\disabled", r"\tools", r"\approvals", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path + child)
            except FileNotFoundError:
                pass
