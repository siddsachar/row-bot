import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import {
  COMPANY_SPACE,
  ENGINEERING_SPACE,
  HANDBOOK,
} from '../../browser/fake-brain';
import type { FakeApi } from '../../browser/fake-api';
import { renderConsole, sent } from '../../testing';
import type { BrainDocument } from '../../types';
import { DOCUMENTS_POLL_MS } from './words';

const PAGE = `/knowledge?space=${COMPANY_SPACE}`;
const COMPANY_DOCUMENTS = `/api/brain/spaces/${COMPANY_SPACE}/documents`;

// Whether the page is hidden, as each test sets it (jsdom's own may vary).
let hidden = false;
beforeEach(() => {
  hidden = false;
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    get: () => hidden,
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, 'hidden');
});

function row(title: string) {
  return within(screen.getByRole('list', { name: 'Documents' }))
    .getByText(title, { selector: '.entity-row-title' })
    .closest('li') as HTMLElement;
}

function reads(api: FakeApi, path: string) {
  const got = api.calls.filter((call) => call.method === 'GET');
  return got.filter((call) => call.path === path).length;
}

/** Each poll's ticks, inside act: the reads they start land after it. */
function tick(times = 1) {
  act(() => {
    vi.advanceTimersByTime(DOCUMENTS_POLL_MS * times);
  });
}

function waiting(fields: Partial<BrainDocument>): BrainDocument {
  return {
    id: 'b9000000-0000-4000-8000-000000000001',
    space_id: COMPANY_SPACE,
    title: 'Onboarding',
    file_name: 'onboarding.md',
    media_type: 'text/markdown',
    byte_count: 2048,
    sha256: 'e'.repeat(64),
    status: 'queued',
    status_reason: 'Add an embeddings model in Models',
    version: 1,
    uploaded_by: '11111111-1111-4111-8111-111111111111',
    uploaded_at: new Date().toISOString(),
    processed_at: null,
    chunk_count: 2,
    graph_status: 'pending',
    graph_reason: null,
    ...fields,
  };
}

test('each document says its status, and why it stopped or waits, in plain words', async () => {
  renderConsole(PAGE);
  await screen.findByRole('list', { name: 'Documents' });
  expect(row('Employee handbook')).toHaveTextContent('Ready');
  expect(row('Employee handbook')).toHaveTextContent(
    'handbook.docx · 47 KB · Ada Lovelace ·',
  );
  expect(row('Expenses policy')).toHaveTextContent(
    'Only the start of this document is in the knowledge graph (its first 40 of 52 parts).',
  );
  expect(row('Office guide')).toHaveTextContent(
    'Its knowledge waits. Your monthly model budget is used up.',
  );
  expect(row('Signed contract scan')).toHaveTextContent('Failed');
  expect(row('Signed contract scan')).toHaveTextContent(
    'This Word document can’t be read.',
  );
  expect(
    screen.getByText(
      'Text, Markdown or Word files (.txt, .md, .docx), up to 25 MB each.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/queued|processing|pending|graph_/),
  ).not.toBeInTheDocument();
});

test('a document waiting for a model says which, links to Models, and is found by its words meanwhile', async () => {
  renderConsole(PAGE, 'populated', (url) =>
    url.pathname === COMPANY_DOCUMENTS
      ? {
          status: 200,
          body: {
            documents: [
              waiting({}),
              waiting({
                id: 'b9000000-0000-4000-8000-000000000002',
                title: 'Holidays',
                status: 'ready',
                status_reason: null,
                graph_status: 'waiting',
                graph_reason: 'Add a chat model in Models',
              }),
            ],
          },
        }
      : url.pathname === '/api/admin/models'
        ? { status: 200, body: { models: [] } }
        : undefined,
  );
  await screen.findByRole('list', { name: 'Documents' });
  expect(row('Onboarding')).toHaveTextContent('Waiting');
  expect(row('Onboarding')).toHaveTextContent(
    'Add an embeddings model in Models Row-Bot finds it by its words until then.',
  );
  expect(
    within(row('Onboarding')).getByRole('link', {
      name: 'Add an embeddings model in Models',
    }),
  ).toHaveAttribute('href', '/models?tab=catalog');
  expect(row('Holidays')).toHaveTextContent('Ready');
  expect(
    within(row('Holidays')).getByRole('link', {
      name: 'Add a chat model in Models',
    }),
  ).toHaveAttribute('href', '/models?tab=catalog');
  expect(
    await screen.findByText('No embeddings model yet'),
  ).toBeInTheDocument();
  expect(screen.getByText('No chat model yet')).toBeInTheDocument();
});

test('an upload shows at once, its status moves on by itself until it is ready, then the reading stops', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  const api = renderConsole(PAGE);
  await screen.findByRole('list', { name: 'Documents' });
  // Nothing in the company space is being read: the list isn't read again.
  tick(3);
  expect(reads(api, COMPANY_DOCUMENTS)).toBe(1);

  await userEvent.upload(
    screen.getByLabelText('Choose files to upload'),
    new File(['# Onboarding'], 'onboarding.md', { type: 'text/markdown' }),
  );
  await waitFor(() => expect(row('onboarding')).toHaveTextContent('Queued'));
  expect(sent(api, 'POST', COMPANY_DOCUMENTS)).toEqual({
    file: { name: 'onboarding.md', size: 12 },
  });

  tick();
  await waitFor(() =>
    expect(row('onboarding')).toHaveTextContent('Finding knowledge'),
  );
  tick();
  await waitFor(() => expect(row('onboarding')).toHaveTextContent('Ready'));
  expect(reads(api, COMPANY_DOCUMENTS)).toBe(3);

  tick(4);
  expect(reads(api, COMPANY_DOCUMENTS)).toBe(3);
});

test('the list is not read again while the page is hidden', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  const path = `/api/brain/spaces/${ENGINEERING_SPACE}/documents`;
  const api = renderConsole(`/knowledge?space=${ENGINEERING_SPACE}`);
  await screen.findByRole('list', { name: 'Documents' });
  expect(row('Release checklist')).toHaveTextContent('Reading');

  hidden = true;
  tick(4);
  expect(reads(api, path)).toBe(1);

  hidden = false;
  tick();
  await waitFor(() =>
    expect(row('Release checklist')).toHaveTextContent('Finding knowledge'),
  );
  expect(reads(api, path)).toBe(2);
});

test('files the server would refuse are refused at once, with the reason', async () => {
  const api = renderConsole(PAGE);
  await screen.findByRole('list', { name: 'Documents' });
  const zone = screen
    .getByText('Drop files here to add them to this space.')
    .closest('.console-dropzone') as HTMLElement;
  const big = new File(['x'], 'archive.md');
  Object.defineProperty(big, 'size', { value: 30 * 1024 * 1024 });
  const dataTransfer = {
    types: ['Files'],
    files: [new File(['%PDF-1.7'], 'plan.pdf'), big],
  };
  fireEvent.dragEnter(zone, { dataTransfer });
  expect(zone).toHaveAttribute('data-dragging', 'true');
  fireEvent.drop(zone, { dataTransfer });
  expect(zone).not.toHaveAttribute('data-dragging');

  const title = await screen.findByText('2 files weren’t added');
  const notice = title.closest('.console-notice') as HTMLElement;
  expect(notice).toHaveTextContent(
    "plan.pdf can't be added: only text, Markdown and Word files (.txt, .md, .docx) can be added.",
  );
  expect(notice).toHaveTextContent(
    'archive.md is larger than 25 MB, the most the server takes.',
  );
  expect(api.calls.some((call) => call.method === 'POST')).toBe(false);
  await userEvent.click(
    within(notice).getByRole('button', { name: 'Dismiss' }),
  );
  expect(screen.queryByText('2 files weren’t added')).not.toBeInTheDocument();
});

test('an upload the busy server turns away says to try again', async () => {
  renderConsole(PAGE, 'populated', (url, method) =>
    method === 'POST' && url.pathname === COMPANY_DOCUMENTS
      ? { status: 429, body: { error: 'too_many_uploads' } }
      : undefined,
  );
  await screen.findByRole('list', { name: 'Documents' });
  await userEvent.upload(
    screen.getByLabelText('Choose files to upload'),
    new File(['notes'], 'notes.txt', { type: 'text/plain' }),
  );
  expect(
    await screen.findByText(
      'The server is busy with other uploads. Try notes.txt again in a moment.',
    ),
  ).toBeInTheDocument();
  expect(screen.getByText('A file wasn’t added')).toBeInTheDocument();
  expect(screen.queryByText('Uploading')).not.toBeInTheDocument();
});

test('replacing asks first and names the new file; deleting asks first and says what is lost', async () => {
  const api = renderConsole(PAGE);
  await screen.findByRole('list', { name: 'Documents' });
  await userEvent.click(
    screen.getByRole('button', { name: 'Replace Employee handbook' }),
  );
  await userEvent.upload(
    screen.getByLabelText('Choose the new file'),
    new File(['new text'], 'handbook-2026.docx'),
  );
  const replace = screen.getByRole('alertdialog');
  expect(replace).toHaveTextContent(
    'Replace Employee handbook with handbook-2026.docx?',
  );
  expect(replace).toHaveTextContent('Its old text stops being found at once.');
  await userEvent.click(
    within(replace).getByRole('button', { name: 'Replace' }),
  );
  await waitFor(() =>
    expect(row('Employee handbook')).toHaveTextContent('Queued'),
  );
  expect(row('Employee handbook')).toHaveTextContent('handbook-2026.docx');
  expect(api.calls.find((call) => call.method === 'PUT')?.path).toBe(
    `/api/brain/documents/${HANDBOOK}`,
  );

  await userEvent.click(
    screen.getByRole('button', { name: 'Delete Expenses policy' }),
  );
  let confirm = screen.getByRole('alertdialog');
  expect(confirm).toHaveTextContent('Delete Expenses policy?');
  expect(confirm).toHaveTextContent(
    'facts written from it lose their source. This can’t be undone.',
  );
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel' }),
  );
  expect(api.calls.some((call) => call.method === 'DELETE')).toBe(false);

  await userEvent.click(
    screen.getByRole('button', { name: 'Delete Expenses policy' }),
  );
  confirm = screen.getByRole('alertdialog');
  await userEvent.click(
    within(confirm).getByRole('button', { name: 'Delete' }),
  );
  expect(
    await screen.findByText('Expenses policy is deleted'),
  ).toBeInTheDocument();
  const list = screen.getByRole('list', { name: 'Documents' });
  expect(within(list).queryByText('Expenses policy')).not.toBeInTheDocument();
});
