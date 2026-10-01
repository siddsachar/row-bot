import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { SkillHubSearchResult } from '../../api/types';
import type { PublicSkillHubIO } from './PublicSkillHub';
import PublicSkillHub from './PublicSkillHub';

const entry = {
  id: 'fixture:sample',
  name: 'Sample',
  description: 'Synthetic skill',
  source: 'github',
  author: '',
  trust_level: 'community',
  tags: [],
  installed: false,
};
const preview = {
  schema_version: 1 as const,
  preview_id: 'a'.repeat(32),
  content_hash: 'b'.repeat(64),
  entry,
  skill_name: 'sample',
  primary_text: 'Use only fixtures.',
  files: ['SKILL.md'],
  scan: { blocked: false, findings: [], token_estimate: 24 },
};
function found(
  overrides: Partial<SkillHubSearchResult> = {},
): SkillHubSearchResult {
  return {
    schema_version: 1,
    revision: 'c'.repeat(64),
    mode: 'cache',
    query: 'sample',
    entries: [entry],
    has_more: false,
    source_statuses: [],
    error: '',
    ...overrides,
  };
}
function receipt(message = 'Installed sample.') {
  return {
    schema_version: 1 as const,
    command_id: crypto.randomUUID(),
    success: true,
    message,
    skill_name: 'sample',
  };
}
function fixture(): PublicSkillHubIO {
  return {
    search: vi.fn().mockResolvedValue(found()),
    preview: vi.fn().mockResolvedValue(preview),
    install: vi.fn().mockResolvedValue(receipt()),
    receipt: vi.fn().mockResolvedValue(receipt()),
  };
}
async function openSample() {
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  fireEvent.click(await screen.findByRole('button', { name: 'View Sample' }));
  return screen.findByRole('dialog', { name: 'Sample' });
}
afterEach(() => sessionStorage.clear());

it('searches only on click and opens a skill in its own dialog', async () => {
  const io = fixture();
  render(<PublicSkillHub io={io} ownerKey="session-a" />);
  expect(io.search).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Search or source URL'), {
    target: { value: 'sample' },
  });
  const dialog = await openSample();
  expect(io.search).toHaveBeenCalledWith(
    { query: 'sample', source: 'all', refresh: false, limit: 24 },
    expect.any(AbortSignal),
  );
  expect(io.preview).toHaveBeenCalledWith(
    { revision: 'c'.repeat(64), entry_id: 'fixture:sample' },
    expect.any(AbortSignal),
  );
  // The SKILL.md shows inside the dialog, not below the results.
  expect(await within(dialog).findByText('Use only fixtures.')).toBeVisible();
  expect(
    within(dialog).getByRole('button', { name: 'Install skill' }),
  ).toBeEnabled();
});

it('installs the exact scanned content, available in chats unless turned off', async () => {
  const io = fixture();
  const onInstalled = vi.fn();
  render(
    <PublicSkillHub io={io} ownerKey="session-b" onInstalled={onInstalled} />,
  );
  const dialog = await openSample();
  const available = within(dialog).getByRole('switch', {
    name: 'Available in chats',
  });
  expect(available).toBeChecked();
  fireEvent.click(available);
  fireEvent.click(
    await within(dialog).findByRole('button', { name: 'Install skill' }),
  );
  await waitFor(() =>
    expect(io.install).toHaveBeenCalledWith(
      expect.objectContaining({
        preview_id: preview.preview_id,
        content_hash: preview.content_hash,
        make_available: false,
      }),
    ),
  );
  // The result shows where Install was clicked, and Install can't repeat.
  expect(await within(dialog).findByText('Installed sample.')).toBeVisible();
  expect(
    within(dialog).getByRole('button', { name: 'Install skill' }),
  ).toBeDisabled();
  expect(onInstalled).toHaveBeenCalledOnce();
});

it('refreshes the results after an install', async () => {
  const io = fixture();
  vi.mocked(io.search)
    .mockResolvedValueOnce(found())
    .mockResolvedValue(found({ entries: [{ ...entry, installed: true }] }));
  render(<PublicSkillHub io={io} ownerKey="session-c" />);
  const dialog = await openSample();
  fireEvent.click(
    await within(dialog).findByRole('button', { name: 'Install skill' }),
  );
  await waitFor(() => expect(io.search).toHaveBeenCalledTimes(2));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close dialog' }));
  const row = (await screen.findByText('Installed')).closest('li');
  expect(row).toHaveTextContent('Sample');
});

it('disables Install when a skill with that name is installed', async () => {
  const io = fixture();
  vi.mocked(io.preview).mockResolvedValue({
    ...preview,
    entry: { ...entry, installed: true },
  });
  render(<PublicSkillHub io={io} ownerKey="session-d" />);
  const dialog = await openSample();
  expect(
    await within(dialog).findByRole('button', { name: 'Install skill' }),
  ).toBeDisabled();
  expect(within(dialog).getByText(/Already installed as sample/)).toBeVisible();
  expect(io.install).not.toHaveBeenCalled();
});

it('blocks installation when the scan blocks it', async () => {
  const io = fixture();
  vi.mocked(io.preview).mockResolvedValue({
    ...preview,
    scan: { ...preview.scan, blocked: true },
  });
  render(<PublicSkillHub io={io} ownerKey="session-e" />);
  const dialog = await openSample();
  expect(
    await within(dialog).findByRole('button', { name: 'Install skill' }),
  ).toBeDisabled();
  expect(io.install).not.toHaveBeenCalled();
});

it('shows a preview failure in the dialog, in plain words', async () => {
  const io = fixture();
  vi.mocked(io.preview).mockRejectedValue({
    code: 'skill_preview_unavailable',
    status: 503,
  });
  render(<PublicSkillHub io={io} ownerKey="session-f" />);
  const dialog = await openSample();
  expect(
    await within(dialog).findByText(
      /couldn't read this skill's files from its source/,
    ),
  ).toBeVisible();
});

it('shows results as sources answer, then the rest', async () => {
  const io = fixture();
  const late = { ...entry, id: 'fixture:late', name: 'Late skill' };
  let answerLate: () => void = () => {};
  vi.mocked(io.search)
    .mockResolvedValueOnce(
      found({
        source_statuses: [
          { source_id: 'skills_sh', status: 'live', message: '' },
          { source_id: 'github', status: 'pending', message: '' },
        ],
      }),
    )
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answerLate = () => resolve(found({ entries: [entry, late] }));
        }),
    );
  render(<PublicSkillHub io={io} ownerKey="session-g" />);
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  expect(await screen.findByText('Still searching GitHub…')).toBeVisible();
  expect(screen.getByRole('button', { name: 'View Sample' })).toBeVisible();
  await act(async () => answerLate());
  expect(
    await screen.findByRole('button', { name: 'View Late skill' }),
  ).toBeVisible();
  expect(screen.queryByText('Still searching GitHub…')).toBeNull();
  expect(vi.mocked(io.search).mock.calls[1][0]).toEqual({
    query: '',
    source: 'all',
    refresh: false,
    limit: 24,
  });
});

it('loads more results on request', async () => {
  const io = fixture();
  vi.mocked(io.search).mockResolvedValue(found({ has_more: true }));
  render(<PublicSkillHub io={io} ownerKey="session-h" />);
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Load more skills' }),
  );
  await waitFor(() =>
    expect(io.search).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 48 }),
      expect.any(AbortSignal),
    ),
  );
});

it('checks a retained receipt without starting another installation', async () => {
  sessionStorage.setItem(
    'row-bot-skill-hub-install:session-i',
    'original-command',
  );
  const io = fixture();
  render(<PublicSkillHub io={io} ownerKey="session-i" />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original result' }),
  );
  await waitFor(() =>
    expect(io.receipt).toHaveBeenCalledWith(
      'original-command',
      expect.any(AbortSignal),
    ),
  );
  expect(io.install).not.toHaveBeenCalled();
  expect(
    sessionStorage.getItem('row-bot-skill-hub-install:session-i'),
  ).toBeNull();
});

it('keeps a lost install record until the owner explicitly clears it', async () => {
  sessionStorage.setItem('row-bot-skill-hub-install:session-j', 'lost-command');
  const io = fixture();
  vi.mocked(io.receipt).mockRejectedValue({ code: 'skill_receipt_missing' });
  render(<PublicSkillHub io={io} ownerKey="session-j" />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original result' }),
  );
  expect(
    await screen.findByRole('button', {
      name: 'Clear lost record after inspecting Skill Library',
    }),
  ).toBeVisible();
  expect(sessionStorage.getItem('row-bot-skill-hub-install:session-j')).toBe(
    'lost-command',
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Clear lost record after inspecting Skill Library',
    }),
  );
  expect(
    sessionStorage.getItem('row-bot-skill-hub-install:session-j'),
  ).toBeNull();
  expect(io.install).not.toHaveBeenCalled();
});
