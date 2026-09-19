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

it('loads saved controls passively without a review or mutation', async () => {
  const current = props();
  render(<ArtifactDesignControls {...current} />);
  await screen.findByText('Selected element properties');
  expect(current.load).toHaveBeenCalledWith({
    page_id: 'first',
    element_id: 'element-a',
    section: 'elements',
  });
  expect(current.apply).not.toHaveBeenCalled();
  expect(current.review).not.toHaveBeenCalled();
});

it('submits an explicit brand update under the exact revision and retains confirmation after refresh', async () => {
  const current = props();
  const view = render(<ArtifactDesignControls {...current} />);
  await screen.findByText('Brand and fonts');
  fireEvent.change(screen.getByLabelText('primary color'), {
    target: { value: '#445577' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Apply brand' })),
  );
  expect(current.apply).toHaveBeenCalledWith(
    'brand',
    expect.objectContaining({ primary_color: '#445577' }),
    'r1',
    'first',
    'element-a',
  );
  expect(screen.getByText('Changes saved.')).toBeInTheDocument();
  view.rerender(
    <ArtifactDesignControls
      {...current}
      resourceRevision="r2"
      load={vi.fn(async () => ({ ...state, resource_revision: 'r2' }))}
    />,
  );
  await screen.findByText('Brand and fonts');
  expect(screen.getByText('Changes saved.')).toBeInTheDocument();
});

it('submits selected scalar styles and explicit hotspot target', async () => {
  const current = props({
    apply: vi.fn(async () => ({ resource_revision: 'r1' })),
  });
  render(<ArtifactDesignControls {...current} />);
  await screen.findByText('Selected element properties');
  fireEvent.change(screen.getByLabelText('Element font-size'), {
    target: { value: '40px' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Apply properties' })),
  );
  expect(current.apply).toHaveBeenCalledWith(
    'style',
    { 'font-size': '40px' },
    'r1',
    'first',
    'element-a',
  );
  fireEvent.change(screen.getByLabelText('Hotspot target'), {
    target: { value: 'second' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Apply hotspot' })),
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
  render(<ArtifactDesignControls {...current} />);
  await screen.findByText('Selected element properties');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Run design review' })),
  );
  expect(
    screen.getByText(/No clean result has been established/),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText('Design review')).not.toBeInTheDocument();
  expect(current.apply).not.toHaveBeenCalled();
});

it('discloses heuristic limits and requires an explicit category fix', async () => {
  const current = props();
  render(<ArtifactDesignControls {...current} />);
  await screen.findByText('Selected element properties');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Run design review' })),
  );
  expect(
    screen.getByText(/Safe fixes apply the selected category across its page/),
  ).toBeInTheDocument();
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
  await screen.findByText('Brand and fonts');
  fireEvent.click(screen.getByRole('button', { name: 'Apply brand' }));
  view.rerender(
    <ArtifactDesignControls
      {...current}
      resourceId="design-b"
      load={vi.fn(async () => ({ ...state, resource_id: 'design-b' }))}
    />,
  );
  await screen.findByText('Brand and fonts');
  expect(screen.getByRole('button', { name: 'Apply brand' })).toBeDisabled();
  await act(async () => resolve({ resource_revision: 'r2' }));
  expect(screen.queryByText('Changes saved.')).not.toBeInTheDocument();
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
  render(<ArtifactDesignControls {...current} />);
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
  render(<ArtifactDesignControls {...current} />);
  await screen.findByText('Selected element properties');
  await act(async () =>
    fireEvent.change(screen.getByLabelText('Design catalog'), {
      target: { value: 'assets' },
    }),
  );
  const file = new File(['synthetic bytes'], 'asset.png', {
    type: 'image/png',
  });
  fireEvent.change(screen.getByLabelText('Choose asset'), {
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

it('reviews global preset scope before explicit mutation and keeps project revision', async () => {
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
  render(<ArtifactDesignControls {...current} />);
  await screen.findByText('Selected element properties');
  await act(async () =>
    fireEvent.change(screen.getByLabelText('Design catalog'), {
      target: { value: 'presets' },
    }),
  );
  fireEvent.change(screen.getByLabelText('New global preset name'), {
    target: { value: 'Shared brand' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Save current brand as preset' }),
  );
  expect(current.mutatePreset).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Global preset review')).toHaveTextContent(
    'all projects',
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm global preset change' }),
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
  render(<ArtifactDesignControls {...current} />);
  await screen.findByText('Selected element properties');
  fireEvent.click(screen.getByRole('button', { name: 'Run design review' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Draft AI fix in chat' }),
  );
  await screen.findByText(
    'AI fix drafted in chat. Review and send it when ready.',
  );
  expect(current.draftFix).toHaveBeenCalledWith('finding-a', 'first', 'r1');
  expect(current.onDraftText).toHaveBeenCalledWith('Focused fix instruction');
  expect(current.apply).not.toHaveBeenCalled();
});
