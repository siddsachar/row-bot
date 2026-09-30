import { act, cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AttachmentView } from '../../api/types';
import {
  ComposerAttachments,
  shortName,
  type AttachmentUpload,
} from './ComposerAttachments';
import { forgetMediaPreviews } from './MediaPreview';

const mock = vi.hoisted(() => ({
  attachmentThumbnail: vi.fn(),
  download: vi.fn(),
  open: vi.fn(),
}));
vi.mock('../../runtime', () => ({ useRuntime: () => ({ controller: mock }) }));
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({ open: mock.open, close: vi.fn() }),
}));

const photo: AttachmentView = {
  attachment_ref: 'conversation-a:photo',
  name: 'photo.png',
  mime_type: 'image/png',
  size_bytes: 2048,
  revision: '1',
};
const report: AttachmentView = {
  attachment_ref: 'conversation-a:report',
  name: 'quarterly-planning-report-final.pdf',
  mime_type: 'application/pdf',
  size_bytes: 2 * 1024 * 1024,
  revision: '1',
};
function upload(file: File, patch: Partial<AttachmentUpload> = {}) {
  return {
    key: `key-${file.name}`,
    conversation: 'c',
    file,
    sent: 0,
    ...patch,
  };
}
const handlers = {
  onRemove: vi.fn(),
  onRetry: vi.fn(),
  onDismiss: vi.fn(),
};
function tiles(
  attachments: AttachmentView[],
  uploads: AttachmentUpload[] = [],
): ReactElement {
  return (
    <ComposerAttachments
      attachments={attachments}
      uploads={uploads}
      {...handlers}
    />
  );
}

beforeEach(() => {
  forgetMediaPreviews(mock);
  let sequence = 0;
  vi.spyOn(URL, 'createObjectURL').mockImplementation(
    () => `blob:fixture-${++sequence}`,
  );
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

it('draws a picture from its small server thumbnail and other files as an icon, name and size', async () => {
  const thumbnail = new Blob(['small png'], { type: 'image/png' });
  mock.attachmentThumbnail.mockResolvedValue(thumbnail);
  const { unmount } = render(tiles([photo, report]));
  const picture = screen.getByRole('button', { name: 'Preview photo.png' });
  await vi.waitFor(() =>
    expect(picture.querySelector('img')).toHaveAttribute(
      'src',
      'blob:fixture-1',
    ),
  );
  expect(URL.createObjectURL).toHaveBeenCalledExactlyOnceWith(thumbnail);
  // Only the picture asks for a thumbnail; the attachment itself is never
  // downloaded for a tile.
  expect(mock.attachmentThumbnail).toHaveBeenCalledExactlyOnceWith(
    'conversation-a:photo',
    expect.any(AbortSignal),
  );
  expect(mock.download).not.toHaveBeenCalled();
  const file = screen.getByRole('button', {
    name: 'Preview quarterly-planning-report-final.pdf',
  });
  expect(file).toHaveTextContent('quarterly-planning-….pdf');
  expect(file).toHaveTextContent('2.0 MB · PDF');
  expect(file.querySelector('img')).toBeNull();
  unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:fixture-1');
});

it('shows a picture whose thumbnail cannot be made as a file with its name and size', async () => {
  mock.attachmentThumbnail.mockRejectedValue({ code: 'payload_too_large' });
  render(tiles([photo]));
  const tile = screen.getByRole('button', { name: 'Preview photo.png' });
  await vi.waitFor(() => expect(tile).toHaveTextContent('2 KB · PNG image'));
  expect(tile.querySelector('img')).toBeNull();
});

it('shows a just-picked picture from the file itself with its progress, and lets the URL go when removed', async () => {
  const shot = new File(['12345678'], 'shot.png', { type: 'image/png' });
  const { rerender } = render(tiles([], [upload(shot, { sent: 2 })]));
  const list = screen.getByRole('list', { name: 'Attachments' });
  expect(URL.createObjectURL).toHaveBeenCalledExactlyOnceWith(shot);
  expect(list.querySelector('img')).toHaveAttribute('src', 'blob:fixture-1');
  expect(mock.attachmentThumbnail).not.toHaveBeenCalled();
  const progress = within(list).getByRole('progressbar', {
    name: 'Uploading shot.png',
  });
  expect(progress).toHaveAttribute('aria-valuenow', '25');
  // An uploading tile has nothing to preview yet.
  expect(within(list).queryByRole('button', { name: /^Preview/ })).toBeNull();
  // Removed from the keyboard: Tab reaches ×, Enter presses it.
  const user = userEvent.setup();
  await user.tab();
  expect(
    within(list).getByRole('button', { name: 'Remove shot.png' }),
  ).toHaveFocus();
  await user.keyboard('{Enter}');
  expect(handlers.onDismiss).toHaveBeenCalledExactlyOnceWith('key-shot.png');
  rerender(tiles([], []));
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:fixture-1');
  expect(screen.queryByRole('list', { name: 'Attachments' })).toBeNull();
});

it('says a failed upload on its tile and offers Retry', async () => {
  const notes = new File(['notes'], 'notes.txt', { type: 'text/plain' });
  const failed = upload(notes, { error: 'Row-Bot lost the connection.' });
  render(tiles([], [failed]));
  const list = screen.getByRole('list', { name: 'Attachments' });
  expect(within(list).getByRole('alert')).toHaveTextContent(
    'Couldn’t upload notes.txt. Row-Bot lost the connection.',
  );
  expect(within(list).getByText('Row-Bot lost the connection.')).toBeVisible();
  expect(within(list).queryByRole('progressbar')).toBeNull();
  const user = userEvent.setup();
  await user.click(
    within(list).getByRole('button', { name: 'Retry notes.txt' }),
  );
  expect(handlers.onRetry).toHaveBeenCalledExactlyOnceWith(failed);
  await user.click(
    within(list).getByRole('button', { name: 'Remove notes.txt' }),
  );
  expect(handlers.onDismiss).toHaveBeenCalledExactlyOnceWith('key-notes.txt');
});

it('removes an uploaded attachment by its reference', async () => {
  render(tiles([report]));
  await userEvent.setup().click(
    screen.getByRole('button', {
      name: 'Remove quarterly-planning-report-final.pdf',
    }),
  );
  expect(handlers.onRemove).toHaveBeenCalledExactlyOnceWith(
    'conversation-a:report',
  );
});

it('opens the attachment in the preview a sent message uses', async () => {
  mock.download.mockResolvedValue(new Blob(['%PDF-1.7']));
  render(tiles([report]));
  await userEvent.setup().click(
    screen.getByRole('button', {
      name: 'Preview quarterly-planning-report-final.pdf',
    }),
  );
  expect(mock.open).toHaveBeenCalledOnce();
  const dialog = mock.open.mock.calls[0][0];
  expect(dialog.title).toBe('quarterly-planning-report-final.pdf');
  expect(dialog.description).toBe('2.0 MB · PDF');
  await act(async () => {
    render(dialog.content);
  });
  expect(mock.download).toHaveBeenCalledWith(
    'conversation-a:report',
    expect.any(AbortSignal),
  );
});

it('keeps the start and the extension of a long name', () => {
  expect(shortName('notes.txt')).toBe('notes.txt');
  expect(shortName('a-very-long-name-for-a-spreadsheet.xlsx')).toBe(
    'a-very-long-name-f….xlsx',
  );
  expect(shortName('no-extension-but-a-rather-long-name')).toBe(
    'no-extension-but-a-rath…',
  );
});
