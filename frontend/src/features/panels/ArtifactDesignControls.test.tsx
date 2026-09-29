import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
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
  expect(screen.queryByRole('region', { name: 'Review' })).toBeNull();
  expect(current.review).not.toHaveBeenCalled();
  view.rerender(<ArtifactDesignControls {...current} view="review" />);
  await screen.findByRole('group', { name: 'Design review findings' });
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
  expect(
    await screen.findByText(/No clean result has been established/),
  ).toBeInTheDocument();
  // A failed check is not repeated in a loop.
  expect(current.review).toHaveBeenCalledOnce();
  expect(
    screen.queryByRole('group', { name: 'Design review findings' }),
  ).not.toBeInTheDocument();
  expect(current.apply).not.toHaveBeenCalled();
});

it('checks by itself, says it is heuristic and fixes one safe issue on request', async () => {
  const current = props();
  render(<ArtifactDesignControls {...current} view="review" />);
  await screen.findByRole('button', { name: 'Fix: Missing spacing' });
  expect(
    screen.getByText(/checks again after every change/),
  ).toBeInTheDocument();
  expect(current.review).toHaveBeenCalledWith({
    page_id: 'first',
    scope: 'page',
    cursor: undefined,
  });
  expect(current.apply).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Fix: Missing spacing' }),
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
    review: vi.fn(async () => ({
      ...report,
      findings: [{ ...report.findings[0], auto_fixable: false }],
    })),
    draftFix: vi.fn(async () => 'Focused fix instruction'),
    onDraftText: vi.fn(),
  });
  render(<ArtifactDesignControls {...current} view="review" />);
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Ask Row-Bot to fix: Missing spacing',
    }),
  );
  await screen.findByText(
    'AI fix drafted in chat. Review and send it when ready.',
  );
  expect(current.draftFix).toHaveBeenCalledWith('finding-a', 'first', 'r1');
  expect(current.onDraftText).toHaveBeenCalledWith('Focused fix instruction');
  expect(current.apply).not.toHaveBeenCalled();
});

it('fixes all safe issues in one step, with the reviewed scope', async () => {
  const safe = (id: string) => ({ ...report.findings[0], id, message: id });
  const current = props({
    review: vi.fn(async () => ({
      ...report,
      findings: [
        safe('Tight spacing'),
        safe('Low contrast'),
        { ...safe('Weak hierarchy'), auto_fixable: false },
      ],
      finding_count: 3,
    })),
  });
  render(<ArtifactDesignControls {...current} view="review" />);
  await act(async () =>
    fireEvent.click(
      await screen.findByRole('button', { name: 'Fix all safe issues (2)' }),
    ),
  );
  expect(current.apply).toHaveBeenCalledWith(
    'review_fix_all',
    { scope: 'page' },
    'r1',
    'first',
    'element-a',
  );
});

it('checks again after a saved change and says when nothing is left', async () => {
  let revision = 'r1';
  const current = props({
    load: vi.fn(async () => ({ ...state, resource_revision: revision })),
    review: vi.fn(async () => ({
      ...report,
      resource_revision: revision,
      findings: revision === 'r1' ? report.findings : [],
      finding_count: revision === 'r1' ? 1 : 0,
    })),
  });
  const view = render(<ArtifactDesignControls {...current} view="review" />);
  await screen.findByRole('button', { name: 'Fix: Missing spacing' });
  revision = 'r2';
  view.rerender(
    <ArtifactDesignControls {...current} view="review" resourceRevision="r2" />,
  );
  expect(await screen.findByText('No issues found.')).toBeInTheDocument();
  expect(current.review).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole('button', { name: /Fix all/ })).toBeNull();
});

it('takes brand colours and fonts from a website through the brand control', async () => {
  const suggestBrand = vi.fn(async () => ({
    found: true,
    site: 'example.com',
    primary_color: '#1D4ED8',
    secondary_color: '#F97316',
    accent_color: null,
    heading_font: 'Playfair Display',
    body_font: null,
  }));
  const current = props({ suggestBrand });
  render(<ArtifactDesignControls {...current} view="properties" />);
  const field = await screen.findByRole('textbox', {
    name: 'Website address',
  });
  fireEvent.change(field, { target: { value: 'example.com' } });
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Use its colours' }),
    ).toBeEnabled(),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Use its colours' })),
  );
  expect(suggestBrand).toHaveBeenCalledWith('https://example.com');
  expect(current.apply).toHaveBeenCalledWith(
    'brand',
    expect.objectContaining({
      primary_color: '#1D4ED8',
      secondary_color: '#F97316',
      accent_color: '#445566',
      heading_font: 'Playfair Display',
      body_font: 'Inter',
    }),
    'r1',
    'first',
    'element-a',
  );
  expect(
    await screen.findByText('Used 2 colours and fonts from example.com.'),
  ).toBeInTheDocument();
});

it('says when a website has no colours and changes nothing', async () => {
  const current = props({
    suggestBrand: vi.fn(async () => ({
      found: false,
      site: 'example.com',
      primary_color: null,
      secondary_color: null,
      accent_color: null,
      heading_font: null,
      body_font: null,
    })),
  });
  render(<ArtifactDesignControls {...current} view="properties" />);
  fireEvent.change(
    await screen.findByRole('textbox', { name: 'Website address' }),
    { target: { value: 'https://example.com' } },
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Use its colours' })),
  );
  expect(
    screen.getByText('No colours or fonts were found on example.com.'),
  ).toBeInTheDocument();
  expect(current.apply).not.toHaveBeenCalled();
});

it('explains a website it may not read', async () => {
  const current = props({
    suggestBrand: vi.fn().mockRejectedValue({
      code: 'brand_website_unavailable',
    }),
  });
  render(<ArtifactDesignControls {...current} view="properties" />);
  fireEvent.change(
    await screen.findByRole('textbox', { name: 'Website address' }),
    { target: { value: 'http://192.168.1.2' } },
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Use its colours' })),
  );
  expect(
    screen.getByText(/pages on this computer or your local network/),
  ).toBeInTheDocument();
  expect(current.apply).not.toHaveBeenCalled();
});
