import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { LiveUpdates } from '../../api/live-updates';
import type { LiveFrame } from '../../api/types';
import { provideLive } from '../../live';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import LearningHome from './LearningHome';
import { useLearning } from './learning-state';
import {
  downloadState,
  installedModel,
  learningSnapshot,
  NO_GPU,
  trainingRun,
  versionState,
} from './test-data';

/** The Learning tab's Personal model view, on the window's shared Learning state. */
function PersonalModelScreen() {
  const io = useLearning();
  return io?.snapshot ? (
    <LearningHome
      io={io}
      view="model"
      onViewChange={() => undefined}
      onOpenSettings={() => undefined}
    />
  ) : null;
}

const ready = (version: number) =>
  learningSnapshot({
    state: 'on',
    personal_model: installedModel({
      version: versionState({
        version,
        file_name: `row-bot-personal-v${version}-Q8_0.gguf`,
      }),
    }),
  });

it('a version made ready in the background shows at once, without a reload (O127)', async () => {
  const frames: ((frame: LiveFrame) => void)[] = [];
  const live = new LiveUpdates(
    {
      async *observeLive(signal: AbortSignal) {
        while (!signal.aborted)
          yield await new Promise<LiveFrame>((resolve) => frames.push(resolve));
      },
    },
    () => Promise.resolve(),
  );
  const send = (revision: number, learning: number) =>
    frames.shift()?.({
      epoch: 'server-a',
      revision,
      topics: {
        conversations: 0,
        runs: 0,
        approvals: 0,
        helpers: 0,
        processes: 0,
        monitor: 0,
        models: 0,
        learning,
      },
    });
  let onServer = ready(1);
  const learning = vi.fn(async () => onServer);
  const controller = {
    subscribe: () => () => {},
    getSnapshot: () => ({
      status: 'ready',
      handshake: {
        instance_id: 'instance',
        client_session_id: 'session',
        application_capabilities: [],
      },
    }),
    learning,
    learningTraining: vi.fn(async () => trainingRun()),
    learningDownload: vi.fn(async () => downloadState()),
    learningHardware: vi.fn(async () => NO_GPU),
  } as unknown as ClientController;
  provideLive(live);
  live.start();
  try {
    render(
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <MemoryRouter>
          <OverlayProvider>
            <PersonalModelScreen />
          </OverlayProvider>
        </MemoryRouter>
      </RuntimeContext.Provider>,
    );
    expect(await screen.findByText('v1 is ready to compare')).toBeVisible();
    // The window's first frame is where things stand.
    await waitFor(() => expect(frames).toHaveLength(1));
    act(() => void send(1, 0));
    await waitFor(() => expect(live.connected).toBe(true));

    // Overnight: v3 is made ready while the screen stays open.
    onServer = ready(3);
    await waitFor(() => expect(frames).toHaveLength(1));
    act(() => void send(2, 1));

    expect(await screen.findByText('v3 is ready to compare')).toBeVisible();
    expect(screen.queryByText('v1 is ready to compare')).toBeNull();
    expect(learning).toHaveBeenCalledTimes(2);
  } finally {
    live.stop();
  }
});
