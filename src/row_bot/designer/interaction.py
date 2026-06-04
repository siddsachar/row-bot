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


def inject_bridge_js(html: str) -> str:
    """Inject the interaction bridge JS into page HTML.

    Inserts before </body> if present, otherwise appends.
    """
    if "</body>" in html.lower():
        # Insert before </body>
        idx = html.lower().rfind("</body>")
        return html[:idx] + BRIDGE_JS + html[idx:]
    return html + BRIDGE_JS


# ═══════════════════════════════════════════════════════════════════════
# PARENT-SIDE MESSAGE LISTENER  (registered once per preview)
# ═══════════════════════════════════════════════════════════════════════

def get_parent_listener_js(callback_id: str) -> str:
    """Return JS to register a window message listener that calls back into Python.

    The callback_id is the NiceGUI element ID used for emitting events.
    """
    return f"""
    (function() {{
        if (window.__rowBotDesignerListener) return;
        window.__rowBotDesignerListener = true;

        window.addEventListener('message', function(e) {{
            var data = e.data;
            if (!data || !data.type) return;
            // Forward to NiceGUI via custom event on the document
            if (data.type === 'element-click' || data.type === 'text-edit' ||
                data.type === 'edit-start' || data.type === 'edit-cancel') {{
                // Emit to NiceGUI backend via the global emitEvent helper
                emitEvent('{callback_id}', {{msgType: data.type, detail: data.detail || {{}}}});
            }}
        }});
    }})();
    """


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
