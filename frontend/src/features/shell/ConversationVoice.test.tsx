import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ConversationVoice from './ConversationVoice';
import type { ClientController } from '../../api/controller';
import type { TalkControlsProps } from './TalkControls';
import type { RealtimeTalkControlsProps } from './RealtimeTalkControls';

let talk: TalkControlsProps;
let realtime: RealtimeTalkControlsProps;
vi.mock('./TalkControls', () => ({
  default: (props: TalkControlsProps) => {
    talk = props;
    return <span>Local voice controls</span>;
  },
}));
vi.mock('./RealtimeTalkControls', () => ({
  default: (props: RealtimeTalkControlsProps) => {
    realtime = props;
    return <span>Realtime voice controls</span>;
  },
}));
const scope = {
  conversationId: 'conversation-a',
  clientSessionId: 'session',
  serverEpoch: 'epoch',
  selectionKey: '1',
};
const handle = {
  conversation_id: scope.conversationId,
  server_epoch: scope.serverEpoch,
  voice_session_id: 1,
  lease_id: '11111111-1111-4111-8111-111111111111',
};
function setup() {
  const controller = {
    startTalk: vi.fn().mockResolvedValue({ handle, run_id: null }),
    startRealtime: vi
      .fn()
      .mockResolvedValue({ snapshot: { handle, run_id: null } }),
    voiceRun: vi.fn(),
    voiceControl: vi.fn(),
    transcribeTalk: vi.fn(),
    talkOutput: vi.fn(),
    realtimeExchange: vi.fn(),
    realtimeEvent: vi.fn(),
  };
  const props = {
    controller: controller as unknown as ClientController,
    scope,
    available: true,
    disabled: false,
    running: false,
    context: {
      conversation_revision: '2',
      model_selection: {
        provider_id: 'fixture',
        model_ref: 'model:fixture:chat',
      },
      write_targets: [
        {
          kind: 'artifact' as const,
          binding_id: 'binding',
          resource_id: 'artifact',
          binding_revision: '3',
          resource_revision: '4',
        },
      ],
    },
    targets: ['Project sketch'],
    onBusy: vi.fn(),
  };
  const view = render(<ConversationVoice {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  return { controller, props, view };
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('reviews the selected targets and passes their exact captured revisions to normal voice admission', async () => {
  const { controller, props, view } = setup();
  expect(
    screen.getByText(
      /Starting Talk allows spoken requests to change Project sketch/,
    ),
  ).toBeVisible();
  const signal = new AbortController().signal;
  await act(async () => {
    talk.onBusy?.(true);
    await talk.start('request', signal);
  });
  expect(controller.startTalk).toHaveBeenCalledWith(
    scope,
    { ...props.context, request_id: 'request' },
    signal,
  );
  view.rerender(<ConversationVoice {...props} targets={['Replacement']} />);
  expect(screen.getByText(/Project sketch/)).toBeVisible();
  expect(
    screen.queryByRole('combobox', { name: 'Talk mode' }),
  ).not.toBeInTheDocument();
});

it('does not admit local Talk during a current typed response and exposes explicit realtime mode', () => {
  const { props, view } = setup();
  view.rerender(<ConversationVoice {...props} running />);
  expect(talk.disabled).toBe(true);
  fireEvent.change(screen.getByRole('combobox', { name: 'Talk mode' }), {
    target: { value: 'realtime' },
  });
  expect(realtime.disabled).toBe(false);
});

it('polls one exact active run at a time and stops reading after its final saved output', async () => {
  vi.useFakeTimers();
  const { controller } = setup();
  let resolve!: (value: unknown) => void;
  controller.voiceRun.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  await act(async () => {
    await talk.start('request', new AbortController().signal);
  });
  await act(async () => {
    talk.onSubmitted?.({ snapshot: { handle }, run_id: 'run' } as Parameters<
      NonNullable<TalkControlsProps['onSubmitted']>
    >[0]);
  });
  expect(controller.voiceRun).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(controller.voiceRun).toHaveBeenCalledTimes(1);
  await act(async () => {
    resolve({
      handle,
      run_id: 'run',
      state: 'completed',
      output_id: 'output',
      text: 'Saved result.',
    });
  });
  expect(talk.run).toEqual({
    id: 'run',
    state: 'completed',
    outputId: 'output',
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(controller.voiceRun).toHaveBeenCalledTimes(1);
});

it('ignores a late result after leaving the conversation and aborts the read', async () => {
  const { controller, view, props } = setup();
  let resolve!: (value: unknown) => void;
  controller.voiceRun.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  await act(async () => {
    await talk.start('request', new AbortController().signal);
  });
  await act(async () => {
    talk.onSubmitted?.({ snapshot: { handle }, run_id: 'run' } as Parameters<
      NonNullable<TalkControlsProps['onSubmitted']>
    >[0]);
  });
  const signal = controller.voiceRun.mock.calls[0][3] as AbortSignal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => {
    resolve({
      handle,
      run_id: 'run',
      state: 'completed',
      output_id: 'output',
      text: 'Late result.',
    });
  });
  expect(props.onBusy).toHaveBeenLastCalledWith(false);
});

it('sends realtime SDP only through the selected authenticated conversation controller', async () => {
  const { controller } = setup();
  fireEvent.change(screen.getByRole('combobox', { name: 'Talk mode' }), {
    target: { value: 'realtime' },
  });
  const signal = new AbortController().signal;
  await realtime.exchange!(handle, 'v=0 synthetic', signal);
  expect(controller.realtimeExchange).toHaveBeenCalledWith(
    scope,
    handle,
    'v=0 synthetic',
    signal,
  );
});
