import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { TranscriptRow } from '../../api/types';
import { publicBlockText, TranscriptBlocks } from './TranscriptBlocks';

const download = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: { download } }),
}));

beforeEach(() => {
  download.mockReset();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(
    () => undefined,
  );
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(
    () => undefined,
  );
});

it('keeps YouTube external until explicit consent and uses the no-cookie host', () => {
  const blocks: TranscriptRow['blocks'] = [
    {
      id: 'block:youtube',
      type: 'youtube',
      video_id: 'dQw4w9WgXcQ',
      url: 'https://youtu.be/dQw4w9WgXcQ',
      title: 'Synthetic video',
    },
  ];
  const { container } = render(<TranscriptBlocks blocks={blocks} />);
  expect(container.querySelector('iframe')).toBeNull();
  expect(screen.getByText(/contacts YouTube/i)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Load YouTube player' }));
  expect(screen.getByTitle('Synthetic video')).toHaveAttribute(
    'src',
    'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ',
  );
});

it('renders canonical Markdown and returns only represented public text', () => {
  const blocks: TranscriptRow['blocks'] = [
    { id: 'block:markdown', type: 'markdown', text: '**Safe**' },
    {
      id: 'block:chart',
      type: 'chart',
      figure_json: '{"data":[],"layout":{}}',
      text: 'Chart ready',
    },
  ];
  render(<TranscriptBlocks blocks={blocks.slice(0, 1)} />);
  expect(screen.getByText('Safe')).toHaveProperty('tagName', 'STRONG');
  expect(blocks.map(publicBlockText)).toEqual(['**Safe**', 'Chart ready']);
});

it('renders a durable attachment chip and authenticated local media preview', async () => {
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:attachment');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
  download.mockResolvedValue(new Blob(['fixture'], { type: 'audio/wav' }));
  const blocks: TranscriptRow['blocks'] = [
    {
      id: 'attachment:fixture',
      type: 'attachment',
      attachment_ref: 'conversation-a:attachment-a',
      name: 'fixture.wav',
      mime_type: 'audio/wav',
      size_bytes: 2048,
      revision: '1',
    },
  ];

  render(<TranscriptBlocks blocks={blocks} />);

  expect(screen.getByText('fixture.wav')).toBeVisible();
  expect(screen.getByText('2 KB · audio/wav')).toBeVisible();
  expect(
    await screen.findByRole('group', { name: 'fixture.wav' }),
  ).toBeVisible();
  expect(download).toHaveBeenCalledWith(
    'conversation-a:attachment-a',
    expect.any(AbortSignal),
  );
  expect(publicBlockText(blocks[0])).toBe('fixture.wav');
});
