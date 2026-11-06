import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ResourceView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import ContextOutputs, { type ConversationOutput } from './ContextOutputs';

const runtime = vi.hoisted(() => ({
  attachmentThumbnail: vi.fn(),
  download: vi.fn(),
  intent: vi.fn(),
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

function output(n: number, mime = 'image/png'): ConversationOutput {
  return { id: `media-${n}`, reference: `conversation-a:media-${n}`, mime };
}

function renderOutputs(
  outputs: ConversationOutput[],
  {
    resources = [],
    onUseOutputInCode,
  }: {
    resources?: ResourceView[];
    onUseOutputInCode?: (output: { reference: string; mime: string }) => void;
  } = {},
) {
  return render(
    <OverlayProvider>
      <ContextOutputs
        conversationId="conversation-a"
        conversationRevision="7"
        outputs={outputs}
        resources={resources}
        onUseOutputInCode={onUseOutputInCode}
      />
    </OverlayProvider>,
  );
}

const tiles = () =>
  screen
    .getAllByRole('button', { name: /^Open / })
    .map((tile) => tile.getAttribute('aria-label'));

async function choose(tile: string, action: string) {
  const menu = screen.getByRole('button', { name: `Actions for ${tile}` });
  await act(async () => fireEvent.keyDown(menu, { key: 'Enter' }));
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: action })),
  );
}

it('names each output by its kind and place in the chat, newest first', () => {
  renderOutputs([
    output(1),
    output(2),
    output(3, 'video/mp4'),
    output(4, 'image/jpeg'),
  ]);
  expect(tiles()).toEqual([
    'Open Image 3',
    'Open Video 1',
    'Open Image 2',
    'Open Image 1',
  ]);
});

it('shows the newest six and folds the rest under Show all', () => {
  renderOutputs(Array.from({ length: 8 }, (_, n) => output(n + 1)));
  expect(tiles()).toEqual([
    'Open Image 8',
    'Open Image 7',
    'Open Image 6',
    'Open Image 5',
    'Open Image 4',
    'Open Image 3',
  ]);
  const more = screen.getByRole('button', { name: 'Show all 8' });
  expect(more).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(more);
  expect(tiles()).toHaveLength(8);
  fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }));
  expect(tiles()).toHaveLength(6);
});

it('draws a picture from its small thumbnail and a video as a play mark, without downloading either', async () => {
  const { container } = renderOutputs([output(1), output(2, 'video/mp4')]);
  await waitFor(() =>
    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      'blob:thumbnail',
    ),
  );
  expect(runtime.attachmentThumbnail).toHaveBeenCalledOnce();
  expect(runtime.attachmentThumbnail.mock.calls[0][0]).toBe(
    'conversation-a:media-1',
  );
  // One rendering per result (B22): the media itself stays in the conversation.
  expect(runtime.download).not.toHaveBeenCalled();
});

it('opens an output full size in the media viewer', () => {
  renderOutputs([output(1, 'video/mp4')]);
  fireEvent.click(screen.getByRole('button', { name: 'Open Video 1' }));
  expect(viewer).toHaveBeenCalledWith(expect.anything(), {
    reference: 'conversation-a:media-1',
    mime: 'video/mp4',
    name: 'Video 1',
  });
});

it('shows an output where it is in the conversation', async () => {
  const inline = document.createElement('div');
  inline.dataset.mediaRef = 'conversation-a:media-1';
  inline.scrollIntoView = vi.fn();
  document.body.append(inline);
  renderOutputs([output(1)]);
  await choose('Image 1', 'Show in conversation');
  expect(inline.scrollIntoView).toHaveBeenCalledOnce();
  inline.remove();
});

it('saves an output with one scoped command and hands it to the code folder without regenerating', async () => {
  runtime.intent.mockResolvedValue({
    status: 'completed',
    saved_name: 'output-fixture.png',
  });
  const onUseOutputInCode = vi.fn();
  renderOutputs([output(1)], {
    resources: [resource('workspace', 'workspace-a')],
    onUseOutputInCode,
  });
  await choose('Image 1', 'Use in code folder');
  expect(onUseOutputInCode).toHaveBeenCalledWith(
    expect.objectContaining({
      reference: 'conversation-a:media-1',
      mime: 'image/png',
    }),
  );
  await choose('Image 1', 'Save to workspace');
  await waitFor(() =>
    expect(runtime.intent).toHaveBeenCalledWith(
      'conversation-a',
      'media.save',
      { media_ref: 'conversation-a:media-1' },
      '7',
    ),
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Saved to outputs/output-fixture.png',
  );
});

it('offers the code folder and designs only when the conversation has them', async () => {
  renderOutputs([output(1)], { onUseOutputInCode: vi.fn() });
  const menu = screen.getByRole('button', { name: 'Actions for Image 1' });
  await act(async () => fireEvent.keyDown(menu, { key: 'Enter' }));
  expect(
    screen.getAllByRole('menuitem').map((item) => item.textContent),
  ).toEqual(['Show in conversation', 'Save to workspace']);
});

it('copies one output into the chosen design without generating again', async () => {
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
  await choose('Image 1', 'Add to Poster');
  await waitFor(() =>
    expect(runtime.designSession.upload).toHaveBeenCalledOnce(),
  );
  expect(runtime.designSession.upload.mock.calls[0][0]).toBeInstanceOf(File);
  expect(runtime.designSession.upload.mock.calls[0][1]).toBe(
    'resource-revision',
  );
});

it('shows a failed save in words', async () => {
  runtime.intent.mockResolvedValue({ status: 'failed' });
  renderOutputs([output(1)]);
  await choose('Image 1', 'Save to workspace');
  expect(await screen.findByRole('alert')).toBeVisible();
});
