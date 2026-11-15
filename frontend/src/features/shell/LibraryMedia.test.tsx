import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { MediaItem } from '../../api/types';
import LibraryMedia from './LibraryMedia';

const runtime = vi.hoisted(() => ({
  media: vi.fn(),
  setMediaTags: vi.fn(),
  attachmentThumbnail: vi.fn(),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: runtime }),
}));

const clip: MediaItem = {
  ref: 'chat-a:00000000-0000-0000-0000-000000000001',
  name: 'signup-walkthrough.mp4',
  kind: 'video',
  mime_type: 'video/mp4',
  size: 14_336,
  added_at: '2026-10-28T09:00:00Z',
  conversation_id: 'chat-a',
  conversation_title: 'Launch assets',
  tags: [],
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  runtime.media.mockReset().mockResolvedValue({ items: [clip] });
  runtime.setMediaTags.mockReset();
  runtime.attachmentThumbnail.mockReset().mockRejectedValue(new Error('none'));
});

async function settle() {
  await act(async () => {
    vi.advanceTimersByTime(250);
  });
}

it('lists media with its chat, filters by kind and saves tags (5.3.1 B3)', async () => {
  render(
    <MemoryRouter>
      <LibraryMedia />
    </MemoryRouter>,
  );
  await settle();
  const list = await screen.findByRole('list', { name: 'Media items' });
  expect(
    within(list).getByRole('link', {
      name: 'signup-walkthrough.mp4, in Launch assets',
    }),
  ).toHaveAttribute('href', '/conversations/chat-a');

  fireEvent.click(screen.getByRole('radio', { name: 'Videos' }));
  await settle();
  expect(runtime.media).toHaveBeenLastCalledWith(
    { query: '', kind: 'video' },
    expect.anything(),
  );

  runtime.setMediaTags.mockResolvedValue({
    ref: clip.ref,
    tags: ['demo', 'x post'],
  });
  fireEvent.click(
    within(list).getByRole('button', {
      name: 'Add tags to signup-walkthrough.mp4',
    }),
  );
  const tags = within(list).getByRole('textbox', {
    name: 'Tags for signup-walkthrough.mp4',
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
