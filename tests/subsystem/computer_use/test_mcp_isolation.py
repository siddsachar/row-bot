from __future__ import annotations

from row_bot.mcp_client import runtime


def test_private_cua_session_is_not_registered_in_generic_catalog() -> None:
    before = runtime.get_catalog_snapshot()
    private = runtime.PrivateMcpSession(command="never-started-cua", args=["mcp"], env={})
    after = runtime.get_catalog_snapshot()
    assert after == before
    assert all("cua" not in server.casefold() for server in after)
    private.close()
