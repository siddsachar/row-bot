from scripts.docs.collect_inventory import (
    collect_cli_options,
    collect_environment,
    collect_settings_controls,
)


def test_remote_access_cli_options_are_in_generated_inventory() -> None:
    rows = collect_cli_options()
    commands = {row["command"] for row in rows}

    assert "row-bot serve" in commands
    assert "row-bot access invite" in commands
    assert "row-bot access list" in commands
    assert "row-bot access revoke" in commands
    assert "row-bot access revoke-all" in commands
    assert "row-bot access doctor" in commands
    assert any(
        row["command"] == "row-bot serve" and row["option"] == "--public-url"
        for row in rows
    )
    assert any(
        row["command"] == "row-bot access invite" and row["option"] == "--layout"
        for row in rows
    )


def test_remote_access_environment_is_in_generated_inventory() -> None:
    variables = {row["variable"] for row in collect_environment()}

    assert {
        "ROW_BOT_DEPLOYMENT_MODE",
        "ROW_BOT_PUBLIC_URL",
        "ROW_BOT_ALLOWED_HOSTS",
        "ROW_BOT_TRUSTED_PROXY_CIDRS",
        "ROW_BOT_UNTRUSTED_FORWARDED_ACTION",
        "ROW_BOT_WORKERS",
        "ROW_BOT_SECRETS_DIR",
        "ROW_BOT_BROWSER_HEADLESS",
    } <= variables


def test_settings_inventory_uses_the_react_remote_access_page() -> None:
    access_rows = [
        row for row in collect_settings_controls() if row["page_id"] == "access"
    ]

    assert {"connect", "devices", "remote-access", "tunnel"} <= {
        row["anchor"] for row in access_rows
    }
    assert all(
        row["app_route"] == f"/app-v2/settings/access#{row['anchor']}"
        and row["source"] == f"frontend/src/features/settings/model.ts#{row['anchor']}"
        for row in access_rows
    )
    assert {row["docs_route"] for row in access_rows} == {
        "/docs/operations/remote-access"
    }
