import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ResourceView } from '../../api/types';
import ArtifactDesignPanel from './ArtifactDesignPanel';
import type { DesignControlsState } from './ArtifactDesignControls';
import {
  createArtifactDesignSessions,
  type DesignReceipt,
  type DesignSessionOwner,
} from './artifact-design-sessions';

const resource = {
  available: true,
  resource_revision: 'r1',
  binding: {
    kind: 'artifact',
    resource_id: 'design',
    binding_id: 'binding',
    revision: 'b1',
  },
} as ResourceView;
function fixture(mode = 'deck') {
  const snapshot = {
    identity: 'auth',
    conversationId: 'chat',
    conversationRevision: '1',
    loading: false,
    resources: [resource],
  };
  const listeners = new Set<() => void>();
  const state: DesignControlsState = {
    resource_id: 'design',
    resource_revision: 'r1',
    mode,
    page_id: 'first',
    section: 'elements',
    brand: {
      primary_color: '#112233',
      secondary_color: '#223344',
      accent_color: '#334455',
      bg_color: '#ffffff',
      text_color: '#000000',
      heading_font: 'Inter',
      body_font: 'Inter',
      logo_asset_id: '',
      logo_mode: 'auto',
      logo_scope: 'all',
      logo_position: 'top_right',
      logo_max_height: 72,
      logo_padding: 24,
    },
    element: { id: 'heading', tag: 'h1', styles: {}, action: '' },
    items: [],
    item_count: 0,
    next_cursor: null,
  };
  const success = (id: string): DesignReceipt => ({
    command_id: id,
    conversation_id: 'chat',
    binding_id: 'binding',
    binding_revision: 'b1',
    resource_id: 'design',
    status: 'completed',
    artifact_design: {
      resource_id: 'design',
      resource_revision: 'r1',
      operation: 'brand',
      status: 'saved',
      code: '',
    },
  });
  const owner: DesignSessionOwner = {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    load: vi.fn(async (_scope, options) => ({
      ...state,
      section: options.section,
    })),
    review: vi.fn(async () => ({
      resource_id: 'design',
      resource_revision: 'r1',
      page_id: 'first',
      scope: 'page' as const,
      heuristic: true,
      score: 80,
      findings: [
        {
          id: 'finding',
          source: 'brand_lint',
          category: 'missing_alt',
          severity: 'low',
          message: 'Missing alternative text',
          suggested_fix: 'Add alt text',
          page_id: 'first',
          auto_fixable: true,
        },
      ],
      finding_count: 1,
      next_cursor: null,
    })),
    draftFix: vi.fn(async () => 'Review this local draft'),
    stageUpload: vi.fn(),
    presetReview: vi.fn(),
    execute: vi.fn(async (_scope, id) => success(id)),
    receipt: vi.fn(async () => null),
  };
  const sessions = createArtifactDesignSessions(owner),
    session = sessions.get('chat', resource),
    onDraftText = vi.fn();
  const props = {
    session,
    resourceRevision: 'r1',
    pageId: 'first',
    selectedElementId: 'heading',
    onSelectElement: vi.fn(),
    visible: true,
    onDraftText,
  };
  return {
    owner,
    state,
    snapshot,
    success,
    session,
    sessions,
    props,
    notify: () => listeners.forEach((listener) => listener()),
  };
}

it.each(['deck', 'document', 'landing', 'app_mockup', 'storyboard'])(
  'opens %s controls passively and retains a brand draft through full remount',
  async (mode) => {
    const f = fixture(mode);
    const first = render(<ArtifactDesignPanel {...f.props} />);
    await screen.findByText('Brand and fonts');
    fireEvent.change(screen.getByLabelText('primary color'), {
      target: { value: '#445566' },
    });
    first.unmount();
    render(<ArtifactDesignPanel {...f.props} />);
    await screen.findByText('Brand and fonts');
    expect(screen.getByLabelText('primary color')).toHaveValue('#445566');
    expect(f.owner.execute).not.toHaveBeenCalled();
    expect(f.owner.stageUpload).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('button', { name: 'Apply hotspot' }) !== null,
    ).toBe(['landing', 'app_mockup', 'storyboard'].includes(mode));
  },
);

it('retains pending original command through remount and accepts its late completion once', async () => {
  const f = fixture();
  let finish!: (value: DesignReceipt) => void;
  vi.mocked(f.owner.execute).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const first = render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('Brand and fonts');
  fireEvent.change(screen.getByLabelText('primary color'), {
    target: { value: '#445566' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Apply brand' }));
  const id = f.session.getSnapshot().attempt!.commandId;
  first.unmount();
  render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('The original design command is in progress.');
  expect(screen.getByRole('button', { name: 'Apply brand' })).toBeDisabled();
  await act(async () => finish(f.success(id)));
  await screen.findByText('Original design command confirmed.');
  expect(f.owner.execute).toHaveBeenCalledTimes(1);
});

it('keeps uncertainty visible across remount and only explicitly checks the original receipt', async () => {
  const f = fixture();
  vi.mocked(f.owner.execute).mockRejectedValue(new Error('Lost response'));
  const first = render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('Brand and fonts');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Apply brand' })),
  );
  first.unmount();
  render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText(/New changes are paused/);
  expect(f.owner.receipt).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Check original design command' }),
    ),
  );
  expect(f.owner.receipt).toHaveBeenCalledTimes(1);
  expect(f.owner.execute).toHaveBeenCalledTimes(1);
});

it('does not resurrect a private draft or pending command after auth loss and late success', async () => {
  const f = fixture();
  let finish!: (value: DesignReceipt) => void;
  vi.mocked(f.owner.execute).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('Brand and fonts');
  fireEvent.change(screen.getByLabelText('primary color'), {
    target: { value: '#445566' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Apply brand' }));
  const id = f.session.getSnapshot().attempt!.commandId;
  act(() => {
    f.snapshot.identity = '';
    f.notify();
  });
  await act(async () => finish(f.success(id)));
  expect(screen.getByText('Design access changed')).toBeInTheDocument();
  expect(f.session.getSnapshot().attempt).toBeNull();
  expect(f.session.form.getSnapshot().brand).toBeNull();
});

it('blocks a stale draft until the user explicitly discards it', async () => {
  const f = fixture();
  const view = render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('Brand and fonts');
  fireEvent.change(screen.getByLabelText('primary color'), {
    target: { value: '#445566' },
  });
  f.state.resource_revision = 'r2';
  view.rerender(<ArtifactDesignPanel {...f.props} resourceRevision="r2" />);
  await screen.findByText(/unsaved design draft belongs/);
  expect(screen.getByRole('button', { name: 'Apply brand' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Discard design draft' }));
  await screen.findByText('Brand and fonts');
  expect(screen.getByLabelText('primary color')).toHaveValue('#112233');
  expect(f.owner.execute).not.toHaveBeenCalled();
});

it('drafts into the existing composer only after the explicit button and never executes a design command', async () => {
  const f = fixture();
  render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('Brand and fonts');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Run design review' })),
  );
  expect(f.props.onDraftText).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Draft AI fix in chat' }),
    ),
  );
  expect(f.props.onDraftText).toHaveBeenCalledWith('Review this local draft');
  expect(f.owner.execute).not.toHaveBeenCalled();
});

it('keeps each catalog page bounded and makes the first page explicitly reachable', async () => {
  const f = fixture();
  vi.mocked(f.owner.load).mockImplementation(async (_scope, options) => ({
    ...f.state,
    section: options.section,
    items: Array.from({ length: 50 }, (_, index) => ({
      id: `${options.cursor ? 'tail' : 'head'}-${index}`,
      label: `${options.cursor ? 'Later' : 'Earlier'} ${index}`,
      kind: 'h1',
      detail: '',
      available: true,
    })),
    item_count: 100,
    next_cursor: options.cursor ? null : 'next',
  }));
  render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('Earlier 0 ·');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Next controls page' })),
  );
  expect(screen.queryByText('Earlier 0 ·')).not.toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(50);
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'First controls page' }),
    ),
  );
  await screen.findByText('Earlier 0 ·');
  expect(screen.queryByText('Later 0 ·')).not.toBeInTheDocument();
});

it('replaces review pages without hiding later findings and exposes First findings page', async () => {
  const f = fixture();
  vi.mocked(f.owner.review).mockImplementation(async (_scope, options) => ({
    resource_id: 'design',
    resource_revision: 'r1',
    page_id: 'first',
    scope: 'page',
    heuristic: true,
    score: 80,
    findings: Array.from({ length: 50 }, (_, index) => ({
      id: `${options.cursor ? 'later' : 'first'}-${index}`,
      source: 'brand_lint',
      category: 'spacing',
      severity: 'low',
      message: `${options.cursor ? 'Later' : 'First'} finding ${index}`,
      suggested_fix: 'Review layout',
      page_id: 'first',
      auto_fixable: false,
    })),
    finding_count: 100,
    next_cursor: options.cursor ? null : 'next',
  }));
  render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('Brand and fonts');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Run design review' })),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Next findings page' })),
  );
  expect(screen.queryByText('low: First finding 0')).not.toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(50);
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'First findings page' }),
    ),
  );
  expect(screen.getByText('low: First finding 0')).toBeInTheDocument();
  expect(screen.queryByText('low: Later finding 0')).not.toBeInTheDocument();
});

it('preserves the selected local asset and name through remount without uploading', async () => {
  const f = fixture();
  const first = render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('Brand and fonts');
  fireEvent.change(screen.getByLabelText('Design catalog'), {
    target: { value: 'assets' },
  });
  const file = new File(['123'], 'local.png', { type: 'image/png' });
  await screen.findByLabelText('Choose asset');
  fireEvent.change(screen.getByLabelText('Choose asset'), {
    target: { files: [file] },
  });
  first.unmount();
  render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByLabelText('Choose asset');
  expect(f.session.form.getSnapshot().file).toBe(file);
  expect(
    screen.getByText('Selected local asset: local.png'),
  ).toBeInTheDocument();
  expect(f.owner.stageUpload).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Add asset' })).toBeEnabled();
});

it('does not discard an unrelated unsaved brand draft when an asset upload succeeds', async () => {
  const f = fixture();
  vi.mocked(f.owner.stageUpload).mockResolvedValue({
    upload_id: 'owned',
    size_bytes: 3,
    sha256: 'a'.repeat(64),
  });
  vi.mocked(f.owner.execute).mockImplementation(async (_scope, id) => ({
    ...f.success(id),
    artifact_design: {
      ...f.success(id).artifact_design!,
      operation: 'asset_upload',
    },
  }));
  render(<ArtifactDesignPanel {...f.props} />);
  await screen.findByText('Brand and fonts');
  fireEvent.change(screen.getByLabelText('primary color'), {
    target: { value: '#445566' },
  });
  fireEvent.change(screen.getByLabelText('Design catalog'), {
    target: { value: 'assets' },
  });
  await screen.findByLabelText('Choose asset');
  fireEvent.change(screen.getByLabelText('Choose asset'), {
    target: { files: [new File(['123'], 'asset.png')] },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Add asset' })),
  );
  expect(screen.getByLabelText('primary color')).toHaveValue('#445566');
  expect(f.session.form.getSnapshot().dirtyFields).toEqual(['brand']);
  expect(f.session.form.getSnapshot().dirtySource).not.toBeNull();
});
