"""Designer — interactive iframe bridge for click feedback and inline editing.

Injects JavaScript into the preview iframe to enable:
  1. Hover highlight on elements
  2. Click-to-select with element info sent via postMessage
  3. Double-click text to edit inline (contenteditable)
  4. Text edits sent back via postMessage for HTML patching

The parent listener is registered via NiceGUI's ui.run_javascript().
"""

from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup, Tag

from row_bot.brand import APP_BRAND_ACCENT

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# BRIDGE JS  (injected into iframe srcdoc)
# ═══════════════════════════════════════════════════════════════════════

BRIDGE_JS = r"""
<script>
(function() {
    // Avoid double-init if srcdoc is set multiple times
    if (window.__rowBotBridge) return;
    window.__rowBotBridge = true;

    // ── State ─────────────────────────────────────────────────────
    var selectedEl = null;
    var editingEl = null;
    var highlightOutline = '2px solid rgba(37,99,235,0.6)';
    var selectOutline = '2px solid #2563EB';
    var origOutlineMap = new WeakMap();

    // ── Helpers ───────────────────────────────────────────────────
    function getXPath(el) {
        if (el === document.body) return '/html/body';
        var parts = [];
        while (el && el.nodeType === 1) {
            var idx = 1;
            var sib = el.previousSibling;
            while (sib) {
                if (sib.nodeType === 1 && sib.tagName === el.tagName) idx++;
                sib = sib.previousSibling;
            }
            parts.unshift(el.tagName.toLowerCase() + '[' + idx + ']');
            el = el.parentNode;
        }
        return '/' + parts.join('/');
    }

    function isEditable(el) {
        if (!el || el.nodeType !== 1) return false;
        var tag = el.tagName.toLowerCase();
        // Never treat structural roots or media as text-editable.
        if (['html','body','head','script','style','img','video','audio',
             'iframe','svg','canvas','input','textarea','select','picture',
             'source'].indexOf(tag) >= 0) return false;
        // Known text-bearing tags are always editable.
        var known = ['h1','h2','h3','h4','h5','h6','p','span','a','li','td','th',
                     'label','figcaption','blockquote','button','dt','dd',
                     'strong','em','b','i','small','code','pre','caption',
                     'summary','figcaption'];
        if (known.indexOf(tag) >= 0) return true;
        // Leaf text containers (e.g. a <div> holding only a label/email/etc.)
        // are also editable so template copy wrapped in div/section still
        // accepts double-click-to-edit.
        if (el.children && el.children.length === 0) {
            var txt = (el.textContent || '').trim();
            return txt.length > 0;
        }
        return false;
    }

    function getElementInfo(el) {
        var rect = el.getBoundingClientRect();
        var assetRoot = el.closest('[data-row-bot-id]');
        var elementRoot = el.closest('[data-row-bot-element-id]');
        return {
            tag: el.tagName.toLowerCase(),
            text: (el.textContent || '').substring(0, 200),
            className: el.className || '',
            id: el.id || '',
            assetId: assetRoot ? assetRoot.getAttribute('data-row-bot-id') || '' : '',
            assetKind: assetRoot ? assetRoot.getAttribute('data-row-bot-kind') || '' : '',
            elementId: elementRoot ? elementRoot.getAttribute('data-row-bot-element-id') || '' : '',
            xpath: getXPath(el),
            rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height}
        };
    }

    function clearSelection() {
        if (selectedEl && selectedEl !== editingEl) {
            selectedEl.style.outline = origOutlineMap.get(selectedEl) || '';
        }
        selectedEl = null;
    }

    function selectElement(el) {
        clearSelection();
        selectedEl = el;
        if (!origOutlineMap.has(el)) {
            origOutlineMap.set(el, el.style.outline || '');
        }
        el.style.outline = selectOutline;
    }

    // ── Hover highlight ───────────────────────────────────────────
    document.addEventListener('mouseover', function(e) {
        var el = e.target;
        if (el === document.body || el === document.documentElement) return;
        if (el === editingEl || el === selectedEl) return;
        if (!origOutlineMap.has(el)) {
            origOutlineMap.set(el, el.style.outline || '');
        }
        el.style.outline = highlightOutline;
    }, true);

    document.addEventListener('mouseout', function(e) {
        var el = e.target;
        if (el === selectedEl || el === editingEl) return;
        el.style.outline = origOutlineMap.get(el) || '';
    }, true);

    // ── Click → select + send info ───────────────────────────────
    document.addEventListener('click', function(e) {
        if (editingEl) return;  // don't interfere with editing
        e.preventDefault();
        e.stopPropagation();
        var el = e.target;
        if (el === document.body || el === document.documentElement) {
            clearSelection();
            return;
        }
        selectElement(el);
        window.parent.postMessage({
            type: 'element-click',
            detail: getElementInfo(el)
        }, '*');
    }, true);

    // ── Keyboard shortcuts outside inline editing ─────────────────
    document.addEventListener('keydown', function(e) {
        if (editingEl) return;
        if (e.repeat) return;
        if (!(e.ctrlKey || e.metaKey)) return;

        var key = (e.key || '').toLowerCase();
        if (key !== 'z') return;

        e.preventDefault();
        e.stopPropagation();
        window.parent.postMessage({
            type: e.shiftKey ? 'designer-redo-shortcut' : 'designer-undo-shortcut'
        }, '*');
    }, true);

    // ── Double-click → inline text edit ──────────────────────────
    document.addEventListener('dblclick', function(e) {
        var el = e.target;
        if (!isEditable(el)) return;
        e.preventDefault();
        e.stopPropagation();

        // Start editing
        editingEl = el;
        var oldHTML = el.innerHTML;
        el.setAttribute('contenteditable', 'true');
        el.style.outline = '2px solid __ROW_BOT_BRAND_ACCENT__';
        el.style.outlineOffset = '2px';
        el.focus();

        window.parent.postMessage({
            type: 'edit-start',
            detail: getElementInfo(el)
        }, '*');

        // On blur → finish editing
        function finishEdit() {
            el.removeEventListener('blur', finishEdit);
            el.removeEventListener('keydown', onKey);
            el.removeAttribute('contenteditable');
            el.style.outline = origOutlineMap.get(el) || '';
            el.style.outlineOffset = '';
            editingEl = null;

            var newHTML = el.innerHTML;
            if (newHTML !== oldHTML) {
                window.parent.postMessage({
                    type: 'text-edit',
                    detail: {
                        xpath: getXPath(el),
                        tag: el.tagName.toLowerCase(),
                        oldText: oldHTML,
                        newText: newHTML,
                        elementInfo: getElementInfo(el)
                    }
                }, '*');
            } else {
                window.parent.postMessage({type: 'edit-cancel'}, '*');
            }
        }

        function onKey(ke) {
            if (ke.key === 'Escape') {
                el.innerHTML = oldHTML;
                el.blur();
            } else if (ke.key === 'Enter' && !ke.shiftKey) {
                ke.preventDefault();
                el.blur();
            }
        }

        el.addEventListener('blur', finishEdit);
        el.addEventListener('keydown', onKey);
    }, true);

})();
</script>
"""
BRIDGE_JS = BRIDGE_JS.replace("__ROW_BOT_BRAND_ACCENT__", APP_BRAND_ACCENT)


def inject_bridge_js(html: str, *, preview_id: str = "", revision: str = "",
                     capability: str = "") -> str:
    """Inject the interaction bridge JS into page HTML.

    Inserts before </body> if present, otherwise appends.
    """
    if not preview_id or not revision or not capability:
        return html
    identity = json.dumps({"previewId": preview_id, "revision": revision,
                           "capability": capability}).replace("<", "\\u003c")
    bridge_js = BRIDGE_JS.replace("window.parent.postMessage(", "sendToOwner(")
    bridge_js = bridge_js.replace("(function() {", "(function() {\n"
        f"const identity = {identity};\n"
        "function sendToOwner(message) { window.parent.postMessage("
        "Object.assign({}, identity, message), '*'); }\n", 1)
    if "</body>" in html.lower():
        # Insert before </body>
        idx = html.lower().rfind("</body>")
        return html[:idx] + bridge_js + html[idx:]
    return html + bridge_js


# ═══════════════════════════════════════════════════════════════════════
# PARENT-SIDE MESSAGE LISTENER  (registered once per preview)
# ═══════════════════════════════════════════════════════════════════════

def get_parent_listener_js(callback_id: str, *, iframe_id: str = "") -> str:
    """Return JS to register a window message listener that calls back into Python.

    The callback_id is the NiceGUI element ID used for emitting events.
    """
    if not iframe_id:
        return ""  # No ambient global receiver is a safe default.
    return f"""
    (function() {{
        const frameId = {json.dumps(iframe_id)};
        const callbackId = {json.dumps(callback_id)};
        window.__rowBotDesignerListeners ||= new Map();
        const previous = window.__rowBotDesignerListeners.get(frameId);
        if (previous) previous();
        let observer = null;
        function cleanup() {{
            window.removeEventListener('message', listener);
            if (observer) observer.disconnect();
            window.__rowBotDesignerListeners.delete(frameId);
        }}
        function listener(e) {{
            const frame = document.getElementById(frameId);
            const bridge = getElement(callbackId);
            if (!frame || !bridge) {{
                cleanup();
                return;
            }}
            if (e.source !== frame.contentWindow || e.origin !== 'null') return;
            var data = e.data;
            if (!data || typeof data !== 'object' || Array.isArray(data)) return;
            if (data.previewId !== frameId || data.revision !== frame.dataset.previewRevision ||
                !data.capability || data.capability !== frame.dataset.previewCapability) return;
            if (!['element-click','text-edit','edit-start','edit-cancel',
                  'designer-undo-shortcut','designer-redo-shortcut'].includes(data.type)) return;
            if (Object.keys(data).some(k => !['previewId','revision','capability','type','detail'].includes(k))) return;
            if (data.detail !== undefined && (!data.detail || typeof data.detail !== 'object' || Array.isArray(data.detail))) return;
            let size; try {{ size = JSON.stringify(data).length; }} catch (_) {{ return; }}
            if (size > 16384) return;
            const event = new Event('bridge_msg', {{bubbles:true}});
            event.msgType = data.type;
            event.detail = data.detail || {{}};
            event.previewId = data.previewId;
            event.revision = data.revision;
            event.capability = data.capability;
            bridge.dispatchEvent(event);
        }}
        window.__rowBotDesignerListeners.set(frameId, cleanup);
        window.addEventListener('message', listener);
        observer = new MutationObserver(() => {{
            if (!document.getElementById(frameId) || !getElement(callbackId)) cleanup();
        }});
        observer.observe(document.body, {{childList:true, subtree:true}});
    }})();
    """


def validate_bridge_event(data: object, *, preview_id: str, revision: str,
                          capability: str) -> bool:
    """Validate the exact current bounded authoring event before invoking edits."""
    if not isinstance(data, dict) or set(data) - {
        "msgType", "detail", "previewId", "revision", "capability",
    }:
        return False
    if (not capability or data.get("previewId") != preview_id
            or data.get("revision") != revision or data.get("capability") != capability):
        return False
    detail = data.get("detail", {})
    if not isinstance(detail, dict):
        return False
    try:
        if len(json.dumps(data)) > 16384:
            return False
    except (TypeError, ValueError):
        return False
    kind = data.get("msgType")
    if kind in {"designer-undo-shortcut", "designer-redo-shortcut", "edit-cancel"}:
        return not detail
    if kind == "text-edit":
        return (set(detail) <= {"xpath", "tag", "oldText", "newText", "elementInfo"}
                and all(isinstance(detail.get(k), str) for k in ("xpath", "tag", "oldText", "newText"))
                and detail["xpath"].startswith("/html")
                and isinstance(detail.get("elementInfo", {}), dict))
    if kind in {"element-click", "edit-start"}:
        return (set(detail) <= {"tag", "text", "className", "id", "assetId", "assetKind", "elementId", "xpath", "rect"}
                and all(isinstance(detail.get(k), str) for k in ("tag", "xpath"))
                and detail["xpath"].startswith("/html")
                and all(isinstance(v, str) for k, v in detail.items() if k != "rect")
                and isinstance(detail.get("rect", {}), dict))
    return False


# ═══════════════════════════════════════════════════════════════════════
# HTML PATCHING  (apply inline text edits to page HTML)
# ═══════════════════════════════════════════════════════════════════════

_XPATH_PART_RE = re.compile(r"(?P<tag>[a-zA-Z0-9_-]+)(?:\[(?P<index>\d+)\])?")


def _find_tag_by_xpath(soup: BeautifulSoup, xpath: str) -> Tag | None:
    """Resolve the absolute XPath emitted by the preview bridge."""

    if not xpath.startswith("/"):
        return None

    current: Tag | BeautifulSoup = soup
    for part in [segment for segment in xpath.split("/") if segment]:
        match = _XPATH_PART_RE.fullmatch(part)
        if match is None:
            return None
        tag_name = match.group("tag").lower()
        index = int(match.group("index") or "1") - 1
        children = [child for child in current.children if isinstance(child, Tag) and child.name == tag_name]
        if index < 0 or index >= len(children):
            return None
        current = children[index]
    return current if isinstance(current, Tag) else None


def _replace_tag_inner_html(tag: Tag, new_text: str) -> None:
    """Replace a tag's inner HTML while preserving the outer element."""

    fragment_soup = BeautifulSoup(new_text, "html.parser")
    container = fragment_soup.body or fragment_soup
    tag.clear()
    nodes = list(container.contents)
    if not nodes and new_text:
        tag.append(new_text)
        return
    for node in nodes:
        tag.append(node)


def _normalized_html_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _matches_old_content(tag: Tag, tag_name: str, old_text: str) -> bool:
    """Check whether a candidate tag matches the bridge's original content."""

    if tag.name != tag_name:
        return False
    if tag.decode_contents() == old_text:
        return True

    normalized_old = _normalized_html_text(old_text)
    if _normalized_html_text(tag.decode_contents()) == normalized_old:
        return True

    old_plain_text = BeautifulSoup(old_text, "html.parser").get_text(" ", strip=True)
    return bool(old_plain_text) and _normalized_html_text(tag.get_text(" ", strip=True)) == _normalized_html_text(old_plain_text)

def patch_html_text(html: str, xpath: str, tag: str,
                    old_text: str, new_text: str) -> str:
    """Apply a text edit from the inline editor to the page HTML.

    Tries the exact XPath from the preview bridge first, then falls back to
    tag/content matching before using a final string replacement.
    """
    soup = BeautifulSoup(html, "html.parser")
    target = _find_tag_by_xpath(soup, xpath) if xpath else None
    if target is not None and target.name == tag:
        _replace_tag_inner_html(target, new_text)
        return str(soup)

    for candidate in soup.find_all(tag):
        if _matches_old_content(candidate, tag, old_text):
            _replace_tag_inner_html(candidate, new_text)
            return str(soup)

    # Fallback: simple string replacement of old_text → new_text within the HTML
    if old_text in html:
        return html.replace(old_text, new_text, 1)

    logger.warning("Could not patch HTML: tag=%s, old_text=%.50s", tag, old_text)
    return html
