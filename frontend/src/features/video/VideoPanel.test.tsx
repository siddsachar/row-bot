import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { VideoProjectView } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import VideoPanel from './VideoPanel';
import { VideoProjectCard } from './VideoProjectCard';
import { onVideoRequest, type VideoRequest } from './open-video';

const AT = new Date().toISOString();

function view(patch: Partial<VideoProjectView> = {}): VideoProjectView {
  return {
    project_id: 'vid-1a2b3c4d',
    conversation_id: 'conversation-a',
    title: 'Launch demo',
    latest: 2,
    versions: [
      { number: 2, at: AT, by: 'row-bot', note: 'Shorter intro' },
      { number: 1, at: AT, by: 'row-bot', note: 'First cut' },
    ],
    number: 2,
    scenes: [
      {
        scene_id: 's0a0a0a',
        source: 'signup.mp4',
        start: 1,
        end: 4,
        speed: 1,
        zoom: null,
        card: 'Sign up',
        line: 'First, sign up.',
        voiced: true,
        at: 0,
        length: 3,
      },
      {
        scene_id: 's0b0b0b',
        source: 'pricing.mp4',
        start: 0,
        end: null,
        speed: 2,
        zoom: [0.5, 0, 0.5, 0.5],
        card: '',
        line: 'Then pick a plan.',
        voiced: true,
        at: 3,
        length: 2.5,
      },
    ],
    changed: ['s0a0a0a'],
    music: '',
    music_volume: 0.3,
    captions: false,
    voice_lines: true,
    status: 'made',
    problem: '',
    edit: {
      edit_id: 'edit-00000001',
      conversation_id: 'conversation-a',
      kind: 'video',
      summary: 'Joined 2 videos and cut 2 scenes',
      status: 'saved',
      progress: 1,
      download_received: null,
      download_total: null,
      started_at: AT,
      ended_at: AT,
      seconds: 4,
      duration_seconds: 5.5,
      output_name: 'Launch demo v2.mp4',
      media_ref: 'conversation-a:launch',
      mime_type: 'video/mp4',
      size_bytes: 2048,
      frame_count: 0,
      stopped_by: '',
      problem: '',
      detail: '',
      ffmpeg: null,
    },
    checks: [
      'Length 0:05, 1280×720.',
      'Sound: present (average -18 dB, peak -2 dB).',
    ],
    checks_passed: true,
    aspect: '',
    exports: [],
    brand: true,
    ...patch,
  };
}

beforeEach(() => {
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: () => 'blob:launch',
    revokeObjectURL: () => undefined,
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function setup(
  shown: VideoProjectView = view(),
  platform = {} as ClientPlatform,
) {
  const current = { value: shown };
  const controller = {
    subscribe: () => () => {},
    getSnapshot: () => ({
      status: 'ready',
      conversation: { id: 'conversation-a', revision: '7' },
    }),
    videoProject: vi.fn(async () => current.value),
    changeVideoProject: vi.fn(async (_id: string, change) => {
      current.value = view({
        number: 3,
        latest: 3,
        status: 'voicing',
        scenes: change.scenes.map(
          (scene: VideoProjectView['scenes'][number]) => ({
            ...shown.scenes.find((item) => item.scene_id === scene.scene_id)!,
            ...scene,
          }),
        ),
      });
      return current.value;
    }),
    restoreVideoProject: vi.fn(async () => view({ number: 3, latest: 3 })),
    download: vi.fn(async () => new Blob(['mp4'], { type: 'video/mp4' })),
    controlsSettled: vi.fn(async () => undefined),
    workspaceFor: vi.fn(async () => ({
      revision: '7',
      controls: {
        model_selection: { provider_id: 'fixture', model_ref: 'fixture::m' },
      },
    })),
    intent: vi.fn(async () => ({ status: 'accepted' })),
    showLatest: vi.fn(),
  } as unknown as ClientController & Record<string, ReturnType<typeof vi.fn>>;
  render(
    <RuntimeContext.Provider value={{ controller, platform }}>
      <OverlayProvider>
        <VideoPanel
          conversationId="conversation-a"
          projectId="vid-1a2b3c4d"
          visible
        />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
  return { controller };
}

it('plays the made video above its scenes, each with its line, speed and what changed', async () => {
  setup();
  const panel = await screen.findByRole('region', {
    name: 'Launch demo video',
  });
  await waitFor(() =>
    expect(
      within(panel).getByLabelText('Launch demo, version 2'),
    ).toHaveAttribute('src', 'blob:launch'),
  );
  const scenes = within(panel).getAllByRole('listitem').slice(0, 2);
  expect(scenes[0]).toHaveAttribute('data-changed', 'true');
  expect(scenes[1]).not.toHaveAttribute('data-changed');
  expect(within(scenes[1]).getByText('2.5 s')).toBeVisible();
  expect(within(scenes[1]).getByLabelText('Scene 2 speed')).toHaveValue('2');
  expect(within(scenes[0]).getByLabelText('Scene 1 line')).toHaveValue(
    'First, sign up.',
  );
  expect(
    within(panel).getByRole('region', { name: 'Review' }),
  ).toHaveTextContent('Ready to share');
});

it('saves edits as a new version made on the one shown, and offers nothing until something changed', async () => {
  const { controller } = setup();
  const line = await screen.findByLabelText('Scene 2 line');
  expect(
    screen.queryByRole('group', { name: 'Unsaved changes' }),
  ).not.toBeInTheDocument();
  await userEvent.clear(line);
  await userEvent.type(line, 'Then pick the plan that fits.');
  await userEvent.click(
    screen.getByRole('button', { name: 'Move scene 2 up' }),
  );
  await userEvent.click(screen.getByRole('button', { name: 'Save and make' }));
  expect(controller.changeVideoProject).toHaveBeenCalledWith('vid-1a2b3c4d', {
    base: 2,
    brand: true,
    captions: false,
    note: 'Edited in the panel',
    scenes: [
      expect.objectContaining({
        scene_id: 's0b0b0b',
        line: 'Then pick the plan that fits.',
      }),
      expect.objectContaining({ scene_id: 's0a0a0a', line: 'First, sign up.' }),
    ],
  });
  expect(await screen.findByText('Voicing')).toBeVisible();
});

it('goes back a version in one tap, and Post to X asks Row-Bot in the chat', async () => {
  const { controller } = setup();
  await userEvent.click(
    await screen.findByRole('button', { name: 'Back to the version before' }),
  );
  expect(controller.restoreVideoProject).toHaveBeenCalledWith(
    'vid-1a2b3c4d',
    1,
  );
  await userEvent.click(
    await screen.findByRole('button', { name: 'Post to X' }),
  );
  await waitFor(() => expect(controller.intent).toHaveBeenCalled());
  const [conversation, kind, body] = (
    controller.intent as unknown as ReturnType<typeof vi.fn>
  ).mock.calls[0];
  expect([conversation, kind]).toEqual([
    'conversation-a',
    'conversation.submit',
  ]);
  expect(body.text).toContain('(vid-1a2b3c4d) to X. Show me the post first.');
});

it("the chat's card says how the video stands and opens it beside the chat", async () => {
  const opened: VideoRequest[] = [];
  const stop = onVideoRequest((request) => {
    opened.push(request);
    return true;
  });
  const controller = {
    subscribe: () => () => {},
    getSnapshot: () => ({ status: 'ready' }),
    videoProject: vi.fn(async () => view()),
  } as unknown as ClientController;
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <VideoProjectCard
        projectId="vid-1a2b3c4d"
        conversationId="conversation-a"
      />
    </RuntimeContext.Provider>,
  );
  const card = await screen.findByRole('group', {
    name: 'Launch demo · video',
  });
  expect(card).toHaveTextContent('Made');
  expect(card).toHaveTextContent('Version 2 of 2');
  await userEvent.click(within(card).getByRole('button', { name: 'Open' }));
  expect(opened).toEqual([
    {
      conversationId: 'conversation-a',
      projectId: 'vid-1a2b3c4d',
      title: 'Launch demo',
    },
  ]);
  stop();
});

it('a version narrated as a whole shows its narration on the timeline, not each scene’s line', async () => {
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        matches: query === '(min-width: 768px)',
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
  setup(view({ number: 5, latest: 5, narration: 'launch-script.wav' }));
  await userEvent.click(await screen.findByRole('radio', { name: 'Timeline' }));
  const timeline = screen.getByRole('application', { name: /^Timeline/ });
  expect(within(timeline).getByLabelText('Narration')).toHaveTextContent(
    'launch-script.wav',
  );
  expect(within(timeline).queryByLabelText('Voice')).not.toBeInTheDocument();
});

it('a title card scene sets how long it shows, with no speed or trim', async () => {
  const card = {
    ...view().scenes[0],
    scene_id: 's0c0c0c',
    source: '',
    start: 0,
    end: 3,
    card: 'Row-Bot 5.3',
    line: '',
  };
  // A version no earlier test left unsaved edits on.
  const { controller } = setup(
    view({ number: 5, latest: 5, scenes: [card, view().scenes[1]] }),
  );
  const seconds = await screen.findByLabelText('Scene 1 lasts, in seconds');
  expect(seconds).toHaveValue(3);
  expect(screen.queryByLabelText('Scene 1 speed')).not.toBeInTheDocument();
  expect(screen.getByText('A title card on your brand colour')).toBeVisible();
  expect(screen.getByLabelText('Scene 2 speed')).toHaveValue('2');
  fireEvent.change(seconds, { target: { value: '4' } });
  await userEvent.click(screen.getByRole('button', { name: 'Save and make' }));
  expect(controller.changeVideoProject).toHaveBeenCalledWith(
    'vid-1a2b3c4d',
    expect.objectContaining({
      scenes: [
        expect.objectContaining({ scene_id: 's0c0c0c', source: '', end: 4 }),
        expect.objectContaining({ scene_id: 's0b0b0b' }),
      ],
    }),
  );
});

it('the timeline trims, splits at the playhead, removes and undoes with the keys', async () => {
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        matches: query === '(min-width: 768px)',
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
  setup();
  await userEvent.click(await screen.findByRole('radio', { name: 'Timeline' }));
  const timeline = screen.getByRole('application', { name: /^Timeline/ });
  const video = await screen.findByLabelText('Launch demo, version 2');
  // The playhead 1 s into the first scene: its own video's 2 s (it starts at 1 s).
  Object.defineProperty(video, 'currentTime', {
    value: 1,
    configurable: true,
    writable: true,
  });
  fireEvent.timeUpdate(video);
  await userEvent.click(
    within(timeline).getByRole('button', { name: 'Scene 1, 3.0 s' }),
  );
  timeline.focus();
  await userEvent.keyboard('s');
  expect(
    within(timeline).getByRole('button', { name: 'Scene 1, 1.0 s' }),
  ).toBeVisible();
  expect(
    within(timeline).getByRole('button', { name: 'Scene 2, 2.0 s' }),
  ).toBeVisible();
  await userEvent.keyboard('{Delete}');
  expect(within(timeline).getAllByRole('button')).toHaveLength(2);
  await userEvent.keyboard('{Control>}z{/Control}');
  expect(within(timeline).getAllByRole('button')).toHaveLength(3);
  expect(screen.getByRole('group', { name: 'Unsaved changes' })).toBeVisible();
});

it('Download saves the made video through the app, and says where it went', async () => {
  const save = vi.fn(async () => ({
    status: 'ok' as const,
    value: {
      kind: 'exports' as const,
      fileName: 'Launch demo v2.mp4',
      folder: 'Exports',
      reveal: async () => true,
    },
  }));
  // A version no earlier test left unsaved edits on (they hide the player's tools).
  const made = view().edit!;
  setup(view({ number: 4, latest: 4 }), { save } as unknown as ClientPlatform);
  await userEvent.click(
    await screen.findByRole('button', { name: 'Download' }),
  );
  expect(save).toHaveBeenCalledWith(made.media_ref, made.output_name);
  expect(
    await screen.findByText('Saved Launch demo v2.mp4 to Exports.'),
  ).toBeVisible();
});

it('a made version offers its other shapes, says where each fits, and downloads the ones made', async () => {
  const made = view().edit!;
  const { controller } = setup(
    view({
      aspect: '16:9',
      exports: [
        {
          shape: 'gif',
          edit: {
            ...made,
            edit_id: 'edit-00000002',
            output_name: 'Launch demo v2.gif',
            mime_type: 'image/gif',
          },
        },
      ],
      edit: { ...made, duration_seconds: 75 },
    }),
  );
  const shapes = await screen.findByRole('list', { name: 'Other shapes' });
  const rows = within(shapes).getAllByRole('listitem');
  expect(rows.map((row) => row.textContent)).toEqual([
    expect.stringContaining(
      'Tall 9:16Shorts, Reels · longer than Shorts allow (1:00)',
    ),
    expect.stringContaining('Square 1:1X, Instagram'),
    expect.stringContaining('GIF'),
  ]);
  expect(
    within(rows[2]).getByRole('button', { name: 'Download GIF' }),
  ).toBeVisible();
  controller.exportVideoProject = vi.fn(async () => view());
  await userEvent.click(within(rows[0]).getByRole('button', { name: 'Make' }));
  expect(controller.exportVideoProject).toHaveBeenCalledWith(
    'vid-1a2b3c4d',
    '9:16',
  );
});

it('while edited, the player previews the edits live from each scene’s own video, without making anything', async () => {
  const shown = view();
  shown.scenes = shown.scenes.map((scene, index) => ({
    ...scene,
    source_ref: `conversation-a:${scene.source}`,
    voice_ref: index === 0 ? 'conversation-a:line-1.wav' : null,
  }));
  const { controller } = setup(shown);
  await screen.findByLabelText('Launch demo, version 2');
  await userEvent.type(await screen.findByLabelText('Scene 1 card'), '!');
  const preview = await screen.findByLabelText('Preview of your edits');
  expect(
    screen.queryByLabelText('Launch demo, version 2'),
  ).not.toBeInTheDocument();
  expect(within(preview).getByLabelText('Scene 1')).toHaveAttribute(
    'src',
    'blob:launch',
  );
  expect(preview).toHaveTextContent('Sign up!');
  expect(preview).toHaveTextContent('Scene 1 of 2');
  await waitFor(() =>
    expect(controller.download).toHaveBeenCalledWith(
      'conversation-a:signup.mp4',
      expect.anything(),
    ),
  );
  expect(controller.download).toHaveBeenCalledWith(
    'conversation-a:line-1.wav',
    expect.anything(),
  );
  expect(controller.changeVideoProject).not.toHaveBeenCalled();
});
