import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { CompanyDocument, CompanyDocumentsView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import CompanyDocuments, {
  COMPANY_DOCUMENTS_POLL_MS,
  type CompanyDocumentsController,
} from './CompanyDocuments';

// Settings › Documents' company documents (Row-Bot for Teams, B3): shown only
// for spaces the person owns; add, replace and delete with the server's limits
// checked first; every status in words; and the list read again only while
// the server works on a document and the page is in view.

afterEach(() => {
  vi.useRealTimers();
});

const COMPANY = 'rowbot.example.test';
const SALES = '5a000000-0000-4000-8000-000000000002';
const MARKETING = '5a000000-0000-4000-8000-000000000003';
const ACCOUNTS = 'd0000000-0000-4000-8000-000000000002';

function doc(over: Partial<CompanyDocument> = {}): CompanyDocument {
  return {
    id: ACCOUNTS,
    title: 'Accounts 2026',
    file_name: 'accounts.md',
    byte_count: 2048,
    status: 'ready',
    status_reason: null,
    graph_status: 'done',
    graph_reason: null,
    version: 1,
    uploaded_at: '2026-11-20T09:00:00+00:00',
    ...over,
  };
}

const owned: CompanyDocumentsView = {
  schema_version: 1,
  state: 'ready',
  company: COMPANY,
  reason: null,
  spaces: [{ id: SALES, name: 'Sales', kind: 'group', is_owner: true }],
  space_id: SALES,
  documents: [doc()],
  max_upload_bytes: 25 * 1024 * 1024,
  supported_types: ['.docx', '.markdown', '.md', '.txt'],
  outcome: null,
};

function show(controller: Partial<CompanyDocumentsController>) {
  return render(
    <MemoryRouter>
      <OverlayProvider>
        <CompanyDocuments
          controller={controller as CompanyDocumentsController}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
}

const fileInput = () =>
  document.querySelector('input[type="file"]') as HTMLInputElement;

function choose(file: File) {
  fireEvent.change(fileInput(), { target: { files: [file] } });
}

it('shows nothing without a company, without a space of the person’s, or outside the app', async () => {
  for (const answer of [
    { ...owned, state: 'not_joined', spaces: [], space_id: null },
    { ...owned, spaces: [], space_id: null, documents: [] },
  ] as CompanyDocumentsView[]) {
    const read = vi.fn(async () => answer);
    const { unmount } = show({ companyDocuments: read });
    await waitFor(() => expect(read).toHaveBeenCalledOnce());
    await act(async () => {});
    expect(screen.queryByText('Company documents')).toBeNull();
    unmount();
  }
  show({});
  await act(async () => {});
  expect(screen.queryByText('Company documents')).toBeNull();
});

it('lists the owned space’s documents with each status and reason in words', async () => {
  const read = vi.fn(async () => ({
    ...owned,
    spaces: [
      ...owned.spaces,
      {
        id: MARKETING,
        name: 'Marketing',
        kind: 'group' as const,
        is_owner: true,
      },
    ],
    documents: [
      doc(),
      doc({
        id: 'd2',
        title: 'Old prices',
        file_name: 'prices.docx',
        byte_count: 4096,
        status: 'failed',
        status_reason: "This Word document can't be read.",
      }),
      doc({
        id: 'd3',
        title: 'Holiday notes',
        status: 'queued',
        status_reason: 'Add an embeddings model in Models',
      }),
      doc({ id: 'd4', title: 'Price list', status: 'processing' }),
      doc({ id: 'd5', title: 'Kickoff', graph_status: 'extracting' }),
      doc({
        id: 'd6',
        title: 'Org chart',
        graph_status: 'waiting',
        graph_reason: 'Add a chat model in Models',
      }),
    ],
  }));
  show({ companyDocuments: read });

  const list = await screen.findByRole('list', {
    name: 'Documents in Sales',
  });
  const row = (title: string) =>
    within(within(list).getByText(title).closest('li') as HTMLElement);
  expect(row('Accounts 2026').getByText('Ready')).toBeVisible();
  expect(row('Accounts 2026').getByText('accounts.md · 2 KB')).toBeVisible();
  expect(row('Old prices').getByText('Couldn’t be read')).toBeVisible();
  expect(
    row('Old prices').getByText(
      "prices.docx · 4 KB · This Word document can't be read.",
    ),
  ).toBeVisible();
  expect(row('Holiday notes').getByText('Waiting')).toBeVisible();
  expect(
    row('Holiday notes').getByText(
      /your company’s admin hasn’t added a search model yet/,
    ),
  ).toBeVisible();
  expect(row('Price list').getByText('Reading')).toBeVisible();
  expect(row('Kickoff').getByText('Finding knowledge')).toBeVisible();
  expect(row('Org chart').getByText('Searchable')).toBeVisible();
  expect(
    row('Org chart').getByText(
      /waits for your company’s admin to add a chat model/,
    ),
  ).toBeVisible();
  // The server's codes never show.
  expect(screen.queryByText(/queued|processing|extracting/)).toBeNull();
  expect(
    screen.getByText(
      'What you add to Sales goes to rowbot.example.test’s brain, for everyone who reads that space. Add .docx, .markdown, .md or .txt files, up to 25 MB each.',
    ),
  ).toBeVisible();

  // The person owns two spaces: the picker reads the other one's documents.
  fireEvent.change(screen.getByRole('combobox', { name: 'Space' }), {
    target: { value: MARKETING },
  });
  await waitFor(() => expect(read).toHaveBeenLastCalledWith(MARKETING));
  expect(
    screen.getByRole('button', { name: 'Add a document to Sales' }),
  ).toBeVisible();
});

it('adds a file, refusing one of another type or too large before anything is sent', async () => {
  const upload = vi.fn(async () => ({
    ...owned,
    max_upload_bytes: 1024 * 1024,
    documents: [
      doc({
        id: 'd9',
        title: 'notes',
        file_name: 'notes.txt',
        status: 'queued',
        graph_status: 'pending',
      }),
      doc(),
    ],
    outcome: 'uploaded' as const,
  }));
  show({
    companyDocuments: vi.fn(async () => ({
      ...owned,
      max_upload_bytes: 1024 * 1024,
    })),
    uploadCompanyDocument: upload,
  });

  fireEvent.click(
    await screen.findByRole('button', { name: 'Add a document to Sales' }),
  );
  expect(fileInput()).toHaveAttribute('accept', '.docx,.markdown,.md,.txt');
  choose(new File(['x'], 'photo.png'));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'photo.png can’t be added. Add .docx, .markdown, .md or .txt files.',
  );
  choose(new File([new Uint8Array(1024 * 1024 + 1)], 'big.txt'));
  expect(screen.getByRole('alert')).toHaveTextContent(
    'big.txt is larger than the 1 MB your company allows.',
  );
  expect(upload).not.toHaveBeenCalled();

  const file = new File(['Holiday policy: 25 days.'], 'notes.txt', {
    type: 'text/plain',
  });
  choose(file);
  await waitFor(() =>
    expect(upload).toHaveBeenCalledWith(SALES, file, 'notes.txt'),
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'notes.txt was added. Your company’s server reads it next.',
  );
  expect(screen.getByText('Waiting to be read')).toBeVisible();
});

it('says the server’s refusals, and a busy server, in plain words', async () => {
  const read = vi.fn(async () => owned);
  const upload = vi
    .fn()
    .mockResolvedValueOnce({
      ...owned,
      supported_types: ['.md', '.txt'],
      outcome: 'unsupported_type',
    })
    .mockResolvedValueOnce({
      ...owned,
      max_upload_bytes: 1024 * 1024,
      outcome: 'too_large',
    })
    .mockResolvedValueOnce({
      ...owned,
      state: 'unreachable',
      reason: 'busy',
      spaces: [],
      space_id: null,
      documents: [],
    });
  show({ companyDocuments: read, uploadCompanyDocument: upload });
  const add = await screen.findByRole('button', {
    name: 'Add a document to Sales',
  });

  fireEvent.click(add);
  choose(new File(['x'], 'notes.docx'));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'notes.docx can’t be added. Add .md or .txt files.',
  );
  fireEvent.click(add);
  choose(new File(['x'], 'notes.md'));
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent(
      'notes.md is larger than the 1 MB your company allows.',
    ),
  );
  fireEvent.click(add);
  choose(new File(['x'], 'notes.md'));
  expect(
    await screen.findByText(
      `${COMPANY} is busy. Row-Bot stays joined; try again in a minute.`,
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  expect(await screen.findByText('Accounts 2026')).toBeVisible();
});

it('replaces and deletes a document only once confirmed', async () => {
  const after = { ...owned, documents: [doc({ version: 2 })] };
  const replace = vi.fn(async () => ({
    ...after,
    outcome: 'replaced' as const,
  }));
  const remove = vi.fn(async () => ({
    ...owned,
    documents: [],
    outcome: 'removed' as const,
  }));
  show({
    companyDocuments: vi.fn(async () => owned),
    replaceCompanyDocument: replace,
    removeCompanyDocument: remove,
  });

  fireEvent.click(
    await screen.findByRole('button', { name: 'Replace Accounts 2026' }),
  );
  const file = new File(['# Accounts'], 'accounts-v2.md');
  choose(file);
  const replacing = await screen.findByRole('alertdialog', {
    name: 'Replace Accounts 2026?',
  });
  expect(replacing).toHaveTextContent('accounts-v2.md takes its place.');
  fireEvent.click(within(replacing).getByRole('button', { name: 'Replace' }));
  await waitFor(() =>
    expect(replace).toHaveBeenCalledWith(ACCOUNTS, file, 'accounts-v2.md'),
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'accounts-v2.md replaced the earlier file.',
  );

  fireEvent.click(screen.getByRole('button', { name: 'Delete Accounts 2026' }));
  const kept = await screen.findByRole('alertdialog', {
    name: 'Delete Accounts 2026?',
  });
  expect(kept).toHaveTextContent('Facts taken from it stay');
  fireEvent.click(within(kept).getByRole('button', { name: 'Keep it' }));
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  expect(remove).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Delete Accounts 2026' }));
  const deleting = await screen.findByRole('alertdialog', {
    name: 'Delete Accounts 2026?',
  });
  fireEvent.click(within(deleting).getByRole('button', { name: 'Delete' }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith(ACCOUNTS));
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Accounts 2026 was deleted.',
  );
  expect(screen.getByText('No documents in Sales yet.')).toBeVisible();
});

it('reads the list again only while the server works on a document and the page is in view', async () => {
  vi.useFakeTimers();
  const working = () => ({
    ...owned,
    documents: [doc({ status: 'processing' })],
  });
  const read = vi
    .fn()
    .mockImplementationOnce(async () => working())
    .mockImplementationOnce(async () => working())
    .mockImplementationOnce(async () => working())
    .mockImplementation(async () => ({ ...owned }));
  let visibility: DocumentVisibilityState = 'visible';
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => visibility,
  });
  try {
    show({ companyDocuments: read });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(read).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(COMPANY_DOCUMENTS_POLL_MS));
    expect(read).toHaveBeenCalledTimes(2);

    visibility = 'hidden';
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await act(() => vi.advanceTimersByTimeAsync(COMPANY_DOCUMENTS_POLL_MS * 3));
    expect(read).toHaveBeenCalledTimes(2);

    visibility = 'visible';
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await act(() => vi.advanceTimersByTimeAsync(COMPANY_DOCUMENTS_POLL_MS));
    expect(read).toHaveBeenCalledTimes(3);
    await act(() => vi.advanceTimersByTimeAsync(COMPANY_DOCUMENTS_POLL_MS));
    expect(read).toHaveBeenCalledTimes(4); // Nothing is being worked on now.
    await act(() => vi.advanceTimersByTimeAsync(COMPANY_DOCUMENTS_POLL_MS * 3));
    expect(read).toHaveBeenCalledTimes(4);
  } finally {
    delete (document as { visibilityState?: unknown }).visibilityState;
  }
});
