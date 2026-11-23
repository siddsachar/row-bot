import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { OutputView } from '../../api/types';
import LibraryMedia from './LibraryMedia';

const runtime = vi.hoisted(() => ({
  outputs: vi.fn(),
  setMediaTags: vi.fn(),
  attachmentThumbnail: vi.fn(),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: runtime }),
}));

function version(n: number, role: OutputView['role']) {
  return {
    ref: `chat-a:00000000-0000-0000-0000-00000000000${n}`,
    version: n,
    role,
    file_name: 'Sign-up walkthrough.mp4',
    mime_type: 'video/mp4',
    size: 14_336,
    created_at: '2026-10-28T09:00:00Z',
  };
}

/** A film with two versions, the newest a draft, from the chat "Launch assets". */
const clip: OutputView = {
  ...version(2, 'draft'),
  id: '0123456789abcdef01234567',
  conversation_id: 'chat-a',
  conversation_title: 'Launch assets',
  name: 'Sign-up walkthrough',
  kind: 'video',
  versions: [version(2, 'draft'), version(1, 'draft')],
  version_count: 2,
  working_count: 3,
  updated_at: '2026-10-28T09:00:00Z',
  tags: [],
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  runtime.outputs.mockReset().mockResolvedValue({ items: [clip] });
  runtime.setMediaTags.mockReset();
  runtime.attachmentThumbnail.mockReset().mockRejectedValue(new Error('none'));
});

async function settle() {
  await act(async () => {
    vi.advanceTimersByTime(250);
  });
}

function renderMedia() {
  render(
    <MemoryRouter>
      <LibraryMedia />
    </MemoryRouter>,
  );
}

it('lists what the chats made, once each with its versions and its chat, finished items first (W3)', async () => {
  renderMedia();
  await settle();
  expect(runtime.outputs).toHaveBeenLastCalledWith(
    { show: 'made', query: '', kind: undefined },
    expect.anything(),
  );
  const list = await screen.findByRole('list', { name: 'Media items' });
  const link = within(list).getByRole('link', {
    name: 'Sign-up walkthrough, in Launch assets',
  });
  expect(link).toHaveAttribute('href', '/conversations/chat-a');
  expect(link).toHaveTextContent('Draft');
  expect(link).toHaveTextContent(/Video · v2 of 2 · 14 KB · In Launch assets/);
  const chips = screen.getByRole('radiogroup', { name: 'Show' });
  expect(
    within(chips)
      .getAllByRole('radio')
      .map((chip) => chip.getAttribute('aria-label')),
  ).toEqual(['Made', 'Uploads', 'Working files']);
  expect(within(chips).getByRole('radio', { name: 'Made' })).toBeChecked();
});

it('shows uploads and working files on their chips, with loading rows between', async () => {
  renderMedia();
  await settle();
  await screen.findByRole('list', { name: 'Media items' });
  runtime.outputs.mockResolvedValue({ items: [] });
  fireEvent.click(screen.getByRole('radio', { name: 'Working files' }));
  expect(screen.getByRole('status', { name: 'Loading media' })).toBeVisible();
  await settle();
  expect(runtime.outputs).toHaveBeenLastCalledWith(
    { show: 'working', query: '', kind: undefined },
    expect.anything(),
  );
  expect(await screen.findByText('No working files')).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: 'Uploads' }));
  await settle();
  expect(await screen.findByText('No uploads yet')).toBeVisible();
});

it('filters by kind and saves tags on the version shown', async () => {
  renderMedia();
  await settle();
  const list = await screen.findByRole('list', { name: 'Media items' });
  fireEvent.click(screen.getByRole('radio', { name: 'Videos' }));
  await settle();
  expect(runtime.outputs).toHaveBeenLastCalledWith(
    { show: 'made', query: '', kind: 'video' },
    expect.anything(),
  );

  runtime.setMediaTags.mockResolvedValue({
    ref: clip.ref,
    tags: ['demo', 'x post'],
  });
  fireEvent.click(
    within(list).getByRole('button', {
      name: 'Add tags to Sign-up walkthrough',
    }),
  );
  const tags = within(list).getByRole('textbox', {
    name: 'Tags for Sign-up walkthrough',
  });
  fireEvent.change(tags, { target: { value: 'Demo, x post' } });
  await act(async () => {
    fireEvent.submit(tags.closest('form')!);
  });
  expect(runtime.setMediaTags).toHaveBeenCalledWith(clip.ref, [
    'Demo',
    'x post',
  ]);
  expect(within(list).getByText('x post')).toBeVisible();
});
