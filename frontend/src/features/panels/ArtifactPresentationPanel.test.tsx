import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ArtifactPreview } from '../../api/types';
import ArtifactPresentationPanel, {
  StaticDesignPage,
  type ArtifactPresentationPanelProps,
} from './ArtifactPresentationPanel';

let visibility: (visible: boolean) => void;
beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      constructor(callback: IntersectionObserverCallback) {
        visibility = (visible) =>
          callback(
            [{ isIntersecting: visible }] as IntersectionObserverEntry[],
            this as unknown as IntersectionObserver,
          );
      }
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function preview(pageId = 'one'): ArtifactPreview {
  return {
    resource_id: 'a',
    resource_revision: 'r1',
    preview_revision: 'p1',
    mode: 'deck',
    page_id: pageId,
    page_index: 0,
    page_count: 2,
    page_title: pageId,
    canvas_width: 1920,
    canvas_height: 1080,
    pages: [],
    html: '<h1>Saved slide</h1>',
    scripts_allowed: false,
    unchanged: false,
  };
}
function props(): ArtifactPresentationPanelProps {
  return {
    resourceId: 'a',
    resourceRevision: 'r1',
    visible: true,
    load: vi.fn(async ({ page_index }) => ({
      resource_id: 'a',
      resource_revision: 'r1',
      page_id: page_index === 1 ? 'two' : 'one',
      page_index: page_index ?? 0,
      page_count: 2,
      title: 'Saved design',
      notes: 'PRIVATE SPEAKER NOTES',
      pages: [{ id: 'one', title: 'First', index: 0 }],
      next_cursor: null,
    })),
    preview: vi.fn(async (pageId) => preview(pageId)),
  };
}

it('loads thumbnails only while visible and aborts when hidden', async () => {
  const load = vi.fn(async (_pageId: string, _signal: AbortSignal) =>
    preview(),
  );
  render(
    <StaticDesignPage
      resourceId="a"
      resourceRevision="r1"
      pageId="one"
      preview={load}
      thumbnail
    />,
  );
  expect(load).not.toHaveBeenCalled();
  act(() => visibility(true));
  const frame = await screen.findByTitle('Thumbnail: one');
  expect(frame).toHaveAttribute('sandbox', '');
  expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer');
  const signal = load.mock.calls[0]?.[1] as AbortSignal | undefined;
  act(() => visibility(false));
  expect(signal?.aborted).toBe(true);
  expect(screen.queryByTitle('Thumbnail: one')).not.toBeInTheDocument();
});

it.each(['resource', 'revision', 'page', 'scripts'])(
  'rejects a mismatched or unsafe %s preview',
  async (field) => {
    const value = preview();
    if (field === 'resource') value.resource_id = 'other';
    if (field === 'revision') value.resource_revision = 'other';
    if (field === 'page') value.page_id = 'other';
    if (field === 'scripts') value.scripts_allowed = true;
    render(
      <StaticDesignPage
        resourceId="a"
        resourceRevision="r1"
        pageId="one"
        preview={async () => value}
      />,
    );
    await screen.findByText('Presentation preview unavailable');
    expect(document.querySelector('iframe')).toBeNull();
  },
);

it('opens an explicit slide-only audience, follows navigation, and closes on revision change', async () => {
  const childDocument = document.implementation.createHTMLDocument();
  const child = {
    document: childDocument,
    close: vi.fn(),
    focus: vi.fn(),
    closed: false,
  };
  const open = vi
    .spyOn(window, 'open')
    .mockReturnValue(child as unknown as Window);
  const p = props();
  const view = render(<ArtifactPresentationPanel {...p} />);
  expect(open).not.toHaveBeenCalled();
  expect(p.preview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Start presentation' }));
  await screen.findByTitle('Presentation: one');
  fireEvent.click(screen.getByRole('button', { name: 'Open audience window' }));
  await waitFor(() =>
    expect(childDocument.querySelector('iframe')?.getAttribute('title')).toBe(
      'Presentation: one',
    ),
  );
  expect(open).toHaveBeenCalledWith(
    'about:blank',
    '_blank',
    'popup,width=1280,height=800',
  );
  expect(childDocument.body.textContent).not.toContain('PRIVATE SPEAKER NOTES');
  expect(childDocument.querySelector('iframe')?.getAttribute('sandbox')).toBe(
    '',
  );
  expect(childDocument.querySelector('button')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Next slide' }));
  await waitFor(() =>
    expect(childDocument.querySelector('iframe')?.getAttribute('title')).toBe(
      'Presentation: two',
    ),
  );
  view.rerender(<ArtifactPresentationPanel {...p} resourceRevision="r2" />);
  expect(child.close).toHaveBeenCalledTimes(1);
  expect(childDocument.querySelector('iframe')).toBeNull();
});

it('closes its audience on unmount', async () => {
  const child = {
    document: document.implementation.createHTMLDocument(),
    close: vi.fn(),
    closed: false,
  };
  vi.spyOn(window, 'open').mockReturnValue(child as unknown as Window);
  const view = render(<ArtifactPresentationPanel {...props()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Start presentation' }));
  await screen.findByTitle('Presentation: one');
  fireEvent.click(screen.getByRole('button', { name: 'Open audience window' }));
  await waitFor(() =>
    expect(child.document.querySelector('iframe')).not.toBeNull(),
  );
  view.unmount();
  expect(child.close).toHaveBeenCalledTimes(1);
});
