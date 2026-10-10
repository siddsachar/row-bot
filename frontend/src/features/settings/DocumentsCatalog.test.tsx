import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { DocumentSummaryPage } from '../../api/types';
import DocumentsCatalog, { DOCUMENTS_POLL_MS } from './DocumentsCatalog';

type Item = DocumentSummaryPage['items'][number];
const base: Item = {
  id: 'document',
  name: 'report.pdf',
  status: 'completed',
  stage: 'finalize',
  record_state: 'saved',
  index_current: null,
  index_total: null,
  extraction_current: null,
  extraction_total: null,
  updated_at: '',
  truncated: false,
  searchability: 'unknown',
  error_code: null,
};
function page(...items: Partial<Item>[]): DocumentSummaryPage {
  return {
    schema_version: 1,
    revision: 'one',
    availability: 'available',
    total: items.length,
    next_cursor: null,
    items: items.map((item, index) => ({
      ...base,
      id: `document-${index}`,
      ...item,
    })),
  };
}

afterEach(() => vi.useRealTimers());

it('says why a document failed on its row, in words and never as a code', async () => {
  render(
    <DocumentsCatalog
      load={async () =>
        page(
          {
            name: 'broken.pdf',
            status: 'failed',
            record_state: 'job_only',
            error_code: 'parse_failed',
          },
          {
            name: 'stopped.txt',
            status: 'cancelled',
            record_state: 'job_only',
          },
          { name: 'odd.txt', status: 'failed', error_code: 'future_code' },
        )
      }
    />,
  );
  const row = within(
    (await screen.findByText('broken.pdf')).closest('li') as HTMLElement,
  );
  expect(row.getByText(/couldn't read this file/)).toBeVisible();
  expect(screen.queryByText(/parse_failed|future_code/)).toBeNull();
  expect(screen.getByText('Cancelled before it finished.')).toBeVisible();
  expect(screen.getByText(/Something went wrong while adding/)).toBeVisible();
});

it('offers Remove on a document that never reached search as taking it off the list', async () => {
  const remove = vi.fn();
  render(
    <DocumentsCatalog
      load={async () =>
        page(
          { name: 'broken.pdf', status: 'failed', record_state: 'job_only' },
          { name: 'kept.pdf', status: 'failed', record_state: 'saved' },
        )
      }
      onRemove={remove}
    />,
  );
  fireEvent.click(await screen.findByText('broken.pdf'));
  expect(screen.getByText(/never reached search/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Remove broken.pdf' }));
  expect(remove).toHaveBeenLastCalledWith('document-0', 'broken.pdf', {
    listOnly: true,
  });
  fireEvent.click(screen.getByRole('button', { name: 'Remove kept.pdf' }));
  expect(remove).toHaveBeenLastCalledWith('document-1', 'kept.pdf', {
    listOnly: false,
  });
});

it('updates statuses by itself while documents are being added, then stops reading', async () => {
  vi.useFakeTimers();
  const load = vi
    .fn()
    .mockResolvedValueOnce(page({ status: 'queued' }))
    .mockResolvedValueOnce(page({ status: 'indexing' }))
    .mockResolvedValue(page({ status: 'completed' }));
  render(<DocumentsCatalog load={load} />);
  await act(() => vi.advanceTimersByTimeAsync(0));
  const row = () =>
    within(screen.getByText('report.pdf').closest('li') as HTMLElement);
  expect(row().getByText('Queued')).toBeVisible();
  await act(() => vi.advanceTimersByTimeAsync(DOCUMENTS_POLL_MS));
  expect(row().getByText('Reading')).toBeVisible();
  await act(() => vi.advanceTimersByTimeAsync(DOCUMENTS_POLL_MS));
  expect(row().getByText('Completed')).toBeVisible();
  await act(() => vi.advanceTimersByTimeAsync(DOCUMENTS_POLL_MS * 3));
  expect(load).toHaveBeenCalledTimes(3);
});
