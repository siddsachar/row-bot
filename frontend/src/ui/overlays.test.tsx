import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { useEffect, useState } from 'react';
import { ModalTask, OverlayProvider, useOverlay } from './overlays';
import { Button, Input, Skeleton } from './primitives';

function Form({ confirmed }: { confirmed: () => void }) {
  const [draft, setDraft] = useState('Saved example');
  const { open, notify } = useOverlay();
  return (
    <>
      <Input
        aria-label="Draft"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <Button onClick={() => notify('Example notification')}>Notify</Button>
      <Button
        onClick={() =>
          open({
            kind: 'alert',
            title: 'Reset example?',
            description: 'Confirm a local fixture reset.',
            onConfirm: confirmed,
          })
        }
      >
        Reset example
      </Button>
    </>
  );
}
function Fixture({ confirmed }: { confirmed: () => void }) {
  const { open } = useOverlay();
  return (
    <Button
      onClick={() =>
        open({
          title: 'Edit example',
          description: 'An editable local fixture.',
          content: <Form confirmed={confirmed} />,
        })
      }
    >
      Edit
    </Button>
  );
}
it('keeps the originating form mounted while confirmation cancels or confirms', async () => {
  const user = userEvent.setup();
  const confirmed = vi.fn();
  render(
    <OverlayProvider>
      <Fixture confirmed={confirmed} />
    </OverlayProvider>,
  );
  await user.click(screen.getByRole('button', { name: 'Edit' }));
  const input = screen.getByRole('textbox', { name: 'Draft' });
  await user.clear(input);
  await user.type(input, 'Unsaved idea');
  const reset = screen.getByRole('button', {
    name: 'Reset example',
  });
  await user.click(reset);
  expect(screen.getByRole('alertdialog')).toHaveAttribute('aria-modal', 'true');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
  await user.keyboard('{Escape}');
  expect(confirmed).not.toHaveBeenCalled();
  expect(screen.getByRole('textbox')).toBe(input);
  expect(input).toHaveValue('Unsaved idea');
  expect(reset).toHaveFocus();
  await user.click(reset);
  await user.click(screen.getByRole('button', { name: 'Confirm' }));
  expect(confirmed).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('textbox')).toBe(input);
  await user.click(screen.getByRole('button', { name: 'Close' }));
  expect(screen.getByRole('button', { name: 'Edit' })).toHaveFocus();
});

it('uses same-URL Back layers without changing the conversation task', async () => {
  const push = vi
    .spyOn(window.history, 'pushState')
    .mockImplementation(() => undefined);
  const back = vi
    .spyOn(window.history, 'back')
    .mockImplementation(() => undefined);
  const user = userEvent.setup();
  render(
    <OverlayProvider>
      <Fixture confirmed={vi.fn()} />
      <p data-testid="conversation-identity">conversation-a</p>
    </OverlayProvider>,
  );
  await user.click(screen.getByRole('button', { name: 'Edit' }));
  expect(push).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'Reset example' }));
  expect(push).toHaveBeenCalledTimes(2);
  fireEvent.popState(window);
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  expect(screen.getByRole('dialog')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Reset example' })).toHaveFocus();
  fireEvent.popState(window);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByTestId('conversation-identity')).toHaveTextContent(
    'conversation-a',
  );
  expect(back).not.toHaveBeenCalled();
  push.mockRestore();
  back.mockRestore();
});

it('does not unwind a newer route when navigation closes an overlay', async () => {
  const originalUrl = window.location.href;
  const back = vi
    .spyOn(window.history, 'back')
    .mockImplementation(() => undefined);
  const user = userEvent.setup();
  try {
    render(
      <OverlayProvider>
        <Fixture confirmed={vi.fn()} />
      </OverlayProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Edit' }));
    window.history.pushState(
      { route: 'conversation-b' },
      '',
      '/app-v2/conversations/conversation-b',
    );
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(back).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe(
      '/app-v2/conversations/conversation-b',
    );
  } finally {
    window.history.replaceState(null, '', originalUrl);
    back.mockRestore();
  }
});

function DeclarativeDialog() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Manage provider</Button>
      <ModalTask
        open={open}
        title="Manage provider"
        description="Edit one provider without leaving the conversation."
        onOpenChange={setOpen}
      >
        <Input aria-label="Provider name" data-initial-focus />
        <Button onClick={() => setOpen(false)}>Cancel</Button>
      </ModalTask>
    </>
  );
}

it('gives declarative settings tasks the shared focus, Escape and Back contract', async () => {
  const back = vi
    .spyOn(window.history, 'back')
    .mockImplementation(() => undefined);
  const user = userEvent.setup();
  render(<DeclarativeDialog />);
  const opener = screen.getByRole('button', { name: 'Manage provider' });
  await user.click(opener);
  expect(screen.getByTestId('shared-dialog-task')).toHaveAttribute(
    'data-overlay-kind',
    'dialog',
  );
  expect(screen.getByRole('textbox', { name: 'Provider name' })).toHaveFocus();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
  expect(back).toHaveBeenCalledTimes(1);
  // Complete the mocked same-URL history traversal before opening again.
  fireEvent.popState(window);
  await user.click(opener);
  fireEvent.popState(window);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await waitFor(() => expect(opener).toHaveFocus());
  await user.click(opener);
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await waitFor(() => expect(opener).toHaveFocus());
  back.mockRestore();
});

it('shows a stable loading announcement before delaying skeleton visuals', () => {
  vi.useFakeTimers();
  try {
    const { container, unmount } = render(<Skeleton label="Loading fixture" />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading fixture');
    expect(container.querySelector('.skeleton')).toBeNull();
    // Unmounting cancels the owned delay; it cannot update a discarded surface.
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});

it('does not confirm when Enter originates in unrelated text', async () => {
  const confirmed = vi.fn();
  const user = userEvent.setup();
  render(
    <OverlayProvider>
      <Fixture confirmed={confirmed} />
    </OverlayProvider>,
  );
  await user.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
  expect(confirmed).not.toHaveBeenCalled();
});

it('queues notifications during a modal so Escape dismisses the active task', async () => {
  const user = userEvent.setup();
  const { container } = render(
    <OverlayProvider>
      <Fixture confirmed={vi.fn()} />
    </OverlayProvider>,
  );
  const footer = container.querySelector('.notification-footer')!;
  const viewport = container.querySelector('.toast-viewport')!;
  expect(footer).not.toBeVisible();
  const opener = screen.getByRole('button', { name: 'Edit' });
  await user.click(opener);
  const draft = screen.getByRole('textbox', { name: 'Draft' });
  await user.clear(draft);
  await user.type(draft, 'Keep the modal draft');
  await user.click(screen.getByRole('button', { name: 'Notify' }));
  expect(footer).not.toBeVisible();
  expect(screen.queryByText('Example notification')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Reset example' }));
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(draft);
  expect(draft).toHaveValue('Keep the modal draft');
  expect(footer).not.toBeVisible();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
  expect(await screen.findByText('Example notification')).toBeVisible();
  expect(footer).toBeVisible();
  expect(container.querySelector('.toast-viewport')).toBe(viewport);
});

function NotificationFixture({ mounted }: { mounted: () => void }) {
  const [draft, setDraft] = useState('');
  const { notify } = useOverlay();
  useEffect(() => {
    mounted();
  }, [mounted]);
  return (
    <>
      <Input
        aria-label="Conversation draft"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <Button onClick={() => notify('Message queued')}>Queue message</Button>
      <Button
        onClick={() => ['One', 'Two', 'Two', 'Three', 'Four'].forEach(notify)}
      >
        Several notices
      </Button>
    </>
  );
}

it('keeps the child draft and node identity as the normal-flow notification footer opens and empties', async () => {
  const user = userEvent.setup();
  const mounted = vi.fn();
  const { container } = render(
    <OverlayProvider>
      <NotificationFixture mounted={mounted} />
    </OverlayProvider>,
  );
  const input = screen.getByRole('textbox', { name: 'Conversation draft' });
  const content = container.querySelector('.overlay-content')!;
  const footer = container.querySelector('.notification-footer')!;
  const viewport = container.querySelector('.toast-viewport')!;
  expect(content.nextElementSibling).toBe(footer);
  expect(content.contains(input)).toBe(true);
  expect(footer).not.toBeVisible();
  await user.type(input, 'Keep this unsent draft');
  await user.click(screen.getByRole('button', { name: 'Queue message' }));
  expect(footer).toBeVisible();
  expect(screen.getByRole('textbox', { name: 'Conversation draft' })).toBe(
    input,
  );
  expect(input).toHaveValue('Keep this unsent draft');
  expect(screen.getByRole('button', { name: 'Queue message' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }));
  expect(footer).not.toBeVisible();
  expect(container.querySelector('.toast-viewport')).toBe(viewport);
  expect(screen.getByRole('textbox', { name: 'Conversation draft' })).toBe(
    input,
  );
  expect(input).toHaveValue('Keep this unsent draft');
  expect(mounted).toHaveBeenCalledTimes(1);
});

it('deduplicates and bounds notices while preserving F8 focus and explicit dismissal', async () => {
  const user = userEvent.setup();
  const { container } = render(
    <OverlayProvider>
      <NotificationFixture mounted={vi.fn()} />
    </OverlayProvider>,
  );
  await user.click(screen.getByRole('button', { name: 'Several notices' }));
  const viewport =
    container.querySelector<HTMLOListElement>('.toast-viewport')!;
  expect(viewport.querySelectorAll('.toast')).toHaveLength(3);
  expect(within(viewport).queryByText('One')).not.toBeInTheDocument();
  expect(within(viewport).getAllByText('Two')).toHaveLength(1);
  expect(within(viewport).getByText('Three')).toBeVisible();
  expect(within(viewport).getByText('Four')).toBeVisible();
  fireEvent.keyDown(document, { key: 'F8', code: 'F8' });
  expect(viewport).toHaveFocus();
  const dismiss = within(viewport).getAllByRole('button', {
    name: 'Dismiss notification',
  });
  fireEvent.click(dismiss[0]);
  expect(within(viewport).queryByText('Two')).not.toBeInTheDocument();
  expect(viewport.querySelectorAll('.toast')).toHaveLength(2);
});
