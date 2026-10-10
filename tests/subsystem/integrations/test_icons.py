"""App icons: bundled marks, letter avatars, and update-time rasters that are checked and re-encoded."""
import io

import pytest
from PIL import Image

from row_bot.integrations import apps, icons


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(icons.time, "sleep", lambda seconds: None)
    return tmp_path


def image(fmt: str, size=(512, 256), **options) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA" if fmt in {"PNG", "WEBP"} else "RGB", size, "#20a0e0").save(out, fmt, **options)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF"])
def test_raster_icons_are_reencoded_as_small_png_without_metadata(fmt):
    source = image(fmt, exif=b"Exif\x00\x00secret-camera") if fmt == "JPEG" else image(fmt)
    data = icons.reencode(source)
    with Image.open(io.BytesIO(data)) as result:
        assert result.format == "PNG" and max(result.size) == icons.SIZE and result.size == (64, 32)
        assert "exif" not in result.info
    assert b"secret-camera" not in data


@pytest.mark.parametrize("payload", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'  <?xml version="1.0"?><svg/>',
    b"<html><body>not an image</body></html>",
    b"GIF89a-truncated",
    b"%PDF-1.7 not an icon",
    b"\x00" * 64,
])
def test_svg_html_and_non_raster_payloads_are_refused(payload):
    with pytest.raises(ValueError, match="icon_refused"):
        icons.reencode(payload)


def test_oversized_and_bomb_images_are_refused_before_decoding():
    with pytest.raises(ValueError, match="icon_refused"):
        icons.reencode(b"\x89PNG" + b"0" * icons.MAX_BYTES)
    with pytest.raises(ValueError, match="icon_refused"):
        icons.reencode(image("PNG", size=(4096, 16)))  # Small file, too many pixels on one side.
    bmp = io.BytesIO()
    Image.new("RGB", (8, 8)).save(bmp, "BMP")
    with pytest.raises(ValueError, match="icon_refused"):
        icons.reencode(bmp.getvalue())  # Only PNG, JPEG and GIF are decoded.
    with pytest.raises(ValueError, match="icon_refused"):
        icons.reencode(image("WEBP"))  # WebP's decoder is left out on purpose.


def test_update_caches_at_most_its_quota_and_skips_failures(local, monkeypatch):
    fetched = []

    def download(url):
        fetched.append(url)
        if "bad" in url:
            return b"<svg/>"
        return image("PNG")
    urls = ["https://cdn.example.test/bad.svg.png"] + [f"https://cdn.example.test/{i}.png" for i in range(icons.PER_UPDATE + 5)]
    monkeypatch.setattr(icons, "_download", download)
    result = icons.cache_remote(urls + ["http://cdn.example.test/insecure.png"])
    assert result == {"cached": icons.PER_UPDATE - 1, "failed": 1}
    assert len(fetched) == icons.PER_UPDATE and "http://cdn.example.test/insecure.png" not in fetched
    monkeypatch.setattr(icons, "_download", lambda url: pytest.fail("cached or failed icons are fetched once"))
    again = icons.cache_remote(urls[:4])
    assert again == {"cached": 0, "failed": 0}  # The failed one waits a month.
    assert icons.entry_icon(None, urls[1], "Example") == icons.remote_id(urls[1])
    data, media = icons.render(icons.remote_id(urls[1]))
    assert media == "image/png" and data.startswith(b"\x89PNG")


def test_rendering_never_fetches_and_falls_back_to_a_letter(local, monkeypatch):
    monkeypatch.setattr(icons, "_download", lambda url: pytest.fail("fetched at render time"))
    assert icons.entry_icon(None, "https://cdn.example.test/never-cached.png", "weather now") == "letter:W"
    notion = apps.catalog()[0]["notion"]
    assert icons.entry_icon(notion, "https://cdn.example.test/x.png", "x") == "si:notion"
    svg, media = icons.render("si:notion")
    assert media == "image/svg+xml" and svg.startswith(b"<svg") and b"<script" not in svg
    assert icons.license_of("si:notion")["license"] == "CC0-1.0" and icons.license_of("letter:N") is None
    for bad in ("letter:ab", "letter:<", "si:missing", "cached:" + "f" * 32, "cached:../x", "file:///etc/passwd"):
        with pytest.raises(ValueError, match="not_found"):
            icons.render(bad)


def test_every_bundled_mark_is_plain_path_data_with_a_recorded_licence():
    marks = icons.marks()
    used = {app.icon[3:] for app in apps.catalog()[0].values() if app.icon}
    assert used <= set(marks)
    for slug, mark in marks.items():
        assert mark["license"] and mark["source"].startswith("https://github.com/simple-icons/simple-icons/")
        assert not set(mark["path"]) & set("<>\"'&;:()")


@pytest.mark.parametrize(("url", "namespace", "allowed"), [
    ("https://cdn.notion.com/logo.png", "com.notion", True),
    ("https://notion.com/logo.png", "com.notion", True),
    ("https://tracker.example.test/pixel.png?u=1", "com.notion", False),
    ("https://notion.com.evil.test/logo.png", "com.notion", False),
    ("https://avatars.githubusercontent.com/u/1", "io.github.someone", True),
    ("https://someone.github.io/logo.png", "io.github.someone", False),  # Pages can redirect anywhere.
    ("https://example.test/logo.png", "io.github.someone", False),
])
def test_registry_icons_are_fetched_only_from_their_publishers_own_domain(url, namespace, allowed):
    assert icons.publisher_host(url, namespace) is allowed
