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
        safe_input: '{"limit": 3}',
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

it('keeps groups and tool results collapsed by default with stable status hooks', () => {
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  const group = screen.getByText('files').closest('details');
  expect(group).not.toHaveAttribute('open');
  expect(group).toHaveAttribute('data-trace-status', 'succeeded');
  expect(group?.querySelector('summary')).toHaveTextContent('1 call');
  expect(messageText).not.toHaveBeenCalled();
});

it('loads the first public page on call expansion and copies the visible result', async () => {
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  fireEvent.click(screen.getByText('files'));
  const call = screen.getByText('read_file').closest('details')!;
  expect(call.querySelector('summary')).toHaveTextContent(
    '1read_filesucceeded',
  );
  await act(async () => fireEvent.click(within(call).getByText('read_file')));
  expect(screen.getByText('Skill · Careful review activated')).toBeVisible();
  expect(screen.getByText('{"limit": 3}')).toBeVisible();
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
  fireEvent.click(screen.getByText('files'));
  await act(async () => fireEvent.click(screen.getByText('read_file')));
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

it('ignores a stale first page and aborts it when the call closes', async () => {
  let resolve!: (value: unknown) => void;
  messageText.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  render(<TranscriptTrace conversation="conversation-a" groups={groups} />);
  fireEvent.click(screen.getByText('files'));
  const summary = screen.getByText('read_file');
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
  expect(screen.getAllByText('A bounded summary.')).toHaveLength(2);
});

it('keeps a safe summary and offers no private fetch without a content reference', async () => {
  const noReference = [
    { ...groups[0], items: [{ ...groups[0].items[0], content_ref: '' }] },
  ];
  render(
    <TranscriptTrace conversation="conversation-a" groups={noReference} />,
  );
  fireEvent.click(screen.getByText('files'));
  await act(async () => fireEvent.click(screen.getByText('read_file')));
  expect(messageText).not.toHaveBeenCalled();
  expect(screen.getByText('A bounded summary.')).toBeVisible();
});

it('loads a newly public result for an already expanded pending call', async () => {
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
  fireEvent.click(screen.getByText('files'));
  await act(async () => fireEvent.click(screen.getByText('read_file')));
  expect(messageText).not.toHaveBeenCalled();
  expect(screen.getByText('read_file').closest('details')).toHaveAttribute(
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
  fireEvent.click(screen.getByText('files'));
  await act(async () => fireEvent.click(screen.getByText('read_file')));
  expect(screen.getByText('A bounded summary.')).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Public result could not be loaded',
  );
  expect(screen.queryByText('private backend detail')).toBeNull();
});

it('restores generated media previews and an accessible unavailable fallback', async () => {
  const mediaGroups: TranscriptTraceGroup[] = [
    {
      ...groups[0],
      items: [
        {
          ...groups[0].items[0],
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
  fireEvent.click(screen.getByText('files'));
  fireEvent.click(screen.getAllByText('read_file')[0]);
  fireEvent.click(screen.getAllByText('read_file')[1]);

  expect(await screen.findByAltText('Generated result')).toBeVisible();
  expect(screen.getByRole('alert', { name: '' })).toHaveTextContent(
    'generated media is unavailable',
  );
});
