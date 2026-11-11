"""Bound credentials reach actual Hatch media scopes without wire persistence."""

# ruff: noqa: F811 -- reuse the canonical isolated Hatch owner fixture.
import json

from row_bot.providers.media_auth import CapturedMediaAuth, current_media_auth
from row_bot.tools import image_gen_tool as image
from tests.helpers import buddy_art
from tests.subsystem.buddy.test_client_hatch import hatch_owner  # noqa: F401


def test_hatch_passes_exact_auth_to_each_media_scope_and_never_saves_credentials(
    hatch_owner, monkeypatch
):
    r = hatch_owner
    seen = []

    def captured(kind, selection):
        provider = selection.split("/", 1)[0]
        return CapturedMediaAuth(
            selection,
            provider,
            "synthetic-secret-" + kind,
            "identity-" + kind,
            "https://api.openai.com/v1"
            if provider == "openai"
            else "https://api.x.ai/v1",
        )

    def edit(prompt, image_source="last", **kwargs):
        bound = current_media_auth("openai")
        seen.append(("image", bound.credential))
        image._save_image_to_disk(buddy_art.b64(buddy_art.answer(prompt)))

    monkeypatch.setattr(image, "_edit_image", edit)
    result = r.start(validate_provider=captured)
    assert result.status == "completed" and result.completed_clips == 7
    # One image model paints the character and every expression; no video scope.
    assert seen == [("image", "synthetic-secret-image")] * 7
    assert current_media_auth() is None
    for path in r.root.rglob("*.json"):
        assert "synthetic-secret" not in path.read_text()
    assert "synthetic-secret" not in json.dumps(result.__dict__)


def test_invalid_auth_is_rejected_before_provider_started_checkpoint(hatch_owner):
    result = hatch_owner.start(validate_provider=lambda *args: object())
    assert result.status == "failed" and not result.selected and not hatch_owner.calls
    assert "image_provider_started" not in [item.stage for item in hatch_owner.progress]


def test_account_capture_rejection_is_before_provider_effect_and_preserves_previous_pack(
    hatch_owner,
):
    r = hatch_owner
    r.config.save_buddy_config({"pack_id": "glyph"})
    before = r.config._BUDDY_CONFIG_PATH.read_bytes()

    def deny(*args):
        raise PermissionError("synthetic account revoked")

    result = r.start(validate_provider=deny)
    assert result.status == "failed" and not r.calls and not result.selected
    assert r.config._BUDDY_CONFIG_PATH.read_bytes() == before
