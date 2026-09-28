import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fileKind, forgetMediaPreviews, MediaPreview } from './MediaPreview';

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
function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}
beforeEach(() => {
  forgetMediaPreviews(mock);
  mock.download.mockReset();
  let sequence = 0;
  vi.spyOn(URL, 'createObjectURL').mockImplementation(
    () => `blob:fixture-${++sequence}`,
  );
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

it('retrieves through the authenticated controller and releases the owned image URL', async () => {
  const blob = new Blob(['fake pixels'], { type: 'image/png' });
  mock.download.mockResolvedValue(blob);
  const { unmount } = render(
    <MediaPreview reference="opaque-reference" mime="image/png" />,
  );
  expect(await screen.findByAltText('Generated result')).toHaveAttribute(
    'src',
    'blob:fixture-1',
  );
  expect(mock.download).toHaveBeenCalledWith(
    'opaque-reference',
    expect.any(AbortSignal),
  );
  expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
  expect(
    screen.getByRole('link', { name: 'Download generated result' }),
  ).toHaveAttribute('download', 'result');
  unmount();
  expect(mock.download.mock.calls[0][1].aborted).toBe(true);
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:fixture-1');
});

it('retries a retrieval failure without another transport or direct URL', async () => {
  mock.download
    .mockRejectedValueOnce(new TypeError('disconnected'))
    .mockResolvedValueOnce(new Blob(['pixels']));
  render(<MediaPreview reference="opaque-reference" mime="image/png" />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Retry generated result' }),
  );
  expect(await screen.findByAltText('Generated result')).toHaveAttribute(
    'src',
    'blob:fixture-1',
  );
  expect(mock.download).toHaveBeenCalledTimes(2);
  expect(mock.download.mock.calls[0][1].aborted).toBe(true);
});

it.each([
  ['audio/mpeg', 'audio', 'Generated audio result'],
  ['video/mp4', 'video', 'Generated video result'],
])(
  'renders %s with native named controls and no autoplay',
  async (mime, tag, label) => {
    mock.download.mockResolvedValue(
      new Blob(['synthetic media'], { type: mime }),
    );
    const { container } = render(
      <MediaPreview reference="opaque-reference" mime={mime} />,
    );
    const player = await screen.findByLabelText(label);
    expect(player.tagName.toLowerCase()).toBe(tag);
    expect(player).toHaveAttribute('controls');
    expect(player).toHaveAttribute('preload', 'metadata');
    expect(player).not.toHaveAttribute('autoplay');
    expect(container.querySelector('iframe,object,embed')).toBeNull();
    if (tag === 'video') expect(player).toHaveAttribute('playsinline');
  },
);

it('names a file the way a person would, never by its media type', () => {
  expect(fileKind('application/octet-stream', 'tide-note-a.txt')).toBe(
    'TXT file',
  );
  expect(fileKind('application/octet-stream', 'notes')).toBe('File');
  expect(fileKind('image/png', 'Pasted image.png')).toBe('PNG image');
  expect(fileKind('image/jpeg', 'photo.jpg')).toBe('JPEG image');
  expect(fileKind('application/pdf', 'brief.pdf')).toBe('PDF');
  expect(fileKind('video/mp4', 'clip.mp4')).toBe('MP4 video');
  expect(fileKind('audio/mpeg', 'song.mp3')).toBe('MP3 audio');
  expect(fileKind('text/markdown; charset=utf-8', 'readme.md')).toBe('MD file');
});

it.each([
  'text/html',
  'application/pdf',
  'image/svg+xml',
  'application/octet-stream',
  'image/unknown',
  'audio/unknown',
  'video/unknown',
])('keeps %s download-only without embedding', async (mime) => {
  mock.download.mockResolvedValue(
    new Blob(['<script>sentinel()</script>'], { type: mime }),
  );
  const { container } = render(
    <MediaPreview reference="opaque-reference" mime={mime} />,
  );
  expect(
    await screen.findByRole('link', { name: 'Download generated result' }),
  ).toHaveAttribute('href', 'blob:fixture-1');
  expect(
    container.querySelector('img,audio,video,iframe,object,embed,script'),
  ).toBeNull();
});

it('normalizes explicit MIME parameters and refuses a conflicting fetched content type', async () => {
  mock.download
    .mockResolvedValueOnce(new Blob(['pixels'], { type: 'image/png' }))
    .mockResolvedValueOnce(new Blob(['markup'], { type: 'text/html' }));
  const { rerender, container } = render(
    <MediaPreview reference="first" mime=" IMAGE/PNG; charset=binary " />,
  );
  await screen.findByAltText('Generated result');
  rerender(<MediaPreview reference="second" mime="image/png" />);
  await screen.findByRole('link', { name: 'Download generated result' });
  expect(container.querySelector('img,iframe,object,embed')).toBeNull();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:fixture-1');
});

it.each([
  ['image/png', 'Generated result'],
  ['audio/ogg', 'Generated audio result'],
  ['video/webm', 'Generated video result'],
])(
  'keeps a download and retry after %s decode failure',
  async (mime, label) => {
    mock.download.mockResolvedValue(new Blob(['media'], { type: mime }));
    const { container } = render(
      <MediaPreview reference="opaque-reference" mime={mime} />,
    );
    const player = mime.startsWith('image')
      ? await screen.findByAltText(label)
      : await screen.findByLabelText(label);
    fireEvent.error(player);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'could not be previewed',
    );
    expect(container.querySelector('img,audio,video')).toBeNull();
    expect(screen.getByRole('link')).toHaveAttribute('href', 'blob:fixture-1');
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Retry generated result' }),
    );
    await screen.findByRole('link');
    expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith(
      'blob:fixture-1',
    );
    expect(screen.getByRole('link')).toHaveAttribute('href', 'blob:fixture-2');
  },
);

it('aborts an old reference and ignores its later success without allocating a URL', async () => {
  const old = pending<Blob>();
  mock.download
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(new Blob(['new'], { type: 'image/png' }));
  const { rerender } = render(
    <MediaPreview reference="old" mime="image/png" />,
  );
  rerender(<MediaPreview reference="new" mime="image/png" />);
  await screen.findByAltText('Generated result');
  expect(mock.download.mock.calls[0][1].aborted).toBe(true);
  await act(async () => old.resolve(new Blob(['old'])));
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  expect(screen.getByAltText('Generated result')).toHaveAttribute(
    'src',
    'blob:fixture-1',
  );
});

it('releases a prior loaded URL when MIME changes and ignores late decode events', async () => {
  mock.download
    .mockResolvedValueOnce(new Blob(['pixels'], { type: 'image/png' }))
    .mockResolvedValueOnce(new Blob(['sound'], { type: 'audio/mpeg' }));
  const { rerender } = render(
    <MediaPreview reference="same" mime="image/png" />,
  );
  const previous = await screen.findByAltText('Generated result');
  rerender(<MediaPreview reference="same" mime="audio/mpeg" />);
  const current = await screen.findByLabelText('Generated audio result');
  fireEvent.error(previous);
  expect(current).toBeInTheDocument();
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:fixture-1');
  expect(mock.download).toHaveBeenCalledTimes(2);
});

it.each(['resolve', 'reject'])(
  'ignores pending %s after unmount',
  async (outcome) => {
    const request = pending<Blob>();
    mock.download.mockReturnValue(request.promise);
    const { unmount } = render(
      <MediaPreview reference="pending" mime="video/mp4" />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Loading generated result',
    );
    unmount();
    await act(async () =>
      outcome === 'resolve'
        ? request.resolve(new Blob(['late']))
        : request.reject(new TypeError('late')),
    );
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    expect(mock.download.mock.calls[0][1].aborted).toBe(true);
  },
);

it.each(['audio/mpeg', 'video/mp4'])(
  'stops native %s playback and releases its source on unmount',
  async (mime) => {
    mock.download.mockResolvedValue(new Blob(['media'], { type: mime }));
    const { unmount, container } = render(
      <MediaPreview reference="player" mime={mime} />,
    );
    await screen.findByRole('link');
    const player = container.querySelector('audio,video')!;
    expect(player).toHaveAttribute('src', 'blob:fixture-1');
    unmount();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalledTimes(1);
    expect(player).not.toHaveAttribute('src');
    expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith(
      'blob:fixture-1',
    );
  },
);

it('embeds a real PDF in a same-origin frame typed as PDF', async () => {
  mock.download.mockResolvedValue(
    new Blob(['%PDF-1.7 fixture'], { type: 'application/pdf' }),
  );
  let typed = '';
  vi.mocked(URL.createObjectURL).mockImplementation((blob) => {
    typed = (blob as Blob).type;
    return 'blob:pdf';
  });
  render(
    <MediaPreview
      reference="opaque-reference"
      mime="application/pdf"
      label="report.pdf"
      downloadName="report.pdf"
    />,
  );
  const frame = await screen.findByTitle('report.pdf (PDF)');
  expect(frame.tagName).toBe('IFRAME');
  expect(frame).toHaveAttribute('src', 'blob:pdf');
  expect(typed).toBe('application/pdf');
  expect(
    screen.getByRole('link', { name: 'Download report.pdf' }),
  ).toHaveAttribute('download', 'report.pdf');
});

it('shows bounded plain-text attachments as code, never as markup', async () => {
  mock.download.mockResolvedValue(
    new Blob(['const answer = 42;\n<b>not bold</b>'], { type: 'text/plain' }),
  );
  const { container } = render(
    <MediaPreview
      reference="opaque-reference"
      mime="text/plain"
      label="notes.ts"
      downloadName="notes.ts"
    />,
  );
  expect(await screen.findByText(/const answer = 42;/)).toBeVisible();
  expect(container.querySelector('b')).toBeNull();
  expect(container.querySelector('iframe,object,embed')).toBeNull();
});

it('downloads a result once when it mounts again, and again only on retry', async () => {
  mock.download.mockResolvedValue(new Blob(['pixels'], { type: 'image/png' }));
  const first = render(
    <MediaPreview reference="settled-once" mime="image/png" />,
  );
  await screen.findByAltText('Generated result');
  first.unmount();
  // A live preview becoming the settled one reuses the same bytes (B22).
  render(<MediaPreview reference="settled-once" mime="image/png" />);
  await screen.findByAltText('Generated result');
  expect(mock.download).toHaveBeenCalledTimes(1);
  fireEvent.error(screen.getByAltText('Generated result'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Retry generated result' }),
  );
  await screen.findByAltText('Generated result');
  expect(mock.download).toHaveBeenCalledTimes(2);
});
