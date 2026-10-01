/**
 * A page inside a pywebview window. `window.pywebview` arrives late, but the
 * embedding engines expose their message channels from the first script:
 * WebView2 (`chrome.webview`) and WKWebView/WebKitGTK (`webkit.messageHandlers
 * .jsBridge`, the handler pywebview registers). Browsers have neither.
 */
export function pywebviewHost(target: Window): boolean {
  const host = target as Window & {
    chrome?: { webview?: unknown };
    webkit?: { messageHandlers?: { jsBridge?: unknown } };
  };
  return (
    'pywebview' in target ||
    Boolean(host.chrome?.webview) ||
    Boolean(host.webkit?.messageHandlers?.jsBridge)
  );
}
