import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { DocumentSummaryPage } from '../../api/types';
import DocumentsCatalog from './DocumentsCatalog';

// The saved-library list (SavedCatalog) through Settings › Documents.
function documents(): DocumentSummaryPage {
  return {
    schema_version: 1,
    revision: 'one',
    availability: 'available',
    total: 1,
    next_cursor: null,
    items: [
      {
        id: 'document',
        name: 'report.txt',
        status: 'completed',
        stage: 'finalize',
        record_state: 'partial',
        index_current: 0,
        index_total: null,
        extraction_current: null,
        extraction_total: 4,
        updated_at: '',
        truncated: true,
        searchability: 'unknown',
      },
    ],
  };
}

it('does not regress rich saved document details', async () => {
  render(<DocumentsCatalog load={async () => documents()} />);
  fireEvent.click(await screen.findByText('report.txt'));
  const row = within(screen.getByText('report.txt').closest('li')!);
  // The status shows on the row and again in its details.
  expect(row.getAllByText('Completed')).toHaveLength(2);
  expect(row.getByText(/Partial — completion records/)).toBeVisible();
  expect(row.getByText('Current searchability')).toBeVisible();
});

it('does not regress explicit saved document removal', async () => {
  const remove = vi.fn();
  render(<DocumentsCatalog load={async () => documents()} onRemove={remove} />);
  await screen.findByText('report.txt');
  fireEvent.click(screen.getByRole('button', { name: 'Remove report.txt' }));
  expect(remove).toHaveBeenCalledWith('document', 'report.txt');
});

it('does not regress the saved document search contract', async () => {
  const user = userEvent.setup();
  const load = vi.fn(async () => documents());
  render(<DocumentsCatalog load={load} />);
  await screen.findByText('report.txt');
  await user.selectOptions(
    screen.getByLabelText('Saved document status'),
    'unknown',
  );
  // The filter applies at once; the search applies on Enter (or a pause).
  expect(load).toHaveBeenLastCalledWith(
    '',
    'unknown',
    undefined,
    expect.any(AbortSignal),
  );
  await user.type(screen.getByRole('searchbox'), 'report{Enter}');
  expect(load).toHaveBeenLastCalledWith(
    'report',
    'unknown',
    undefined,
    expect.any(AbortSignal),
  );
});

it('says there is nothing yet, not that a search missed, before anything is added', async () => {
  render(
    <DocumentsCatalog
      load={async () => ({ ...documents(), total: 0, items: [] })}
    />,
  );
  expect(await screen.findByText('No documents yet')).toBeVisible();
  expect(screen.queryByText(/Try another search/)).toBeNull();
});
