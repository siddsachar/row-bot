import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import { forgetMediaPreviews } from './MediaPreview';
import { MessageMedia, type GeneratedMedia } from './TranscriptMessage';

const controller = {
  download: vi.fn(async () => new Blob(['video'], { type: 'video/mp4' })),
} as unknown as ClientController;

function show(media: GeneratedMedia[]) {
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:media');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
  return render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <OverlayProvider>
        <MessageMedia media={media} />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
}

afterEach(() => {
  forgetMediaPreviews(controller);
  vi.restoreAllMocks();
});

it('shows a presented final large, with its name, a Final mark, a player and its caption', async () => {
  const { container } = show([
    {
      reference: 'conversation-a:film',
      mime: 'video/mp4',
      label: 'Birthday montage.mp4',
      caption: 'For Sam, with the party clips',
      role: 'final',
      presented: true,
    },
    { reference: 'conversation-a:clip', mime: 'video/mp4', role: 'final' },
  ]);

  // Named by its title, as its Outputs tile is; it downloads as its file.
  const final = screen.getByRole('figure', { name: 'Final: Birthday montage' });
  expect(within(final).getByText('Final')).toBeVisible();
  expect(within(final).getByText('Birthday montage')).toBeVisible();
  expect(within(final).queryByText(/\.mp4/)).toBeNull();
  expect(final).toHaveTextContent('For Sam, with the party clips');
  await waitFor(() =>
    expect(final.querySelector('video')).toHaveAttribute('src', 'blob:media'),
  );
  expect(
    within(final).getByRole('link', { name: 'Download Birthday montage' }),
  ).toHaveAttribute('download', 'Birthday montage.mp4');
  // An AI clip shows too, in the grid under the finals, without a mark.
  const grid = container.querySelector('.message-media-grid')!;
  expect(grid).toHaveAttribute('data-count', '1');
  expect(within(grid as HTMLElement).queryByText('Final')).toBeNull();
});

it('marks a made video final where it already shows, with no second player', () => {
  const { container } = show([
    {
      reference: 'conversation-a:clip',
      mime: 'video/mp4',
      role: 'final',
      final: true,
    },
  ]);
  expect(screen.queryByRole('figure')).toBeNull();
  expect(
    container.querySelector('.message-media-final .final-chip'),
  ).toHaveTextContent('Final');
  expect(container.querySelectorAll('[data-media-ref]')).toHaveLength(1);
});

it('keeps the file name of a presented document, its type part of its name', () => {
  show([
    {
      reference: 'conversation-a:report',
      mime: 'application/pdf',
      label: 'Q3 report.pdf',
      role: 'final',
      presented: true,
    },
  ]);
  const final = screen.getByRole('figure', { name: 'Final: Q3 report.pdf' });
  expect(within(final).getByText('Q3 report.pdf')).toBeVisible();
});
