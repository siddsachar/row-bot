"""Windows connectors (the On-device Agent Registry): found without starting anything, listed only
when the person updates the catalog, connected through the normal plan, hidden everywhere else.
A fake ``odr.exe`` only: nothing here runs a Windows command."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import catalogs, plans, sources, windows_connectors as odr
from tests.helpers.registry import search_catalog

pytestmark = pytest.mark.platform

LISTED = {"servers": [
    {"server": {"name": "com.microsoft.windows/file-explorer", "title": "File Explorer", "version": "1.0",
                "description": "Find and organise files on this PC.",
                "packages": [{"registryType": "on_device", "identifier": "MicrosoftWindows.FileExplorer_cw5n1h2txyewy"}]}},
    {"server": {"name": "com.contoso/notes", "description": "Contoso notes.", "version": "2.1",
                "packages": [{"registryType": "on_device", "identifier": "Contoso.Notes_8wekyb3d8bbwe"}]}},
    {"server": {"name": "com.example/remote", "description": "Hosted only.", "remotes": [{"url": "https://x.example/mcp"}]}},
    {"server": {"name": "com.example/flag", "packages": [{"registryType": "on_device", "identifier": "--help"}]}},
    {"server": {"name": "com.example/shell", "packages": [{"registryType": "on_device", "identifier": "a & calc"}]}},
]}


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """A Windows Insider build whose system folder has ``odr.exe``; ``ran`` records every command."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    system = tmp_path / "Windows"
    (system / "System32").mkdir(parents=True)
    (system / "System32" / "odr.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", lambda: SimpleNamespace(build=26220), raising=False)
    monkeypatch.setattr(odr, "_windows_folder", lambda: system)
    ran, outputs = [], {("list",): json.dumps(LISTED)}

    def run(command, **options):
        assert options["shell"] is False and options["timeout"] == odr.LIST_SECONDS
        ran.append(command[1:])
        return SimpleNamespace(returncode=0, stdout=outputs.get(tuple(command[1:]), "").encode())
    monkeypatch.setattr(subprocess, "run", run)
    return SimpleNamespace(odr=system / "System32" / "odr.exe", ran=ran, outputs=outputs)


def test_it_is_found_only_in_the_windows_system_folder_of_a_new_enough_build(windows, monkeypatch, tmp_path):
    assert odr.locate() == windows.odr
    monkeypatch.setattr(sys, "getwindowsversion", lambda: SimpleNamespace(build=26200), raising=False)
    assert odr.locate() is None
    monkeypatch.setattr(sys, "getwindowsversion", lambda: SimpleNamespace(build=26220), raising=False)
    windows.odr.unlink()  # One on PATH doesn't count.
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "odr.exe").write_bytes(b"")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert odr.locate() is None
    monkeypatch.setattr(sys, "platform", "darwin")
    assert odr.locate() is None
    assert windows.ran == []  # Finding it never starts anything.


def test_the_catalog_is_hidden_where_windows_has_no_connectors(windows, monkeypatch):
    listed = {item["id"]: item for item in api.list_sources()["items"]}
    assert listed["windows"]["enabled"] is True and listed["windows"]["label"] == "Windows connectors"
    assert "windows" in catalogs.updatable()
    monkeypatch.setattr(sys, "platform", "linux")
    assert "windows" not in {item["id"] for item in api.list_sources()["items"]}
    assert "windows" not in catalogs.updatable()
    assert sources.SOURCES["windows"].search(sources.Search("owner", "")).rows == []


def test_connectors_are_listed_only_when_the_person_updates_and_never_changed(windows):
    assert search_catalog(query="files", sources=["windows"])["items"] == []  # Searching reads only what was kept.
    assert windows.ran == []
    catalogs.update("windows", wait=True)
    assert windows.ran == [["list"]]  # Read-only, and nothing else: never add, remove, configure or provision.
    assert catalogs.state("windows")["state"] == "done" and catalogs.state("windows")["entries"] == 2
    found = search_catalog(query="files", sources=["windows"])["items"]
    assert [row["name"] for row in found] == ["File Explorer"]
    assert found[0]["id"] == "mcp:windows:MicrosoftWindows.FileExplorer_cw5n1h2txyewy"
    assert {row["name"] for row in search_catalog(query="", sources=["windows"])["items"]} == {"File Explorer", "notes"}
    assert windows.ran == [["list"]]


def test_earlier_builds_list_under_mcp_and_a_failed_listing_keeps_nothing_new(windows):
    windows.outputs.clear()
    windows.outputs[("mcp", "list")] = json.dumps([LISTED["servers"][1]["server"]])  # The bare-list shape.
    catalogs.update("windows", wait=True)
    assert windows.ran == [["list"], ["mcp", "list"]]
    assert [row["identifier"] for row in odr.saved()] == ["Contoso.Notes_8wekyb3d8bbwe"]
    windows.outputs.clear()  # Now neither answers: the list from before stays.
    catalogs.update("windows", wait=True)
    assert catalogs.state("windows")["state"] == "failed"
    assert [row["identifier"] for row in odr.saved()] == ["Contoso.Notes_8wekyb3d8bbwe"]


def test_an_empty_listing_is_an_answer_even_when_the_older_command_is_gone(windows):
    windows.outputs[("list",)] = json.dumps({"servers": []})  # No connectors; ``mcp list`` no longer exists.
    catalogs.update("windows", wait=True)
    assert catalogs.state("windows")["state"] == "done" and catalogs.state("windows")["entries"] == 0
    assert windows.ran == [["list"], ["mcp", "list"]] and odr.saved() == []


def test_a_connector_connects_through_the_normal_plan_with_windows_own_proxy(windows):
    catalogs.update("windows", wait=True)
    row, reference = sources.catalog_entry("mcp:windows:Contoso.Notes_8wekyb3d8bbwe")
    assert row["source"] == "windows" and row["publisher"] == "Windows" and row["lifecycle"] == "available"
    plan = plans.compute(row, reference, intent="connect")
    assert [step["type"] for step in plan["steps"]][:1] == ["consent"] and plan["supported"] is True
    assert {"test", "access", "enable"} <= {step["type"] for step in plan["steps"]}
    assert plan["consent"]["runs_locally"] is True and plan["consent"]["destinations"] == []
    entry = reference["entry"]
    assert entry.install == {"transport": "stdio", "command": str(windows.odr),
                             "args": ["mcp", "run", "--proxy", "Contoso.Notes_8wekyb3d8bbwe"]}
    with pytest.raises(ValueError, match="invalid_connector"):
        odr.install(windows.odr, "--help")


def test_listings_take_only_local_connectors_with_plain_identifiers():
    found = odr.parse(json.dumps(LISTED))
    assert [row["identifier"] for row in found] == ["MicrosoftWindows.FileExplorer_cw5n1h2txyewy", "Contoso.Notes_8wekyb3d8bbwe"]
    assert found[1]["title"] == "notes"  # No title: the last part of its name.
    assert odr.parse("not json") is None and odr.parse(json.dumps({"servers": "x"})) is None
    assert odr.parse(json.dumps({"servers": []})) == []
    flat = odr.parse(json.dumps({"servers": [{"name": "a/b", "identifier": "A.B_1"}]}))
    assert [row["identifier"] for row in flat] == ["A.B_1"]
