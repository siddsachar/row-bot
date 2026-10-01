import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import {
  EditMenu,
  editItems,
  readEditTarget,
  runEditCommand,
  type EditTarget,
} from './EditMenu';

const nativeWindow = {
  status: 'ok' as const,
  value: {
    kind: 'pywebview' as const,
    platform: 'windows' as const,
    capabilities: ['clipboard_read', 'clipboard_write'],
    instanceId: 'instance',
    windowId: 'window',
    epoch: 1,
  },
};

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
  document.getSelection()?.removeAllRanges();
});

function field(value: string, start: number, end: number, readOnly = false) {
  const element = document.createElement('textarea');
  element.value = value;
  element.readOnly = readOnly;
  document.body.append(element);
  element.setSelectionRange(start, end);
  return element;
}

function labels(target: EditTarget, canPaste = true) {
  return editItems(target, canPaste).map(
    (item) => `${item.label}${item.enabled ? '' : ' (off)'}`,
  );
}

it('offers Cut and Paste only where text can change (row 14)', () => {
  expect(labels(readEditTarget(field('Hello there', 0, 5)))).toEqual([
    'Cut',
    'Copy',
    'Paste',
    'Select All',
  ]);
  expect(labels(readEditTarget(field('Hello', 0, 0)), false)).toEqual([
    'Cut (off)',
    'Copy (off)',
    'Paste (off)',
    'Select All',
  ]);
  const readOnly = readEditTarget(field('Read-only text', 0, 4, true));
  expect(readOnly.editable).toBe(false);
  expect(labels(readOnly)).toEqual(['Copy', 'Select All']);

  const message = document.createElement('p');
  message.textContent = 'A reply from Row-Bot';
  document.body.append(message);
  const range = document.createRange();
  range.selectNodeContents(message);
  document.getSelection()?.addRange(range);
  const onMessage = readEditTarget(message.firstChild);
  expect(onMessage).toMatchObject({
    element: null,
    editable: false,
    text: 'A reply from Row-Bot',
  });
  expect(labels(onMessage)).toEqual(['Copy', 'Select All']);
});

it('never copies or cuts a password', () => {
  const password = document.createElement('input');
  password.type = 'password';
  password.value = 'secret-value';
  document.body.append(password);
  password.setSelectionRange(0, 6);
  const target = readEditTarget(password);
  expect(target.secret).toBe(true);
  expect(labels(target)).toEqual([
    'Cut (off)',
    'Copy (off)',
    'Paste',
    'Select All',
  ]);
});

it('pastes into the selection as an ordinary edit and keeps the rest', async () => {
  const element = field('Hello world', 6, 11);
  const input = vi.fn();
  element.addEventListener('input', input);
  const platform = createFakePlatform({
    readClipboard: { status: 'ok', value: 'Row-Bot ✓' },
  });

  await expect(
    runEditCommand('paste', readEditTarget(element), platform),
  ).resolves.toBe('done');

  expect(element.value).toBe('Hello Row-Bot ✓');
  expect(element.selectionStart).toBe('Hello Row-Bot ✓'.length);
  expect(input).toHaveBeenCalledTimes(1);
});

it('leaves the text alone when the clipboard cannot be read', async () => {
  const element = field('Hello', 0, 5);
  await expect(
    runEditCommand('paste', readEditTarget(element), createFakePlatform()),
  ).resolves.toBe('paste_unavailable');
  expect(element.value).toBe('Hello');
});

it('copies and cuts through the desktop app when the web view refuses', async () => {
  const writeClipboard = vi.fn(async () => ({
    status: 'ok' as const,
    value: null,
  }));
  const platform = { ...createFakePlatform(), writeClipboard };
  const element = field('Keep this part', 5, 9);

  await runEditCommand('copy', readEditTarget(element), platform);
  expect(writeClipboard).toHaveBeenLastCalledWith('this');
  expect(element.value).toBe('Keep this part');

  await runEditCommand('cut', readEditTarget(element), platform);
  expect(writeClipboard).toHaveBeenLastCalledWith('this');
  expect(element.value).toBe('Keep  part');
});

it('selects all of a field', async () => {
  const element = field('Everything', 2, 2);
  await runEditCommand(
    'selectAll',
    readEditTarget(element),
    createFakePlatform(),
  );
  expect([element.selectionStart, element.selectionEnd]).toEqual([0, 10]);
});

function Composer() {
  const [value, setValue] = useState('Draft ');
  return (
    <textarea
      aria-label="Message"
      value={value}
      onChange={(event) => setValue(event.target.value)}
    />
  );
}

async function renderMenu(platform = createFakePlatform()) {
  render(
    <RuntimeContext.Provider value={{ controller: {} as never, platform }}>
      <OverlayProvider>
        <Composer />
        <p>A reply from Row-Bot</p>
        <EditMenu />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
  await act(async () => undefined);
}

it('leaves the browser menu alone outside the desktop window', async () => {
  await renderMenu();
  const event = new MouseEvent('contextmenu', {
    bubbles: true,
    cancelable: true,
  });
  screen.getByLabelText('Message').dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
  expect(screen.queryByRole('menu')).toBeNull();
});

it('pastes into the composer from the desktop right-click menu', async () => {
  const user = userEvent.setup();
  await renderMenu(
    createFakePlatform({
      discover: nativeWindow,
      readClipboard: { status: 'ok', value: 'from the clipboard' },
    }),
  );
  const composer = screen.getByLabelText<HTMLTextAreaElement>('Message');
  composer.focus();
  composer.setSelectionRange(6, 6);

  fireEvent.contextMenu(composer, { clientX: 40, clientY: 40 });

  const menu = await screen.findByRole('menu', { name: 'Edit' });
  expect(
    [...menu.querySelectorAll('[role="menuitem"]')].map((item) =>
      item.textContent?.replace(/Ctrl.*$/, ''),
    ),
  ).toEqual(['Cut', 'Copy', 'Paste', 'Select All']);
  await user.click(screen.getByRole('menuitem', { name: /Paste/ }));

  expect(screen.queryByRole('menu')).toBeNull();
  await vi.waitFor(() =>
    expect(composer).toHaveValue('Draft from the clipboard'),
  );
  expect(composer).toHaveFocus();
});

it('offers only Copy and Select All on a message', async () => {
  await renderMenu(createFakePlatform({ discover: nativeWindow }));
  fireEvent.contextMenu(screen.getByText('A reply from Row-Bot'), {
    clientX: 20,
    clientY: 20,
  });
  const menu = await screen.findByRole('menu', { name: 'Edit' });
  expect(menu).not.toHaveTextContent(/Cut|Paste/);
  expect(screen.getByRole('menuitem', { name: /Copy/ })).toHaveAttribute(
    'data-disabled',
  );
  expect(
    screen.getByRole('menuitem', { name: /Select All/ }),
  ).not.toHaveAttribute('data-disabled');
});
