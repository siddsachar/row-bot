import { expect, it } from 'vitest';
import type { Notice } from '../../api/types';
import { noticeToShow } from './background-notices';

const now = Date.parse('2026-09-28T12:00:00Z');
function notice(patch: Partial<Notice>): Notice {
  return {
    id: 1,
    level: 'info',
    title: '⚡ Task Complete',
    message: 'Morning digest finished (2 steps).',
    source: 'workflow',
    requested: true,
    startup: false,
    count: 1,
    at: '2026-09-28T11:59:00Z',
    ...patch,
  };
}

it('shows warnings and errors always, information only for jobs the person started', () => {
  expect(noticeToShow(notice({}), now)).toEqual({
    text: 'Task Complete: Morning digest finished (2 steps).',
    tone: undefined,
  });
  expect(noticeToShow(notice({ requested: false }), now)).toBeNull();
  expect(
    noticeToShow(
      notice({
        level: 'warning',
        requested: false,
        title: '⏸️ Approval Required',
      }),
      now,
    ),
  ).toEqual({
    text: 'Approval Required: Morning digest finished (2 steps).',
    tone: 'warning',
  });
  expect(
    noticeToShow(notice({ level: 'error', requested: false }), now)?.tone,
  ).toBe('danger');
});

it('shows start-up warnings in their own words and skips stale news', () => {
  expect(
    noticeToShow(
      notice({
        level: 'warning',
        title: 'Start-up warning',
        message: "The plugin 'rss' didn't load.",
        startup: true,
        at: '2026-09-28T08:00:00Z',
      }),
      now,
    ),
  ).toEqual({ text: "The plugin 'rss' didn't load.", tone: 'warning' });
  expect(
    noticeToShow(notice({ level: 'warning', at: '2026-09-28T11:00:00Z' }), now),
  ).toBeNull();
  expect(noticeToShow(notice({ count: 3 }), now)?.text).toBe(
    'Task Complete: Morning digest finished (2 steps). (3 times)',
  );
});
