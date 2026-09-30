import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type {
  EventRecord,
  TranscriptRow,
  TranscriptTraceGroup,
  TranscriptTraceItem,
} from '../../api/types';
import TranscriptTrace, { type LiveActivity } from './TranscriptTrace';
import { answerStreaming } from './transcript-model';

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
afterEach(() => {
  vi.useRealTimers();
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
  expect(screen.getByText("Couldn't read a file")).toBeInTheDocument();
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
  // A computer-use pause is you using the computer, not an approval.
  rerender(
    <TranscriptTrace
      conversation="conversation-a"
      groups={[]}
      live={{ running: true, paused: true }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    'Paused while you use the computer',
  );
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

function summaryIcon() {
  return row().querySelector('.activity-summary-icon svg')!;
}

it('draws the turn check only when this client watched it finish', () => {
  const item = { ...groups[0].items[0], call_id: 'call-draw', content_ref: '' };
  const running: TranscriptTraceGroup[] = [
    {
      ...groups[0],
      status: 'pending',
      items: [{ ...item, status: 'pending' }],
    },
  ];
  const done: TranscriptTraceGroup[] = [{ ...groups[0], items: [item] }];
  const live = render(
    <TranscriptTrace
      conversation="conversation-a"
      groups={running}
      live={{ running: true }}
    />,
  );
  live.rerender(
    <TranscriptTrace
      conversation="conversation-a"
      groups={done}
      live={{ running: false }}
    />,
  );
  expect(summaryIcon()).toHaveClass('icon-draw');
  live.unmount();
  // The stored row that replaces the live one keeps the motion.
  render(<TranscriptTrace conversation="conversation-a" groups={done} />);
  expect(summaryIcon()).toHaveClass('icon-draw');
});

it('never animates history it did not watch', () => {
  const item = { ...groups[0].items[0], call_id: 'call-history' };
  render(
    <TranscriptTrace
      conversation="conversation-a"
      groups={[{ ...groups[0], items: [item] }]}
    />,
  );
  expect(summaryIcon()).not.toHaveClass('icon-draw');
  openRow();
  expect(step().querySelector('.activity-node svg')).not.toHaveClass(
    'icon-draw-settle',
  );
});

function command(
  overrides: Partial<TranscriptTraceItem>,
): TranscriptTraceGroup[] {
  const item: TranscriptTraceItem = {
    ...groups[0].items[0],
    canonical_name: 'run_command',
    safe_input: '{"command": "New-Item notes.txt"}',
    content_ref: '',
    specialization: null,
    ...overrides,
  };
  return [
    {
      ...groups[0],
      status: item.status,
      counts: { [item.status]: 1 },
      items: [item],
    },
  ];
}

it.each([
  [
    'Approval: asked; denied by you — did not run\nCommand cancelled by user.',
    'Denied',
  ],
  ['Command cancelled by user.', 'Denied'],
  ['Cancelled: stopped before it finished.', 'Stopped'],
])(
  'shows a step that never ran as %#: no spinner, no failure (B234)',
  (summary, label) => {
    render(
      <TranscriptTrace
        conversation="conversation-a"
        groups={command({
          call_id: `call-${label}-${summary.length}`,
          status: 'cancelled',
          safe_summary: summary,
        })}
      />,
    );
    expect(row()).toHaveAttribute('data-trace-status', 'skipped');
    expect(row().querySelector('.activity-spinner')).toBeNull();
    openRow();
    const skipped = screen.getByText("Didn't run a command").closest('li')!;
    expect(within(skipped).getByText(label)).toBeVisible();
    expect(skipped.querySelector('.activity-node')).toHaveAttribute(
      'data-state',
      'skipped',
    );
  },
);

it('shows the approval apart from the result, and the wait apart from the run (B235)', () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(0);
  const pending = { call_id: 'call-approved', status: 'pending' as const };
  const stored = render(
    <TranscriptTrace
      conversation="conversation-a"
      groups={command({ ...pending, safe_summary: '' })}
    />,
  );
  // The live row holds the approval card while the step waits.
  const live = render(
    <TranscriptTrace
      conversation="conversation-a"
      groups={[]}
      live={{ running: false, waiting: true }}
    >
      <aside aria-label="Approval required for run_command" />
    </TranscriptTrace>,
  );
  vi.setSystemTime(19_800);
  live.rerender(
    <TranscriptTrace
      conversation="conversation-a"
      groups={[]}
      live={{ running: true }}
    />,
  );
  vi.setSystemTime(20_040);
  stored.rerender(
    <TranscriptTrace
      conversation="conversation-a"
      groups={command({
        ...pending,
        status: 'succeeded',
        safe_summary:
          'Approval: asked; approved by you\n$ New-Item notes.txt\n\n[Exit code: 0 | Duration: 0.24s]',
      })}
    />,
  );
  live.unmount();
  openRow();
  const approved = screen.getByText('Ran a command').closest('details')!;
  fireEvent.click(within(approved).getByText('Ran a command'));
  // The step's own time is its run; the wait is told apart.
  expect(within(approved).getByText('0.2s')).toBeVisible();
  expect(within(approved).getByText('Asked; approved by you')).toBeVisible();
  expect(
    within(approved).getByText('Waited 20s for approval · ran 0.2s'),
  ).toBeVisible();
  expect(approved.querySelector('.trace-output')).toHaveTextContent(
    /^\$ New-Item notes\.txt/,
  );
});

function event(
  type: 'generation.activity' | 'tool.activity',
  revision: string,
): EventRecord {
  return {
    cursor: revision,
    event: { type, projection_revision: revision, payload: {} },
  } as unknown as EventRecord;
}
function answer(text: string, revision: string, segment = 'one') {
  return {
    id: `assistant:live:pass:${segment}`,
    role: 'assistant',
    blocks: [{ type: 'text', text }],
    render_revision: revision,
  } as TranscriptRow;
}

it('thinks only until the answer starts, then settles without ticking under the text (B233)', () => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
  const user = {
    id: 'user:one',
    role: 'user',
    blocks: [{ type: 'text', text: 'Make a note' }],
  } as TranscriptRow;
  const view = (
    rows: TranscriptRow[],
    activity: EventRecord[],
    traceGroups: TranscriptTraceGroup[] = [],
    running = true,
  ) => {
    const live: LiveActivity = {
      running,
      thinking: activity.at(-1)?.event.type === 'generation.activity',
      startedAt: 0,
      answering: running && answerStreaming(rows, activity),
    };
    return (
      <TranscriptTrace
        conversation="conversation-a"
        groups={traceGroups}
        live={live}
      />
    );
  };
  const thinking = event('generation.activity', '5');
  const { container, rerender } = render(view([user], [thinking]));
  expect(screen.getByText('Thinking…')).toBeVisible();
  act(() => vi.advanceTimersByTime(3000));
  expect(container.querySelector('.activity-duration')).toHaveTextContent(
    '3.0s',
  );
  // The status region says what changed, never the seconds.
  expect(screen.getByRole('status')).toHaveTextContent(/^Thinking$/);

  // The first answer text: the line settles on how long the thinking took.
  act(() => vi.advanceTimersByTime(5200));
  rerender(view([user, answer('Sure', '6')], [thinking]));
  expect(screen.queryByText('Thinking…')).toBeNull();
  expect(screen.getByText('Thought for 8.2s')).toBeVisible();
  expect(container.querySelector('.activity-spinner')).toBeNull();
  expect(container.querySelector('.activity-duration')).toBeNull();
  expect(screen.getByRole('status')).toHaveTextContent(/^Answering$/);

  // More text: nothing counts on.
  act(() => vi.advanceTimersByTime(4000));
  rerender(view([user, answer('Sure, one moment', '9')], [thinking]));
  expect(screen.getByText('Thought for 8.2s')).toBeVisible();

  // A tool step takes its own row.
  const tool = event('tool.activity', '10');
  rerender(
    view(
      [user, answer('Sure, one moment', '9')],
      [thinking, tool],
      command({
        call_id: 'call-live-tool',
        status: 'pending',
      }),
    ),
  );
  expect(
    within(row()).getByText('Running a command New-Item notes.txt'),
  ).toHaveAttribute('data-live', 'true');
  expect(screen.getByRole('status')).toHaveTextContent(/^Running a command$/);
  expect(screen.queryByText(/Thought for/)).toBeNull();

  // The step settles into its stored row and the model thinks again.
  const done = event('tool.activity', '11');
  const stored = {
    id: 'assistant:stored',
    role: 'assistant',
    blocks: [],
  } as TranscriptRow;
  rerender(view([user, stored], [thinking, tool, done]));
  expect(screen.getByText('Thinking…')).toBeVisible();
  act(() => vi.advanceTimersByTime(2000));
  expect(container.querySelector('.activity-duration')).toHaveTextContent(
    '2.0s',
  );

  // Its answer streams: the line settles again, nothing spins below it.
  rerender(
    view([user, stored, answer('Done.', '12', 'two')], [thinking, tool, done]),
  );
  expect(screen.queryByText('Thinking…')).toBeNull();
  expect(screen.getByText('Thought for 2.0s')).toBeVisible();
  expect(container.querySelector('.activity-spinner')).toBeNull();

  // Done: the live line leaves.
  rerender(
    view(
      [user, stored, answer('Done.', '12', 'two')],
      [thinking, tool, done],
      [],
      false,
    ),
  );
  expect(container).toBeEmptyDOMElement();
});

it('gives a thought under a second no line of its own', () => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
  const thinking = event('generation.activity', '3');
  const live = (answering: boolean): LiveActivity => ({
    running: true,
    thinking: !answering,
    startedAt: 0,
    answering,
  });
  const { container, rerender } = render(
    <TranscriptTrace
      conversation="conversation-a"
      groups={[]}
      live={live(false)}
    />,
  );
  act(() => vi.advanceTimersByTime(400));
  const rows = [answer('Sure', '4')];
  rerender(
    <TranscriptTrace
      conversation="conversation-a"
      groups={[]}
      live={live(answerStreaming(rows, [thinking]))}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});
