import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import ArtifactDesignControls, {
  type DesignControlItem,
  type DesignControlsProps,
  type DesignControlsState,
  type DesignElementView,
  type DesignReviewState,
} from './ArtifactDesignControls';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
});

const heading: DesignElementView = {
  id: 'element-a',
  tag: 'h1',
  styles: { 'font-size': '32px' },
  action: '',
  kind: 'text',
  text: 'Solstice Bake Sale',
  alt: '',
  asset_id: '',
};
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
  element: heading,
  section: 'elements',
  items: [
    {
      id: 'element-a',
      label: 'Solstice Bake Sale',
      kind: 'h1',
      detail: 'text',
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
    view: 'selection',
    resourceId: 'design-a',
    resourceRevision: 'r1',
    pageId: 'first',
    selectedElementId: 'element-a',
    visible: true,
    load: vi.fn(async () => state),
    thumbnail: vi.fn(
      async () => new Blob(['synthetic'], { type: 'image/png' }),
    ),
    review: vi.fn(async () => report),
    apply: vi.fn(async () => ({ resource_revision: 'r2' })),
    upload: vi.fn(async () => ({ resource_revision: 'r2' })),
    onSelectElement: vi.fn(),
    onSelectionLost: vi.fn(),
    onReload: vi.fn(),
    ...overrides,
  };
}

function item(
  id: string,
  kind: string,
  label = id,
  detail = '',
): DesignControlItem {
  return { id, label, kind, detail, available: true };
}

/**
 * Controls over a design that keeps what is saved: the selected element's
 * styles and the brand follow each apply; catalogs answer from `catalog`.
 */
function live(
  element: DesignElementView | null,
  catalog: Partial<Record<string, DesignControlItem[]>> = {},
  overrides: Partial<DesignControlsProps> = {},
  mode = state.mode,
) {
  let saved = element && { ...element, styles: { ...element.styles } };
  let brand = { ...state.brand };
  return props({
    load: vi.fn(async (options: Parameters<DesignControlsProps['load']>[0]) =>
      options.section === 'elements'
        ? { ...state, mode, brand, element: saved }
        : {
            ...state,
            mode,
            brand,
            section: options.section,
            items: catalog[options.section] ?? [],
          },
    ),
    apply: vi.fn(async (kind, payload) => {
      if (kind === 'style' && saved)
        saved = {
          ...saved,
          styles: { ...(payload as Record<string, string>) },
        };
      if (kind === 'brand') brand = { ...brand, ...payload };
      return { resource_revision: 'r1' };
    }),
    ...overrides,
  });
}

/** The last change saved these styles (among the rest it keeps). */
function lastStyle(current: DesignControlsProps) {
  const call = vi.mocked(current.apply).mock.calls.at(-1);
  return call?.[0] === 'style' ? call[1] : null;
}

/** The controls have saved and read the design again. */
const settled = (name = 'Design controls') =>
  waitFor(() =>
    expect(screen.getByRole('region', { name })).toHaveAttribute(
      'aria-busy',
      'false',
    ),
  );

async function commit(label: string, value: string) {
  const field = screen.getByLabelText(label);
  fireEvent.change(field, { target: { value } });
  await act(async () => fireEvent.blur(field));
}

it('loads the selection’s own controls passively, without a review, a change or CSS boxes', async () => {
  const current = props();
  render(<ArtifactDesignControls {...current} />);
  expect(await screen.findByText('Heading')).toBeInTheDocument();
  expect(screen.getByText('“Solstice Bake Sale”')).toBeInTheDocument();
  expect(current.load).toHaveBeenCalledWith({
    page_id: 'first',
    element_id: 'element-a',
    section: 'elements',
    limit: 50,
  });
  expect(screen.queryByRole('textbox', { name: /^Element / })).toBeNull();
  expect(current.apply).not.toHaveBeenCalled();
  expect(current.review).not.toHaveBeenCalled();
});

it('clears a selection an edit removed without an error and shows what is on the page', async () => {
  const load = vi.fn(
    async (options: Parameters<DesignControlsProps['load']>[0]) => {
      if (options.element_id) throw { code: 'element_unavailable' };
      return {
        ...state,
        element: null,
        items: [
          item('a', 'h1', 'Solstice Bake Sale', 'text'),
          item('b', 'img', 'img', 'image'),
          item('c', 'section', 'Everything', 'layout'),
        ],
      };
    },
  );
  const current = props({ load });
  const view = render(<ArtifactDesignControls {...current} />);
  await waitFor(() =>
    expect(current.onSelectionLost).toHaveBeenCalledWith('element-a'),
  );
  expect(screen.queryByRole('alert')).toBeNull();
  view.rerender(
    <ArtifactDesignControls {...current} selectedElementId={undefined} />,
  );
  const page = await screen.findByRole('region', { name: 'On this page' });
  // Texts, pictures and shapes; layout boxes stay out of the way.
  expect(within(page).getAllByRole('button')).toHaveLength(2);
  expect(
    within(page).getByRole('button', { name: 'Select img' }),
  ).toHaveTextContent('Image');
  fireEvent.click(
    within(page).getByRole('button', { name: 'Select Solstice Bake Sale' }),
  );
  expect(current.onSelectElement).toHaveBeenCalledWith('a');
});

it('reads the design again quietly when it moved on before the panel caught up', async () => {
  const current = props({
    load: vi.fn(async () => ({ ...state, resource_revision: 'r2' })),
  });
  const view = render(<ArtifactDesignControls {...current} />);
  await waitFor(() => expect(current.onReload).toHaveBeenCalledOnce());
  expect(screen.queryByRole('alert')).toBeNull();
  view.rerender(<ArtifactDesignControls {...current} resourceRevision="r2" />);
  await screen.findByText('Heading');
  expect(screen.queryByRole('alert')).toBeNull();
});

it('says what failed in plain words and retries with the current design and selection', async () => {
  const load = vi
    .fn<DesignControlsProps['load']>()
    .mockRejectedValueOnce({ code: 'design_catalog_unavailable' })
    .mockResolvedValue(state);
  const current = props({ load });
  render(<ArtifactDesignControls {...current} />);
  const card = await screen.findByRole('alert');
  expect(card).toHaveTextContent("That didn't work");
  expect(card).toHaveTextContent(/couldn.t be read/);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Retry' })),
  );
  expect(current.onReload).toHaveBeenCalledOnce();
  expect(
    load.mock.calls
      .filter(([options]) => options.section === 'elements')
      .at(-1),
  ).toEqual([
    {
      page_id: 'first',
      element_id: 'element-a',
      section: 'elements',
      limit: 50,
    },
  ]);
  await screen.findByText('Heading');
  expect(screen.queryByRole('alert')).toBeNull();
});

it('text gets font, size, weight, colour and alignment, starting from how it looks', async () => {
  const current = live(heading);
  render(
    <ArtifactDesignControls
      {...current}
      look={{
        'font-size': '32px',
        'font-weight': '700',
        color: 'rgb(17, 34, 51)',
        'text-align': 'start',
        'line-height': '38.4px',
        'letter-spacing': 'normal',
      }}
    />,
  );
  const size = await screen.findByRole('textbox', { name: 'Size' });
  expect(size).toHaveValue('32');
  expect(screen.getByRole('radio', { name: 'Bold' })).toBeChecked();
  expect(screen.getByRole('radio', { name: 'Left' })).toBeChecked();
  expect(screen.getByRole('textbox', { name: 'Line height' })).toHaveValue(
    '1.2',
  );
  expect(screen.getByRole('textbox', { name: 'Letter spacing' })).toHaveValue(
    '0',
  );
  const colour = screen.getByRole('group', { name: 'Colour' });
  expect(
    within(colour).getByRole('button', { name: 'Primary' }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Font' })).toBeInTheDocument();
  // Stepping saves once the steps settle, as one change.
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: 'Increase size' }));
  fireEvent.click(screen.getByRole('button', { name: 'Increase size' }));
  expect(current.apply).not.toHaveBeenCalled();
  await act(async () => vi.advanceTimersByTime(700));
  vi.useRealTimers();
  expect(current.apply).toHaveBeenCalledTimes(1);
  expect(lastStyle(current)).toEqual({ 'font-size': '34px' });
  // A choice saves at once, keeping what is saved.
  await waitFor(() => expect(size).toHaveValue('34'));
  await settled();
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Medium' })),
  );
  await waitFor(() =>
    expect(lastStyle(current)).toEqual({
      'font-size': '34px',
      'font-weight': '500',
    }),
  );
  await settled();
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Centre' })),
  );
  await waitFor(() =>
    expect(lastStyle(current)).toMatchObject({ 'text-align': 'center' }),
  );
  // Brand swatches follow the brand.
  await settled();
  await act(async () =>
    fireEvent.click(within(colour).getByRole('button', { name: 'Accent' })),
  );
  await waitFor(() =>
    expect(lastStyle(current)).toMatchObject({ color: 'var(--accent)' }),
  );
  expect(vi.mocked(current.apply).mock.calls.at(-1)?.slice(2)).toEqual([
    'r1',
    'first',
    'element-a',
  ]);
});

it('a shape gets fill, corners, border and opacity', async () => {
  const badge: DesignElementView = {
    ...heading,
    id: 'badge',
    tag: 'div',
    kind: 'shape',
    text: 'Saturday',
    styles: {},
  };
  const current = live(badge, {}, { selectedElementId: 'badge' });
  render(<ArtifactDesignControls {...current} look={{ opacity: '1' }} />);
  expect(await screen.findAllByText('Shape')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: 'Font' })).toBeNull();
  const fill = screen.getByRole('group', { name: 'Fill' });
  await act(async () =>
    fireEvent.click(within(fill).getByRole('button', { name: 'Secondary' })),
  );
  await waitFor(() =>
    expect(lastStyle(current)).toEqual({
      'background-color': 'var(--secondary)',
    }),
  );
  await settled();
  await commit('Corners', '16');
  await waitFor(() =>
    expect(lastStyle(current)).toMatchObject({ 'border-radius': '16px' }),
  );
  expect(screen.queryByRole('textbox', { name: 'Border width' })).toBeNull();
  await settled();
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Dashed' })),
  );
  await waitFor(() =>
    expect(lastStyle(current)).toMatchObject({ 'border-style': 'dashed' }),
  );
  expect(
    await screen.findByRole('textbox', { name: 'Border width' }),
  ).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Opacity' })).toHaveValue('100');
  await settled();
  await commit('Opacity', '80');
  await waitFor(() =>
    expect(lastStyle(current)).toMatchObject({ opacity: '0.8' }),
  );
});

it('a picture is replaced with one of the design’s images, fitted, cropped and described', async () => {
  const user = userEvent.setup();
  Object.assign(URL, {
    createObjectURL: vi.fn(() => 'blob:thumbnail'),
    revokeObjectURL: vi.fn(),
  });
  const photo: DesignElementView = {
    ...heading,
    id: 'photo',
    tag: 'img',
    kind: 'image',
    text: '',
    alt: 'Old words',
    asset_id: 'asset-old',
    styles: {},
  };
  const current = live(
    photo,
    {
      assets: [
        item('asset-old', 'image', 'old.png'),
        item('asset-rye', 'image', 'rye.jpg'),
        item('asset-jingle', 'audio', 'jingle.wav'),
      ],
    },
    {
      selectedElementId: 'photo',
      upload: vi.fn(async () => ({
        resource_revision: 'r1',
        asset_id: 'asset-new',
      })),
    },
  );
  render(<ArtifactDesignControls {...current} />);
  expect(await screen.findByText('Old words')).toBeInTheDocument();
  expect(current.thumbnail).toHaveBeenCalledWith('asset-old');
  await user.click(screen.getByRole('button', { name: 'Replace…' }));
  const menu = await screen.findByRole('menu');
  expect(
    within(menu).queryByRole('menuitem', { name: 'jingle.wav' }),
  ).toBeNull();
  await user.click(within(menu).getByRole('menuitem', { name: 'rye.jpg' }));
  await waitFor(() =>
    expect(current.apply).toHaveBeenLastCalledWith(
      'image',
      { asset_id: 'asset-rye' },
      'r1',
      'first',
      'photo',
    ),
  );
  const fit = screen.getByRole('radiogroup', { name: 'Fit' });
  await settled();
  await user.click(within(fit).getByRole('radio', { name: 'Fit' }));
  await waitFor(() =>
    expect(lastStyle(current)).toEqual({ 'object-fit': 'contain' }),
  );
  // Crop chooses which part of a filled frame shows.
  expect(screen.getByRole('button', { name: /^Crop: Centre/ })).toBeDisabled();
  await settled();
  await user.click(within(fit).getByRole('radio', { name: 'Fill' }));
  await settled();
  await user.click(screen.getByRole('button', { name: /^Crop: Centre/ }));
  await user.click(await screen.findByRole('menuitem', { name: 'Top' }));
  await waitFor(() =>
    expect(lastStyle(current)).toEqual({
      'object-fit': 'cover',
      'object-position': 'top',
    }),
  );
  await settled();
  await commit('Description', 'A rye loaf on a warm table');
  await waitFor(() =>
    expect(current.apply).toHaveBeenLastCalledWith(
      'image',
      { alt: 'A rye loaf on a warm table' },
      'r1',
      'first',
      'photo',
    ),
  );
  // A new picture is uploaded, then shown once the design has it.
  await settled();
  const file = new File(['synthetic'], 'new.png', { type: 'image/png' });
  await act(async () =>
    fireEvent.change(screen.getByLabelText('Image file'), {
      target: { files: [file] },
    }),
  );
  expect(current.upload).toHaveBeenCalledWith(file, 'r1');
  await waitFor(() =>
    expect(current.apply).toHaveBeenLastCalledWith(
      'image',
      { asset_id: 'asset-new' },
      'r1',
      'first',
      'photo',
    ),
  );
});

it('a layout box gets its width and spacing', async () => {
  const box: DesignElementView = {
    ...heading,
    id: 'box',
    tag: 'section',
    kind: 'layout',
    text: '',
    styles: {},
  };
  const current = live(box, {}, { selectedElementId: 'box' });
  render(<ArtifactDesignControls {...current} look={{ width: '318.4px' }} />);
  expect(await screen.findByText('Group')).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Gap' })).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'Fixed width' })).toBeNull();
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Fixed' })),
  );
  await waitFor(() => expect(lastStyle(current)).toEqual({ width: '318px' }));
  expect(
    await screen.findByRole('textbox', { name: 'Fixed width' }),
  ).toHaveValue('318');
  await settled();
  await act(async () =>
    fireEvent.click(screen.getByRole('radio', { name: 'Fill' })),
  );
  await waitFor(() => expect(lastStyle(current)).toEqual({ width: '100%' }));
  await settled();
  await commit('Space inside', '12');
  await waitFor(() =>
    expect(lastStyle(current)).toMatchObject({ padding: '12px' }),
  );
});

it('keeps raw values for power users behind Edit as CSS…', async () => {
  const user = userEvent.setup();
  const current = props({
    apply: vi.fn(async () => ({ resource_revision: 'r1' })),
  });
  render(<ArtifactDesignControls {...current} />);
  await user.click(
    await screen.findByRole('button', { name: 'More for this element' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Edit as CSS…' }),
  );
  await commit('Element font-size', '40px');
  expect(current.apply).toHaveBeenCalledWith(
    'style',
    { 'font-size': '40px' },
    'r1',
    'first',
    'element-a',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Close CSS' }));
  expect(screen.queryByLabelText('Element font-size')).toBeNull();
});

it('sets an interaction on an interactive design', async () => {
  const current = props({
    apply: vi.fn(async () => ({ resource_revision: 'r1' })),
  });
  render(<ArtifactDesignControls {...current} />);
  fireEvent.change(await screen.findByLabelText('Hotspot target'), {
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

it('clears the selection quietly when a change finds its element gone', async () => {
  const current = props({
    apply: vi.fn(async () => {
      throw new Error('element_unavailable');
    }),
  });
  render(<ArtifactDesignControls {...current} />);
  await act(async () =>
    fireEvent.click(await screen.findByRole('radio', { name: 'Bold' })),
  );
  await waitFor(() =>
    expect(current.onSelectionLost).toHaveBeenCalledWith('element-a'),
  );
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText(/earlier page|another page/)).toBeNull();
});

it('shows only the requested view', async () => {
  const current = props();
  const view = render(<ArtifactDesignControls {...current} view="brand" />);
  await screen.findByRole('region', { name: 'Colours' });
  expect(screen.queryByText('Heading')).toBeNull();
  expect(screen.queryByRole('region', { name: 'Review' })).toBeNull();
  expect(current.review).not.toHaveBeenCalled();
  view.rerender(<ArtifactDesignControls {...current} view="review" />);
  await screen.findByRole('group', { name: 'Design review findings' });
  expect(screen.queryByRole('region', { name: 'Colours' })).toBeNull();
});

it('saves a brand colour under the exact revision when the field is left and keeps the outcome after refresh', async () => {
  const current = props({ view: 'brand' });
  const view = render(<ArtifactDesignControls {...current} />);
  await screen.findByRole('region', { name: 'Colours' });
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
  await screen.findByRole('region', { name: 'Colours' });
  expect(screen.getByText('Saved.')).toBeInTheDocument();
});

it('applies a picked brand colour shortly after the last change and never an invalid hex', async () => {
  const current = props({ view: 'brand' });
  render(<ArtifactDesignControls {...current} />);
  await screen.findByRole('region', { name: 'Colours' });
  vi.useFakeTimers();
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
  vi.useRealTimers();
  await act(async () => {});
  const other = props({ view: 'brand' });
  render(<ArtifactDesignControls {...other} />);
  await screen.findAllByRole('region', { name: 'Colours' });
  const field = screen.getAllByLabelText('Text colour').at(-1)!;
  fireEvent.change(field, { target: { value: '#12' } });
  await act(async () => fireEvent.blur(field));
  expect(other.apply).not.toHaveBeenCalled();
  expect(screen.getAllByText(/Use hex colours/).length).toBeGreaterThan(0);
});

it('holds mutation admission through resource switch and ignores late confirmation', async () => {
  let resolve!: (result: { resource_revision: string }) => void;
  const current = props({
    view: 'brand',
    apply: vi.fn(
      () =>
        new Promise<{ resource_revision: string }>((done) => {
          resolve = done;
        }),
    ),
  });
  const view = render(<ArtifactDesignControls {...current} />);
  await screen.findByRole('region', { name: 'Colours' });
  await commit('Primary colour', '#445577');
  view.rerender(
    <ArtifactDesignControls
      {...current}
      resourceId="design-b"
      load={vi.fn(async () => ({ ...state, resource_id: 'design-b' }))}
    />,
  );
  await screen.findByRole('region', { name: 'Colours' });
  expect(screen.getByLabelText('Secondary colour')).toBeDisabled();
  await commit('Secondary colour', '#101010');
  await act(async () => resolve({ resource_revision: 'r2' }));
  expect(screen.queryByText('Saved.')).not.toBeInTheDocument();
  expect(current.apply).toHaveBeenCalledTimes(1);
});

async function fromWebsite(address: string) {
  fireEvent.click(
    await screen.findByRole('button', { name: 'From a website…' }),
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Website address' }), {
    target: { value: address },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Use its colours' })),
  );
}

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
  const current = props({ suggestBrand, view: 'brand' });
  render(<ArtifactDesignControls {...current} />);
  await fromWebsite('example.com');
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
    view: 'brand',
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
  render(<ArtifactDesignControls {...current} />);
  await fromWebsite('https://example.com');
  expect(
    screen.getByText('No colours or fonts were found on example.com.'),
  ).toBeInTheDocument();
  expect(current.apply).not.toHaveBeenCalled();
});

it('explains a website it may not read', async () => {
  const current = props({
    view: 'brand',
    suggestBrand: vi.fn().mockRejectedValue({
      code: 'brand_website_unavailable',
    }),
  });
  render(<ArtifactDesignControls {...current} />);
  await fromWebsite('http://192.168.1.2');
  expect(
    screen.getByText(/pages on this computer or your local network/),
  ).toBeInTheDocument();
  expect(current.apply).not.toHaveBeenCalled();
});

it('picks a brand font from a searchable list, each name in its own face', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('FontFace', class {});
  Object.defineProperty(document, 'fonts', {
    configurable: true,
    value: { add: vi.fn() },
  });
  localStorage.setItem(
    'row-bot.design-fonts.recent.v1',
    JSON.stringify(['Georgia']),
  );
  const current = live(heading, {
    fonts: [
      item('Inter', 'bundled'),
      item('Playfair Display', 'bundled'),
      item('Georgia', 'system'),
      item('Pt Sans', 'cached'),
    ],
  });
  try {
    render(<ArtifactDesignControls {...current} view="brand" />);
    await user.click(
      await screen.findByRole('button', { name: 'Heading font' }),
    );
    const list = await screen.findByRole('listbox', { name: 'Heading font' });
    const names = (group: string) =>
      within(within(list).getByRole('group', { name: group }))
        .getAllByRole('option')
        .map((option) => option.textContent);
    expect(names('Recently used')).toEqual(['Georgia']);
    expect(names('Bundled')).toEqual(['Inter', 'Playfair Display']);
    expect(names('On this computer')).toEqual(['Pt Sans']);
    await user.type(
      screen.getByRole('combobox', { name: 'Search heading font' }),
      'play',
    );
    const playfair = within(list).getByRole('option', {
      name: 'Playfair Display',
    });
    expect(within(list).getAllByRole('option')).toHaveLength(1);
    expect(
      within(playfair).getByText('Playfair Display').style.fontFamily,
    ).toContain('Playfair Display');
    await user.click(playfair);
    await waitFor(() =>
      expect(current.apply).toHaveBeenCalledWith(
        'brand',
        expect.objectContaining({
          heading_font: 'Playfair Display',
          body_font: 'Inter',
        }),
        'r1',
        'first',
        'element-a',
      ),
    );
    expect(
      JSON.parse(localStorage.getItem('row-bot.design-fonts.recent.v1')!),
    ).toEqual(['Playfair Display', 'Georgia']);
  } finally {
    delete (document as { fonts?: unknown }).fonts;
  }
});

it('chooses the logo from the design’s pictures without an ID, sets where it goes, and No logo clears it', async () => {
  const user = userEvent.setup();
  Object.assign(URL, {
    createObjectURL: vi.fn(() => 'blob:thumbnail'),
    revokeObjectURL: vi.fn(),
  });
  const current = live(heading, {
    assets: [
      item('asset-logo', 'image', 'logo.png'),
      item('asset-jingle', 'audio', 'jingle.wav'),
    ],
  });
  render(<ArtifactDesignControls {...current} view="brand" />);
  const choices = await screen.findByRole('radiogroup', { name: 'Logo' });
  expect(
    within(choices).getByRole('radio', { name: 'No logo' }),
  ).toHaveAttribute('aria-checked', 'true');
  const logo = await within(choices).findByRole('radio', { name: 'logo.png' });
  expect(
    within(choices).queryByRole('radio', { name: 'jingle.wav' }),
  ).toBeNull();
  await waitFor(() =>
    expect(logo.querySelector('img')).toHaveAttribute('src', 'blob:thumbnail'),
  );
  expect(
    screen.queryByRole('radiogroup', { name: 'Logo placement' }),
  ).toBeNull();
  const brand = (change: Record<string, unknown>) =>
    waitFor(() =>
      expect(current.apply).toHaveBeenLastCalledWith(
        'brand',
        expect.objectContaining(change),
        'r1',
        'first',
        'element-a',
      ),
    );
  await user.click(logo);
  await brand({ logo_asset_id: 'asset-logo' });
  await user.click(await screen.findByRole('radio', { name: 'Bottom left' }));
  await brand({ logo_asset_id: 'asset-logo', logo_position: 'bottom_left' });
  await user.click(screen.getByRole('radio', { name: 'Large' }));
  await brand({ logo_max_height: 120 });
  await user.click(screen.getByRole('radio', { name: 'No logo' }));
  await brand({ logo_asset_id: '' });
});

it('uploads a logo and makes it the logo once the design has it', async () => {
  const current = live(
    heading,
    {},
    {
      upload: vi.fn(async () => ({
        resource_revision: 'r1',
        asset_id: 'asset-new',
      })),
    },
  );
  render(<ArtifactDesignControls {...current} view="brand" />);
  expect(
    await screen.findByRole('button', { name: 'Upload logo…' }),
  ).toBeEnabled();
  const file = new File(['synthetic'], 'brand.png', { type: 'image/png' });
  await act(async () =>
    fireEvent.change(screen.getByLabelText('Logo file'), {
      target: { files: [file] },
    }),
  );
  expect(current.upload).toHaveBeenCalledWith(file, 'r1');
  await waitFor(() =>
    expect(current.apply).toHaveBeenCalledWith(
      'brand',
      expect.objectContaining({ logo_asset_id: 'asset-new' }),
      'r1',
      'first',
      'element-a',
    ),
  );
});

const shelves = {
  blocks: [
    {
      id: 'hero_callout',
      label: 'Hero Callout',
      kind: 'Story',
      detail: 'Two-column opener',
      available: true,
    },
  ],
  assets: [
    item('asset-photo', 'image', 'photo.png'),
    item('asset-jingle', 'audio', 'jingle.wav'),
  ],
  presets: [item('preset-warm', 'preset', 'Warm')],
  interactions: [item('hop', 'navigate', 'navigate', 'first: second')],
};

it('searches blocks, images and styles in one grid and a tile inserts or applies itself', async () => {
  const user = userEvent.setup();
  Object.assign(URL, {
    createObjectURL: vi.fn(() => 'blob:thumbnail'),
    revokeObjectURL: vi.fn(),
  });
  const current = live(heading, shelves, {}, 'deck');
  render(<ArtifactDesignControls {...current} view="library" />);
  const tiles = await screen.findByRole('list', { name: 'Library items' });
  await waitFor(() =>
    expect(within(tiles).getAllByRole('listitem')).toHaveLength(5),
  );
  expect(within(tiles).getByText('Sound')).toBeInTheDocument();
  expect(within(tiles).getByText('Navigate')).toBeInTheDocument();
  const search = screen.getByRole('searchbox', { name: 'Search the library' });
  await user.type(search, 'hero');
  expect(within(tiles).getAllByRole('listitem')).toHaveLength(1);
  await user.click(screen.getByRole('button', { name: 'Insert Hero Callout' }));
  expect(current.apply).toHaveBeenLastCalledWith(
    'block_insert',
    { component_name: 'hero_callout' },
    'r1',
    'first',
    'element-a',
  );
  await settled('Design library');
  await user.clear(search);
  await user.click(screen.getByRole('button', { name: /^Your images/ }));
  expect(screen.getByRole('button', { name: /^Your images/ })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await user.click(
    await screen.findByRole('button', { name: 'Insert photo.png' }),
  );
  await waitFor(() =>
    expect(current.apply).toHaveBeenLastCalledWith(
      'asset_insert',
      { asset_id: 'asset-photo' },
      'r1',
      'first',
      'element-a',
    ),
  );
  await settled('Design library');
  await user.click(
    screen.getByRole('button', { name: 'More actions for photo.png' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Remove photo.png from page' }),
  );
  await waitFor(() =>
    expect(current.apply).toHaveBeenLastCalledWith(
      'asset_remove',
      { asset_id: 'asset-photo' },
      'r1',
      'first',
      'element-a',
    ),
  );
  await settled('Design library');
  await user.click(screen.getByRole('button', { name: 'Styles' }));
  await user.click(await screen.findByRole('button', { name: 'Apply Warm' }));
  await waitFor(() =>
    expect(current.apply).toHaveBeenLastCalledWith(
      'preset',
      { preset_id: 'preset-warm' },
      'r1',
      'first',
      'element-a',
    ),
  );
});

it('offers no blocks where the design type has none', async () => {
  render(<ArtifactDesignControls {...live(heading)} view="library" />);
  await screen.findByRole('button', { name: 'Styles' });
  expect(screen.queryByRole('button', { name: 'Blocks' })).toBeNull();
});

it('keeps chosen image bytes local until explicit upload and discloses retained recovery on failure', async () => {
  const current = live(
    heading,
    {},
    {
      upload: vi.fn(async () => {
        throw new Error('Revoked after bytes saved');
      }),
    },
  );
  render(<ArtifactDesignControls {...current} view="library" />);
  fireEvent.click(await screen.findByRole('button', { name: /^Your images/ }));
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
  expect(screen.getByText(/The file is kept for recovery/)).toBeInTheDocument();
  expect(current.upload).toHaveBeenCalledTimes(1);
});

it('saves the brand as a shared style on one click and keeps the design’s revision', async () => {
  const current = live(
    heading,
    {},
    {
      mutatePreset: vi.fn(async (options) => ({
        ...options,
        preset_id: 'saved',
        resource_id: 'design-a',
        resource_revision: 'r1',
      })),
    },
  );
  render(<ArtifactDesignControls {...current} view="library" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Styles' }));
  fireEvent.change(await screen.findByLabelText('New global preset name'), {
    target: { value: 'Shared brand' },
  });
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Save current brand as preset' }),
    ),
  );
  await screen.findByText('Style saved.');
  expect(current.mutatePreset).toHaveBeenCalledWith(
    { action: 'save', name: 'Shared brand' },
    'r1',
  );
  expect(current.apply).not.toHaveBeenCalled();
});

it('does not interpret a failed check as a clean review', async () => {
  const current = props({
    view: 'review',
    review: vi.fn(async () => {
      throw new Error('Unavailable');
    }),
  });
  render(<ArtifactDesignControls {...current} />);
  expect(await screen.findByText(/there is no result yet/)).toBeInTheDocument();
  // A failed check is not repeated in a loop.
  expect(current.review).toHaveBeenCalledOnce();
  expect(
    screen.queryByRole('group', { name: 'Design review findings' }),
  ).not.toBeInTheDocument();
  expect(current.apply).not.toHaveBeenCalled();
});

it('checks by itself, says it is heuristic and fixes one safe issue on request', async () => {
  const current = props({ view: 'review' });
  render(<ArtifactDesignControls {...current} />);
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

it('drafts an explicit review instruction into chat without submitting a provider request', async () => {
  const current = props({
    view: 'review',
    review: vi.fn(async () => ({
      ...report,
      findings: [{ ...report.findings[0], auto_fixable: false }],
    })),
    draftFix: vi.fn(async () => 'Focused fix instruction'),
    onDraftText: vi.fn(),
  });
  render(<ArtifactDesignControls {...current} />);
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Ask Row-Bot to fix: Missing spacing',
    }),
  );
  await screen.findByText(
    'A fix is drafted in the chat. Review it and send it.',
  );
  expect(current.draftFix).toHaveBeenCalledWith('finding-a', 'first', 'r1');
  expect(current.onDraftText).toHaveBeenCalledWith('Focused fix instruction');
  expect(current.apply).not.toHaveBeenCalled();
});

it('fixes all safe issues in one step, with the reviewed scope', async () => {
  const safe = (id: string) => ({ ...report.findings[0], id, message: id });
  const current = props({
    view: 'review',
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
  render(<ArtifactDesignControls {...current} />);
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
    view: 'review',
    load: vi.fn(async () => ({ ...state, resource_revision: revision })),
    review: vi.fn(async () => ({
      ...report,
      resource_revision: revision,
      findings: revision === 'r1' ? report.findings : [],
      finding_count: revision === 'r1' ? 1 : 0,
    })),
  });
  const view = render(<ArtifactDesignControls {...current} />);
  await screen.findByRole('button', { name: 'Fix: Missing spacing' });
  revision = 'r2';
  view.rerender(<ArtifactDesignControls {...current} resourceRevision="r2" />);
  expect(await screen.findByText('No issues found.')).toBeInTheDocument();
  expect(current.review).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole('button', { name: /Fix all/ })).toBeNull();
});
