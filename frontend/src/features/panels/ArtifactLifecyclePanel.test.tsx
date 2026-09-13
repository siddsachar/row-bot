import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ArtifactLifecyclePanel, {
  type ArtifactLifecyclePanelProps,
  type ArtifactLifecycleState,
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

function props(
  overrides: Partial<ArtifactLifecyclePanelProps> = {},
): ArtifactLifecyclePanelProps {
  return {
    resourceId: 'design-a',
    resourceRevision: 'r1',
    visible: true,
    load: vi.fn(async () => state),
    renderPresentation: vi.fn(() => <p>Presentation controls</p>),
    renderExport: vi.fn(() => <p>Export controls</p>),
    renderSharing: vi.fn(() => <p>Sharing controls</p>),
    ...overrides,
  };
}

it('loads readiness passively and mounts an owner only after its explicit view action', async () => {
  const current = props();
  render(<ArtifactLifecyclePanel {...current} />);
  await screen.findByText(/Presentation:/);

  expect(current.load).toHaveBeenCalledWith(
    'design-a',
    'r1',
    expect.any(AbortSignal),
  );
  expect(current.renderPresentation).not.toHaveBeenCalled();
  expect(current.renderExport).not.toHaveBeenCalled();
  expect(current.renderSharing).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Present' }));
  expect(screen.getByText('Presentation controls')).toBeInTheDocument();
  expect(current.renderPresentation).toHaveBeenCalledWith(state);

  fireEvent.click(screen.getByRole('button', { name: 'Export' }));
  expect(screen.queryByText('Presentation controls')).not.toBeInTheDocument();
  expect(screen.getByText('Export controls')).toBeInTheDocument();
  expect(current.renderExport).toHaveBeenCalledWith(state);
});

it('shows check-on-use and unavailable states without claiming success', async () => {
  render(<ArtifactLifecyclePanel {...props()} />);
  await screen.findByText(/PDF export:/);
  expect(screen.getByText(/PDF export:/).parentElement).toHaveTextContent(
    'Checked when used.',
  );
  expect(screen.getByText(/Channel delivery:/).parentElement).toHaveTextContent(
    'Unavailable.',
  );
});

it('refreshes readiness explicitly when local runtimes or destinations change', async () => {
  const changed: ArtifactLifecycleState = {
    ...state,
    capabilities: state.capabilities.map((item) =>
      item.id === 'share.channel'
        ? { ...item, state: 'ready' as const, detail: 'Channel is running.' }
        : item,
    ),
  };
  const load = vi
    .fn()
    .mockResolvedValueOnce(state)
    .mockResolvedValueOnce(changed);
  render(<ArtifactLifecyclePanel {...props({ load })} />);
  await screen.findByText(/Channel delivery:/);
  expect(screen.getByText(/Channel delivery:/).parentElement).toHaveTextContent(
    'Unavailable.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Refresh options' }));
  await vi.waitFor(() =>
    expect(
      screen.getByText(/Channel delivery:/).parentElement,
    ).toHaveTextContent('Ready. Channel is running.'),
  );
  expect(load).toHaveBeenCalledTimes(2);
});

it('disables a lifecycle group when every canonical operation in it is unavailable', async () => {
  const unavailable: ArtifactLifecycleState = {
    ...state,
    capabilities: state.capabilities.map((item) =>
      item.id.startsWith('publish.') || item.id.startsWith('share.')
        ? { ...item, state: 'unavailable' as const }
        : item,
    ),
  };
  const current = props({ load: vi.fn(async () => unavailable) });
  render(<ArtifactLifecyclePanel {...current} />);
  await screen.findByText(/Local published link:/);
  expect(screen.getByRole('button', { name: 'Share' })).toBeDisabled();
  expect(current.renderSharing).not.toHaveBeenCalled();
});

it('rejects a stale readiness response and only retries after an explicit reload', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce({ ...state, resource_revision: 'old' })
    .mockResolvedValueOnce(state);
  render(<ArtifactLifecyclePanel {...props({ load })} />);
  await screen.findByText(/saved design changed/i);
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
  await screen.findByText(/Presentation:/);
  expect(load).toHaveBeenCalledTimes(2);
});

it('aborts the old read and clears mounted lifecycle controls when authority changes', async () => {
  let finish!: (value: ArtifactLifecycleState) => void;
  const signals: AbortSignal[] = [];
  const load = vi.fn((_id: string, _revision: string, signal: AbortSignal) => {
    signals.push(signal);
    return new Promise<ArtifactLifecycleState>((resolve) => {
      finish = resolve;
    });
  });
  const current = props({ load });
  const view = render(<ArtifactLifecyclePanel {...current} />);
  expect(signals[0]?.aborted).toBe(false);
  view.rerender(
    <ArtifactLifecyclePanel
      {...current}
      resourceId="design-b"
      resourceRevision="r2"
    />,
  );
  expect(signals[0]?.aborted).toBe(true);
  await act(async () => finish(state));
  expect(screen.queryByText(/Presentation:/)).not.toBeInTheDocument();
  view.unmount();
  expect(signals.at(-1)?.aborted).toBe(true);
});

it('renders nothing and makes no request while the parent panel is hidden', () => {
  const current = props({ visible: false });
  const { container } = render(<ArtifactLifecyclePanel {...current} />);
  expect(container).toBeEmptyDOMElement();
  expect(current.load).not.toHaveBeenCalled();
});
