import { afterEach, expect, it, vi } from 'vitest';
import { copyTextLater } from './clipboard';

// U3: one click starts a sign-in and copies its code once the server has it.
// Safari copies only from inside the click, which the clipboard API allows by
// taking the text as a promise; elsewhere the text is copied when it arrives.

const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
afterEach(() => {
  if (original) Object.defineProperty(navigator, 'clipboard', original);
  else delete (navigator as { clipboard?: unknown }).clipboard;
  vi.unstubAllGlobals();
});

function later() {
  let resolve: (text: string) => void = () => undefined;
  let reject: (cause: Error) => void = () => undefined;
  const text = new Promise<string>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { text, resolve, reject };
}

it('hands the browser the promised text from inside the click', async () => {
  const written: Blob[] = [];
  vi.stubGlobal(
    'ClipboardItem',
    class {
      constructor(readonly items: Record<string, Promise<Blob>>) {}
    },
  );
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: {
      write: vi.fn(
        async ([item]: { items: Record<string, Promise<Blob>> }[]) => {
          written.push(await item.items['text/plain']);
        },
      ),
      writeText: vi.fn(),
    },
  });
  const code = later();
  const copying = copyTextLater(code.text);
  // The write began before the code was known.
  expect(navigator.clipboard.write).toHaveBeenCalledOnce();
  code.resolve('ABCD-EFGH');
  expect(await copying).toBe(true);
  expect(await written[0].text()).toBe('ABCD-EFGH');
  expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
});

it('copies once the text arrives where the browser takes only text', async () => {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn(async () => undefined) },
  });
  const code = later();
  const copying = copyTextLater(code.text);
  code.resolve('ABCD-EFGH');
  expect(await copying).toBe(true);
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith('ABCD-EFGH');
});

it('copies nothing when the text never comes', async () => {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn(async () => undefined) },
  });
  const code = later();
  const copying = copyTextLater(code.text);
  code.reject(new Error('the sign-in did not start'));
  expect(await copying).toBe(false);
  expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
});
