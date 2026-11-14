import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it, vi } from 'vitest';

// The real public/service-worker.js, run against a fake worker scope.
const source = readFileSync(resolve('public/service-worker.js'), 'utf8');
const ORIGIN = 'https://row-bot.example';

type Listener = (event: Record<string, unknown>) => void;

function worker(windows: Array<Record<string, unknown>> = []) {
  const listeners = new Map<string, Listener>();
  const scope = {
    location: new URL(`${ORIGIN}/app-v2/service-worker.js`),
    addEventListener: (type: string, listener: Listener) =>
      listeners.set(type, listener),
    registration: { showNotification: vi.fn().mockResolvedValue(undefined) },
    clients: {
      matchAll: vi.fn().mockResolvedValue(windows),
      openWindow: vi.fn().mockResolvedValue(null),
    },
  };
  new Function('self', 'caches', 'fetch', 'Response', 'URL', source)(
    scope,
    {},
    vi.fn(),
    Response,
    URL,
  );
  return {
    scope,
    async fire(type: string, event: Record<string, unknown>) {
      let waited: Promise<unknown> = Promise.resolve();
      listeners.get(type)!({
        ...event,
        waitUntil: (promise: Promise<unknown>) => {
          waited = promise;
        },
      });
      await waited;
    },
  };
}

const pushOf = (value: unknown) => ({
  data: value === undefined ? null : { json: () => value },
});

it('shows the title and line a push carries, and keeps the place it is about', async () => {
  const sw = worker();
  await sw.fire(
    'push',
    pushOf({
      title: 'Approval needed',
      body: 'Send email to Sam?',
      path: '/app-v2/conversations/chat-1',
    }),
  );
  expect(sw.scope.registration.showNotification).toHaveBeenCalledWith(
    'Approval needed',
    expect.objectContaining({
      body: 'Send email to Sam?',
      tag: 'row-bot:/app-v2/conversations/chat-1',
      data: { path: '/app-v2/conversations/chat-1' },
    }),
  );
});

it('falls back to the generic words for a push with nothing readable in it', async () => {
  for (const event of [
    pushOf(undefined),
    { data: { json: () => JSON.parse('not json') } },
    pushOf({ title: '  ', body: 'ignored' }),
  ]) {
    const sw = worker();
    await sw.fire('push', event);
    expect(sw.scope.registration.showNotification).toHaveBeenCalledWith(
      'Row-Bot',
      expect.objectContaining({
        body: 'Row-Bot is waiting for you.',
        data: { path: '/app-v2/' },
      }),
    );
  }
});

it('keeps text short and never opens a place outside Row-Bot', async () => {
  const sw = worker();
  await sw.fire(
    'push',
    pushOf({
      title: 'T'.repeat(200),
      body: 'B'.repeat(500),
      path: 'https://elsewhere.example/app-v2/',
    }),
  );
  const [title, options] = sw.scope.registration.showNotification.mock
    .calls[0] as [string, { body: string; data: { path: string } }];
  expect(title).toHaveLength(60);
  expect(options.body).toHaveLength(120);
  expect(options.data.path).toBe('/app-v2/');
});

it('opens the place in a new window, or takes an open Row-Bot window there', async () => {
  const notification = (path: string) => ({
    notification: { close: vi.fn(), data: { path } },
  });
  const fresh = worker();
  await fresh.fire(
    'notificationclick',
    notification('/app-v2/conversations/chat-1'),
  );
  expect(fresh.scope.clients.openWindow).toHaveBeenCalledWith(
    `${ORIGIN}/app-v2/conversations/chat-1`,
  );

  const open = {
    url: `${ORIGIN}/app-v2/`,
    focus: vi.fn(),
    navigate: vi.fn().mockResolvedValue(null),
  };
  open.focus.mockResolvedValue(open);
  const running = worker([open]);
  await running.fire(
    'notificationclick',
    notification('/app-v2/conversations/chat-1'),
  );
  expect(open.focus).toHaveBeenCalled();
  expect(open.navigate).toHaveBeenCalledWith(
    `${ORIGIN}/app-v2/conversations/chat-1`,
  );
  expect(running.scope.clients.openWindow).not.toHaveBeenCalled();
});
