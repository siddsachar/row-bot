import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PublicSkillHubIO } from './PublicSkillHub';
import PublicSkillHub from './PublicSkillHub';

const entry = {
  id: 'fixture:sample',
  name: 'Sample',
  description: 'Synthetic skill',
  source: 'fixture',
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
  primary_text: 'Use only fixtures.',
  files: ['SKILL.md'],
  scan: { blocked: false, findings: [], token_estimate: 24 },
};
function fixture(): PublicSkillHubIO {
  return {
    search: vi.fn().mockResolvedValue({
      schema_version: 1,
      revision: 'c'.repeat(64),
      mode: 'cache',
      query: 'sample',
      entries: [entry],
      source_statuses: [],
      error: '',
    }),
    preview: vi.fn().mockResolvedValue(preview),
    install: vi.fn().mockResolvedValue({
      schema_version: 1,
      command_id: crypto.randomUUID(),
      success: true,
      message: 'Skill installed disabled.',
      skill_name: 'sample',
    }),
    receipt: vi.fn().mockResolvedValue({
      schema_version: 1,
      command_id: crypto.randomUUID(),
      success: true,
      message: 'Skill installed disabled.',
      skill_name: 'sample',
    }),
  };
}
afterEach(() => sessionStorage.clear());

it('searches and previews only on click, then installs the exact scanned content', async () => {
  const io = fixture();
  const onInstalled = vi.fn();
  render(
    <PublicSkillHub io={io} ownerKey="session-a" onInstalled={onInstalled} />,
  );
  expect(io.search).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Search or source URL'), {
    target: { value: 'sample' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  expect(
    await screen.findByRole('button', { name: 'Inspect Sample' }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Sample' }));
  expect(await screen.findByText('Use only fixtures.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Install skill' }));
  await waitFor(() =>
    expect(io.install).toHaveBeenCalledWith(
      expect.objectContaining({
        preview_id: preview.preview_id,
        content_hash: preview.content_hash,
        make_available: false,
      }),
    ),
  );
  expect(await screen.findByText('Skill installed disabled.')).toBeVisible();
  expect(onInstalled).toHaveBeenCalledOnce();
});

it('blocks installation when the scan blocks it', async () => {
  const io = fixture();
  vi.mocked(io.preview).mockResolvedValue({
    ...preview,
    scan: { ...preview.scan, blocked: true },
  });
  render(<PublicSkillHub io={io} ownerKey="session-b" />);
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Inspect Sample' }),
  );
  expect(
    await screen.findByRole('button', { name: 'Install skill' }),
  ).toBeDisabled();
  expect(io.install).not.toHaveBeenCalled();
});

it('checks a retained receipt without starting another installation', async () => {
  sessionStorage.setItem(
    'row-bot-skill-hub-install:session-c',
    'original-command',
  );
  const io = fixture();
  render(<PublicSkillHub io={io} ownerKey="session-c" />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original result' }),
  );
  await waitFor(() =>
    expect(io.receipt).toHaveBeenCalledWith('original-command'),
  );
  expect(io.install).not.toHaveBeenCalled();
  expect(
    sessionStorage.getItem('row-bot-skill-hub-install:session-c'),
  ).toBeNull();
});

it('keeps a lost install record until the owner explicitly clears it', async () => {
  sessionStorage.setItem('row-bot-skill-hub-install:session-d', 'lost-command');
  const io = fixture();
  vi.mocked(io.receipt).mockRejectedValue({ code: 'skill_receipt_missing' });
  render(<PublicSkillHub io={io} ownerKey="session-d" />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original result' }),
  );
  expect(
    await screen.findByRole('button', {
      name: 'Clear lost record after inspecting Skill Library',
    }),
  ).toBeVisible();
  expect(sessionStorage.getItem('row-bot-skill-hub-install:session-d')).toBe(
    'lost-command',
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Clear lost record after inspecting Skill Library',
    }),
  );
  expect(
    sessionStorage.getItem('row-bot-skill-hub-install:session-d'),
  ).toBeNull();
  expect(io.install).not.toHaveBeenCalled();
});
