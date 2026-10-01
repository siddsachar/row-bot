import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import type { SearchHit, SearchPage, SettingsSnapshot } from '../../api/types';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { ThemeProvider } from '../../ui/theme';
import { THEME_KEY } from '../../ui/theme-model';
import CommandPalette from './CommandPalette';
import { settingsSwitches } from './palette-switches';

/**
 * The palette understands what people mean (Phase 18, U10, U25): twelve
 * scripted queries and the first result each must give, plus Enter never
 * running a result that only matches scattered letters.
 */

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.useRealTimers();
  localStorage.clear();
});
beforeEach(() => {
  localStorage.setItem(
    THEME_KEY,
    JSON.stringify({
      version: 1,
      appearance: 'light',
      accent: 'blue',
      density: 'compact',
      reduce_transparency: false,
    }),
  );
});

const lantern: SearchHit = {
  conversation_id: 'chat-harbor',
  title: 'Harbor notes',
  message_id: 'message-7',
  row_id: null,
  excerpt: 'The **LANTERN** stays lit on the north pier.',
  checkpoint_revision: 'r1',
};

/**
 * One conversation where a prompt was sent three times (once with more words
 * at its end, past what a row shows), and one other line.
 */
const poster =
  'A **beacon** over the harbour at dusk, painted gold against a low grey sky';
const beacons: SearchHit[] = [
  poster,
  poster,
  `${poster} and gulls`,
  'A second beacon on the hill.',
].map((excerpt, index) => ({
  conversation_id: 'chat-release',
  title: 'Release poster',
  message_id: `message-${index + 1}`,
  row_id: null,
  excerpt,
  checkpoint_revision: 'r2',
}));

/** Only what the switches read from Settings: Developer off, shell on. */
const settings = {
  utilities: {
    availability: 'available',
    items: [
      {
        utility_id: 'developer',
        label: 'Developer',
        description: '',
        available: true,
        enabled: false,
      },
    ],
  },
  system: {
    availability: 'available',
    shell: { enabled: true },
    browser: { enabled: false },
  },
  preferences: {
    availability: 'available',
    identity: { self_improvement_enabled: true },
    dream_cycle: { enabled: true },
  },
  tracker: { availability: 'available', tool_available: true, enabled: false },
} as unknown as SettingsSnapshot;

async function setup(
  loadSwitches: () => Promise<ReturnType<typeof settingsSwitches>> = async () =>
    settingsSwitches(settings, set),
) {
  const transport = new FixtureTransport({ conversationCount: 6 });
  const search = vi.fn(async (query: string): Promise<SearchPage> => ({
    items:
      query.toLowerCase() === 'lantern'
        ? [lantern]
        : query.toLowerCase() === 'beacon'
          ? beacons
          : [],
    has_more: false,
    next_cursor: null,
    scanned_messages: 40,
    revision: 'search-r',
  }));
  Object.assign(transport, { search });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  await controller.start();
  const handlers = {
    onOpenConversation: vi.fn(),
    onOpenSearchHit: vi.fn(),
    onOpenSetting: vi.fn(),
    onStartAgent: vi.fn(),
    onOpenWorkflow: vi.fn(),
    onClose: vi.fn(),
  };
  render(
    <ThemeProvider>
      <MemoryRouter>
        <RuntimeContext.Provider
          value={{ controller, platform: createFakePlatform() }}
        >
          <CommandPalette
            commands={[
              {
                id: 'new-chat',
                label: 'New chat',
                keywords: 'new conversation start compose',
                shortcut: 'Mod+Shift+O',
                run: vi.fn(),
              },
            ]}
            loadAgents={async () => [
              // Its letters spell "lantern" in order: a scattered match.
              { id: 'scatter', label: 'Learn and test every review note' },
            ]}
            loadWorkflows={async () => []}
            loadSwitches={loadSwitches}
            {...handlers}
          />
        </RuntimeContext.Provider>
      </MemoryRouter>
    </ThemeProvider>,
  );
  await act(async () => undefined);
  return { set, search, ...handlers };
}

let set = vi.fn();
beforeEach(() => {
  set = vi.fn();
});

const field = () =>
  screen.getByRole('searchbox', { name: 'Find a workspace command' });
const options = () =>
  within(screen.getByRole('listbox', { name: 'Results' })).queryAllByRole(
    'option',
  );

async function ask(query: string) {
  fireEvent.change(field(), { target: { value: query } });
  // Switches are read on the first letters; message history after a pause.
  await act(async () => undefined);
}

type Ran = Awaited<ReturnType<typeof setup>>;

const INTENTS: {
  query: string;
  first: RegExp;
  ran: (handlers: Ran) => void;
}[] = [
  {
    query: 'connect a model',
    first: /^Connect a model provider/,
    ran: (h) =>
      expect(h.onOpenSetting).toHaveBeenCalledWith('/settings/providers'),
  },
  {
    query: 'turn on developer tools',
    first: /^Turn on Developer tools/,
    ran: (h) =>
      expect(h.set).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'developer' }),
        true,
      ),
  },
  {
    query: 'phone',
    first: /^Connect a phone or computer/,
    ran: (h) =>
      expect(h.onOpenSetting).toHaveBeenCalledWith('/settings/access#connect'),
  },
  {
    query: 'connect telegram',
    first: /^Connect Telegram/,
    ran: (h) =>
      expect(h.onOpenSetting).toHaveBeenCalledWith(
        '/settings/channels#telegram',
      ),
  },
  {
    query: 'api key',
    first: /^Connect a model provider/,
    ran: (h) =>
      expect(h.onOpenSetting).toHaveBeenCalledWith('/settings/providers'),
  },
  {
    query: 'dark mode',
    first: /^Use dark appearance/,
    ran: (h) => {
      expect(JSON.parse(localStorage.getItem(THEME_KEY)!)).toMatchObject({
        appearance: 'dark',
      });
      expect(h.onClose).toHaveBeenCalled();
    },
  },
  {
    query: 'add mcp server',
    first: /^Add an MCP server/,
    ran: (h) =>
      expect(h.onOpenSetting).toHaveBeenCalledWith('/settings/mcp#mcp-servers'),
  },
  {
    query: 'install a skill',
    first: /^Find and install skills/,
    ran: (h) =>
      expect(h.onOpenSetting).toHaveBeenCalledWith(
        '/settings/skills#public-skills',
      ),
  },
  {
    query: 'change model',
    first: /^Choose your default model/,
    ran: (h) =>
      expect(h.onOpenSetting).toHaveBeenCalledWith(
        '/settings/models#default-model',
      ),
  },
  {
    query: 'turn off shell',
    first: /^Turn off Shell access/,
    ran: (h) =>
      expect(h.set).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'shell' }),
        false,
      ),
  },
  {
    query: 'connect gmail',
    first: /^Connect Google/,
    ran: (h) =>
      expect(h.onOpenSetting).toHaveBeenCalledWith('/settings/accounts#google'),
  },
];

it.each(INTENTS)(
  '"$query" gives "$first" first and Enter runs it',
  async ({ query, first, ran }) => {
    const handlers = await setup();
    await ask(query);
    expect(options()[0]).toHaveAccessibleName(first);
    expect(field()).toHaveAttribute('aria-activedescendant', options()[0].id);
    fireEvent.keyDown(field(), { key: 'Enter' });
    ran(handlers);
  },
);

it('"LANTERN" puts the message hit first, above scattered matches, and Enter opens it', async () => {
  const { onOpenSearchHit, onStartAgent } = await setup();
  await ask('LANTERN');
  // Only scattered letters match until history answers: nothing is chosen
  // for Enter, and Enter runs nothing (U25).
  expect(options().map((option) => option.textContent)).toContain(
    'Chat with Learn and test every review note',
  );
  expect(field()).not.toHaveAttribute('aria-activedescendant');
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(onStartAgent).not.toHaveBeenCalled();

  const hit = await screen.findByRole('option', { name: /^Harbor notes/ });
  expect(options()[0]).toBe(hit);
  expect(hit).toHaveTextContent('The LANTERN stays lit on the north pier.');
  expect(field()).toHaveAttribute('aria-activedescendant', hit.id);
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(onOpenSearchHit).toHaveBeenCalledWith(lantern);
  expect(onStartAgent).not.toHaveBeenCalled();
});

it('runs a scattered match only when it is chosen with the arrow keys', async () => {
  const { onStartAgent } = await setup();
  await ask('lrnt');
  expect(options()[0]).toHaveAccessibleName(
    /^Chat with Learn and test every review note/,
  );
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(onStartAgent).not.toHaveBeenCalled();
  fireEvent.keyDown(field(), { key: 'ArrowDown' });
  expect(field()).toHaveAttribute('aria-activedescendant', options()[0].id);
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(onStartAgent).toHaveBeenCalledWith(
    expect.objectContaining({ id: 'scatter' }),
  );
});

it('lists a settings switch with its state and says when it is already as asked', async () => {
  const { set, onOpenSetting } = await setup();
  await ask('developer');
  const developer = options()[0];
  expect(developer).toHaveAccessibleName(/^Turn on Developer tools/);
  expect(developer).toHaveTextContent('Now off');

  await ask('turn on shell');
  expect(options()[0]).toHaveAccessibleName(/^Shell access is already on/);
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(set).not.toHaveBeenCalled();
  expect(onOpenSetting).toHaveBeenCalledWith('/settings/system#shell.enabled');
});

it('says it is reading settings while a switch could still match, and runs nothing', async () => {
  const { onOpenSetting } = await setup(() => new Promise(() => {}));
  await ask('turn on developer tools');
  expect(screen.getByText('Reading your settings…')).toBeVisible();
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(set).not.toHaveBeenCalled();
  expect(onOpenSetting).not.toHaveBeenCalled();
});

it('shows a command’s shortcut beside it', async () => {
  await setup();
  await ask('new conversation');
  const option = options()[0];
  expect(option).toHaveAccessibleName(/^New chat/);
  expect(option.querySelector('.command-palette-kbd')).not.toBeNull();
});

it('shows the same words from one conversation once, and the two-letter hint only before typing', async () => {
  await setup();
  expect(screen.getByText('Type two letters to search messages')).toBeVisible();
  await ask('beacon');
  const hits = await screen.findAllByRole('option', {
    name: /^Release poster/,
  });
  expect(hits.map((hit) => hit.textContent)).toEqual([
    expect.stringContaining('A beacon over the harbour at dusk, painted gold'),
    expect.stringContaining('A second beacon on the hill.'),
  ]);
  expect(screen.queryByText('Type two letters to search messages')).toBeNull();
});
