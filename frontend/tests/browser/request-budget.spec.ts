import type { Page, Request } from '@playwright/test';
import { expect, test, writeEvidence } from './evidence';
import {
  composer,
  fixtureState,
  newConversation,
  releaseProducer,
  type FixtureCall,
} from './unified-helpers';

/*
 * The client's background reads stay within a budget. Opening the app reads
 * what the first screen shows first and the rest a moment later; a reply
 * reads the sidebar's list once per burst of changes the list shows.
 *
 * Each number is a measured count plus a small margin. A deliberate raise
 * edits the number here, so a reviewer sees it.
 */

// API requests from opening the app on a conversation until it is idle:
// measured 34-35 (35 before: the deferred reads still come, a moment later),
// then 39: Learning is on from the start (O195) and reads its state and the
// open chat's Teach state (two reads, both within the first screen's budget).
const OPEN_BUDGET = 41;
// Of those, the requests sent before the first read the first screen doesn't
// show: measured 19-21. Before, Proactive and Monitor went 4th, ahead of the
// open conversation (the spec's order checks fail there).
const FIRST_SCREEN_BUDGET = 24;
// API requests during one scripted reply (four model steps, each with a tool
// step and a picture or video), from Send until idle: measured 40-42 locally
// (45 before). A slower CI runner can split the same live updates into one
// more batch, which re-reads the conversation and its workspace once more and
// acks twice more: 47 seen on #966 with no change to what the client asks for.
// The margin covers that; a real extra request per step still trips it.
const REPLY_BUDGET = 50;
// Of those, reads of the sidebar's list: the turn starting and its end, a
// burst each. Measured 2-3 (3 before, with 4 reads of the waits beside them).
const REPLY_LIST_BUDGET = 4;

// The client is idle when no API request has started for this long: longer
// than the reads the first screen doesn't show wait after it.
const QUIET_MS = 3_000;

// A conversation's Buddy follows its status on a 2-second timer while it is
// shown: a steady pace, not a burst, so it is counted on its own and never
// keeps the client from being idle.
const TIMED = /^GET \/api\/v1\/conversations\/[^/]+\/buddy$/;
// What the first screen doesn't show: the sidebar's Monitor counts and
// agents, Buddy, Proactive and voice.
const LATER = [
  /^GET \/api\/v1\/monitor\/(attention|approvals)$/,
  /^GET \/api\/v1\/settings\/profiles$/,
  /^GET \/api\/v1\/voice\/dictation\/capability$/,
  /^(GET|POST) \/api\/v1\/proactive\//,
  /^GET \/api\/v1\/(conversations\/[^/]+\/)?buddy\//,
];
const LIST = 'GET /api/v1/conversations';

function counter(page: Page) {
  const paths: string[] = [];
  const timed: string[] = [];
  let last = Date.now();
  const listen = (request: Request) => {
    const url = new URL(request.url());
    // The live stream itself stays open: it is the channel, not a read.
    if (!url.pathname.startsWith('/api/') || url.pathname === '/api/v1/live')
      return;
    const path = `${request.method()} ${url.pathname}`;
    if (TIMED.test(path)) {
      timed.push(path);
      return;
    }
    paths.push(path);
    last = Date.now();
  };
  page.on('request', listen);
  return {
    paths,
    timed,
    reset() {
      paths.length = 0;
      timed.length = 0;
      last = Date.now();
    },
    async idle() {
      await expect
        .poll(() => Date.now() - last, { timeout: 30_000, intervals: [250] })
        .toBeGreaterThanOrEqual(QUIET_MS);
    },
    stop() {
      page.off('request', listen);
    },
  };
}

const tally = (paths: string[]) =>
  Object.entries(
    paths.reduce<Record<string, number>>((counts, path) => {
      const key = path.replace(/\/[0-9a-f-]{12,}(?=\/|$)/g, '/{id}');
      counts[key] = (counts[key] ?? 0) + 1;
      return counts;
    }, {}),
  ).sort((a, b) => b[1] - a[1]);

test('opening the app and one reply stay within their request budgets', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One window size measures the budget.',
  );
  test.setTimeout(120_000);
  const id = await newConversation(page);

  // Open the app on a conversation, as a reload does, once the page before
  // it has finished its own reads.
  const requests = counter(page);
  await requests.idle();
  requests.reset();
  await page.goto(`/app-v2/conversations/${id}`);
  await expect(composer(page)).toBeVisible();
  await requests.idle();
  const opening = [...requests.paths];
  const later = opening.findIndex((path) =>
    LATER.some((pattern) => pattern.test(path)),
  );
  const firstScreen = later < 0 ? opening : opening.slice(0, later);

  // One scripted reply: four model steps, each with a tool step and media.
  requests.reset();
  await composer(page).fill('turn steps fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const log = page.getByRole('log', { name: 'Conversation' });
  await expect(
    log.getByText('Next, a shorter cut with captions.', { exact: true }),
  ).toBeVisible();
  let held: FixtureCall | undefined;
  await expect
    .poll(async () => {
      held = (await fixtureState(page)).calls
        .filter((call) => call.case === 'turn-steps')
        .at(-1);
      return Boolean(held);
    })
    .toBe(true);
  await releaseProducer(page, held!);
  await expect(
    log.getByText(
      'Here are the three videos and the still. The shorter cut with captions is the one to share.',
      { exact: true },
    ),
  ).toBeVisible();
  await requests.idle();
  const reply = [...requests.paths];
  requests.stop();
  const listReads = reply.filter((path) => path === LIST).length;

  await writeEvidence(info, 'request-budget', {
    opening: {
      count: opening.length,
      firstScreen: firstScreen.length,
      order: opening.map((path) => path.replace(/\/[0-9a-f-]{12,}/g, '/{id}')),
      paths: tally(opening),
    },
    reply: {
      count: reply.length,
      listReads,
      buddyTimer: requests.timed.length,
      paths: tally(reply),
    },
  });
  // The first screen's own reads went first: the list and the open conversation.
  expect(firstScreen).toContain(LIST);
  expect(
    firstScreen.some((path) =>
      /^GET \/api\/v1\/conversations\/[^/]+\/open$/.test(path),
    ),
  ).toBe(true);
  expect(
    later,
    'The reads the first screen doesn’t show still come',
  ).toBeGreaterThan(0);
  expect(firstScreen.length, JSON.stringify(firstScreen)).toBeLessThanOrEqual(
    FIRST_SCREEN_BUDGET,
  );
  expect(opening.length, JSON.stringify(tally(opening))).toBeLessThanOrEqual(
    OPEN_BUDGET,
  );
  expect(reply.length, JSON.stringify(tally(reply))).toBeLessThanOrEqual(
    REPLY_BUDGET,
  );
  expect(listReads).toBeLessThanOrEqual(REPLY_LIST_BUDGET);
});
