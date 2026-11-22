import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ConversationView, MediaItem } from '../../api/types';
import { takeWaitingPage } from '../pages/open-page';
import LibraryFiles from './LibraryFiles';

const runtime = vi.hoisted(() => ({
  conversationsOfGroup: vi.fn(),
  pages: vi.fn(),
  media: vi.fn(),
  workspaceFor: vi.fn(),
  attachmentThumbnail: vi.fn(),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: runtime }),
}));

const chat = (
  id: string,
  title: string,
  updated_at: string,
  kind?: 'artifact' | 'workspace',
): ConversationView => ({
  id,
  revision: '1',
  title,
  pinned: false,
  updated_at,
  resource_bindings: kind
    ? [
        {
          binding_id: `${id}-binding`,
          kind,
          resource_id: `${id}-resource`,
          role: 'primary',
          revision: '1',
        },
      ]
    : [],
});
const resource = (
  ref: string,
  title: string,
  kind: 'artifact' | 'workspace',
) => ({
  resource_ref: ref,
  conversation_revision: '1',
  binding: {
    binding_id: `${ref}-binding`,
    kind,
    resource_id: ref,
    role: 'primary',
    revision: '1',
  },
  title,
  resource_revision: '1',
  available: true,
});
const voice: MediaItem = {
  ref: 'chat-media:voice',
  name: 'voice-line-01.wav',
  kind: 'audio',
  mime_type: 'audio/wav',
  size: 16_000,
  added_at: '2026-11-18T09:00:00Z',
  conversation_id: 'chat-media',
  conversation_title: 'Launch assets',
  tags: [],
};

beforeEach(() => {
  // Each listing group from the server: the chats with a design, and those
  // with a code folder (one a Code chat whose folder is gone).
  runtime.conversationsOfGroup
    .mockReset()
    .mockImplementation(async (group: string) => ({
      items:
        group === 'artifact'
          ? [
              chat(
                'chat-deck',
                'Community workshop deck',
                '2026-11-20T10:00:00Z',
                'artifact',
              ),
            ]
          : [
              chat(
                'chat-code',
                'Release notes cleanup',
                '2026-11-19T10:00:00Z',
                'workspace',
              ),
              chat('chat-unbound', 'Old code chat', '2026-11-20T11:00:00Z'),
            ],
      has_more: false,
      next_cursor: null,
    }));
  runtime.workspaceFor.mockReset().mockImplementation(async (id: string) => ({
    resources:
      id === 'chat-deck'
        ? [resource('artifact:deck', 'Community Workshop Deck', 'artifact')]
        : [resource('workspace:notes', 'demo-release-notes', 'workspace')],
  }));
  runtime.pages.mockReset().mockResolvedValue({
    items: [
      {
        page_id: 'page-plan',
        conversation_id: 'chat-plan',
        conversation_title: 'Beta launch notes',
        title: 'Beta launch plan',
        updated_at: '2026-11-17T09:00:00Z',
        pending: 0,
      },
    ],
  });
  runtime.media.mockReset().mockResolvedValue({ items: [voice] });
  runtime.attachmentThumbnail.mockReset().mockResolvedValue(null);
});

/** Where a tapped file went, and what it asked its chat to open. */
function Opened() {
  const location = useLocation();
  return (
    <p>
      Opened {location.pathname} {JSON.stringify(location.state)}
    </p>
  );
}

function renderFiles() {
  render(
    <MemoryRouter initialEntries={['/library?show=files']}>
      <Routes>
        <Route path="/library" element={<LibraryFiles />} />
        <Route path="/conversations/:id" element={<Opened />} />
      </Routes>
    </MemoryRouter>,
  );
}

it('lists what Row-Bot made, newest first, each with its chat; chips and search narrow it', async () => {
  renderFiles();
  const list = await screen.findByRole('list', { name: 'Files' });
  const names = () =>
    within(list)
      .getAllByRole('link')
      .map((link) => link.textContent);
  expect(names()).toEqual([
    expect.stringMatching(
      /^Community Workshop DeckDesign · in Community workshop deck/,
    ),
    expect.stringMatching(
      /^demo-release-notesCode folder · in Release notes cleanup/,
    ),
    expect.stringMatching(/^voice-line-01\.wavSound · in Launch assets/),
    expect.stringMatching(/^Beta launch planPage · in Beta launch notes/),
  ]);
  // Only the chats with a design or a folder are listed, each in one page,
  // and one with nothing bound is not read for resources.
  expect(
    runtime.conversationsOfGroup.mock.calls.map(([group]) => group),
  ).toEqual(['artifact', 'workspace']);
  expect(runtime.workspaceFor.mock.calls.map(([id]) => id)).toEqual([
    'chat-deck',
    'chat-code',
  ]);

  fireEvent.click(screen.getByRole('radio', { name: 'Folders' }));
  expect(names()).toEqual([expect.stringMatching(/^demo-release-notes/)]);
  fireEvent.click(screen.getByRole('radio', { name: 'Media' }));
  expect(names()).toEqual([expect.stringMatching(/^voice-line-01\.wav/)]);
  fireEvent.click(screen.getByRole('radio', { name: 'All' }));
  fireEvent.change(
    screen.getByRole('searchbox', { name: 'Search what Row-Bot made' }),
    { target: { value: 'launch notes' } },
  );
  expect(names()).toEqual([expect.stringMatching(/^Beta launch plan/)]);
  fireEvent.change(
    screen.getByRole('searchbox', { name: 'Search what Row-Bot made' }),
    { target: { value: 'nothing like this' } },
  );
  expect(screen.getByText('Nothing matches')).toBeVisible();
});

it('opens a design in its chat with its panel asked for', async () => {
  renderFiles();
  fireEvent.click(
    await screen.findByRole('link', { name: /^Community Workshop Deck/ }),
  );
  expect(
    screen.getByText(
      'Opened /conversations/chat-deck {"openResource":"artifact:deck"}',
    ),
  ).toBeVisible();
});

it('opens a page beside its chat', async () => {
  renderFiles();
  fireEvent.click(
    await screen.findByRole('link', { name: /^Beta launch plan/ }),
  );
  expect(screen.getByText(/^Opened \/conversations\/chat-plan/)).toBeVisible();
  expect(takeWaitingPage('chat-plan')).toEqual({
    conversationId: 'chat-plan',
    pageId: 'page-plan',
    title: 'Beta launch plan',
  });
});

it('says when nothing is made yet', async () => {
  runtime.conversationsOfGroup.mockResolvedValue({
    items: [],
    has_more: false,
    next_cursor: null,
  });
  runtime.pages.mockResolvedValue({ items: [] });
  runtime.media.mockResolvedValue({ items: [] });
  renderFiles();
  expect(await screen.findByText('Nothing made yet')).toBeVisible();
});

it('shows pages and media while the designs and folders are still read, then adds them by date', async () => {
  let release!: () => void;
  const opened = new Promise<void>((resolve) => {
    release = resolve;
  });
  const read = runtime.workspaceFor.getMockImplementation()!;
  runtime.workspaceFor.mockImplementation(async (id: string) => {
    await opened;
    return read(id);
  });
  renderFiles();
  const list = await screen.findByRole('list', { name: 'Files' });
  const names = () =>
    within(list)
      .getAllByRole('link')
      .map((link) => link.textContent);
  expect(names()).toEqual([
    expect.stringMatching(/^voice-line-01\.wav/),
    expect.stringMatching(/^Beta launch plan/),
  ]);
  expect(
    screen.getByRole('status', { name: 'Loading designs and folders' }),
  ).toBeVisible();
  await act(async () => release());
  expect(
    await within(list).findByRole('link', { name: /^Community Workshop Deck/ }),
  ).toBeVisible();
  expect(names()).toEqual([
    expect.stringMatching(/^Community Workshop Deck/),
    expect.stringMatching(/^demo-release-notes/),
    expect.stringMatching(/^voice-line-01\.wav/),
    expect.stringMatching(/^Beta launch plan/),
  ]);
  expect(
    screen.queryByRole('status', { name: 'Loading designs and folders' }),
  ).toBeNull();
});

it('shows loading rows in the list shape until every kind of file is read', async () => {
  vi.useFakeTimers();
  try {
    let release!: (value: { items: MediaItem[] }) => void;
    runtime.media.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    renderFiles();
    const loading = screen.getByRole('status', { name: 'Loading files' });
    expect(loading).toHaveAttribute('aria-busy', 'true');
    // A quick read shows no placeholder; a slower one shows the rows.
    expect(loading.querySelectorAll('.skeleton-row')).toHaveLength(0);
    await act(() => vi.advanceTimersByTimeAsync(200));
    expect(loading.querySelectorAll('.skeleton-row')).toHaveLength(9);
    expect(screen.queryByRole('list', { name: 'Files' })).toBeNull();
    await act(async () => release({ items: [voice] }));
    expect(screen.getByRole('list', { name: 'Files' })).toBeVisible();
    expect(screen.queryByRole('status', { name: 'Loading files' })).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});
