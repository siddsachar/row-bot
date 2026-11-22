"""Write the organisation policy's JSON Schema and admin templates to contracts/policy/v1/, all generated from
row_bot.policy.schema so they never drift from what Row-Bot accepts:

- org-policy.schema.json and org-policy-keys.schema.json: the JSON Schemas;
- org-policy.example.json: an example policy that sets every setting;
- windows/row-bot.admx and windows/en-US/row-bot.adml: Group Policy and Intune (ADMX ingestion) templates;
- macos/ai.row-bot.mobileconfig: an example configuration profile carrying the example policy;
- macos/ai.row-bot.jamf-schema.json: a Jamf Pro custom settings schema.

--check writes nothing and fails when a file is missing, differs or is no longer generated.
"""

from __future__ import annotations

import argparse
import copy
import json
import plistlib
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from row_bot.policy.schema import (  # noqa: E402 -- load checkout source after path bootstrap
    SCHEMA_VERSION, SECTIONS, Spec, json_schema, keys_json_schema, property_schema, specs)
from row_bot.policy.sources import MAC_DOMAIN, REGISTRY_KEY  # noqa: E402

DESTINATION = ROOT / "contracts" / "policy" / "v1"
ADMX_NAMESPACE = "http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions"
SUPPORTED = "SUPPORTED_ROW_BOT_POLICY_1"
# Fixed, so the profile's UUIDs are the same on every run.
PROFILE_NAMESPACE = uuid.UUID("8f4f1f53-3a0c-4c55-9a51-5b1d2a9c0e71")

EXAMPLE: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "require_signature": False,
    "organisation": {
        "id": "example-co",
        "display_name": "Example Co",
        "server_url": "https://row-bot.example.com",
        "policy_version": "2027-01",
        "contact": "IT help desk, it-help@example.com",
    },
    "approvals": {"max_level": "auto"},
    "models": {
        "allowed_providers": ["anthropic", "custom_openai_example-gateway"],
        "allowed_models": ["model:anthropic:claude-sonnet-4-5", "model:custom_openai_example-gateway:gpt-5"],
        "gateway_url": "https://ai-gateway.example.com/v1",
        "gateway_shape": "openai_chat",
        "personal_api_keys": False,
        "subscription_sign_ins": False,
        "local_models": False,
    },
    "tools": {"disabled": ["computer_use", "shell"], "files_outside_workspace": False},
    "channels": {"allowed": ["slack"]},
    "extensions": {
        "plugins": "allow-list",
        "allowed_plugins": ["example-co-tools"],
        "mcp_servers": "allow-list",
        "allowed_mcp_servers": ["github"],
        "skills": "allow-list",
        "allowed_skills": ["example-co-style-guide"],
    },
    "features": {"learning": False, "proactive": True, "remote_access": False, "data_export": True,
                 "install_count": False},
    "updates": {"channel": "stable", "deferral_days": 7, "pinned_version": "5.4.0", "external": False},
    "line": {"regulated": False},
    "audit": {"sink": "local", "redaction": "strict"},
}


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=False) + "\n"


# --- Windows ADMX / ADML ------------------------------------------------------------------------------------

def _policies() -> list[tuple[str, str, Spec, list[tuple[str, Spec]]]]:
    """(section, setting, spec, joined settings): one Group Policy per setting, with the settings that need it
    (an allow-list beside its mode, the gateway's API beside its URL) as further elements of the same policy."""
    result = []
    for section in SECTIONS:
        section_specs = specs(section)
        for name, spec in section_specs.items():
            if spec.requires:
                continue
            joined = [(other, other_spec) for other, other_spec in section_specs.items() if other_spec.requires == name]
            result.append((section, name, spec, joined))
    return result


def _xml(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _element(parent: ET.Element, policy: str, section: str, name: str, spec: Spec) -> None:
    key = f"{REGISTRY_KEY}\\{section}"
    if spec.kind == "enum":
        element = ET.SubElement(parent, "enum", id=name, valueName=name, required="true")
        for choice in spec.choices:
            item = ET.SubElement(element, "item", displayName=f"$(string.{policy}_{name}_{_id(choice)})")
            ET.SubElement(ET.SubElement(item, "value"), "string").text = choice
    elif spec.kind == "list":
        ET.SubElement(parent, "list", id=name, key=f"{key}\\{name}", valuePrefix="")
    elif spec.kind == "int":
        ET.SubElement(parent, "decimal", id=name, valueName=name, required="true",
                      minValue=str(spec.minimum), maxValue=str(spec.maximum))
    else:
        ET.SubElement(parent, "text", id=name, valueName=name, required="true", maxLength=str(spec.max_length))


def _id(text: str) -> str:
    return text.replace("-", "_")


def admx() -> str:
    root = ET.Element("policyDefinitions", {"xmlns": ADMX_NAMESPACE, "revision": "1.0", "schemaVersion": "1.0"})
    namespaces = ET.SubElement(root, "policyNamespaces")
    ET.SubElement(namespaces, "target", prefix="rowbot", namespace="RowBot.Policies.RowBot")
    ET.SubElement(root, "resources", minRequiredRevision="1.0")
    supported = ET.SubElement(ET.SubElement(root, "supportedOn"), "definitions")
    ET.SubElement(supported, "definition", name=SUPPORTED, displayName=f"$(string.{SUPPORTED})")
    categories = ET.SubElement(root, "categories")
    ET.SubElement(categories, "category", name="RowBot", displayName="$(string.RowBot)")
    for section in SECTIONS:
        category = ET.SubElement(categories, "category", name=f"RowBot_{section}",
                                 displayName=f"$(string.RowBot_{section})")
        ET.SubElement(category, "parentCategory", ref="RowBot")
    policies = ET.SubElement(root, "policies")
    for section, name, spec, joined in _policies():
        policy = f"{section}_{name}"
        attributes = {"name": policy, "class": "Machine", "displayName": f"$(string.{policy})",
                      "explainText": f"$(string.{policy}_Explain)", "key": f"{REGISTRY_KEY}\\{section}"}
        if spec.kind == "bool":
            attributes["valueName"] = name
        else:
            attributes["presentation"] = f"$(presentation.{policy})"
        element = ET.SubElement(policies, "policy", attributes)
        ET.SubElement(element, "parentCategory", ref=f"RowBot_{section}")
        ET.SubElement(element, "supportedOn", ref=SUPPORTED)
        if spec.kind == "bool":
            ET.SubElement(ET.SubElement(element, "enabledValue"), "decimal", value="1")
            ET.SubElement(ET.SubElement(element, "disabledValue"), "decimal", value="0")
        # Whatever the admin sets, the policy says which schema it speaks.
        for state in ("enabledList", "disabledList") if spec.kind == "bool" else ("enabledList",):
            item = ET.SubElement(ET.SubElement(element, state), "item", key=REGISTRY_KEY, valueName="schema_version")
            ET.SubElement(ET.SubElement(item, "value"), "decimal", value=str(SCHEMA_VERSION))
        if spec.kind != "bool":
            elements = ET.SubElement(element, "elements")
            for setting, setting_spec in [(name, spec), *joined]:
                _element(elements, policy, section, setting, setting_spec)
    return _xml(root)


def adml() -> str:
    root = ET.Element("policyDefinitionResources",
                      {"xmlns": ADMX_NAMESPACE, "revision": "1.0", "schemaVersion": "1.0"})
    ET.SubElement(root, "displayName").text = "Row-Bot"
    ET.SubElement(root, "description").text = "Row-Bot organisation policy, schema version 1."
    resources = ET.SubElement(root, "resources")
    strings = ET.SubElement(resources, "stringTable")
    presentations = ET.SubElement(resources, "presentationTable")

    def string(identifier: str, text: str) -> None:
        ET.SubElement(strings, "string", id=identifier).text = text

    string("RowBot", "Row-Bot")
    string(SUPPORTED, "Row-Bot with organisation policy schema 1")
    for section, cls in SECTIONS.items():
        string(f"RowBot_{section}", section.replace("_", " ").capitalize())
    for section, name, spec, joined in _policies():
        policy = f"{section}_{name}"
        string(policy, spec.title)
        lines = [_explain(section, name, spec)] + [_explain(section, other, other_spec) for other, other_spec in joined]
        if spec.kind == "bool":
            lines.append("Enabled: true. Disabled: false.")
        string(f"{policy}_Explain", "\n\n".join(lines))
        if spec.kind == "bool":
            continue
        presentation = ET.SubElement(presentations, "presentation", id=policy)
        for setting, setting_spec in [(name, spec), *joined]:
            label = setting_spec.title
            if setting_spec.kind == "enum":
                ET.SubElement(presentation, "dropdownList", refId=setting, noSort="true").text = label
                for choice in setting_spec.choices:
                    string(f"{policy}_{setting}_{_id(choice)}", choice)
            elif setting_spec.kind == "list":
                ET.SubElement(presentation, "listBox", refId=setting).text = label
            elif setting_spec.kind == "int":
                ET.SubElement(presentation, "decimalTextBox", refId=setting,
                              defaultValue=str(setting_spec.minimum)).text = label
            else:
                ET.SubElement(ET.SubElement(presentation, "textBox", refId=setting), "label").text = label
    return _xml(root)


def _explain(section: str, name: str, spec: Spec) -> str:
    return f"{section}.{name}: {property_schema(section, name)['description']}"


# --- macOS --------------------------------------------------------------------------------------------------

def _profile_example() -> dict[str, Any]:
    # A profile can't be signed the way a file is, so require_signature has no place in it.
    return {key: value for key, value in EXAMPLE.items() if key != "require_signature"}


def mobileconfig() -> bytes:
    payload = {
        "PayloadDisplayName": "Row-Bot organisation policy",
        "PayloadIdentifier": f"{MAC_DOMAIN}.policy.settings",
        "PayloadType": MAC_DOMAIN,
        "PayloadUUID": str(uuid.uuid5(PROFILE_NAMESPACE, "settings")).upper(),
        "PayloadVersion": 1,
        **_profile_example(),
    }
    profile = {
        "PayloadContent": [payload],
        "PayloadDescription": "Example Row-Bot organisation policy. Edit the settings, then sign and deploy the "
                              "profile with your MDM.",
        "PayloadDisplayName": "Row-Bot organisation policy (example)",
        "PayloadIdentifier": f"{MAC_DOMAIN}.policy",
        "PayloadOrganization": "Example Co",
        "PayloadScope": "System",
        "PayloadType": "Configuration",
        "PayloadUUID": str(uuid.uuid5(PROFILE_NAMESPACE, "profile")).upper(),
        "PayloadVersion": 1,
    }
    return plistlib.dumps(profile, sort_keys=True)


def _jamf(node: Any) -> Any:
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if key in ("$schema", "additionalProperties", "dependentSchemas"):
                continue
            if key == "const":
                result["enum"] = [value]
                result["default"] = value
                continue
            result[key] = _jamf(value)
        return result
    if isinstance(node, list):
        return [_jamf(item) for item in node]
    return node


def jamf_schema() -> dict[str, Any]:
    schema = copy.deepcopy(json_schema())
    del schema["properties"]["require_signature"]
    schema = _jamf(schema)
    schema["title"] = f"Row-Bot organisation policy ({MAC_DOMAIN})"
    return schema


# --- Files --------------------------------------------------------------------------------------------------

def outputs() -> dict[str, bytes]:
    """Every generated file, by its path under contracts/policy/v1/."""
    return {
        "org-policy.schema.json": _json_text(json_schema()).encode(),
        "org-policy-keys.schema.json": _json_text(keys_json_schema()).encode(),
        "org-policy.example.json": _json_text(EXAMPLE).encode(),
        "windows/row-bot.admx": admx().encode(),
        "windows/en-US/row-bot.adml": adml().encode(),
        "macos/ai.row-bot.mobileconfig": mobileconfig(),
        "macos/ai.row-bot.jamf-schema.json": _json_text(jamf_schema()).encode(),
    }


def drift(destination: Path) -> list[str]:
    """Generated files under *destination* that are missing, differ or are no longer generated."""
    expected = outputs()
    present = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
    # A Windows checkout may turn line ends into CRLF; that is not drift.
    changed = [name for name, content in expected.items()
               if name not in present or (destination / name).read_bytes().replace(b"\r\n", b"\n") != content]
    return sorted([*changed, *(present - set(expected))])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        problems = drift(DESTINATION) if DESTINATION.is_dir() else sorted(outputs())
        if problems:
            print(f"Policy templates out of date ({', '.join(problems)}); "
                  "run python scripts/policy/generate_templates.py")
            return 1
        print("Policy templates verified")
        return 0
    for name, content in outputs().items():
        path = DESTINATION / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes().replace(b"\r\n", b"\n") != content:
            path.write_bytes(content)
    for name in drift(DESTINATION):
        (DESTINATION / name).unlink()
    print("Policy templates generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
