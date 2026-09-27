import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ArtifactDesignControls, {
  type DesignControlsProps,
  type DesignControlsState,
  type DesignReviewState,
} from './ArtifactDesignControls';

const state: DesignControlsState = {
  resource_id: 'design-a',
  resource_revision: 'r1',
  mode: 'app_mockup',
  page_id: 'first',
  brand: {
    primary_color: '#112233',
    secondary_color: '#223344',
    accent_color: '#445566',
    bg_color: '#000000',
    text_color: '#FFFFFF',
    heading_font: 'Inter',
    body_font: 'Inter',
    logo_asset_id: '',
    logo_mode: 'auto',
    logo_scope: 'all',
    logo_position: 'top_right',
    logo_max_height: 72,
    logo_padding: 24,
  },
  element: {
    id: 'element-a',
    tag: 'h1',
    styles: { 'font-size': '32px' },
    action: '',
  },
  section: 'elements',
  items: [
    {
      id: 'element-a',
      label: 'Heading',
      kind: 'h1',
      detail: '',
      available: true,
    },
  ],
  item_count: 1,
  next_cursor: null,
};
const report: DesignReviewState = {
  resource_id: 'design-a',
  resource_revision: 'r1',
  page_id: 'first',
  scope: 'page',
  heuristic: true,
  score: 90,
  findings: [
    {
      id: 'finding-a',
      source: 'critique',
      category: 'spacing',
      severity: 'low',
      message: 'Missing spacing',
      suggested_fix: 'Add gap',
      page_id: 'first',
      auto_fixable: true,
    },
  ],
  finding_count: 1,
  next_cursor: null,
};
function props(
  overrides: Partial<DesignControlsProps> = {},
): DesignControlsProps {
  return {
    resourceId: 'design-a',
    resourceRevision: 'r1',
    pageId: 'first',
    selectedElementId: 'element-a',
    visible: true,
    load: vi.fn(async () => state),
    review: vi.fn(async () => report),
    apply: vi.fn(async () => ({ resource_revision: 'r2' })),
    upload: vi.fn(async () => ({ resource_revision: 'r2' })),
    onSelectElement: vi.fn(),
    ...overrides,
  };
}

async function commit(label: string, value: string) {
  const field = screen.getByLabelText(label);
  fireEvent.change(field, { target: { value } });
  await act(async () => fireEvent.blur(field));
}

it('loads saved controls passively without a review or mutation', async () => {
  const current = props();
  render(<ArtifactDesignControls {...current} />);
  await screen.findByRole('region', { name: 'Selection' });
  expect(screen.getByRole('region', { name: 'Brand' })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Type' })).toBeInTheDocument();
  expect(current.load).toHaveBeenCalledWith({
    page_id: 'first',
    element_id: 'element-a',
    section: 'elements',
  });
  expect(current.apply).not.toHaveBeenCalled();
  expect(current.review).not.toHaveBeenCalled();
});

it('shows only the requested inspector section', async () => {
  const current = props();
  const view = render(
    <ArtifactDesignControls {...current} view="properties" />,
  );
  await screen.findByRole('region', { name: 'Brand' });
  expect(
    screen.queryByRole('radiogroup', { name: 'Library section' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Run design review' }),
  ).not.toBeInTheDocument();
  view.rerender(<ArtifactDesignControls {...current} view="review" />);
  await screen.findByRole('button', { name: 'Run design review' });
  expect(
    screen.queryByRole('region', { name: 'Brand' }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('region', { name: 'Design review' }),
  ).toBeInTheDocument();
});

it('shows curated blocks only in supported modes and inserts the selected catalog entry', async () => {
  const current = props({
    load: vi.fn(async (options: Parameters<DesignControlsProps['load']>[0]) =>
      options.section === 'blocks'
        ? {
            ...state,
            mode: 'deck',
            section: 'blocks' as const,
            items: [
              {
                id: 'hero_callout',
                label: 'Hero Callout',
                kind: 'Story',
                detail: 'Two-column opener',
                available: true,
              },
            ],
          }
        : { ...state, mode: 'deck' },
    ),
  });
  render(<ArtifactDesignControls {...current} view="library" />);
  await screen.findByRole('radio', { name: 'Blocks' });
  expect(current.apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('radio', { name: 'Blocks' }));
  await screen.findByRole('button', { name: 'Insert Hero Callout' });
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Insert Hero Callout' }),
    ),
  );
  expect(current.apply).toHaveBeenCalledWith(
    'block_insert',
    { component_name: 'hero_callout' },
    'r1',
    'first',
    'element-a',
  );
});

it('hides curated blocks where the mode has none', async () => {
  render(<ArtifactDesignControls {...props()} view="library" />);
  await screen.findByRole('radio', { name: 'Assets' });
  expect(screen.queryByRole('radio', { name: 'Blocks' })).toBeNull();
});

it('saves a brand change under the exact revision when the field is left and keeps the outcome after refresh', async () => {
  const current = props();
  const view = render(<ArtifactDesignControls {...current} />);
  await screen.findByRole('region', { name: 'Brand' });
  await commit('Primary colour', '#445577');
  expect(current.apply).toHaveBeenCalledWith(
    'brand',
    expect.objectContaining({ primary_color: '#445577' }),
    'r1',
    'first',
    'element-a',
  );
  expect(current.apply).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Saved.')).toBeInTheDocument();
  view.rerender(
    <ArtifactDesignControls
      {...current}
      resourceRevision="r2"
      load={vi.fn(async () => ({ ...state, resource_revision: 'r2' }))}
    />,
  );
  await screen.findByRole('region', { name: 'Brand' });
  expect(screen.getByText('Saved.')).toBeInTheDocument();
});

it('applies a picked colour shortly after the last change and never an invalid hex', async () => {
  const current = props();
  render(<ArtifactDesignControls {...current} />);
  await screen.findByRole('region', { name: 'Brand' });
  vi.useFakeTimers();
  try {
    fireEvent.change(screen.getByLabelText('Accent colour picker'), {
      target: { value: '#aa0000' },
    });
    fireEvent.change(screen.getByLabelText('Accent colour picker'), {
      target: { value: '#bb0000' },
    });
    act(() => vi.advanceTimersByTime(300));
    expect(current.apply).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(500));
    expect(current.apply).toHaveBeenCalledTimes(1);
    expect(current.apply).toHaveBeenCalledWith(
      'brand',
      expect.objectContaining({ accent_color: '#bb0000' }),
      'r1',
      'first',
      'element-a',
    );
  } finally {
    vi.useRealTimers();
  }
  await act(async () => {});
  const other = props();
  render(<ArtifactDesignControls {...other} />);
  await screen.findAllByRole('region', { name: 'Brand' });
  const fields = screen.getAllByLabelText('Text colour');
  const field = fields.at(-1)!;
  fireEvent.change(field, { target: { value: '#12' } });
  await act(async () => fireEvent.blur(field));
  expect(other.apply).not.toHaveBeenCalled();
  expect(screen.getAllByText(/Use hex colours/).length).toBeGreaterThan(0);
});

it('saves selected scalar styles when left and an explicit interaction target', async () => {
  const current = props({
    apply: vi.fn(async () => ({ resource_revision: 'r1' })),
  });
  render(<ArtifactDesignControls {...current} />);
  await screen.findByRole('region', { name: 'Selection' });
  await commit('Element font-size', '40px');
  expect(current.apply).toHaveBeenCalledWith(
    'style',
    { 'font-size': '40px' },
    'r1',
    'first',
    'element-a',
  );
  await screen.findByLabelText('Hotspot target');
  fireEvent.change(screen.getByLabelText('Hotspot target'), {
    target: { value: 'second' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Apply interaction' })),
  );
  expect(current.apply).toHaveBeenLastCalledWith(
    'hotspot',
    { action: 'navigate', target: 'second' },
    'r1',
    'first',
    'element-a',
  );
});

it('does not interpret a failed scanner as a clean review', async () => {
  const current = props({
    review: vi.fn(async () => {
      throw new Error('Unavailable');
    }),
  });
  render(<ArtifactDesignControls {...current} view="review" />);
  await screen.findByRole('button', { name: 'Run design review' });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Run design review' })),
  );
  expect(
    screen.getByText(/No clean result has been established/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('group', { name: 'Design review findings' }),
  ).not.toBeInTheDocument();
  expect(current.apply).not.toHaveBeenCalled();
});

it('discloses heuristic limits and requires an explicit category fix', async () => {
  const current = props();
  render(<ArtifactDesignControls {...current} view="review" />);
  await screen.findByRole('button', { name: 'Run design review' });
  expect(
    screen.getByText(/a safe fix applies its category across the page/),
  ).toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Run design review' })),
  );
  expect(current.apply).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply safe spacing fix' }),
    ),
  );
  expect(current.apply).toHaveBeenCalledWith(
    'review_fix',
    { finding_id: 'finding-a' },
    'r1',
    'first',
    'element-a',
  );
});

it('holds mutation admission through resource switch and ignores late confirmation', async () => {
  let resolve!: (result: { resource_revision: string }) => void;
  const current = props({
    apply: vi.fn(
      () =>
        new Promise<{ resource_revision: string }>((done) => {
          resolve = done;
        }),
    ),
  });
  const view = render(<ArtifactDesignControls {...current} />);
  await screen.findByRole('region', { name: 'Brand' });
  await commit('Primary colour', '#445577');
  view.rerender(
    <ArtifactDesignControls
      {...current}
      resourceId="design-b"
      load={vi.fn(async () => ({ ...state, resource_id: 'design-b' }))}
    />,
  );
  await screen.findByRole('region', { name: 'Brand' });
  expect(screen.getByLabelText('Secondary colour')).toBeDisabled();
  await commit('Secondary colour', '#101010');
  await act(async () => resolve({ resource_revision: 'r2' }));
  expect(screen.queryByText('Saved.')).not.toBeInTheDocument();
  expect(current.apply).toHaveBeenCalledTimes(1);
});

it('follows real collection continuation instead of hiding later descriptors', async () => {
  const current = props({
    load: vi.fn(async (options) =>
      options.cursor
        ? {
            ...state,
            items: [{ ...state.items[0], id: 'later', label: 'Later element' }],
            item_count: 2,
          }
        : { ...state, item_count: 2, next_cursor: 'next-page' },
    ),
  });
  render(<ArtifactDesignControls {...current} view="library" />);
  await screen.findByRole('button', { name: 'Next controls page' });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Next controls page' })),
  );
  expect(
    screen.getByRole('button', { name: 'Select Later element' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Next controls page' }),
  ).not.toBeInTheDocument();
});

it('keeps chosen asset bytes local until explicit upload and discloses retained recovery on failure', async () => {
  const current = props({
    load: vi.fn(async (options) => ({
      ...state,
      section: options.section,
      items: [],
    })),
    upload: vi.fn(async () => {
      throw new Error('Revoked after bytes saved');
    }),
  });
  render(<ArtifactDesignControls {...current} view="library" />);
  await screen.findByRole('radio', { name: 'Assets' });
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Assets' })),
  );
  const file = new File(['synthetic bytes'], 'asset.png', {
    type: 'image/png',
  });
  fireEvent.change(await screen.findByLabelText('Choose asset'), {
    target: { files: [file] },
  });
  expect(current.upload).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Add asset' })),
  );
  expect(current.upload).toHaveBeenCalledWith(file, 'r1');
  expect(
    screen.getByText(/Uploaded files are retained for recovery/),
  ).toBeInTheDocument();
  expect(current.upload).toHaveBeenCalledTimes(1);
});

it('saves a global preset on one click and keeps project revision', async () => {
  const current = props({
    load: vi.fn(async (options) => ({
      ...state,
      section: options.section,
      items: [],
    })),
    mutatePreset: vi.fn(async (options) => ({
      ...options,
      preset_id: 'saved',
      resource_id: 'design-a',
      resource_revision: 'r1',
    })),
  });
  render(<ArtifactDesignControls {...current} view="library" />);
  await screen.findByRole('radio', { name: 'Presets' });
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Presets' })),
  );
  fireEvent.change(await screen.findByLabelText('New global preset name'), {
    target: { value: 'Shared brand' },
  });
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Save current brand as preset' }),
    ),
  );
  await screen.findByText('Global preset saved.');
  expect(current.mutatePreset).toHaveBeenCalledWith(
    { action: 'save', name: 'Shared brand' },
    'r1',
  );
  expect(current.apply).not.toHaveBeenCalled();
});

it('drafts an explicit review instruction into chat without submitting a provider request', async () => {
  const current = props({
    draftFix: vi.fn(async () => 'Focused fix instruction'),
    onDraftText: vi.fn(),
  });
  render(<ArtifactDesignControls {...current} view="review" />);
  await screen.findByRole('button', { name: 'Run design review' });
  fireEvent.click(screen.getByRole('button', { name: 'Run design review' }));
  fireEvent.click(
    await screen.findByRole('button', { name: /Draft AI fix in chat/ }),
  );
  await screen.findByText(
    'AI fix drafted in chat. Review and send it when ready.',
  );
  expect(current.draftFix).toHaveBeenCalledWith('finding-a', 'first', 'r1');
  expect(current.onDraftText).toHaveBeenCalledWith('Focused fix instruction');
  expect(current.apply).not.toHaveBeenCalled();
});
