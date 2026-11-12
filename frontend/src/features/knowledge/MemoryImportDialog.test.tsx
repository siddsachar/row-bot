import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type {
  MemoryImportBatch,
  MemoryImportCandidate,
  MemoryImportPreviewItem,
  MemoryImportReceipt,
  MemoryImportSources,
} from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import MemoryImportDialog from './MemoryImportDialog';
import type { MemoryImportIO } from './memory-import-io';

const BATCH = 'a'.repeat(32);
const REVISION = 'b'.repeat(64);

function sources(over: Partial<MemoryImportSources> = {}): MemoryImportSources {
  return {
    sources: [
      {
        id: 'claude',
        name: 'Claude',
        export: 'memories',
        prompt_route: false,
        how_to_get: 'In Claude open Settings › Privacy › Export data.',
      },
      {
        id: 'chatgpt',
        name: 'ChatGPT',
        export: 'history',
        prompt_route: true,
        how_to_get: 'In ChatGPT open Settings › Data controls › Export data.',
      },
      {
        id: 'other',
        name: 'Another assistant',
        export: 'none',
        prompt_route: true,
        how_to_get: 'Paste the prompt into the assistant.',
      },
    ],
    prompt: 'category | date | fact',
    continue_prompt: 'Please continue',
    file_max_bytes: 8 * 1024 ** 3,
    conversation_limit: 200,
    character_limit: 400000,
    model: { name: 'qwen3:8b', provider: 'Local', on_this_computer: true },
    recent: [],
    ...over,
  };
}

function batch(over: Partial<MemoryImportBatch> = {}): MemoryImportBatch {
  return {
    batch_id: BATCH,
    status: 'open',
    files: [],
    texts: 0,
    job: null,
    scanned: false,
    found: 0,
    sources: [
      { source: 'pasted', label: 'Pasted text', facts: 3, conversations: 0 },
    ],
    warnings: [],
    skipped: [],
    skipped_count: 0,
    conversations: {
      available: 0,
      limit: 0,
      character_limit: 400000,
      ran: false,
      read: 0,
      characters: 0,
      found: 0,
    },
    model: { name: 'qwen3:8b', provider: 'Local', on_this_computer: true },
    ...over,
  };
}

const id = (n: number) => n.toString(16).padStart(20, '0');
const found: MemoryImportCandidate[] = [
  {
    candidate_id: id(1),
    text: 'Recordings of the app are made in light theme',
    category: 'preference',
    source: 'pasted',
    source_label: 'Pasted answer',
    date: '2026-10-01',
    origin: 'export',
  },
  {
    candidate_id: id(2),
    text: 'Plays the cello in a community orchestra',
    category: 'skill',
    source: 'pasted',
    source_label: 'Pasted answer',
    origin: 'export',
  },
  {
    candidate_id: id(3),
    text: 'Lives in a canal boat near Leeds',
    category: 'fact',
    source: 'pasted',
    source_label: 'Pasted answer',
    origin: 'export',
  },
];
const fromChats: MemoryImportCandidate = {
  candidate_id: id(4),
  text: 'Asked about grafting apple trees',
  category: 'fact',
  source: 'chatgpt',
  source_label: 'ChatGPT · Grafting',
  origin: 'conversation',
};

function receipt(over: Partial<MemoryImportReceipt> = {}): MemoryImportReceipt {
  return {
    batch_id: BATCH,
    command_id: '11111111-1111-4111-8111-111111111111',
    status: 'applied',
    applied_at: '2026-11-12T10:00:00',
    undo_until: '2026-11-19T10:00:00',
    sources: ['a pasted answer'],
    added: 1,
    updated: 0,
    superseded: 1,
    skipped: 0,
    failed: 0,
    ...over,
  };
}

function fakeIO(over: Partial<MemoryImportIO> = {}) {
  let scanned = batch();
  const io: MemoryImportIO = {
    sources: vi.fn(async () => sources()),
    begin: vi.fn(async () => batch()),
    batch: vi.fn(async () => scanned),
    discard: vi.fn(async () => ({ cancelled: true as const })),
    upload: vi.fn(async (_batch, file, progress) => {
      progress(file.size);
      return 'f'.repeat(32);
    }),
    removeFile: vi.fn(async () => ({ cancelled: true as const })),
    addText: vi.fn(async () => batch({ texts: 1 })),
    step: vi.fn(async (_batch, step) => {
      scanned = batch({
        scanned: true,
        found: 3,
        skipped: [
          { name: 'user.json', reason: 'Account details are never imported.' },
        ],
        skipped_count: 1,
        job: {
          kind: step === 'conversations' ? 'conversations' : 'scan',
          status: 'completed',
          done: 1,
          total: 1,
        },
        conversations: {
          available: 12,
          limit: 12,
          character_limit: 400000,
          ran: step === 'conversations',
          read: step === 'conversations' ? 12 : 0,
          characters: 900,
          found: step === 'conversations' ? 1 : 0,
        },
      });
      return {
        ...scanned,
        job: { ...scanned.job!, status: 'running' as const },
      };
    }),
    candidates: vi.fn(async () => ({
      batch_id: BATCH,
      items: vi
        .mocked(io.step)
        .mock.calls.some(([, step]) => step === 'conversations')
        ? [...found, fromChats]
        : found,
    })),
    preview: vi.fn(async (_batch, items) => {
      scanned = {
        ...scanned,
        job: {
          kind: 'preview',
          status: 'completed',
          done: items.length,
          total: items.length,
        },
      };
      return scanned;
    }),
    readPreview: vi.fn(async () => ({
      batch_id: BATCH,
      revision: REVISION,
      items: [
        {
          candidate_id: id(1),
          action: 'supersede',
          text: found[0].text,
          category: 'preference',
          source_label: 'Pasted answer',
          subject: 'Recording theme',
          saved_subject: 'App recordings theme',
          saved_text: 'Recordings of the app are made in dark theme.',
        },
        {
          candidate_id: id(2),
          action: 'add',
          text: 'Plays the viola in a community orchestra',
          category: 'skill',
          source_label: 'Pasted answer',
          subject: 'Plays the viola',
        },
      ] satisfies MemoryImportPreviewItem[],
    })),
    apply: vi.fn(async () => receipt()),
    undo: vi.fn(async () =>
      receipt({ status: 'undone', undone_at: '2026-11-12T10:05:00' }),
    ),
    ...over,
  };
  return io;
}

function show(
  io: MemoryImportIO,
  onImported = vi.fn(),
  onOpenChange = vi.fn(),
) {
  const view = render(
    <OverlayProvider>
      <MemoryImportDialog
        open
        onOpenChange={onOpenChange}
        io={io}
        onImported={onImported}
      />
    </OverlayProvider>,
  );
  return { ...view, onImported, onOpenChange };
}

async function pasteAndFind() {
  fireEvent.click(await screen.findByRole('radio', { name: /ChatGPT/ }));
  expect(
    screen.getByText(/Settings › Data controls › Export data/),
  ).toBeVisible();
  fireEvent.change(
    screen.getByRole('textbox', { name: /Paste its answer here/ }),
    {
      target: { value: '```\npreference | unknown | Likes light theme\n```' },
    },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Find memories' }));
  await screen.findByRole('list', { name: 'Memories found' });
}

describe('Import memories', () => {
  it('finds, edits, previews, imports and undoes, saving nothing before Import', async () => {
    const io = fakeIO();
    const { onImported } = show(io);
    const dialog = await screen.findByRole('dialog', {
      name: 'Import memories',
    });
    expect(
      within(dialog).getByRole('radiogroup', { name: 'Where from' }),
    ).toBeVisible();
    await pasteAndFind();

    expect(io.addText).toHaveBeenCalledWith(
      BATCH,
      expect.stringContaining('Likes light theme'),
    );
    expect(
      screen.getByText('3 memories found · 1 skipped · 3 chosen'),
    ).toBeVisible();
    fireEvent.click(screen.getByText('1 note about your files'));
    expect(
      screen.getByText(/Account details are never imported/),
    ).toBeVisible();

    fireEvent.click(
      screen.getByRole('checkbox', {
        name: 'Lives in a canal boat near Leeds',
      }),
    );
    const cello = screen
      .getByText('Plays the cello in a community orchestra')
      .closest('li')!;
    fireEvent.click(within(cello).getByRole('button', { name: 'Edit memory' }));
    fireEvent.change(within(cello).getByRole('textbox', { name: 'Memory' }), {
      target: { value: 'Plays the viola in a community orchestra' },
    });
    fireEvent.click(within(cello).getByRole('button', { name: 'Save edit' }));
    expect(
      screen.getByText('Plays the viola in a community orchestra'),
    ).toBeVisible();
    expect(io.apply).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Preview 2 memories' }));
    await screen.findByRole('list', { name: 'What each memory does' });
    expect(io.preview).toHaveBeenCalledWith(BATCH, [
      { candidate_id: id(1) },
      { candidate_id: id(2), text: 'Plays the viola in a community orchestra' },
    ]);
    expect(screen.getByText('Replaces “App recordings theme”')).toBeVisible();
    expect(
      screen.getByText(
        /Kept as history: Recordings of the app are made in dark theme/,
      ),
    ).toBeVisible();
    const viola = within(
      screen.getByRole('list', { name: 'What each memory does' }),
    )
      .getByText('Plays the viola in a community orchestra')
      .closest('li')!;
    expect(within(viola).getByText('New')).toBeVisible();
    expect(io.apply).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Import 2 memories' }));
    await screen.findByText('Imported from a pasted answer');
    expect(io.apply).toHaveBeenCalledWith(
      BATCH,
      expect.objectContaining({ preview_revision: REVISION, exclude: [] }),
    );
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/You can undo this import until/)).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Undo import' }));
    await screen.findByText('Import undone');
    expect(io.undo).toHaveBeenCalledWith(BATCH, expect.any(String));
    expect(onImported).toHaveBeenCalledTimes(2);
  });

  it('looks in past conversations only when turned on, naming the model first', async () => {
    const io = fakeIO();
    show(io);
    await pasteAndFind();
    expect(
      screen.getByText('Also look for memories in 12 past conversations'),
    ).toBeVisible();
    expect(screen.getByText(/Uses qwen3:8b on this computer/)).toBeVisible();
    expect(io.step).not.toHaveBeenCalledWith(BATCH, 'conversations');

    fireEvent.click(
      screen.getByRole('switch', { name: 'Also look in past conversations' }),
    );
    await screen.findByText('Asked about grafting apple trees');
    expect(io.step).toHaveBeenCalledWith(BATCH, 'conversations');
    expect(
      screen.getByText('Found 1 more memory in 12 conversations.'),
    ).toBeVisible();
    expect(
      screen.getByText('4 memories found · 1 skipped · 4 chosen'),
    ).toBeVisible();

    // Off again: what was found in conversations is left out.
    fireEvent.click(
      screen.getByRole('switch', { name: 'Also look in past conversations' }),
    );
    expect(screen.queryByText('Asked about grafting apple trees')).toBeNull();
    expect(
      screen.getByText('3 memories found · 1 skipped · 3 chosen'),
    ).toBeVisible();
  });

  it('sends picked export files before scanning, and says when a source has no file', async () => {
    const io = fakeIO();
    show(io);
    await screen.findByRole('radio', { name: /Claude/ });
    expect(
      screen.queryByRole('textbox', { name: /Paste its answer here/ }),
    ).toBeNull();
    const file = new File(['{}'], 'claude-export.zip', {
      type: 'application/zip',
    });
    fireEvent.change(screen.getByLabelText('Choose export files'), {
      target: { files: [file] },
    });
    expect(
      within(screen.getByRole('list', { name: 'Files to import' })).getByText(
        'claude-export.zip',
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Find memories' }));
    await screen.findByRole('list', { name: 'Memories found' });
    expect(io.upload).toHaveBeenCalledWith(
      BATCH,
      file,
      expect.any(Function),
      expect.any(AbortSignal),
    );
    expect(io.step).toHaveBeenCalledWith(BATCH, 'scan');

    fireEvent.click(screen.getByRole('button', { name: 'Back to where from' }));
    fireEvent.click(screen.getByRole('radio', { name: /Another app/ }));
    expect(screen.queryByLabelText('Choose export files')).toBeNull();
    expect(screen.getByRole('button', { name: 'Copy prompt' })).toBeVisible();
  });

  it('shows what went wrong, with a way on', async () => {
    const io = fakeIO({
      sources: vi
        .fn()
        .mockRejectedValueOnce({ code: 'dependency_unavailable' })
        .mockResolvedValue(sources()),
    });
    show(io);
    expect(
      await screen.findByText("Import isn't available right now"),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await screen.findByRole('radiogroup', { name: 'Where from' });

    vi.mocked(io.step).mockImplementationOnce(async () =>
      batch({
        job: {
          kind: 'scan',
          status: 'failed',
          done: 0,
          total: 1,
          code: 'import_scan_failed',
        },
      }),
    );
    vi.mocked(io.batch).mockResolvedValueOnce(
      batch({
        job: {
          kind: 'scan',
          status: 'failed',
          done: 0,
          total: 1,
          code: 'import_scan_failed',
        },
      }),
    );
    fireEvent.click(screen.getByRole('radio', { name: /ChatGPT/ }));
    fireEvent.change(
      screen.getByRole('textbox', { name: /Paste its answer here/ }),
      { target: { value: 'x' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Find memories' }));
    expect(await screen.findByText(/couldn't read those files/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Find memories' })).toBeVisible();
  });

  it('an empty export says so, and closing before importing lets the batch go', async () => {
    const io = fakeIO({
      candidates: vi.fn(async () => ({ batch_id: BATCH, items: [] })),
    });
    const { unmount } = show(io);
    fireEvent.click(await screen.findByRole('radio', { name: /ChatGPT/ }));
    fireEvent.change(
      screen.getByRole('textbox', { name: /Paste its answer here/ }),
      { target: { value: 'x' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Find memories' }));
    expect(await screen.findByText('No memories found')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
    unmount();
    await waitFor(() => expect(io.discard).toHaveBeenCalledWith(BATCH));
  });

  it('lists recent imports with Undo', async () => {
    const io = fakeIO({
      sources: vi.fn(async () =>
        sources({ recent: [receipt({ sources: ['ChatGPT'] })] }),
      ),
    });
    show(io);
    const recent = await screen.findByRole('list', { name: 'Recent imports' });
    expect(within(recent).getByText('From ChatGPT')).toBeVisible();
    fireEvent.click(
      within(recent).getByRole('button', { name: 'Undo this import' }),
    );
    expect(
      await screen.findByText('Import undone. The memories it added are gone.'),
    ).toBeVisible();
    expect(screen.queryByRole('list', { name: 'Recent imports' })).toBeNull();
  });
});
