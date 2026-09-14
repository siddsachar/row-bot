from __future__ import annotations

import importlib.util
import json
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import pytest


RUNNER_PATH = Path(__file__).parent / "browser" / "settings_parity" / "run_visual.py"
SPEC = importlib.util.spec_from_file_location(
    "row_bot_settings_parity_runner", RUNNER_PATH
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


inventory = _load_module(
    "row_bot_settings_parity_inventory",
    RUNNER_PATH.with_name("build_inventory.py"),
)
evidence = _load_module(
    "row_bot_settings_parity_evidence",
    RUNNER_PATH.with_name("build_evidence.py"),
)


def _temp_directory() -> tempfile.TemporaryDirectory[str]:
    scratch = Path(__file__).parents[1] / ".tmp" / "settings-parity-unit"
    scratch.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=scratch)


def _write_png(path: Path, width: int, height: int) -> None:
    def chunk(kind: bytes, value: bytes) -> bytes:
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
        )

    row = b"\x00" + (b"\x00\x00\x00" * width)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_real_data_capture_requires_explicit_authorization() -> None:
    with pytest.raises(runner.CaptureSafetyError, match="requires --authorize"):
        runner._canonical_real_data_dir(runner.AUTHORIZED_REAL_DATA, False)


def test_real_data_capture_rejects_any_other_directory() -> None:
    with pytest.raises(
        runner.CaptureSafetyError, match="canonical configured directory"
    ):
        runner._canonical_real_data_dir(
            Path(r"D:\not-the-row-bot-data-directory"), True
        )


def test_manifest_diff_separates_ephemeral_and_meaningful_changes() -> None:
    before = {
        "files": [
            {"path": "settings.json", "size": 1, "mtime_ns": 1, "sha256": "a"},
            {"path": "logs/row_bot.log", "size": 1, "mtime_ns": 1, "sha256": "a"},
        ]
    }
    after = {
        "files": [
            {"path": "settings.json", "size": 2, "mtime_ns": 2, "sha256": "b"},
            {"path": "logs/row_bot.log", "size": 2, "mtime_ns": 2, "sha256": "b"},
        ]
    }

    report = runner._manifest_changes(before, after)

    assert [row["path"] for row in report["changes"]] == [
        "logs/row_bot.log",
        "settings.json",
    ]
    assert [row["path"] for row in report["meaningful"]] == ["settings.json"]


def test_page_targets_preserve_required_order_and_surface_ownership() -> None:
    nicegui, react = runner._targets(None)

    assert [target.name for target in nicegui] == list(runner.NICEGUI_PAGES)
    assert [target.name for target in react] == list(runner.REACT_PAGES)
    assert len(nicegui) == 16
    assert len(react) == 18
    assert react[3].route == "/app-v2/settings/wiki"
    assert react[5].route == "/app-v2/settings/goals"


def test_page_filter_keeps_cross_surface_page_selection() -> None:
    requested = runner._parse_pages("Providers,Goals")
    nicegui, react = runner._targets(requested)

    assert [target.name for target in nicegui] == ["Providers"]
    assert [target.name for target in react] == ["Providers", "Goals"]


@pytest.mark.parametrize(
    ("url", "method", "allowed", "reason"),
    [
        ("http://127.0.0.1:49100/api/v1/settings", "GET", True, "loopback read"),
        ("http://localhost:49100/api/v1/handshake", "POST", True, "observational post"),
        (
            "http://127.0.0.1:49100/api/client-error",
            "POST",
            False,
            "diagnostic request",
        ),
        ("http://127.0.0.1:49100/api/v1/settings", "PATCH", False, "non-read request"),
        ("https://example.com/runtime.js", "GET", False, "external"),
    ],
)
def test_browser_request_policy_is_explicit(
    url: str,
    method: str,
    allowed: bool,
    reason: str,
) -> None:
    assert runner._request_policy(url, method) == (allowed, reason)


def test_scroll_positions_overlap_and_include_boundaries_once() -> None:
    assert runner._scroll_positions(1800, 900) == [0, 820, 900]
    assert runner._scroll_positions(900, 900) == [0]
    tiny_client = runner._scroll_positions(100, 0)
    assert tiny_client[:3] == [0, 1, 2]
    assert tiny_client[-1] == 99
    assert tiny_client == sorted(set(tiny_client))


def test_react_scroll_capture_prefers_the_settings_pane() -> None:
    nicegui, react = runner._targets({"providers"})

    assert runner._scroll_owner_selector(nicegui[0]) == ""
    assert runner._scroll_owner_selector(react[0]) == ".settings-shell-body"


def test_axe_snapshot_uses_evaluation_not_script_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def __init__(self) -> None:
            self.scripts: list[str] = []

        def evaluate(self, script: str, argument=None):
            self.scripts.append(script)
            if "window.axe.run" in script:
                return {
                    "testEngine": {"name": "axe-core"},
                    "testEnvironment": {},
                    "violations": [],
                    "incomplete": [],
                    "passes": [{"id": "one"}],
                }
            return None

    with _temp_directory() as directory:
        axe_source = Path(directory) / "axe.min.js"
        axe_source.write_text("window.axe = window.axe || {};", encoding="utf-8")
        monkeypatch.setattr(runner, "AXE_SOURCE", axe_source)
        page = FakePage()
        result = runner._axe_snapshot(page, "main")

    assert result["pass_count"] == 1
    assert page.scripts[0] == "window.axe = window.axe || {};"
    assert "window.axe.run" in page.scripts[1]
    assert not hasattr(page, "add_script_tag")


def test_secret_masks_ignore_controls_inside_closed_disclosures() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.selectors: list[str] = []

        def locator(self, selector: str) -> str:
            self.selectors.append(selector)
            return selector

    page = FakePage()

    masks = runner._sensitive_masks(page)

    assert masks == page.selectors
    assert masks
    assert all(selector.endswith(":visible") for selector in masks)
    assert 'input[type="password"]:visible' in masks


def test_inventory_marks_only_semantic_react_peers_as_matched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _temp_directory() as directory:
        root = Path(directory)
        nicegui = root / "nicegui"
        react = root / "react"
        nicegui.mkdir()
        react.mkdir()
        monkeypatch.setattr(
            inventory,
            "OWNERS",
            {"Providers": ("owner.py", 12, "build", "Providers.tsx")},
        )
        monkeypatch.setattr(inventory, "TESTS", {"Providers": "focused-test"})
        (nicegui / "providers-desktop-dom.json").write_text(
            json.dumps(
                {
                    "controls": [
                        {
                            "index": 0,
                            "tag": "button",
                            "type": "button",
                            "label": "Refresh",
                            "visible": True,
                            "rect": {},
                        },
                        {
                            "index": 1,
                            "tag": "button",
                            "type": "button",
                            "label": "Delete",
                            "visible": True,
                            "rect": {},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        (react / "providers-desktop-dom.json").write_text(
            json.dumps(
                {
                    "controls": [
                        {
                            "index": 0,
                            "tag": "button",
                            "type": "button",
                            "label": "Refresh",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = root / "inventory.md"
        count = inventory.build(nicegui, react, output)
        rendered = output.read_text(encoding="utf-8")

    assert count == 2
    assert "Providers-refresh-button-01" in rendered
    assert "unreviewed=0" in rendered
    assert "| matched |" in rendered
    assert "no semantic control peer" in rendered
    assert "| blocked |" in rendered
    assert f"{nicegui}/providers-desktop-dom.json" in rendered
    assert f"{react}/providers-desktop-dom.json" in rendered


def test_inventory_identifies_native_control_adaptations_and_implementation_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _temp_directory() as directory:
        root = Path(directory)
        nicegui = root / "nicegui"
        react = root / "react"
        nicegui.mkdir()
        react.mkdir()
        monkeypatch.setattr(
            inventory,
            "OWNERS",
            {"Providers": ("owner.py", 12, "build", "Providers.tsx")},
        )
        monkeypatch.setattr(inventory, "TESTS", {"Providers": "focused-test"})
        (nicegui / "providers-desktop-dom.json").write_text(
            json.dumps(
                {
                    "controls": [
                        {
                            "index": 0,
                            "tag": "div",
                            "role": "switch",
                            "label": "Enabled",
                            "visible": True,
                            "rect": {},
                        },
                        {
                            "index": 1,
                            "tag": "input",
                            "type": "checkbox",
                            "label": "",
                            "visible": False,
                            "rect": {},
                        },
                        {
                            "index": 2,
                            "tag": "div",
                            "role": "combobox",
                            "label": "Provider",
                            "visible": True,
                            "rect": {},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        (react / "providers-desktop-dom.json").write_text(
            json.dumps(
                {
                    "controls": [
                        {
                            "index": 0,
                            "tag": "input",
                            "type": "checkbox",
                            "label": "Enabled",
                        },
                        {
                            "index": 1,
                            "tag": "select",
                            "type": "select-one",
                            "label": "Provider\nLocal\nCloud",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = root / "inventory.md"
        count = inventory.build(nicegui, react, output)
        rendered = output.read_text(encoding="utf-8")

    assert count == 3
    assert "matched-with-accessibility-adaptation=2" in rendered
    assert "mapped-to-existing-automatic-owner=1" in rendered
    assert (
        "non-visible native input owned by the adjacent labelled Quasar control"
        in rendered
    )
    assert "blocked=" not in rendered


def test_verification_treats_explicit_blocked_controls_as_reviewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _temp_directory() as directory:
        root = Path(directory)
        monkeypatch.setattr(evidence, "EVIDENCE", root)
        monkeypatch.setattr(
            evidence,
            "PAGES",
            ((1, "Providers", "providers", "owner"),),
        )
        (root / "control-inventory.md").write_text(
            "Inventory status: blocked=1, matched=2, unreviewed=0.\n",
            encoding="utf-8",
        )
        for relative in (
            "candidate/react/providers-desktop-full.png",
            "candidate/react/providers-phone-full.png",
            "reference/nicegui/providers-desktop-full.png",
            "reference/nicegui/providers-phone-full.png",
        ):
            _write_png(root / relative, 10, 20)
        checklist = evidence._build_verification_checklist(
            "candidate",
            {
                "records": [{} for _ in range(68)],
                "failed": 0,
                "meaningful_data_changes": [],
            },
            [{"inspection": "reviewed"}],
            {
                "providers": {"result": "verified"},
                "_checklist": {},
            },
        )

    assert (
        "[x] Control inventory has zero unreviewed controls; every matched, "
        "adapted, automatic-owner, or blocked disposition is explicit."
    ) in checklist


def test_evidence_gallery_and_inspection_manifest_are_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _temp_directory() as directory:
        root = Path(directory)
        monkeypatch.setattr(evidence, "EVIDENCE", root)
        monkeypatch.setattr(
            evidence,
            "PAGES",
            (
                (1, "Providers", "providers", "owner"),
                (2, "Goals", None, "React-only route"),
            ),
        )
        _write_png(root / "reference/nicegui/providers-desktop-full.png", 10, 20)
        _write_png(root / "before/react/providers-desktop-full.png", 30, 40)
        _write_png(root / "before/react/goals-desktop-full.png", 30, 40)
        markdown, gallery = evidence._build_gallery("before")
        inspection, records = evidence._build_inspection_log("before", {})

    assert "No exact NiceGUI Settings owner" in markdown
    assert "React · before" in gallery
    assert len(records) == 3
    assert all(
        record["inspection"] == "pending original-size inspection" for record in records
    )
    assert "Automated enumeration is not visual inspection" in inspection
    assert {(record["width"], record["height"]) for record in records} == {
        (10, 20),
        (30, 40),
    }
