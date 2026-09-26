import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ConversationHeader from './ConversationHeader';

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
