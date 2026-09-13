import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  openRealtimeSession,
  type RealtimeSnapshot,
  type RealtimeStart,
  type RealtimeSession,
} from '../../api/voice_realtime';
import RealtimeTalkControls, {
  type RealtimeTalkControlsProps,
} from './RealtimeTalkControls';

vi.mock('../../api/voice_realtime', () => ({ openRealtimeSession: vi.fn() }));
const snapshot: RealtimeSnapshot = {
  schema_version: 1,
  handle: {
    lease_id: '11111111-1111-4111-8111-111111111111',
    voice_session_id: 1,
    conversation_id: 'A',
    server_epoch: 'epoch',
  },
  state: 'listening',
  quiesced: true,
  expires_in_ms: 120_000,
  run_id: null,
  transport: 'openai_realtime',
};
const started: RealtimeStart = {
  snapshot,
  client_secret: 'synthetic',
  secret_expires_in_ms: 60_000,
};
let session: RealtimeSession;
let options: Parameters<typeof openRealtimeSession>[0];

function props(): RealtimeTalkControlsProps {
  return {
    scope: {
      conversationId: 'A',
      clientSessionId: 'session',
      serverEpoch: 'epoch',
      selectionKey: 'open1',
    },
    available: true,
    start: vi.fn().mockResolvedValue(started),
    stop: vi.fn().mockResolvedValue({ ...snapshot, state: 'stopped' }),
    heartbeat: vi.fn().mockResolvedValue(snapshot),
    event: vi.fn(),
    onBusy: vi.fn(),
    onRun: vi.fn(),
  };
}
beforeEach(() => {
  session = {
    stop: vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
    mute: vi.fn(),
    sendOutput: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal('isSecureContext', true);
  vi.stubGlobal('RTCPeerConnection', class {});
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn() },
  });
  vi.mocked(openRealtimeSession).mockImplementation(async (value) => {
    options = value;
    value.onSession(session);
    return session;
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  vi.useRealTimers();
});

async function start() {
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  await waitFor(() => expect(openRealtimeSession).toHaveBeenCalledTimes(1));
}

it('requires explicit start and keeps transcripts in captions rather than a second composer', async () => {
  const p = props();
  render(<RealtimeTalkControls {...p} />);
  expect(p.start).not.toHaveBeenCalled();
  expect(openRealtimeSession).not.toHaveBeenCalled();
  await start();
  act(() => {
    options.onCaption('Spoken text');
    options.onSnapshot({ ...snapshot, run_id: 'run-1', state: 'thinking' });
  });
  expect(screen.getByLabelText('Voice caption')).toHaveTextContent(
    'Spoken text',
  );
  expect(screen.getByText('Thinking…')).toBeVisible();
  expect(p.onRun).toHaveBeenCalledWith('run-1');
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});

it('Stops private media first and waits for server quiescence', async () => {
  const p = props();
  vi.mocked(p.stop).mockResolvedValueOnce({
    ...snapshot,
    state: 'stopping',
    quiesced: false,
  });
  render(<RealtimeTalkControls {...p} />);
  await start();
  fireEvent.click(screen.getByRole('button', { name: 'Stop Talk' }));
  expect(session.stop).toHaveBeenCalled();
  expect(options.signal.aborted).toBe(true);
  await screen.findByRole('button', { name: 'Check voice status' });
  expect(p.onBusy).not.toHaveBeenCalledWith(false);
  fireEvent.click(screen.getByRole('button', { name: 'Check voice status' }));
  await screen.findByRole('button', { name: 'Talk' });
  expect(p.onBusy).toHaveBeenCalledWith(false);
});

it('scope change stops the exact session and rejects its late captions and output', async () => {
  const p = props();
  const view = render(<RealtimeTalkControls {...p} />);
  await start();
  view.rerender(
    <RealtimeTalkControls
      {...p}
      scope={{ ...p.scope, conversationId: 'B', selectionKey: 'B' }}
    />,
  );
  act(() => {
    options.onCaption('Late text');
    options.onSnapshot({ ...snapshot, run_id: 'old' });
  });
  expect(session.stop).toHaveBeenCalled();
  expect(p.stop).toHaveBeenCalledWith(snapshot.handle);
  expect(p.onRun).not.toHaveBeenCalled();
  expect(screen.queryByText('Late text')).not.toBeInTheDocument();
});

it('stops a late issued handle after cancellation without starting WebRTC', async () => {
  let resolve!: (result: RealtimeStart) => void;
  const p = props();
  p.start = vi.fn().mockReturnValue(
    new Promise<RealtimeStart>((done) => {
      resolve = done;
    }),
  );
  render(<RealtimeTalkControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  fireEvent.click(screen.getByRole('button', { name: 'Stop Talk' }));
  await act(async () => resolve(started));
  expect(openRealtimeSession).not.toHaveBeenCalled();
  expect(p.stop).toHaveBeenCalledWith(snapshot.handle);
  expect(screen.getByRole('button', { name: 'Talk' })).toBeVisible();
});

it('terminal heartbeat with time remaining still stops microphone transport', async () => {
  vi.useFakeTimers();
  const p = props();
  vi.mocked(p.heartbeat).mockResolvedValue({
    ...snapshot,
    state: 'stopped',
    expires_in_ms: 60_000,
  });
  render(<RealtimeTalkControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  await act(async () => undefined);
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(session.stop).toHaveBeenCalled();
  expect(p.stop).toHaveBeenCalledWith(snapshot.handle);
  expect(screen.getByRole('button', { name: 'Talk' })).toBeVisible();
});

it('speech mute and captions remain presentation controls without provider calls', async () => {
  const p = props();
  render(<RealtimeTalkControls {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Speech' }));
  expect(p.start).not.toHaveBeenCalled();
  await start();
  expect(session.mute).toHaveBeenCalledWith(true);
  act(() => options.onCaption('Text caption'));
  fireEvent.click(screen.getByRole('button', { name: 'Captions' }));
  expect(screen.queryByLabelText('Voice caption')).not.toBeInTheDocument();
  expect(p.event).not.toHaveBeenCalled();
});
