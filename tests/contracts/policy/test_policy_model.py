"""The shared company policy model (row_bot.policy): strict parsing, defaults, the published JSON Schemas,
lockdown, and the additive merge of group overrides (TEAMS-DESIGN Â§5)."""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from row_bot.policy import (
    DEFAULT_POLICY, MAX_DOCUMENT_BYTES, PolicyError, document_to_dict, json_schema, loads, lockdown_policy,
    parse_override, parse_policy, policy_to_dict, resolve,
)
from row_bot.policy.model import SECTIONS, specs

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]


def effective(company: dict | None = None, *overrides: dict) -> dict:
    return policy_to_dict(resolve(None if company is None else parse_policy(company),
                                  [parse_override(override) for override in overrides]))


# -- defaults and parsing -----------------------------------------------------------------------------

def test_a_policy_that_sets_nothing_takes_every_default() -> None:
    policy = resolve(parse_policy({}))

    assert policy == DEFAULT_POLICY == resolve(None)
    assert policy.version == 0
    assert policy.models.allowed == {"*"}
    assert (policy.models.personal_api_keys, policy.models.consumer_sign_ins, policy.models.local_models) == (
        False, False, True)
    assert policy.approvals.max_level == "auto"  # never Hands-off unless the company allows it
    assert policy.offline.max_days == 7
    assert (policy.data.chat_export, policy.data.share_to_company) == (True, True)
    assert policy.tools.disabled_groups == frozenset() and policy.tools.disabled_tools == frozenset()
    assert policy.apps.channels == "allow-list" and "whatsapp" not in policy.apps.allowed_channels


def test_a_policy_sets_what_it_names_and_keeps_the_rest_default() -> None:
    policy = resolve(parse_policy({
        "version": 4,
        "models": {"allowed": ["gpt-5", "claude-sonnet"], "personal_api_keys": True},
        "tools": {"disabled_groups": ["shell", "computer_use"], "disabled_tools": ["run_command"]},
        "approvals": {"max_level": "approve"},
        "apps": {"mcp_servers": "allow-list", "allowed_mcp_servers": ["github"]},
        "data": {"chat_export": False},
        "offline": {"max_days": 0},
    }))

    assert policy.version == 4
    assert policy.models.allowed == {"gpt-5", "claude-sonnet"} and policy.models.personal_api_keys
    assert policy.models.consumer_sign_ins is False and policy.models.local_models is True
    assert policy.tools.disabled_groups == {"shell", "computer_use"} and policy.tools.disabled_tools == {"run_command"}
    assert policy.approvals.max_level == "approve"
    assert (policy.apps.mcp_servers, policy.apps.allowed_mcp_servers) == ("allow-list", {"github"})
    assert policy.apps.plugins == "any"
    assert policy.data.chat_export is False and policy.data.share_to_company is True
    assert policy.offline.max_days == 0


@pytest.mark.parametrize("document", [
    [],
    {"surprise": {}},
    {"models": {"surprise": True}},
    {"models": []},
    {"models": {"personal_api_keys": "true"}},
    {"models": {"personal_api_keys": 1}},
    {"models": {"allowed": "gpt-5"}},
    {"models": {"allowed": [""]}},
    {"models": {"allowed": ["has space"]}},
    {"models": {"allowed": ["m"] * 513}},
    {"tools": {"disabled_groups": ["Shell"]}},
    {"tools": {"disabled_groups": ["*"]}},
    {"tools": {"enabled_groups": ["shell"]}},  # an override's key, not the company's
    {"approvals": {"max_level": "ask"}},
    {"approvals": {"max_level": "hands-off"}},
    {"apps": {"plugins": "some"}},
    {"apps": {"allowed_channels": [7]}},
    {"offline": {"max_days": -1}},
    {"offline": {"max_days": 366}},
    {"offline": {"max_days": True}},
    {"offline": {"max_days": 7.0}},
    {"version": -1},
    {"version": True},
    {"version": "3"},
])
def test_the_company_policy_refuses_anything_not_in_v1(document: object) -> None:
    with pytest.raises(PolicyError):
        parse_policy(document)


@pytest.mark.parametrize("document", [
    {"tools": {"disabled_groups": ["shell"]}},  # an override gives back; it never takes away
    {"tools": {"surprise": []}},
    {"approvals": {"max_level": "everything"}},
    {"groups": {}},
])
def test_a_group_override_refuses_anything_not_in_v1(document: object) -> None:
    with pytest.raises(PolicyError):
        parse_override(document)


def test_the_error_names_the_setting_at_fault() -> None:
    with pytest.raises(PolicyError) as error:
        parse_policy({"models": {"allowed": ["ok", "not ok"]}})
    assert error.value.detail == "models.allowed[1]: not a valid entry"


@pytest.mark.parametrize("text", [
    '{"models": {}, "models": {}}',
    '{"offline": {"max_days": NaN}}',
    '{"offline": {"max_days": Infinity}}',
    '{"models": ',
    b'{"models": {"allowed": ["\xff"]}}',
    '{"x": "' + "a" * MAX_DOCUMENT_BYTES + '"}',
], ids=["duplicate", "nan", "infinity", "truncated", "not-utf8", "oversize"])
def test_strict_json_refuses_duplicates_constants_bad_bytes_and_oversize(text: str | bytes) -> None:
    with pytest.raises(PolicyError):
        loads(text)


def test_a_document_is_written_back_as_set_normalised() -> None:
    document = parse_policy(loads(json.dumps({
        "offline": {"max_days": 3}, "models": {"allowed": ["b", "a", "a"], "local_models": True}, "data": {},
    })))

    assert document_to_dict(document) == {"models": {"allowed": ["a", "b"], "local_models": True},
                                          "offline": {"max_days": 3}}
    override = parse_override({"version": 2, "tools": {"enabled_tools": ["run_command"]}})
    assert document_to_dict(override) == {"version": 2, "tools": {"enabled_tools": ["run_command"]}}


def test_a_star_among_the_models_means_every_model() -> None:
    assert resolve(parse_policy({"models": {"allowed": ["gpt-5", "*"]}})).models.allowed == {"*"}


def test_the_effective_policy_reads_back_as_itself() -> None:
    policy = resolve(parse_policy({"version": 9, "tools": {"disabled_groups": ["shell"]}}),
                     [parse_override({"version": 11, "approvals": {"max_level": "full"}})])

    assert resolve(parse_policy(loads(json.dumps(policy_to_dict(policy))))) == policy
    assert resolve(parse_policy(policy_to_dict(lockdown_policy()))) == lockdown_policy()


# -- the published schemas ----------------------------------------------------------------------------

def _published(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "policy" / "v1" / f"{name}.schema.json").read_text(encoding="utf-8"))


def test_the_published_schemas_are_the_generated_ones() -> None:
    assert _published("CompanyPolicy") == json_schema()
    assert _published("GroupOverride") == json_schema(override=True)


def test_the_published_schemas_accept_what_the_parser_accepts_and_refuse_what_it_refuses() -> None:
    company, override = Draft202012Validator(_published("CompanyPolicy")), Draft202012Validator(
        _published("GroupOverride"))

    company.validate(policy_to_dict(DEFAULT_POLICY))
    company.validate(policy_to_dict(lockdown_policy()))
    override.validate({"version": 3, "tools": {"enabled_groups": ["shell"]}, "approvals": {"max_level": "full"}})
    for bad in ({"surprise": {}}, {"models": {"personal_api_keys": "true"}}, {"tools": {"enabled_groups": []}},
                {"offline": {"max_days": 366}}, {"approvals": {"max_level": "ask"}}, {"models": {"allowed": [""]}}):
        assert not company.is_valid(bad), bad
        with pytest.raises(PolicyError):
            parse_policy(bad)
    for bad in ({"tools": {"disabled_groups": ["shell"]}}, {"apps": {"plugins": "some"}}):
        assert not override.is_valid(bad), bad
        with pytest.raises(PolicyError):
            parse_override(bad)


# -- lockdown -----------------------------------------------------------------------------------------

def test_lockdown_is_chat_with_local_models_and_nothing_that_reaches_out() -> None:
    policy = lockdown_policy()

    assert policy.models.allowed == frozenset() and policy.models.local_models is True
    assert not policy.models.personal_api_keys and not policy.models.consumer_sign_ins
    assert {"shell", "browser", "computer_use", "mcp", "web_search", "developer"} <= policy.tools.disabled_groups
    assert not {"memory", "documents", "calculator"} & policy.tools.disabled_groups
    assert policy.approvals.max_level == "block"
    assert {policy.apps.plugins, policy.apps.mcp_servers, policy.apps.skills_hub, policy.apps.channels} == {"none"}
    assert not policy.data.chat_export and not policy.data.share_to_company


# -- the additive merge -------------------------------------------------------------------------------

def test_without_overrides_the_effective_policy_is_the_company_policy() -> None:
    company = {"approvals": {"max_level": "approve"}, "models": {"allowed": ["gpt-5"]}}
    assert effective(company) == effective(company, {})


@pytest.mark.parametrize("company,override", [
    ({"models": {"personal_api_keys": True}}, {"models": {"personal_api_keys": False}}),
    ({}, {"models": {"local_models": False}}),
    ({"approvals": {"max_level": "full"}}, {"approvals": {"max_level": "block"}}),
    ({}, {"approvals": {"max_level": "approve"}}),
    ({}, {"apps": {"plugins": "none", "mcp_servers": "allow-list"}}),
    ({"apps": {"skills_hub": "allow-list", "allowed_skills": ["a"]}}, {"apps": {"skills_hub": "none"}}),
    ({}, {"models": {"allowed": ["gpt-5"]}}),  # the company allows every model; one named model takes none away
    ({"models": {"allowed": ["gpt-5", "claude"]}}, {"models": {"allowed": ["gpt-5"]}}),
    ({"offline": {"max_days": 30}}, {"offline": {"max_days": 1}}),
    ({}, {"data": {"chat_export": False, "share_to_company": False}}),
    ({"tools": {"disabled_groups": ["shell"]}}, {"tools": {"enabled_groups": ["browser"]}}),
])
def test_an_override_never_narrows_the_company_policy(company: dict, override: dict) -> None:
    assert effective(company, override) == effective(company)


def test_an_override_widens_each_kind_of_setting() -> None:
    company = {
        "models": {"allowed": ["gpt-5"]},
        "tools": {"disabled_groups": ["shell", "browser"], "disabled_tools": ["run_command", "delete_file"]},
        "approvals": {"max_level": "approve"},
        "apps": {"plugins": "none", "mcp_servers": "allow-list", "allowed_mcp_servers": ["github"]},
        "data": {"chat_export": False},
        "offline": {"max_days": 2},
    }
    widened = effective(company, {
        "models": {"allowed": ["claude"], "personal_api_keys": True, "consumer_sign_ins": True},
        "tools": {"enabled_groups": ["shell"], "enabled_tools": ["run_command", "not_disabled"]},
        "approvals": {"max_level": "full"},
        "apps": {"plugins": "allow-list", "allowed_plugins": ["jira"], "allowed_mcp_servers": ["jira-mcp"]},
        "data": {"chat_export": True},
        "offline": {"max_days": 14},
    })

    assert widened["models"]["allowed"] == ["claude", "gpt-5"]
    assert widened["models"]["personal_api_keys"] and widened["models"]["consumer_sign_ins"]
    assert widened["tools"] == {"disabled_groups": ["browser"], "disabled_tools": ["delete_file"]}
    assert widened["approvals"]["max_level"] == "full"
    assert (widened["apps"]["plugins"], widened["apps"]["allowed_plugins"]) == ("allow-list", ["jira"])
    assert (widened["apps"]["mcp_servers"], widened["apps"]["allowed_mcp_servers"]) == ("allow-list",
                                                                                         ["github", "jira-mcp"])
    assert widened["data"]["chat_export"] is True
    assert widened["offline"]["max_days"] == 14


def test_a_person_in_two_groups_gets_what_either_group_adds() -> None:
    company = {"models": {"allowed": ["base"]},
               "tools": {"disabled_groups": ["shell", "browser", "computer_use"]},
               "approvals": {"max_level": "block"}}
    engineering = {"models": {"allowed": ["code-model"]}, "tools": {"enabled_groups": ["shell"]},
                   "approvals": {"max_level": "approve"}}
    design = {"models": {"allowed": ["image-model"]}, "tools": {"enabled_groups": ["browser"]},
              "approvals": {"max_level": "auto"}}

    both = effective(company, engineering, design)
    assert both["models"]["allowed"] == ["base", "code-model", "image-model"]
    assert both["tools"]["disabled_groups"] == ["computer_use"]
    assert both["approvals"]["max_level"] == "auto"
    # Each group alone gets only its own additions.
    assert effective(company, engineering)["tools"]["disabled_groups"] == ["browser", "computer_use"]
    assert effective(company, design)["models"]["allowed"] == ["base", "image-model"]


def test_conflicting_overrides_resolve_to_the_more_permissive() -> None:
    company = {"apps": {"plugins": "allow-list", "allowed_plugins": ["base-plugin"]}}
    allow_list = {"apps": {"plugins": "allow-list", "allowed_plugins": ["eng-plugin"]}, "offline": {"max_days": 3}}
    none = {"apps": {"plugins": "none", "allowed_plugins": []}, "offline": {"max_days": 30}}
    everything = {"apps": {"plugins": "any"}}

    merged = effective(company, allow_list, none)
    assert (merged["apps"]["plugins"], merged["apps"]["allowed_plugins"]) == ("allow-list",
                                                                              ["base-plugin", "eng-plugin"])
    assert merged["offline"]["max_days"] == 30
    assert effective(company, allow_list, everything)["apps"]["plugins"] == "any"
    assert effective(company, {"models": {"allowed": ["*"]}}, {"models": {"allowed": ["x"]}})["models"][
        "allowed"] == ["*"]


def test_an_override_that_gives_a_tool_back_doesnt_undo_a_later_company_restriction() -> None:
    engineering = {"tools": {"enabled_groups": ["shell"]}}

    before = effective({"tools": {"disabled_groups": ["shell"]}}, engineering)
    after = effective({"tools": {"disabled_groups": ["shell", "browser"]}}, engineering)

    assert before["tools"]["disabled_groups"] == []
    assert after["tools"]["disabled_groups"] == ["browser"]


def test_the_version_is_the_newest_of_the_documents_merged() -> None:
    assert effective({"version": 4}, {"version": 9}, {"version": 2})["version"] == 9
    assert effective({"version": 12}, {"version": 9})["version"] == 12
    assert effective(None, {"version": 5})["version"] == 5


def test_resolve_takes_one_company_policy_and_group_overrides() -> None:
    with pytest.raises(ValueError):
        resolve(parse_override({}))
    with pytest.raises(ValueError):
        resolve(parse_policy({}), [parse_policy({})])


def _at_least_as_permissive(widened: dict, company: dict) -> bool:
    for section in SECTIONS:
        for name, spec in specs(section).items():
            got, base = widened[section][name], company[section][name]
            if spec.widen == "any" and base and not got:
                return False
            if spec.widen == "max" and (spec.choices.index(got) < spec.choices.index(base) if spec.kind == "enum"
                                        else got < base):
                return False
            if spec.widen == "union" and not ("*" in got or set(base) <= set(got)):
                return False
            if spec.widen == "remove" and not set(got) <= set(base):
                return False
    return True


def _random_document(rng: random.Random, *, override: bool) -> dict:
    words = ["a", "b", "c", "shell", "browser"]
    document: dict = {}
    for section in SECTIONS:
        for name, spec in specs(section).items():
            if rng.random() < 0.5:
                continue
            key = spec.override_key if override and spec.override_key else name
            if spec.kind == "bool":
                value: object = rng.random() < 0.5
            elif spec.kind == "int":
                value = rng.randint(0, spec.maximum)
            elif spec.kind == "enum":
                value = rng.choice(spec.choices)
            else:
                value = rng.sample(words + (["*"] if name == "allowed" and section == "models" else []),
                                   rng.randint(0, 3))
            document.setdefault(section, {})[key] = value
    return document


def test_merging_is_order_free_and_never_narrows_across_many_random_policies() -> None:
    rng = random.Random(20261122)
    for _ in range(300):
        company = _random_document(rng, override=False)
        overrides = [_random_document(rng, override=True) for _ in range(rng.randint(0, 3))]
        merged = effective(company, *overrides)

        assert _at_least_as_permissive(merged, effective(company))
        for override in overrides:
            assert _at_least_as_permissive(merged, effective(company, override))
        for order in itertools.permutations(overrides):
            assert effective(company, *order) == merged
