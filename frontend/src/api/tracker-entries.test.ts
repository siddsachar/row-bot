import { afterEach, expect, it, vi } from 'vitest';
import {
  getTrackerEntries,
  type SessionProof,
} from '../../../contracts/client-platform/v1/typescript/client';

afterEach(() => {
  vi.unstubAllGlobals();
});

const proof: SessionProof = {
  client_session_id: crypto.randomUUID(),
  csrf_token: 'synthetic'.repeat(8),
};
const page = {
  schema_version: 1,
  tracker_id: 'water / 2',
  total: 1,
  items: [{ at: '2026-09-14T18:42:00', value: '8', note: null }],
};

function respond(body: object) {
  const fetcher = vi.fn(async (_url: string, _init: RequestInit) => ({
    ok: true,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

it('reads one tracker’s entries from its own encoded path', async () => {
  const fetcher = respond(page);
  await expect(getTrackerEntries('', proof, 'water / 2')).resolves.toEqual(
    page,
  );
  const [url, init] = fetcher.mock.calls[0];
  expect(new URL(url, 'http://h').pathname).toBe(
    '/api/v1/settings/tracker/water%20%2F%202/entries',
  );
  expect(init.method).toBe('GET');
});

it('refuses an entry page that carries anything more, such as a file path', async () => {
  respond({ ...page, path: 'C:/synthetic/tracker.db' });
  await expect(getTrackerEntries('', proof, 'water')).rejects.toBeDefined();
});
