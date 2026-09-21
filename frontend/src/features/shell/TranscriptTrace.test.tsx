import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { TranscriptTraceGroup } from '../../api/types';
import TranscriptTrace from './TranscriptTrace';

const messageText = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: { messageText } }),
}));

const groups: TranscriptTraceGroup[] = [
  {
    group_id: 'group-1',
    name: 'files',
    kind: 'generic',
    group_order: 0,
    status: 'succeeded',
    counts: { succeeded: 1 },
    items: [
      {
        item_id: 'item-1',
        group_id: 'group-1',
        call_id: 'call-1',
        result_message_id: 'result-1',
        call_order: 0,
        group_order: 0,
        canonical_name: 'read_file',
        group_name: 'files',
        group_kind: 'generic',
        status: 'succeeded',
        safe_summary: 'A bounded summary.',
        summary_truncated: true,
        content_ref: 'result-1',
        specialization: {
          kind: 'skill_load',
          skill_id: 'review',
          display_name: 'Careful review',
          newly_active: true,
        },
      },
    ],
  },
];

beforeEach(() => {
  messageText.mockReset();
  messageText.mockResolvedValue({
    data: btoa('Public result page.'),
    next_cursor: null,
  });
});

it('keeps groups and tool results collapsed by default with stable status hooks', () => {
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  const group = screen.getByText('Done files').closest('details');
  expect(group).not.toHaveAttribute('open');
  expect(group).toHaveAttribute('data-trace-status', 'succeeded');
});

it('reveals specialization and lazily pages public result text', async () => {
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  fireEvent.click(screen.getByText('Done files'));
  fireEvent.click(screen.getByText('read_file · succeeded'));
  expect(screen.getByText('Skill · Careful review activated')).toBeVisible();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Load public result' })),
  );
  expect(messageText).toHaveBeenCalledWith(
    'conversation-a',
    'result-1',
    undefined,
  );
  expect(screen.getByText('Public result page.')).toBeVisible();
});
