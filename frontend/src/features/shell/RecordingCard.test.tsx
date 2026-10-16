import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { RecordingView } from '../../api/types';
import { forgetMediaPreviews } from './MediaPreview';
import { RecordingCard } from './RecordingCard';
import { cardOf } from './TranscriptCards';
import {
  RecordingIoContext,
  clockWords,
  recordingStatus,
  stoppedWords,
  type RecordingIo,
} from './recordings';
import { approvalQuestion, stepVerb } from './tool-activity';

const mock = vi.hoisted(() => ({ download: vi.fn() }));
vi.mock('../../runtime', () => ({ useRuntime: () => ({ controller: mock }) }));
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({
    open: vi.fn(),
    close: vi.fn(),
    dismiss: vi.fn(),
    notify: vi.fn(),
  }),
}));

function view(patch: Partial<RecordingView> = {}): RecordingView {
  return {
    recording_id: 'rec-1a2b3c4d',
    conversation_id: 'conversation-a',
    kind: 'browser',
    status: 'recording',
    started_at: '2026-10-14T10:00:00+00:00',
    ended_at: null,
    seconds: 42,
    max_seconds: 300,
    stopped_by: '',
    media_ref: null,
    mime_type: null,
    size_bytes: null,
    problem: '',
    ...patch,
  };
}

function io(current: RecordingView[]) {
  return {
    load: vi.fn(async () => ({
      items: [...current],
      recording: current.filter((item) => item.status === 'recording').length,
    })),
    stop: vi.fn(async (id: string) => {
      const saved = view({
        ...current[0],
        recording_id: id,
        status: 'saved',
        stopped_by: 'you',
        seconds: 44,
        ended_at: '2026-10-14T10:00:44+00:00',
        media_ref: 'conversation-a:4b7c',
        mime_type: 'video/mp4',
        size_bytes: 3_250_000,
      });
      current.splice(0, current.length, saved);
      return saved;
    }),
  } satisfies RecordingIo;
}

function renderCard(recordings: RecordingIo) {
  return render(
    <RecordingIoContext.Provider value={recordings}>
      <RecordingCard
        recordingId="rec-1a2b3c4d"
        conversationId="conversation-a"
      />
    </RecordingIoContext.Provider>,
  );
}

beforeEach(() => {
  forgetMediaPreviews(mock);
  mock.download.mockReset();
  mock.download.mockResolvedValue(
    new Blob(['fake video'], { type: 'video/mp4' }),
  );
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:video');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(
    () => undefined,
  );
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(
    () => undefined,
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('shows a recording in progress with its clock, its limit and Stop', async () => {
  const recordings = io([view()]);
  renderCard(recordings);

  const card = await screen.findByRole('group', {
    name: 'Browser recording: Recording · 0:42',
  });
  expect(
    within(card).getByText("Recording Row-Bot's browser"),
  ).toBeInTheDocument();
  expect(within(card).getByText('Stops by itself at 5:00')).toBeInTheDocument();
  expect(recordings.load).toHaveBeenCalledWith(
    'conversation-a',
    expect.anything(),
  );
});

it('stops it and shows the saved video with a player', async () => {
  const recordings = io([view({ kind: 'screen' })]);
  const { container } = renderCard(recordings);
  const card = await screen.findByRole('group', {
    name: /Screen recording: Recording/,
  });
  expect(within(card).getByText('Recording your screen')).toBeInTheDocument();

  fireEvent.click(within(card).getByRole('button', { name: 'Stop' }));

  const saved = await screen.findByRole('group', {
    name: 'Screen recording: 0:44 · 3.1 MB',
  });
  expect(recordings.stop).toHaveBeenCalledWith('rec-1a2b3c4d');
  expect(within(saved).getByText('Stopped by you')).toBeInTheDocument();
  expect(
    within(saved).queryByRole('button', { name: 'Stop' }),
  ).not.toBeInTheDocument();
  await waitFor(() =>
    expect(container.querySelector('video')).toHaveAttribute(
      'src',
      'blob:video',
    ),
  );
  expect(mock.download).toHaveBeenCalledWith(
    'conversation-a:4b7c',
    expect.anything(),
  );
});

it('keeps counting while it records, between reads too', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const started = Date.now() - 5000;
  const recordings = io([view({ seconds: 5 })]);
  // The server counts from when it started; the card counts on between reads.
  recordings.load.mockImplementation(async () => ({
    items: [view({ seconds: (Date.now() - started) / 1000 })],
    recording: 1,
  }));
  renderCard(recordings);
  await screen.findByRole('group', { name: /Recording · 0:0[5-6]/ });

  await act(async () => {
    vi.advanceTimersByTime(3000);
  });

  await waitFor(() =>
    expect(
      screen.getByRole('group', { name: /Recording · 0:0[7-9]/ }),
    ).toBeInTheDocument(),
  );
});

it('says plainly why there is no video', async () => {
  renderCard(
    io([
      view({
        status: 'failed',
        problem: 'nothing_recorded',
        stopped_by: 'agent',
        seconds: 1,
      }),
    ]),
  );
  const card = await screen.findByRole('group', {
    name: 'Browser recording: Nothing was recorded.',
  });
  expect(within(card).getByText('Stopped by Row-Bot')).toBeInTheDocument();
  expect(card.querySelector('video')).toBeNull();
});

it('says so when the recording is no longer listed', async () => {
  renderCard(io([]));
  expect(
    await screen.findByText('This recording is no longer listed.'),
  ).toBeInTheDocument();
});

it('names its states in plain words', () => {
  expect(clockWords(42)).toBe('0:42');
  expect(clockWords(245)).toBe('4:05');
  expect(clockWords(3730)).toBe('1:02:10');
  expect(recordingStatus(view({ status: 'saving' }), 3).label).toBe(
    'Saving the video…',
  );
  expect(
    recordingStatus(view({ status: 'failed', problem: 'too_large' }), 3).label,
  ).toBe('The video was too large to keep in the chat.');
  expect(stoppedWords(view({ stopped_by: 'limit' }))).toBe(
    'Stopped at its 5-minute limit',
  );
  expect(stoppedWords(view({ stopped_by: 'quit' }))).toBe(
    'Stopped when Row-Bot closed',
  );
});

it('reads the step that started or stopped a recording as its card', () => {
  expect(
    cardOf({
      kind: 'recording',
      recording_id: 'rec-1a2b3c4d',
    } as Parameters<typeof cardOf>[0]),
  ).toEqual({ kind: 'recording', recordingId: 'rec-1a2b3c4d' });
  expect(approvalQuestion('browser_record')).toBe('Record the browser?');
  // Settled steps carry the canonical name.
  expect(stepVerb('Browser Screenshot', 'succeeded')).toBe('Took a screenshot');
  expect(stepVerb('Browser Viewport', 'succeeded')).toBe(
    'Changed the page size',
  );
  expect(stepVerb('Browser Navigate', 'succeeded')).toBe('Opened page');
});
