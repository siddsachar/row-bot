import { useEffect, useSyncExternalStore } from 'react';
import { clientError } from '../../api/errors';
import type { ReadAloudSpeech } from '../../api/types';
import { followBuddyVoice, wavEnvelope } from '../buddy/living/voice';

/**
 * Read aloud speaks a saved reply with the speech output chosen in Voice
 * settings (Kokoro), never the device's own voice. The server reads the
 * reply's words itself and returns it a part at a time: the first part is a
 * sentence or so, and each next part is asked for while the one before it
 * plays, so speech starts quickly and stays in order. One reply reads at a
 * time, and Buddy's mouth follows the voice.
 */
export type ReadPart = (
  part: number,
  signal: AbortSignal,
) => Promise<ReadAloudSpeech>;
export type ReadingPhase = 'idle' | 'loading' | 'playing';

type Reading = {
  owner: string;
  abort: AbortController;
  phase: Exclude<ReadingPhase, 'idle'>;
  audio?: HTMLAudioElement;
  url?: string;
  stopVoice: () => void;
  endPlayback?: () => void;
};

let current: Reading | null = null;
const listeners = new Set<() => void>();
function notify() {
  for (const listener of listeners) listener();
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** An earlier part that is still being made answers busy for a moment after Stop: wait for it. */
const BUSY_RETRIES = 4;
const BUSY_WAIT_MS = 1000;

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Stopped', 'AbortError'));
      },
      { once: true },
    );
  });
}

async function fetchPart(read: ReadPart, part: number, signal: AbortSignal) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await read(part, signal);
    } catch (cause) {
      if (
        signal.aborted ||
        attempt >= BUSY_RETRIES ||
        clientError(cause).code !== 'voice_session_busy'
      )
        throw cause;
      await wait(BUSY_WAIT_MS, signal);
    }
  }
}

function releaseAudio(reading: Reading) {
  reading.stopVoice();
  reading.stopVoice = () => {};
  if (reading.audio) {
    reading.audio.onended = null;
    reading.audio.onerror = null;
    reading.audio.pause();
    reading.audio = undefined;
  }
  if (reading.url) URL.revokeObjectURL(reading.url);
  reading.url = undefined;
}

async function play(reading: Reading, audio: Blob) {
  const envelope = wavEnvelope(await audio.arrayBuffer());
  if (current !== reading) return;
  reading.url = URL.createObjectURL(audio);
  const element = new Audio(reading.url);
  reading.audio = element;
  if (envelope) reading.stopVoice = followBuddyVoice(element, envelope);
  if (reading.phase !== 'playing') {
    reading.phase = 'playing';
    notify();
  }
  try {
    await new Promise<void>((resolve, reject) => {
      reading.endPlayback = resolve;
      element.onended = () => resolve();
      element.onerror = () => reject(new Error('audio_playback_failed'));
      void element.play().catch(reject);
    });
  } finally {
    reading.endPlayback = undefined;
    releaseAudio(reading);
  }
}

/**
 * Read a reply aloud, part by part. Resolves when it ends or is stopped;
 * rejects with the reason it couldn't speak.
 */
export async function startReading(owner: string, read: ReadPart) {
  stopReading();
  const reading: Reading = {
    owner,
    abort: new AbortController(),
    phase: 'loading',
    stopVoice: () => {},
  };
  current = reading;
  notify();
  const signal = reading.abort.signal;
  try {
    let next = fetchPart(read, 0, signal);
    for (let part = 0; current === reading; part++) {
      const speech = await next;
      const more = part + 1 < speech.parts;
      if (more) {
        next = fetchPart(read, part + 1, signal);
        // Seen when its turn comes; a Stop before then is not a failure.
        next.catch(() => {});
      }
      await play(reading, speech.audio);
      if (!more) break;
    }
  } catch (cause) {
    if (current !== reading || signal.aborted) return;
    throw cause;
  } finally {
    if (current === reading) {
      current = null;
      notify();
    }
  }
}

/** Stop the reply being read (only *owner*'s, when given). */
export function stopReading(owner?: string) {
  const reading = current;
  if (!reading || (owner && reading.owner !== owner)) return;
  current = null;
  reading.abort.abort();
  reading.endPlayback?.();
  releaseAudio(reading);
  notify();
}

/** Whether *owner*'s reply is being read: fetching its first part, or speaking. */
export function useReading(owner: string): ReadingPhase {
  return useSyncExternalStore(subscribe, () =>
    current?.owner === owner ? current.phase : 'idle',
  );
}

/**
 * Whether Read aloud can speak, from the voice capability: 'available', a
 * code saying what it needs (kokoro_model_missing), or '' until known. One
 * check per connection serves every reply; a missing voice is checked again
 * on the next reply shown, a little later, so installing it is noticed.
 */
type ReadinessController = {
  dictationCapability(): Promise<{ read_aloud_reason?: string }>;
};
type Readiness = {
  reason: string;
  checkedAt: number;
  checking: boolean;
  listeners: Set<() => void>;
};
const readiness = new WeakMap<ReadinessController, Readiness>();
const RECHECK_MS = 10_000;

function readinessOf(controller: ReadinessController): Readiness {
  let found = readiness.get(controller);
  if (!found) {
    found = {
      reason: '',
      checkedAt: -Infinity,
      checking: false,
      listeners: new Set(),
    };
    readiness.set(controller, found);
  }
  return found;
}

/** What a Read aloud attempt showed: 'available', or the code it failed with. */
export function noteReadAloud(controller: ReadinessController, reason: string) {
  const found = readinessOf(controller);
  found.reason = reason;
  found.checkedAt = Date.now();
  for (const listener of found.listeners) listener();
}

export function useReadAloudReadiness(controller: ReadinessController): string {
  const found = readinessOf(controller);
  const value = useSyncExternalStore(
    (listener) => {
      found.listeners.add(listener);
      return () => found.listeners.delete(listener);
    },
    () => found.reason,
  );
  useEffect(() => {
    if (found.reason === 'available' || found.checking) return;
    if (Date.now() - found.checkedAt < RECHECK_MS) return;
    found.checking = true;
    controller
      .dictationCapability()
      .then((capability) =>
        noteReadAloud(
          controller,
          capability.read_aloud_reason ?? 'host_unavailable',
        ),
      )
      .catch(() => {})
      .finally(() => {
        found.checking = false;
      });
  }, [controller, found]);
  return value;
}
