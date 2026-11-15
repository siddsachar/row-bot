import { afterEach, expect, it } from 'vitest';
import { clientError } from './errors';
import { currentPlace, placeWords, setPlace } from './place';

// U7: "this computer" only when the browser is on the computer Row-Bot runs
// on; otherwise "your Row-Bot server", named after its host when one runs it.

afterEach(() => setPlace(null));

it('words each place as UX.md’s table says', () => {
  expect(placeWords({ kind: 'this_computer' })).toEqual({
    kind: 'this_computer',
    label: 'This computer',
    name: 'this computer',
    noun: 'this computer',
    again: 'this computer',
    on: 'on this computer',
    onAgain: 'on this computer',
  });
  expect(placeWords({ kind: 'hosted', host_name: 'PikaPods' })).toEqual({
    kind: 'hosted',
    label: 'Your Row-Bot server',
    name: 'your Row-Bot server',
    noun: 'your Row-Bot server at PikaPods',
    again: 'your server',
    on: 'on your Row-Bot server at PikaPods',
    onAgain: 'on your server',
  });
  expect(placeWords({ kind: 'server' })).toEqual({
    kind: 'server',
    label: 'Your Row-Bot server',
    name: 'your Row-Bot server',
    noun: 'the computer Row-Bot runs on',
    again: 'the computer Row-Bot runs on',
    on: 'on the computer Row-Bot runs on',
    onAgain: 'on the computer Row-Bot runs on',
  });
});

it('follows the latest handshake, and reads as the desktop before one', () => {
  expect(currentPlace().on).toBe('on this computer');
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  expect(currentPlace().on).toBe('on your Row-Bot server at PikaPods');
});

it('words an error about the keychain for where the sign-in is kept', () => {
  const words = () =>
    clientError({ code: 'subscription_storage_unavailable' }).message;
  expect(words()).toBe(
    "Row-Bot can't save the sign-in: this computer's keychain is locked or unavailable. Unlock it (on a Mac, in Keychain Access), then try again.",
  );
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  expect(words()).toBe(
    "Row-Bot can't save the sign-in: secure storage on your Row-Bot server at PikaPods is unavailable. Try again.",
  );
  setPlace({ kind: 'server' });
  expect(words()).toBe(
    "Row-Bot can't save the sign-in: secure storage on the computer Row-Bot runs on is locked or unavailable. Unlock it there (on a Mac, in Keychain Access), then try again.",
  );
});

it('words other errors about where Row-Bot runs the same way', () => {
  const words = (code: string) => clientError({ code }).message;
  expect(words('sensevoice_unavailable')).toBe(
    "SenseVoice isn't ready on this computer. Install it in Voice settings, or choose Whisper.",
  );
  setPlace({ kind: 'server' });
  expect(words('sandbox_runtime_unavailable')).toBe(
    "Docker isn't running on the computer Row-Bot runs on. Start Docker Desktop (or Docker), then try again.",
  );
});
