import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ArtifactPresentation, {
  type ArtifactPresentationProps,
  type DesignPresentationState,
} from './ArtifactPresentation';

const first: DesignPresentationState = {
  resource_id: 'a',
  resource_revision: 'r1',
  page_id: 'one',
  title: 'First',
  notes: '<script>literal notes</script>',
  page_index: 0,
  page_count: 2,
  pages: [{ id: 'one', title: 'First', index: 0 }],
  next_cursor: 'next',
};
function props(): ArtifactPresentationProps {
  return {
    resourceId: 'a',
    resourceRevision: 'r1',
    visible: true,
    load: vi.fn(async ({ page_index, cursor }) =>
      page_index === 1
        ? {
            ...first,
            page_id: 'two',
            title: 'Second',
            notes: 'Second notes',
            page_index: 1,
          }
        : cursor
          ? {
              ...first,
              pages: [{ id: 'two', title: 'Second', index: 1 }],
              next_cursor: null,
            }
          : first,
    ),
    renderPreview: vi.fn((id, kind) => (
      <span>
        {kind}:{id}
      </span>
    )),
    onSession: vi.fn(),
    openAudience: vi.fn(async () => true),
  };
}
async function start() {
  fireEvent.click(screen.getByRole('button', { name: 'Start presentation' }));
  await screen.findByText('stage:one');
}

it('does no work on open and only renders isolated callback content after explicit start', async () => {
  const p = props();
  render(<ArtifactPresentation {...p} />);
  expect(p.load).not.toHaveBeenCalled();
  expect(p.renderPreview).not.toHaveBeenCalled();
  await start();
  expect(screen.getByText(first.notes)).toBeInTheDocument();
  expect(document.querySelector('script')).toBeNull();
  expect(p.onSession).toHaveBeenCalledWith(
    expect.objectContaining({ state: 'started', resource_revision: 'r1' }),
  );
  expect(p.openAudience).not.toHaveBeenCalled();
});

it('navigates with exact page index and announces notes without changing saved source', async () => {
  const p = props();
  render(<ArtifactPresentation {...p} />);
  await start();
  fireEvent.click(screen.getByRole('button', { name: 'Next slide' }));
  await screen.findByText('stage:two');
  expect(screen.getByText('Second notes')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Next slide' })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole('button', { name: 'Previous slide' }), {
    key: 'ArrowLeft',
  });
  await screen.findByText('stage:one');
  expect(p.onSession).toHaveBeenLastCalledWith(
    expect.objectContaining({ state: 'page', page_id: 'one' }),
  );
});

it('lazily renders real paginated thumbnails only after explicit choice', async () => {
  const p = props();
  render(<ArtifactPresentation {...p} />);
  await start();
  expect(screen.queryByText('thumbnail:one')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Slide thumbnails' }));
  expect(screen.getByText('thumbnail:one')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'More slides' }));
  await screen.findByText('thumbnail:two');
  expect(screen.queryByText('thumbnail:one')).not.toBeInTheDocument();
  expect(p.load).toHaveBeenLastCalledWith(
    { page_index: 0, cursor: 'next', limit: 25 },
    expect.any(AbortSignal),
  );
  fireEvent.click(screen.getByRole('button', { name: 'First thumbnails' }));
  await screen.findByText('thumbnail:one');
  expect(screen.queryByText('thumbnail:two')).not.toBeInTheDocument();
});

it('ends session and removes previews on revocation or revision change', async () => {
  const p = props();
  const view = render(<ArtifactPresentation {...p} />);
  await start();
  view.rerender(<ArtifactPresentation {...p} resourceRevision="r2" />);
  expect(screen.queryByText('stage:one')).not.toBeInTheDocument();
  expect(p.onSession).toHaveBeenLastCalledWith(
    expect.objectContaining({ state: 'ended', resource_revision: 'r1' }),
  );
});

it('ignores an old response after switching resource and allows a fresh read', async () => {
  let finish!: (value: DesignPresentationState) => void;
  const p = props();
  p.load = vi.fn(
    () =>
      new Promise<DesignPresentationState>((resolve) => {
        finish = resolve;
      }),
  );
  const view = render(<ArtifactPresentation {...p} />);
  fireEvent.click(screen.getByRole('button', { name: 'Start presentation' }));
  view.rerender(<ArtifactPresentation {...p} resourceId="b" />);
  await act(async () => finish(first));
  expect(p.onSession).not.toHaveBeenCalled();
  expect(screen.queryByText('stage:one')).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Start presentation' }),
  ).toBeEnabled();
});

it('rejects stale preview descriptors and shows explicit fullscreen/audience failure', async () => {
  const p = props();
  p.openAudience = vi.fn(async () => false);
  render(<ArtifactPresentation {...p} />);
  await start();
  fireEvent.click(screen.getByRole('button', { name: 'Fullscreen' }));
  expect(
    screen.getByText('Fullscreen is unavailable in this browser.'),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Open audience window' }));
  await screen.findByText(
    'The audience window could not open. Check popup permissions and try again.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'End presentation' }));
  expect(p.onSession).toHaveBeenLastCalledWith(
    expect.objectContaining({ state: 'ended' }),
  );
});
