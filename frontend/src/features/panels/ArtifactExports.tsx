import { useEffect, useRef, useState } from 'react';
import { Button, ErrorState, Field, Input, Select } from '../../ui/primitives';

import type { ArtifactExport } from '../../api/types';

export type ArtifactExportResult = ArtifactExport;
export type ArtifactExportOptions = {
  format: 'pdf' | 'html' | 'png' | 'pptx';
  pages: string;
  pptx_mode?: 'screenshot' | 'structured';
};
export type ArtifactExportsProps = {
  resourceId: string;
  resourceRevision: string;
  currentPageIndex: number;
  pageCount: number;
  visible: boolean;
  create: (
    options: ArtifactExportOptions,
    expectedRevision: string,
  ) => Promise<ArtifactExportResult>;
  download: (exportId: string) => Promise<void>;
};

function failure(reason: unknown) {
  const code =
    typeof reason === 'object' && reason !== null && 'code' in reason
      ? String(reason.code)
      : '';
  const denied = [
    'action_denied',
    'capability_revoked',
    'resource_binding_revoked',
    'not_found',
  ].includes(code);
  const text = denied
    ? 'Access to this design changed. Review its binding before continuing.'
    : code === 'resource_revision_conflict'
      ? 'The saved design changed. Review its current version before exporting again.'
      : code === 'export_expired'
        ? 'This download expired. Export the current saved version again.'
        : code === 'invalid_page_range'
          ? 'Choose page numbers within this design, such as 1-3 or 1,3,5.'
          : code === 'export_capacity_reached'
            ? 'Export storage is full. Existing copies are preserved; review local export recovery before retrying.'
            : 'The export could not be completed. Any partial local copy is retained; no complete download is available for this attempt.';
  return { denied, text };
}

export default function ArtifactExports(props: ArtifactExportsProps) {
  const [format, setFormat] = useState<ArtifactExportOptions['format']>('pdf');
  const [pages, setPages] = useState('all');
  const [range, setRange] = useState('');
  const [pptxMode, setPptxMode] = useState<'screenshot' | 'structured'>(
    'screenshot',
  );
  const [result, setResult] = useState<ArtifactExportResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const operation = useRef<symbol | null>(null);
  const scope = useRef(props);
  useEffect(() => {
    scope.current = props;
  }, [props]);
  useEffect(() => {
    setResult(null);
    setError('');
    setNotice('');
  }, [props.resourceId]);

  async function run(download = false) {
    if (operation.current || !props.visible) return;
    const identity = Symbol('export');
    operation.current = identity;
    setBusy(true);
    setError('');
    setNotice('');
    const resourceId = props.resourceId;
    const revision = props.resourceRevision;
    const selected = result;
    try {
      if (download) {
        if (!selected || selected.resource_id !== resourceId) return;
        await props.download(selected.export_id);
        if (scope.current.resourceId === resourceId)
          setNotice('Download requested.');
      } else {
        const options: ArtifactExportOptions = {
          format,
          pages:
            pages === 'current'
              ? String(props.currentPageIndex + 1)
              : pages === 'range'
                ? range
                : 'all',
          ...(format === 'pptx' ? { pptx_mode: pptxMode } : {}),
        };
        setResult(null);
        const next = await props.create(options, revision);
        if (scope.current.resourceId !== resourceId) return;
        if (
          next.status !== 'ready' ||
          next.resource_id !== resourceId ||
          next.resource_revision !== revision
        )
          throw { code: 'export_incomplete' };
        setResult(next);
      }
    } catch (reason) {
      if (scope.current.resourceId !== resourceId) return;
      const issue = failure(reason);
      setError(issue.text);
      if (issue.denied) setResult(null);
    } finally {
      if (operation.current === identity) {
        operation.current = null;
        setBusy(false);
      }
    }
  }

  if (!props.visible) return null;
  const current = result?.resource_id === props.resourceId ? result : null;
  return (
    <section
      className="studio-section stack"
      aria-label="Design export"
      aria-busy={busy}
    >
      <p>Export the saved design. Downloads stay local.</p>
      <Field label="Export format">
        <Select
          aria-label="Export format"
          value={format}
          disabled={busy}
          onChange={(event) =>
            setFormat(event.target.value as ArtifactExportOptions['format'])
          }
        >
          <option value="pdf">PDF</option>
          <option value="html">HTML</option>
          <option value="png">PNG</option>
          <option value="pptx">PPTX</option>
        </Select>
      </Field>
      {format === 'pptx' && (
        <Field label="PPTX mode">
          <Select
            aria-label="PPTX mode"
            value={pptxMode}
            disabled={busy}
            onChange={(event) =>
              setPptxMode(event.target.value as 'screenshot' | 'structured')
            }
          >
            <option value="screenshot">High fidelity</option>
            <option value="structured">Editable</option>
          </Select>
        </Field>
      )}
      <Field label="Export pages">
        <Select
          aria-label="Export pages"
          value={pages}
          disabled={busy}
          onChange={(event) => setPages(event.target.value)}
        >
          <option value="all">All pages</option>
          <option value="current">Current page</option>
          <option value="range">Page range</option>
        </Select>
      </Field>
      {pages === 'range' && (
        <Field label="Page range">
          <Input
            aria-label="Page range"
            placeholder="1-3 or 1,3,5"
            value={range}
            maxLength={256}
            disabled={busy}
            onChange={(event) => setRange(event.target.value)}
          />
        </Field>
      )}
      <p>
        {props.pageCount} {props.pageCount === 1 ? 'page' : 'pages'} ·
        Multi-page PNG exports download as a ZIP.
      </p>
      {format === 'pptx' && (
        <p>
          {pptxMode === 'structured'
            ? 'Editable text and shapes may differ from the preview.'
            : 'Slides contain rendered images.'}
        </p>
      )}
      <Button
        disabled={
          busy || props.pageCount < 1 || (pages === 'range' && !range.trim())
        }
        onClick={() => void run()}
      >
        {busy ? 'Working…' : 'Export design'}
      </Button>
      {error && <ErrorState title="Export unavailable">{error}</ErrorState>}
      {current && (
        <div>
          <p>
            {current.filename} · {Math.ceil(current.size_bytes / 1024)} KB ·{' '}
            {current.page_count} pages
          </p>
          {current.resource_revision !== props.resourceRevision && (
            <p>This export contains an earlier saved version.</p>
          )}
          {current.warnings.includes('external_assets_unavailable') && (
            <p role="status">
              External assets were unavailable offline. Review the download
              before sharing it.
            </p>
          )}
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => void run(true)}
          >
            Download {current.format.toUpperCase()}
          </Button>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
