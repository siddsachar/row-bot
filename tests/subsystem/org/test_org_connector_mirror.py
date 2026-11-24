"""Connectors without a database: how upstream permissions become a document's readers (anything unmappable makes a
file admins only, never everyone's), how Graph's and Drive's permission shapes are read, that a source's calls never
leave its documented hosts (a next link or download redirect elsewhere is refused before any request), and that a
sealed secret opens only for its own connector. Fakes only (``httpx.MockTransport``): no real Microsoft or Google
host is called.
"""

from __future__ import annotations

import httpx
import pytest

from row_bot_org.connectors import secrets, upstream
from row_bot_org.connectors.mirror import GROUP, OTHER, PERSON, Grant, mirror
from row_bot_org.identity.config import IdpConfig

ENGINEERING_ID = "5d3b2a10-0000-4000-8000-00000000e001"


def idp(kind: str = "okta", group_map: tuple[tuple[str, str], ...] = ()) -> IdpConfig:
    return IdpConfig(kind=kind, issuer="https://idp.example/", client_id="row-bot", group_map=group_map)


def person(email: str | None, key: str = "user:1") -> Grant:
    return Grant(PERSON, key, "Someone", email=email)


def group(identifier: str, key: str | None = None) -> Grant:
    return Grant(GROUP, key or f"microsoft-group:{identifier}", "A group", group=identifier)


# -- mirroring --------------------------------------------------------------------------------------

def test_people_map_by_email_and_groups_by_the_identity_providers_map() -> None:
    mapped = mirror([person("Bo@Example.com"), group(ENGINEERING_ID)], provider="microsoft",
                    idp=idp(group_map=((ENGINEERING_ID, "Engineering"),)), principal_map={})
    assert (mapped.readers, mapped.unmapped) == (("e:bo@example.com", "g:Engineering"), ())
    # Google groups are compared without case.
    google = mirror([Grant(GROUP, "google-group:eng@example.com", "eng", group="Eng@Example.com")],
                    provider="google", idp=idp(group_map=(("eng@example.com", "Engineering"),)), principal_map={})
    assert google.readers == ("g:Engineering",)


def test_without_a_group_map_only_entras_own_ids_map_and_only_for_microsoft() -> None:
    entra = idp("entra")
    assert mirror([group(ENGINEERING_ID)], provider="microsoft", idp=entra, principal_map={}).readers == (
        f"g:{ENGINEERING_ID}",)
    for provider, provider_idp in (("microsoft", idp("okta")), ("google", idp("google")), ("google", entra)):
        found = mirror([group(ENGINEERING_ID)], provider=provider, idp=provider_idp, principal_map={})
        assert found.readers == ()
        assert [entry["key"] for entry in found.unmapped] == [f"microsoft-group:{ENGINEERING_ID}"]


def test_anything_unmappable_makes_the_file_admins_only_never_everyones() -> None:
    grants = [person("bo@example.com"), Grant(OTHER, "link:organization", "Anyone in the organisation with the link"),
              Grant(OTHER, "sharepoint-group:5", "Contoso Members"), person(None, key="user:7"),
              group("not-in-the-map")]
    found = mirror(grants, provider="microsoft", idp=idp(group_map=((ENGINEERING_ID, "Engineering"),)),
                   principal_map={})
    assert found.readers == ()
    assert [entry["key"] for entry in found.unmapped] == ["link:organization", "microsoft-group:not-in-the-map",
                                                          "sharepoint-group:5", "user:7"]
    # An admin's principal map names a Row-Bot group for what can't be mapped otherwise.
    remapped = mirror(grants, provider="microsoft", idp=idp(group_map=((ENGINEERING_ID, "Engineering"),)),
                      principal_map={"link:organization": "Everyone", "sharepoint-group:5": "Sales",
                                     "user:7": "Sales", "microsoft-group:not-in-the-map": "Sales"})
    assert remapped == type(remapped)(("e:bo@example.com", "g:Everyone", "g:Sales"), ())
    # No grant at all: nobody listed, so admins only too.
    assert mirror([], provider="google", idp=idp(), principal_map={}).readers == ()
    many = mirror([person(f"p{n}@example.com", key=f"user:{n}") for n in range(1001)], provider="google", idp=idp(),
                  principal_map={})
    assert many.readers == () and [entry["key"] for entry in many.unmapped] == ["too-many-readers"]


# -- the sources' permission shapes -----------------------------------------------------------------------

def test_graph_permissions_are_read_by_who_they_name() -> None:
    def keys(permission: dict) -> list[tuple[str, str, str | None]]:
        return [(g.kind, g.key, g.email or g.group) for g in upstream.graph_grants(permission)]

    assert keys({"id": "p1", "roles": ["read"], "grantedToV2": {"group": {"id": "g-1", "displayName": "Eng"}}}) == [
        (GROUP, "microsoft-group:g-1", "g-1")]
    assert keys({"id": "p2", "roles": ["write"],
                 "grantedToV2": {"user": {"id": "u-1", "displayName": "Bo"},
                                 "siteUser": {"id": "9", "email": "bo@example.com"}}}) == [
        (PERSON, "user:u-1", "bo@example.com")]
    assert keys({"id": "p3", "roles": ["owner"], "grantedToV2": {"siteGroup": {"id": "3", "displayName": "Owners"}}}) == [
        (OTHER, "sharepoint-group:3", None)]
    assert keys({"id": "p4", "link": {"scope": "organization"}}) == [(OTHER, "link:organization", None)]
    assert keys({"id": "p5", "link": {"scope": "anonymous"}}) == [(OTHER, "link:anonymous", None)]
    # A link for specific people names them.
    assert keys({"id": "p6", "link": {"scope": "users"},
                 "grantedToIdentitiesV2": [{"user": {"id": "u-2", "email": "eve@example.com"}}]}) == [
        (PERSON, "user:u-2", "eve@example.com")]
    # An application reads nothing through Row-Bot: no grant.
    assert keys({"id": "p7", "grantedToV2": {"application": {"id": "app"}}}) == []


def test_drive_permissions_are_read_by_type() -> None:
    def keys(permission: dict) -> list[tuple[str, str]]:
        return [(g.kind, g.key) for g in upstream.drive_grants(permission)]

    assert keys({"id": "1", "type": "user", "role": "reader", "emailAddress": "bo@example.com"}) == [(PERSON, "user:1")]
    assert keys({"id": "2", "type": "group", "role": "writer", "emailAddress": "Eng@Example.com"}) == [
        (GROUP, "google-group:eng@example.com")]
    assert keys({"id": "3", "type": "domain", "role": "reader", "domain": "Example.com"}) == [
        (OTHER, "google-domain:example.com")]
    assert keys({"id": "4", "type": "anyone", "role": "reader"}) == [(OTHER, "google-anyone")]
    assert keys({"id": "5", "type": "user", "emailAddress": "gone@example.com", "deleted": True}) == []


# -- the sources' calls stay on their hosts -------------------------------------------------------------

def graph_source(handler) -> tuple[upstream.MicrosoftSource, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(answer), follow_redirects=False)
    credentials = upstream.Credentials("microsoft", "t-1", "c-1", "a-secret")
    return upstream.MicrosoftSource(client, credentials, "b!drive", None), seen


def test_a_next_link_or_download_elsewhere_is_never_followed() -> None:
    source, seen = graph_source(lambda request: httpx.Response(
        200, json={"value": [], "@odata.nextLink": "https://evil.example/v1.0/drives/b!drive/root/children"}))
    with pytest.raises(upstream.Unavailable):
        source.children("root-1", None)
    assert [request.url.host for request in seen] == ["graph.microsoft.com"]

    for location in ("https://evil.example/download/1", "http://contoso.sharepoint.com/download/1",
                     "https://contoso.sharepoint.com.evil.example/download/1"):
        source, seen = graph_source(lambda request, to=location: httpx.Response(302, headers={"Location": to}))
        with pytest.raises(upstream.ItemGone):
            source.download(upstream.Item("i-1", "root-1", "a.md", False), 1024)
        assert [request.url.host for request in seen] == ["graph.microsoft.com"]

    # SharePoint's pre-authenticated download is fetched without the access token.
    def sharepoint(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(302, headers={"Location": "https://contoso.sharepoint.com/download/1"})
        return httpx.Response(200, content=b"the file")

    source, seen = graph_source(sharepoint)
    source.http.token = "a-token"
    assert source.download(upstream.Item("i-1", "root-1", "a.md", False), 1024) == b"the file"
    assert [(r.url.host, r.headers.get("authorization")) for r in seen] == [
        ("graph.microsoft.com", "Bearer a-token"), ("contoso.sharepoint.com", None)]
    with pytest.raises(upstream.TooLarge):
        graph_source(sharepoint)[0].download(upstream.Item("i-1", "root-1", "a.md", False), 4)


def test_failures_are_told_apart_and_say_nothing_of_the_secret() -> None:
    def answering(status: int, **headers: str):
        return graph_source(lambda request: httpx.Response(status, headers=headers,
                                                           json={"error": "invalid_client a-secret"}))[0]

    with pytest.raises(upstream.Refused) as refused:
        answering(401).sign_in()
    assert "a-secret" not in refused.value.reason
    with pytest.raises(upstream.Throttled) as throttled:
        answering(429, **{"Retry-After": "120"}).root()
    assert throttled.value.retry_after == 120.0
    with pytest.raises(upstream.Unavailable) as down:
        answering(503).root()
    assert down.value.retry_after is None
    with pytest.raises(upstream.FeedExpired):
        answering(410).changes("https://graph.microsoft.com/v1.0/drives/b!drive/root/delta?token=t1")
    with pytest.raises(upstream.Refused):
        answering(403).root()
    with pytest.raises(upstream.ItemGone):
        answering(404).grants(upstream.Item("i-1", "root-1", "a.md", False))
    assert "a-secret" not in repr(upstream.Credentials("microsoft", "t", "c", "a-secret"))


def test_a_sealed_secret_opens_only_for_its_own_connector_tenant_and_client() -> None:
    key = bytes(range(32))
    sealed = secrets.seal(key, "connector-1", "microsoft", "tenant-1", "client-1", "a-secret")
    assert b"a-secret" not in sealed
    assert secrets.unseal(key, "connector-1", "microsoft", "tenant-1", "client-1", sealed) == "a-secret"
    for other in (("connector-2", "microsoft", "tenant-1", "client-1"), ("connector-1", "google", "tenant-1", "client-1"),
                  ("connector-1", "microsoft", "tenant-2", "client-1"), ("connector-1", "microsoft", "tenant-1", "c-2")):
        with pytest.raises(secrets.SecretUnavailable):
            secrets.unseal(key, *other, sealed)
    with pytest.raises(secrets.SecretUnavailable):
        secrets.unseal(bytes(32), "connector-1", "microsoft", "tenant-1", "client-1", sealed)


def test_entra_people_map_by_object_id_and_emails_only_inside_the_allowed_domains() -> None:
    oid = "aaaaaaaa-0000-4000-8000-000000000001"
    grant = Grant(PERSON, "user:1", "Eve", email="eve@contoso.example", oid=oid)
    assert mirror([grant], provider="microsoft", idp=idp("entra"), principal_map={}).readers == (
        "e:eve@contoso.example", f"o:{oid}")
    # Only an Entra sign-in gives Row-Bot people's object ids, and only Graph names people by them.
    for provider, provider_idp in (("microsoft", idp("okta")), ("google", idp("entra"))):
        assert mirror([grant], provider=provider, idp=provider_idp, principal_map={}).readers == (
            "e:eve@contoso.example",)
    # With allowed_email_domains, an email outside them maps to nobody, so its file is admins only.
    okta = IdpConfig(kind="okta", issuer="https://idp.example/", client_id="row-bot",
                     allowed_email_domains=("example.com",))
    inside, outside = person("Bo@Example.com"), person("bo@elsewhere.example", key="user:2")
    assert mirror([inside], provider="google", idp=okta, principal_map={}).readers == ("e:bo@example.com",)
    found = mirror([inside, outside], provider="google", idp=okta, principal_map={})
    assert found.readers == () and [entry["key"] for entry in found.unmapped] == ["user:2"]


def test_graph_marks_sharing_changes_and_ids_or_links_too_long_to_keep_are_dropped() -> None:
    shared = upstream.graph_item({"id": "f-1", "name": "F", "folder": {}, "parentReference": {"id": "root-1"},
                                  "@microsoft.graph.sharedChanged": "True"})
    assert (shared.parent_id, shared.is_folder, shared.shared_changed) == ("root-1", True, True)
    plain = upstream.graph_item({"id": "f-1", "name": "F", "folder": {}, "parentReference": {"id": "root-1"}})
    assert plain.shared_changed is False
    assert upstream.drive_item({"id": "d-1", "name": "D", "mimeType": upstream.FOLDER_MIME}).shared_changed is None
    # A parent too long to be a folder the connector keeps leaves the item out of scope; an id too long, out.
    assert upstream.graph_item({"id": "f-2", "name": "x.md", "file": {},
                                "parentReference": {"id": "p" * 600}}).parent_id is None
    assert upstream.drive_item({"id": "d-2", "name": "x.md", "parents": ["p" * 600]}).parent_id is None
    assert upstream.graph_item({"id": "i" * 600, "name": "x.md"}) is None
    # A Microsoft user is named by their Entra object id (lower case).
    (grant,) = upstream.graph_grants({"id": "p", "grantedToV2": {"user": {"id": ENGINEERING_ID.upper()}}})
    assert grant.oid == ENGINEERING_ID

    source, seen = graph_source(lambda request: httpx.Response(200, json={
        "@odata.deltaLink": f"{upstream.GRAPH}/drives/b!drive/root/delta?token=t1"}))
    source.latest_cursor()
    # The cursor the feed starts from is asked for with the same sharing-changes header as the feed.
    assert seen[0].headers["prefer"] == upstream.GRAPH_PREFER
    with pytest.raises(upstream.Unavailable):
        source.changes(f"{upstream.GRAPH}/drives/b!drive/root/delta?token=" + "t" * 9000)
    assert len(seen) == 1
