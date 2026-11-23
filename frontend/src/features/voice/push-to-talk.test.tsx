import { cleanup, fireEvent, render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RuntimeContext } from '../../runtime';
import { createFakePlatform } from '../../platform/fake';
import type { ClientPlatform } from '../../platform';
import {
  chooseVoiceHotkey,
  resetVoiceHotkey,
  usePushToTalkKeys,
  voiceHotkey,
} from './push-to-talk';

function Keys(props: { active: boolean; hold(held: boolean): void }) {
  usePushToTalkKeys(props.active, props.hold);
  return (
    <div>
      <p>Transcript</p>
      <textarea aria-label="Message" />
      <button type="button">Mute</button>
    </div>
  );
}

function withPlatform(platform: ClientPlatform, children: ReactNode) {
  return (
    <RuntimeContext.Provider
      value={{ controller: {} as never, platform } as never}
    >
      {children}
    </RuntimeContext.Provider>
  );
}

beforeEach(() => {
  localStorage.clear();
  resetVoiceHotkey();
});
afterEach(cleanup);

const space = (target: Element, type: 'keyDown' | 'keyUp', repeat = false) =>
  fireEvent[type](target, { code: 'Space', key: ' ', repeat });

it('holds to talk while Space is held, when nothing that takes keys has focus', () => {
  const hold = vi.fn();
  const view = render(<Keys active hold={hold} />);
  const text = view.getByText('Transcript');
  space(text, 'keyDown');
  space(text, 'keyDown', true);
  expect(hold.mock.calls).toEqual([[true]]);
  space(text, 'keyUp');
  expect(hold.mock.calls).toEqual([[true], [false]]);
  // Space in the message box types a space; on a button it presses it.
  space(view.getByRole('textbox', { name: 'Message' }), 'keyDown');
  space(view.getByRole('button', { name: 'Mute' }), 'keyDown');
  expect(hold).toHaveBeenCalledTimes(2);
});

it('does nothing without push to talk, and lets go when the window loses focus', () => {
  const hold = vi.fn();
  const view = render(<Keys active={false} hold={hold} />);
  space(document.body, 'keyDown');
  expect(hold).not.toHaveBeenCalled();
  view.rerender(<Keys active hold={hold} />);
  space(document.body, 'keyDown');
  fireEvent.blur(window);
  expect(hold.mock.calls).toEqual([[true], [false]]);
});

it('the desktop hotkey holds while it is pressed, wherever focus is', () => {
  const hold = vi.fn();
  render(<Keys active hold={hold} />);
  const press = (held: boolean) =>
    window.dispatchEvent(
      new CustomEvent('row-bot-voice-hotkey', { detail: { held } }),
    );
  press(true);
  // Another app has focus: the window losing it doesn't end the hold.
  fireEvent.blur(window);
  expect(hold.mock.calls).toEqual([[true]]);
  press(false);
  expect(hold.mock.calls).toEqual([[true], [false]]);
});

it('arms the chosen desktop hotkey only while push-to-talk voice is open', () => {
  const platform = createFakePlatform();
  const arm = vi.spyOn(platform, 'voiceHotkey');
  const view = render(withPlatform(platform, <Keys active hold={vi.fn()} />));
  // Off by default: nothing is armed.
  expect(arm).not.toHaveBeenCalled();
  chooseVoiceHotkey('ctrl+alt+space');
  view.rerender(withPlatform(platform, <Keys active hold={vi.fn()} />));
  expect(arm.mock.calls).toEqual([['ctrl+alt+space']]);
  view.rerender(withPlatform(platform, <Keys active={false} hold={vi.fn()} />));
  expect(arm.mock.calls).toEqual([['ctrl+alt+space'], [null]]);
  view.rerender(withPlatform(platform, <Keys active hold={vi.fn()} />));
  view.unmount();
  expect(arm.mock.calls.at(-1)).toEqual([null]);
});

it('remembers the hotkey on this computer, and only a key it can listen for', () => {
  chooseVoiceHotkey('f13');
  resetVoiceHotkey();
  expect(voiceHotkey()).toBe('f13');
  localStorage.setItem('row-bot.voice-hotkey', 'ctrl+alt+delete');
  resetVoiceHotkey();
  expect(voiceHotkey()).toBe('');
  chooseVoiceHotkey('');
  expect(localStorage.getItem('row-bot.voice-hotkey')).toBeNull();
});
