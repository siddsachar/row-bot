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
    download: vi.fn(async () => {}),
    ...overrides,
  };
}

it('opens passively and requires separate explicit export and download actions', async () => {
  const current = props();
  render(<ArtifactExports {...current} />);
  expect(current.create).not.toHaveBeenCalled();
  expect(current.download).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Export design' })),
  );
  expect(current.create).toHaveBeenCalledWith(
    { format: 'pdf', pages: 'all' },
    'r1',
  );
  expect(current.download).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Download PDF' })),
  );
  expect(current.download).toHaveBeenCalledWith('export-a');
});

it.each(['html', 'png', 'pptx'])(
  'sends typed %s options and exact current page',
  async (format) => {
    const current = props();
    render(<ArtifactExports {...current} />);
    fireEvent.change(screen.getByLabelText('Export format'), {
      target: { value: format },
    });
    fireEvent.change(screen.getByLabelText('Export pages'), {
      target: { value: 'current' },
    });
    if (format === 'pptx')
      fireEvent.change(screen.getByLabelText('PPTX mode'), {
        target: { value: 'structured' },
      });
    await act(async () =>
      fireEvent.click(screen.getByRole('button', { name: 'Export design' })),
    );
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

it('preserves range options on incomplete failure and never offers its download', async () => {
  const current = props({
    create: vi.fn().mockRejectedValue({ code: 'export_incomplete' }),
  });
  render(<ArtifactExports {...current} />);
  fireEvent.change(screen.getByLabelText('Export pages'), {
    target: { value: 'range' },
  });
  expect(screen.getByRole('button', { name: 'Export design' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Page range'), {
    target: { value: '1,3' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Export design' })),
  );
  expect(screen.getByLabelText('Page range')).toHaveValue('1,3');
  expect(
    screen.getByText(/partial local copy is retained/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /Download/ }),
  ).not.toBeInTheDocument();
  expect(current.create).toHaveBeenCalledTimes(1);
});

it('shows offline asset warnings and captured older revision without rebuilding', async () => {
  const current = props({
    create: vi.fn(async (): Promise<ArtifactExportResult> => ({
      ...output,
      warnings: ['external_assets_unavailable'],
    })),
  });
  const view = render(<ArtifactExports {...current} />);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Export design' })),
  );
  view.rerender(<ArtifactExports {...current} resourceRevision="r2" />);
  expect(screen.getByText(/earlier saved version/)).toBeInTheDocument();
  expect(
    screen.getByText(/External assets were unavailable offline/),
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
  fireEvent.click(screen.getByRole('button', { name: 'Export design' }));
  view.rerender(<ArtifactExports {...current} resourceId="design-b" />);
  expect(screen.getByRole('button', { name: 'Working…' })).toBeDisabled();
  await act(async () => finish(output));
  expect(screen.queryByText(/Design.pdf/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Export design' })).toBeEnabled();
  expect(current.create).toHaveBeenCalledTimes(1);
});

it('clears download on current authority revocation without automatic retry', async () => {
  const current = props({
    download: vi.fn().mockRejectedValue({ code: 'capability_revoked' }),
  });
  render(<ArtifactExports {...current} />);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Export design' })),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Download PDF' })),
  );
  expect(
    screen.queryByRole('button', { name: 'Download PDF' }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/Access to this design changed/)).toBeInTheDocument();
  expect(current.download).toHaveBeenCalledTimes(1);
});
