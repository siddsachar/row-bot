import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { TranscriptTraceGroup } from '../../api/types';
import TranscriptTrace from './TranscriptTrace';

const messageText = vi.fn();
const writeClipboard = vi.fn();
const download = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({
    controller: { messageText, download },
    platform: { writeClipboard },
  }),
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
        safe_input: '{"path": "notes/plan.md", "limit": 3}',
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

function row() {
  return screen.getByRole('group', { name: 'Tool activity' });
}
function openRow() {
  fireEvent.click(row().querySelector('summary.activity-summary')!);
}
function step() {
  return screen.getByText('Read a file').closest('details')!;
}

beforeEach(() => {
  messageText.mockReset();
  messageText.mockResolvedValue({
    data: btoa('Public result page.'),
    has_more: false,
    next_cursor: null,
  });
  writeClipboard.mockReset();
  writeClipboard.mockResolvedValue({ status: 'ok' });
  download.mockReset();
  download.mockResolvedValue(new Blob(['fixture'], { type: 'image/png' }));
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:trace-media');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
});

it('shows one quiet collapsed line per turn with stable status hooks', () => {
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  expect(row()).toHaveAttribute('data-trace-status', 'succeeded');
  const disclosure = within(row()).getByText('Used 1 tool').closest('details');
  expect(disclosure).not.toHaveAttribute('open');
  // Human verb and the key argument, not the raw function name.
  expect(screen.getByText('Read a file')).toBeInTheDocument();
  expect(screen.getByText('notes/plan.md')).toBeInTheDocument();
  expect(step()).toHaveAttribute('data-trace-status', 'succeeded');
  expect(messageText).not.toHaveBeenCalled();
});

it('summarises failures in the row and in the step verb', () => {
  const failed: TranscriptTraceGroup[] = [
    {
      ...groups[0],
      status: 'failed',
      items: [{ ...groups[0].items[0], status: 'failed' }],
    },
  ];
  render(<TranscriptTrace conversation="conversation-a" groups={failed} />);
  expect(row()).toHaveAttribute('data-trace-status', 'failed');
  expect(within(row()).getByText('Used 1 tool · 1 failed')).toBeVisible();
  expect(screen.getByText('Read a file failed')).toBeInTheDocument();
});

it('shows live shimmer text for the running step and an approval hold', () => {
  const pending: TranscriptTraceGroup[] = [
    {
      ...groups[0],
      status: 'pending',
      items: [
        {
          ...groups[0].items[0],
          canonical_name: 'web_search',
          safe_input: '{"query": "local embedding models"}',
          status: 'pending',
          content_ref: '',
        },
      ],
    },
  ];
  const { rerender } = render(
    <TranscriptTrace
      conversation="conversation-a"
      groups={pending}
      live={{ running: true }}
    />,
  );
  expect(row()).toHaveAttribute('data-trace-status', 'pending');
  expect(
    within(row()).getByText('Searching the web “local embedding models”'),
  ).toHaveAttribute('data-live', 'true');
  rerender(
    <TranscriptTrace
      conversation="conversation-a"
      groups={[]}
      live={{ running: true, waiting: true }}
    >
      <aside aria-label="Approval required for fixture" />
    </TranscriptTrace>,
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    'Waiting for your approval',
  );
  expect(
    screen.getByRole('complementary', {
      name: 'Approval required for fixture',
    }),
  ).toBeInTheDocument();
});

it('loads the first public page on step expansion and copies the visible result', async () => {
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  openRow();
  await act(async () =>
    fireEvent.click(within(step()).getByText('Read a file')),
  );
  expect(screen.getByText('Skill · Careful review activated')).toBeVisible();
  expect(within(step()).getByText('read_file')).toBeVisible();
  expect(step().querySelector('.trace-input pre')).toHaveTextContent(
    '"path": "notes/plan.md"',
  );
  expect(messageText).toHaveBeenCalledWith(
    'conversation-a',
    'result-1',
    undefined,
    expect.any(AbortSignal),
  );
  expect(screen.getByText('Public result page.')).toBeVisible();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Copy result' })),
  );
  expect(writeClipboard).toHaveBeenCalledExactlyOnceWith('Public result page.');
  expect(screen.getByRole('status')).toHaveTextContent('Result copied.');
});

it('pages only on request and keeps the safe summary after a failed first page', async () => {
  messageText
    .mockResolvedValueOnce({
      data: btoa('First page.'),
      has_more: true,
      next_cursor: 'next',
    })
    .mockRejectedValueOnce(new Error('private backend detail'));
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  openRow();
  await act(async () => fireEvent.click(screen.getByText('Read a file')));
  expect(messageText).toHaveBeenCalledTimes(1);
  expect(screen.getByText('First page.')).toBeVisible();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Load next result page' }),
    ),
  );
  expect(messageText).toHaveBeenLastCalledWith(
    'conversation-a',
    'result-1',
    'next',
    expect.any(AbortSignal),
  );
  expect(screen.getByText('First page.')).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Public result could not be loaded',
  );
  expect(screen.queryByText('private backend detail')).toBeNull();
});

it('ignores a stale first page and aborts it when the step closes', async () => {
  let resolve!: (value: unknown) => void;
  messageText.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  openRow();
  const summary = screen.getByText('Read a file');
  await act(async () => fireEvent.click(summary));
  const signal = messageText.mock.calls[0][3] as AbortSignal;
  await act(async () => fireEvent.click(summary));
  expect(signal.aborted).toBe(true);
  await act(async () =>
    resolve({
      data: btoa('Stale private text'),
      has_more: false,
      next_cursor: null,
    }),
  );
  expect(screen.queryByText('Stale private text')).toBeNull();
  expect(step().querySelector('.trace-output')).toHaveTextContent(
    'A bounded summary.',
  );
});

it('keeps a safe summary and offers no private fetch without a content reference', async () => {
  const noReference = [
    { ...groups[0], items: [{ ...groups[0].items[0], content_ref: '' }] },
  ];
  render(
    <TranscriptTrace conversation="conversation-a" groups={noReference} />,
  );
  openRow();
  await act(async () => fireEvent.click(screen.getByText('Read a file')));
  expect(messageText).not.toHaveBeenCalled();
  expect(step().querySelector('.trace-output')).toHaveTextContent(
    'A bounded summary.',
  );
});

it('loads a newly public result for an already expanded pending step', async () => {
  const pending = [
    {
      ...groups[0],
      status: 'pending' as const,
      items: [
        { ...groups[0].items[0], status: 'pending' as const, content_ref: '' },
      ],
    },
  ];
  const { rerender } = render(
    <TranscriptTrace conversation="conversation-a" groups={pending} />,
  );
  openRow();
  await act(async () => fireEvent.click(screen.getByText('Reading a file')));
  expect(messageText).not.toHaveBeenCalled();
  expect(screen.getByText('Reading a file').closest('details')).toHaveAttribute(
    'data-trace-status',
    'pending',
  );
  await act(async () =>
    rerender(<TranscriptTrace conversation="conversation-a" groups={groups} />),
  );
  expect(messageText).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Public result page.')).toBeVisible();
});

it('keeps the safe summary when the first public page fails', async () => {
  messageText.mockRejectedValueOnce(new Error('private backend detail'));
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  openRow();
  await act(async () => fireEvent.click(screen.getByText('Read a file')));
  expect(step().querySelector('.trace-output')).toHaveTextContent(
    'A bounded summary.',
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Public result could not be loaded',
  );
  expect(screen.queryByText('private backend detail')).toBeNull();
});

it('names generated media without rendering a second copy (B22) and flags unavailable media', async () => {
  const mediaGroups: TranscriptTraceGroup[] = [
    {
      ...groups[0],
      items: [
        {
          ...groups[0].items[0],
          canonical_name: 'generate_image',
          specialization: {
            kind: 'media',
            media_kind: 'image',
            media: [
              {
                media_ref: 'conversation-a:media-a',
                mime_type: 'image/png',
              },
            ],
          },
        },
        {
          ...groups[0].items[0],
          canonical_name: 'generate_image',
          item_id: 'item-error',
          call_id: 'call-error',
          call_order: 1,
          specialization: {
            kind: 'media',
            media_kind: 'unavailable',
            error_code: 'media_unavailable',
          },
        },
      ],
    },
  ];
  render(
    <TranscriptTrace conversation="conversation-a" groups={mediaGroups} />,
  );
  fireEvent.click(screen.getByText('Used 2 tools'));
  for (const summary of screen.getAllByText('Generated an image'))
    fireEvent.click(summary);
  expect(
    screen.getByText('Created 1 image · shown in the conversation'),
  ).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'generated media is unavailable',
  );
  expect(screen.queryByRole('img')).toBeNull();
  expect(download).not.toHaveBeenCalled();
});
