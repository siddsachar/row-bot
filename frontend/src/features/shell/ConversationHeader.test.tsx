import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ConversationHeader, { TITLE_LIMIT } from './ConversationHeader';

it('renames once when Enter commits and the input then blurs', async () => {
  let finish: () => void = () => {};
  const onRename = vi.fn(
    () => new Promise<void>((resolve) => (finish = resolve)),
  );
  render(
    <ConversationHeader
      title="New conversation"
      canRename
      onRename={onRename}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Rename conversation' }));
  const input = screen.getByRole('textbox', { name: 'Conversation title' });
  fireEvent.change(input, { target: { value: '[test] Renamed' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  // Disabling the focused input blurs it while the first rename is pending.
  fireEvent.blur(input);
  expect(onRename).toHaveBeenCalledTimes(1);
  expect(onRename).toHaveBeenCalledWith('[test] Renamed');
  finish();
  await waitFor(() =>
    expect(
      screen.queryByRole('textbox', { name: 'Conversation title' }),
    ).not.toBeInTheDocument(),
  );
  expect(onRename).toHaveBeenCalledTimes(1);
});

it('keeps titles within the 120 characters the server saves (B135)', () => {
  const onRename = vi.fn(async () => {});
  render(<ConversationHeader title="Short" canRename onRename={onRename} />);
  fireEvent.click(screen.getByRole('button', { name: 'Rename conversation' }));
  const input = screen.getByRole('textbox', { name: 'Conversation title' });
  expect(TITLE_LIMIT).toBe(120);
  expect(input).toHaveAttribute('maxlength', '120');
  fireEvent.change(input, { target: { value: 'x'.repeat(150) } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onRename).toHaveBeenCalledWith('x'.repeat(120));
});

it('does not rename when the title is unchanged or blank', () => {
  const onRename = vi.fn(async () => {});
  render(<ConversationHeader title="Kept" canRename onRename={onRename} />);
  fireEvent.click(screen.getByRole('button', { name: 'Rename conversation' }));
  const input = screen.getByRole('textbox', { name: 'Conversation title' });
  fireEvent.change(input, { target: { value: '   ' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onRename).not.toHaveBeenCalled();
  expect(screen.getByRole('heading', { name: 'Kept' })).toBeVisible();
});

it('is one phone row: back, title and a ⋯ that holds every action', async () => {
  const onFind = vi.fn();
  const onContext = vi.fn();
  const onSearch = vi.fn();
  render(
    <ConversationHeader
      title="Trip ideas"
      canRename
      onRename={vi.fn(async () => {})}
      model="Local model"
      onFind={onFind}
      onShare={vi.fn()}
      onContext={onContext}
      leading={<button type="button">Toggle navigation</button>}
      menuActions={[{ label: 'Workspace commands', onSelect: onSearch }]}
    />,
  );
  const header = screen.getByRole('banner');
  expect(header).toHaveAttribute('data-layout', 'phone');
  expect(
    screen.getByRole('button', { name: 'Toggle navigation' }),
  ).toBeVisible();
  // Icon actions and the model chip fold away; the title stays.
  expect(screen.queryByRole('button', { name: 'Find' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Context' })).toBeNull();
  expect(screen.queryByText('Local model')).toBeNull();
  const more = screen.getByRole('button', { name: 'Conversation menu' });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  expect(
    screen.getAllByRole('menuitem').map((item) => item.textContent),
  ).toEqual([
    'Workspace commands',
    'Find in conversation',
    'Context',
    'Share or export',
    'Rename conversation',
  ]);
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: 'Context' })),
  );
  expect(onContext).toHaveBeenCalledTimes(1);
});

it('keeps focus in the title field when Rename is chosen from the phone menu', async () => {
  render(
    <ConversationHeader
      title="Trip ideas"
      canRename
      onRename={vi.fn(async () => {})}
      menuActions={[]}
    />,
  );
  const more = screen.getByRole('button', { name: 'Conversation menu' });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  await act(async () =>
    fireEvent.click(
      screen.getByRole('menuitem', { name: 'Rename conversation' }),
    ),
  );
  const input = await screen.findByRole('textbox', {
    name: 'Conversation title',
  });
  await waitFor(() => expect(input).toHaveFocus());
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(input).toHaveFocus();
  expect(
    screen.getByRole('textbox', { name: 'Conversation title' }),
  ).toBeInTheDocument();
});
