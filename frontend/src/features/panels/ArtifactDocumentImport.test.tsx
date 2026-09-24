import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ArtifactDocumentImport from './ArtifactDocumentImport';
import type { ArtifactDesignSession } from './artifact-design-sessions';

function setup() {
  const preview = {
    resource_id: 'design-a',
    resource_revision: 'r1',
    filename: 'synthetic.docx',
    source_sha256: 'a'.repeat(64),
    page_count: 1,
    pages: [{ title: 'Synthetic page', has_notes: true }],
    replacing_page_count: 2,
  };
  const staged = {
    upload_id: 'upload-a',
    sha256: preview.source_sha256,
    size_bytes: 3,
  };
  const session = {
    guard: vi.fn(),
    prepareImport: vi.fn(async () => ({ preview, staged })),
    importDocument: vi.fn(async () => ({
      resource_id: 'design-a',
      resource_revision: 'r2',
    })),
  } as unknown as ArtifactDesignSession;
  const onImported = vi.fn(),
    onOpenChange = vi.fn();
  render(
    <ArtifactDocumentImport
      open
      onOpenChange={onOpenChange}
      session={session}
      resourceId="design-a"
      resourceRevision="r1"
      onImported={onImported}
    />,
  );
  const file = new File(['zip'], 'synthetic.docx');
  fireEvent.change(screen.getByLabelText('Choose PPTX or DOCX'), {
    target: { files: [file] },
  });
  return { session, onImported, onOpenChange, file };
}

it('stages and previews only after the user clicks, then imports once', async () => {
  const current = setup();
  expect(current.session.prepareImport).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Preview pages' })),
  );
  expect(current.session.prepareImport).toHaveBeenCalledWith(
    current.file,
    'r1',
  );
  expect(screen.getByText(/Synthetic page.*Speaker notes/)).toBeVisible();
  expect(current.session.importDocument).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Import pages' })),
  );
  expect(current.session.importDocument).toHaveBeenCalledWith(
    expect.objectContaining({
      filename: 'synthetic.docx',
      revision: 'r1',
      replace: false,
    }),
  );
  expect(current.onImported).toHaveBeenCalledOnce();
});

it('replaces all pages with one click after the user chooses replacement', async () => {
  const current = setup();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Preview pages' })),
  );
  fireEvent.click(
    screen.getByRole('switch', { name: 'Replace existing pages' }),
  );
  expect(screen.getByRole('button', { name: 'Replace pages' })).toBeEnabled();
  expect(current.session.importDocument).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Replace pages' })),
  );
  expect(current.session.importDocument).toHaveBeenCalledWith(
    expect.objectContaining({ replace: true }),
  );
});
