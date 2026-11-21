import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { ModalTask, OverlayProvider, useOverlay } from './overlays';
import { Button, Menu } from './primitives';
import { PHONE_SHEETS } from './sheet-swipe';
import { PHONE_LAYOUT } from './use-media';

let phone = true;
beforeEach(() => {
  phone = true;
  vi.mocked(window.matchMedia).mockImplementation(
    (query: string) =>
      ({
        matches: phone && (query === PHONE_SHEETS || query === PHONE_LAYOUT),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
});
afterEach(() => vi.mocked(window.matchMedia).mockReset());

const entry = () =>
  (window.history.state?.__row_bot_overlay?.id as string | undefined) ?? null;

/** Lets our own history step (and the popstate it fires) land. */
async function landed() {
  await act(() => new Promise((resolve) => setTimeout(resolve, 150)));
}

/** The platform's Back. */
async function pressBack() {
  await act(async () => {
    window.history.back();
  });
  await landed();
}

function Sheet({ onClose }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open workflow</Button>
      <ModalTask
        open={open}
        kind="sheet"
        title="Weekly brief"
        description="Its runs"
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) onClose?.();
        }}
      >
        <Button onClick={() => setOpen(false)}>Done</Button>
        <Button
          onClick={() => {
            // A route pushed, then the sheet closes (the workflow sheet's Open chat).
            window.history.pushState({ route: 'chat' }, '', '/app-v2/c/1');
            setOpen(false);
          }}
        >
          Open chat
        </Button>
        <Button
          onClick={() => {
            setOpen(false);
            queueMicrotask(() =>
              window.history.pushState({ route: 'chat' }, '', '/app-v2/c/2'),
            );
          }}
        >
          Close, then open chat
        </Button>
      </ModalTask>
    </>
  );
}

it('closes an open sheet on Back and stays on the page', async () => {
  const user = userEvent.setup();
  const url = window.location.href;
  render(<Sheet />);
  const page = entry();
  await user.click(screen.getByRole('button', { name: 'Open workflow' }));
  expect(entry()).not.toBe(page);
  await pressBack();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(entry()).toBe(page);
  expect(window.location.href).toBe(url);
});

it('gives the entry back once when the sheet closes by its ×, and never steps back twice', async () => {
  const go = vi.spyOn(window.history, 'go');
  const back = vi.spyOn(window.history, 'back');
  try {
    const user = userEvent.setup();
    render(<Sheet />);
    const page = entry();
    await user.click(screen.getByRole('button', { name: 'Open workflow' }));
    await user.click(screen.getByRole('button', { name: 'Close dialog' }));
    await landed();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(go.mock.calls).toEqual([[-1]]);
    expect(back).not.toHaveBeenCalled();
    expect(entry()).toBe(page);
    // A choice inside it releases it the same way.
    await user.click(screen.getByRole('button', { name: 'Open workflow' }));
    await user.click(screen.getByRole('button', { name: 'Done' }));
    await landed();
    expect(go.mock.calls).toEqual([[-1], [-1]]);
    expect(entry()).toBe(page);
  } finally {
    go.mockRestore();
    back.mockRestore();
  }
});

it('gives the entry back when an open sheet unmounts (the window outgrew the phone)', async () => {
  const go = vi.spyOn(window.history, 'go');
  try {
    const user = userEvent.setup();
    const { unmount } = render(<Sheet />);
    const page = entry();
    await user.click(screen.getByRole('button', { name: 'Open workflow' }));
    unmount();
    await landed();
    expect(go.mock.calls).toEqual([[-1]]);
    expect(entry()).toBe(page);
  } finally {
    go.mockRestore();
  }
});

it('lets a navigation from an open sheet take its step: Back from there returns to the page', async () => {
  const go = vi.spyOn(window.history, 'go');
  const url = window.location.href;
  try {
    const user = userEvent.setup();
    render(<Sheet />);
    const page = entry();
    await user.click(screen.getByRole('button', { name: 'Open workflow' }));
    await user.click(screen.getByRole('button', { name: 'Open chat' }));
    await landed();
    expect(go).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe('/app-v2/c/1');
    // No same-URL entry was left between the chat and the page.
    await pressBack();
    expect(window.location.href).toBe(url);
    expect(entry()).toBe(page);
  } finally {
    go.mockRestore();
    window.history.replaceState(null, '', url);
  }
});

it('lets a navigation right after a sheet closes reuse its entry, never stepping back over it', async () => {
  const go = vi.spyOn(window.history, 'go');
  const url = window.location.href;
  try {
    const user = userEvent.setup();
    render(<Sheet />);
    const page = entry();
    await user.click(screen.getByRole('button', { name: 'Open workflow' }));
    await user.click(
      screen.getByRole('button', { name: 'Close, then open chat' }),
    );
    await landed();
    expect(go).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe('/app-v2/c/2');
    await pressBack();
    expect(window.location.href).toBe(url);
    expect(entry()).toBe(page);
  } finally {
    go.mockRestore();
    window.history.replaceState(null, '', url);
  }
});

function Nested() {
  const overlay = useOverlay();
  const always = () =>
    overlay.open({
      kind: 'sheet',
      over: true,
      title: 'Always allow this?',
      description: 'Approve it now and from now on.',
      content: <Button onClick={() => overlay.close()}>Cancel</Button>,
    });
  return (
    <Button
      onClick={() =>
        overlay.open({
          key: 'approval',
          title: 'Review and approve',
          description: 'Commit changes?',
          content: <Button onClick={always}>Always allow…</Button>,
        })
      }
    >
      Review
    </Button>
  );
}

it('closes nested sheets one level per Back on a phone', async () => {
  const user = userEvent.setup();
  render(
    <OverlayProvider>
      <Nested />
    </OverlayProvider>,
  );
  const page = entry();
  await user.click(screen.getByRole('button', { name: 'Review' }));
  await user.click(screen.getByRole('button', { name: 'Always allow…' }));
  expect(screen.getByRole('dialog')).toHaveAccessibleName('Always allow this?');
  await pressBack();
  expect(screen.getByRole('dialog')).toHaveAccessibleName('Review and approve');
  await pressBack();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(entry()).toBe(page);
});

it('returns to the sheet underneath when the one opened over it is cancelled', async () => {
  const user = userEvent.setup();
  render(
    <OverlayProvider>
      <Nested />
    </OverlayProvider>,
  );
  const page = entry();
  await user.click(screen.getByRole('button', { name: 'Review' }));
  await user.click(screen.getByRole('button', { name: 'Always allow…' }));
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  await landed();
  expect(screen.getByRole('dialog')).toHaveAccessibleName('Review and approve');
  await pressBack();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(entry()).toBe(page);
});

it('replaces the shown task on a computer, as before', async () => {
  phone = false;
  const user = userEvent.setup();
  render(
    <OverlayProvider>
      <Nested />
    </OverlayProvider>,
  );
  const page = entry();
  await user.click(screen.getByRole('button', { name: 'Review' }));
  await user.click(screen.getByRole('button', { name: 'Always allow…' }));
  await pressBack();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(entry()).toBe(page);
});

function ChatRow() {
  const overlay = useOverlay();
  return (
    <Menu
      label="Chat actions"
      actions={[
        {
          label: 'Rename',
          onSelect: () =>
            overlay.open({
              title: 'Rename chat',
              description: 'A new name',
              content: <p>Name</p>,
            }),
        },
        { label: 'Pin', onSelect: () => {} },
      ]}
    />
  );
}

it('closes a phone menu sheet on Back, and a dialog chosen from it takes its entry', async () => {
  const go = vi.spyOn(window.history, 'go');
  try {
    const user = userEvent.setup();
    render(
      <OverlayProvider>
        <ChatRow />
      </OverlayProvider>,
    );
    const page = entry();
    await user.click(screen.getByRole('button', { name: 'Chat actions' }));
    await waitFor(() => expect(entry()).not.toBe(page));
    await pressBack();
    expect(screen.queryByRole('menu')).toBeNull();
    expect(entry()).toBe(page);

    await user.click(screen.getByRole('button', { name: 'Chat actions' }));
    await user.click(screen.getByRole('menuitem', { name: 'Rename' }));
    await landed();
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Rename chat');
    expect(go).not.toHaveBeenCalled();
    await pressBack();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(entry()).toBe(page);
  } finally {
    go.mockRestore();
  }
});

it('holds no entry for a menu on a computer, where it is a popover', async () => {
  phone = false;
  const user = userEvent.setup();
  render(
    <OverlayProvider>
      <ChatRow />
    </OverlayProvider>,
  );
  const page = entry();
  await user.click(screen.getByRole('button', { name: 'Chat actions' }));
  expect(screen.getByRole('menu')).toBeVisible();
  await landed();
  expect(entry()).toBe(page);
});

function Refusing() {
  return (
    <ModalTask
      open
      dismissible={false}
      title="Installing"
      description="Wait"
      onOpenChange={() => {}}
    >
      <p>Working</p>
    </ModalTask>
  );
}

it('keeps a task that cannot be dismissed open on Back, with a fresh entry', async () => {
  render(<Refusing />);
  await landed();
  const held = entry();
  expect(held).not.toBeNull();
  await pressBack();
  expect(screen.getByRole('dialog')).toBeVisible();
  expect(entry()).not.toBeNull();
});
