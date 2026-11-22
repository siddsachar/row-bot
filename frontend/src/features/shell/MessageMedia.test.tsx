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

  const final = screen.getByRole('figure', {
    name: 'Final: Birthday montage.mp4',
  });
  expect(within(final).getByText('Final')).toBeVisible();
  expect(final).toHaveTextContent('For Sam, with the party clips');
  await waitFor(() =>
    expect(final.querySelector('video')).toHaveAttribute('src', 'blob:media'),
  );
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
