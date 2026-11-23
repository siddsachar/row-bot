import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { OverlayProvider } from '../ui/overlays';
import App from './App';
import { FakeApi, type FakeAnswer, type FakeState } from './browser/fake-api';

/**
 * The console at `path` (inside /console) for vitest, with the browser
 * spec's FakeApi behind `fetch`. `answer` may answer a request first.
 */
export function renderConsole(
  path = '/people',
  state: FakeState = 'populated',
  answer?: (url: URL, method: string) => FakeAnswer | undefined,
) {
  const api = new FakeApi(state);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const url = new URL(input, 'https://org.example.test');
      const method = init.method ?? 'GET';
      const body =
        typeof init.body === 'string' ? JSON.parse(init.body) : undefined;
      const answered = answer?.(url, method);
      if (answered)
        api.calls.push({ method, path: url.pathname + url.search, body });
      const reply = answered ?? api.handle(method, url, body);
      return new Response(
        reply.body === undefined ? null : JSON.stringify(reply.body),
        { status: reply.status },
      );
    }),
  );
  render(
    <OverlayProvider>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </OverlayProvider>,
  );
  return api;
}

/** The body of the last `method path` request the console sent. */
export function sent(api: FakeApi, method: string, path: string) {
  return api.calls
    .filter((call) => call.method === method && call.path === path)
    .at(-1)?.body;
}
