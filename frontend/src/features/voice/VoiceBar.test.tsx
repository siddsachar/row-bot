import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import VoiceBar, { latencyLine, type VoiceBarSession } from './VoiceBar';
import type { VoiceSnapshot } from './voice-session';

afterEach(cleanup);

function fakeSession(initial: Partial<VoiceSnapshot> = {}) {
  let snapshot: VoiceSnapshot = {
    orb: 'listening',
    muted: false,
    held: false,
    pushToTalk: false,
    captionsDefault: true,
    you: null,
    rowBot: null,
    inputLevel: 0,
    latency: null,
    approval: null,
    error: '',
    ...initial,
  };
  const listeners = new Set<() => void>();
  const session = {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSnapshot: () => snapshot,
    setMuted: vi.fn(),
    hold: vi.fn(),
    stopSpeaking: vi.fn(),
    resume: vi.fn(async () => undefined),
    end: vi.fn(async () => undefined),
  };
  const update = (patch: Partial<VoiceSnapshot>) =>
    act(() => {
      snapshot = { ...snapshot, ...patch };
      listeners.forEach((listener) => listener());
    });
  return {
    session: session as unknown as VoiceBarSession & typeof session,
    update,
  };
}

function bar(
  session: VoiceBarSession,
  extra: Partial<Parameters<typeof VoiceBar>[0]> = {},
) {
  const props = {
    session,
    typing: false,
    onTypeInstead: vi.fn(),
    onEnded: vi.fn(),
    ...extra,
  };
  render(<VoiceBar {...props} />);
  return props;
}

it('names every orb state in plain words and the orb acts only where it means something', () => {
  const { session, update } = fakeSession();
  bar(session);
  const region = screen.getByRole('region', { name: 'Voice conversation' });
  for (const [orb, words] of [
    ['connecting', 'Connecting…'],
    ['listening', 'Listening'],
    ['thinking', 'Thinking'],
    ['waiting', 'Waiting for you'],
  ] as const) {
    update({ orb });
    expect(region).toHaveAttribute('data-state', orb);
    expect(screen.getByText(words)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: words })).toBeDisabled();
  }
  update({ orb: 'speaking' });
  fireEvent.click(screen.getByRole('button', { name: 'Stop speaking' }));
  expect(session.stopSpeaking).toHaveBeenCalled();
  update({ orb: 'paused' });
  expect(screen.getByText('Paused · tap to resume')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Resume voice' }));
  expect(session.resume).toHaveBeenCalled();
  update({ orb: 'muted', muted: true });
  fireEvent.click(screen.getAllByRole('button', { name: 'Unmute' })[0]);
  expect(session.setMuted).toHaveBeenCalledWith(false);
});

it('captions both sides, starting from the saved captions setting', () => {
  const { session, update } = fakeSession({ captionsDefault: false });
  bar(session);
  update({
    you: { text: 'What is on today?', final: true },
    rowBot: { text: 'Two meetings.', final: true },
  });
  expect(screen.queryByText('What is on today?')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show captions' }));
  expect(screen.getByText('What is on today?')).toBeInTheDocument();
  expect(screen.getByText('Two meetings.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Hide captions' }));
  expect(screen.queryByText('Two meetings.')).not.toBeInTheDocument();
});

it('shows an ellipsis while the person’s words are being written down', () => {
  const { session, update } = fakeSession();
  bar(session);
  update({ you: { text: '', final: false } });
  expect(screen.getByText('…')).toBeInTheDocument();
});

it('mutes, types instead and ends from icon buttons', () => {
  const { session } = fakeSession();
  const props = bar(session);
  fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
  expect(session.setMuted).toHaveBeenCalledWith(true);
  fireEvent.click(screen.getByRole('button', { name: 'Type instead' }));
  expect(props.onTypeInstead).toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'End voice' }));
  expect(session.end).toHaveBeenCalled();
  expect(props.onEnded).toHaveBeenCalled();
});

it('push to talk holds while pressed', () => {
  const { session } = fakeSession({ pushToTalk: true });
  bar(session);
  const hold = screen.getByRole('button', { name: 'Hold to talk' });
  fireEvent.pointerDown(hold, { pointerId: 1 });
  expect(session.hold).toHaveBeenLastCalledWith(true);
  fireEvent.pointerUp(hold, { pointerId: 1 });
  expect(session.hold).toHaveBeenLastCalledWith(false);
});

it('the phone bar expands into a full-screen focus and back', () => {
  const { session } = fakeSession();
  bar(session, { phone: true });
  fireEvent.click(screen.getByRole('button', { name: 'Full screen' }));
  expect(
    screen.getByRole('region', { name: 'Voice conversation' }),
  ).toHaveAttribute('data-focus', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'Leave full screen' }));
  expect(
    screen.getByRole('region', { name: 'Voice conversation' }),
  ).not.toHaveAttribute('data-focus');
});

it('timing stays behind its button, which waits for the first spoken reply', () => {
  const { session, update } = fakeSession();
  bar(session);
  expect(screen.getByRole('button', { name: 'No timing yet' })).toBeDisabled();
  expect(
    screen.queryByText(/Replied in|No timing yet/, { selector: 'p' }),
  ).toBeNull();
  update({
    latency: {
      last: { voice_to_voice: 640 },
      summary: { turns: 1, stages: { voice_to_voice: { p50: 640, p90: 640 } } },
    },
  });
  expect(screen.queryByText('Replied in 0.6 s')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show timing' }));
  expect(screen.getByText('Replied in 0.6 s')).toBeVisible();
});

it('timing reads as one plain line', () => {
  expect(latencyLine(null)).toBe('No timing yet');
  expect(
    latencyLine({
      last: { voice_to_voice: 812, voice_to_ear: 870 },
      summary: { turns: 4, stages: { voice_to_ear: { p50: 910, p90: 1200 } } },
    }),
  ).toBe('Replied in 0.9 s · usually 0.9 s');
  expect(
    latencyLine({
      last: { voice_to_voice: 640 },
      summary: { turns: 1, stages: { voice_to_voice: { p50: 640, p90: 640 } } },
    }),
  ).toBe('Replied in 0.6 s');
});

it('a progress line reads as Row-Bot working, not as its answer', () => {
  const { session, update } = fakeSession();
  bar(session);
  update({
    rowBot: { text: 'Checking your calendar…', final: true, kind: 'progress' },
  });
  expect(
    screen.getByText('Checking your calendar…').closest('.voice-caption'),
  ).toHaveAttribute('data-kind', 'progress');
});

it('a waiting approval says how it may be answered and points at its card', () => {
  const { session, update } = fakeSession({ orb: 'waiting' });
  const onShowApproval = vi.fn();
  bar(session, { onShowApproval });
  update({
    approval: { id: 'approval-1', action: 'Move / rename file?', voice: true },
  });
  expect(screen.getByText('Move / rename file?')).toBeInTheDocument();
  expect(screen.getByText('Say yes, or tap Approve')).toBeInTheDocument();
  // The bar never answers it: only the card's own buttons do.
  expect(screen.queryByRole('button', { name: /approve/i })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show the approval' }));
  expect(onShowApproval).toHaveBeenCalled();
  update({
    approval: { id: 'approval-2', action: 'Send email?', voice: false },
  });
  expect(screen.getByText('Needs a tap on Approve')).toBeInTheDocument();
  expect(screen.queryByText('Say yes, or tap Approve')).toBeNull();
  update({ approval: null });
  expect(screen.queryByText('Send email?')).toBeNull();
});

it('the phone focus shows the newest result, opened with one tap, and leaves focus to show an approval', () => {
  const { session, update } = fakeSession();
  const open = vi.fn();
  bar(session, {
    phone: true,
    onShowApproval: vi.fn(),
    result: { label: 'Fireworks over a lake', open },
  });
  // Outside focus the transcript shows it already.
  expect(screen.queryByText('Fireworks over a lake')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Full screen' }));
  fireEvent.click(
    screen.getByRole('button', { name: /Fireworks over a lake/ }),
  );
  expect(open).toHaveBeenCalled();
  update({
    approval: { id: 'approval-1', action: 'Move / rename file?', voice: true },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Show the approval' }));
  expect(
    screen.getByRole('region', { name: 'Voice conversation' }),
  ).not.toHaveAttribute('data-focus');
});
