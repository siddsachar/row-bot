"""The server's identity provider, parsed from ``ROW_BOT_ORG_IDP`` and ``ROW_BOT_ORG_IDP_CLIENT_SECRET``.

One provider per server. ``ROW_BOT_ORG_IDP`` is a JSON object (not a secret)::

    {"kind": "entra", "issuer": "https://login.microsoftonline.com/<tenant id>/v2.0",
     "client_id": "...", "allowed_tenants": ["<tenant id>"], "groups_claim": "groups",
     "group_map": {"<group object id>": "engineering"}, "allowed_email_domains": ["contoso.com"]}

``ROW_BOT_ORG_IDP_CLIENT_SECRET`` (a secret) is the client secret the server sends to the token
endpoint (``client_secret_basic``).

Google needs ``hosted_domains``: the Workspace domains whose accounts may sign in (the ID token's ``hd``).
Without it any Google account in the world would get in. Entra takes no ``allowed_email_domains``: it sends
no ``email_verified``, so its tenants (``allowed_tenants``) and the application's assignment decide.

Each problem is worded without the value, as for every setting.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

KINDS = ("entra", "okta", "google")
# Entra's multi-tenant authorities: their issuer is a template that any tenant's tokens match.
ENTRA_MULTI_TENANT = ("common", "organizations", "consumers", "{tenantid}")
_TENANT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_CLAIM = re.compile(r"^[A-Za-z0-9_:./-]{1,64}$")
_KEYS = {"kind", "issuer", "client_id", "allowed_tenants", "hosted_domains", "groups_claim", "group_map",
         "allowed_email_domains"}
_MAX_GROUP_NAME = 256


class IdpConfigError(ValueError):
    """The provider configuration can't be used; the message never carries a value."""


@dataclass(frozen=True)
class IdpConfig:
    kind: str
    issuer: str
    client_id: str
    # Entra only: the tenants whose tokens are accepted (the token's `tid`).
    allowed_tenants: tuple[str, ...] = ()
    # Google only: the Workspace domains whose accounts are accepted (the token's `hd`).
    hosted_domains: tuple[str, ...] = ()
    groups_claim: str = "groups"
    # IdP group (an Entra object id, an Okta group name) -> Row-Bot group. With a map, other groups are dropped.
    group_map: tuple[tuple[str, str], ...] = ()
    # With domains, sign-in needs a verified email in one of them.
    allowed_email_domains: tuple[str, ...] = ()
    # Never in the repr.
    client_secret: str = field(default="", repr=False)


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


def _text(entry: dict[str, Any], key: str, *, limit: int = 512) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise IdpConfigError(f"needs its {key}")
    return value.strip()


def parse_idp(raw: str, client_secret: str) -> IdpConfig:
    """The provider in ``raw`` (a JSON object), with its client secret."""
    try:
        entry = json.loads(raw)
    except ValueError:
        raise IdpConfigError("must be a JSON object describing the identity provider") from None
    if not isinstance(entry, dict):
        raise IdpConfigError("must be a JSON object describing the identity provider")
    if unknown := sorted(set(entry) - _KEYS):
        raise IdpConfigError(f"has unknown keys: {', '.join(unknown)}")
    kind = entry.get("kind")
    if kind not in KINDS:
        raise IdpConfigError(f"needs a kind: one of {', '.join(KINDS)}")
    issuer = check_endpoint_url(_text(entry, "issuer"), what="the issuer")
    if urlsplit(issuer).query:
        raise IdpConfigError("the issuer must not have a query")
    client_id = _text(entry, "client_id", limit=256)
    tenants: tuple[str, ...] = ()
    if kind == "entra":
        segments = {segment.lower() for segment in urlsplit(issuer).path.split("/")}
        if segments & set(ENTRA_MULTI_TENANT):
            raise IdpConfigError(
                "the issuer is a multi-tenant authority, which accepts any tenant's tokens: use the "
                "tenant's own issuer (https://login.microsoftonline.com/<tenant id>/v2.0)"
            )
        tenants = _strings(entry.get("allowed_tenants"), "allowed_tenants", _TENANT, lower=True)
        if not tenants:
            raise IdpConfigError("(Entra) needs allowed_tenants")
        if "allowed_email_domains" in entry:
            raise IdpConfigError(
                "allowed_email_domains doesn't work with Entra, which sends no email_verified: allowed_tenants "
                "and the application's assignment (Assignment required) decide who signs in"
            )
    elif "allowed_tenants" in entry:
        raise IdpConfigError("allowed_tenants is for Entra only")
    hosted: tuple[str, ...] = ()
    if kind == "google":
        hosted = _strings(entry.get("hosted_domains", []), "hosted_domains", _DOMAIN, lower=True)
        if not hosted:
            raise IdpConfigError("(Google) needs hosted_domains, the Workspace domains whose accounts may sign in: "
                                 "without them any Google account could")
    elif "hosted_domains" in entry:
        raise IdpConfigError("hosted_domains is for Google only")
    groups_claim = entry.get("groups_claim", "groups")
    if not isinstance(groups_claim, str) or not _CLAIM.fullmatch(groups_claim):
        raise IdpConfigError("groups_claim isn't a valid claim name")
    raw_map = entry.get("group_map", {})
    if not isinstance(raw_map, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and 0 < len(k) <= _MAX_GROUP_NAME and 0 < len(v.strip()) <= _MAX_GROUP_NAME
        for k, v in raw_map.items()
    ):
        raise IdpConfigError("group_map must map group names to non-empty group names")
    domains = _strings(entry.get("allowed_email_domains", []), "allowed_email_domains", _DOMAIN, lower=True)
    return IdpConfig(
        kind=kind, issuer=issuer, client_id=client_id, allowed_tenants=tenants, hosted_domains=hosted,
        groups_claim=groups_claim,
        group_map=tuple(sorted((k, v.strip()) for k, v in raw_map.items())), allowed_email_domains=domains,
        client_secret=client_secret,
    )


__all__ = ["ENTRA_MULTI_TENANT", "KINDS", "IdpConfig", "IdpConfigError", "check_endpoint_url", "is_loopback_host",
           "parse_idp"]
