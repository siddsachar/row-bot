import { useEffect, useRef, useState } from 'react';
import { Ear, EarOff } from 'lucide-react';
import { useOptionalRuntime, useRuntime } from '../../runtime';
import { useBackgroundReady } from '../../shell-settled';
import { usePhone } from '../../ui/on-your-computer';
import { Button, Hint, IconButton } from '../../ui/primitives';
import {
  WakeListener,
  announceWakeWord,
  setWakeListener,
  useWakeWord,
  useWakeWordEnabled,
  wakeOpenChat,
  type WakeSnapshot,
} from './wake-word';

/**
 * Whether "Hey Row-Bot" can listen in this window: the host says `available`
 * only to the app on the computer Row-Bot runs on (with its model). A phone
 * never shows it.
 */
export function useWakeWordAvailable(): boolean | null {
  const controller = useOptionalRuntime()?.controller;
  const phone = usePhone();
  const [reason, setReason] = useState<string | null>(null);
  useEffect(() => {
    if (!controller) return;
    const abort = new AbortController();
    controller
      .dictationCapability(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted)
          setReason(
            (value as { wake_reason?: string }).wake_reason ??
              'host_unavailable',
          );
      })
      .catch(() => {
        if (!abort.signal.aborted) setReason('host_unavailable');
      });
    return () => abort.abort();
  }, [controller]);
  if (phone || !controller) return false;
  return reason === null ? null : reason === 'available';
}

/**
 * Mount once in the workspace. While the wake word is on (and this window can
 * use it), one listener runs; hearing it starts Row-Bot Voice in the open
 * chat, or in a new chat as Home's Talk does. It reads nothing until the
 * first screen is up.
 */
export function WakeWordHost(props: { startInNewChat(): Promise<unknown> }) {
  return useBackgroundReady() ? <WakeWordListening {...props} /> : null;
}

function WakeWordListening({
  startInNewChat,
}: {
  startInNewChat(): Promise<unknown>;
}) {
  const { controller } = useRuntime();
  const available = useWakeWordAvailable();
  const enabled = useWakeWordEnabled();
  useEffect(() => {
    if (!available) return;
    const abort = new AbortController();
    void controller
      .settingsSnapshot(abort.signal)
      .then((snapshot) => {
        if (!abort.signal.aborted && snapshot)
          announceWakeWord(snapshot.voice.runtime.wake_word === true);
      })
      .catch(() => undefined);
    return () => abort.abort();
  }, [available, controller]);
  const newChat = useRef(startInNewChat);
  newChat.current = startInNewChat;
  useEffect(() => {
    if (!available || !enabled) return;
    const listener = new WakeListener({
      ticket: async () => (await controller.wakeTicket()).ticket,
      openSocket: () => controller.openWakeSocket(),
      onWake: () => wakeOpenChat() ?? newChat.current(),
    });
    setWakeListener(listener);
    listener.arm();
    return () => {
      listener.disarm();
      setWakeListener(null);
    };
  }, [available, enabled, controller]);
  return null;
}

function indicator(snapshot: WakeSnapshot): {
  label: string;
  short: string;
  listening: boolean;
} {
  if (snapshot.paused)
    return {
      label: 'Wake word off in this window. Listen again',
      short: 'Wake word off here',
      listening: false,
    };
  switch (snapshot.state) {
    case 'listening':
    case 'starting':
      return {
        label: 'Listening for “Hey Row-Bot”. Turn off',
        short: 'Listening for “Hey Row-Bot”',
        listening: true,
      };
    case 'held':
      return {
        label: 'Wake word waits while voice is on. Turn off',
        short: 'Wake word waits for voice',
        listening: false,
      };
    case 'hidden':
      return {
        label: 'Wake word paused while the window is hidden. Turn off',
        short: 'Wake word paused',
        listening: false,
      };
    case 'failed':
      return {
        label: `${snapshot.error} Listen again`,
        short: 'Wake word stopped',
        listening: false,
      };
    default:
      return {
        label: 'Wake word off. Listen again',
        short: 'Wake word off',
        listening: false,
      };
  }
}

/**
 * While the wake word is on: a clear mark that it listens, and one click to
 * stop (or start again). `row` shows its state in words, as the expanded
 * navigation's rows do; the collapsed rail shows the icon alone.
 */
export function WakeWordIndicator({ row = false }: { row?: boolean }) {
  const { snapshot, listener } = useWakeWord();
  if (!listener) return null;
  const { label, short, listening } = indicator(snapshot);
  const resumable = snapshot.paused || snapshot.state === 'failed';
  const toggle = () => (resumable ? listener.resume() : listener.pause());
  const state = snapshot.paused ? 'paused' : snapshot.state;
  const icon = resumable ? (
    <EarOff size={row ? 16 : 18} aria-hidden />
  ) : (
    <Ear size={row ? 16 : 18} aria-hidden />
  );
  if (row)
    return (
      <Hint label={label}>
        <Button
          variant="ghost"
          className="nav-row wake-word-indicator"
          data-state={state}
          aria-label={label}
          aria-pressed={listening}
          onClick={toggle}
        >
          {icon}
          <span className="nav-row-label">{short}</span>
        </Button>
      </Hint>
    );
  return (
    <IconButton
      label={label}
      className="wake-word-indicator"
      data-state={state}
      pressed={listening}
      onClick={toggle}
    >
      {icon}
    </IconButton>
  );
}
