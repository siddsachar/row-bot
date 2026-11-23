import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import VoiceBar, { latencyLine, type VoiceBarSession } from './VoiceBar';
import type { VoiceSnapshot } from './voice-session';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** A browser with a camera, and a screen to share when *screen*. */
function devices(screen: boolean) {
  vi.stubGlobal('navigator', {
    ...navigator,
    mediaDevices: {
      getUserMedia: vi.fn(),
      ...(screen && { getDisplayMedia: vi.fn() }),
    },
  });
}

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
    heard: null,
    unspoken: null,
    share: null,
    canShare: 'no',
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
    startShare: vi.fn(async () => undefined),
    stopShare: vi.fn(),
    switchCamera: vi.fn(async () => undefined),
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
  render(
    <MemoryRouter>
      <VoiceBar {...props} />
    </MemoryRouter>,
  );
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
  expect(screen.getByRole('button', { name: 'No details yet' })).toBeDisabled();
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
  fireEvent.click(screen.getByRole('button', { name: 'Show details' }));
  expect(screen.getByText('Replied in 0.6 s')).toBeVisible();
});

it('the details name the language Row-Bot heard, quietly beside the timing', () => {
  const { session, update } = fakeSession({ heard: 'fr' });
  bar(session);
  fireEvent.click(screen.getByRole('button', { name: 'Show details' }));
  expect(screen.getByText('French')).toBeVisible();
  update({
    latency: {
      last: { voice_to_voice: 640 },
      summary: { turns: 1, stages: { voice_to_voice: { p50: 640, p90: 640 } } },
    },
  });
  expect(screen.getByText('Replied in 0.6 s · French')).toBeVisible();
});

it('captions in a right-to-left script read right to left, in their language', () => {
  const { session } = fakeSession({
    you: { text: 'مرحبا، كيف حالك؟', final: true, language: 'ar' },
    rowBot: { text: 'שלום, מה שלומך?', final: true, kind: 'answer' },
  });
  bar(session);
  const you = screen.getByText('مرحبا، كيف حالك؟');
  expect(you).toHaveAttribute('dir', 'auto');
  expect(you).toHaveAttribute('lang', 'ar');
  expect(screen.getByText('שלום, מה שלומך?')).toHaveAttribute('dir', 'auto');
});

it('an answer in a language the voice can not speak stays on screen, and the bar says why', () => {
  const { session, update } = fakeSession({
    rowBot: { text: 'Es ist drei Uhr.', final: true, kind: 'answer' },
    unspoken: 'de',
  });
  bar(session);
  expect(screen.getByText('Es ist drei Uhr.')).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Row-Bot’s voice can’t speak German, so its answers stay on screen.',
  );
  update({ unspoken: null });
  expect(screen.queryByText(/can’t speak German/)).toBeNull();
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
    approval: { id: 'approval-1', title: 'Move a file?', voice: true },
  });
  expect(screen.getByText('Move a file?')).toBeInTheDocument();
  expect(
    screen.getByText('Say yes or no, or tap the card'),
  ).toBeInTheDocument();
  // The bar never answers it: only the card's own buttons do.
  expect(screen.queryByRole('button', { name: /approve/i })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show the approval' }));
  expect(onShowApproval).toHaveBeenCalled();
  update({
    approval: { id: 'approval-2', title: 'Send an email?', voice: false },
  });
  expect(screen.getByText('Needs a tap on Approve')).toBeInTheDocument();
  expect(screen.queryByText('Say yes or no, or tap the card')).toBeNull();
  update({ approval: null });
  expect(screen.queryByText('Send an email?')).toBeNull();
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
    approval: { id: 'approval-1', title: 'Move a file?', voice: true },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Show the approval' }));
  expect(
    screen.getByRole('region', { name: 'Voice conversation' }),
  ).not.toHaveAttribute('data-focus');
});

it('no share control until the host says sharing can work', () => {
  devices(true);
  const { session } = fakeSession({ canShare: 'no' });
  bar(session);
  expect(screen.queryByRole('button', { name: /Share/ })).toBeNull();
});

it('a computer shares its camera or a screen from one menu', async () => {
  devices(true);
  const { session } = fakeSession({ canShare: 'yes' });
  bar(session);
  const trigger = screen.getByRole('button', {
    name: 'Share camera or screen',
  });
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
  const items = await screen.findAllByRole('menuitem');
  expect(items.map((item) => item.textContent)).toEqual([
    expect.stringContaining('Camera'),
    expect.stringContaining('Screen or window'),
  ]);
  fireEvent.click(items[1]);
  expect(session.startShare).toHaveBeenCalledWith('screen');
});

it('where a screen can’t be shared (a phone), one tap shares the camera', () => {
  devices(false);
  const { session } = fakeSession({ canShare: 'yes' });
  bar(session, { phone: true });
  fireEvent.click(screen.getByRole('button', { name: 'Share camera' }));
  expect(session.startShare).toHaveBeenCalledWith('camera');
});

it('while sharing, the bar always says so with a live preview, and one tap stops it', () => {
  devices(true);
  const stream = { id: 'synthetic' } as unknown as MediaStream;
  const { session, update } = fakeSession({
    canShare: 'yes',
    share: { source: 'camera', stream, facingUser: true, canSwitch: true },
  });
  bar(session);
  const indicator = screen.getByRole('status');
  expect(indicator).toHaveTextContent('Camera on');
  const preview = document.querySelector(
    'video.voice-share-preview',
  ) as HTMLVideoElement;
  expect(preview.srcObject).toBe(stream);
  expect(preview).toHaveAttribute('data-mirrored', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'Switch camera' }));
  expect(session.switchCamera).toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Stop sharing' }));
  expect(session.stopShare).toHaveBeenCalled();
  update({
    share: { source: 'screen', stream, facingUser: false, canSwitch: false },
  });
  expect(screen.getByRole('status')).toHaveTextContent('Sharing your screen');
  expect(screen.queryByRole('button', { name: 'Switch camera' })).toBeNull();
  expect(
    document.querySelector('video.voice-share-preview'),
  ).not.toHaveAttribute('data-mirrored');
});

it('a chat whose model can’t see images says so plainly, with the way to one that can', () => {
  devices(true);
  const { session } = fakeSession({ canShare: 'blind' });
  bar(session);
  fireEvent.click(
    screen.getByRole('button', { name: 'Share camera or screen' }),
  );
  expect(session.startShare).not.toHaveBeenCalled();
  expect(screen.getByRole('status')).toHaveTextContent(
    'This chat’s model can’t see images, so the camera and screen can’t be shared.',
  );
  expect(
    screen.getByRole('link', { name: 'choose a vision model' }),
  ).toHaveAttribute('href', '/settings/models#vision-model');
});
