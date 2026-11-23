/**
 * The microphone and speaker Row-Bot Voice uses in this browser. The choice
 * is remembered here, per browser (never sent to Row-Bot); '' is the
 * system's own default. Devices are named as the operating system names them
 * (a Bluetooth headset included), which browsers show only once the
 * microphone was allowed. Choosing a speaker needs `setSinkId` on audio
 * contexts (Chromium engines: Chrome, Edge, the Windows app); elsewhere
 * (Safari, iPhone, Firefox) the system's own output is used and no speaker
 * choice is offered.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';

export type AudioDevice = { id: string; label: string };
export type DeviceChoice = { input: string; output: string };
export type DeviceList = {
  inputs: AudioDevice[];
  outputs: AudioDevice[];
  /** The browser named the devices (the microphone was allowed). */
  named: boolean;
  /** What the system default is now, where the browser says ("Headset (WH-1000XM5)"). */
  defaults: { input: string; output: string };
};

/** Why the bar says the device changed: the chosen one is gone, or no microphone is left. */
export type DeviceNotice = 'input' | 'output' | 'no-input';

const KEY = 'row-bot.voice-devices';
const DEFAULTS: DeviceChoice = { input: '', output: '' };
const NO_DEVICES: DeviceList = {
  inputs: [],
  outputs: [],
  named: false,
  defaults: { input: '', output: '' },
};
// The browser's own stand-ins for the system default; the lists show "System default" instead.
const STAND_INS = new Set(['', 'default', 'communications']);

function read(): DeviceChoice {
  try {
    const saved = JSON.parse(
      globalThis.localStorage?.getItem(KEY) ?? 'null',
    ) as Partial<DeviceChoice> | null;
    return {
      input: typeof saved?.input === 'string' ? saved.input : '',
      output: typeof saved?.output === 'string' ? saved.output : '',
    };
  } catch {
    return DEFAULTS;
  }
}

let choice: DeviceChoice | null = null;
const listeners = new Set<() => void>();

/** The microphone and speaker chosen in this browser. */
export function deviceChoice(): DeviceChoice {
  choice ??= read();
  return choice;
}

export function chooseDevice(next: Partial<DeviceChoice>) {
  choice = { ...deviceChoice(), ...next };
  try {
    globalThis.localStorage?.setItem(KEY, JSON.stringify(choice));
  } catch {
    // Storage blocked (a private window): the choice lasts while the page does.
  }
  listeners.forEach((listener) => listener());
}

export function onDeviceChoice(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Tests: forget the remembered choice so the next read comes from storage. */
export function resetDeviceChoice() {
  choice = null;
}

/** Whether a speaker can be chosen here (an audio context's `setSinkId`). */
export function canChooseOutput(): boolean {
  return (
    typeof AudioContext !== 'undefined' &&
    typeof (AudioContext.prototype as { setSinkId?: unknown }).setSinkId ===
      'function'
  );
}

/** This browser's microphones and speakers, without the default stand-ins. */
export async function listDevices(
  media: MediaDevices | undefined = globalThis.navigator?.mediaDevices,
): Promise<DeviceList> {
  if (!media?.enumerateDevices) return NO_DEVICES;
  const all = await media.enumerateDevices();
  const system = (kind: MediaDeviceKind) =>
    all
      .find((device) => device.kind === kind && device.deviceId === 'default')
      ?.label.replace(/^Default\s*-\s*/, '') ?? '';
  const pick = (kind: MediaDeviceKind, unnamed: string) =>
    all
      .filter(
        (device) => device.kind === kind && !STAND_INS.has(device.deviceId),
      )
      .map((device, index) => ({
        id: device.deviceId,
        label: device.label || `${unnamed} ${index + 1}`,
      }));
  return {
    inputs: pick('audioinput', 'Microphone'),
    outputs: pick('audiooutput', 'Speaker'),
    named: all.some((device) => device.kind === 'audioinput' && device.label),
    defaults: { input: system('audioinput'), output: system('audiooutput') },
  };
}

/**
 * Open the chosen microphone with the capture settings given, or the
 * default when it isn't connected; `fellBack` says the default stood in.
 */
export async function openMicrophone(
  constraints: MediaTrackConstraints,
  chosen: string,
  media: MediaDevices = navigator.mediaDevices,
): Promise<{ stream: MediaStream; fellBack: boolean }> {
  const stream = await media.getUserMedia({
    audio: chosen
      ? { ...constraints, deviceId: { ideal: chosen } }
      : constraints,
  });
  const used = stream.getAudioTracks()[0]?.getSettings?.().deviceId;
  return { stream, fellBack: Boolean(chosen && used && used !== chosen) };
}

/** Whether a device with this id is connected now. */
export async function connected(
  kind: 'inputs' | 'outputs',
  id: string,
  media?: MediaDevices,
): Promise<boolean> {
  try {
    return (await listDevices(media))[kind].some((device) => device.id === id);
  } catch {
    return false;
  }
}

/** Ask for the microphone once so the browser names the devices, then let it go. */
export async function allowDeviceNames(
  media: MediaDevices = navigator.mediaDevices,
): Promise<void> {
  const stream = await media.getUserMedia({ audio: true });
  stream.getTracks().forEach((track) => track.stop());
}

/** The device lists (kept current as devices come and go) and the choice. */
export function useVoiceDevices(refresh = 0) {
  const chosen = useSyncExternalStore(onDeviceChoice, deviceChoice);
  const [list, setList] = useState<DeviceList | null>(null);
  useEffect(() => {
    const media = globalThis.navigator?.mediaDevices;
    let live = true;
    const update = () =>
      void listDevices(media).then(
        (next) => live && setList(next),
        () => live && setList(NO_DEVICES),
      );
    update();
    media?.addEventListener?.('devicechange', update);
    return () => {
      live = false;
      media?.removeEventListener?.('devicechange', update);
    };
  }, [refresh]);
  return { list, chosen };
}
