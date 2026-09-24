import { useEffect, useState } from 'react';
import type { ArtifactDesignSession } from './artifact-design-sessions';
import type { ArtifactDocumentImportPreview } from '../../api/types';
import { Button, Field, Input, Toggle } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';

type Staged = { upload_id: string; sha256: string; size_bytes: number };

export default function ArtifactDocumentImport({
  open,
  onOpenChange,
  session,
  resourceId,
  resourceRevision,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  session: ArtifactDesignSession;
  resourceId: string;
  resourceRevision: string;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [prepared, setPrepared] = useState<{
    staged: Staged;
    preview: ArtifactDocumentImportPreview;
  } | null>(null);
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    setPrepared(null);
  }, [resourceId, resourceRevision]);

  async function preview() {
    if (!file || busy) return;
    setBusy(true);
    setError('');
    setPrepared(null);
    try {
      const result = await session.prepareImport(file, resourceRevision);
      if (result.preview.resource_id !== resourceId)
        throw new Error('resource_binding_revoked');
      setPrepared(result);
    } catch {
      setError(
        'The document could not be previewed. Check the file and current design, then retry.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function importPages() {
    if (!file || !prepared || busy) return;
    setBusy(true);
    setError('');
    try {
      session.guard();
      if (
        prepared.preview.resource_id !== resourceId ||
        prepared.preview.resource_revision !== resourceRevision ||
        prepared.preview.filename !== file.name
      )
        throw new Error('resource_revision_conflict');
      await session.importDocument({
        staged: prepared.staged,
        filename: file.name,
        revision: resourceRevision,
        replace,
      });
      setNotice(`${prepared.preview.page_count} page(s) imported.`);
      setPrepared(null);
      setFile(null);
      onOpenChange(false);
      onImported();
    } catch {
      setError(
        'Import was not confirmed. Check the original design command receipt before retrying.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title="Import PPTX or DOCX"
      description="Preview the pages before adding them to this design."
    >
      <Field label="Choose PPTX or DOCX">
        <Input
          type="file"
          aria-label="Choose PPTX or DOCX"
          accept=".pptx,.docx"
          disabled={busy}
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setPrepared(null);
            setError('');
          }}
        />
      </Field>
      <Button disabled={busy || !file} onClick={() => void preview()}>
        Preview pages
      </Button>
      {busy && <p role="status">Working on document…</p>}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {prepared && (
        <div className="stack">
          <p>{prepared.preview.page_count} page(s) found.</p>
          <ol>
            {prepared.preview.pages.map((page, index) => (
              <li key={index}>
                {page.title}
                {page.has_notes ? ' · Speaker notes' : ''}
              </li>
            ))}
          </ol>
          <div className="setting-row">
            <span>Replace existing pages</span>
            <Toggle
              label="Replace existing pages"
              checked={replace}
              onChange={(event) => setReplace(event.target.checked)}
            />
          </div>
          {replace && (
            <p>
              Replace all {prepared.preview.replacing_page_count} current pages.
              The prior design remains in history.
            </p>
          )}
          <Button disabled={busy} onClick={() => void importPages()}>
            {replace ? 'Replace pages' : 'Import pages'}
          </Button>
        </div>
      )}
    </ModalTask>
  );
}
