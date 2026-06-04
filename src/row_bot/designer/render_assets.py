"""Designer asset rendering helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import pathlib
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from row_bot.designer.html_ops import ASSET_ID_ATTR, ASSET_KIND_ATTR, ASSET_LABEL_ATTR
from row_bot.designer.state import DesignerAsset, DesignerProject, DesignerReference
from row_bot.designer.storage import load_asset_bytes, load_reference_bytes, save_asset_bytes

_PUBLIC_ASSET_ID_ATTR = "data-asset-id"
_MISSING_ASSET_DATA_URI = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
_ASSET_PLACEHOLDER_RE = re.compile(r"__ASSET_([A-Za-z0-9._-]+)__", re.IGNORECASE)


def find_unresolved_asset_placeholders(html: str) -> list[str]:
    """Return unsupported __ASSET_<id>__ placeholder ids still present in HTML."""

    if not html or "__ASSET_" not in html:
        return []

    placeholders: list[str] = []
    seen: set[str] = set()
    for match in _ASSET_PLACEHOLDER_RE.finditer(html):
        asset_id = (match.group(1) or "").strip().lower()
        if asset_id and asset_id not in seen:
            seen.add(asset_id)
            placeholders.append(asset_id)
    return placeholders


def normalize_inline_image_sources(
    html: str,
    project: DesignerProject | None,
    *,
    default_asset_kind: str = "uploaded-image",
) -> tuple[str, bool]:
    """Persist inline image data URIs as project assets and rewrite them to asset:// refs."""

    if not html or project is None or "data:" not in html:
        return html, False

    soup = BeautifulSoup(html, "html.parser")
    changed = False

    for image in soup.find_all("img"):
        source = (image.get("src") or "").strip()
        if not source.startswith("data:"):
            continue

        decoded = _decode_data_uri(source)
        if decoded is None:
            continue
        mime_type, data = decoded

        asset_parent = image.find_parent(attrs={ASSET_ID_ATTR: True})
        asset_id = ""
        asset_kind = default_asset_kind
        label = (image.get("alt") or image.get("title") or "").strip()
        filename_seed = label or "image"

        if isinstance(asset_parent, Tag):
            asset_id = (
                (asset_parent.get(ASSET_ID_ATTR) or "").strip()
                or (asset_parent.get(_PUBLIC_ASSET_ID_ATTR) or "").strip()
            )
            asset_kind = (asset_parent.get(ASSET_KIND_ATTR) or default_asset_kind).strip() or default_asset_kind
            label = (asset_parent.get(ASSET_LABEL_ATTR) or label).strip()
            filename_seed = label or filename_seed

        if not asset_id:
            asset_id = (image.get(_PUBLIC_ASSET_ID_ATTR) or "").strip()

        asset = _upsert_project_asset(
            project,
            data=data,
            asset_kind=asset_kind,
            label=label or filename_seed,
            mime_type=mime_type,
            filename=_make_asset_filename(filename_seed, mime_type, asset_kind or "image"),
            asset_id=asset_id,
        )
        image["src"] = f"asset://{asset.id}"
        image[_PUBLIC_ASSET_ID_ATTR] = asset.id
        if isinstance(asset_parent, Tag):
            asset_parent[_PUBLIC_ASSET_ID_ATTR] = asset.id
        changed = True

    return (str(soup), True) if changed else (html, False)


def normalize_inline_media_sources(
    html: str,
    project: DesignerProject | None,
    *,
    default_asset_kind: str = "uploaded-image",
) -> tuple[str, bool]:
    """Media-aware wrapper around :func:`normalize_inline_image_sources`.

    Phase 2.1.B: videos typically arrive as ``<video src="asset://...">``
    since raw MP4 data URIs are impractical. This function therefore
    delegates to the image normalizer but is the forward-compatible entry
    point for future media kinds (audio, lottie, etc.).
    """
    return normalize_inline_image_sources(
        html,
        project,
        default_asset_kind=default_asset_kind,
    )


def normalize_project_inline_assets(project: DesignerProject | None) -> bool:
    """Migrate inline page image data URIs in a project to persisted assets."""

    if project is None:
        return False

    changed = False
    source_htmls = [page.html for page in project.pages]
    for page in project.pages:
        restored_html = restore_inline_asset_sources(page.html, source_htmls)
        normalized_html, page_changed = normalize_inline_image_sources(
            restored_html,
            project,
            default_asset_kind="uploaded-image",
        )
        normalized_html, asset_ref_changed = normalize_asset_reference_sources(normalized_html, project)
        if restored_html == page.html and not page_changed and not asset_ref_changed:
            continue
        page.html = normalized_html
        page.thumbnail_b64 = None
        changed = True

    brand = project.brand
    if brand and brand.logo_b64:
        logo_data = _decode_base64_payload(brand.logo_b64)
        if logo_data is None:
            brand.logo_b64 = None
            changed = True
        else:
            asset = _upsert_project_asset(
                project,
                data=logo_data,
                asset_kind="brand-logo",
                label=brand.logo_filename or "Brand logo",
                mime_type=brand.logo_mime_type or "image/png",
                filename=_make_asset_filename(
                    brand.logo_filename or "brand-logo",
                    brand.logo_mime_type or "image/png",
                    "brand-logo",
                ),
                asset_id=(brand.logo_asset_id or "brand-logo").strip(),
            )
            brand.logo_asset_id = asset.id
            brand.logo_filename = brand.logo_filename or asset.filename
            brand.logo_mime_type = asset.mime_type or brand.logo_mime_type
            brand.logo_b64 = None
            changed = True

    return changed


def resolve_project_image_sources(html: str, project: DesignerProject | None) -> str:
    """Resolve persisted Designer image references into self-contained data URIs."""

    if not html or project is None:
        return html
    if "<img" not in html.lower():
        return html
    if (
        not project.assets
        and not project.references
        and "ref-" not in html
        and "localhost:8080" not in html
        and "asset:" not in html
    ):
        return html

    soup = BeautifulSoup(html, "html.parser")
    asset_lookup = _build_asset_lookup(project.assets)
    lookup = _build_reference_lookup(project.references)
    changed = False

    for image in soup.find_all("img"):
        source = (image.get("src") or "").strip()
        if not source or source.startswith("data:"):
            continue

        asset = _match_asset(asset_lookup, source, image)
        if asset is not None:
            if asset.stored_name:
                data = load_asset_bytes(project.id, asset.stored_name)
                if data:
                    mime = (
                        asset.mime_type
                        or mimetypes.guess_type(asset.filename or asset.stored_name)[0]
                        or "application/octet-stream"
                    )
                    encoded = base64.b64encode(data).decode("ascii")
                    image["src"] = f"data:{mime};base64,{encoded}"
                    changed = True
                    continue
            image["src"] = _MISSING_ASSET_DATA_URI
            changed = True
            continue
        if source.startswith("asset:"):
            image["src"] = _MISSING_ASSET_DATA_URI
            changed = True
            continue

        reference = _match_reference(project, lookup, source)
        if reference is None or not _reference_is_image(reference) or not reference.stored_name:
            continue

        data = load_reference_bytes(project.id, reference.stored_name)
        if not data:
            continue

        mime = (
            reference.mime_type
            or mimetypes.guess_type(reference.name or reference.stored_name)[0]
            or "application/octet-stream"
        )
        encoded = base64.b64encode(data).decode("ascii")
        image["src"] = f"data:{mime};base64,{encoded}"
        changed = True

    return str(soup) if changed else html


def resolve_project_media_sources(html: str, project: DesignerProject | None) -> str:
    """Resolve persisted video/audio references to data URIs alongside images.

    Images still flow through :func:`resolve_project_image_sources`. For
    video/audio assets referenced as ``asset://<id>`` or by filename, this
    inlines the bytes as a ``data:`` URI so the srcdoc iframe preview can
    play them without a backing HTTP route. The missing-asset fallback is
    a silent no-op (the ``<video>`` tag will show broken-media chrome).
    """
    html = resolve_project_image_sources(html, project)
    if not html or project is None:
        return html
    lowered = html.lower()
    if "<video" not in lowered and "<audio" not in lowered:
        return html
    if not project.assets:
        return html

    soup = BeautifulSoup(html, "html.parser")
    asset_lookup = _build_asset_lookup(project.assets)
    changed = False

    for tag_name in ("video", "audio"):
        for media in soup.find_all(tag_name):
            # Resolve either the inline src or the first <source> child.
            target: Tag | None = media if media.get("src") else None
            if target is None:
                target = media.find("source")
            if target is None:
                continue
            source = (target.get("src") or "").strip()
            if not source or source.startswith("data:"):
                continue

            asset = _match_asset(asset_lookup, source, media)
            if asset is None:
                continue
            if not asset.stored_name:
                continue

            data = load_asset_bytes(project.id, asset.stored_name)
            if not data:
                continue

            mime = (
                asset.mime_type
                or mimetypes.guess_type(asset.filename or asset.stored_name)[0]
                or ("video/mp4" if tag_name == "video" else "audio/mpeg")
            )
            encoded = base64.b64encode(data).decode("ascii")
            target["src"] = f"data:{mime};base64,{encoded}"
            changed = True

            # If the video has an asset-wrapper with a poster asset id on
            # the asset record, resolve it into the <video poster="..."> attr.
            if tag_name == "video" and not media.get("poster") and asset.poster_asset_id:
                poster_asset = next(
                    (a for a in project.assets if a.id == asset.poster_asset_id),
                    None,
                )
                if poster_asset and poster_asset.stored_name:
                    poster_data = load_asset_bytes(project.id, poster_asset.stored_name)
                    if poster_data:
                        poster_mime = (
                            poster_asset.mime_type
                            or mimetypes.guess_type(
                                poster_asset.filename or poster_asset.stored_name
                            )[0]
                            or "image/png"
                        )
                        poster_encoded = base64.b64encode(poster_data).decode("ascii")
                        media["poster"] = f"data:{poster_mime};base64,{poster_encoded}"

    return str(soup) if changed else html


def restore_inline_asset_sources(html: str, source_htmls: list[str] | None) -> str:
    """Hydrate asset:// image references from inline assets already present in prior page HTML."""

    if not html or not source_htmls:
        return html

    lookup = _build_inline_asset_lookup(source_htmls)
    if not lookup:
        return html

    soup = BeautifulSoup(html, "html.parser")
    changed = False

    for image in soup.find_all("img"):
        source = (image.get("src") or "").strip()
        if source.startswith("data:"):
            continue

        candidates = _source_asset_candidates(source)
        public_asset_id = (image.get(_PUBLIC_ASSET_ID_ATTR) or "").strip().lower()
        if public_asset_id and public_asset_id not in candidates:
            candidates.append(public_asset_id)

        for candidate in candidates:
            resolved = lookup.get(candidate)
            if not resolved:
                continue
            image["src"] = resolved
            changed = True
            break

    return str(soup) if changed else html


def normalize_asset_reference_sources(
    html: str,
    project: DesignerProject | None,
) -> tuple[str, bool]:
    """Rewrite asset-like refs to canonical asset:// refs when backing assets exist."""

    if not html or project is None or not project.assets or "<img" not in html.lower():
        return html, False

    soup = BeautifulSoup(html, "html.parser")
    asset_lookup = _build_asset_lookup(project.assets)
    changed = False

    for image in soup.find_all("img"):
        source = (image.get("src") or "").strip()
        if not source or source.startswith("data:"):
            continue

        asset = _match_asset(asset_lookup, source, image)
        if asset is None:
            continue

        canonical_source = f"asset://{asset.id}"
        if source != canonical_source:
            image["src"] = canonical_source
            changed = True

        if (image.get(_PUBLIC_ASSET_ID_ATTR) or "").strip() != asset.id:
            image[_PUBLIC_ASSET_ID_ATTR] = asset.id
            changed = True

        asset_parent = image.find_parent(attrs={ASSET_ID_ATTR: True})
        if isinstance(asset_parent, Tag) and (asset_parent.get(_PUBLIC_ASSET_ID_ATTR) or "").strip() != asset.id:
            asset_parent[_PUBLIC_ASSET_ID_ATTR] = asset.id
            changed = True

    return (str(soup), True) if changed else (html, False)


def _build_reference_lookup(references: list[DesignerReference]) -> dict[str, DesignerReference]:
    lookup: dict[str, DesignerReference] = {}
    for reference in references:
        for candidate in _reference_candidates(reference):
            lookup.setdefault(candidate, reference)
    return lookup


def _build_asset_lookup(assets: list[DesignerAsset]) -> dict[str, DesignerAsset]:
    lookup: dict[str, DesignerAsset] = {}
    for asset in assets:
        for candidate in _asset_candidates(asset):
            lookup.setdefault(candidate, asset)
    return lookup


def _build_inline_asset_lookup(source_htmls: list[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for raw_html in source_htmls:
        if not raw_html or "data:" not in raw_html:
            continue
        soup = BeautifulSoup(raw_html, "html.parser")

        for block in soup.find_all(attrs={ASSET_ID_ATTR: True}):
            asset_id = (block.get(ASSET_ID_ATTR) or "").strip().lower()
            if not asset_id or asset_id in lookup:
                continue
            inline_src = _find_inline_image_source(block)
            if inline_src:
                lookup[asset_id] = inline_src

        for image in soup.find_all("img"):
            inline_src = (image.get("src") or "").strip()
            if not inline_src.startswith("data:"):
                continue
            for candidate in _inline_asset_candidates(image):
                lookup.setdefault(candidate, inline_src)

    return lookup


def _reference_candidates(reference: DesignerReference) -> set[str]:
    values = {
        (reference.id or "").strip().lower(),
        (reference.name or "").strip().lower(),
        (reference.stored_name or "").strip().lower(),
    }
    cleaned = {value for value in values if value}
    derived = set(cleaned)
    for value in cleaned:
        path_name = pathlib.PurePosixPath(value).name.lower()
        if path_name:
            derived.add(path_name)
            stem = pathlib.PurePosixPath(path_name).stem.lower()
            if stem:
                derived.add(stem)
    return derived


def _asset_candidates(asset: DesignerAsset) -> set[str]:
    values = {
        (asset.id or "").strip().lower(),
        (asset.label or "").strip().lower(),
        (asset.filename or "").strip().lower(),
        (asset.stored_name or "").strip().lower(),
    }
    cleaned = {value for value in values if value}
    derived = set(cleaned)
    for value in cleaned:
        path_name = pathlib.PurePosixPath(value).name.lower()
        if path_name:
            derived.add(path_name)
            stem = pathlib.PurePosixPath(path_name).stem.lower()
            if stem:
                derived.add(stem)
    return derived


def _inline_asset_candidates(image: Tag) -> list[str]:
    candidates = [
        (image.get(_PUBLIC_ASSET_ID_ATTR) or "").strip().lower(),
        (image.get("id") or "").strip().lower(),
    ]
    asset_parent = image.find_parent(attrs={ASSET_ID_ATTR: True})
    if isinstance(asset_parent, Tag):
        candidates.append((asset_parent.get(ASSET_ID_ATTR) or "").strip().lower())
        candidates.append((asset_parent.get("id") or "").strip().lower())
    return [candidate for candidate in candidates if candidate]


def _find_inline_image_source(tag: Tag) -> str:
    if tag.name == "img":
        source = (tag.get("src") or "").strip()
        return source if source.startswith("data:") else ""

    image = tag.find("img")
    if image is None:
        return ""
    source = (image.get("src") or "").strip()
    return source if source.startswith("data:") else ""


def _extract_asset_reference_id(source: str) -> str:
    if not source:
        return ""
    placeholder_match = _ASSET_PLACEHOLDER_RE.fullmatch(source.strip())
    if placeholder_match is not None:
        return (placeholder_match.group(1) or "").strip().lower()
    parsed = urlparse(source)
    if parsed.scheme != "asset":
        return ""
    candidate = (parsed.netloc or parsed.path or "").strip().strip("/").lower()
    return candidate


def _source_asset_candidates(source: str) -> list[str]:
    raw = (source or "").strip().lower()
    if not raw:
        return []

    candidates: list[str] = []
    asset_ref = _extract_asset_reference_id(raw)
    if asset_ref:
        candidates.append(asset_ref)

    if _ASSET_PLACEHOLDER_RE.fullmatch(raw):
        return candidates

    if raw.startswith(("data:", "http://", "https://", "file://")):
        return candidates
    if re.match(r"^[a-z]:[\\/]", raw):
        return candidates

    parsed = urlparse(raw)
    if parsed.scheme and parsed.scheme != "asset":
        return candidates

    direct = (parsed.path or raw).strip().strip("/")
    if not direct:
        return candidates

    path_name = pathlib.PurePosixPath(direct).name.lower()
    stem = pathlib.PurePosixPath(path_name).stem.lower()
    for value in (direct, path_name, stem):
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _make_asset_filename(source_name: str, mime_type: str, fallback_stem: str) -> str:
    candidate = pathlib.Path((source_name or fallback_stem).strip()).name or fallback_stem
    suffix = pathlib.Path(candidate).suffix.lower()
    if not suffix:
        suffix = mimetypes.guess_extension(mime_type or "") or ".bin"
        if suffix == ".jpe":
            suffix = ".jpg"
        candidate = f"{candidate}{suffix}"
    return candidate


def _upsert_project_asset(
    project: DesignerProject,
    *,
    data: bytes,
    asset_kind: str,
    label: str,
    mime_type: str,
    filename: str,
    asset_id: str = "",
) -> DesignerAsset:
    asset = next((existing for existing in project.assets if existing.id == asset_id), None) if asset_id else None
    if asset is None:
        asset = DesignerAsset(id=asset_id) if asset_id else DesignerAsset()
        project.assets.append(asset)

    asset.kind = asset_kind
    asset.label = label
    asset.mime_type = mime_type
    asset.filename = filename
    asset.size_bytes = len(data)
    asset.sha256 = hashlib.sha256(data).hexdigest()
    asset.stored_name = save_asset_bytes(project.id, asset.id, filename, data)
    return asset


def _decode_base64_payload(payload: str) -> bytes | None:
    normalized = "".join((payload or "").split())
    if not normalized:
        return None
    if re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", normalized) is None:
        return None

    remainder = len(normalized) % 4
    if remainder == 1:
        return None
    if remainder:
        normalized += "=" * (4 - remainder)

    try:
        return base64.b64decode(normalized, validate=False)
    except (binascii.Error, ValueError):
        return None


def _decode_data_uri(source: str) -> tuple[str, bytes] | None:
    if not source.startswith("data:") or "," not in source:
        return None
    header, payload = source.split(",", 1)
    mime_type = (header[5:].split(";", 1)[0] or "application/octet-stream").strip()
    if ";base64" in header:
        decoded = _decode_base64_payload(payload)
        if decoded is None:
            return None
        return mime_type, decoded
    return mime_type, payload.encode("utf-8")


def _match_asset(
    lookup: dict[str, DesignerAsset],
    source: str,
    image: Tag,
) -> DesignerAsset | None:
    candidates = _source_asset_candidates(source)

    inline_candidates = [
        (image.get(_PUBLIC_ASSET_ID_ATTR) or "").strip().lower(),
        (image.get("id") or "").strip().lower(),
    ]
    asset_parent = image.find_parent(attrs={ASSET_ID_ATTR: True})
    if isinstance(asset_parent, Tag):
        inline_candidates.extend([
            (asset_parent.get(ASSET_ID_ATTR) or "").strip().lower(),
            (asset_parent.get(_PUBLIC_ASSET_ID_ATTR) or "").strip().lower(),
            (asset_parent.get("id") or "").strip().lower(),
        ])

    for candidate in inline_candidates:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if not candidate:
            continue
        asset = lookup.get(candidate)
        if asset is not None:
            return asset
    return None


def _match_reference(
    project: DesignerProject,
    lookup: dict[str, DesignerReference],
    source: str,
) -> DesignerReference | None:
    candidates: list[str] = []
    parsed = urlparse(source)
    if parsed.scheme and parsed.netloc:
        normalized = parsed.path or ""
    else:
        normalized = source
    normalized = normalized.split("?", 1)[0].split("#", 1)[0].strip().lower()

    if normalized:
        candidates.extend([
            normalized,
            normalized.lstrip("/"),
        ])
        path_name = pathlib.PurePosixPath(normalized).name.lower()
        if path_name:
            candidates.append(path_name)
            stem = pathlib.PurePosixPath(path_name).stem.lower()
            if stem:
                candidates.append(stem)

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        reference = lookup.get(candidate)
        if reference is not None:
            return reference

    for candidate in seen:
        if "ref-" not in candidate:
            continue
        for reference in project.references:
            if reference.id and reference.id.lower() in candidate:
                return reference
    return None


def _reference_is_image(reference: DesignerReference) -> bool:
    if reference.kind == "image":
        return True
    return (reference.mime_type or "").lower().startswith("image/")
