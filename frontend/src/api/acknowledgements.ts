/** One trailing acknowledgement per observer. Rendering never waits on its HTTP latency. */
export const ACK_RETIRE_TIMEOUT_MS = 3000;
export class Acknowledgements {
  private latest: string | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private sending = false;
  private pending: Promise<void> | null = null;
  private closed = false;
  private request: AbortController | null = null;
  private closing: Promise<void> | null = null;
  private readonly cancel = () => {
    void this.close(true);
  };
  private last = -Infinity;
  constructor(
    private readonly send: (
      cursor: string,
      signal: AbortSignal,
    ) => Promise<unknown>,
    private readonly failed: (error: unknown) => void,
    private readonly now: () => number = () => performance.now(),
    private readonly parent?: AbortSignal,
  ) {
    if (parent?.aborted) this.closed = true;
    else parent?.addEventListener('abort', this.cancel, { once: true });
  }
  offer(cursor: string): void {
    if (this.closed) return;
    this.latest = cursor;
    this.schedule();
  }
  private schedule(): void {
    if (this.closed || this.sending || this.timer || this.latest === null)
      return;
    this.timer = setTimeout(
      () => {
        this.timer = null;
        this.pending = this.flush();
      },
      Math.max(0, 1000 - (this.now() - this.last)),
    );
  }
  private async flush(): Promise<void> {
    if (this.closed || this.latest === null) return;
    const cursor = this.latest;
    this.latest = null;
    this.sending = true;
    this.last = this.now();
    const request = new AbortController();
    this.request = request;
    let abort!: () => void;
    const cancelled = new Promise<never>((_resolve, reject) => {
      abort = () =>
        reject(new DOMException('Acknowledgement retired', 'AbortError'));
      request.signal.addEventListener('abort', abort, { once: true });
    });
    try {
      // Race the signal as well as passing it: a transport ignoring abort must
      // not retain the observer's retirement or surface a late failure.
      await Promise.race([this.send(cursor, request.signal), cancelled]);
    } catch (error) {
      if (!this.closed) this.failed(error);
    } finally {
      request.signal.removeEventListener('abort', abort);
      this.request = null;
      this.sending = false;
      this.schedule();
    }
  }
  close(immediate = false): Promise<void> {
    this.closed = true;
    this.latest = null;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (immediate) this.request?.abort();
    if (this.closing) return this.closing;
    if (!this.sending || !this.pending) {
      this.parent?.removeEventListener('abort', this.cancel);
      return Promise.resolve();
    }
    // Preserve issued ACK ordering on normal resets, but bound stalled HTTP.
    const deadline = setTimeout(
      () => this.request?.abort(),
      ACK_RETIRE_TIMEOUT_MS,
    );
    this.closing = this.pending.finally(() => {
      clearTimeout(deadline);
      this.parent?.removeEventListener('abort', this.cancel);
    });
    return this.closing;
  }
}
