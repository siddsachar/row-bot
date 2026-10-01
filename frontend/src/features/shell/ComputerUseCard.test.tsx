import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  ComputerUsePreview,
  ComputerUseReceipt,
  ComputerUseSnapshot,
} from '../../api/types';
import {
  COMPUTER_POLL_MS,
  ComputerUseCard,
  useComputerUse,
} from './ComputerUseCard';

const A = 'a'.repeat(64);
const B = 'b'.repeat(64);

function snapshot(
  patch: Partial<ComputerUseSnapshot> = {},
): ComputerUseSnapshot {
  return {
    schema_version: 1,
    conversation_id: 'conversation-a',
    revision: A,
    active: true,
    state: 'working',
    app: 'Calculator',
    has_picture: true,
    approval_id: null,
    can_pause: true,
    can_resume: false,
    can_stop: true,
    ...patch,
  };
}

function preview(revision = A): ComputerUsePreview {
  return {
    schema_version: 1,
    conversation_id: 'conversation-a',
    revision,
    state: 'available',
    mime_type: 'image/png',
    image_base64: `cGljdHVyZS0${revision[0]}`,
  };
}

function receipt(
  action: ComputerUseReceipt['action'],
  patch: Partial<ComputerUseReceipt> = {},
): ComputerUseReceipt {
  return {
    schema_version: 1,
    command_id: crypto.randomUUID(),
    action,
    conversation_id: 'conversation-a',
    status: 'completed',
    code: null,
    computer_use: snapshot({ state: 'paused', can_pause: false }),
    ...patch,
  };
}

function card(
  value: ComputerUseSnapshot | null,
  overrides: Partial<Parameters<typeof ComputerUseCard>[0]> = {},
) {
  const props = {
    conversationId: 'conversation-a',
    snapshot: value,
    stopped: false,
    loadPreview: vi.fn(async (_c: string, revision: string) =>
      preview(revision),
    ),
    send: vi.fn(async (_c: string, action: ComputerUseReceipt['action']) =>
      receipt(action),
    ),
    onChange: vi.fn(),
    onStopped: vi.fn(),
    ...overrides,
  };
  return { props, view: render(<ComputerUseCard {...props} />) };
}

afterEach(() => {
  vi.useRealTimers();
});

it('shows the latest picture while working and loads it only when it changes', async () => {
  const { props, view } = card(snapshot());
  const picture = await screen.findByRole('img', {
    name: 'Latest picture of Calculator',
  });
  expect(picture).toHaveAttribute('src', 'data:image/png;base64,cGljdHVyZS0a');
  const region = screen.getByRole('region', { name: 'Computer use' });
  expect(region).toHaveTextContent('Using your computer · Calculator');
  expect(region).toHaveTextContent('Working');
  expect(
    screen.getByRole('button', { name: 'Pause — you take over' }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Stop' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull();

  // The same revision never fetches the picture again.
  view.rerender(<ComputerUseCard {...props} snapshot={snapshot()} />);
  expect(props.loadPreview).toHaveBeenCalledTimes(1);
  view.rerender(
    <ComputerUseCard {...props} snapshot={snapshot({ revision: B })} />,
  );
  await screen.findByRole('img', { name: 'Latest picture of Calculator' });
  expect(props.loadPreview).toHaveBeenCalledTimes(2);
  expect(props.loadPreview).toHaveBeenLastCalledWith(
    'conversation-a',
    B,
    expect.any(AbortSignal),
  );
  expect(screen.getByRole('img')).toHaveAttribute(
    'src',
    'data:image/png;base64,cGljdHVyZS0b',
  );
});

it('hides the picture while you have control and offers Resume and Stop', async () => {
  const { props, view } = card(snapshot());
  await screen.findByRole('img');
  view.rerender(
    <ComputerUseCard
      {...props}
      snapshot={snapshot({
        revision: B,
        state: 'paused',
        has_picture: false,
        approval_id: 'approval-a',
        can_pause: false,
        can_resume: true,
      })}
    />,
  );
  expect(screen.queryByRole('img')).toBeNull();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Paused. You have control; the picture is hidden until you resume.',
  );
  expect(screen.getByRole('region')).toHaveTextContent('Paused');
  expect(screen.getByRole('button', { name: 'Resume' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Stop' })).toBeVisible();
  expect(screen.queryByRole('button', { name: /Pause/ })).toBeNull();
  expect(props.loadPreview).toHaveBeenCalledTimes(1);
});

it('says why the picture is hidden while an approval waits', () => {
  const { props } = card(
    snapshot({
      state: 'waiting_approval',
      has_picture: false,
      can_pause: false,
    }),
  );
  expect(screen.queryByRole('img')).toBeNull();
  expect(screen.getByRole('status')).toHaveTextContent(
    'The picture is hidden while Row-Bot waits for your approval.',
  );
  expect(props.loadPreview).not.toHaveBeenCalled();
});

it('sends the exact action and takes the state the server reports', async () => {
  const { props } = card(snapshot());
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Pause — you take over' }),
    ),
  );
  expect(props.send).toHaveBeenCalledWith(
    'conversation-a',
    'computer_use.pause',
  );
  expect(props.onChange).toHaveBeenCalledWith(
    expect.objectContaining({ state: 'paused' }),
  );
  expect(props.onStopped).not.toHaveBeenCalled();

  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Stop' })),
  );
  expect(props.send).toHaveBeenLastCalledWith(
    'conversation-a',
    'computer_use.stop',
  );
  expect(props.onStopped).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('says plainly when an action could not apply or did not get through', async () => {
  const send = vi
    .fn()
    .mockResolvedValueOnce(
      receipt('computer_use.resume', {
        status: 'rejected',
        code: 'computer_use_resume_failed',
        computer_use: snapshot({ state: 'stopped', active: false }),
      }),
    )
    .mockRejectedValueOnce(new TypeError('Failed to fetch'));
  const { props } = card(
    snapshot({
      state: 'paused',
      has_picture: false,
      can_pause: false,
      can_resume: true,
    }),
    { send },
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Resume' })),
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    "Row-Bot couldn't pick up where it left off.",
  );
  expect(props.onChange).toHaveBeenCalledWith(
    expect.objectContaining({ state: 'stopped' }),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Stop' })),
  );
  expect(screen.getByRole('alert')).not.toHaveTextContent("couldn't pick up");
  expect(props.onStopped).not.toHaveBeenCalled();
});

it('keeps focus in the card when Pause gives way to Resume, and Stop stays ready while Resume runs', async () => {
  let finishResume: (value: ComputerUseReceipt) => void = () => undefined;
  const send = vi.fn(
    (_c: string, action: ComputerUseReceipt['action']) =>
      new Promise<ComputerUseReceipt>((resolve) => {
        if (action === 'computer_use.resume') finishResume = resolve;
        else
          resolve(
            receipt(action, {
              computer_use: snapshot({
                revision: B,
                state: 'paused',
                has_picture: false,
                can_pause: false,
                can_resume: true,
              }),
            }),
          );
      }),
  );
  function Stateful() {
    const [value, setValue] = useState(snapshot());
    return (
      <ComputerUseCard
        conversationId="conversation-a"
        snapshot={value}
        stopped={false}
        loadPreview={async (_c, revision) => preview(revision)}
        send={send}
        onChange={setValue}
        onStopped={vi.fn()}
      />
    );
  }
  render(<Stateful />);
  const pause = screen.getByRole('button', { name: 'Pause — you take over' });
  pause.focus();
  await act(async () => fireEvent.click(pause));
  const resume = screen.getByRole('button', { name: 'Resume' });
  expect(resume).toHaveFocus();

  await act(async () => fireEvent.click(resume));
  expect(screen.getByRole('button', { name: 'Resuming…' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled();
  await act(async () =>
    finishResume(
      receipt('computer_use.resume', {
        computer_use: snapshot({ revision: B }),
      }),
    ),
  );
  expect(
    screen.getByRole('button', { name: 'Pause — you take over' }),
  ).toBeEnabled();
});

it('after Stop says the computer is free and offers nothing else', () => {
  card(snapshot({ active: false, state: 'stopped', can_stop: false }), {
    stopped: true,
  });
  const region = screen.getByRole('region', { name: 'Computer use' });
  expect(region).toHaveAttribute('data-state', 'stopped');
  expect(screen.getByRole('status')).toHaveTextContent(
    'Stopped. Row-Bot no longer controls your computer.',
  );
  expect(screen.queryByRole('button')).toBeNull();
  expect(screen.queryByRole('img')).toBeNull();
});

type HookProps = Parameters<typeof useComputerUse>[0];
let followed: ReturnType<typeof useComputerUse>;
function Follow(props: HookProps) {
  followed = useComputerUse(props);
  return null;
}

function follow(overrides: Partial<HookProps> = {}) {
  const props: HookProps = {
    conversationId: 'conversation-a',
    watch: false,
    generationId: 'run-a',
    turnKey: 'run-a:running',
    load: vi.fn(async () => snapshot()),
    ...overrides,
  };
  return { props, view: render(<Follow {...props} />) };
}

async function wait(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

it('reads once on open, then follows about once a second until it unmounts', async () => {
  vi.useFakeTimers();
  const { props, view } = follow();
  await wait(0);
  expect(props.load).toHaveBeenCalledTimes(1);
  expect(followed.visible).toBe(true);
  expect(followed.checked).toBe(true);
  await wait(COMPUTER_POLL_MS);
  expect(props.load).toHaveBeenCalledTimes(2);
  await wait(COMPUTER_POLL_MS);
  expect(props.load).toHaveBeenCalledTimes(3);
  view.unmount();
  await wait(COMPUTER_POLL_MS * 5);
  expect(props.load).toHaveBeenCalledTimes(3);
});

it('pauses following while the page is hidden', async () => {
  vi.useFakeTimers();
  let hidden = false;
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() =>
    hidden ? 'hidden' : 'visible',
  );
  const { props } = follow();
  await wait(0);
  hidden = true;
  act(() => void document.dispatchEvent(new Event('visibilitychange')));
  await wait(COMPUTER_POLL_MS * 5);
  expect(props.load).toHaveBeenCalledTimes(1);
  hidden = false;
  act(() => void document.dispatchEvent(new Event('visibilitychange')));
  await wait(COMPUTER_POLL_MS);
  expect(props.load).toHaveBeenCalledTimes(2);
});

it('stops following when nothing is left to follow, until the turn asks again', async () => {
  vi.useFakeTimers();
  const idle = snapshot({
    active: false,
    state: 'stopped',
    app: '',
    has_picture: false,
    can_pause: false,
    can_stop: false,
  });
  const load = vi.fn(async () => idle);
  const { props, view } = follow({ load });
  await wait(COMPUTER_POLL_MS * 3);
  expect(load).toHaveBeenCalledTimes(1);
  expect(followed.visible).toBe(false);
  // The turn starts using the computer: follow it.
  view.rerender(<Follow {...props} watch />);
  await wait(COMPUTER_POLL_MS * 2);
  expect(load).toHaveBeenCalledTimes(3);
});

it('never reads for another device or a past page', async () => {
  vi.useFakeTimers();
  const { props } = follow({ conversationId: '', watch: true });
  await wait(COMPUTER_POLL_MS * 3);
  expect(props.load).not.toHaveBeenCalled();
  expect(followed.checked).toBe(true);
  expect(followed.visible).toBe(false);
});

it('keeps the Stopped note until the next turn starts', async () => {
  vi.useFakeTimers();
  const stopped = snapshot({
    active: false,
    state: 'stopped',
    can_stop: false,
  });
  const { props, view } = follow({ load: vi.fn(async () => stopped) });
  await wait(0);
  act(() => followed.markStopped());
  expect(followed.stopped).toBe(true);
  expect(followed.visible).toBe(true);
  view.rerender(<Follow {...props} turnKey="run-a:stopped" />);
  await wait(0);
  expect(followed.stopped).toBe(true);
  view.rerender(
    <Follow {...props} generationId="run-b" turnKey="run-b:running" />,
  );
  await wait(0);
  expect(followed.stopped).toBe(false);
  expect(followed.visible).toBe(false);
});
