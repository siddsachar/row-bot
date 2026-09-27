import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import {
  DesignCapabilities,
  useDesignLifecycle,
  type ArtifactLifecyclePanelProps,
  type ArtifactLifecycleState,
  type LifecycleView,
} from './ArtifactLifecyclePanel';

const state: ArtifactLifecycleState = {
  resource_id: 'design-a',
  resource_revision: 'r1',
  mode: 'deck',
  page_count: 3,
  capabilities: [
    {
      id: 'presentation',
      label: 'Presentation',
      state: 'ready',
      detail: 'Uses an isolated preview.',
      review_required: false,
    },
    {
      id: 'thumbnails',
      label: 'Slide thumbnails',
      state: 'ready',
      detail: 'Loads while visible.',
      review_required: false,
    },
    {
      id: 'export.html',
      label: 'HTML export',
      state: 'ready',
      detail: 'Retained locally.',
      review_required: false,
    },
    {
      id: 'export.pdf',
      label: 'PDF export',
      state: 'check_on_use',
      detail: 'Renderer checked on use.',
      review_required: false,
    },
    {
      id: 'publish.local',
      label: 'Local published link',
      state: 'ready',
      detail: 'Requires review.',
      review_required: true,
    },
    {
      id: 'share.channel',
      label: 'Channel delivery',
      state: 'unavailable',
      detail: 'Start a channel.',
      review_required: true,
    },
  ],
};

function Harness(props: ArtifactLifecyclePanelProps) {
  const lifecycle = useDesignLifecycle(props);
  return (
    <>
      <DesignCapabilities lifecycle={lifecycle} />
      {(['presentation', 'export', 'sharing'] as LifecycleView[]).map(
        (view) => (
          <p key={view}>
            {view}: {lifecycle.available(view) ? 'available' : 'unavailable'}
          </p>
        ),
      )}
      <p>state: {lifecycle.state ? 'loaded' : 'none'}</p>
    </>
  );
}

function props(
  overrides: Partial<ArtifactLifecyclePanelProps> = {},
): ArtifactLifecyclePanelProps {
  return {
    resourceId: 'design-a',
    resourceRevision: 'r1',
    visible: true,
    load: vi.fn(async () => state),
    ...overrides,
  };
}

async function openPopover() {
  await userEvent.click(
    screen.getByRole('button', { name: 'Design capabilities' }),
  );
  return screen.findByRole('dialog', { name: 'Design capabilities' });
}

it('reads readiness once while visible and lists every capability in words', async () => {
  const current = props();
  render(<Harness {...current} />);
  await screen.findByText('state: loaded');
  expect(current.load).toHaveBeenCalledWith(
    'design-a',
    'r1',
    expect.any(AbortSignal),
  );
  const popover = await openPopover();
  expect(popover).toHaveTextContent('Capabilities and review requirements');
  const pdf = screen.getByText('PDF export').closest('li')!;
  expect(pdf).toHaveTextContent('Checked when used');
  expect(screen.getByText('Channel delivery').closest('li')).toHaveTextContent(
    'Unavailable · Review required',
  );
  expect(
    screen.getByText('Local published link').closest('li'),
  ).toHaveTextContent('Ready · Review required');
  expect(popover).toHaveTextContent(
    'Publishing and delivery require a separate review.',
  );
  expect(current.load).toHaveBeenCalledTimes(1);
});

it('treats a view as available while unknown and unavailable only when every operation is', async () => {
  const unavailable: ArtifactLifecycleState = {
    ...state,
    capabilities: state.capabilities.map((item) =>
      item.id.startsWith('publish.') || item.id.startsWith('share.')
        ? { ...item, state: 'unavailable' as const }
        : item,
    ),
  };
  let finish!: (value: ArtifactLifecycleState) => void;
  const load = vi.fn(
    () =>
      new Promise<ArtifactLifecycleState>((resolve) => {
        finish = resolve;
      }),
  );
  render(<Harness {...props({ load })} />);
  expect(screen.getByText('sharing: available')).toBeInTheDocument();
  await act(async () => finish(unavailable));
  expect(screen.getByText('sharing: unavailable')).toBeInTheDocument();
  expect(screen.getByText('export: available')).toBeInTheDocument();
  expect(screen.getByText('presentation: available')).toBeInTheDocument();
});

it('rejects a stale readiness response and retries only from the popover', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce({ ...state, resource_revision: 'old' })
    .mockResolvedValueOnce(state);
  render(<Harness {...props({ load })} />);
  await openPopover();
  await screen.findByText(/saved design changed/i);
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText('state: loaded');
  expect(load).toHaveBeenCalledTimes(2);
});

it('aborts the old read when authority changes and ignores its late answer', async () => {
  let finish!: (value: ArtifactLifecycleState) => void;
  const signals: AbortSignal[] = [];
  const load = vi.fn((_id: string, _revision: string, signal: AbortSignal) => {
    signals.push(signal);
    return new Promise<ArtifactLifecycleState>((resolve) => {
      finish = resolve;
    });
  });
  const current = props({ load });
  const view = render(<Harness {...current} />);
  expect(signals[0]?.aborted).toBe(false);
  view.rerender(
    <Harness {...current} resourceId="design-b" resourceRevision="r2" />,
  );
  expect(signals[0]?.aborted).toBe(true);
  await act(async () => finish(state));
  expect(screen.getByText('state: none')).toBeInTheDocument();
  view.unmount();
  expect(signals.at(-1)?.aborted).toBe(true);
});

it('makes no request while the parent panel is hidden', () => {
  const current = props({ visible: false });
  render(<Harness {...current} />);
  expect(current.load).not.toHaveBeenCalled();
  expect(screen.getByText('state: none')).toBeInTheDocument();
});
