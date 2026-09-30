import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ArtifactEditingState, CommandReceipt } from '../../api/types';
import ArtifactEditor, {
  historyLabel,
  type ArtifactEditorProps,
} from './ArtifactEditor';

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

async function commit(label: string, value: string) {
  const field = screen.getByLabelText(label);
  fireEvent.change(field, { target: { value } });
  await act(async () => fireEvent.blur(field));
}

it('reads the page’s name and notes without submitting a mutation', async () => {
  const current = props();
  await act(async () => render(<ArtifactEditor {...current} />));
  expect(screen.getByLabelText('Page name')).toHaveValue('Opening');
  expect(screen.getByLabelText('Page notes')).toHaveValue('Saved notes');
  // The page's own settings: no text field shows here.
  expect(screen.queryByLabelText('Element text')).not.toBeInTheDocument();
  expect(current.edit).not.toHaveBeenCalled();
});

it('shows saved versions in the history view without mutating the design', async () => {
  const current = props({
    view: 'history',
    load: vi.fn(async () =>
      view({
        history_count: 1,
        history: [
          {
            id: '1700000000.1',
            label: 'Before panel text',
            author: 'agent',
            page_count: 3,
            available: true,
          },
        ],
      }),
    ),
  });
  await act(async () => render(<ArtifactEditor {...current} />));
  expect(
    screen.getByRole('region', { name: 'Design history' }),
  ).toBeInTheDocument();
  expect(screen.getByText('Before a text edit')).toBeInTheDocument();
  expect(screen.getByText(/Row-Bot · 3 pages/)).toBeInTheDocument();
  expect(screen.queryByLabelText('Page name')).not.toBeInTheDocument();
  expect(current.edit).not.toHaveBeenCalled();
});

it('describes saved versions in words', () => {
  expect(historyLabel('Before panel project_properties')).toBe(
    'Before renaming',
  );
  expect(historyLabel('Before panel brand')).toBe('Before a brand change');
  expect(historyLabel('inline_text_edit_page_0')).toBe(
    'Before: inline text edit (page 1)',
  );
  expect(historyLabel('apply_brand_ui')).toBe('Before: apply brand');
  expect(historyLabel('Before document import')).toBe('Before document import');
  expect(historyLabel('')).toBe('Saved version');
});

it('writes speaker notes with Row-Bot only on click and never over an unsaved draft', async () => {
  const generateNotes = vi.fn(async () => ({ resource_revision: 'r2' }));
  const current = props({ generateNotes });
  await act(async () => render(<ArtifactEditor {...current} />));
  expect(generateNotes).not.toHaveBeenCalled();
  expect(screen.getByText(/may incur provider charges/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Page notes'), {
    target: { value: 'Unsaved notes' },
  });
  expect(
    screen.getByRole('button', { name: 'Write with Row-Bot' }),
  ).toBeDisabled();
  await act(async () => fireEvent.blur(screen.getByLabelText('Page notes')));
  expect(current.edit).toHaveBeenCalledWith(
    { operation: 'page_properties', page_id: 'page-a', notes: 'Unsaved notes' },
    'r1',
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Write with Row-Bot' })),
  );
  expect(generateNotes).toHaveBeenCalledWith('page-a', 'r1');
});

it('sends the exact revision and plain text only when the field is committed', async () => {
  const current = props({ view: 'text', selectedElementId: 'element-a' });
  await act(async () => render(<ArtifactEditor {...current} />));
  expect(screen.queryByLabelText('Page name')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Element text'), {
    target: { value: '<img onerror="bad()">' },
  });
  expect(current.edit).not.toHaveBeenCalled();
  await act(async () => fireEvent.blur(screen.getByLabelText('Element text')));
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

it('does not save a field that returns to its saved value or an empty title', async () => {
  const current = props();
  await act(async () => render(<ArtifactEditor {...current} />));
  await commit('Page name', 'Opening');
  await commit('Page name', '   ');
  expect(current.edit).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Page name')).toHaveValue('Opening');
  expect(screen.getByText(/A page needs a title/)).toBeInTheDocument();
});

it('keeps a draft through a conflict and blocks editing until saved values are reloaded', async () => {
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
  const rendered = await act(async () =>
    render(<ArtifactEditor {...current} />),
  );
  await commit('Page notes', 'My unsaved note');
  expect(screen.getByLabelText('Page notes')).toHaveValue('My unsaved note');
  expect(screen.getByRole('alert')).toHaveTextContent('Your draft is kept');
  await act(async () =>
    rendered.rerender(<ArtifactEditor {...current} resourceRevision="r2" />),
  );
  expect(screen.getByLabelText('Page notes')).toHaveValue('My unsaved note');
  expect(screen.getByLabelText('Page notes')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Reload saved values' }));
  expect(screen.getByLabelText('Page notes')).toHaveValue(
    'A concurrent saved note',
  );
  expect(current.edit).toHaveBeenCalledTimes(1);
});

it('clears revoked saved content and cannot send another edit', async () => {
  const current = props({
    edit: vi.fn().mockRejectedValue({ code: 'resource_binding_revoked' }),
  });
  await act(async () => render(<ArtifactEditor {...current} />));
  await commit('Page name', 'My title');
  expect(screen.queryByLabelText('Page name')).not.toBeInTheDocument();
  expect(screen.queryByText('Original text')).not.toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Access to this design changed',
  );
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
    .mockResolvedValue(
      view({ resource_id: 'design-b', page_title: 'Other page' }),
    );
  const current = props({ load });
  const rendered = render(<ArtifactEditor {...current} />);
  await act(async () =>
    rendered.rerender(<ArtifactEditor {...current} resourceId="design-b" />),
  );
  await act(async () => resolve(view()));
  expect(screen.getByLabelText('Page name')).toHaveValue('Other page');
});

it('follows the history continuation cursor without duplicate rows', async () => {
  const load = vi.fn(async (options) =>
    options.historyCursor
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
      : view({
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
        }),
  );
  const current = props({ load, view: 'history' });
  await act(async () => render(<ArtifactEditor {...current} />));
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Load more history' })),
  );
  expect(screen.getByRole('button', { name: 'Restore Second' })).toBeEnabled();
  expect(screen.getAllByRole('button', { name: /^Restore/ })).toHaveLength(2);
  expect(load.mock.calls.map(([options]) => options)).toEqual([
    { pageId: undefined, elementId: undefined },
    { pageId: 'page-a', historyCursor: 'history-cursor' },
  ]);
});

it('requests the exact selected element and does not offer an oversized source', async () => {
  const current = props({
    view: 'text',
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
  expect(
    screen.getByText(/too long to change here\. Double-click/),
  ).toBeVisible();
});

it('hands back a selection that is gone instead of showing an error, and reads the page without it', async () => {
  const load = vi.fn(async (options: { elementId?: string }) => {
    if (options.elementId) throw { code: 'element_unavailable' };
    return view();
  });
  const onSelectionLost = vi.fn();
  const current = props({
    view: 'text',
    selectedElementId: 'element-a',
    load,
    onSelectionLost,
  });
  const rendered = await act(async () =>
    render(<ArtifactEditor {...current} />),
  );
  expect(onSelectionLost).toHaveBeenCalledWith('element-a');
  expect(screen.queryByRole('alert')).toBeNull();
  await act(async () =>
    rendered.rerender(
      <ArtifactEditor {...current} view="page" selectedElementId={undefined} />,
    ),
  );
  expect(load).toHaveBeenLastCalledWith(
    { pageId: undefined, elementId: undefined },
    expect.any(AbortSignal),
  );
  expect(screen.getByLabelText('Page name')).toHaveValue('Opening');
  expect(screen.queryByRole('alert')).toBeNull();
});

it('restores only an available explicit history choice and keeps the captured revision', async () => {
  const current = props({
    view: 'history',
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
  expect(screen.getByText('Version restored.')).toBeInTheDocument();
});

it('keeps other draft fields when saving just the page title', async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(view())
    .mockResolvedValue(
      view({ page_title: 'New title', resource_revision: 'r2' }),
    );
  const current = props({ load });
  await act(async () => render(<ArtifactEditor {...current} />));
  fireEvent.change(screen.getByLabelText('Page notes'), {
    target: { value: 'Keep this unsaved note' },
  });
  await commit('Page name', 'New title');
  expect(current.edit).toHaveBeenCalledWith(
    { operation: 'page_properties', page_id: 'page-a', title: 'New title' },
    'r1',
  );
  expect(screen.getByLabelText('Page notes')).toHaveValue(
    'Keep this unsaved note',
  );
  expect(screen.getByLabelText('Page notes')).toBeEnabled();
});

it.each(['completed', 'denied'] as const)(
  'holds save admission through selection and revision changes, ignores late %s results and then sends the queued edit once',
  async (outcome) => {
    let finish!: (receipt: CommandReceipt) => void;
    let reject!: (reason: unknown) => void;
    const pending = new Promise<CommandReceipt>((resolve, fail) => {
      finish = resolve;
      reject = fail;
    });
    const edit = vi
      .fn()
      .mockImplementationOnce(() => pending)
      .mockResolvedValue({
        command_id: 'cmd-2',
        status: 'completed',
        resource_id: 'design-a',
        resource_revision: 'r4',
      });
    const current = props({ edit, onEdited: vi.fn() });
    const rendered = await act(async () =>
      render(<ArtifactEditor {...current} />),
    );
    await commit('Page name', 'Pending title');
    const updated = {
      ...current,
      resourceRevision: 'r3',
      pageId: 'page-b',
      selectedElementId: 'element-b',
      load: vi.fn(async () =>
        view({
          resource_revision: 'r3',
          page_id: 'page-b',
          page_title: 'Current title',
          elements: [
            { id: 'element-b', tag: 'p', text: 'Current text', editable: true },
          ],
        }),
      ),
    };
    await act(async () => rendered.rerender(<ArtifactEditor {...updated} />));
    expect(screen.getByLabelText('Page name')).toBeDisabled();
    expect(
      screen.getByRole('region', { name: 'Design editing' }),
    ).toHaveAttribute('aria-busy', 'true');
    await commit('Page notes', 'Another change');
    expect(edit).toHaveBeenCalledTimes(1);
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
    // The late result of the first save changes nothing it no longer owns;
    // the edit made meanwhile is sent once, for the page and revision now on
    // screen.
    expect(edit).toHaveBeenCalledTimes(2);
    expect(edit).toHaveBeenLastCalledWith(
      {
        operation: 'page_properties',
        page_id: 'page-b',
        notes: 'Another change',
      },
      'r3',
    );
    expect(screen.getByLabelText('Page name')).toHaveValue('Current title');
    expect(screen.queryByText("That didn't work")).not.toBeInTheDocument();
    expect(current.onEdited).toHaveBeenCalledTimes(1);
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
  fireEvent.change(screen.getByLabelText('Page notes'), {
    target: { value: 'Keep unsaved notes' },
  });
  await commit('Page name', 'Saved title');
  const updated = {
    ...current,
    resourceRevision: 'r2',
    load: vi.fn(async () =>
      view({ resource_revision: 'r2', page_title: 'Saved title' }),
    ),
  };
  await act(async () => rendered.rerender(<ArtifactEditor {...updated} />));
  expect(screen.getByLabelText('Page name')).toBeDisabled();
  await act(async () =>
    finish({
      command_id: 'cmd',
      status: 'completed',
      resource_id: 'design-a',
      resource_revision: 'r2',
    }),
  );
  expect(screen.getByText('Saved.')).toBeVisible();
  expect(screen.getByLabelText('Page name')).toHaveValue('Saved title');
  expect(screen.getByLabelText('Page notes')).toHaveValue('Keep unsaved notes');
  expect(screen.getByLabelText('Page notes')).toBeEnabled();
  expect(screen.queryByText(/saved design changed/)).not.toBeInTheDocument();
});
