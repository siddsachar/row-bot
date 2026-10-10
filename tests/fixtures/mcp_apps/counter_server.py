"""A synthetic MCP Apps server for tests and the browser journey: a counter with an interactive view.

``counter`` shows the view; ``increment`` only its view may call (visibility ``app``); ``reset`` only the
agent may call (``model``). The view also checks, and shows, that it can't read cookies, storage, the
Row-Bot page or Row-Bot's API. Nothing here leaves this computer.

Run: ``python tests/fixtures/mcp_apps/counter_server.py`` (stdio).
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

VIEW_URI = "ui://counter/view.html"
MEDIA_TYPE = "text/html;profile=mcp-app"
VIEW = """<!doctype html>
<html><head><meta charset="utf-8"><title>Counter</title>
<style>
  :root { color-scheme: light; }
  :root[data-theme="dark"] { color-scheme: dark; }
  body { font: 14px system-ui, sans-serif; margin: 12px; color: #1f2328; }
  :root[data-theme="dark"] body { color: #e6edf3; }
  button { font: inherit; padding: 4px 12px; }
  pre { font-size: 12px; opacity: .8; }
</style></head>
<body>
<p>Count: <strong id="count">…</strong></p>
<button id="more" type="button">Add one</button>
<pre id="isolation"></pre>
<script>
(() => {
  let next = 0;
  const pending = new Map();
  const post = (message) => parent.postMessage({ jsonrpc: '2.0', ...message }, '*');
  const request = (method, params) => new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, { resolve, reject });
    post({ id, method, params });
  });
  const size = () => post({ method: 'ui/notifications/size-changed',
    params: { height: Math.ceil(document.documentElement.getBoundingClientRect().height) } });
  const show = (value) => { document.getElementById('count').textContent = String(value); size(); };
  addEventListener('message', (event) => {
    if (event.source !== parent) return;
    const message = event.data;
    if (!message || message.jsonrpc !== '2.0') return;
    if (message.id !== undefined && !message.method) {
      const waiting = pending.get(message.id);
      if (waiting) {
        pending.delete(message.id);
        message.error ? waiting.reject(message.error) : waiting.resolve(message.result);
      }
      return;
    }
    if (message.method === 'ui/notifications/tool-result') show(message.params?.structuredContent?.count ?? '?');
    else if (message.method === 'ui/notifications/host-context-changed' && message.params?.theme)
      document.documentElement.dataset.theme = message.params.theme;
    else if (message.method === 'ui/resource-teardown') post({ id: message.id, result: {} });
    else if (message.id !== undefined) post({ id: message.id, error: { code: -32601, message: 'Method not found' } });
  });
  const probes = async () => {
    const found = [];
    try { void document.cookie; found.push('cookies: readable'); } catch { found.push('cookies: blocked'); }
    try { localStorage.setItem('probe', '1'); found.push('storage: readable'); } catch { found.push('storage: blocked'); }
    try { void parent.document.title; found.push('page: readable'); } catch { found.push('page: blocked'); }
    try { await fetch('/api/v1/handshake', { method: 'POST' }); found.push('row-bot: reachable'); }
    catch { found.push('row-bot: blocked'); }
    document.getElementById('isolation').textContent = found.join('\\n');
    size();
  };
  (async () => {
    const started = await request('ui/initialize', { protocolVersion: '2026-01-26',
      appInfo: { name: 'counter', version: '1.0.0' }, appCapabilities: { availableDisplayModes: ['inline'] } });
    document.documentElement.dataset.theme = started.hostContext?.theme || 'light';
    post({ method: 'ui/notifications/initialized', params: {} });
    await probes();
    document.getElementById('more').onclick = async () => {
      try {
        const result = await request('tools/call', { name: 'increment', arguments: { by: 1 } });
        show(result.structuredContent?.count ?? '?');
      } catch (error) {
        document.getElementById('count').textContent = 'not allowed: ' + (error.message || error);
        size();
      }
    };
  })();
})();
</script>
</body></html>
"""

server = FastMCP("fixture-counter")
_count = {"value": 0}


@server.resource(VIEW_URI, mime_type=MEDIA_TYPE, meta={"ui": {"prefersBorder": True}})
def view() -> str:
    return VIEW


@server.tool(meta={"ui": {"resourceUri": VIEW_URI}})
def counter(start: int = 0) -> dict[str, int]:
    """Show the counter, starting from a number."""
    _count["value"] = start
    return {"count": _count["value"]}


@server.tool(meta={"ui": {"resourceUri": VIEW_URI, "visibility": ["app"]}})
def increment(by: int = 1) -> dict[str, int]:
    """Add to the counter (its view's button)."""
    _count["value"] += by
    return {"count": _count["value"]}


@server.tool(meta={"ui": {"visibility": ["model"]}})
def reset() -> dict[str, int]:
    """Set the counter back to zero."""
    _count["value"] = 0
    return {"count": 0}


if __name__ == "__main__":
    server.run()
