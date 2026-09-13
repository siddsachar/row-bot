import type { Page } from '@playwright/test';

type CapturedDownload = {
  bytes: number[] | null;
  error: string;
  mimeType: string;
  name: string;
  size: number;
};

type DownloadCaptureState = {
  blobs: Map<string, Blob>;
  records: CapturedDownload[];
};

declare global {
  interface Window {
    __rowBotBrowserTestDownloads?: DownloadCaptureState;
  }
}

/**
 * Observe the exact Blob and filename presented to the browser download API.
 *
 * Playwright's Firefox driver does not emit a download event for the Blob URL
 * anchor used by the web client. The native click and application event
 * handlers still run; this hook records the browser input and prevents the
 * captured Blob from replacing the application page in Firefox.
 */
export async function captureBrowserDownload(
  page: Page,
  trigger: () => Promise<unknown>,
) {
  await page.evaluate(() => {
    const existing = window.__rowBotBrowserTestDownloads;
    if (existing) {
      existing.records.length = 0;
      return;
    }

    const state: DownloadCaptureState = { blobs: new Map(), records: [] };
    window.__rowBotBrowserTestDownloads = state;
    const createObjectURL = URL.createObjectURL.bind(URL);
    const revokeObjectURL = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (object: Blob | MediaSource) => {
      const url = createObjectURL(object);
      if (object instanceof Blob) state.blobs.set(url, object);
      return url;
    };
    URL.revokeObjectURL = (url: string) => {
      revokeObjectURL(url);
      queueMicrotask(() => state.blobs.delete(url));
    };

    const click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      const blob = state.blobs.get(this.href);
      if (blob && this.download) {
        const record: CapturedDownload = {
          bytes: null,
          error: '',
          mimeType: blob.type,
          name: this.download,
          size: blob.size,
        };
        state.records.push(record);
        void blob
          .arrayBuffer()
          .then((value) => {
            record.bytes = Array.from(new Uint8Array(value));
          })
          .catch((error: unknown) => {
            record.error =
              error instanceof Error ? error.message : 'read_failed';
          });

        // Firefox can treat a programmatic Blob download as a document
        // navigation under Playwright. Dispatch the same native click so the
        // application's handler and accessible control are exercised, while
        // cancelling only the browser's default download/navigation. The Blob
        // bytes and filename presented to that default action are asserted
        // below for every engine.
        const preventDefault = (event: MouseEvent) => event.preventDefault();
        this.addEventListener('click', preventDefault, { once: true });
      }
      return click.call(this);
    };
  });

  await trigger();
  await page.waitForFunction(() => {
    const state = window.__rowBotBrowserTestDownloads;
    const record = state?.records?.[0];
    return Boolean(record && (record.bytes || record.error));
  });
  const captured = await page.evaluate(
    () => window.__rowBotBrowserTestDownloads?.records[0],
  );
  if (!captured || captured.error || !captured.bytes)
    throw new Error(
      captured?.error || 'The browser download was not captured.',
    );
  if (captured.bytes.length !== captured.size)
    throw new Error('The captured browser download was incomplete.');
  return {
    bytes: Buffer.from(captured.bytes),
    mimeType: captured.mimeType,
    name: captured.name,
  };
}
