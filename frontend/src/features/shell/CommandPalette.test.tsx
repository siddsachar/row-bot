import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import CommandPalette, {
  plainSnippet,
  type PaletteCommand,
} from './CommandPalette';

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.useRealTimers();
});

async function setup(commands: PaletteCommand[] = []) {
  const transport = new FixtureTransport({ conversationCount: 8 });
  transport.conversations[2].title = 'Landing page review';
  transport.conversations[2].category = 'designer';
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  const handlers = {
    onOpenConversation: vi.fn(),
    onOpenSearchHit: vi.fn(),
    onOpenSetting: vi.fn(),
    onStartAgent: vi.fn(),
    onOpenWorkflow: vi.fn(),
  };
  const loadWorkflows = vi.fn(async () => [
    { id: 'task-brief', label: 'Morning brief', description: 'Daily summary' },
  ]);
  const loadAgents = vi.fn(async () => [
    { id: 'researcher', label: 'Researcher', description: 'Finds sources' },
  ]);
  render(
    <MemoryRouter>
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <CommandPalette
          commands={commands}
          loadAgents={loadAgents}
          loadWorkflows={loadWorkflows}
          {...handlers}
        />
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  await act(async () => undefined);
  return { controller, transport, loadAgents, ...handlers };
}

const field = () =>
  screen.getByRole('searchbox', { name: 'Find a workspace command' });
const options = () =>
  within(screen.getByRole('listbox', { name: 'Results' })).queryAllByRole(
    'option',
  );

it('starts with recent conversations and commands, then ranks one search across kinds', async () => {
  const run = vi.fn();
  const { onOpenSetting, onOpenConversation, loadAgents } = await setup([
    { id: 'new', label: 'New chat', shortcut: 'Mod+Shift+O', run },
    { id: 'settings', label: 'Settings', keywords: 'configuration', run },
  ]);
  expect(loadAgents).toHaveBeenCalledTimes(1);
  expect(
    screen
      .getByRole('group', { name: 'Recent' })
      .querySelectorAll('[role=option]'),
  ).toHaveLength(5);
  expect(screen.getByRole('option', { name: 'New chat' })).toBeVisible();

  fireEvent.change(field(), { target: { value: 'Open Knowledge settings' } });
  // A former page name still reaches its new page (Knowledge → Memory) first.
  expect(options()[0]).toHaveAccessibleName(/^Memory settings/);
  expect(field()).toHaveAttribute('aria-activedescendant', options()[0].id);
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(onOpenSetting).toHaveBeenCalledWith('/settings/knowledge');

  fireEvent.change(field(), { target: { value: 'Open Utilities settings' } });
  expect(options()[0]).toHaveAccessibleName(/^Tools settings/);

  // Individual settings rows open their page at that row.
  fireEvent.change(field(), { target: { value: 'dream cycle' } });
  const row = screen.getByRole('option', { name: /^Dream Cycle/ });
  fireEvent.click(row);
  expect(onOpenSetting).toHaveBeenLastCalledWith(
    '/settings/preferences#dream-cycle',
  );

  fireEvent.change(field(), { target: { value: 'landing' } });
  const landing = screen.getByRole('option', { name: 'Landing page review' });
  expect(options()[0]).toBe(landing);
  fireEvent.click(landing);
  expect(onOpenConversation).toHaveBeenCalledWith(
    expect.objectContaining({ title: 'Landing page review' }),
  );
});

it('moves the active result with the arrow keys and runs it with Enter', async () => {
  const first = vi.fn();
  const second = vi.fn();
  await setup([
    { id: 'rename-file', label: 'Rename file here', run: first },
    { id: 'rename-chat', label: 'Rename chat', run: second },
  ]);
  fireEvent.change(field(), { target: { value: 'rename' } });
  // Only literal matches show once one exists; shorter labels rank first.
  expect(options().map((option) => option.textContent)).toEqual([
    'Rename chat',
    'Rename file here',
  ]);
  fireEvent.keyDown(field(), { key: 'ArrowDown' });
  expect(options()[1]).toHaveAttribute('aria-selected', 'true');
  expect(field()).toHaveAttribute('aria-activedescendant', options()[1].id);
  fireEvent.keyDown(field(), { key: 'ArrowDown' });
  expect(options()[0]).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(field(), { key: 'ArrowUp' });
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(first).toHaveBeenCalledTimes(1);
  expect(second).not.toHaveBeenCalled();
});

it('offers agents by name and searches history after a pause', async () => {
  const { controller, onStartAgent } = await setup();
  vi.useFakeTimers();
  const search = vi.spyOn(controller, 'searchLibrary');
  fireEvent.change(field(), { target: { value: 'research' } });
  const agent = screen.getByRole('option', { name: /^Chat with Researcher/ });
  fireEvent.click(agent);
  expect(onStartAgent).toHaveBeenCalledWith(
    expect.objectContaining({ id: 'researcher' }),
  );
  expect(search).not.toHaveBeenCalledWith('research');
  await act(async () => vi.advanceTimersByTimeAsync(250));
  expect(search).toHaveBeenCalledWith('research');
  fireEvent.change(field(), { target: { value: 'zzzz-no-match' } });
  expect(screen.getByText(/No results for/)).toBeVisible();
});

it('shows message snippets as one line of plain text', () => {
  expect(
    plainSnippet(
      '## Plan\n\n**local** prospect → `tour` and [pricing](https://example.com)\n- next',
    ),
  ).toBe('Plan local prospect → tour and pricing next');
});

it('finds saved workflows and opens their runs rather than starting them', async () => {
  const { onOpenWorkflow } = await setup();
  fireEvent.change(field(), { target: { value: 'morning brief' } });
  const option = screen.getByRole('option', { name: /^Run Morning brief…/ });
  fireEvent.click(option);
  expect(onOpenWorkflow).toHaveBeenCalledWith(
    expect.objectContaining({ id: 'task-brief' }),
  );
});

it('finds the pages and media Row-Bot made in phone Search, and opens them', async () => {
  const transport = new FixtureTransport({ conversationCount: 2 });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  const onOpenFile = vi.fn();
  const page = {
    id: 'page:p1',
    label: 'Launch checklist',
    kind: 'page' as const,
    conversationId: 'conversation-a',
    conversationTitle: 'A place for your ideas',
    pageId: 'p1',
  };
  render(
    <MemoryRouter>
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <CommandPalette
          phone
          commands={[]}
          loadFiles={async () => [
            page,
            {
              id: 'media:m1',
              label: 'launch-teaser.mp4',
              kind: 'video',
              conversationId: 'conversation-2',
              conversationTitle: '',
            },
          ]}
          onOpenConversation={vi.fn()}
          onOpenSearchHit={vi.fn()}
          onOpenSetting={vi.fn()}
          onOpenFile={onOpenFile}
        />
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  await act(async () => undefined);
  fireEvent.change(
    screen.getByRole('searchbox', { name: 'Search chats, files and settings' }),
    { target: { value: 'launch' } },
  );
  const files = screen.getByRole('group', { name: 'Files' });
  expect(within(files).getAllByRole('option')).toHaveLength(2);
  fireEvent.click(
    within(files).getByRole('option', { name: /^Launch checklist/ }),
  );
  expect(onOpenFile).toHaveBeenCalledWith(page);

  // A chat found by its title shows once, not again among the messages.
  const hit = {
    conversation_id: 'conversation-a',
    title: 'A place for your ideas',
    row_id: null,
    checkpoint_revision: '',
  };
  fireEvent.change(
    screen.getByRole('searchbox', { name: 'Search chats, files and settings' }),
    { target: { value: 'ideas' } },
  );
  act(() =>
    (controller as unknown as { update(patch: object): void }).update({
      search: {
        items: [
          { ...hit, message_id: null, excerpt: hit.title },
          { ...hit, message_id: 'm1', excerpt: 'Some ideas for the garden' },
        ],
      },
    }),
  );
  expect(
    within(screen.getByRole('group', { name: 'Chats' })).getAllByRole('option'),
  ).toHaveLength(1);
  expect(
    within(screen.getByRole('group', { name: 'Messages' })).getAllByRole(
      'option',
    ),
  ).toHaveLength(1);
});
