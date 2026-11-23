import type { Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { FakeApi, type FakeAnswer, type FakeState } from './fake-api';

// The built console, served the way the Company Server serves it: its two
// documents and assets with the server's CSP, everything else from FakeApi.
export const ORIGIN = 'https://org.example.test';
const BUILD = resolve(import.meta.dirname, '../../../dist-console');
const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";
const TYPES: Record<string, string> = {
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.woff2': 'font/woff2',
  '.png': 'image/png',
  '.html': 'text/html',
};

/**
 * Open the console at `path` with FakeApi in `state`. `answer` may answer a
 * request first (a failing load); `delay` holds every API answer that long.
 */
export async function openConsole(
  page: Page,
  path = '/console/people',
  state: FakeState = 'populated',
  {
    answer,
    delay = 0,
    page: served = 'console.html',
  }: {
    answer?: (url: URL) => FakeAnswer | undefined;
    delay?: number;
    /** The document the server sends for a page (a member gets console-not-admin.html). */
    page?: string;
  } = {},
) {
  const api = new FakeApi(state);
  const violations: string[] = [];
  page.on('console', (message) => {
    // The one refusal the strict CSP makes on purpose: the dialogs' scroll
    // lock stylesheet (Radix), which the console doesn't need: its page
    // never scrolls the document itself.
    if (
      message.type() === 'error' &&
      !message.text().startsWith('Applying inline style violates')
    )
      violations.push(message.text());
  });
  await page.route(`${ORIGIN}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.startsWith('/console')) {
      const file = url.pathname.startsWith('/console/assets/')
        ? url.pathname.slice('/console/'.length)
        : served;
      return route.fulfill({
        status: 200,
        headers: {
          'Content-Type': TYPES[file.slice(file.lastIndexOf('.'))] ?? '',
          'Content-Security-Policy': CSP,
        },
        body: readFileSync(resolve(BUILD, file)),
      });
    }
    const body = request.postData();
    const reply =
      answer?.(url) ??
      api.handle(request.method(), url, body ? JSON.parse(body) : undefined);
    if (delay) await new Promise((done) => setTimeout(done, delay));
    return route.fulfill({
      status: reply.status,
      contentType: 'application/json',
      body: reply.body === undefined ? '' : JSON.stringify(reply.body),
    });
  });
  await page.goto(`${ORIGIN}${path}`);
  return { api, violations };
}
