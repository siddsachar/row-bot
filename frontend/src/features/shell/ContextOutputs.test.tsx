import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { OutputView, ResourceView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import ContextOutputs from './ContextOutputs';

const runtime = vi.hoisted(() => ({
  attachmentThumbnail: vi.fn(),
  download: vi.fn(),
  intent: vi.fn(),
  outputs: vi.fn(),
  designSession: { upload: vi.fn() },
}));
const viewer = vi.hoisted(() => vi.fn());

vi.mock('../../runtime', () => ({
  useRuntime: () => ({
    controller: runtime,
    artifactDesignSessions: { get: () => runtime.designSession },
  }),
}));
vi.mock('./MediaPreview', () => ({ openMediaViewer: viewer }));

beforeEach(() => {
  vi.clearAllMocks();
  runtime.attachmentThumbnail.mockResolvedValue(
    new Blob(['png'], { type: 'image/png' }),
  );
  URL.createObjectURL = vi.fn(() => 'blob:thumbnail');
  URL.revokeObjectURL = vi.fn();
});

function resource(kind: 'workspace' | 'artifact', id: string, title = id) {
  return {
    resource_ref: `conversation-a:${id}`,
    conversation_revision: '1',
    resource_revision: 'resource-revision',
    title,
    available: true,
    binding: {
      binding_id: id,
      resource_id: `resource-${id}`,
      kind,
      role: 'primary',
      revision: '1',
    },
  } satisfies ResourceView;
}

const MIME = {
  image: 'image/png',
  video: 'video/mp4',
  audio: 'audio/wav',
  document: 'application/pdf',
  file: 'application/octet-stream',
} as const;

function output(
  n: number,
  kind: OutputView['kind'] = 'image',
  extra: Partial<OutputView> = {},
): OutputView {
  const fileName = `picture-${n}.png`;
  const version = {
    ref: `conversation-a:media-${n}`,
    version: 1,
    role: 'final' as const,
    file_name: fileName,
    mime_type: MIME[kind],
    size: 2048,
    created_at: '2026-11-22T10:00:00Z',
  };
  return {
    ...version,
    id: n.toString(16).padStart(24, '0'),
    conversation_id: 'conversation-a',
    name: `picture-${n}`,
    kind,
    versions: [version],
    version_count: 1,
    working_count: 0,
    updated_at: '2026-11-22T10:00:00Z',
    ...extra,
  };
}

/** A film with three versions: the second presented, the third a newer draft. */
function film(): OutputView {
  const version = (n: number, role: 'final' | 'draft') => ({
    ref: `conversation-a:film-${n}`,
    version: n,
    role,
    file_name: 'Birthday montage.mp4',
    mime_type: 'video/mp4',
    size: 4096,
    created_at: '2026-11-22T10:00:00Z',
  });
  return {
    ...output(9, 'video'),
    ...version(2, 'final'),
    name: 'Birthday montage',
    versions: [version(3, 'draft'), version(2, 'final'), version(1, 'draft')],
    version_count: 3,
    working_count: 4,
  };
}

function renderOutputs(
  outputs: OutputView[],
  {
    resources = [],
    workingCount = 0,
    onUseOutputInCode,
  }: {
    resources?: ResourceView[];
    workingCount?: number;
    onUseOutputInCode?: (output: { reference: string; mime: string }) => void;
  } = {},
) {
  return render(
    <OverlayProvider>
      <ContextOutputs
        conversationId="conversation-a"
        conversationRevision="7"
        outputs={outputs}
        workingCount={workingCount}
        refresh="1"
        resources={resources}
        onUseOutputInCode={onUseOutputInCode}
      />
    </OverlayProvider>,
  );
}

const tiles = (list = 'Outputs') =>
  Array.from(
    screen
      .getByRole('list', { name: list })
      .querySelectorAll('.context-output-open'),
    (tile) => tile.getAttribute('aria-label'),
  );

async function openMenu(tile: string) {
  const menu = screen.getByRole('button', { name: `Actions for ${tile}` });
  await act(async () => fireEvent.keyDown(menu, { key: 'Enter' }));
}

async function choose(tile: string, action: string) {
  await openMenu(tile);
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: action })),
  );
}

it('shows one tile per output in the order given, a made name read as words', () => {
  renderOutputs([
    output(1, 'image', { name: 'waves-at-dusk' }),
    film(),
    output(3, 'document', { name: 'Q3 budget £480' }),
  ]);
  expect(tiles()).toEqual([
    'Open Waves at dusk',
    'Open Birthday montage',
    'Open Q3 budget £480',
  ]);
});

it('marks a draft and an output with versions on its tile, and names its file in the tooltip', async () => {
  const draft = output(1, 'video', {
    name: 'Trailer',
    role: 'draft',
    file_name: 'Trailer.mp4',
  });
  renderOutputs([film(), draft]);
  const badges = [...document.querySelectorAll('.context-output-badges')].map(
    (badge) => badge.textContent,
  );
  expect(badges).toEqual(['3', 'Draft']);
  act(() =>
    screen.getByRole('button', { name: 'Open Birthday montage' }).focus(),
  );
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    'Birthday montage.mp4 · v2 of 3 · Final',
  );
});

it('lists an output’s versions in its menu, newest first, and opens the one chosen', async () => {
  renderOutputs([film()]);
  await openMenu('Birthday montage');
  const versions = screen
    .getAllByRole('menuitem')
    .filter((item) => item.textContent?.startsWith('Version'));
  expect(versions.map((item) => item.textContent)).toEqual([
    'Version 3Draft',
    'Version 2Final',
    'Version 1Draft',
  ]);
  expect(versions[1]).toHaveAttribute('aria-current', 'true');
  await act(async () => fireEvent.click(versions[0]));
  expect(viewer).toHaveBeenCalledWith(expect.anything(), {
    reference: 'conversation-a:film-3',
    mime: 'video/mp4',
    name: 'Birthday montage · v3',
    size: 4096,
  });
});

it('opens an output’s shown version full size in the media viewer', () => {
  renderOutputs([film()]);
  fireEvent.click(
    screen.getByRole('button', { name: 'Open Birthday montage' }),
  );
  expect(viewer).toHaveBeenCalledWith(expect.anything(), {
    reference: 'conversation-a:film-2',
    mime: 'video/mp4',
    name: 'Birthday montage · v2',
    size: 4096,
  });
});

it('shows the newest six and folds the rest under Show all', () => {
  renderOutputs(Array.from({ length: 8 }, (_, n) => output(n + 1)));
  expect(tiles()).toHaveLength(6);
  const more = screen.getByRole('button', { name: 'Show all 8' });
  expect(more).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(more);
  expect(tiles()).toHaveLength(8);
  fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }));
  expect(tiles()).toHaveLength(6);
});

it('draws a picture and a video from their small thumbnails, and a document by its kind, without downloading any', async () => {
  const { container } = renderOutputs([
    output(1),
    output(2, 'video'),
    output(3, 'document'),
  ]);
  await waitFor(() =>
    expect(container.querySelectorAll('img')).toHaveLength(2),
  );
  expect(
    runtime.attachmentThumbnail.mock.calls.map(([reference]) => reference),
  ).toEqual(['conversation-a:media-1', 'conversation-a:media-2']);
  // A video's poster frame (I20) keeps its play mark; a picture has none.
  expect(container.querySelectorAll('.context-output-play')).toHaveLength(1);
  // One rendering per result (B22): the media itself stays in the conversation.
  expect(runtime.download).not.toHaveBeenCalled();
});

it('keeps the play mark alone for a video with no poster', async () => {
  runtime.attachmentThumbnail.mockRejectedValue(new Error('not_found'));
  const { container } = renderOutputs([output(1, 'video')]);
  await waitFor(() => expect(runtime.attachmentThumbnail).toHaveBeenCalled());
  expect(container.querySelector('img')).toBeNull();
  expect(container.querySelector('.context-output-play')).not.toBeNull();
});

it('shows an output where it is in the conversation', async () => {
  const inline = document.createElement('div');
  inline.dataset.mediaRef = 'conversation-a:media-1';
  inline.scrollIntoView = vi.fn();
  document.body.append(inline);
  renderOutputs([output(1)]);
  await choose('Picture 1', 'Show in conversation');
  expect(inline.scrollIntoView).toHaveBeenCalledOnce();
  inline.remove();
});

it('saves any output to the folder it names, and hands a picture to the code folder without regenerating', async () => {
  runtime.intent.mockResolvedValue({
    status: 'completed',
    saved_name: 'Saved outputs/Q3 budget £480.pdf',
  });
  const onUseOutputInCode = vi.fn();
  renderOutputs(
    [
      output(1),
      output(2, 'document', {
        name: 'Q3 budget £480',
        file_name: 'Q3 budget £480.pdf',
      }),
    ],
    { resources: [resource('workspace', 'workspace-a')], onUseOutputInCode },
  );
  await choose('Picture 1', 'Use in code folder');
  expect(onUseOutputInCode).toHaveBeenCalledWith({
    reference: 'conversation-a:media-1',
    mime: 'image/png',
  });
  await openMenu('Q3 budget £480');
  const save = screen.getByRole('menuitem', { name: 'Save to workspace' });
  expect(save).toHaveAccessibleDescription('Saved outputs/Q3 budget £480.pdf');
  await act(async () => fireEvent.click(save));
  await waitFor(() =>
    expect(runtime.intent).toHaveBeenCalledWith(
      'conversation-a',
      'media.save',
      { media_ref: 'conversation-a:media-2' },
      '7',
    ),
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Saved to your workspace as Saved outputs/Q3 budget £480.pdf',
  );
});

it('offers the code folder and designs only for pictures and videos, and only when the chat has them', async () => {
  renderOutputs([output(1), output(2, 'audio')], {
    onUseOutputInCode: vi.fn(),
    resources: [
      resource('workspace', 'workspace-a'),
      resource('artifact', 'artifact-a', 'Poster'),
    ],
  });
  await openMenu('Picture 2');
  expect(screen.getAllByRole('menuitem')).toHaveLength(2);
  expect(
    screen.queryByRole('menuitem', { name: 'Use in code folder' }),
  ).toBeNull();
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
  await openMenu('Picture 1');
  for (const name of ['Use in code folder', 'Add to design'])
    expect(screen.getByRole('menuitem', { name })).toBeVisible();
});

it('shows only the working files toggle in a chat that made nothing for the person yet', () => {
  renderOutputs([], { workingCount: 2 });
  expect(screen.queryByRole('list', { name: 'Outputs' })).toBeNull();
  expect(
    screen.getByRole('switch', { name: 'Show working files' }),
  ).toBeVisible();
});

it('copies one picture into the chosen design under its own name, without generating again', async () => {
  runtime.download.mockResolvedValue(
    new Blob(['fixture'], { type: 'image/png' }),
  );
  runtime.designSession.upload.mockResolvedValue({ status: 'saved' });
  renderOutputs([output(1)], {
    resources: [
      resource('artifact', 'artifact-a', 'Launch deck'),
      resource('artifact', 'artifact-b', 'Poster'),
    ],
  });
  await choose('Picture 1', 'Add to Poster');
  await waitFor(() =>
    expect(runtime.designSession.upload).toHaveBeenCalledOnce(),
  );
  const [file, revision] = runtime.designSession.upload.mock.calls[0];
  expect(file).toBeInstanceOf(File);
  expect((file as File).name).toBe('picture-1.png');
  expect(revision).toBe('resource-revision');
});

it('shows a failed save in words', async () => {
  runtime.intent.mockResolvedValue({ status: 'failed' });
  renderOutputs([output(1)]);
  await choose('Picture 1', 'Save to workspace');
  expect(await screen.findByRole('alert')).toBeVisible();
});

it('reads the working files only when the toggle shows them', async () => {
  runtime.outputs.mockResolvedValue({
    items: [
      output(5, 'video', {
        name: 'recording',
        role: 'working',
        file_name: 'generated-recording.mp4',
      }),
    ],
  });
  renderOutputs([film()], { workingCount: 4 });
  expect(runtime.outputs).not.toHaveBeenCalled();
  expect(screen.queryByRole('list', { name: 'Working files' })).toBeNull();
  const toggle = screen.getByRole('switch', { name: 'Show working files' });
  expect(toggle).not.toBeChecked();
  await act(async () => fireEvent.click(toggle));
  expect(runtime.outputs).toHaveBeenCalledWith(
    { conversation_id: 'conversation-a', show: 'working' },
    expect.any(AbortSignal),
  );
  expect(await screen.findByRole('list', { name: 'Working files' })).toBe(
    screen.getByRole('list', { name: 'Working files' }),
  );
  expect(tiles('Working files')).toEqual(['Open Recording']);
  await act(async () => fireEvent.click(toggle));
  expect(screen.queryByRole('list', { name: 'Working files' })).toBeNull();
});

it('hides the toggle in a chat with no working files', () => {
  renderOutputs([output(1)]);
  expect(screen.queryByRole('switch')).toBeNull();
});
