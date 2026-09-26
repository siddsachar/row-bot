import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import CommandPalette, { type PaletteCommand } from './CommandPalette';

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
  };
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
  // The legacy "Open … settings" phrasing still reaches the page first.
  expect(options()[0]).toHaveAccessibleName(/^Knowledge settings/);
  expect(field()).toHaveAttribute('aria-activedescendant', options()[0].id);
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(onOpenSetting).toHaveBeenCalledWith('/settings/knowledge');

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
    { id: 'reset', label: 'Reset layout', run: first },
    { id: 'rename', label: 'Reset zoom', run: second },
  ]);
  fireEvent.change(field(), { target: { value: 'reset' } });
  // Only literal matches show once one exists; shorter labels rank first.
  expect(options().map((option) => option.textContent)).toEqual([
    'Reset zoom',
    'Reset layout',
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
