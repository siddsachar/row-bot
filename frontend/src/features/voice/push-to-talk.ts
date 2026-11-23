/**
 * Push to talk from the keyboard. In Row-Bot's window, holding Space talks
 * while nothing that takes keys has focus (a text field, a button, a menu).
 * In the Windows desktop app the person may also choose a hotkey on this
 * computer (off by default): it is armed only while a push-to-talk session is
 * open, so Row-Bot takes that one key from other apps only then, and sees no
 * other keystroke.
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { useOptionalRuntime } from '../../runtime';
import { VOICE_HOTKEYS, type VoiceHotkey } from '../../platform/types';

export const HOTKEY_LABEL: Record<VoiceHotkey, string> = {
  'ctrl+alt+space': 'Ctrl + Alt + Space',
  'ctrl+shift+space': 'Ctrl + Shift + Space',
  f13: 'F13',
};

const KEY = 'row-bot.voice-hotkey';
const listeners = new Set<() => void>();
let hotkey: VoiceHotkey | '' | null = null;

/** The hotkey chosen on this computer, or '' (off, the default). */
export function voiceHotkey(): VoiceHotkey | '' {
  if (hotkey === null) {
    try {
      const saved = globalThis.localStorage?.getItem(KEY) ?? '';
      hotkey = (VOICE_HOTKEYS as readonly string[]).includes(saved)
        ? (saved as VoiceHotkey)
        : '';
    } catch {
      hotkey = '';
    }
  }
  return hotkey;
}

export function chooseVoiceHotkey(next: VoiceHotkey | '') {
  hotkey = next;
  try {
    if (next) globalThis.localStorage?.setItem(KEY, next);
    else globalThis.localStorage?.removeItem(KEY);
  } catch {
    // Storage blocked: the choice lasts while the page does.
  }
  listeners.forEach((listener) => listener());
}

function onVoiceHotkey(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Tests: read the choice from storage again. */
export function resetVoiceHotkey() {
  hotkey = null;
}

export function useVoiceHotkey() {
  return useSyncExternalStore(onVoiceHotkey, voiceHotkey);
}

const TAKES_KEYS = [
  'input',
  'textarea',
  'select',
  'button',
  'a[href]',
  '[contenteditable]:not([contenteditable="false"])',
  '[role="button"]',
  '[role="menu"]',
  '[role="menuitem"]',
  '[role="menuitemcheckbox"]',
  '[role="checkbox"]',
  '[role="switch"]',
  '[role="tab"]',
  '[role="option"]',
  '[role="slider"]',
  '[role="textbox"]',
  '[role="combobox"]',
  '[role="dialog"]',
].join(',');

/** Whether Space belongs to what has focus (typing, pressing a control). */
function takesKeys(target: EventTarget | null) {
  return target instanceof Element && target.closest(TAKES_KEYS) !== null;
}

/**
 * While *active* (push to talk is on and voice is open): hold Space in this
 * window, or the desktop hotkey anywhere, to talk.
 */
export function usePushToTalkKeys(
  active: boolean,
  hold: (held: boolean) => void,
) {
  const platform = useOptionalRuntime()?.platform;
  const key = useVoiceHotkey();
  const latest = useRef(hold);
  latest.current = hold;
  useEffect(() => {
    if (!active) return;
    let held = false;
    // Space holds only while this window has focus; the desktop hotkey holds anywhere.
    let bySpace = false;
    const set = (next: boolean, space = false) => {
      bySpace = next && space;
      if (next === held) return;
      held = next;
      latest.current(next);
    };
    const down = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || takesKeys(event.target)) return;
      if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey)
        return;
      // Space would scroll the page.
      event.preventDefault();
      if (!event.repeat) set(true, true);
    };
    const up = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || !bySpace) return;
      event.preventDefault();
      set(false);
    };
    const away = () => bySpace && set(false);
    const hotkey = (event: Event) =>
      set((event as CustomEvent<{ held?: unknown }>).detail?.held === true);
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', away);
    window.addEventListener('row-bot-voice-hotkey', hotkey);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', away);
      window.removeEventListener('row-bot-voice-hotkey', hotkey);
      set(false);
    };
  }, [active]);
  // The desktop hotkey is armed only while this lasts.
  useEffect(() => {
    if (!active || !key || !platform) return;
    void platform.voiceHotkey(key);
    return () => void platform.voiceHotkey(null);
  }, [active, key, platform]);
}
