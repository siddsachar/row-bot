import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { Notice } from '../../api/types';
import { noticeToShow, useBackgroundNotices } from './background-notices';

const runtime = vi.hoisted(() => ({
  listener: null as null | ((notice: Notice) => void),
  notify: vi.fn(),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({
    controller: {
      onNotice: (listener: (notice: Notice) => void) => {
        runtime.listener = listener;
        return () => {
          runtime.listener = null;
        };
      },
    },
  }),
}));
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({ notify: runtime.notify }),
}));

afterEach(() => {
  cleanup();
  runtime.notify.mockReset();
});

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

const sent = (id: number) =>
  notice({
    id,
    title: 'Row-Bot sent you report.pdf',
    message: '',
    source: 'deliveries',
    conversation_id: 'chat-a',
    at: new Date().toISOString(),
  });

it('a file Row-Bot sent offers Open, which opens its conversation', () => {
  expect(noticeToShow(sent(1), Date.now())).toEqual({
    text: 'Row-Bot sent you report.pdf',
    conversationId: 'chat-a',
    tone: undefined,
  });
  const open = vi.fn();
  renderHook(() => useBackgroundNotices('chat-b', open));
  act(() => runtime.listener?.(sent(2)));

  const [[text, tone, action]] = runtime.notify.mock.calls;
  expect([text, tone, action.label]).toEqual([
    'Row-Bot sent you report.pdf',
    undefined,
    'Open',
  ]);
  action.onAction();
  expect(open).toHaveBeenCalledWith('chat-a');
});

it('is not shown while its conversation is open here: the card is on screen', () => {
  renderHook(() => useBackgroundNotices('chat-a', vi.fn()));
  act(() => runtime.listener?.(sent(3)));
  expect(runtime.notify).not.toHaveBeenCalled();
  act(() =>
    runtime.listener?.(notice({ id: 4, at: new Date().toISOString() })),
  );
  expect(runtime.notify).toHaveBeenCalledWith(
    'Task Complete: Morning digest finished (2 steps).',
    undefined,
    undefined,
  );
});
