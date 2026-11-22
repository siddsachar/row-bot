"""The organisation policy's published contract: the JSON Schema accepts exactly what the loader accepts, a
policy written through each admin template loads back to the same settings, the templates can't drift, and the
documented defaults hold: no policy changes nothing, a policy's missing settings lean safe, and lockdown refuses
everything."""

from __future__ import annotations

import importlib.util
import json
import plistlib
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from row_bot.approval_policy import APPROVAL_MODES
from row_bot.policy import LOCKDOWN, Lockdown, NoPolicy, OrgPolicy, PolicySource
from row_bot.policy.schema import InvalidPolicy, parse
from row_bot.policy.sources import REG_DWORD, REG_SZ, RegistryKey, load_policy
from row_bot.providers.catalog import PROVIDER_BILLING
from tests.helpers.policy_fakes import fake_system, windows_policy_file, write_bytes, write_policy

pytestmark = [pytest.mark.contract, pytest.mark.platform]

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "contracts" / "policy" / "v1"
ADMX = "{http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions}"


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location("policy_templates", ROOT / "scripts" / "policy" /
                                                  "generate_templates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _example() -> dict[str, Any]:
    return json.loads((CONTRACT / "org-policy.example.json").read_text(encoding="utf-8"))


def _accepted_by_loader(document: object) -> bool:
    try:
        parse(document)
    except InvalidPolicy:
        return False
    return True


def _with(section: str, values: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, section: values}


DOCUMENTS = {
    "the example": _example(),
    "only the version": {"schema_version": 1},
    "a later version": {"schema_version": 2},
    "no version": {"approvals": {"max_level": "auto"}},
    "a version as text": {"schema_version": "1"},
    "an unknown section": {"schema_version": 1, "telemetry": {}},
    "an unknown setting": _with("models", {"allow_everything": True}),
    "a number for a switch": _with("models", {"local_models": 1}),
    "an unknown level": _with("approvals", {"max_level": "anything"}),
    "a bad provider id": _with("models", {"allowed_providers": ["Open AI"]}),
    "a model without its provider": _with("models", {"allowed_models": ["gpt-5"]}),
    "a list of a list": _with("tools", {"disabled": [["shell"]]}),
    "plain http to another host": _with("organisation", {"server_url": "http://org.example.com"}),
    "a password in the URL": _with("organisation", {"server_url": "https://me:secret@org.example.com"}),
    "http to this computer": _with("models", {"gateway_url": "http://127.0.0.1:8080/v1"}),
    "a gateway API without a gateway": _with("models", {"gateway_shape": "openai_chat"}),
    "allowed plugins without an allow-list": _with("extensions", {"plugins": "any", "allowed_plugins": ["a"]}),
    "allowed plugins with an allow-list": _with("extensions", {"plugins": "allow-list", "allowed_plugins": ["a"]}),
    "too many deferral days": _with("updates", {"deferral_days": 366}),
    "a control character": _with("organisation", {"display_name": "Example\nCo"}),
    "a bad version pin": _with("updates", {"pinned_version": "latest"}),
}


@pytest.mark.parametrize("name", DOCUMENTS)
def test_the_published_schema_accepts_exactly_what_the_loader_accepts(name: str) -> None:
    schema = json.loads((CONTRACT / "org-policy.schema.json").read_text(encoding="utf-8"))
    published = jsonschema.Draft202012Validator(schema).is_valid(DOCUMENTS[name])

    assert published == _accepted_by_loader(DOCUMENTS[name])


def test_the_published_schema_documents_every_default_and_lockdown_value() -> None:
    schema = json.loads((CONTRACT / "org-policy.schema.json").read_text(encoding="utf-8"))

    for section, body in schema["properties"].items():
        for name, setting in body.get("properties", {}).items():
            assert "Not set in a policy:" in setting["description"] and "Lockdown:" in setting["description"]
            assert f"`{section}.{name}`" in schema["description"]


def test_the_example_policy_sets_every_setting_and_loads(tmp_path: Path) -> None:
    example = _example()
    schema = json.loads((CONTRACT / "org-policy.schema.json").read_text(encoding="utf-8"))
    every = {(section, name) for section, body in schema["properties"].items() for name in body.get("properties", {})}
    write_policy(tmp_path / "etc" / "row-bot" / "org-policy.json", example)

    policy = load_policy(fake_system(tmp_path))

    assert {(section, name) for section, values in example.items() if isinstance(values, dict)
            for name in values} == every
    assert isinstance(policy, OrgPolicy) and policy.settings == parse(example)[0]


# --- Templates ----------------------------------------------------------------------------------------------

def _apply_admx(document: dict[str, Any]) -> RegistryKey:
    """What Group Policy writes to the registry when each ADMX policy is set to *document*'s value."""
    root = ET.parse(CONTRACT / "windows" / "row-bot.admx").getroot()
    written: dict[str, dict[str, tuple[int, object]]] = {}

    def write(key: str, name: str, value: tuple[int, object]) -> None:
        written.setdefault(key, {})[name] = value

    def setting(key: str, name: str) -> object:
        return document.get(key.split("\\")[-1], {}).get(name)

    def element_value(policy_key: str, element: ET.Element) -> object:
        if element.tag == f"{ADMX}list":
            section_key, name = element.get("key").rsplit("\\", 1)
            return setting(section_key, name)
        return setting(policy_key, element.get("valueName"))

    for policy in root.iter(f"{ADMX}policy"):
        key = policy.get("key")
        if policy.get("valueName"):
            value = setting(key, policy.get("valueName"))
            if value is None:
                continue  # not configured
            state = policy.find(f"{ADMX}enabledValue" if value else f"{ADMX}disabledValue")
            write(key, policy.get("valueName"), (REG_DWORD, int(state.find(f"{ADMX}decimal").get("value"))))
            listed = policy.find(f"{ADMX}enabledList" if value else f"{ADMX}disabledList")
        else:
            elements = list(policy.find(f"{ADMX}elements"))
            if element_value(key, elements[0]) is None:
                continue  # not configured
            for element in elements:
                tag = element.tag.removeprefix(ADMX)
                value = element_value(key, element)
                if value is None:
                    continue
                if tag == "list":
                    for index, entry in enumerate(value, 1):
                        write(element.get("key"), f"{element.get('valuePrefix')}{index}", (REG_SZ, entry))
                    continue
                if tag == "enum":
                    choices = [item.find(f"{ADMX}value/{ADMX}string").text for item in element.iter(f"{ADMX}item")]
                    assert value in choices
                    write(key, element.get("valueName"), (REG_SZ, value))
                elif tag == "decimal":
                    write(key, element.get("valueName"), (REG_DWORD, value))
                else:
                    write(key, element.get("valueName"), (REG_SZ, value))
            listed = policy.find(f"{ADMX}enabledList")
        for item in listed.iter(f"{ADMX}item"):
            write(item.get("key"), item.get("valueName"),
                  (REG_DWORD, int(item.find(f"{ADMX}value/{ADMX}decimal").get("value"))))

    def tree(path: str) -> RegistryKey:
        children = {name[len(path) + 1:].split("\\")[0] for name in written if name.startswith(path + "\\")}
        return RegistryKey(written.get(path, {}), {child: tree(f"{path}\\{child}") for child in children})

    return tree(r"SOFTWARE\Policies\Row-Bot")


def test_a_policy_set_through_the_admx_loads_back_to_the_example(tmp_path: Path) -> None:
    example = _example()

    policy = load_policy(fake_system(tmp_path, "windows", registry=_apply_admx(example)))

    assert isinstance(policy, OrgPolicy)
    assert policy.source.kind == "registry"
    assert policy.settings == parse(example)[0]


def test_every_admx_string_and_presentation_is_defined_in_the_adml() -> None:
    admx = (CONTRACT / "windows" / "row-bot.admx").read_text(encoding="utf-8")
    adml = ET.parse(CONTRACT / "windows" / "en-US" / "row-bot.adml").getroot()
    strings = {item.get("id") for item in adml.iter(f"{ADMX}string")}
    presentations = {item.get("id") for item in adml.iter(f"{ADMX}presentation")}

    assert set(re.findall(r"\$\(string\.([^)]+)\)", admx)) <= strings
    assert set(re.findall(r"\$\(presentation\.([^)]+)\)", admx)) == presentations


def test_a_profile_deployed_from_the_mobileconfig_loads_back_to_the_example(tmp_path: Path) -> None:
    profile = plistlib.loads((CONTRACT / "macos" / "ai.row-bot.mobileconfig").read_bytes())
    (payload,) = profile["PayloadContent"]
    # macOS keeps the payload's preference keys, not its Payload* metadata, in the managed domain.
    managed = {key: value for key, value in payload.items() if not key.startswith("Payload")}
    write_bytes(tmp_path / "Managed Preferences" / "ai.row-bot.plist", plistlib.dumps(managed, fmt=plistlib.FMT_BINARY))

    policy = load_policy(fake_system(tmp_path, "macos"))

    assert payload["PayloadType"] == "ai.row-bot"
    assert isinstance(policy, OrgPolicy) and policy.source.kind == "managed_preferences"
    assert policy.settings == parse(_example())[0]


def test_the_jamf_schema_describes_the_profile_settings() -> None:
    jamf = json.loads((CONTRACT / "macos" / "ai.row-bot.jamf-schema.json").read_text(encoding="utf-8"))
    profile = {key: value for key, value in _example().items() if key != "require_signature"}

    jsonschema.Draft202012Validator(jamf).validate(profile)
    assert "require_signature" not in jamf["properties"]


def test_check_reports_every_template_that_drifted(tmp_path: Path) -> None:
    generator = _generator()
    copy = tmp_path / "v1"
    shutil.copytree(CONTRACT, copy)
    assert generator.drift(copy) == []

    admx = copy / "windows" / "row-bot.admx"
    admx.write_text(admx.read_text(encoding="utf-8").replace('maxValue="365"', 'maxValue="999"'), encoding="utf-8")
    (copy / "org-policy.example.json").unlink()
    (copy / "windows" / "old.admx").write_text("<policyDefinitions/>", encoding="utf-8")

    assert generator.drift(copy) == ["org-policy.example.json", "windows/old.admx", "windows/row-bot.admx"]


def test_check_ignores_windows_line_ends(tmp_path: Path) -> None:
    generator = _generator()
    copy = tmp_path / "v1"
    shutil.copytree(CONTRACT, copy)
    adml = copy / "windows" / "en-US" / "row-bot.adml"
    adml.write_bytes(adml.read_bytes().replace(b"\n", b"\r\n"))

    assert generator.drift(copy) == []


# --- Documented defaults ------------------------------------------------------------------------------------

SUBSCRIPTION = sorted(provider for provider, billing in PROVIDER_BILLING.items() if billing == "subscription")
PAY_PER_USE = sorted(provider for provider, billing in PROVIDER_BILLING.items() if billing == "pay_per_use")
CHANNELS = ("discord", "slack", "sms", "telegram", "whatsapp")


def test_no_policy_changes_nothing() -> None:
    policy = NoPolicy()

    assert [policy.limit_approval_level(level) for level in APPROVAL_MODES] == list(APPROVAL_MODES)
    assert all(policy.allows_provider(provider) for provider in PROVIDER_BILLING)
    assert policy.allows_model("model:codex:gpt-5") and policy.allows_model("anything")
    assert policy.allows_personal_api_keys() and policy.allows_local_models()
    assert policy.allows_tool("shell") and policy.allows_files_outside_workspace()
    assert all(policy.allows_channel(channel) for channel in CHANNELS)
    assert all(policy.allows_extension(kind, "x") for kind in ("plugins", "mcp_servers", "skills"))
    assert all(policy.allows_feature(feature) for feature in
               ("learning", "proactive", "remote_access", "data_export", "install_count"))


def test_a_policy_that_sets_nothing_turns_off_only_the_known_risks(tmp_path: Path) -> None:
    write_policy(tmp_path / "etc" / "row-bot" / "org-policy.json", {"schema_version": 1})

    policy = load_policy(fake_system(tmp_path))

    assert isinstance(policy, OrgPolicy)
    assert policy.limit_approval_level("full") == "auto"
    assert policy.limit_approval_level("approve") == "approve"
    assert SUBSCRIPTION and not any(policy.allows_provider(provider) for provider in SUBSCRIPTION)
    assert all(policy.allows_provider(provider) for provider in PAY_PER_USE)
    assert not policy.allows_channel("whatsapp")
    assert all(policy.allows_channel(channel) for channel in CHANNELS if channel != "whatsapp")
    assert not policy.allows_feature("install_count")
    assert not policy.allows_feature("learning") and not policy.allows_feature("remote_access")
    assert policy.allows_feature("proactive") and policy.allows_feature("data_export")
    assert policy.allows_tool("shell") and policy.allows_extension("mcp_servers", "github")
    assert policy.settings.updates.channel is None and not policy.settings.updates.external


def test_lockdown_refuses_everything() -> None:
    policy = Lockdown(reason="invalid", detail="test", source=PolicySource("file", "org-policy.json"))

    assert policy.settings == LOCKDOWN
    assert {policy.limit_approval_level(level) for level in APPROVAL_MODES} == {"block"}
    assert not any(policy.allows_provider(provider) for provider in PROVIDER_BILLING)
    assert not policy.allows_model("model:anthropic:claude-sonnet-4-5")
    assert not policy.allows_personal_api_keys() and not policy.allows_local_models()
    assert not policy.allows_tool("calculator") and not policy.allows_files_outside_workspace()
    assert not any(policy.allows_channel(channel) for channel in CHANNELS)
    assert not any(policy.allows_extension(kind, "x") for kind in ("plugins", "mcp_servers", "skills"))
    assert not any(policy.allows_feature(feature) for feature in
                   ("learning", "proactive", "remote_access", "data_export", "install_count"))
    assert policy.settings.updates.external and policy.settings.line.regulated


def test_an_allow_list_names_exactly_what_may_be_used(tmp_path: Path) -> None:
    write_policy(windows_policy_file(tmp_path), {
        "schema_version": 1,
        "models": {"allowed_providers": ["anthropic", "codex"], "allowed_models": ["model:anthropic:claude-x"],
                   "subscription_sign_ins": True},
        "tools": {"disabled": ["shell"]},
        "channels": {"allowed": ["slack", "whatsapp"]},
        "extensions": {"skills": "allow-list", "allowed_skills": ["style-guide"], "plugins": "none"},
    })

    policy = load_policy(fake_system(tmp_path, "windows"))

    assert policy.allows_provider("anthropic") and policy.allows_provider("codex")
    assert not policy.allows_provider("openai")
    assert policy.allows_model("model:anthropic:claude-x")
    assert not policy.allows_model("model:anthropic:claude-y") and not policy.allows_model("model:openai:gpt-5")
    assert not policy.allows_model("claude-x")
    assert not policy.allows_tool("shell") and policy.allows_tool("browser")
    assert policy.allows_channel("whatsapp") and not policy.allows_channel("telegram")
    assert policy.allows_extension("skills", "style-guide") and not policy.allows_extension("skills", "other")
    assert not policy.allows_extension("plugins", "anything")
    assert policy.allows_extension("mcp_servers", "anything")
