"""The featured app catalog: real routes, sourced facts, findable by name and job, featured first."""
import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import apps, icons, index
from row_bot.integrations.safe import TtlCache
from row_bot.mcp_client import marketplace, registry_snapshot

ROUTES = ("curated:", "registry:", "endpoint:", "account:", "channel:", "hermes:", "bundled:")


def featured() -> list[apps.App]:
    return sorted((app for app in apps.catalog()[0].values() if app.featured_rank), key=lambda app: app.featured_rank)


def test_every_featured_app_has_a_real_route_sourced_facts_and_an_icon():
    ranked = featured()
    assert 80 <= len(ranked) <= 100 and [app.featured_rank for app in ranked] == list(range(1, len(ranked) + 1))
    assert {app.category for app in ranked} == set(apps.CATEGORIES)
    for app in apps.catalog()[0].values():
        assert any(ref.startswith(ROUTES) for ref in app.refs), app.id
        assert app.jobs and app.synonyms and len(app.example_prompts) >= (0 if app.placeholder else 2), app.id
        assert app.placeholder or app.sources or app.id == "local-text-tools", app.id  # Vendor docs recorded.
        assert app.icon == "" or icons.license_of(app.icon)["license"], app.id
        assert app.ref()["icon"].startswith(("si:", "letter:"))


def test_each_curated_recipe_endpoint_is_a_vendor_host_with_the_badge():
    for entry in marketplace.CURATED_STARTER_CATALOG:
        refs = ["curated:" + entry.id.lower()] + apps.recipe_refs(entry.install)
        app = apps.match(refs)
        if (entry.install or {}).get("url") and app.domains:
            assert apps.verified(app, refs), entry.id


@pytest.mark.parametrize("app", featured()[:50], ids=lambda app: app.id)
def test_the_top_50_apps_are_findable_by_name_and_by_job(app):
    assert api.list_apps(app.name)["items"][0]["id"] == app.id
    assert app.id in [found["id"] for found in api.list_apps(app.jobs[0])["items"]]


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    """The real release snapshot (tens of thousands of records), indexed as start-up does."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(registry_snapshot, "SHIPPED", registry_snapshot.Path(registry_snapshot.__file__).with_name(
        "registry_snapshot.jsonl.xz"))
    monkeypatch.setattr(api, "_SEARCHES", TtlCache(1200, 64))
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("a search fetched"))
    index.ensure()


@pytest.mark.slow
def test_discover_opens_on_featured_apps_and_the_top_50_connect_by_name(shipped):
    page = api.read_items(owner_id="owner", scope="catalog", kind="mcp", limit=50)
    assert page["items"] and all(row["app"] and row["app"]["featured_rank"] for row in page["items"])
    ranks = [row["app"]["featured_rank"] for row in page["items"]]
    assert ranks == sorted(ranks)  # Featured order, never alphabetical.
    for app in featured()[:50]:
        if app.placeholder or not any(ref.startswith(("curated:", "registry:", "endpoint:")) for ref in app.refs):
            continue  # Accounts and channels join Apps in Phase 5.
        rows = api.read_items(owner_id="owner", scope="catalog", kind="mcp", query=app.name, limit=5)["items"]
        assert rows and rows[0]["app"] and rows[0]["app"]["id"] == app.id, app.id


def test_a_shared_hosting_subdomain_never_borrows_a_vendor_badge():
    netlify = apps.catalog()[0]["netlify"]
    assert apps.verified(netlify, ["endpoint:netlify-mcp.netlify.app"])  # The endpoint Netlify documents.
    assert not apps.verified(netlify, ["endpoint:someone-else.netlify.app"])
    assert not any(domain.endswith(apps.SHARED_HOSTING) for app in apps.catalog()[0].values() for domain in app.domains)
