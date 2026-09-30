import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { Profiler } from 'react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  ArtifactAuthoring,
  ArtifactEditingState,
  ArtifactPreview as Preview,
  CommandReceipt,
  ResourceView,
} from '../../api/types';
import type { DesignControlsState } from './ArtifactDesignControls';
import ArtifactPreview, { type ArtifactPreviewProps } from './ArtifactPreview';
import {
  createArtifactDesignSessions,
  type DesignSessionOwner,
} from './artifact-design-sessions';
import { designCommandSets } from './design-commands';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function resizeFixture() {
  const observers: {
    notify: () => void;
    disconnect: ReturnType<typeof vi.fn>;
  }[] = [];
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: ResizeObserverCallback) {
        observers.push({
          notify: () => callback([], this as unknown as ResizeObserver),
          disconnect: this.disconnect,
        });
      }
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
    },
  );
  return observers;
}

function snapshot(resource = 'deck-a', index = 0): Preview {
  return {
    resource_id: resource,
    resource_revision: 'resource-1',
    preview_revision: `preview-${resource}-${index}`,
    mode: 'deck',
    page_id: `slide-${index}`,
    page_index: index,
    page_count: 2,
    page_title: index === 0 ? 'Opening' : 'Closing',
    canvas_width: 1920,
    canvas_height: 1080,
    pages: [
      { id: 'slide-0', index: 0, title: 'Opening' },
      { id: 'slide-1', index: 1, title: 'Closing' },
    ],
    html: `<html><body>${resource} slide ${index}</body></html>`,
    unchanged: false,
  };
}

function editing(
  overrides: Partial<ArtifactEditingState> = {},
): ArtifactEditingState {
  return {
    resource_id: 'deck-a',
    resource_revision: 'resource-1',
    mode: 'deck',
    name: 'Launch deck',
    canvas_width: 1920,
    canvas_height: 1080,
    page_id: 'slide-0',
    page_title: 'Opening',
    page_notes: '',
    pages: [{ id: 'slide-0', title: 'Opening', index: 0 }],
    page_count: 2,
    page_next_cursor: null,
    elements: [],
    element_count: 0,
    element_next_cursor: null,
    history: [],
    history_count: 0,
    history_next_cursor: null,
    ...overrides,
  };
}

const receipt = (revision: string): CommandReceipt => ({
  command_id: `command-${revision}`,
  status: 'completed',
  resource_id: 'deck-a',
  resource_revision: revision,
});

it('shows a top bar, a floating dock and a canvas without loading anything else', async () => {
  const load = vi.fn(async () => snapshot());
  const loadEditing = vi.fn(() => new Promise<ArtifactEditingState>(() => {}));
  render(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-1"
      title="Launch deck"
      visible
      load={load}
      loadEditing={loadEditing}
      edit={vi.fn()}
      loadPalette={vi.fn()}
      onDraftText={vi.fn()}
    />,
  );
  await screen.findByTitle('Slide preview: Opening');
  expect(screen.getByRole('textbox', { name: 'Design name' })).toHaveValue(
    'Launch deck',
  );
  const dock = screen.getByRole('toolbar', { name: 'Design preview controls' });
  expect(dock).toHaveClass('design-dock');
  expect(screen.getByRole('radio', { name: 'Preview' })).toBeChecked();
  expect(screen.getByRole('radio', { name: 'Edit' })).not.toBeChecked();
  for (const name of [
    'Previous slide',
    'Next slide',
    'Undo',
    'Redo',
    'Design history',
    'Design properties',
    'More design actions',
  ])
    expect(screen.getByRole('button', { name })).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: /^Slide 1 of 2: Opening/ }),
  ).toBeEnabled();
  expect(screen.getByRole('combobox', { name: 'Preview zoom' })).toHaveValue(
    'fit',
  );
  // A fitted page never scrolls; a zoomed one is reachable by keyboard.
  expect(screen.queryByRole('group', { name: 'Slide canvas' })).toBeNull();
  fireEvent.change(screen.getByRole('combobox', { name: 'Preview zoom' }), {
    target: { value: 'actual' },
  });
  expect(screen.getByRole('group', { name: 'Slide canvas' })).toHaveAttribute(
    'tabindex',
    '0',
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Preview zoom' }), {
    target: { value: 'fit' },
  });
  // Page thumbnails keep the design's own shape (16:9 here).
  expect(
    screen
      .getByRole('navigation', { name: 'Slides' })
      .style.getPropertyValue('--page-aspect'),
  ).toBe('1.7778');
  // A manual refresh exists only on an error card.
  expect(screen.queryByRole('button', { name: /Refresh/ })).toBeNull();
  expect(screen.queryByRole('complementary')).toBeNull();
  expect(loadEditing).not.toHaveBeenCalled();
});

it('switches to Edit with an authoring identity and opens the inspector', async () => {
  const load = vi.fn(async () => snapshot());
  render(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-1"
      visible
      load={load}
      loadEditing={vi.fn(async () => editing())}
      edit={vi.fn()}
    />,
  );
  await screen.findByTitle('Slide preview: Opening');
  const region = screen.getByRole('region', { name: 'Design preview' });
  region.getBoundingClientRect = () => ({ width: 900 }) as DOMRect;
  fireEvent.click(screen.getByRole('radio', { name: 'Edit' }));
  await waitFor(() =>
    expect(load).toHaveBeenCalledWith(
      undefined,
      expect.any(String),
      expect.any(AbortSignal),
      expect.objectContaining({
        previewId: expect.any(String),
        capability: expect.any(String),
      }),
    ),
  );
  expect(screen.getByRole('radio', { name: 'Edit' })).toBeChecked();
  expect(
    screen.getByRole('complementary', { name: 'Design inspector' }),
  ).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'Properties' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  fireEvent.click(screen.getByRole('radio', { name: 'Preview' }));
  expect(
    screen.queryByRole('complementary', { name: 'Design inspector' }),
  ).toBeNull();
  // A narrow panel keeps the canvas clear to select on; the sheet waits.
  region.getBoundingClientRect = () => ({ width: 420 }) as DOMRect;
  fireEvent.click(screen.getByRole('radio', { name: 'Edit' }));
  expect(screen.getByRole('radio', { name: 'Edit' })).toBeChecked();
  expect(
    screen.queryByRole('complementary', { name: 'Design inspector' }),
  ).toBeNull();
});

it('opens the bound Designer palette from the menu and picks a page or draft', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async (pageId?: string) =>
    snapshot('deck-a', pageId === 'slide-1' ? 1 : 0),
  );
  const palette = vi.fn(async (_revision: string, _query: string) => ({
    resource_id: 'deck-a',
    resource_revision: 'resource-1',
    tools_available: true,
    has_more_matches: false,
    items: [
      {
        category: 'tool' as const,
        label: 'Generate notes',
        hint: 'designer_generate_notes',
        identity: 'designer_generate_notes',
        prefill: 'Use designer_generate_notes for the current page',
      },
      {
        category: 'page' as const,
        label: 'Go to: Closing',
        hint: 'page 2',
        identity: 'slide-1',
        prefill: '',
      },
    ],
  }));
  const draft = vi.fn();
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={load}
        loadPalette={palette}
        onDraftText={draft}
      />,
    ),
  );
  expect(palette).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'More design actions' }));
  await user.click(
    screen.getByRole('menuitem', { name: /Search design tools, pages/ }),
  );
  await user.click(
    await screen.findByRole('button', { name: /Go to: Closing/ }),
  );
  await waitFor(() =>
    expect(load.mock.calls.some((call) => call[0] === 'slide-1')).toBe(true),
  );
  await user.click(screen.getByRole('button', { name: 'More design actions' }));
  await user.click(
    screen.getByRole('menuitem', { name: /Search design tools, pages/ }),
  );
  fireEvent.change(
    screen.getByRole('searchbox', {
      name: 'Search design tools, pages, assets',
    }),
    { target: { value: 'notes' } },
  );
  await waitFor(() =>
    expect(palette).toHaveBeenCalledWith(
      'resource-1',
      'notes',
      expect.any(AbortSignal),
    ),
  );
  await user.click(
    await screen.findByRole('button', { name: /Generate notes/ }),
  );
  expect(draft).toHaveBeenCalledWith(
    'Use designer_generate_notes for the current page',
  );
  expect(
    screen.queryByRole('dialog', { name: 'Design command palette' }),
  ).toBeNull();
});

it('renders an opaque iframe and navigates through exact page IDs', async () => {
  const load = vi.fn(async (pageId?: string) =>
    snapshot('deck-a', pageId === 'slide-1' ? 1 : 0),
  );
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="1"
        visible
        load={load}
      />,
    ),
  );
  const frame = screen.getByTitle('Slide preview: Opening');
  expect(frame).toHaveAttribute('sandbox', '');
  expect(frame).toHaveAttribute('srcdoc', snapshot().html);
  expect(screen.getByRole('button', { name: 'Previous slide' })).toBeDisabled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Next slide' })),
  );
  expect(load.mock.calls[1]?.[0]).toBe('slide-1');
  expect(screen.getByTitle('Slide preview: Closing')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Next slide' })).toBeDisabled();
});

it.each([
  ['document', true, ''],
  ['deck', true, ''],
  ['landing', false, ''],
  ['landing', true, 'allow-scripts'],
  ['app_mockup', true, 'allow-scripts'],
  ['storyboard', true, 'allow-scripts'],
] as const)(
  'keeps %s script permission %s in an opaque sandbox',
  async (mode, scripts, sandbox) => {
    const load = vi.fn(async () => ({
      ...snapshot(),
      mode,
      scripts_allowed: scripts,
    }));
    await act(async () =>
      render(
        <ArtifactPreview
          resourceId="deck-a"
          resourceRevision="1"
          visible
          load={load}
        />,
      ),
    );
    const label = mode === 'deck' ? 'Slide' : 'Page';
    expect(screen.getByTitle(`${label} preview: Opening`)).toHaveAttribute(
      'sandbox',
      sandbox,
    );
    expect(
      screen.getByRole('button', { name: new RegExp(`^${label} 1 of 2`) }),
    ).toBeEnabled();
    // Only responsive web designs offer device widths.
    expect(
      screen.queryByRole('radiogroup', { name: 'Device width' }) !== null,
    ).toBe(['landing', 'app_mockup'].includes(mode));
  },
);

it('refreshes itself when the saved revision changes and keeps the same frame when unchanged', async () => {
  const load = vi.fn(async (_pageId?: string, revision?: string) =>
    revision ? { ...snapshot(), html: null, unchanged: true } : snapshot(),
  );
  const props = { resourceId: 'deck-a', load };
  const view = render(
    <ArtifactPreview {...props} resourceRevision="1" visible={false} />,
  );
  expect(load).not.toHaveBeenCalled();
  await act(async () =>
    view.rerender(<ArtifactPreview {...props} resourceRevision="1" visible />),
  );
  const frame = screen.getByTitle('Slide preview: Opening');
  await act(async () =>
    view.rerender(<ArtifactPreview {...props} resourceRevision="2" visible />),
  );
  expect(load.mock.calls[1]?.[1]).toBe(snapshot().preview_revision);
  expect(screen.getByTitle('Slide preview: Opening')).toBe(frame);
  expect(frame).toHaveAttribute('srcdoc', snapshot().html);
  view.rerender(
    <ArtifactPreview {...props} resourceRevision="3" visible={false} />,
  );
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.queryByTitle('Slide preview: Opening')).not.toBeInTheDocument();
});

it('announces an automatic refresh and restores navigation when it settles', async () => {
  let finish!: (value: Preview) => void;
  const load = vi
    .fn()
    .mockResolvedValueOnce(snapshot())
    .mockImplementationOnce(
      () =>
        new Promise<Preview>((resolve) => {
          finish = resolve;
        }),
    );
  const props = { resourceId: 'deck-a', load, visible: true };
  const view = render(
    <ArtifactPreview {...props} resourceRevision="resource-1" />,
  );
  await screen.findByTitle('Slide preview: Opening');
  const preview = screen.getByRole('region', { name: 'Design preview' });
  const previous = screen.getByRole('button', { name: 'Previous slide' });
  const next = screen.getByRole('button', { name: 'Next slide' });
  expect(preview).toHaveAttribute('aria-busy', 'false');
  expect(next).toBeEnabled();
  await act(async () =>
    view.rerender(<ArtifactPreview {...props} resourceRevision="resource-2" />),
  );
  const status = screen.getByText('Updating preview…');
  expect(preview).toHaveAttribute('aria-busy', 'true');
  expect(status).toHaveAttribute('role', 'status');
  expect(previous).toBeDisabled();
  expect(next).toBeDisabled();
  await act(async () => finish({ ...snapshot(), html: null, unchanged: true }));
  expect(preview).toHaveAttribute('aria-busy', 'false');
  expect(screen.queryByText('Updating preview…')).toBeNull();
  expect(previous).toBeDisabled();
  expect(next).toBeEnabled();
});

it('aborts and fences reversed resource responses', async () => {
  let finishA!: (value: Preview) => void;
  let finishB!: (value: Preview) => void;
  const loadA = vi.fn(
    (_page?: string, _revision?: string, _signal?: AbortSignal) =>
      new Promise<Preview>((resolve) => {
        finishA = resolve;
      }),
  );
  const loadB = vi.fn(
    () =>
      new Promise<Preview>((resolve) => {
        finishB = resolve;
      }),
  );
  const view = render(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="1"
      visible
      load={loadA}
    />,
  );
  view.rerender(
    <ArtifactPreview
      resourceId="deck-b"
      resourceRevision="1"
      visible
      load={loadB}
    />,
  );
  expect(loadA.mock.calls[0]?.[2]?.aborted).toBe(true);
  await act(async () => finishB(snapshot('deck-b')));
  await act(async () => finishA(snapshot('deck-a')));
  expect(screen.getByTitle('Slide preview: Opening')).toHaveAttribute(
    'srcdoc',
    snapshot('deck-b').html,
  );
});

it.each([
  [{ code: 'capability_revoked' }, 'Access to this design changed'],
  [
    { status: 403, code: 'resource_binding_revoked' },
    'Access to this design changed. Review its binding before continuing.',
  ],
])(
  'removes a revoked preview and offers one explicit scoped retry (%j)',
  async (reason, message) => {
    const load = vi
      .fn()
      .mockResolvedValueOnce(snapshot())
      .mockRejectedValueOnce(reason)
      .mockResolvedValueOnce(snapshot());
    const props = { resourceId: 'deck-a', load, visible: true };
    const view = render(<ArtifactPreview {...props} resourceRevision="1" />);
    await screen.findByTitle('Slide preview: Opening');
    await act(async () =>
      view.rerender(<ArtifactPreview {...props} resourceRevision="2" />),
    );
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    expect(
      screen.queryByTitle('Slide preview: Opening'),
    ).not.toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(2);
    await act(async () =>
      fireEvent.click(screen.getByRole('button', { name: 'Reload preview' })),
    );
    expect(load.mock.calls[2]?.[1]).toBeUndefined();
    expect(screen.getByTitle('Slide preview: Opening')).toBeVisible();
  },
);

it('rejects mismatched response identity', async () => {
  const load = vi.fn(async () => snapshot('another-design'));
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="1"
        visible
        load={load}
      />,
    ),
  );
  expect(screen.getByRole('alert')).toHaveTextContent('different design');
  expect(screen.queryByTitle('Slide preview: Opening')).not.toBeInTheDocument();
});

it('fits the native isolated canvas to both viewport dimensions without refetching or rebuilding its iframe', async () => {
  const observers = resizeFixture(),
    commits = vi.fn();
  const load = vi.fn(async () => snapshot());
  await act(async () =>
    render(
      <Profiler id="preview" onRender={commits}>
        <ArtifactPreview
          resourceId="deck-a"
          resourceRevision="1"
          visible
          load={load}
        />
      </Profiler>,
    ),
  );
  const frame = screen.getByTitle<HTMLIFrameElement>('Slide preview: Opening'),
    host = frame.parentElement!.parentElement!;
  let width = 756,
    height = 96;
  Object.defineProperty(host, 'clientWidth', { get: () => width });
  Object.defineProperty(host, 'clientHeight', { get: () => height });
  await act(async () => observers[0].notify());
  expect(frame.style.position).toBe('absolute');
  expect(frame.style.width).toBe('1920px');
  expect(frame.style.height).toBe('1080px');
  expect(Number(frame.style.transform.match(/scale\((.+)\)/)?.[1])).toBeCloseTo(
    96 / 1080,
  );
  expect(parseFloat(frame.style.left)).toBeCloseTo(
    (756 - (1920 * 96) / 1080) / 2,
  );
  expect(frame.style.top).toBe('0px');
  expect(host.style.minHeight).toBe('180px');
  expect(host.style.overflow).toBe('clip');
  expect(
    screen.getByRole('toolbar', { name: 'Design preview controls' }),
  ).toContainElement(screen.getByRole('button', { name: /^Slide 1 of 2/ }));
  const count = commits.mock.calls.length;
  await act(async () => {
    for (let index = 0; index < 100; index++) observers[0].notify();
  });
  expect(commits).toHaveBeenCalledTimes(count);
  width = 320;
  height = 640;
  await act(async () => observers[0].notify());
  expect(Number(frame.style.transform.match(/scale\((.+)\)/)?.[1])).toBeCloseTo(
    320 / 1920,
  );
  expect(parseFloat(frame.style.top)).toBeCloseTo(230);
  expect(frame.style.left).toBe('0px');
  expect(screen.getByTitle('Slide preview: Opening')).toBe(frame);
  expect(frame).toHaveAttribute('srcdoc', snapshot().html);
  expect(frame).toHaveAttribute('sandbox', '');
  expect(load).toHaveBeenCalledTimes(1);
});

it('ignores queued measurement callbacks after hiding and unmounting without loading hidden HTML', async () => {
  const observers = resizeFixture(),
    commits = vi.fn(),
    readWidth = vi.fn(() => 756),
    readHeight = vi.fn(() => 96);
  const load = vi.fn(async () => snapshot());
  const element = (visible: boolean) => (
    <Profiler id="preview" onRender={commits}>
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="1"
        visible={visible}
        load={load}
      />
    </Profiler>
  );
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(element(true));
  });
  const host = screen.getByTitle('Slide preview: Opening').parentElement!
    .parentElement!;
  Object.defineProperty(host, 'clientWidth', { get: readWidth });
  Object.defineProperty(host, 'clientHeight', { get: readHeight });
  await act(async () => observers[0].notify());
  await act(async () => view.rerender(element(false)));
  expect(observers[0].disconnect).toHaveBeenCalledOnce();
  readWidth.mockClear();
  readHeight.mockClear();
  const count = commits.mock.calls.length;
  await act(async () => observers[0].notify());
  expect(commits).toHaveBeenCalledTimes(count);
  expect(readWidth).not.toHaveBeenCalled();
  expect(readHeight).not.toHaveBeenCalled();
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => view.rerender(element(true)));
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => observers[0].notify());
  expect(readWidth).not.toHaveBeenCalled();
  view.unmount();
  expect(observers.at(-1)?.disconnect).toHaveBeenCalledOnce();
  await act(async () => observers.at(-1)?.notify());
  expect(load).toHaveBeenCalledTimes(2);
});

it('offers readable zoom levels without replacing the frame or fetching again', async () => {
  const observers = resizeFixture();
  const load = vi.fn(async () => snapshot());
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="1"
        visible
        load={load}
      />,
    ),
  );
  const frame = screen.getByTitle<HTMLIFrameElement>('Slide preview: Opening');
  const host = frame.parentElement!.parentElement!;
  Object.defineProperty(host, 'clientWidth', { value: 480 });
  Object.defineProperty(host, 'clientHeight', { value: 320 });
  await act(async () => observers[0].notify());
  const zoom = screen.getByRole('combobox', { name: 'Preview zoom' });
  expect(zoom).toHaveDisplayValue('Fit · 25%');
  fireEvent.change(zoom, { target: { value: 'actual' } });
  expect(frame.style.transform).toBe('scale(1)');
  expect(host.style.overflow).toBe('auto');
  expect(frame.parentElement!.style.width).toBe('1920px');
  fireEvent.change(zoom, { target: { value: 'width' } });
  expect(frame.style.transform).toBe('scale(0.25)');
  fireEvent.change(zoom, { target: { value: '0.5' } });
  expect(frame.style.transform).toBe('scale(0.5)');
  fireEvent.change(zoom, { target: { value: 'fit' } });
  expect(host.style.overflow).toBe('clip');
  expect(screen.getByTitle('Slide preview: Opening')).toBe(frame);
  expect(load).toHaveBeenCalledTimes(1);
});

it('fits a tall web page to its width and offers device widths', async () => {
  const load = vi.fn(async () => ({
    ...snapshot(),
    mode: 'landing' as const,
    canvas_width: 1440,
    canvas_height: 3200,
  }));
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="1"
        visible
        load={load}
      />,
    ),
  );
  expect(screen.getByRole('combobox', { name: 'Preview zoom' })).toHaveValue(
    'width',
  );
  const frame = screen.getByTitle<HTMLIFrameElement>('Page preview: Opening');
  expect(frame.style.width).toBe('1440px');
  fireEvent.click(screen.getByRole('radio', { name: 'Phone' }));
  expect(frame.style.width).toBe('390px');
  expect(frame.style.height).toBe('844px');
  expect(screen.getByTitle('Page preview: Opening')).toBe(frame);
  expect(load).toHaveBeenCalledTimes(1);
});

it('renames the design once, under the revision on screen', async () => {
  const edit = vi.fn(async () => receipt('resource-2'));
  const load = vi.fn(async () => snapshot());
  const view = await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        title="Launch deck"
        visible
        load={load}
        loadEditing={vi.fn(async () => editing())}
        edit={edit}
      />,
    ),
  );
  const name = screen.getByRole('textbox', { name: 'Design name' });
  fireEvent.change(name, { target: { value: 'Launch deck' } });
  await act(async () => fireEvent.blur(name));
  expect(edit).not.toHaveBeenCalled();
  fireEvent.change(name, { target: { value: '  Spring launch  ' } });
  await act(async () => fireEvent.keyDown(name, { key: 'Enter' }));
  await act(async () => fireEvent.blur(name));
  expect(edit).toHaveBeenCalledTimes(1);
  expect(edit).toHaveBeenCalledWith(
    { operation: 'project_properties', name: 'Spring launch' },
    'resource-1',
  );
  expect(name).toHaveValue('Spring launch');
  expect(screen.getByText('Renamed.')).toBeInTheDocument();
  const props = {
    resourceId: 'deck-a',
    resourceRevision: 'resource-2',
    visible: true,
    load,
    loadEditing: vi.fn(async () => editing()),
    edit,
  };
  // The server's title follows the rename, then an undo takes it back.
  view.rerender(<ArtifactPreview {...props} title="Spring launch" />);
  expect(name).toHaveValue('Spring launch');
  view.rerender(<ArtifactPreview {...props} title="Launch deck" />);
  expect(name).toHaveValue('Launch deck');
});

it('undoes by restoring the newest snapshot and redoes the state it replaced', async () => {
  let revision = 'resource-1';
  const history = [{ id: '100.000001', label: 'Before panel text' }];
  const load = vi.fn(async () => ({
    ...snapshot(),
    resource_revision: revision,
  }));
  const loadEditing = vi.fn(async () =>
    editing({
      resource_revision: revision,
      history: history.map((item) => ({
        ...item,
        author: 'user',
        page_count: 2,
        available: true,
      })),
      history_count: history.length,
    }),
  );
  const edit = vi.fn(async () => {
    revision = revision === 'resource-1' ? 'resource-2' : 'resource-3';
    history.unshift({
      id: revision === 'resource-2' ? '200.000001' : '300.000001',
      label: 'Before panel restore',
    });
    return receipt(revision);
  });
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={load}
        loadEditing={loadEditing}
        edit={edit}
      />,
    ),
  );
  expect(screen.getByRole('button', { name: 'Redo' })).toBeDisabled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Undo' })),
  );
  expect(edit).toHaveBeenCalledWith(
    { operation: 'restore', snapshot_id: '100.000001' },
    'resource-1',
  );
  await screen.findByText('Undone.');
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Redo' })).toBeEnabled(),
  );
  await act(async () =>
    fireEvent.keyDown(screen.getByRole('region', { name: 'Design preview' }), {
      key: 'z',
      ctrlKey: true,
      shiftKey: true,
    }),
  );
  expect(edit).toHaveBeenLastCalledWith(
    { operation: 'restore', snapshot_id: '200.000001' },
    'resource-2',
  );
  await screen.findByText('Redone.');
});

it('says so when there is nothing to undo and changes nothing', async () => {
  const edit = vi.fn();
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={vi.fn(async () => snapshot())}
        loadEditing={vi.fn(async () => editing())}
        edit={edit}
      />,
    ),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Undo' })),
  );
  expect(screen.getByText('Nothing to undo yet.')).toBeInTheDocument();
  expect(edit).not.toHaveBeenCalled();
});

it('opens history, export and sharing as side sheets inside the panel', async () => {
  const lifecycleLoad = vi.fn(
    async (_id: string, resourceRevision: string) => ({
      resource_id: 'deck-a',
      resource_revision: resourceRevision,
      mode: 'deck' as const,
      page_count: 2,
      capabilities: (['export.html', 'share.channel'] as const).map((id) => ({
        id,
        label: id,
        state: 'ready' as const,
        detail: '',
        review_required: id === 'share.channel',
      })),
    }),
  );
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={vi.fn(async () => snapshot())}
        loadEditing={vi.fn(async () => editing())}
        edit={vi.fn()}
        lifecycle={{ load: lifecycleLoad }}
        createExport={vi.fn()}
        downloadExport={vi.fn()}
        sharing={{
          prepare: vi.fn(),
          execute: vi.fn(),
          loadChannels: vi
            .fn()
            .mockResolvedValue({ items: [], next_cursor: null }),
        }}
      />,
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Design history' }));
  expect(screen.getByRole('tab', { name: 'History' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await screen.findByRole('region', { name: 'Design history' });
  fireEvent.click(screen.getByRole('button', { name: 'Export' }));
  const preview = screen.getByRole('region', { name: 'Design preview' });
  expect(
    screen.getByRole('complementary', { name: 'Export design' }),
  ).toBeInTheDocument();
  expect(preview).toContainElement(
    screen.getByRole('region', { name: 'Design export' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Share' }));
  expect(
    await screen.findByRole('region', { name: 'Design sharing' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Design export' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Close sharing' }));
  expect(screen.queryByRole('complementary')).toBeNull();
  expect(
    screen.getByRole('button', { name: 'Design capabilities' }),
  ).toBeInTheDocument();
});

it('waits for the newly saved preview revision before allowing presentation', async () => {
  let finish!: (value: Preview) => void;
  const load = vi
    .fn()
    .mockResolvedValueOnce(snapshot())
    .mockImplementationOnce(
      () =>
        new Promise<Preview>((resolve) => {
          finish = resolve;
        }),
    );
  const presentation = { load: vi.fn(), preview: vi.fn() };
  const view = render(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-1"
      visible
      load={load}
      presentation={presentation}
    />,
  );
  await act(async () => {});
  expect(screen.getByRole('button', { name: 'Present' })).toBeEnabled();
  view.rerender(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-2"
      visible
      load={load}
      presentation={presentation}
    />,
  );
  expect(screen.getByRole('button', { name: 'Present' })).toBeDisabled();
  await act(async () =>
    finish({ ...snapshot(), resource_revision: 'resource-2' }),
  );
  expect(screen.getByRole('button', { name: 'Present' })).toBeEnabled();
  expect(presentation.load).not.toHaveBeenCalled();
});

it('presents at once from the current page and returns to the canvas when it ends', async () => {
  resizeFixture();
  const presentation = {
    load: vi.fn(async () => ({
      resource_id: 'deck-a',
      resource_revision: 'resource-1',
      page_id: 'slide-0',
      title: 'Opening',
      notes: '',
      page_index: 0,
      page_count: 2,
      pages: [],
      next_cursor: null,
    })),
    preview: vi.fn(() => new Promise<Preview>(() => {})),
  };
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={vi.fn(async () => snapshot())}
        presentation={presentation}
      />,
    ),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Present' })),
  );
  expect(presentation.load).toHaveBeenCalledWith(
    { page_index: 0, cursor: undefined, limit: 25 },
    expect.any(AbortSignal),
  );
  expect(
    screen.getByRole('heading', { name: 'Presentation' }),
  ).toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'End presentation' })),
  );
  expect(screen.getByTitle('Slide preview: Opening')).toBeInTheDocument();
});

it('reads capabilities only for the saved revision the preview shows', async () => {
  let finish!: (value: Preview) => void;
  const load = vi
    .fn()
    .mockResolvedValueOnce(snapshot())
    .mockImplementationOnce(
      () =>
        new Promise<Preview>((resolve) => {
          finish = resolve;
        }),
    );
  const lifecycleLoad = vi.fn(
    async (_resourceId: string, resourceRevision: string) => ({
      resource_id: 'deck-a',
      resource_revision: resourceRevision,
      mode: 'deck' as const,
      page_count: 2,
      capabilities: [],
    }),
  );
  const props = {
    resourceId: 'deck-a',
    load,
    lifecycle: { load: lifecycleLoad },
  };
  const view = render(
    <ArtifactPreview {...props} resourceRevision="resource-1" visible />,
  );
  await waitFor(() =>
    expect(lifecycleLoad).toHaveBeenCalledWith(
      'deck-a',
      'resource-1',
      expect.any(AbortSignal),
    ),
  );
  view.rerender(
    <ArtifactPreview
      {...props}
      resourceRevision="resource-1"
      visible={false}
    />,
  );
  lifecycleLoad.mockClear();
  view.rerender(
    <ArtifactPreview {...props} resourceRevision="resource-2" visible />,
  );
  await act(async () => {});
  expect(lifecycleLoad).not.toHaveBeenCalled();
  await act(async () =>
    finish({ ...snapshot(), resource_revision: 'resource-2' }),
  );
  await waitFor(() =>
    expect(lifecycleLoad).toHaveBeenCalledWith(
      'deck-a',
      'resource-2',
      expect.any(AbortSignal),
    ),
  );
});

function bridgeEvent(
  frame: HTMLIFrameElement,
  identity: ArtifactAuthoring,
  data: Record<string, unknown>,
) {
  return new MessageEvent('message', {
    source: frame.contentWindow,
    origin: 'null',
    data: { ...identity, revision: snapshot().preview_revision, ...data },
  });
}

it('selects an element on the canvas and hands a request about it to the chat', async () => {
  const load = vi.fn<ArtifactPreviewProps['load']>(async () => snapshot());
  const onAsk = vi.fn(() => 'sent' as const);
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={load}
        loadEditing={vi.fn(async () => editing())}
        edit={vi.fn()}
        onAsk={onAsk}
      />,
    ),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Edit' })),
  );
  const identity = load.mock.calls.at(-1)![3]!;
  const frame = screen.getByTitle<HTMLIFrameElement>('Slide preview: Opening');
  await act(async () =>
    window.dispatchEvent(
      bridgeEvent(frame, identity, {
        type: 'element-click',
        detail: {
          tag: 'h1',
          text: 'Launch day',
          elementId: '',
          xpath: '/html/body/h1[1]',
          rect: { x: 40, y: 60, w: 400, h: 80 },
        },
      }),
    ),
  );
  const ask = screen.getByRole('textbox', {
    name: 'Ask Row-Bot to change this',
  });
  expect(
    screen.getByRole('group', { name: 'Selected heading' }),
  ).toBeInTheDocument();
  fireEvent.change(ask, { target: { value: 'Make it shorter' } });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Send to Row-Bot' })),
  );
  expect(onAsk).toHaveBeenCalledWith(
    'In this design, on slide 1 (“Opening”), change the heading that reads “Launch day”: Make it shorter',
  );
  expect(screen.getByText(/Row-Bot is on it in the chat/)).toBeInTheDocument();
  // A message from any other frame or with a stale identity is ignored.
  await act(async () =>
    window.dispatchEvent(
      new MessageEvent('message', {
        source: window,
        origin: 'null',
        data: { ...identity, type: 'designer-undo-shortcut' },
      }),
    ),
  );
  expect(
    screen.getByRole('textbox', { name: 'Ask Row-Bot to change this' }),
  ).toBeInTheDocument();
});

it('puts the request in the draft when the chat cannot take it', async () => {
  const load = vi.fn<ArtifactPreviewProps['load']>(async () => snapshot());
  const draft = vi.fn();
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={load}
        loadEditing={vi.fn(async () => editing())}
        edit={vi.fn()}
        onAsk={() => 'unavailable'}
        onDraftText={draft}
      />,
    ),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Edit' })),
  );
  const identity = load.mock.calls.at(-1)![3]!;
  const frame = screen.getByTitle<HTMLIFrameElement>('Slide preview: Opening');
  await act(async () =>
    window.dispatchEvent(
      bridgeEvent(frame, identity, {
        type: 'element-click',
        detail: {
          tag: 'img',
          text: '',
          elementId: '',
          xpath: '/html/body/img[1]',
        },
      }),
    ),
  );
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Ask Row-Bot to change this' }),
    { target: { value: 'Use a warmer photo' } },
  );
  await act(async () =>
    fireEvent.submit(screen.getByRole('group', { name: 'Selected image' })),
  );
  expect(draft).toHaveBeenCalledWith(
    'In this design, on slide 1 (“Opening”), change the selected image (img): Use a warmer photo',
  );
  expect(screen.getByText(/Added to your message/)).toBeInTheDocument();
});

it('clears a selection an edit took away, quietly, in both inspector panels', async () => {
  const gone = 'a'.repeat(64);
  const load = vi.fn<ArtifactPreviewProps['load']>(async () => snapshot());
  const loadEditing = vi.fn(async (options: { elementId?: string }) => {
    if (options.elementId === gone) throw { code: 'element_unavailable' };
    return editing();
  });
  const controls = vi.fn<DesignSessionOwner['load']>(
    async (_scope, options) => {
      if (options.element_id === gone) throw { code: 'element_unavailable' };
      return designControls();
    },
  );
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={load}
        loadEditing={loadEditing}
        edit={vi.fn(async () => receipt('resource-2'))}
        design={{ session: designSession(controls), onDraftText: vi.fn() }}
      />,
    ),
  );
  const region = screen.getByRole('region', { name: 'Design preview' });
  region.getBoundingClientRect = () => ({ width: 900 }) as DOMRect;
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Edit' })),
  );
  await screen.findByRole('region', { name: 'Brand' });
  const identity = load.mock.calls.at(-1)![3]!;
  const frame = screen.getByTitle<HTMLIFrameElement>('Slide preview: Opening');
  // An inline edit names an element that the saved design no longer has.
  await act(async () =>
    window.dispatchEvent(
      bridgeEvent(frame, identity, {
        type: 'text-edit',
        detail: {
          newText: 'kSALE',
          elementInfo: { tag: 'h1', elementId: gone },
        },
      }),
    ),
  );
  expect(await screen.findByText('Selection cleared.')).toBeInTheDocument();
  await waitFor(() =>
    expect(loadEditing).toHaveBeenLastCalledWith(
      { pageId: 'slide-0', elementId: undefined },
      expect.any(AbortSignal),
    ),
  );
  expect(controls).toHaveBeenLastCalledWith(
    expect.anything(),
    { page_id: 'slide-0', element_id: undefined, section: 'elements' },
    expect.any(AbortSignal),
  );
  expect(
    await screen.findByRole('region', { name: 'Brand' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('alert')).toBeNull();
});

function designControls(): DesignControlsState {
  return {
    resource_id: 'deck-a',
    resource_revision: 'resource-1',
    mode: 'deck',
    page_id: 'slide-0',
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
    element: null,
    section: 'elements',
    items: [],
    item_count: 0,
    next_cursor: null,
  };
}

function designSession(load: DesignSessionOwner['load']) {
  const resource = {
    available: true,
    resource_revision: 'resource-1',
    binding: {
      kind: 'artifact',
      resource_id: 'deck-a',
      binding_id: 'binding',
      revision: 'b1',
    },
  } as ResourceView;
  const owner: DesignSessionOwner = {
    getSnapshot: () => ({
      identity: 'auth',
      conversationId: 'chat',
      conversationRevision: '1',
      loading: false,
      resources: [resource],
    }),
    subscribe: () => () => undefined,
    load,
    assetThumbnail: vi.fn(),
    review: vi.fn(),
    draftFix: vi.fn(),
    stageUpload: vi.fn(),
    presetReview: vi.fn(),
    execute: vi.fn(),
    receipt: vi.fn(),
  };
  return createArtifactDesignSessions(owner).get('chat', resource);
}

it('runs the canvas undo shortcut through the same reviewed restore', async () => {
  const load = vi.fn<ArtifactPreviewProps['load']>(async () => snapshot());
  const edit = vi.fn(async () => receipt('resource-2'));
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={load}
        loadEditing={vi.fn(async () =>
          editing({
            history: [
              {
                id: '100.1',
                label: 'Before panel text',
                author: 'user',
                page_count: 2,
                available: true,
              },
            ],
            history_count: 1,
          }),
        )}
        edit={edit}
      />,
    ),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Edit' })),
  );
  const identity = load.mock.calls.at(-1)![3]!;
  const frame = screen.getByTitle<HTMLIFrameElement>('Slide preview: Opening');
  await act(async () =>
    window.dispatchEvent(
      bridgeEvent(frame, identity, { type: 'designer-undo-shortcut' }),
    ),
  );
  await waitFor(() =>
    expect(edit).toHaveBeenCalledWith(
      { operation: 'restore', snapshot_id: '100.1' },
      'resource-1',
    ),
  );
});

function renderDeck(edit = vi.fn(async () => receipt('resource-2'))) {
  const load = vi.fn(async (pageId?: string) =>
    snapshot('deck-a', pageId === 'slide-1' ? 1 : 0),
  );
  render(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-1"
      title="Launch deck"
      visible
      load={load}
      loadEditing={vi.fn(async () => editing())}
      edit={edit}
    />,
  );
  return { edit, load };
}

it('adds a slide after the one shown and opens it', async () => {
  const user = userEvent.setup();
  const { edit, load } = renderDeck();
  await user.click(
    await screen.findByRole('button', { name: /^Slide 1 of 2: Opening/ }),
  );
  await user.click(
    screen.getByRole('menuitem', { name: 'Add a slide after this one' }),
  );
  await waitFor(() =>
    expect(edit).toHaveBeenCalledWith(
      { operation: 'page_add', page_id: 'slide-0' },
      'resource-1',
    ),
  );
  expect(await screen.findByText('Added a slide.')).toBeInTheDocument();
  // The server selects the new slide: the preview reloads without a page.
  await waitFor(() => expect(load.mock.calls.length).toBeGreaterThan(1));
  expect(load.mock.lastCall?.[0]).toBeUndefined();
});

it('deletes the slide shown, moves to its neighbour and offers Undo', async () => {
  const user = userEvent.setup();
  const { edit, load } = renderDeck();
  await user.click(
    await screen.findByRole('button', { name: /^Slide 1 of 2: Opening/ }),
  );
  await user.click(screen.getByRole('menuitem', { name: 'Delete this slide' }));
  await waitFor(() =>
    expect(edit).toHaveBeenCalledWith(
      { operation: 'page_delete', page_id: 'slide-0' },
      'resource-1',
    ),
  );
  const status = await screen.findByText('Deleted “Opening”.');
  expect(status).toBeInTheDocument();
  await waitFor(() => expect(load.mock.lastCall?.[0]).toBe('slide-1'));
  expect(
    screen.getByRole('button', { name: 'Undo this change' }),
  ).toBeInTheDocument();
});

it('offers no delete for the only slide', async () => {
  const user = userEvent.setup();
  const only = { ...snapshot(), page_count: 1, pages: [snapshot().pages[0]] };
  render(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-1"
      title="Launch deck"
      visible
      load={vi.fn(async () => only)}
      loadEditing={vi.fn(async () => editing())}
      edit={vi.fn()}
    />,
  );
  await user.click(
    await screen.findByRole('button', { name: /^Slide 1 of 1: Opening/ }),
  );
  expect(
    screen.getByRole('menuitem', { name: 'Add a slide after this one' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('menuitem', { name: 'Delete this slide' }),
  ).toBeNull();
});

it('changes the size from the dock and re-fits every slide', async () => {
  const user = userEvent.setup();
  const { edit } = renderDeck();
  const trigger = await screen.findByRole('button', {
    name: 'Size: 16:9. Change the size',
  });
  await user.click(trigger);
  expect(
    screen.getByRole('menuitem', { name: '16:9 · Widescreen' }),
  ).toHaveAttribute('aria-current', 'true');
  await user.click(screen.getByRole('menuitem', { name: '4:3 · Standard' }));
  await waitFor(() =>
    expect(edit).toHaveBeenCalledWith(
      { operation: 'canvas_size', aspect_ratio: '4:3' },
      'resource-1',
    ),
  );
  expect(
    await screen.findByText(
      'Changed the size to 4:3. Every slide was re-fitted.',
    ),
  ).toBeInTheDocument();
});

it('keeps the size menu off landing pages and read-only previews', async () => {
  render(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-1"
      title="Launch deck"
      visible
      load={vi.fn(async () => snapshot())}
    />,
  );
  await screen.findByTitle('Slide preview: Opening');
  expect(screen.queryByRole('button', { name: /^Size:/ })).toBeNull();
});

it('presents full screen and ends when the person leaves full screen', async () => {
  resizeFixture();
  let fullscreenElement: Element | null = null;
  Object.defineProperty(document, 'fullscreenElement', {
    configurable: true,
    get: () => fullscreenElement,
  });
  const requestFullscreen = vi.fn(async function (this: Element) {
    fullscreenElement = document.querySelector('.design-stage');
    expect(this).toBe(fullscreenElement);
    document.dispatchEvent(new Event('fullscreenchange'));
  });
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
    configurable: true,
    value: requestFullscreen,
  });
  const presentation = {
    load: vi.fn(async () => ({
      resource_id: 'deck-a',
      resource_revision: 'resource-1',
      page_id: 'slide-0',
      title: 'Opening',
      notes: '',
      page_index: 0,
      page_count: 2,
      pages: [],
      next_cursor: null,
    })),
    preview: vi.fn(() => new Promise<Preview>(() => {})),
  };
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={vi.fn(async () => snapshot())}
        presentation={presentation}
      />,
    ),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Present' })),
  );
  expect(requestFullscreen).toHaveBeenCalledOnce();
  expect(requestFullscreen.mock.contexts[0]).toHaveClass('design-stage');
  await screen.findByRole('button', { name: 'End presentation' });
  // Already full screen: no second Fullscreen button.
  expect(screen.queryByRole('button', { name: 'Fullscreen' })).toBeNull();
  // Escape in full screen is the browser's: it leaves full screen.
  await act(async () => {
    fullscreenElement = null;
    document.dispatchEvent(new Event('fullscreenchange'));
  });
  expect(screen.queryByRole('heading', { name: 'Presentation' })).toBeNull();
  expect(screen.getByTitle('Slide preview: Opening')).toBeInTheDocument();
  delete (HTMLElement.prototype as { requestFullscreen?: unknown })
    .requestFullscreen;
  delete (document as { fullscreenElement?: unknown }).fullscreenElement;
});

it('duplicates the design from the menu and says where the copy is', async () => {
  const user = userEvent.setup();
  const duplicate = vi.fn(async () => {});
  render(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-1"
      title="Launch deck"
      visible
      load={vi.fn(async () => snapshot())}
      duplicate={duplicate}
    />,
  );
  await screen.findByTitle('Slide preview: Opening');
  await user.click(screen.getByRole('button', { name: 'More design actions' }));
  await user.click(screen.getByRole('menuitem', { name: 'Duplicate design' }));
  expect(duplicate).toHaveBeenCalledOnce();
  expect(
    await screen.findByText('Made a copy. It opens beside this design.'),
  ).toBeInTheDocument();
  duplicate.mockRejectedValueOnce({ code: 'resource_revision_conflict' });
  await user.click(screen.getByRole('button', { name: 'More design actions' }));
  await user.click(screen.getByRole('menuitem', { name: 'Duplicate design' }));
  expect(
    await screen.findByText('The copy was not made. This design is unchanged.'),
  ).toBeInTheDocument();
});

it('says what a drafting turn is doing and refreshes the page per saved step', async () => {
  const load = vi.fn(async () => snapshot());
  const base = {
    resourceId: 'deck-a',
    resourceRevision: 'resource-1',
    title: 'Launch deck',
    visible: true,
    load,
  };
  const view = render(<ArtifactPreview {...base} />);
  await screen.findByTitle('Slide preview: Opening');
  const before = load.mock.calls.length;
  view.rerender(
    <ArtifactPreview
      {...base}
      drafting={{ key: 'p:0:add', label: 'Adding pages' }}
    />,
  );
  expect(
    await screen.findByText('Drafting · Adding pages…'),
  ).toBeInTheDocument();
  await waitFor(() => expect(load.mock.calls.length).toBe(before + 1));
  view.rerender(
    <ArtifactPreview
      {...base}
      drafting={{ key: 'p:1:add', label: 'Adding pages' }}
    />,
  );
  await waitFor(() => expect(load.mock.calls.length).toBe(before + 2));
  view.rerender(<ArtifactPreview {...base} drafting={null} />);
  expect(screen.queryByText(/Drafting/)).toBeNull();
});

it('offers the actions of the shown design to the global palette while visible', async () => {
  const user = userEvent.setup();
  const duplicate = vi.fn(async () => {});
  const view = render(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-1"
      title="Launch deck"
      visible
      load={vi.fn(async () => snapshot())}
      loadEditing={vi.fn(async () => editing())}
      edit={vi.fn(async () => receipt('resource-2'))}
      duplicate={duplicate}
    />,
  );
  await screen.findByTitle('Slide preview: Opening');
  const set = designCommandSets().find((item) => item.resourceId === 'deck-a');
  expect(set?.commands.map((command) => command.label)).toEqual([
    'Add a slide to Launch deck',
    'Duplicate Launch deck',
    'Launch deck: versions',
  ]);
  await act(async () =>
    set!.commands.find((command) => command.id === 'duplicate')!.run(),
  );
  expect(duplicate).toHaveBeenCalledOnce();
  await user.click(screen.getByRole('button', { name: 'Design history' }));
  view.rerender(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-1"
      title="Launch deck"
      visible={false}
      load={vi.fn(async () => snapshot())}
    />,
  );
  expect(designCommandSets()).toEqual([]);
});

it('hands focus back to Present only once full screen has ended', async () => {
  resizeFixture();
  let fullscreenElement: Element | null = null;
  Object.defineProperty(document, 'fullscreenElement', {
    configurable: true,
    get: () => fullscreenElement,
  });
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
    configurable: true,
    value: vi.fn(async () => {
      fullscreenElement = document.querySelector('.design-stage');
      document.dispatchEvent(new Event('fullscreenchange'));
    }),
  });
  let exited = () => {};
  Object.defineProperty(document, 'exitFullscreen', {
    configurable: true,
    value: vi.fn(
      () =>
        new Promise<void>((resolve) => {
          exited = () => {
            fullscreenElement = null;
            document.dispatchEvent(new Event('fullscreenchange'));
            resolve();
          };
        }),
    ),
  });
  const presentation = {
    load: vi.fn(async () => ({
      resource_id: 'deck-a',
      resource_revision: 'resource-1',
      page_id: 'slide-0',
      title: 'Opening',
      notes: '',
      page_index: 0,
      page_count: 2,
      pages: [],
      next_cursor: null,
    })),
    preview: vi.fn(() => new Promise<Preview>(() => {})),
  };
  await act(async () =>
    render(
      <ArtifactPreview
        resourceId="deck-a"
        resourceRevision="resource-1"
        visible
        load={vi.fn(async () => snapshot())}
        presentation={presentation}
      />,
    ),
  );
  const present = screen.getByRole('button', { name: 'Present' });
  await act(async () => fireEvent.click(present));
  await screen.findByRole('button', { name: 'End presentation' });
  // Escape inside the presentation ends it; the browser leaves full screen
  // a moment later, and only then can Present take the focus back.
  await act(async () =>
    fireEvent.keyDown(screen.getByRole('group', { name: 'Presentation' }), {
      key: 'Escape',
    }),
  );
  expect(document.exitFullscreen).toHaveBeenCalledOnce();
  await act(async () => {
    await new Promise((resolve) => requestAnimationFrame(resolve));
  });
  expect(present).not.toHaveFocus();
  await act(async () => {
    exited();
    await new Promise((resolve) => requestAnimationFrame(resolve));
    await new Promise((resolve) => requestAnimationFrame(resolve));
  });
  expect(present).toHaveFocus();
  delete (HTMLElement.prototype as { requestFullscreen?: unknown })
    .requestFullscreen;
  delete (document as { fullscreenElement?: unknown }).fullscreenElement;
  delete (document as { exitFullscreen?: unknown }).exitFullscreen;
});
