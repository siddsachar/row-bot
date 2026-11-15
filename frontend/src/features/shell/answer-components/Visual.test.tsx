import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { onPageRequest } from '../../pages/open-page';
import { AnswerVisual } from './Visual';

const FRAME = `/page-frames/${'a'.repeat(32)}`;
const visualFrame = vi.fn(async () => ({ frame_url: FRAME }));
const createVisualPage = vi.fn(async () => ({
  page_id: 'page-1234abcd',
  title: 'Orbit',
}));
const runtime = { controller: { visualFrame, createVisualPage } };
vi.mock('../../../runtime', () => ({ useOptionalRuntime: () => runtime }));
const open = vi.fn();
vi.mock('../../../ui/overlays', () => ({
  useOverlay: () => ({ open, notify: vi.fn() }),
}));

const BLOCK = {
  id: 'block:visual',
  type: 'visual' as const,
  title: 'Orbit',
  source: '<svg viewBox="0 0 10 10"><title>Orbit</title><circle r="4"/></svg>',
  text: 'Visual: Orbit',
};

async function shown() {
  render(<AnswerVisual block={BLOCK} conversationId="chat" />);
  return (await screen.findByTitle('Orbit')) as HTMLIFrameElement;
}

function post(source: MessageEventSource | null, data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { source, data }));
  });
}

it('draws the visual in a scripts-only sandbox, from a frame Row-Bot serves once', async () => {
  const element = await shown();
  expect(visualFrame).toHaveBeenCalledWith(
    'chat',
    BLOCK.source,
    expect.objectContaining({ mode: expect.any(String) }),
    expect.any(AbortSignal),
  );
  expect(element.getAttribute('sandbox')).toBe('allow-scripts');
  expect(element).toHaveAttribute('src', FRAME);
  expect(element).toHaveAttribute('referrerpolicy', 'no-referrer');
  expect(screen.getByText('Made by Row-Bot')).toBeVisible();
});

it('takes only a height message, only from its own frame, within bounds', async () => {
  const element = await shown();
  post(element.contentWindow, { type: 'rowbot-visual-height', h: 420 });
  expect(element.style.height).toBe('420px');
  post(element.contentWindow, { type: 'rowbot-visual-height', h: 99999 });
  expect(element.style.height).toBe('900px');
  post(element.contentWindow, { type: 'rowbot-visual-height', h: 3 });
  expect(element.style.height).toBe('120px');
  // Another window, another kind of message, or a bad number changes nothing.
  post(window, { type: 'rowbot-visual-height', h: 500 });
  post(element.contentWindow, { type: 'navigate', h: 500 });
  post(element.contentWindow, { type: 'rowbot-visual-height', h: 'tall' });
  expect(element.style.height).toBe('120px');
});

it('stops a visual that tries to open another page', async () => {
  const element = await shown();
  fireEvent.load(element);
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.load(element);
  expect(screen.getByRole('alert')).toHaveTextContent(
    'This visual tried to open another page, so it was stopped.',
  );
  expect(screen.queryByTitle('Orbit')).toBeNull();
});

it('opens as a page beside the chat, expands, and copies its source', async () => {
  const requests = vi.fn(() => true);
  const stop = onPageRequest(requests);
  const copyText = vi.fn(async () => true);
  render(
    <AnswerVisual block={BLOCK} conversationId="chat" copyText={copyText} />,
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Open as page' })),
  );
  expect(createVisualPage).toHaveBeenCalledWith('chat', 'Orbit', BLOCK.source);
  expect(requests).toHaveBeenCalledWith({
    conversationId: 'chat',
    pageId: 'page-1234abcd',
    title: 'Orbit',
  });
  stop();
  fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
  expect(open).toHaveBeenCalledWith(
    expect.objectContaining({ title: 'Orbit', className: 'visual-dialog' }),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Copy source' })),
  );
  expect(copyText).toHaveBeenCalledWith(BLOCK.source);
});
