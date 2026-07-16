from __future__ import annotations

from row_bot.tools.browser_tool import _consequential_browser_target


def test_submit_send_delete_payment_upload_and_download_are_gated() -> None:
    assert _consequential_browser_target({}, submit=True)
    for label in ("Send", "Delete account", "Pay now", "Publish", "Grant access"):
        assert _consequential_browser_target({"label": label})
    assert _consequential_browser_target({"type": "file"})
    assert _consequential_browser_target({"download": True})
    assert not _consequential_browser_target({"label": "Next page"})
