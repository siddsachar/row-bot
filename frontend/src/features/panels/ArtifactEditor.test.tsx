import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ArtifactEditingState, CommandReceipt } from '../../api/types';
import ArtifactEditor, { type ArtifactEditorProps } from './ArtifactEditor';

function view(
  overrides: Partial<ArtifactEditingState> = {},
): ArtifactEditingState {
  return {
    resource_id: 'design-a',
    resource_revision: 'r1',
    mode: 'deck',
    name: 'Saved design',
    canvas_width: 1920,
    canvas_height: 1080,
    page_id: 'page-a',
    page_title: 'Opening',
    page_notes: 'Saved notes',
    pages: [{ id: 'page-a', title: 'Opening', index: 0 }],
    page_count: 1,
    page_next_cursor: null,
    elements: [
      { id: 'element-a', tag: 'h1', text: 'Original text', editable: true },
    ],
    element_count: 1,
    element_next_cursor: null,
    history: [],
    history_count: 0,
    history_next_cursor: null,
    ...overrides,
  };
}
function props(
  overrides: Partial<ArtifactEditorProps> = {},
): ArtifactEditorProps {
  return {
    resourceId: 'design-a',
    resourceRevision: 'r1',
    visible: true,
    onPageChange: vi.fn(),
    load: vi.fn(async () => view()),
    edit: vi.fn(async (): Promise<CommandReceipt> => ({
      command_id: 'cmd',
      status: 'completed',
      resource_id: 'design-a',
      resource_revision: 'r2',
    })),
    ...overrides,
  };
}

it('reads properties without submitting a mutation or enabling authoring', async () => {
  const current = props({ onAuthoringChange: vi.fn() });
  await act(async () => render(<ArtifactEditor {...current} />));
  expect(screen.getByLabelText('Design name')).toHaveValue('Saved design');
  expect(screen.getByLabelText('Page notes')).toHaveValue('Saved notes');
  expect(current.edit).not.toHaveBeenCalled();
  expect(current.onAuthoringChange).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Edit in preview' }),
  ).toHaveAttribute('aria-pressed', 'false');
});

it('submits exact captured resource revision and plain text only after explicit Apply', async () => {
  const current = props();
  await act(async () => render(<ArtifactEditor {...current} />));
  fireEvent.change(screen.getByLabelText('Element text'), {
    target: { value: '<img onerror="bad()">' },
  });
  expect(current.edit).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Apply text edit' })),
  );
  expect(current.edit).toHaveBeenCalledWith(
    {
      operation: 'text',
      page_id: 'page-a',
      element_id: 'element-a',
      text: '<img onerror="bad()">',
    },
    'r1',
  );
});

it('keeps a draft through failure and disables stale resubmission after refresh', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(view())
    .mockResolvedValue(
      view({ resource_revision: 'r2', page_notes: 'A concurrent saved note' }),
    );
  const current = props({
    load,
    edit: vi.fn().mockRejectedValue({ code: 'resource_revision_conflict' }),
  });
  await act(async () => render(<ArtifactEditor {...current} />));
  fireEvent.change(screen.getByLabelText('Page notes'), {
    target: { value: 'My unsaved note' },
  });
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Save page properties' }),
    ),
  );
  expect(screen.getByLabelText('Page notes')).toHaveValue('My unsaved note');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Refresh properties' })),
  );
  expect(screen.getByLabelText('Page notes')).toHaveValue('My unsaved note');
  expect(
    screen.getByRole('button', { name: 'Save page properties' }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Reload saved values' }));
  expect(screen.getByLabelText('Page notes')).toHaveValue(
    'A concurrent saved note',
  );
});

it('clears revoked saved content and cannot send another edit', async () => {
  const current = props({
    edit: vi.fn().mockRejectedValue({ code: 'resource_binding_revoked' }),
  });
  await act(async () => render(<ArtifactEditor {...current} />));
  fireEvent.change(screen.getByLabelText('Design name'), {
    target: { value: 'My name' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save design name' })),
  );
  expect(screen.queryByLabelText('Design name')).not.toBeInTheDocument();
  expect(screen.queryByText('Original text')).not.toBeInTheDocument();
  expect(current.edit).toHaveBeenCalledTimes(1);
});

it('ignores a delayed result after the resource is replaced', async () => {
  let resolve!: (value: ArtifactEditingState) => void;
  const first = new Promise<ArtifactEditingState>((done) => {
    resolve = done;
  });
  const load = vi
    .fn()
    .mockReturnValueOnce(first)
    .mockResolvedValue(view({ resource_id: 'design-b', name: 'Other design' }));
  const current = props({ load });
  const rendered = render(<ArtifactEditor {...current} />);
  await act(async () =>
    rendered.rerender(<ArtifactEditor {...current} resourceId="design-b" />),
  );
  await act(async () => resolve(view()));
  expect(screen.getByLabelText('Design name')).toHaveValue('Other design');
});

it('follows page, element and history continuation cursors without duplicate rows', async () => {
  const initial = view({
    page_count: 2,
    page_next_cursor: 'page-cursor',
    element_count: 2,
    element_next_cursor: 'element-cursor',
    history_count: 2,
    history: [
      {
        id: '12.1',
        label: 'First',
        author: 'user',
        page_count: 1,
        available: true,
      },
    ],
    history_next_cursor: 'history-cursor',
  });
  const load = vi.fn(async (options) =>
    options.pageCursor
      ? view({
          pages: [{ id: 'page-b', title: 'Closing', index: 1 }],
          page_count: 2,
        })
      : options.elementCursor
        ? view({
            elements: [
              {
                id: 'element-b',
                tag: 'p',
                text: 'Second text',
                editable: true,
              },
            ],
            element_count: 2,
          })
        : options.historyCursor
          ? view({
              history: [
                {
                  id: '12.2',
                  label: 'Second',
                  author: 'agent',
                  page_count: 1,
                  available: true,
                },
              ],
              history_count: 2,
            })
          : initial,
  );
  const current = props({ load });
  await act(async () => render(<ArtifactEditor {...current} />));
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Load more elements' })),
  );
  expect(
    screen.getByRole('option', { name: 'p: Second text' }),
  ).toBeInTheDocument();
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'Pages' }), {
    button: 0,
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Load more pages' })),
  );
  fireEvent.click(screen.getByRole('button', { name: '2. Closing' }));
  expect(current.onPageChange).toHaveBeenCalledWith('page-b');
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'History' }), {
    button: 0,
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Load more history' })),
  );
  expect(screen.getByRole('button', { name: 'Restore Second' })).toBeEnabled();
  expect(load.mock.calls.map(([options]) => options)).toEqual([
    { pageId: undefined, elementId: undefined },
    { pageId: 'page-a', elementCursor: 'element-cursor' },
    { pageId: 'page-a', pageCursor: 'page-cursor' },
    { pageId: 'page-a', historyCursor: 'history-cursor' },
  ]);
});

it('requests the exact selected element and disables an oversized source', async () => {
  const current = props({
    selectedElementId: 'large-element',
    load: vi.fn(async () =>
      view({
        elements: [
          { id: 'large-element', tag: 'p', text: '', editable: false },
        ],
      }),
    ),
  });
  await act(async () => render(<ArtifactEditor {...current} />));
  expect(current.load).toHaveBeenCalledWith(
    { pageId: undefined, elementId: 'large-element' },
    expect.any(AbortSignal),
  );
  expect(screen.queryByLabelText('Element text')).not.toBeInTheDocument();
  expect(screen.getByText(/complete source is preserved/)).toBeInTheDocument();
});

it('restores only an available explicit history choice and keeps the captured revision', async () => {
  const current = props({
    load: vi.fn(async () =>
      view({
        history_count: 2,
        history: [
          {
            id: '12.1',
            label: 'Saved original',
            author: 'user',
            page_count: 1,
            available: true,
          },
          {
            id: '12.2',
            label: 'Unavailable',
            author: 'unknown',
            page_count: 0,
            available: false,
          },
        ],
      }),
    ),
  });
  await act(async () => render(<ArtifactEditor {...current} />));
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'History' }), {
    button: 0,
  });
  expect(
    screen.getByRole('button', { name: 'Restore Unavailable' }),
  ).toBeDisabled();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Restore Saved original' }),
    ),
  );
  expect(current.edit).toHaveBeenCalledWith(
    { operation: 'restore', snapshot_id: '12.1' },
    'r1',
  );
});

it('keeps other draft fields when saving just the design name', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(view())
    .mockResolvedValue(view({ name: 'New name', resource_revision: 'r2' }));
  const current = props({ load });
  await act(async () => render(<ArtifactEditor {...current} />));
  fireEvent.change(screen.getByLabelText('Design name'), {
    target: { value: 'New name' },
  });
  fireEvent.change(screen.getByLabelText('Page notes'), {
    target: { value: 'Keep this unsaved note' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save design name' })),
  );
  expect(screen.getByLabelText('Page notes')).toHaveValue(
    'Keep this unsaved note',
  );
  expect(
    screen.getByRole('button', { name: 'Save page properties' }),
  ).toBeEnabled();
});

it.each(['completed', 'denied'] as const)(
  'holds save admission through selection and revision changes and ignores late %s results',
  async (outcome) => {
    let finish!: (receipt: CommandReceipt) => void;
    let reject!: (reason: unknown) => void;
    const pending = new Promise<CommandReceipt>((resolve, fail) => {
      finish = resolve;
      reject = fail;
    });
    const current = props({ edit: vi.fn(() => pending), onEdited: vi.fn() });
    const rendered = await act(async () =>
      render(<ArtifactEditor {...current} />),
    );
    fireEvent.change(screen.getByLabelText('Design name'), {
      target: { value: 'Pending name' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save design name' }));
    const updated = {
      ...current,
      resourceRevision: 'r3',
      pageId: 'page-b',
      selectedElementId: 'element-b',
      load: vi.fn(async () =>
        view({
          resource_revision: 'r3',
          page_id: 'page-b',
          name: 'Current name',
          elements: [
            { id: 'element-b', tag: 'p', text: 'Current text', editable: true },
          ],
        }),
      ),
    };
    await act(async () => rendered.rerender(<ArtifactEditor {...updated} />));
    expect(screen.getByLabelText('Design name')).toBeDisabled();
    expect(
      screen.getByRole('region', { name: 'Design editing' }),
    ).toHaveAttribute('aria-busy', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Save design name' }));
    expect(current.edit).toHaveBeenCalledTimes(1);
    await act(async () => {
      if (outcome === 'completed')
        finish({
          command_id: 'cmd',
          status: 'completed',
          resource_id: 'design-a',
          resource_revision: 'r2',
        });
      else reject({ code: 'capability_revoked' });
    });
    expect(screen.getByLabelText('Design name')).toHaveValue('Current name');
    expect(screen.getByLabelText('Element text')).toHaveValue('Current text');
    expect(screen.getByLabelText('Design name')).toBeEnabled();
    expect(screen.queryByText('Changes saved.')).not.toBeInTheDocument();
    expect(
      screen.queryByText('Design update unavailable'),
    ).not.toBeInTheDocument();
    expect(current.onEdited).not.toHaveBeenCalled();
  },
);

it('reconciles its confirmed receipt when its own resource event arrives before the response', async () => {
  let finish!: (receipt: CommandReceipt) => void;
  const pending = new Promise<CommandReceipt>((resolve) => {
    finish = resolve;
  });
  const current = props({ edit: vi.fn(() => pending) });
  const rendered = await act(async () =>
    render(<ArtifactEditor {...current} />),
  );
  fireEvent.change(screen.getByLabelText('Design name'), {
    target: { value: 'Saved name' },
  });
  fireEvent.change(screen.getByLabelText('Page notes'), {
    target: { value: 'Keep unsaved notes' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save design name' }));
  const updated = {
    ...current,
    resourceRevision: 'r2',
    load: vi.fn(async () =>
      view({ resource_revision: 'r2', name: 'Saved name' }),
    ),
  };
  await act(async () => rendered.rerender(<ArtifactEditor {...updated} />));
  expect(screen.getByLabelText('Design name')).toBeDisabled();
  await act(async () =>
    finish({
      command_id: 'cmd',
      status: 'completed',
      resource_id: 'design-a',
      resource_revision: 'r2',
    }),
  );
  expect(screen.getByText('Changes saved.')).toBeVisible();
  expect(screen.getByLabelText('Design name')).toHaveValue('Saved name');
  expect(screen.getByLabelText('Page notes')).toHaveValue('Keep unsaved notes');
  expect(
    screen.getByRole('button', { name: 'Save page properties' }),
  ).toBeEnabled();
  expect(screen.queryByText(/saved design changed/)).not.toBeInTheDocument();
});
