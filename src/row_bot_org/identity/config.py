"""Identity provider configurations, parsed from ``ROW_BOT_ORG_IDPS`` and ``ROW_BOT_ORG_IDP_CLIENT_SECRETS``.

``ROW_BOT_ORG_IDPS`` is a JSON list, one object per identity provider (not a secret)::

    [{"name": "contoso", "kind": "entra", "issuer": "https://login.microsoftonline.com/<tenant id>/v2.0",
      "client_id": "...", "allowed_tenants": ["<tenant id>"], "groups_claim": "groups",
      "group_map": {"<group object id>": "engineering"}, "allowed_email_domains": ["contoso.com"]}]

``ROW_BOT_ORG_IDP_CLIENT_SECRETS`` (a secret) is a JSON object from each provider's name to how the
server authenticates to its token endpoint: a string (the client secret, sent as
``client_secret_basic``) or, with ``"client_auth": "private_key_jwt"``, a private JWK object.

Each problem is worded without the value, as for every setting.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

KINDS = ("entra", "okta", "google")
CLIENT_AUTH = ("client_secret_basic", "private_key_jwt")
# Entra's multi-tenant authorities: their issuer is a template that any tenant's tokens match.
ENTRA_MULTI_TENANT = ("common", "organizations", "consumers", "{tenantid}")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_TENANT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_CLAIM = re.compile(r"^[A-Za-z0-9_:./-]{1,64}$")
_KEYS = {"name", "kind", "issuer", "client_id", "client_auth", "allowed_tenants", "groups_claim", "group_map",
         "allowed_email_domains"}
_MAX_GROUP_NAME = 256


class IdpConfigError(ValueError):
    """A provider configuration can't be used; the message never carries a value."""


@dataclass(frozen=True)
class IdpConfig:
    name: str
    kind: str
    issuer: str
    client_id: str
    client_auth: str = "client_secret_basic"
    # Entra only: the tenants whose tokens are accepted (the token's `tid`).
    allowed_tenants: tuple[str, ...] = ()
    groups_claim: str = "groups"
    # IdP group (an Entra object id, an Okta group name) -> Row-Bot group. With a map, other groups are dropped.
    group_map: tuple[tuple[str, str], ...] = ()
    # With domains, sign-in needs a verified email in one of them.
    allowed_email_domains: tuple[str, ...] = ()
    # The client secret, or a private JWK (a dict) for private_key_jwt. Never in the repr.
    credential: str | dict[str, Any] = field(default="", repr=False)


def is_loopback_host(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_endpoint_url(url: object, *, what: str) -> str:
    """``url`` when it is HTTPS (or HTTP to a loopback address, for a provider on this machine)."""
    if not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise IdpConfigError(f"{what} must be an https:// address")
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None or parts.fragment or not parts.hostname:
        raise IdpConfigError(f"{what} must be an https:// address without credentials or a fragment")
    if parts.scheme == "https" or (parts.scheme == "http" and is_loopback_host(parts.hostname)):
        return url
    raise IdpConfigError(f"{what} must be an https:// address")


def _strings(value: object, what: str, pattern: re.Pattern[str], *, lower: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise IdpConfigError(f"{what} must be a list of strings")
    items = tuple(item.strip().lower() if lower else item.strip() for item in value)
    if not all(pattern.fullmatch(item) for item in items):
        raise IdpConfigError(f"{what} has an entry that isn't valid")
    return tuple(dict.fromkeys(items))


def _text(entry: dict[str, Any], key: str, what: str, *, required: bool = True, limit: int = 512) -> str | None:
    value = entry.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise IdpConfigError(f"{what} needs a {key}")
    return value.strip()


def _one(entry: object, index: int) -> IdpConfig:
    if not isinstance(entry, dict):
        raise IdpConfigError(f"entry {index + 1} must be an object")
    name = entry.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise IdpConfigError(f"entry {index + 1} needs a name of lowercase letters, digits and dashes")
    what = f"identity provider {name}"
    if unknown := sorted(set(entry) - _KEYS):
        raise IdpConfigError(f"{what} has unknown keys: {', '.join(unknown)}")
    kind = entry.get("kind")
    if kind not in KINDS:
        raise IdpConfigError(f"{what} needs a kind: one of {', '.join(KINDS)}")
    issuer = check_endpoint_url(_text(entry, "issuer", what), what=f"{what}'s issuer")
    if urlsplit(issuer).query:
        raise IdpConfigError(f"{what}'s issuer must not have a query")
    client_id = _text(entry, "client_id", what, limit=256)
    client_auth = entry.get("client_auth", "client_secret_basic")
    if client_auth not in CLIENT_AUTH:
        raise IdpConfigError(f"{what}'s client_auth must be one of {', '.join(CLIENT_AUTH)}")
    tenants: tuple[str, ...] = ()
    if kind == "entra":
        segments = {segment.lower() for segment in urlsplit(issuer).path.split("/")}
        if segments & set(ENTRA_MULTI_TENANT):
            raise IdpConfigError(
                f"{what}'s issuer is a multi-tenant authority, which accepts any tenant's tokens: use the "
                "tenant's own issuer (https://login.microsoftonline.com/<tenant id>/v2.0), one entry per tenant"
            )
        tenants = _strings(entry.get("allowed_tenants"), f"{what}'s allowed_tenants", _TENANT, lower=True)
        if not tenants:
            raise IdpConfigError(f"{what} (Entra) needs allowed_tenants")
    elif "allowed_tenants" in entry:
        raise IdpConfigError(f"{what}: allowed_tenants is for Entra only")
    groups_claim = entry.get("groups_claim", "groups")
    if not isinstance(groups_claim, str) or not _CLAIM.fullmatch(groups_claim):
        raise IdpConfigError(f"{what}'s groups_claim isn't a valid claim name")
    raw_map = entry.get("group_map", {})
    if not isinstance(raw_map, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and 0 < len(k) <= _MAX_GROUP_NAME and 0 < len(v.strip()) <= _MAX_GROUP_NAME
        for k, v in raw_map.items()
    ):
        raise IdpConfigError(f"{what}'s group_map must map group names to non-empty group names")
    domains = _strings(entry.get("allowed_email_domains", []), f"{what}'s allowed_email_domains", _DOMAIN, lower=True)
    return IdpConfig(
        name=name, kind=kind, issuer=issuer, client_id=client_id or "", client_auth=client_auth,
        allowed_tenants=tenants, groups_claim=groups_claim,
        group_map=tuple(sorted((k, v.strip()) for k, v in raw_map.items())), allowed_email_domains=domains,
    )


def parse_idps(raw: str, credentials: dict[str, Any] | None) -> tuple[IdpConfig, ...]:
    """The providers in ``raw`` (JSON), each with its credential from ``credentials``."""
    try:
        entries = json.loads(raw)
    except ValueError:
        raise IdpConfigError("must be a JSON list of identity providers") from None
    if not isinstance(entries, list) or not entries:
        raise IdpConfigError("must be a JSON list of identity providers")
    configs = [_one(entry, index) for index, entry in enumerate(entries)]
    names = [config.name for config in configs]
    if len(set(names)) != len(names):
        raise IdpConfigError("names the same identity provider twice")
    found = credentials or {}
    if unknown := sorted(set(found) - set(names)):
        raise IdpConfigError(f"has no identity provider for the credentials of {', '.join(unknown)}")
    result = []
    for config in configs:
        credential = found.get(config.name)
        if config.client_auth == "client_secret_basic":
            if not isinstance(credential, str) or not credential:
                raise IdpConfigError(f"identity provider {config.name} needs its client secret")
        elif not (isinstance(credential, dict) and credential.get("kty") in {"RSA", "EC"} and "d" in credential):
            raise IdpConfigError(f"identity provider {config.name} needs a private RSA or EC JWK for private_key_jwt")
        result.append(replace(config, credential=credential))
    return tuple(result)


def parse_credentials(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except ValueError:
        raise IdpConfigError("must be a JSON object from provider names to credentials") from None
    if not isinstance(value, dict):
        raise IdpConfigError("must be a JSON object from provider names to credentials")
    return value


__all__ = ["CLIENT_AUTH", "ENTRA_MULTI_TENANT", "KINDS", "IdpConfig", "IdpConfigError", "check_endpoint_url",
           "is_loopback_host", "parse_credentials", "parse_idps"]
