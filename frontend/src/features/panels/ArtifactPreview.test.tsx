import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { Profiler } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ArtifactPreview as Preview } from '../../api/types';
import ArtifactPreview from './ArtifactPreview';

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
    expect(screen.getByRole('combobox', { name: label })).toBeEnabled();
  },
);

it('does no hidden loading and retains exact HTML on unchanged refresh', async () => {
  const load = vi.fn(async (_pageId?: string, revision?: string) =>
    revision ? { ...snapshot(), html: null, unchanged: true } : snapshot(),
  );
  const props = { resourceId: 'deck-a', resourceRevision: '1', load };
  const view = render(<ArtifactPreview {...props} visible={false} />);
  expect(load).not.toHaveBeenCalled();
  await act(async () => view.rerender(<ArtifactPreview {...props} visible />));
  const frame = screen.getByTitle('Slide preview: Opening');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' })),
  );
  expect(load.mock.calls[1]?.[1]).toBe(snapshot().preview_revision);
  expect(screen.getByTitle('Slide preview: Opening')).toBe(frame);
  expect(frame).toHaveAttribute('srcdoc', snapshot().html);
  view.rerender(
    <ArtifactPreview {...props} resourceRevision="2" visible={false} />,
  );
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.queryByTitle('Slide preview: Opening')).not.toBeInTheDocument();
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

it('clears revoked preview and retries reads without creating anything', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(snapshot())
    .mockRejectedValueOnce({ code: 'capability_revoked' })
    .mockResolvedValueOnce(snapshot());
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
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' })),
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Access to this design changed',
  );
  expect(screen.queryByTitle('Slide preview: Opening')).not.toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Reload preview' })),
  );
  expect(screen.getByTitle('Slide preview: Opening')).toBeVisible();
  expect(load.mock.calls[2]?.[1]).toBeUndefined();
});

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
  expect(host.style.minHeight).toBe('96px');
  expect(host.style.overflow).toBe('clip');
  expect(screen.getByRole('group', { name: 'Slide navigation' })).toHaveStyle({
    display: 'flex',
    flexWrap: 'wrap',
  });
  expect(screen.getByRole('combobox', { name: 'Slide' })).toHaveStyle({
    width: 'auto',
  });
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

it('offers readable actual-size and width zoom without replacing the frame or fetching again', async () => {
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
  fireEvent.change(screen.getByRole('combobox', { name: 'Preview zoom' }), {
    target: { value: 'actual' },
  });
  expect(frame.style.transform).toBe('scale(1)');
  expect(host.style.overflow).toBe('auto');
  expect(frame.parentElement!.style.width).toBe('1920px');
  fireEvent.change(screen.getByRole('combobox', { name: 'Preview zoom' }), {
    target: { value: 'width' },
  });
  expect(frame.style.transform).toBe('scale(0.25)');
  fireEvent.change(screen.getByRole('combobox', { name: 'Preview zoom' }), {
    target: { value: 'fit' },
  });
  expect(host.style.overflow).toBe('clip');
  expect(screen.getByTitle('Slide preview: Opening')).toBe(frame);
  expect(load).toHaveBeenCalledTimes(1);
});

it('removes cached private preview after binding revocation and offers an explicit scoped retry', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(snapshot())
    .mockRejectedValueOnce({ status: 403, code: 'resource_binding_revoked' })
    .mockResolvedValueOnce(snapshot());
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
  expect(screen.getByTitle('Slide preview: Opening')).toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' })),
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Access to this design changed. Review its binding before continuing.',
  );
  expect(screen.queryByTitle('Slide preview: Opening')).not.toBeInTheDocument();
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Reload preview' })),
  );
  expect(load.mock.calls[2][1]).toBeUndefined();
  expect(screen.getByTitle('Slide preview: Opening')).toBeInTheDocument();
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
  const button = await screen.findByRole('button', { name: 'Present design' });
  await act(async () => {});
  expect(button).toBeEnabled();
  view.rerender(
    <ArtifactPreview
      resourceId="deck-a"
      resourceRevision="resource-2"
      visible
      load={load}
      presentation={presentation}
    />,
  );
  expect(button).toBeDisabled();
  await act(async () =>
    finish({ ...snapshot(), resource_revision: 'resource-2' }),
  );
  expect(button).toBeEnabled();
  expect(presentation.load).not.toHaveBeenCalled();
});

it('does not remount lifecycle controls against a stale preview revision', async () => {
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
    presentation: { load: vi.fn(), preview: vi.fn() },
    createExport: vi.fn(),
    downloadExport: vi.fn(),
    sharing: {
      prepare: vi.fn(),
      execute: vi.fn(),
      loadChannels: vi.fn(),
    },
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
  const frame = screen.getByTitle('Slide preview: Opening');
  const lifecycle = await screen.findByRole('region', {
    name: 'Design lifecycle',
  });
  expect(
    frame.compareDocumentPosition(lifecycle) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
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
