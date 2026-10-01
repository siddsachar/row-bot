import * as Dropdown from '@radix-ui/react-dropdown-menu';
import { Fragment, useEffect, useRef, useState } from 'react';
import type { ClientPlatform } from '../../platform';
import { useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Kbd } from '../../ui/primitives';

/**
 * The desktop window's right-click menu (parity row 14). The embedded web
 * view has no menu of its own, so Row-Bot offers Cut, Copy, Paste and Select
 * All, following what the clicked place allows: nothing is cut or pasted into
 * read-only text, and a password field is never copied. Browsers keep their
 * own menu.
 */

type TextField = HTMLInputElement | HTMLTextAreaElement;
export type EditCommand = 'cut' | 'copy' | 'paste' | 'selectAll';
export type EditTarget = {
  element: HTMLElement | null;
  editable: boolean;
  secret: boolean;
  text: string;
  start: number | null;
  end: number | null;
  range: Range | null;
};
export type EditItem = {
  command: EditCommand;
  label: string;
  shortcut: string;
  enabled: boolean;
  separatorBefore?: boolean;
};

const TEXT_TYPES = new Set([
  'text',
  'search',
  'url',
  'email',
  'tel',
  'password',
  'number',
]);
const EDITABLE =
  'input, textarea, [contenteditable]:not([contenteditable="false"])';

function textField(element: Element | null): element is TextField {
  return (
    element instanceof HTMLTextAreaElement ||
    (element instanceof HTMLInputElement && TEXT_TYPES.has(element.type))
  );
}

/** What the right-click landed on, and the text selected there. */
export function readEditTarget(
  node: EventTarget | null,
  doc: Document = document,
): EditTarget {
  const origin =
    node instanceof Element
      ? node
      : node instanceof Node
        ? node.parentElement
        : null;
  const candidate = origin?.closest(EDITABLE) ?? null;
  if (textField(candidate)) {
    let start: number | null = null;
    let end: number | null = null;
    try {
      start = candidate.selectionStart;
      end = candidate.selectionEnd;
    } catch {
      // email and number fields do not expose a selection.
    }
    return {
      element: candidate,
      editable: !candidate.disabled && !candidate.readOnly,
      secret:
        candidate instanceof HTMLInputElement && candidate.type === 'password',
      text:
        start !== null && end !== null ? candidate.value.slice(start, end) : '',
      start,
      end,
      range: null,
    };
  }
  const selection = doc.getSelection();
  const range =
    selection && selection.rangeCount > 0
      ? selection.getRangeAt(0).cloneRange()
      : null;
  const editable = candidate instanceof HTMLElement;
  return {
    element: editable ? candidate : null,
    editable,
    secret: false,
    text: selection?.toString() ?? '',
    start: null,
    end: null,
    range,
  };
}

/** The menu for a target: Cut and Paste only where text can change. */
export function editItems(target: EditTarget, canPaste: boolean): EditItem[] {
  const selected = target.text.length > 0 && !target.secret;
  const items: EditItem[] = [];
  if (target.editable)
    items.push({
      command: 'cut',
      label: 'Cut',
      shortcut: 'Mod+X',
      enabled: selected,
    });
  items.push({
    command: 'copy',
    label: 'Copy',
    shortcut: 'Mod+C',
    enabled: selected,
  });
  if (target.editable)
    items.push({
      command: 'paste',
      label: 'Paste',
      shortcut: 'Mod+V',
      enabled: canPaste,
    });
  items.push({
    command: 'selectAll',
    label: 'Select All',
    shortcut: 'Mod+A',
    enabled: true,
    separatorBefore: true,
  });
  return items;
}

function exec(command: string, value?: string): boolean {
  try {
    return document.execCommand(command, false, value);
  } catch {
    return false;
  }
}

/** Put focus and the selection back where the right-click found them. */
function restore(target: EditTarget) {
  const element = target.element;
  if (element?.isConnected) element.focus({ preventScroll: true });
  if (textField(element) && target.start !== null && target.end !== null) {
    try {
      element.setSelectionRange(target.start, target.end);
    } catch {
      // The field no longer accepts a selection.
    }
  } else if (target.range) {
    const selection = document.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(target.range);
  }
}

/** Replace the selected text so the page sees an ordinary edit. */
function replaceSelection(target: EditTarget, text: string) {
  if (exec(text ? 'insertText' : 'delete', text || undefined)) return;
  const element = target.element;
  if (!textField(element)) return;
  const start = element.selectionStart ?? element.value.length;
  const end = element.selectionEnd ?? start;
  element.setRangeText(text, start, end, 'end');
  element.dispatchEvent(new Event('input', { bubbles: true }));
}

export async function runEditCommand(
  command: EditCommand,
  target: EditTarget,
  platform: Pick<ClientPlatform, 'readClipboard' | 'writeClipboard'>,
): Promise<'done' | 'paste_unavailable'> {
  if (command === 'paste') {
    const pasted = await platform.readClipboard();
    if (pasted.status !== 'ok') return 'paste_unavailable';
    restore(target);
    replaceSelection(target, pasted.value);
    return 'done';
  }
  restore(target);
  if (command === 'selectAll') {
    if (textField(target.element)) target.element.select();
    else exec('selectAll');
    return 'done';
  }
  if (exec(command)) return 'done';
  // The web view refused the command: copy through the desktop app.
  await platform.writeClipboard(target.text);
  if (command === 'cut') replaceSelection(target, '');
  return 'done';
}

export function EditMenu() {
  const { platform } = useRuntime();
  const { notify } = useOverlay();
  const [host, setHost] = useState<{ paste: boolean; mac: boolean } | null>(
    null,
  );
  const [menu, setMenu] = useState<{
    x: number;
    y: number;
    target: EditTarget;
  } | null>(null);
  const pending = useRef<EditCommand | null>(null);

  useEffect(() => {
    let current = true;
    void platform
      .discover()
      .then((result) => {
        if (
          current &&
          result.status === 'ok' &&
          result.value.kind === 'pywebview'
        )
          setHost({
            paste: result.value.capabilities.includes('clipboard_read'),
            mac: result.value.platform === 'macos',
          });
      })
      .catch(() => undefined);
    return () => void (current = false);
  }, [platform]);

  useEffect(() => {
    if (!host) return;
    const open = (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      event.preventDefault();
      const target = readEditTarget(event.target);
      let { clientX: x, clientY: y } = event;
      if (!x && !y && event.target instanceof Element) {
        // Opened from the keyboard: place it at the focused element.
        const box = event.target.getBoundingClientRect();
        x = box.left + 8;
        y = box.top + Math.min(box.height, 24);
      }
      setMenu({ x, y, target });
    };
    document.addEventListener('contextmenu', open);
    return () => document.removeEventListener('contextmenu', open);
  }, [host]);

  if (!host || !menu) return null;
  const items = editItems(menu.target, host.paste);
  const platformName = host.mac ? 'mac' : 'other';
  return (
    <Dropdown.Root
      open
      modal={false}
      onOpenChange={(open) => {
        if (!open) setMenu(null);
      }}
    >
      <Dropdown.Trigger asChild>
        <span
          aria-hidden
          className="edit-menu-anchor"
          style={{ left: menu.x, top: menu.y }}
        />
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content
          className="menu surface-effect edit-menu"
          aria-label="Edit"
          // The anchor has no name of its own.
          aria-labelledby={undefined}
          align="start"
          side="bottom"
          sideOffset={2}
          collisionPadding={8}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const command = pending.current;
            pending.current = null;
            const target = menu.target;
            if (!command) {
              restore(target);
              return;
            }
            void runEditCommand(command, target, platform).then((result) => {
              if (result === 'paste_unavailable')
                notify(
                  `Row-Bot couldn't read the clipboard. Press ${host.mac ? '⌘V' : 'Ctrl+V'} instead.`,
                  'warning',
                );
            });
          }}
        >
          {items.map((item) => (
            <Fragment key={item.command}>
              {item.separatorBefore && (
                <Dropdown.Separator className="menu-separator" />
              )}
              <Dropdown.Item
                className="menu-item menu-item-rich"
                disabled={!item.enabled}
                onSelect={() => {
                  pending.current = item.command;
                }}
              >
                <span className="menu-item-label">{item.label}</span>
                <span className="menu-item-kbd" aria-hidden>
                  <Kbd keys={item.shortcut} platform={platformName} />
                </span>
              </Dropdown.Item>
            </Fragment>
          ))}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}
