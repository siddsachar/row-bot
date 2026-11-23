import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { VideoScene } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import { StoryboardCard } from './StoryboardCard';

const SHOT: VideoScene = {
  scene_id: 's0f0f0f',
  source: '',
  start: 0,
  end: 4,
  speed: 1,
  zoom: null,
  card: '',
  line: 'The fox knew first.',
  voiced: false,
  at: null,
  length: null,
  footage: 'ai',
  shows: 'a fox crossing deep snow at dawn',
  cue: 'strings swell',
  approved: false,
  has_frame: false,
  made: '',
};
const CLIP: VideoScene = {
  ...SHOT,
  scene_id: 's0e0e0e',
  source: 'town.mp4',
  end: 2,
  footage: 'clip',
  shows: 'the town waking up',
  cue: '',
  has_frame: true,
};

let seen: ((entries: { isIntersecting: boolean }[]) => void)[] = [];
beforeEach(() => {
  seen = [];
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
        seen.push(callback);
      }
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: () => 'blob:frame',
    revokeObjectURL: () => undefined,
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function card(shown: VideoScene, patch: { approvable?: boolean } = {}) {
  const onAsk = vi.fn(async () => true);
  const onApprove = vi.fn();
  const controller = {
    subscribe: () => () => {},
    getSnapshot: () => ({ status: 'ready' }),
    videoSceneFrame: vi.fn(async () => new Blob(['jpeg'])),
  } as unknown as ClientController & Record<string, ReturnType<typeof vi.fn>>;
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <OverlayProvider>
        <ol>
          <StoryboardCard
            projectId="vid-1a2b3c4d"
            title="First snow"
            version={1}
            scene={{ ...shown }}
            shown={shown}
            number={2}
            total={3}
            editable
            approvable={patch.approvable ?? true}
            planning={patch.approvable ?? true}
            changed={false}
            partial={false}
            playable={false}
            busy={false}
            onChange={vi.fn()}
            onMove={vi.fn()}
            onRemove={vi.fn()}
            onApprove={onApprove}
            onAsk={onAsk}
            onSeek={vi.fn()}
          />
        </ol>
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
  return { onAsk, onApprove, controller };
}

it('a planned AI shot says what it shows, its cue and that Row-Bot makes it, with no frame to load', () => {
  const { controller } = card(SHOT);
  const item = screen.getByRole('listitem');
  expect(
    within(item).getByRole('img', { name: 'Scene 2: AI shot' }),
  ).toHaveTextContent('AI shot');
  expect(item).toHaveTextContent('a fox crossing deep snow at dawn');
  expect(item).toHaveTextContent('strings swell');
  expect(item).toHaveTextContent('AI shot to make: Row-Bot makes it');
  expect(within(item).getByLabelText('Scene 2 lasts, in seconds')).toHaveValue(
    4,
  );
  expect(controller.videoSceneFrame).not.toHaveBeenCalled();
  // Its card text is words over the shot, not a title card.
  expect(within(item).getByLabelText('Scene 2 card')).toHaveAttribute(
    'placeholder',
    'Text on screen (optional)',
  );
});

it('a frame loads only once its card comes near the screen', async () => {
  const { controller } = card(CLIP);
  expect(controller.videoSceneFrame).not.toHaveBeenCalled();
  seen.forEach((callback) => callback([{ isIntersecting: true }]));
  await waitFor(() =>
    expect(controller.videoSceneFrame).toHaveBeenCalledWith(
      'vid-1a2b3c4d',
      's0e0e0e',
      1,
      expect.anything(),
    ),
  );
  await waitFor(() =>
    expect(
      screen.getByRole('img', { name: 'Scene 2: Clip' }).querySelector('img'),
    ).toHaveAttribute('src', 'blob:frame'),
  );
});

it('a comment reaches Row-Bot as the person’s words about this scene, and the box closes once sent', async () => {
  const { onAsk } = card(SHOT);
  await userEvent.click(
    screen.getByRole('button', { name: 'Comment on scene 2' }),
  );
  const box = screen.getByLabelText('What to change in scene 2');
  expect(box).toHaveFocus();
  await userEvent.type(box, 'make this one moodier{Enter}');
  expect(onAsk).toHaveBeenCalledWith(
    'Scene 2 [s0f0f0f] of the video "First snow" (vid-1a2b3c4d): make this one moodier',
  );
  await waitFor(() =>
    expect(
      screen.queryByLabelText('What to change in scene 2'),
    ).not.toBeInTheDocument(),
  );
});

it('approves the scene, swaps its footage and asks for a new take, each in one tap', async () => {
  const { onAsk, onApprove } = card(SHOT);
  await userEvent.click(
    screen.getByRole('button', { name: 'Approve scene 2' }),
  );
  expect(onApprove).toHaveBeenCalledWith(true);
  await userEvent.click(
    screen.getByRole('button', { name: "Swap scene 2's footage" }),
  );
  await userEvent.click(
    await screen.findByRole('menuitem', { name: 'Screen recording' }),
  );
  expect(onAsk).toHaveBeenLastCalledWith(
    expect.stringMatching(/: swap its footage for a screen recording\.$/),
  );
  await userEvent.click(
    screen.getByRole('button', { name: 'New take of scene 2' }),
  );
  expect(onAsk).toHaveBeenLastCalledWith(
    expect.stringMatching(/: make a new take of its AI shot\.$/),
  );
});

it('a clip has no new take, and a made video has nothing to approve', () => {
  card(CLIP, { approvable: false });
  expect(
    screen.queryByRole('button', { name: 'New take of scene 2' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Approve scene 2' }),
  ).not.toBeInTheDocument();
  expect(screen.getByLabelText('Scene 2 speed')).toHaveValue('1');
});
