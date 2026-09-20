import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { createElement } from 'react';
import PwaStatus from './PwaStatus';
import { PwaClient, type PwaEnvironment } from './client';

class FakeWorker extends EventTarget {
  state: ServiceWorkerState = 'installed';
  postMessage = vi.fn();
}

class FakeRegistration extends EventTarget {
  waiting: FakeWorker | null = null;
  installing: FakeWorker | null = null;
}

class FakeWorkers extends EventTarget {
  controller: unknown = {};
  registration = new FakeRegistration();
  register = vi.fn(async () => this.registration);
}

function fixture(options: { online?: boolean; active?: boolean } = {}) {
  let online = options.online ?? true;
  let active = options.active ?? true;
  const events = new EventTarget();
  const workers = new FakeWorkers();
  const reload = vi.fn();
  const environment: PwaEnvironment = {
    origin: 'https://row-bot.example',
    online: () => online,
    userActivation: () => active,
    reload,
    serviceWorker: workers,
    addEventListener: (type, listener) =>
      events.addEventListener(type, listener),
    removeEventListener: (type, listener) =>
      events.removeEventListener(type, listener),
  };
  return {
    environment,
    workers,
    reload,
    event(value: string | Event) {
      events.dispatchEvent(
        typeof value === 'string' ? new Event(value) : value,
      );
    },
    setOnline(value: boolean) {
      online = value;
    },
    setActive(value: boolean) {
      active = value;
    },
  };
}

it('registers only the fixed same-origin app-v2 worker and tracks connectivity', async () => {
  const value = fixture();
  const client = new PwaClient(value.environment);
  await client.start();
  expect(value.workers.register).toHaveBeenCalledWith(
    '/app-v2/service-worker.js',
    { scope: '/app-v2/', updateViaCache: 'none' },
  );
  expect(client.getSnapshot()).toMatchObject({
    phase: 'ready',
    online: true,
    updateAvailable: false,
  });
  value.setOnline(false);
  value.event('offline');
  expect(client.getSnapshot()).toMatchObject({
    phase: 'offline',
    online: false,
  });
  value.setOnline(true);
  value.event('online');
  expect(client.getSnapshot().phase).toBe('ready');
  client.dispose();
});

it('keeps registration denial nonfatal and observable', async () => {
  const value = fixture();
  value.workers.register.mockRejectedValueOnce(new DOMException('Denied'));
  const client = new PwaClient(value.environment);
  await expect(client.start()).resolves.toBeUndefined();
  expect(client.getSnapshot()).toMatchObject({
    phase: 'error',
    error: 'pwa_registration_unavailable',
  });
  client.dispose();
});

it('ignores a late registration result after its presentation owner is gone', async () => {
  const value = fixture();
  let resolve!: (registration: FakeRegistration) => void;
  value.workers.register.mockImplementationOnce(
    () =>
      new Promise<FakeRegistration>((done) => {
        resolve = done;
      }),
  );
  const client = new PwaClient(value.environment);
  const observed = vi.fn();
  client.subscribe(observed);
  const start = client.start();
  expect(client.getSnapshot().phase).toBe('registering');
  client.dispose();
  resolve(value.workers.registration);
  await start;
  expect(client.getSnapshot().phase).toBe('registering');
  expect(observed).toHaveBeenCalledTimes(1);
});

it('requires a direct user activation before a waiting worker can reload', async () => {
  const value = fixture({ active: false });
  const waiting = new FakeWorker();
  value.workers.registration.waiting = waiting;
  const client = new PwaClient(value.environment);
  await client.start();
  expect(client.getSnapshot()).toMatchObject({
    phase: 'update_available',
    updateAvailable: true,
  });
  expect(client.applyUpdate()).toBe('user_activation_required');
  expect(waiting.postMessage).not.toHaveBeenCalled();
  expect(value.reload).not.toHaveBeenCalled();
  value.setActive(true);
  expect(client.applyUpdate()).toBe('requested');
  expect(waiting.postMessage).toHaveBeenCalledWith({ type: 'SKIP_WAITING' });
  expect(value.reload).not.toHaveBeenCalled();
  value.workers.dispatchEvent(new Event('controllerchange'));
  expect(value.reload).toHaveBeenCalledTimes(1);
  client.dispose();
});

it('presents stable offline and explicit update controls', async () => {
  const value = fixture({ online: false });
  const waiting = new FakeWorker();
  value.workers.registration.waiting = waiting;
  const client = new PwaClient(value.environment);
  const user = userEvent.setup();
  render(createElement(PwaStatus, { client }));
  await waitFor(() =>
    expect(screen.getByTestId('pwa-status')).toHaveAttribute(
      'data-pwa-state',
      'offline',
    ),
  );
  expect(screen.getByText(/unsent draft stays/i)).toBeVisible();
  value.setOnline(true);
  value.event('online');
  expect(await screen.findByTestId('pwa-apply-update')).toBeVisible();
  await user.click(screen.getByTestId('pwa-apply-update'));
  expect(waiting.postMessage).toHaveBeenCalledTimes(1);
});

it('captures an install prompt in memory and invokes it only from activation', async () => {
  const value = fixture({ active: false });
  const client = new PwaClient(value.environment);
  await client.start();
  const prompt = Object.assign(new Event('beforeinstallprompt'), {
    prompt: vi.fn(async () => undefined),
    userChoice: Promise.resolve({ outcome: 'accepted' as const }),
  });
  value.event(prompt);
  expect(client.getSnapshot().installAvailable).toBe(true);
  expect(await client.requestInstall()).toBe('user_activation_required');
  expect(prompt.prompt).not.toHaveBeenCalled();
  value.setActive(true);
  expect(await client.requestInstall()).toBe('accepted');
  expect(prompt.prompt).toHaveBeenCalledTimes(1);
  expect(client.getSnapshot().installAvailable).toBe(false);
  client.dispose();
});
