import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { Button, Hint, Menu } from './primitives';

it('keeps a focused full-title hint through ancestor scrolling and dismisses on Escape or blur', async () => {
  const user = userEvent.setup();
  render(
    <div data-testid="scrolling-drawer">
      <Hint label="The complete conversation title">
        <Button>Truncated title</Button>
      </Hint>
      <Button>Next control</Button>
    </div>,
  );
  await user.tab();
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    'The complete conversation title',
  );
  fireEvent.scroll(screen.getByTestId('scrolling-drawer'));
  expect(screen.getByRole('button', { name: 'Truncated title' })).toHaveFocus();
  expect(screen.getByRole('tooltip')).toBeVisible();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  await user.tab();
  await user.tab({ shift: true });
  expect(await screen.findByRole('tooltip')).toBeVisible();
  await user.tab();
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
});

it('dismisses a focused hint when its action is activated without swallowing the action', async () => {
  const user = userEvent.setup();
  const activate = vi.fn();
  render(
    <Hint label="The complete conversation title">
      <Button onClick={activate}>Open conversation</Button>
    </Hint>,
  );
  await user.tab();
  expect(await screen.findByRole('tooltip')).toBeVisible();
  await user.keyboard('{Enter}');
  expect(activate).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
});

it('forwards compact menu styling and disabled state to its actual trigger', () => {
  render(
    <Menu
      label="Thinking"
      actions={[]}
      disabled
      variant="ghost"
      className="composer-control"
    />,
  );
  const trigger = screen.getByRole('button', { name: 'Thinking' });
  expect(trigger).toBeDisabled();
  expect(trigger).toHaveClass('ghost', 'composer-control');
});

it('reveals the full current value on keyboard focus and marks the selected menu item', async () => {
  const user = userEvent.setup();
  const select = vi.fn();
  render(
    <Menu
      label="Model"
      hint="Model: Complete model name"
      actions={[
        { label: 'Complete model name', selected: true, onSelect: select },
        { label: 'Another model', onSelect: select },
      ]}
    >
      Short label
    </Menu>,
  );
  await user.tab();
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    'Model: Complete model name',
  );
  expect(
    screen.getByRole('button', { name: 'Model' }),
  ).toHaveAccessibleDescription('Model: Complete model name');
  await user.keyboard('{Enter}');
  expect(
    await screen.findByRole('menuitem', { name: 'Complete model name' }),
  ).toHaveAttribute('aria-current', 'true');
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  await user.keyboard('{Escape}');
  expect(screen.getByRole('button', { name: 'Model' })).toHaveFocus();
});
