from __future__ import annotations

import pathlib
import tomllib

import pytest


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]


def test_project_entrypoint_points_to_launcher() -> None:
    data = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["scripts"]["row-bot"] == "row_bot.launcher:main"


def test_linux_installer_contract_preserves_user_data_and_verifies_dependencies() -> None:
    build_script = pathlib.Path("installer/build_linux_app.sh").read_text(encoding="utf-8")
    install_script = pathlib.Path("installer/install-linux.sh").read_text(encoding="utf-8")

    assert "scripts/verify_runtime_dependencies.py" in build_script
    assert "User data in ~/.row-bot was left untouched." in build_script
    assert "LAUNCH_CMD=\"row-bot\"" in install_script
    assert "mktemp -d" in install_script


def test_macos_installer_uses_native_tray_host() -> None:
    build_script = pathlib.Path("installer/build_mac_app.sh").read_text(encoding="utf-8")
    host_source = pathlib.Path("installer/macos/RowBotTrayHost.m").read_text(encoding="utf-8")

    assert 'HOST_SOURCE="$SCRIPT_DIR/macos/RowBotTrayHost.m"' in build_script
    assert "xcrun clang" in build_script
    assert "-fobjc-arc" in build_script
    assert "-fblocks" in build_script
    assert "-framework Cocoa" in build_script
    assert "-mmacosx-version-min=11.0" in build_script
    assert 'cat > "$MACOS_DIR/row-bot"' not in build_script
    assert '"$MACOS_DIR/row-bot" --self-test' in build_script
    assert "<key>LSUIElement</key>" in build_script
    assert "<true/>" in build_script.split("<key>LSUIElement</key>", 1)[1].split("<key>", 1)[0]

    assert "NSStatusBar" in host_source
    assert "statusItemWithLength" in host_source
    assert '@"launcher.py", @"--no-tray", @"--native"' in host_source
    assert "PYTHONDONTWRITEBYTECODE" in host_source
    assert "launcher_state.json" in host_source
    assert "window_pid" in host_source
    assert "--self-test" in host_source


def test_macos_installer_verify_smokes_native_host() -> None:
    workflow = pathlib.Path(".github/workflows/installer-verify.yml").read_text(encoding="utf-8")

    assert 'APP_EXEC="$APP_PATH/Contents/MacOS/row-bot"' in workflow
    assert 'file "$APP_EXEC" | grep -q "Mach-O"' in workflow
    assert 'plutil -lint "$APP_PATH/Contents/Info.plist"' in workflow
    assert '"$APP_EXEC" --self-test' in workflow


def test_ci_declares_subsystem_and_smoke_lanes() -> None:
    ci = pathlib.Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "scripts/run_test_matrix.py contract-subsystem" in ci
    assert "scripts/run_test_matrix.py pr" in ci
    assert "scripts/run_test_matrix.py legacy-parity" not in ci
    assert "migrated-subsystem-coverage" in ci
    assert "scripts/smoke_app.py --port 8090 --timeout 120" in ci
