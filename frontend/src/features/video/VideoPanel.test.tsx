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
    videoSceneFrame: vi.fn(async () => new Blob(['jpeg'])),
    controlsSettled: vi.fn(async () => undefined),
    workspaceFor: vi.fn(async () => ({
      revision: '7',
      controls: {
        model_selection: { provider_id: 'fixture', model_ref: 'fixture::m' },
      },
    })),
    intent: vi.fn(async () => ({ status: 'accepted' })),
    showLatest: vi.fn(),
    settingsSnapshot: vi.fn(async () => ({
      voice: {
        tts_voice_options: [
          { value: 'af_bella', label: 'Bella (American Female)' },
          { value: 'bf_emma', label: 'Emma (British Female)' },
        ],
      },
    })),
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
    voice: '',
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

it("the chat's card says how the video stands, plays it, and opens it beside the chat", async () => {
  const opened: VideoRequest[] = [];
  const stop = onVideoRequest((request) => {
    opened.push(request);
    return true;
  });
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:draft');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
  const download = vi.fn(
    async () => new Blob(['video'], { type: 'video/mp4' }),
  );
  const controller = {
    subscribe: () => () => {},
    getSnapshot: () => ({ status: 'ready' }),
    videoProject: vi.fn(async () => view()),
    download,
  } as unknown as ClientController;
  const { container } = render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <OverlayProvider>
        <VideoProjectCard
          projectId="vid-1a2b3c4d"
          conversationId="conversation-a"
        />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
  const card = await screen.findByRole('group', {
    name: 'Launch demo · video',
  });
  expect(card).toHaveTextContent('Made');
  expect(card).toHaveTextContent('Version 2 of 2');
  // The made version plays in the card: the draft is looked at in the chat.
  await waitFor(() =>
    expect(container.querySelector('video')).toHaveAttribute(
      'src',
      'blob:draft',
    ),
  );
  expect(download).toHaveBeenCalledWith(
    view().edit?.media_ref,
    expect.anything(),
  );
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
    await screen.findByText('Saved Launch demo v2.mp4 to Exports'),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Show in folder' })).toBeVisible();
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
    expect.stringContaining('Square 1:1X, LinkedIn'),
    expect.stringContaining('Portrait 4:5Instagram, Facebook'),
    expect.stringContaining('GIF'),
  ]);
  expect(
    within(rows[3]).getByRole('button', { name: 'Download GIF' }),
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

function storyboard(patch: Partial<VideoProjectView> = {}): VideoProjectView {
  const scene = view().scenes[0];
  return view({
    title: 'First snow',
    number: 1,
    latest: 1,
    versions: [{ number: 1, at: AT, by: 'row-bot', note: 'First version' }],
    status: 'draft',
    edit: null,
    checks: [],
    checks_passed: null,
    changed: [],
    logline: 'A fox and a town meet winter.',
    scenes: [
      {
        ...scene,
        scene_id: 's0c0c0c',
        source: '',
        start: 0,
        end: 3,
        card: 'Winter',
        footage: 'card',
        at: null,
        length: null,
        voiced: false,
        approved: true,
      },
      {
        ...scene,
        scene_id: 's0f0f0f',
        source: '',
        start: 0,
        end: 4,
        card: '',
        line: 'The fox knew first.',
        footage: 'ai',
        shows: 'a fox in deep snow',
        at: null,
        length: null,
        voiced: false,
      },
      {
        ...scene,
        scene_id: 's0e0e0e',
        source: 'town.mp4',
        start: 0,
        end: 2,
        card: '',
        footage: 'clip',
        has_frame: true,
        at: null,
        length: null,
        voiced: false,
      },
    ],
    ...patch,
  });
}

it('a storyboard shows its logline and cards, and Make asks Row-Bot when footage is still to make', async () => {
  const { controller } = setup(storyboard());
  const bar = await screen.findByRole('region', { name: 'Storyboard' });
  expect(screen.getByText('A fox and a town meet winter.')).toBeVisible();
  expect(bar).toHaveTextContent('3 scenes · about 0:09 · 1 of 3 approved');
  expect(bar).toHaveTextContent('1 scene needs its footage');
  expect(
    screen.queryByLabelText('First snow, version 1'),
  ).not.toBeInTheDocument();
  const cards = within(
    screen.getByRole('list', { name: 'Storyboard' }),
  ).getAllByRole('listitem');
  expect(cards.map((card) => card.getAttribute('data-approved'))).toEqual([
    'true',
    null,
    null,
  ]);
  await userEvent.click(
    within(bar).getByRole('button', { name: 'Make video' }),
  );
  await waitFor(() => expect(controller.intent).toHaveBeenCalled());
  const body = (controller.intent as unknown as ReturnType<typeof vi.fn>).mock
    .calls[0][2];
  expect(body.text).toBe(
    'Make the video "First snow" (vid-1a2b3c4d) from its storyboard, version 1.',
  );
  expect(body.video_panel).toEqual({ action: 'make' });
});

it('a storyboard with all its footage is made from the panel, and its scenes are approved one by one', async () => {
  const board = storyboard({ number: 6, latest: 6 });
  board.scenes = board.scenes.filter((scene) => scene.footage !== 'ai');
  const { controller } = setup(board);
  controller.approveVideoScenes = vi.fn(async () => board);
  const making = view({ number: 6, latest: 6, status: 'voicing', edit: null });
  controller.makeVideoProject = vi.fn(async () => {
    vi.mocked(controller.videoProject).mockImplementation(async () => making);
    return making;
  });
  await userEvent.click(
    await screen.findByRole('button', { name: 'Approve scene 2' }),
  );
  expect(controller.approveVideoScenes).toHaveBeenCalledWith('vid-1a2b3c4d', {
    base: 6,
    scene_ids: ['s0e0e0e'],
    approved: true,
  });
  await userEvent.click(screen.getByRole('button', { name: 'Make video' }));
  expect(controller.makeVideoProject).toHaveBeenCalledWith('vid-1a2b3c4d');
  expect(await screen.findByText('Voicing')).toBeVisible();
});

function dialogue(): VideoProjectView {
  const made = view({
    number: 9,
    latest: 9,
    music: 'fit',
    music_mood: 'warm',
    on_beat: true,
    cast: [{ name: 'Maya', voice: 'af_bella' }],
  });
  made.scenes = [
    { ...made.scenes[0], cue: 'quiet', effects: ['rain'] },
    { ...made.scenes[1], speaker: 'Maya', cue: 'storm', effects: ['whoosh'] },
  ];
  return made;
}

it('each character’s voice is chosen beside the storyboard, and only a changed one is sent', async () => {
  const { controller } = setup(dialogue());
  const voices = await screen.findByRole('group', { name: 'Voices' });
  const maya = within(voices).getByLabelText("Maya's voice");
  await waitFor(() => expect(maya).toHaveValue('af_bella'));
  await userEvent.selectOptions(maya, 'bf_emma');
  await userEvent.click(screen.getByRole('button', { name: 'Save and make' }));
  expect(controller.changeVideoProject).toHaveBeenCalledWith(
    'vid-1a2b3c4d',
    expect.objectContaining({
      cast: [{ name: 'Maya', voice: 'bf_emma' }],
      scenes: [
        expect.objectContaining({ speaker: '', effects: ['rain'] }),
        expect.objectContaining({ speaker: 'Maya', effects: ['whoosh'] }),
      ],
    }),
  );
});

it('the look is set beside the storyboard, and only the choices changed are sent', async () => {
  // A version no other test edits: the panel keeps unsaved edits per version.
  const { controller } = setup(
    view({
      number: 7,
      latest: 7,
      versions: [{ number: 7, at: AT, by: 'row-bot', note: 'Film look' }],
      recipe: 'film',
      grade: 'cinematic',
      grain: true,
      letterbox: true,
    }),
  );
  const look = await screen.findByRole('group', { name: 'Look' });
  expect(within(look).getByLabelText('Grade')).toHaveValue('cinematic');
  expect(
    within(look).getByRole('button', { name: 'Film grain' }),
  ).toHaveAttribute('aria-pressed', 'true');
  await userEvent.selectOptions(
    within(look).getByLabelText('Grade'),
    'Black and white',
  );
  await userEvent.click(
    within(look).getByRole('button', { name: 'Crossfade between scenes' }),
  );
  await userEvent.click(screen.getByRole('button', { name: 'Save and make' }));
  const change = vi.mocked(controller.changeVideoProject).mock.calls[0][1];
  expect(change).toMatchObject({ grade: 'bw', transitions: 'crossfade' });
  expect(change).not.toHaveProperty('grain');
  expect(change).not.toHaveProperty('letterbox');
});

it('captions get their style beside the toggle, and a made video’s chapters copy in one tap', async () => {
  const writeText = vi.fn(async () => undefined);
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
  const { controller } = setup(
    view({
      number: 8,
      latest: 8,
      versions: [{ number: 8, at: AT, by: 'row-bot', note: 'Captioned' }],
      captions: true,
      caption_style: 'word',
      chapters: [
        { at: 0, title: 'Ten tips' },
        { at: 40, title: 'Pricing' },
        { at: 65, title: 'Wrap up' },
      ],
    }),
  );
  const style = await screen.findByLabelText('Caption style');
  expect(style).toHaveValue('word');
  const chapters = screen.getByRole('list', { name: 'Chapters' });
  expect(within(chapters).getAllByRole('listitem')).toHaveLength(3);
  await userEvent.click(
    screen.getByRole('button', { name: 'Copy the chapters' }),
  );
  expect(writeText).toHaveBeenCalledWith(
    '0:00 Ten tips\n0:40 Pricing\n1:05 Wrap up',
  );
  await userEvent.selectOptions(style, 'Clean');
  await userEvent.click(screen.getByRole('button', { name: 'Save and make' }));
  expect(
    vi.mocked(controller.changeVideoProject).mock.calls[0][1],
  ).toMatchObject({ caption_style: 'clean' });
  vi.unstubAllGlobals();
});

it('a made video is got ready for a platform on request, and its publish kit copies, shows and saves', async () => {
  const writeText = vi.fn(async () => undefined);
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
  const save = vi.fn(async () => ({ status: 'cancelled' as const }));
  const { controller } = setup(view({ number: 9, latest: 9 }), {
    save,
  } as unknown as ClientPlatform);
  const kit = await screen.findByRole('region', { name: 'Ready to publish' });
  expect(within(kit).queryByRole('list', { name: 'Thumbnails' })).toBeNull();
  await userEvent.selectOptions(
    within(kit).getByLabelText("Where it's going"),
    'TikTok',
  );
  await userEvent.click(
    within(kit).getByRole('button', { name: 'Get it ready for TikTok' }),
  );
  await waitFor(() => expect(controller.intent).toHaveBeenCalled());
  const body = vi.mocked(controller.intent).mock.calls[0][2] as {
    text: string;
    video_panel?: unknown;
  };
  expect(body.text).toContain(
    'Get version 9 of the video "Launch demo" (vid-1a2b3c4d) ready to publish on TikTok',
  );
  expect(body.video_panel).toBeUndefined();
  vi.unstubAllGlobals();
});

it('a publish kit shows its thumbnails, copies its title and description with chapters, and saves its files', async () => {
  const writeText = vi.fn(async () => undefined);
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: () => 'blob:thumbnail',
    revokeObjectURL: () => undefined,
  });
  const save = vi.fn(async () => ({ status: 'cancelled' as const }));
  setup(
    view({
      number: 10,
      latest: 10,
      versions: [{ number: 10, at: AT, by: 'row-bot', note: 'Made' }],
      publish: {
        platforms: ['youtube', 'reels'],
        title: 'I built a desk',
        description: 'One sheet, no table saw.',
        chapters: [
          { at: 0, title: 'Plywood desk' },
          { at: 12, title: 'Cutting' },
          { at: 30, title: 'Finish' },
        ],
        thumbnails: [
          {
            shape: '16:9',
            name: 'Desk thumbnail 16x9.jpg',
            ref: 'conversation-a:wide',
          },
          {
            shape: '9:16',
            name: 'Desk thumbnail 9x16.jpg',
            ref: 'conversation-a:tall',
          },
        ],
        subtitles: [
          { format: 'srt', name: 'Desk.srt', ref: 'conversation-a:srt' },
          { format: 'vtt', name: 'Desk.vtt', ref: 'conversation-a:vtt' },
        ],
      },
    }),
    { save } as unknown as ClientPlatform,
  );
  const kit = await screen.findByRole('region', { name: 'Ready to publish' });
  expect(
    within(kit).getByRole('heading', {
      name: 'Ready to publish · YouTube, Instagram Reels',
    }),
  ).toBeVisible();
  expect(within(kit).getByLabelText("Where it's going")).toHaveValue('youtube');
  const thumbnails = within(kit).getByRole('list', { name: 'Thumbnails' });
  expect(
    await within(thumbnails).findByRole('img', { name: 'Thumbnail, 16:9' }),
  ).toHaveAttribute('src', 'blob:thumbnail');
  await userEvent.click(
    within(kit).getByRole('button', { name: 'Copy the title' }),
  );
  expect(writeText).toHaveBeenLastCalledWith('I built a desk');
  await userEvent.click(
    within(kit).getByRole('button', { name: 'Copy the description' }),
  );
  expect(writeText).toHaveBeenLastCalledWith(
    'One sheet, no table saw.\n\n0:00 Plywood desk\n0:12 Cutting\n0:30 Finish',
  );
  expect(await screen.findByText('Description copied.')).toBeVisible();
  await userEvent.click(
    within(kit).getByRole('button', { name: 'Download the 9:16 thumbnail' }),
  );
  expect(save).toHaveBeenLastCalledWith(
    'conversation-a:tall',
    'Desk thumbnail 9x16.jpg',
  );
  await userEvent.click(
    within(kit).getByRole('button', { name: 'Download subtitles (SRT)' }),
  );
  expect(save).toHaveBeenLastCalledWith('conversation-a:srt', 'Desk.srt');
  vi.unstubAllGlobals();
});

it('a new character given a line joins the voices, picked for them until chosen', async () => {
  setup(view());
  expect(
    await screen.findByLabelText('Scene 1: who says its line'),
  ).toBeVisible();
  expect(
    screen.queryByRole('group', { name: 'Voices' }),
  ).not.toBeInTheDocument();
  await userEvent.type(
    screen.getByLabelText('Scene 1: who says its line'),
    'Sam',
  );
  const voices = screen.getByRole('group', { name: 'Voices' });
  expect(within(voices).getByLabelText("Sam's voice")).toHaveValue('');
  expect(within(voices).getByLabelText("Sam's voice")).toHaveTextContent(
    'Picked for them',
  );
});

it('the timeline shows who says each line, each scene’s effects and the music’s sections', async () => {
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        matches: query === '(min-width: 768px)',
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
  setup(dialogue());
  await userEvent.click(await screen.findByRole('radio', { name: 'Timeline' }));
  const timeline = screen.getByRole('application', { name: /^Timeline/ });
  expect(within(timeline).getByLabelText('Voice')).toHaveTextContent(
    'Maya: Then pick a plan.',
  );
  expect(within(timeline).getByLabelText('Effects')).toHaveTextContent(
    'RainWhoosh',
  );
  const music = within(timeline).getByLabelText('Music');
  expect(music).toHaveTextContent('Made to fit · warm');
  expect(music).toHaveTextContent('Scene 2');
});

it('a storyboard says what music it has, where it changes and that it cuts on the beat', async () => {
  setup({ ...dialogue(), status: 'draft', edit: null, checks: [] });
  expect(
    await screen.findByRole('region', { name: 'Storyboard' }),
  ).toHaveTextContent(
    'Made to fit · warm · changes at scene 2 · cut on the beat',
  );
});

it('an edit to a storyboard is saved without making it', async () => {
  const { controller } = setup(storyboard({ number: 7, latest: 7 }));
  await userEvent.type(await screen.findByLabelText('Scene 1 card'), '!');
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  expect(controller.changeVideoProject).toHaveBeenCalledWith(
    'vid-1a2b3c4d',
    expect.objectContaining({ base: 7 }),
  );
});

it('after a make that reused unchanged scenes, the panel says which were drawn again', async () => {
  const made = view({ number: 8, latest: 8 });
  made.scenes = made.scenes.map((scene, index) => ({
    ...scene,
    made: index ? 'new' : 'reused',
  }));
  setup(made);
  expect(
    await screen.findByText('Made again: 1 changed scene drawn, 1 reused.'),
  ).toBeVisible();
  const cards = within(
    screen.getByRole('list', { name: 'Storyboard' }),
  ).getAllByRole('listitem');
  expect(cards[0]).toHaveTextContent('Reused');
  expect(cards[1]).toHaveTextContent('Made again');
  expect(
    screen.queryByRole('button', { name: 'Approve scene 1' }),
  ).not.toBeInTheDocument();
});
