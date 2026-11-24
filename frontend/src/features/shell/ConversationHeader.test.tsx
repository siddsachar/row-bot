import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
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

it('is one phone row: back, title, New chat and a ⋯ with Find, Details, Export and Rename first', async () => {
  const onContext = vi.fn();
  const onNewChat = vi.fn();
  render(
    <ConversationHeader
      title="Trip ideas"
      canRename
      onRename={vi.fn(async () => {})}
      model="Local model"
      onFind={vi.fn()}
      onShare={vi.fn()}
      onContext={onContext}
      onNewChat={onNewChat}
      leading={<button type="button">Back</button>}
      menuActions={[{ label: 'Incognito', onSelect: vi.fn() }]}
      trailingMenuActions={[{ label: 'Teach Row-Bot', onSelect: vi.fn() }]}
    />,
  );
  const header = screen.getByRole('banner');
  expect(header).toHaveAttribute('data-layout', 'phone');
  expect(screen.getByRole('button', { name: 'Back' })).toBeVisible();
  // Find, Details and Export fold into ⋯; New chat stays one tap away.
  expect(screen.queryByRole('button', { name: 'Find' })).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Conversation details' }),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  expect(onNewChat).toHaveBeenCalledOnce();
  const more = screen.getByRole('button', { name: 'Conversation menu' });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  expect(
    screen.getAllByRole('menuitem').map((item) => item.textContent),
  ).toEqual([
    'Find in conversation',
    'Conversation details',
    'Export conversation',
    'Rename conversation',
    expect.stringContaining('Incognito'),
    'Teach Row-Bot',
  ]);
  await act(async () =>
    fireEvent.click(
      screen.getByRole('menuitem', { name: 'Conversation details' }),
    ),
  );
  expect(onContext).toHaveBeenCalledTimes(1);
});

it('shows the model under a phone title as the button that opens the model sheet', () => {
  const onModel = vi.fn();
  const onRename = vi.fn(async () => {});
  const { rerender } = render(
    <ConversationHeader
      title="Trip ideas"
      canRename
      onRename={onRename}
      model="qwen3:8b"
      onModel={onModel}
      menuActions={[]}
    />,
  );
  const model = screen.getByRole('button', { name: 'Model: qwen3:8b' });
  expect(model).toHaveAttribute('aria-haspopup', 'dialog');
  fireEvent.click(model);
  expect(onModel).toHaveBeenCalledOnce();
  // Rename lives in ⋯ on a phone: a tap on the title never starts it.
  fireEvent.click(screen.getByRole('heading', { name: 'Trip ideas' }));
  expect(
    screen.queryByRole('textbox', { name: 'Conversation title' }),
  ).toBeNull();

  // While a reply runs the model can't change.
  rerender(
    <ConversationHeader
      title="Trip ideas"
      canRename
      onRename={onRename}
      model=""
      onModel={onModel}
      modelDisabled
      menuActions={[]}
    />,
  );
  const none = screen.getByRole('button', { name: 'Choose a model' });
  expect(none).toBeDisabled();
  fireEvent.click(none);
  expect(onModel).toHaveBeenCalledOnce();
});

it('puts a phone chat’s Working on in a row under the header', () => {
  render(
    <ConversationHeader
      title="Trip ideas"
      canRename={false}
      onRename={vi.fn(async () => {})}
      menuActions={[]}
      workingOn={<button type="button">Working on · Deck</button>}
    />,
  );
  const work = screen.getByRole('button', { name: 'Working on · Deck' });
  expect(
    within(
      screen.getByRole('group', { name: 'Conversation actions' }),
    ).queryByRole('button', { name: 'Working on · Deck' }),
  ).toBeNull();
  expect(work.closest('.conversation-working-on')).not.toBeNull();
});

it('keeps the desktop header: model chip, icon actions, no New chat or model button', () => {
  render(
    <ConversationHeader
      title="Trip ideas"
      canRename
      onRename={vi.fn(async () => {})}
      model="qwen3:8b"
      onModel={vi.fn()}
      onNewChat={vi.fn()}
      onFind={vi.fn()}
    />,
  );
  expect(screen.getByText('qwen3:8b')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Find' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'New chat' })).toBeNull();
  expect(screen.queryByRole('button', { name: /^Model:/ })).toBeNull();
  // A click on the title still renames there.
  fireEvent.click(screen.getByRole('heading', { name: 'Trip ideas' }));
  expect(
    screen.getByRole('textbox', { name: 'Conversation title' }),
  ).toBeVisible();
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
