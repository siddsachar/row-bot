"""The composer's attachment tiles: a small, re-encoded picture of an image."""
from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

MARKER = "fixture-private-caption"


def _upload(client, headers, conversation: str, name: str, data: bytes) -> str:
    response = client.post(
        "/api/v1/uploads",
        params={"conversation_id": conversation, "name": name},
        content=data,
        headers={**headers, "Idempotency-Key": str(uuid4()), "X-Command-Id": str(uuid4()),
                 "X-Content-Sha256": hashlib.sha256(data).hexdigest(),
                 "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    return response.json()["attachment_ref"]


def _conversation(client, headers) -> str:
    return _command(client, headers, "conversation.create", {"title": "Tiles"}).json()["conversation_id"]


def _jpeg(size: tuple[int, int], *, orientation: int = 1) -> bytes:
    exif = Image.Exif()
    exif[0x010E] = MARKER  # ImageDescription
    exif[0x0112] = orientation
    output = BytesIO()
    Image.new("RGB", size, "teal").save(output, format="JPEG", exif=exif)
    return output.getvalue()


def _png(size: tuple[int, int], mode: str = "RGBA") -> bytes:
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("Comment", MARKER)
    output = BytesIO()
    Image.new(mode, size).save(output, format="PNG", pnginfo=info)
    return output.getvalue()


def test_image_thumbnail_is_a_small_re_encoded_inert_picture(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        for name, data, expected in (("photo.jpg", _jpeg((1200, 800)), (160, 107)),
                                     ("shot.png", _png((90, 300)), (48, 160))):
            reference = _upload(client, headers, conversation, name, data)
            response = client.get(f"/api/v1/attachments/{reference}/thumbnail", headers=headers)
            assert response.status_code == 200, response.text
            assert response.headers["content-type"] == "image/png"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
            assert response.headers["content-disposition"] == "inline"
            # Decoded and drawn again: no original byte run (or its metadata) is served.
            assert MARKER.encode() not in response.content
            assert data[-64:] not in response.content
            with Image.open(BytesIO(response.content)) as thumbnail:
                assert thumbnail.format == "PNG"
                assert thumbnail.size == expected


def test_image_thumbnail_is_upright_as_the_camera_meant(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        # A landscape sensor frame tagged "rotate 90°" is a portrait photo.
        reference = _upload(client, headers, conversation, "portrait.jpg", _jpeg((320, 160), orientation=6))
        response = client.get(f"/api/v1/attachments/{reference}/thumbnail", headers=headers)
        assert response.status_code == 200, response.text
        with Image.open(BytesIO(response.content)) as thumbnail:
            assert thumbnail.size == (80, 160)


def test_thumbnail_refuses_files_that_are_not_pictures(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        text = _upload(client, headers, conversation, "notes.txt", b"plain words")
        broken = _upload(client, headers, conversation, "broken.jpg", b"\xff\xd8\xff" + b"not a picture" * 20)
        for reference in (text, broken, f"{conversation}:{uuid4()}"):
            missing = client.get(f"/api/v1/attachments/{reference}/thumbnail", headers=headers)
            assert missing.status_code == 404, missing.text
            assert missing.json()["code"] == "not_found"


def test_thumbnail_never_decodes_an_image_bigger_than_a_camera_photo(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        # 64 megapixels and one row: small as a file, far too big to draw for a tile.
        huge = _upload(client, headers, conversation, "poster.png", _png((8000, 8001), mode="1"))
        refused = client.get(f"/api/v1/attachments/{huge}/thumbnail", headers=headers)
        assert refused.status_code == 413, refused.text
        assert refused.json()["code"] == "payload_too_large"


def test_thumbnail_has_the_attachment_download_access_check(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        other = _conversation(client, headers)
        reference = _upload(client, headers, conversation, "photo.jpg", _jpeg((64, 64)))
        path = f"/api/v1/attachments/{reference}/thumbnail"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={**headers, "X-CSRF-Token": "forged"}).status_code in {401, 403}
        # A reference names its own conversation's file; another conversation's
        # name for the same file finds nothing.
        attachment_id = reference.rsplit(":", 1)[1]
        moved = client.get(f"/api/v1/attachments/{other}:{attachment_id}/thumbnail", headers=headers)
        assert moved.status_code == 404
        assert client.get(path, headers=headers).status_code == 200
