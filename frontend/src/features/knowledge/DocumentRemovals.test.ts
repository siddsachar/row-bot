import { expect, it, vi } from 'vitest';
import { createDocumentRemovals } from './DocumentRemovals';
import type { DocumentRemovalTransport } from './DocumentRemoval';

function transport(): DocumentRemovalTransport {
  return {
    review: vi.fn(async (document_id) => ({
      document_id,
      review_id: 'review',
      source_revision: 'source',
      source_count: 1,
      retains_copies: true as const,
    })),
    reviewRetry: vi.fn(),
    execute: vi.fn(),
    receipt: vi.fn(),
  };
}

it('preserves every reviewed target at capacity and keeps the original review on reselection', async () => {
  const api = transport(),
    owner = createDocumentRemovals(api, 2);
  owner.select('one', 'One');
  const one = owner.entries()[0][1].session;
  await one.review();
  owner.select(null, 'All');
  await owner.entries()[1][1].session.review();
  owner.select('three', 'Three');
  expect(owner.entries()).toHaveLength(2);
  expect(owner.getSnapshot().error).toContain('retained document review');
  owner.select('one', 'One');
  expect(owner.entries()[0][1].session).toBe(one);
  expect(one.getSnapshot().review?.review_id).toBe('review');
  expect(owner.hasRetained()).toBe(true);
  one.dismissReview();
  owner.select('three', 'Three');
  expect(one.getSnapshot().revoked).toBe(true);
  expect(owner.entries().map(([id]) => id)).toEqual(['*', 'three']);
  owner.dispose();
});

it('purges all targets and ignores late review completion after auth disposal', async () => {
  const api = transport();
  let finish!: (
    value: Awaited<ReturnType<DocumentRemovalTransport['review']>>,
  ) => void;
  vi.mocked(api.review).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const owner = createDocumentRemovals(api);
  owner.select('one', 'One');
  const session = owner.entries()[0][1].session;
  const waiting = session.review();
  await Promise.resolve();
  owner.dispose();
  finish({
    document_id: 'one',
    review_id: 'stale',
    source_revision: 'source',
    source_count: 1,
    retains_copies: true,
  });
  await expect(waiting).rejects.toThrow('authentication_required');
  expect(owner.entries()).toEqual([]);
  expect(session.getSnapshot().review).toBeNull();
  expect(owner.hasRetained()).toBe(false);
});
