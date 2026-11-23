import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  canChooseOutput,
  chooseDevice,
  deviceChoice,
  listDevices,
  openMicrophone,
  resetDeviceChoice,
  type DeviceNotice,
} from './devices';
import { BrowserVoiceAudio, CAPTURE_CONSTRAINTS } from './voice-audio';

/** A microphone track; the browser ends it when its device goes away. */
class FakeTrack extends EventTarget {
  kind = 'audio';
  enabled = true;
  stop = vi.fn();
  constructor(readonly deviceId: string) {
    super();
  }
  getSettings() {
    return { deviceId: this.deviceId };
  }
}

type Row = { deviceId: string; kind: MediaDeviceKind; label: string };

/** The system's audio devices, which a test plugs in and pulls out. */
class FakeDevices extends EventTarget {
  rows: Row[] = [
    { deviceId: 'default', kind: 'audioinput', label: 'Default - Laptop mic' },
    { deviceId: 'laptop', kind: 'audioinput', label: 'Laptop mic' },
    { deviceId: 'headset', kind: 'audioinput', label: 'Headset (WH-1000XM5)' },
    {
      deviceId: 'default',
      kind: 'audiooutput',
      label: 'Default - Laptop speakers',
    },
    { deviceId: 'speakers', kind: 'audiooutput', label: 'Laptop speakers' },
    { deviceId: 'phones', kind: 'audiooutput', label: 'Headset (WH-1000XM5)' },
  ];
  asked: MediaTrackConstraints[] = [];
  tracks: FakeTrack[] = [];
  async enumerateDevices() {
    return this.rows.map((row) => ({ ...row, groupId: '' }));
  }
  async getUserMedia(constraints: { audio: MediaTrackConstraints }) {
    this.asked.push(constraints.audio);
    const inputs = this.rows.filter(
      (row) => row.kind === 'audioinput' && row.deviceId !== 'default',
    );
    if (!inputs.length)
      throw Object.assign(new Error('none'), { name: 'NotFoundError' });
    const wanted = (constraints.audio.deviceId as { ideal?: string })?.ideal;
    const track = new FakeTrack(
      inputs.some((row) => row.deviceId === wanted) ? wanted! : 'laptop',
    );
    this.tracks.push(track);
    return {
      getTracks: () => [track],
      getAudioTracks: () => [track],
    } as unknown as MediaStream;
  }
  unplug(id: string) {
    this.rows = this.rows.filter((row) => row.deviceId !== id);
    this.tracks
      .filter((track) => track.deviceId === id)
      .forEach((track) => track.dispatchEvent(new Event('ended')));
    this.dispatchEvent(new Event('devicechange'));
  }
  plug(row: Row) {
    this.rows.push(row);
    this.dispatchEvent(new Event('devicechange'));
  }
}

/** An audio context that plays nowhere; *sinks* adds Chromium's setSinkId. */
function fakeContexts(sinks: boolean) {
  const node = () => ({
    connect: (next: unknown) => next,
    gain: { value: 1, setTargetAtTime() {}, setValueAtTime() {} },
    fftSize: 512,
    getFloatTimeDomainData() {},
    port: { onmessage: null },
  });
  const sinkIds: string[] = [];
  class Context {
    state = 'running';
    currentTime = 0;
    destination = {};
    audioWorklet = { addModule: async () => undefined };
    createMediaStreamSource = node;
    createGain = node;
    createAnalyser = node;
    createMediaStreamDestination = node;
    resume = async () => undefined;
    close = async () => undefined;
  }
  if (sinks)
    Object.assign(Context.prototype, {
      setSinkId: async (id: string) => {
        sinkIds.push(id);
      },
    });
  vi.stubGlobal('AudioContext', Context);
  vi.stubGlobal(
    'AudioWorkletNode',
    class {
      port = { onmessage: null };
      connect = (next: unknown) => next;
    },
  );
  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn(() => 0),
  );
  return sinkIds;
}

let devices: FakeDevices;

beforeEach(() => {
  localStorage.clear();
  resetDeviceChoice();
  devices = new FakeDevices();
  vi.stubGlobal('navigator', {
    ...navigator,
    userAgent: 'Chrome',
    mediaDevices: devices,
  });
});

afterEach(() => vi.unstubAllGlobals());

async function started(choice = {}) {
  chooseDevice(choice);
  const notices: (DeviceNotice | null)[] = [];
  const audio = new BrowserVoiceAudio();
  await audio.startCapture({
    frame: () => undefined,
    level: () => undefined,
    device: (notice) => notices.push(notice),
  });
  return { audio, notices };
}

it('lists microphones and speakers by their system names, without the default stand-ins', async () => {
  const list = await listDevices(devices as unknown as MediaDevices);
  expect(list.inputs).toEqual([
    { id: 'laptop', label: 'Laptop mic' },
    { id: 'headset', label: 'Headset (WH-1000XM5)' },
  ]);
  expect(list.outputs.map((device) => device.label)).toEqual([
    'Laptop speakers',
    'Headset (WH-1000XM5)',
  ]);
  expect(list).toMatchObject({
    named: true,
    defaults: { input: 'Laptop mic', output: 'Laptop speakers' },
  });
  // Before the microphone is allowed the browser gives no names.
  devices.rows = devices.rows.map((row) => ({ ...row, label: '' }));
  const unnamed = await listDevices(devices as unknown as MediaDevices);
  expect(unnamed.named).toBe(false);
  expect(unnamed.inputs.map((device) => device.label)).toEqual([
    'Microphone 1',
    'Microphone 2',
  ]);
});

it('remembers the choice in this browser, and keeps it for the page when storage is blocked', () => {
  chooseDevice({ input: 'headset' });
  chooseDevice({ output: 'phones' });
  resetDeviceChoice();
  expect(deviceChoice()).toEqual({ input: 'headset', output: 'phones' });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  chooseDevice({ input: 'laptop' });
  expect(deviceChoice().input).toBe('laptop');
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  resetDeviceChoice();
  expect(deviceChoice()).toEqual({ input: '', output: '' });
  vi.restoreAllMocks();
});

it('asks for the chosen microphone and says when the default stood in', async () => {
  const media = devices as unknown as MediaDevices;
  const chosen = await openMicrophone(CAPTURE_CONSTRAINTS, 'headset', media);
  expect(devices.asked.at(-1)).toEqual({
    ...CAPTURE_CONSTRAINTS,
    deviceId: { ideal: 'headset' },
  });
  expect(chosen.fellBack).toBe(false);
  const gone = await openMicrophone(CAPTURE_CONSTRAINTS, 'usb-mic', media);
  expect(gone.fellBack).toBe(true);
  const plain = await openMicrophone(CAPTURE_CONSTRAINTS, '', media);
  expect(devices.asked.at(-1)).toEqual(CAPTURE_CONSTRAINTS);
  expect(plain.fellBack).toBe(false);
});

it('offers a speaker choice only where audio contexts can play to one', () => {
  fakeContexts(false);
  expect(canChooseOutput()).toBe(false);
  fakeContexts(true);
  expect(canChooseOutput()).toBe(true);
});

it('falls back to the default microphone when the chosen one is unplugged mid-session, and goes back when it returns', async () => {
  fakeContexts(true);
  const { audio, notices } = await started({ input: 'headset' });
  expect(devices.asked.at(-1)?.deviceId).toEqual({ ideal: 'headset' });
  expect(notices).toEqual([]);
  devices.unplug('headset');
  await vi.waitFor(() => expect(notices).toEqual(['input']));
  // The default took over at once.
  expect(devices.tracks.at(-1)?.deviceId).toBe('laptop');
  devices.plug({
    deviceId: 'headset',
    kind: 'audioinput',
    label: 'Headset (WH-1000XM5)',
  });
  await vi.waitFor(() => expect(notices).toEqual(['input', null]));
  expect(devices.tracks.at(-1)?.deviceId).toBe('headset');
  // The replaced microphone is released.
  expect(devices.tracks[1].stop).toHaveBeenCalled();
  audio.close();
});

it('switches microphone at once when the person chooses another, and keeps a mute', async () => {
  fakeContexts(true);
  const { audio } = await started();
  audio.setMicEnabled(false);
  chooseDevice({ input: 'headset' });
  await vi.waitFor(() =>
    expect(devices.tracks.at(-1)).toMatchObject({
      deviceId: 'headset',
      enabled: false,
    }),
  );
  expect(devices.tracks[0].stop).toHaveBeenCalled();
  audio.close();
});

it('says when no microphone is left, and listens again once one is connected', async () => {
  fakeContexts(true);
  const { audio, notices } = await started();
  devices.unplug('headset');
  devices.unplug('laptop');
  await vi.waitFor(() => expect(notices.at(-1)).toBe('no-input'));
  devices.plug({ deviceId: 'usb', kind: 'audioinput', label: 'USB mic' });
  await vi.waitFor(() => expect(notices.at(-1)).toBe(null));
  expect(devices.tracks.at(-1)?.deviceId).toBe('laptop');
  audio.close();
});

it('plays through the chosen speaker, and the default while it is disconnected', async () => {
  const sinks = fakeContexts(true);
  const { audio, notices } = await started({ output: 'phones' });
  expect(sinks).toEqual(['phones']);
  devices.unplug('phones');
  await vi.waitFor(() => expect(notices).toEqual(['output']));
  expect(sinks.at(-1)).toBe('');
  devices.plug({
    deviceId: 'phones',
    kind: 'audiooutput',
    label: 'Headset (WH-1000XM5)',
  });
  await vi.waitFor(() => expect(notices).toEqual(['output', null]));
  expect(sinks.at(-1)).toBe('phones');
  chooseDevice({ output: 'speakers' });
  await vi.waitFor(() => expect(sinks.at(-1)).toBe('speakers'));
  audio.close();
});

it('never picks a speaker where the browser cannot', async () => {
  fakeContexts(false);
  const { audio, notices } = await started({ output: 'phones' });
  devices.unplug('phones');
  await Promise.resolve();
  expect(notices).toEqual([]);
  audio.close();
});

it('stops following devices once closed', async () => {
  fakeContexts(true);
  const { audio } = await started({ input: 'headset' });
  audio.close();
  const asked = devices.asked.length;
  devices.unplug('headset');
  chooseDevice({ input: 'laptop' });
  await Promise.resolve();
  expect(devices.asked).toHaveLength(asked);
});
