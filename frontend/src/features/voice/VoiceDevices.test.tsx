import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { deviceChoice, resetDeviceChoice } from './devices';
import { DeviceSettings } from './VoiceDevices';

/** A browser whose devices get names once the microphone is allowed (or refused). */
function browser(allow: boolean) {
  let named = false;
  const rows = () => [
    {
      deviceId: 'laptop',
      kind: 'audioinput',
      label: named ? 'Laptop mic' : '',
    },
    {
      deviceId: 'phones',
      kind: 'audiooutput',
      label: named ? 'Headset (WH-1000XM5)' : '',
    },
  ];
  const media = {
    enumerateDevices: vi.fn(async () => rows()),
    getUserMedia: vi.fn(async () => {
      if (!allow)
        throw Object.assign(new Error('no'), { name: 'NotAllowedError' });
      named = true;
      return { getTracks: () => [{ stop: vi.fn() }] };
    }),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
  vi.stubGlobal('navigator', { ...navigator, mediaDevices: media });
  return media;
}

beforeEach(() => {
  localStorage.clear();
  resetDeviceChoice();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('asks for the microphone once to name the devices, then lists them to choose', async () => {
  const media = browser(true);
  render(<DeviceSettings />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Show my devices' }),
  );
  const microphone = await screen.findByRole('combobox', {
    name: 'Microphone',
  });
  expect(media.getUserMedia).toHaveBeenCalledWith({ audio: true });
  expect(
    [...microphone.querySelectorAll('option')].map((row) => row.textContent),
  ).toEqual(['System default', 'Laptop mic']);
  fireEvent.change(microphone, { target: { value: 'laptop' } });
  expect(deviceChoice().input).toBe('laptop');
  // No setSinkId on audio contexts in this browser: no speaker choice.
  expect(screen.queryByRole('combobox', { name: 'Speaker' })).toBeNull();
});

it('says so when the microphone is refused', async () => {
  browser(false);
  render(<DeviceSettings />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Show my devices' }),
  );
  expect(
    await screen.findByText(/The microphone isn’t allowed in this browser/),
  ).toBeInTheDocument();
});

it('says when the chosen device isn’t connected now', async () => {
  browser(true);
  render(<DeviceSettings />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Show my devices' }),
  );
  await screen.findByRole('combobox', { name: 'Microphone' });
  cleanup();
  localStorage.setItem(
    'row-bot.voice-devices',
    JSON.stringify({ input: 'usb-mic', output: '' }),
  );
  resetDeviceChoice();
  render(<DeviceSettings />);
  expect(
    await screen.findByText(/The one you chose isn’t connected now/),
  ).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Microphone' })).toHaveValue('');
});
