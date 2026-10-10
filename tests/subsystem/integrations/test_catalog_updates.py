"""Catalog updates: explicit, in the background, failing safe; the schedule is opt-in (fakes only)."""
import io
import json

import pytest
from PIL import Image

from row_bot.integrations import catalogs, icons, index, sources
from row_bot.mcp_client import marketplace, registry_snapshot
from tests.helpers.registry import search_catalog, use_registry

pytestmark = pytest.mark.platform


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(icons, "_download", lambda url: pytest.fail("an icon was fetched outside the test's fake"))
    monkeypatch.setattr(registry_snapshot.time, "sleep", lambda seconds: None)
    return tmp_path


def envelope(name, *, updated="2026-10-03T10:00:00Z", status="active", icon=""):
    server = {"name": name, "version": "1.0.0", "title": name.split("/")[1].title(), "description": "Fixture " + name,
              "remotes": [{"type": "streamable-http", "url": f"https://{name.split('/')[0]}.example.test/mcp"}]}
    if icon:
        server["icons"] = [{"src": icon, "mimeType": "image/png"}]
    return {"server": server, "_meta": {"io.modelcontextprotocol.registry/official": {"status": status, "updatedAt": updated}}}


def entries(*names):
    return [entry for name in names for entry in marketplace.registry_entries({"servers": [envelope(name)]})]


def registry_pages(monkeypatch, pages, calls=None):
    """Serve Registry pages to the sync; anything else in the list is raised."""
    def get(url, headers, meta):
        if calls is not None:
            calls.append((url, dict(headers)))
        page = pages.pop(0)
        if isinstance(page, Exception):
            raise page
        meta["headers"] = {"etag": '"v2"'}
        return json.dumps(page).encode()
    monkeypatch.setattr(registry_snapshot, "_get", get)


def names():
    return sorted(e.metadata["canonical_name"] for e in index.rows())


def png(size=(300, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "#3366cc").save(out, "PNG")
    return out.getvalue()


def test_a_delta_update_merges_removes_and_swaps_the_mirror(local, monkeypatch):
    use_registry(monkeypatch, local, entries("org.a/one", "org.b/two"), watermark="2026-10-01T00:00:00Z")
    before = index.current()
    calls = []
    registry_pages(monkeypatch, [{"servers": [envelope("org.c/three", icon="https://cdn.c.org/three.png"),
                                              envelope("org.b/two", status="deleted")], "metadata": {}}], calls)
    monkeypatch.setattr(icons, "_download", lambda url: png())
    state = catalogs.update("official", wait=True)
    assert state["state"] == "done" and state["entries"] == 2
    assert "updated_since=2026-09-30T23%3A50%3A00Z" in calls[0][0]  # The watermark, rewound ten minutes.
    assert names() == ["org.a/one", "org.c/three"]
    after = index.current()
    assert after["file"] != before["file"] and after["etag"] == '"v2"'
    assert registry_snapshot.instant(after["watermark"]) == "2026-10-03T10:00:00.000000000Z"
    assert sources.SOURCES["official"].row(index.lookup("org.c/three@1.0.0"))[0]["icon"].startswith("cached:")


def test_a_failed_update_keeps_the_previous_index_serving(local, monkeypatch):
    use_registry(monkeypatch, local, entries("org.a/one"), watermark="2026-10-01T00:00:00Z")
    before = index.current()
    registry_pages(monkeypatch, [{"servers": [envelope("org.c/three")], "metadata": {"nextCursor": "c1"}},
                                 ValueError("invalid_registry_response")])
    state = catalogs.update("official", wait=True)
    assert state["state"] == "failed" and state["error"] == "invalid_registry_response"
    assert index.current() == before and names() == ["org.a/one"]
    assert sorted(p.name for p in (local / "catalogs").iterdir() if p.name.startswith("registry-")) == [before["file"]]
    assert index.search("one")[0]  # Still searchable.


def test_a_build_that_fails_leaves_no_partial_generation(local, monkeypatch):
    use_registry(monkeypatch, local, entries("org.a/one"), watermark="2026-10-01T00:00:00Z")
    before = index.current()
    registry_pages(monkeypatch, [{"servers": [envelope("org.c/three")], "metadata": {}}])
    monkeypatch.setattr(index, "derive", lambda entry: (_ for _ in ()).throw(OSError("disk full")))
    assert catalogs.update("official", wait=True)["state"] == "failed"
    assert index.current() == before
    assert [p.name for p in (local / "catalogs").iterdir() if p.name.startswith("registry-")] == [before["file"]]


def test_not_modified_keeps_the_mirror_and_an_old_one_resyncs_fully(local, monkeypatch):
    import httpx
    use_registry(monkeypatch, local, entries("org.a/one"), watermark="2026-10-01T00:00:00Z")
    before = index.current()
    response = httpx.Response(304, request=httpx.Request("GET", registry_snapshot.SOURCE))
    registry_pages(monkeypatch, [httpx.HTTPStatusError("not modified", request=response.request, response=response)])
    assert catalogs.update("official", wait=True)["state"] == "done" and index.current() == before
    use_registry(monkeypatch, local, entries("org.a/one", "org.z/old"), watermark="2025-01-01T00:00:00Z")
    index.build(entries("org.a/one", "org.z/old"), captured_at=1, watermark="2025-01-01T00:00:00Z")
    calls = []
    registry_pages(monkeypatch, [{"servers": [envelope("org.a/one")], "metadata": {}}], calls)
    catalogs.update("official", wait=True)
    assert "updated_since" not in calls[0][0] and names() == ["org.a/one"]  # A full sync replaces the mirror.


def test_updates_run_only_when_asked_and_unknown_or_local_sources_refuse(local, monkeypatch):
    use_registry(monkeypatch, local, entries("org.a/one"))
    monkeypatch.setattr(registry_snapshot, "_get", lambda *a: pytest.fail("contacted without an update"))
    search_catalog(query="one")
    assert catalogs.state("official")["state"] == "never"
    for source in ("recommended", "native", "examples", "glama", "unknown"):
        with pytest.raises(ValueError, match="not_updatable"):
            catalogs.update(source)
    assert set(catalogs.updatable()) == {"official", "hermes_mcp", "clawhub", "github", "hermes"}
    assert all(sources.SOURCES[key].network == "explicit" for key in catalogs.updatable())


def test_live_sources_update_through_their_owners_and_failures_are_kept_short(local, monkeypatch):
    from row_bot.plugins import hermes_catalog
    from row_bot.skills_hub.models import SkillHubEntry, SourceResult
    from row_bot.skills_hub.source_registry import SkillSourceRegistry
    monkeypatch.setattr(hermes_catalog, "read_catalog", lambda **k: {"entries": [], "status": "stale", "message": "",
                                                                      "fetched_at": 1})
    assert catalogs.update("hermes", wait=True) | {"checked_at": None} == {
        "state": "failed", "updated_at": None, "checked_at": None, "error": "source_unavailable", "entries": None}
    refreshed = []

    def refresh(self, source_id, cancelled=None):
        refreshed.append(source_id)
        return SourceResult([SkillHubEntry("clawhub:a/b", "B", "", "clawhub", "clawhub", "clawhub:a/b")], source_id, "live")
    monkeypatch.setattr(SkillSourceRegistry, "refresh", refresh)
    assert catalogs.update("clawhub", wait=True)["state"] == "done" and refreshed == ["clawhub"]


def test_the_schedule_is_off_by_default_and_runs_only_due_sources(local, monkeypatch):
    from row_bot import tasks
    jobs = {}

    class Scheduler:
        def add_job(self, func, **options):
            jobs[options["id"]] = (func, options)

        def get_job(self, job):
            return jobs.get(job)

        def remove_job(self, job):
            jobs.pop(job)
    monkeypatch.setattr(tasks, "_get_scheduler", Scheduler)
    monkeypatch.setattr(tasks, "_scheduler", Scheduler())
    assert catalogs.schedule() == {"enabled": False, "interval_days": 7, "sources": None}
    started = []
    monkeypatch.setattr(catalogs, "update", lambda source, **k: started.append(source))
    assert catalogs.run_due() == [] and started == []
    use_registry(monkeypatch, local, entries("org.a/one"))
    catalogs.start()
    assert jobs == {}  # Start-up registers nothing while the schedule is off.
    with pytest.raises(ValueError, match="invalid"):
        catalogs.set_schedule(enabled=True, interval_days=2)
    catalogs.set_schedule(enabled=True, interval_days=1)
    assert jobs[catalogs.JOB][1]["trigger"] == "interval"
    catalogs._write(lambda value: value.setdefault("sources", {}).update(official={"checked_at": 1_790_000_000}))
    assert "official" not in catalogs.run_due(now=lambda: 1_790_003_600) and "hermes" in started
    started.clear()
    catalogs.set_schedule(enabled=True, interval_days=1, sources=["official"])  # Only the catalogs the user chose.
    catalogs._write(lambda value: value["sources"].update(official={"checked_at": 1}))
    assert catalogs.run_due(now=lambda: 1_790_003_600) == ["official"] and started == ["official"]
    with pytest.raises(ValueError, match="invalid"):
        catalogs.set_schedule(enabled=True, interval_days=1, sources=["glama"])
    catalogs.set_schedule(enabled=False, interval_days=1)
    assert jobs == {} and catalogs.run_due() == []


def test_watermarks_compare_as_instants_whatever_their_precision():
    assert registry_snapshot.instant("2026-10-04T13:55:59.825784Z") > registry_snapshot.instant("2026-10-04T13:55:59Z")
    assert registry_snapshot.instant("2026-10-04T13:55:59.5+00:00") == "2026-10-04T13:55:59.500000000Z"
    assert registry_snapshot.instant("yesterday") == "" and registry_snapshot.instant(None) == ""


def test_the_previous_generation_outlives_one_swap_for_searches_in_flight(local, monkeypatch):
    use_registry(monkeypatch, local, entries("org.a/one"), watermark="2026-10-01T00:00:00Z")
    first = index.current()["file"]
    second = index.build(entries("org.a/one", "org.b/two"), captured_at=1, watermark="2026-10-02T00:00:00Z")["file"]
    assert (local / "catalogs" / first).exists()  # A search that read the old pointer can still open it.
    index.build(entries("org.a/one"), captured_at=1, watermark="2026-10-03T00:00:00Z")
    assert not (local / "catalogs" / first).exists() and (local / "catalogs" / second).exists()


def test_an_icon_failure_never_fails_an_update_that_already_swapped(local, monkeypatch):
    use_registry(monkeypatch, local, entries("org.a/one"), watermark="2026-10-01T00:00:00Z")
    registry_pages(monkeypatch, [{"servers": [envelope("org.c/three")], "metadata": {}}])
    monkeypatch.setattr(icons, "cache_remote", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only folder")))
    assert catalogs.update("official", wait=True)["state"] == "done" and names() == ["org.a/one", "org.c/three"]


def test_an_enabled_schedule_is_registered_even_if_the_index_cannot_be_built(local, monkeypatch):
    registered = []
    catalogs._write(lambda value: value.update(schedule={"enabled": True, "interval_days": 7}))
    monkeypatch.setattr(catalogs, "_register", lambda: registered.append(True))
    monkeypatch.setattr(index, "ensure", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        catalogs.start()
    assert registered == [True]
