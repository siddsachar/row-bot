import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
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
  expect(
    screen.queryByRole('button', { name: 'Conversation details' }),
  ).toBeNull();
  expect(screen.queryByText('Local model')).toBeNull();
  const more = screen.getByRole('button', { name: 'Conversation menu' });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  expect(
    screen.getAllByRole('menuitem').map((item) => item.textContent),
  ).toEqual([
    'Workspace commands',
    'Find in conversation',
    'Conversation details',
    'Export conversation',
    'Rename conversation',
  ]);
  await act(async () =>
    fireEvent.click(
      screen.getByRole('menuitem', { name: 'Conversation details' }),
    ),
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

it('names the details toggle "Conversation details" (B221)', () => {
  const onContext = vi.fn();
  render(
    <ConversationHeader
      title="Trip ideas"
      canRename={false}
      onRename={vi.fn(async () => {})}
      onContext={onContext}
      contextPressed
    />,
  );
  const toggle = screen.getByRole('button', { name: 'Conversation details' });
  expect(toggle).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(toggle);
  expect(onContext).toHaveBeenCalledOnce();
});

function Opened() {
  return <p>Opened {useParams().id}</p>;
}

it.each([
  ['desktop', undefined],
  ['phone', []],
])(
  'links an agent’s conversation back to its parent by title (%s, B242)',
  async (_layout, menuActions) => {
    render(
      <MemoryRouter initialEntries={['/conversations/child-a']}>
        <Routes>
          <Route
            path="/conversations/child-a"
            element={
              <ConversationHeader
                title="Competitor pricing scan"
                canRename={false}
                onRename={vi.fn(async () => {})}
                breadcrumb={{
                  to: '/conversations/parent-a',
                  title: 'Q4 launch plan',
                }}
                menuActions={menuActions}
              />
            }
          />
          <Route path="/conversations/:id" element={<Opened />} />
        </Routes>
      </MemoryRouter>,
    );
    const back = screen.getByRole('link', { name: 'Back to Q4 launch plan' });
    expect(back).toHaveTextContent('Q4 launch plan');
    expect(back).toHaveAttribute('href', '/conversations/parent-a');
    back.focus();
    expect(back).toHaveFocus();
    fireEvent.click(back);
    expect(await screen.findByText('Opened parent-a')).toBeVisible();
  },
);

it('names the conversation actions button for what it opens: Export, no share', () => {
  const onShare = vi.fn();
  render(
    <ConversationHeader
      title="Trip ideas"
      canRename={false}
      onRename={vi.fn(async () => {})}
      onShare={onShare}
    />,
  );
  expect(screen.queryByRole('button', { name: /share/i })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Export' }));
  expect(onShare).toHaveBeenCalledTimes(1);
});
