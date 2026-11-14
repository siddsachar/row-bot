import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ArtifactExports, {
  type ArtifactExportResult,
  type ArtifactExportsProps,
} from './ArtifactExports';

const output: ArtifactExportResult = {
  export_id: 'export-a',
  resource_id: 'design-a',
  resource_revision: 'r1',
  format: 'pdf',
  pptx_mode: null,
  page_count: 2,
  filename: 'Design.pdf',
  media_type: 'application/pdf',
  size_bytes: 2048,
  sha256: 'digest',
  status: 'ready',
  warnings: [],
  expires_at: 1900000000,
};
function props(
  overrides: Partial<ArtifactExportsProps> = {},
): ArtifactExportsProps {
  return {
    resourceId: 'design-a',
    resourceRevision: 'r1',
    currentPageIndex: 1,
    pageCount: 3,
    visible: true,
    create: vi.fn(async () => output),
    download: vi.fn(async () => ({
      status: 'unavailable' as const,
      reason: 'user_gesture_required',
    })),
    ...overrides,
  };
}
const exportAs = (label: string) =>
  act(async () =>
    fireEvent.click(screen.getByRole('button', { name: `Export as ${label}` })),
  );

it('opens passively; picking a format exports, then asks where to save it', async () => {
  const download = vi.fn(async () => ({
    status: 'ok' as const,
    value: { kind: 'file' as const },
  }));
  const current = props({ download });
  render(<ArtifactExports {...current} />);
  expect(current.create).not.toHaveBeenCalled();
  expect(download).not.toHaveBeenCalled();
  await exportAs('PDF');
  expect(current.create).toHaveBeenCalledWith(
    { format: 'pdf', pages: 'all' },
    'r1',
  );
  expect(download).toHaveBeenCalledWith('export-a');
  expect(screen.getByRole('status')).toHaveTextContent('Saved · Design.pdf');
  expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
  // F17: once saved, the panel keeps only that line until asked again.
  expect(screen.queryByRole('group', { name: 'Export format' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Export again' }));
  expect(screen.getByRole('group', { name: 'Export format' })).toBeVisible();
  expect(screen.queryByText(/Saved ·/)).toBeNull();
});

it('a browser that needs a fresh click offers Download, without an error', async () => {
  const current = props();
  render(<ArtifactExports {...current} />);
  await exportAs('PDF');
  expect(screen.queryByText(/Saved/)).toBeNull();
  expect(screen.queryByText(/Export unavailable|again to save/)).toBeNull();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Download PDF' })),
  );
  expect(current.download).toHaveBeenLastCalledWith('export-a');
  expect(screen.getByText('Press it again to save the export.')).toBeVisible();
});

it('a cancelled Save dialog leaves Download to try again', async () => {
  const current = props({
    download: vi.fn(async () => ({ status: 'cancelled' as const })),
  });
  render(<ArtifactExports {...current} />);
  await exportAs('PDF');
  expect(screen.getByRole('button', { name: 'Download PDF' })).toBeEnabled();
  expect(screen.queryByText(/Saved|Couldn’t/)).toBeNull();
});

it.each([
  ['html', 'HTML'],
  ['png', 'PNG'],
  ['pptx', 'PowerPoint'],
])(
  'sends typed %s options and the exact current page',
  async (format, label) => {
    const current = props();
    render(<ArtifactExports {...current} />);
    fireEvent.change(screen.getByLabelText('Export pages'), {
      target: { value: 'current' },
    });
    fireEvent.change(screen.getByLabelText('PPTX mode'), {
      target: { value: 'structured' },
    });
    await exportAs(label);
    expect(current.create).toHaveBeenCalledWith(
      {
        format,
        pages: '2',
        ...(format === 'pptx' ? { pptx_mode: 'structured' } : {}),
      },
      'r1',
    );
  },
);

it('waits for the saved version before exporting after an edit', async () => {
  const current = props({ updating: true });
  const view = render(<ArtifactExports {...current} />);
  expect(screen.getByRole('button', { name: 'Export as PDF' })).toBeDisabled();
  expect(screen.getByText('Waiting for the saved version…')).toBeVisible();
  view.rerender(
    <ArtifactExports {...current} updating={false} resourceRevision="r2" />,
  );
  await exportAs('PDF');
  expect(current.create).toHaveBeenCalledWith(
    { format: 'pdf', pages: 'all' },
    'r2',
  );
});

it('preserves range options on incomplete failure and never offers its download', async () => {
  const current = props({
    create: vi.fn().mockRejectedValue({ code: 'export_incomplete' }),
  });
  render(<ArtifactExports {...current} />);
  fireEvent.change(screen.getByLabelText('Export pages'), {
    target: { value: 'range' },
  });
  expect(screen.getByRole('button', { name: 'Export as PDF' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Page range'), {
    target: { value: '1,3' },
  });
  await exportAs('PDF');
  expect(screen.getByLabelText('Page range')).toHaveValue('1,3');
  expect(
    screen.getByText(/partial local copy is retained/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /Download/ }),
  ).not.toBeInTheDocument();
  expect(current.create).toHaveBeenCalledTimes(1);
});

it('sends the person to set up Browser Automation when exports need it', async () => {
  const current = props({
    create: vi.fn().mockRejectedValue({ code: 'export_runtime_missing' }),
  });
  render(<ArtifactExports {...current} />);
  await exportAs('PDF');
  expect(
    screen.getByText(/Exports need Browser Automation/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('link', { name: 'Set up Browser Automation' }),
  ).toHaveAttribute('href', '/app-v2/settings/system#browser-computer-use');
});

it('shows offline asset warnings and captured older revision without rebuilding', async () => {
  const current = props({
    create: vi.fn(async (): Promise<ArtifactExportResult> => ({
      ...output,
      warnings: ['external_assets_unavailable'],
    })),
  });
  const view = render(<ArtifactExports {...current} />);
  await exportAs('PDF');
  view.rerender(<ArtifactExports {...current} resourceRevision="r2" />);
  expect(screen.getByText(/earlier saved version/)).toBeInTheDocument();
  expect(
    screen.getByText(/pictures from the web couldn't be included offline/),
  ).toBeInTheDocument();
  expect(current.create).toHaveBeenCalledTimes(1);
});

it('holds admission through resource changes and ignores late old completion', async () => {
  let finish!: (value: ArtifactExportResult) => void;
  const current = props({
    create: vi.fn(
      () =>
        new Promise<ArtifactExportResult>((resolve) => {
          finish = resolve;
        }),
    ),
  });
  const view = render(<ArtifactExports {...current} />);
  fireEvent.click(screen.getByRole('button', { name: 'Export as PDF' }));
  view.rerender(<ArtifactExports {...current} resourceId="design-b" />);
  expect(screen.getByRole('button', { name: 'Export as PDF' })).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Export as PDF' }),
  ).toHaveTextContent('Exporting…');
  await act(async () => finish(output));
  expect(screen.queryByText(/Design.pdf/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Export as PDF' })).toBeEnabled();
  expect(current.create).toHaveBeenCalledTimes(1);
});

it('clears download on current authority revocation without automatic retry', async () => {
  const current = props({
    download: vi.fn(async () => ({
      status: 'unavailable' as const,
      reason: 'capability_revoked',
    })),
  });
  render(<ArtifactExports {...current} />);
  await exportAs('PDF');
  expect(
    screen.queryByRole('button', { name: 'Download PDF' }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/Access to this design changed/)).toBeInTheDocument();
  expect(current.download).toHaveBeenCalledTimes(1);
});
