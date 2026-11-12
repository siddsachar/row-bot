"""The "Continue with Railway" page (row-bot.ai/railway/) talks only to Railway and its relay.

Its flow runs against a fake Railway in `railway_page_runtime_test.cjs`; here, the page's own safety
contract: a strict Content-Security-Policy that lets it reach only Railway's sign-in and the relay, no
script, style, font or image from anywhere else (so no analytics), and no referrer.
"""

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
PAGE_DIR = ROOT / "docs" / "railway"


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.metas: list[dict[str, str]] = []
        self.scripts: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.images: list[dict[str, str]] = []
        self.anchors: list[dict[str, str]] = []
        self.inline_scripts: list[tuple[dict[str, str], str]] = []
        self.inline_styles = 0
        self._script: dict[str, str] | None = None
        self._script_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if "style" in values:
            self.inline_styles += 1
        if tag == "meta":
            self.metas.append(values)
        elif tag == "script":
            self.scripts.append(values)
            self._script, self._script_text = values, []
        elif tag == "link":
            self.links.append(values)
        elif tag == "img":
            self.images.append(values)
        elif tag == "a":
            self.anchors.append(values)
        elif tag == "style":
            self.inline_styles += 1

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            text = "".join(self._script_text).strip()
            if text:
                self.inline_scripts.append((self._script, text))
            self._script = None


def _page() -> PageParser:
    parser = PageParser()
    parser.feed((PAGE_DIR / "index.html").read_text(encoding="utf-8"))
    return parser


def _policy(parser: PageParser) -> dict[str, list[str]]:
    content = next(meta["content"] for meta in parser.metas if meta.get("http-equiv") == "Content-Security-Policy")
    directives = [part.split() for part in content.split(";") if part.strip()]
    return {name: values for name, *values in directives}


def _config(parser: PageParser) -> dict[str, str]:
    return json.loads(next(text for attrs, text in parser.inline_scripts if attrs.get("id") == "railway-config"))


def test_the_page_can_reach_only_railway_and_the_relay() -> None:
    parser = _page()
    policy = _policy(parser)
    config = _config(parser)
    assert policy["default-src"] == ["'none'"]
    assert policy["script-src"] == ["'self'"]
    assert policy["style-src"] == ["'self'"]
    assert policy["img-src"] == ["'self'"]
    assert policy["base-uri"] == ["'none'"]
    assert policy["form-action"] == ["'none'"]
    assert policy["connect-src"] == ["https://backboard.railway.com", config["relay"]]
    assert set(policy) == {"default-src", "script-src", "style-src", "img-src", "connect-src", "base-uri", "form-action"}
    assert config["redirectUri"] == "https://row-bot.ai/railway/"
    assert urlsplit(config["relay"]).scheme == "https" and not urlsplit(config["relay"]).path
    assert {"name": "referrer", "content": "no-referrer"} in parser.metas


def test_the_page_loads_nothing_from_elsewhere() -> None:
    """Its scripts, styles and images are its own files: no analytics, fonts or third-party code."""
    parser = _page()
    assert [attrs.get("type") for attrs, _ in parser.inline_scripts] == ["application/json"]
    assert parser.inline_styles == 0
    sources = [script["src"] for script in parser.scripts if "src" in script]
    sources += [link["href"] for link in parser.links if link.get("rel") in {"stylesheet", "icon"}]
    sources += [image["src"] for image in parser.images]
    assert sources
    for source in sources:
        parts = urlsplit(source)
        assert not parts.scheme and not parts.netloc, source
        assert (PAGE_DIR / parts.path).resolve().is_file(), source
    assert all(link.get("rel") in {"stylesheet", "icon", "canonical"} for link in parser.links)
    # The landing's own script loads analytics; this page doesn't use it.
    assert not any(Path(urlsplit(source).path).name == "site.js" for source in sources)
    assert all(image.get("width") and image.get("height") for image in parser.images)
    for anchor in parser.anchors:
        if anchor.get("target") == "_blank":
            assert {"noopener", "noreferrer"} <= set(anchor.get("rel", "").split()), anchor


def test_the_flow_against_a_fake_railway() -> None:
    """Sign-in, every state, the calls in order, and the token kept out of storage and addresses."""
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "docs" / "railway_page_runtime_test.cjs")],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
