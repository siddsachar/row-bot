import { useEffect, useState } from 'react';
import { Headphones, Mic, MicOff, Smartphone, Speaker, X } from 'lucide-react';
import { IconButton, Menu, Select, type MenuAction } from '../../ui/primitives';
import { useOptionalRuntime } from '../../runtime';
import { VOICE_HOTKEYS, type VoiceHotkey } from '../../platform/types';
import { SettingsItem } from '../settings/anatomy';
import {
  chooseVoiceHotkey,
  HOTKEY_LABEL,
  useVoiceHotkey,
} from './push-to-talk';
import {
  allowDeviceNames,
  canChooseOutput,
  chooseDevice,
  useVoiceDevices,
  type AudioDevice,
  type DeviceNotice,
} from './devices';

/** Whether this device's id is among those connected now. */
const present = (devices: AudioDevice[], id: string) =>
  devices.some((device) => device.id === id);

/** The voice bar's menu: which microphone listens and which speaker plays, in this browser. */
export function DeviceMenu(props: { disabled?: boolean }) {
  const { list, chosen } = useVoiceDevices();
  const speakers = canChooseOutput();
  const inputs = list?.inputs ?? [];
  const outputs = speakers ? (list?.outputs ?? []) : [];
  const actions: MenuAction[] = [
    {
      label: 'Default microphone',
      description: list?.defaults.input || undefined,
      icon: <Mic size={16} />,
      selected: !present(inputs, chosen.input),
      onSelect: () => chooseDevice({ input: '' }),
    },
    ...inputs.map((device) => ({
      label: device.label,
      icon: <Mic size={16} />,
      selected: device.id === chosen.input,
      onSelect: () => chooseDevice({ input: device.id }),
    })),
    ...(speakers
      ? [
          {
            label: 'Default speaker',
            description: list?.defaults.output || undefined,
            icon: <Speaker size={16} />,
            selected: !present(outputs, chosen.output),
            separatorBefore: true,
            onSelect: () => chooseDevice({ output: '' }),
          },
          ...outputs.map((device) => ({
            label: device.label,
            icon: <Speaker size={16} />,
            selected: device.id === chosen.output,
            onSelect: () => chooseDevice({ output: device.id }),
          })),
        ]
      : []),
  ];
  const label = speakers ? 'Microphone and speaker' : 'Microphone';
  return (
    <Menu
      label={label}
      iconOnly
      variant="ghost"
      hint={label}
      disabled={props.disabled}
      className="voice-device-menu"
      actions={actions}
    >
      <Headphones size={16} aria-hidden />
    </Menu>
  );
}

const NOTICE: Record<DeviceNotice, string> = {
  input:
    'Your chosen microphone isn’t connected, so Row-Bot is listening with the default one.',
  output:
    'Your chosen speaker isn’t connected, so Row-Bot is speaking through the default one.',
  'no-input':
    'No microphone is connected. Connect one and Row-Bot listens again.',
};

/** The bar says when the default device stands in for the chosen one. */
export function DeviceNoticeLine({ notice }: { notice: DeviceNotice }) {
  const Icon =
    notice === 'output' ? Speaker : notice === 'input' ? Mic : MicOff;
  return (
    <p className="voice-bar-notice" role="status">
      <Icon size={16} aria-hidden />
      <span>{NOTICE[notice]}</span>
    </p>
  );
}

/** Which phone this is, for its listening limits; null elsewhere. */
export function phoneKind(
  agent = globalThis.navigator?.userAgent ?? '',
  touchPoints = globalThis.navigator?.maxTouchPoints ?? 0,
): 'iphone' | 'android' | null {
  if (
    /iPhone|iPad|iPod/.test(agent) ||
    (/Macintosh/.test(agent) && touchPoints > 1)
  )
    return 'iphone';
  return /Android/.test(agent) ? 'android' : null;
}

export const PHONE_LIMITS: Record<'iphone' | 'android', string> = {
  iphone:
    'On iPhone, Row-Bot stops listening when the screen locks or you switch apps, the home-screen app included. The screen stays on while you talk.',
  android:
    'On Android, Row-Bot stops listening when the screen turns off. The screen stays on while you talk.',
};

const SEEN = 'row-bot.voice-phone-limits-seen';

function seen() {
  try {
    return globalThis.localStorage?.getItem(SEEN) === '1';
  } catch {
    return false;
  }
}

/** Once, the first time voice opens on a phone: when it stops listening. Dismissible. */
export function PhoneLimitsNotice() {
  const [kind] = useState(() => (seen() ? null : phoneKind()));
  const [open, setOpen] = useState(kind !== null);
  useEffect(() => {
    if (!kind) return;
    try {
      globalThis.localStorage?.setItem(SEEN, '1');
    } catch {
      // Storage blocked: it may show again next time.
    }
  }, [kind]);
  if (!kind || !open) return null;
  return (
    <p className="voice-bar-notice voice-phone-limits" role="status">
      <Smartphone size={16} aria-hidden />
      <span>{PHONE_LIMITS[kind]}</span>
      <IconButton size="sm" label="Got it" onClick={() => setOpen(false)}>
        <X size={14} aria-hidden />
      </IconButton>
    </p>
  );
}

/**
 * Settings › Voice: the microphone and speaker, for this browser only. The
 * browser names devices only once the microphone was allowed, so until then
 * one tap asks for it.
 */
export function DeviceSettings() {
  const [asked, setAsked] = useState(0);
  const [refused, setRefused] = useState(false);
  const { list, chosen } = useVoiceDevices(asked);
  const speakers = canChooseOutput();
  if (!list) return null;
  if (!list.named)
    return (
      <SettingsItem
        label="Microphone"
        help={
          refused
            ? 'The microphone isn’t allowed in this browser. Allow it in the browser’s site settings to choose one.'
            : 'Your browser names your microphones and speakers once Row-Bot may use the microphone.'
        }
        control={
          <button
            type="button"
            className="button"
            onClick={() =>
              void allowDeviceNames().then(
                () => setAsked((value) => value + 1),
                () => setRefused(true),
              )
            }
          >
            Show my devices
          </button>
        }
      />
    );
  const select = (
    kind: 'input' | 'output',
    devices: AudioDevice[],
    system: string,
  ) => (
    <Select
      value={present(devices, chosen[kind]) ? chosen[kind] : ''}
      onChange={(event) => chooseDevice({ [kind]: event.target.value })}
    >
      <option value="">
        {system ? `System default (${system})` : 'System default'}
      </option>
      {devices.map((device) => (
        <option key={device.id} value={device.id}>
          {device.label}
        </option>
      ))}
    </Select>
  );
  const missing = (kind: 'input' | 'output', devices: AudioDevice[]) =>
    chosen[kind] && !present(devices, chosen[kind])
      ? ' The one you chose isn’t connected now, so the default is used until it is.'
      : '';
  return (
    <>
      <SettingsItem
        label="Microphone"
        help={`What Talk and Dictate listen with in this browser.${missing('input', list.inputs)}`}
        control={select('input', list.inputs, list.defaults.input)}
      />
      {speakers && (
        <SettingsItem
          label="Speaker"
          help={`Where Row-Bot’s voice plays in this browser.${missing('output', list.outputs)}`}
          control={select('output', list.outputs, list.defaults.output)}
        />
      )}
    </>
  );
}

/**
 * Settings › Voice, with push to talk on, in the Windows desktop app: a key to
 * hold anywhere to talk, chosen on this computer (off by default).
 */
export function HotkeySetting() {
  const platform = useOptionalRuntime()?.platform;
  const key = useVoiceHotkey();
  const [offered, setOffered] = useState(false);
  useEffect(() => {
    let live = true;
    void platform?.discover().then((result) => {
      if (live)
        setOffered(
          result.status === 'ok' &&
            result.value.capabilities.includes('voice_hotkey'),
        );
    });
    return () => {
      live = false;
    };
  }, [platform]);
  if (!offered) return null;
  return (
    <SettingsItem
      label="Hold a key to talk"
      sub
      help="Works while voice is open, even when Row-Bot is in the background. Row-Bot listens for this one key only, and only then."
      control={
        <Select
          value={key}
          onChange={(event) =>
            chooseVoiceHotkey(event.target.value as VoiceHotkey | '')
          }
        >
          <option value="">Off</option>
          {VOICE_HOTKEYS.map((value) => (
            <option key={value} value={value}>
              {HOTKEY_LABEL[value]}
            </option>
          ))}
        </Select>
      }
    />
  );
}
