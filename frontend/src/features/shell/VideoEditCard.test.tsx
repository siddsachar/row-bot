import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { VideoEditView } from '../../api/types';
import { forgetMediaPreviews } from './MediaPreview';
import { cardOf } from './TranscriptCards';
import { VideoEditCard } from './VideoEditCard';
import { stepVerb } from './tool-activity';
import {
  VideoEditIoContext,
  editStatus,
  type VideoEditIo,
} from './video-edits';

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

function view(patch: Partial<VideoEditView> = {}): VideoEditView {
  return {
    edit_id: 'edit-1a2b3c4d',
    conversation_id: 'conversation-a',
    kind: 'video',
    summary: 'Trimmed, sped up 2× and captioned',
    status: 'editing',
    progress: 0.4,
    download_received: null,
    download_total: null,
    started_at: '2026-10-14T10:00:00+00:00',
    ended_at: null,
    seconds: 12,
    duration_seconds: 42,
    output_name: 'demo-edited.mp4',
    media_ref: null,
    mime_type: null,
    size_bytes: null,
    frame_count: 0,
    stopped_by: '',
    problem: '',
    detail: '',
    ffmpeg: 'ffmpeg 7.1 (GPL version 3 or later), downloaded from PyPI',
    ...patch,
  };
}

function io(current: VideoEditView[]) {
  return {
    load: vi.fn(async () => ({
      items: [...current],
      running: current.filter((item) => item.status === 'editing').length,
    })),
    stop: vi.fn(async (id: string) => {
      const stopped = view({
        ...current[0],
        edit_id: id,
        status: 'stopped',
        stopped_by: 'you',
        progress: null,
        ended_at: '2026-10-14T10:00:14+00:00',
      });
      current.splice(0, current.length, stopped);
      return stopped;
    }),
  } satisfies VideoEditIo;
}

function renderCard(edits: VideoEditIo) {
  return render(
    <VideoEditIoContext.Provider value={edits}>
      <VideoEditCard editId="edit-1a2b3c4d" conversationId="conversation-a" />
    </VideoEditIoContext.Provider>,
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
  vi.restoreAllMocks();
});

it('shows the download of ffmpeg the first time, with its progress and Stop', async () => {
  const edits = io([
    view({
      status: 'getting_ffmpeg',
      progress: 0.4,
      download_received: 12 * 1024 * 1024,
      download_total: 30 * 1024 * 1024,
    }),
  ]);
  renderCard(edits);

  const card = await screen.findByRole('group', {
    name: 'Getting ffmpeg for video editing: Downloading · 12 MB of 30 MB',
  });
  expect(within(card).getByText('From PyPI, once')).toBeInTheDocument();
  expect(
    within(card).getByRole('progressbar', { name: 'ffmpeg download' }),
  ).toHaveAttribute('value', '0.4');
  expect(within(card).getByRole('button', { name: 'Stop' })).toBeEnabled();
  expect(within(card).queryByText('Details')).toBeNull();
});

it('shows an edit in progress and stops it, with nothing saved', async () => {
  const edits = io([view()]);
  renderCard(edits);
  const card = await screen.findByRole('group', {
    name: 'Trimmed, sped up 2× and captioned: Editing · 40%',
  });
  expect(
    within(card).getByRole('progressbar', { name: 'Edit progress' }),
  ).toBeInTheDocument();
  expect(edits.load).toHaveBeenCalledWith('conversation-a', expect.anything());

  fireEvent.click(within(card).getByRole('button', { name: 'Stop' }));

  const stopped = await screen.findByRole('group', {
    name: 'Trimmed, sped up 2× and captioned: Stopped by you',
  });
  expect(edits.stop).toHaveBeenCalledWith('edit-1a2b3c4d');
  expect(within(stopped).queryByRole('button', { name: 'Stop' })).toBeNull();
  expect(within(stopped).queryByRole('progressbar')).toBeNull();
  expect(stopped.querySelector('video')).toBeNull();
});

it('shows the edited video with a player and which ffmpeg made it', async () => {
  const { container } = renderCard(
    io([
      view({
        status: 'saved',
        progress: 1,
        media_ref: 'conversation-a:4b7c',
        mime_type: 'video/mp4',
        size_bytes: 3_250_000,
        ended_at: '2026-10-14T10:00:20+00:00',
      }),
    ]),
  );
  const card = await screen.findByRole('group', {
    name: 'Trimmed, sped up 2× and captioned: 0:42 · 3 MB',
  });
  expect(within(card).getByText('Saved to this chat')).toBeInTheDocument();
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
  fireEvent.click(within(card).getByText('Details'));
  expect(
    within(card).getByText(
      'ffmpeg 7.1 (GPL version 3 or later), downloaded from PyPI',
    ),
  ).toBeVisible();
});

it('shows stills as one contact sheet', async () => {
  mock.download.mockResolvedValue(new Blob(['sheet'], { type: 'image/jpeg' }));
  renderCard(
    io([
      view({
        kind: 'frames',
        summary: '6 stills from demo.mp4',
        status: 'saved',
        progress: 1,
        duration_seconds: null,
        frame_count: 6,
        media_ref: 'conversation-a:sheet',
        mime_type: 'image/jpeg',
        size_bytes: 260_000,
      }),
    ]),
  );
  const card = await screen.findByRole('group', {
    name: '6 stills from demo.mp4: 6 stills',
  });
  expect(
    await within(card).findByRole('img', {
      name: /Contact sheet of 6 stills/,
    }),
  ).toBeInTheDocument();
});

it('says plainly why there is no video, with ffmpeg’s words one tap away', async () => {
  renderCard(
    io([
      view({
        status: 'failed',
        problem: 'edit_failed',
        progress: null,
        detail: 'ffmpeg couldn’t make it: Invalid argument',
      }),
    ]),
  );
  const card = await screen.findByRole('group', {
    name: "Trimmed, sped up 2× and captioned: The edit didn't work",
  });
  expect(
    within(card).getByText(
      'Row-Bot will see what went wrong and can try again.',
    ),
  ).toBeInTheDocument();
  expect(within(card).getByText(/Invalid argument/)).not.toBeVisible();
  fireEvent.click(within(card).getByText('Details'));
  expect(within(card).getByText(/Invalid argument/)).toBeVisible();
});

it('says what to do when ffmpeg couldn’t be downloaded or the result is too large', async () => {
  const detail =
    'Row-Bot couldn’t download ffmpeg from PyPI. Check the internet connection and try again.';
  renderCard(
    io([
      view({
        status: 'failed',
        problem: 'download_failed',
        progress: null,
        ffmpeg: null,
        detail,
      }),
    ]),
  );
  const card = await screen.findByRole('group', {
    name: "Trimmed, sped up 2× and captioned: ffmpeg couldn't be downloaded",
  });
  expect(within(card).getByText(detail)).toBeVisible();
  expect(within(card).queryByText('Details')).toBeNull();
  expect(
    editStatus(view({ status: 'failed', problem: 'too_large' }), 0).label,
  ).toBe('Too large for the chat');
});

it('says so when the edit is no longer listed', async () => {
  renderCard(io([]));
  expect(
    await screen.findByText('This video edit is no longer listed.'),
  ).toBeInTheDocument();
});

it('reads an edit_video step as its card, in words', () => {
  expect(
    cardOf({
      kind: 'video_edit',
      edit_id: 'edit-1a2b3c4d',
    } as Parameters<typeof cardOf>[0]),
  ).toEqual({ kind: 'video_edit', editId: 'edit-1a2b3c4d' });
  expect(stepVerb('edit_video', 'succeeded')).toBe('Edited a video');
});
