import { useEffect, useRef, useState } from 'react';
import {
  Button,
  Disclosure,
  ErrorState,
  Field,
  Input,
  Select,
} from '../../ui/primitives';

import type { ArtifactExport } from '../../api/types';
import type { CapabilityResult, SavedFile } from '../../platform/types';
import { saveProblem } from '../shell/save-file';
import { AppLink } from '../../ui/app-link';

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
  /** The panel is still catching up with the saved version (after an edit). */
  updating?: boolean;
  create: (
    options: ArtifactExportOptions,
    expectedRevision: string,
  ) => Promise<ArtifactExportResult>;
  /**
   * Save the export the one way every download saves (5.3.3): the desktop
   * app's Save dialog asks where (then Show in folder), a browser downloads it.
   */
  download: (exportId: string) => Promise<CapabilityResult<SavedFile>>;
};

/** The formats are the presets (parity row 27): one click exports. */
const FORMATS: {
  format: ArtifactExportOptions['format'];
  label: string;
}[] = [
  { format: 'pdf', label: 'PDF' },
  { format: 'png', label: 'PNG' },
  { format: 'pptx', label: 'PowerPoint' },
  { format: 'html', label: 'HTML' },
];

// Why a save didn't happen, as opposed to the export being gone or denied.
const SAVE_REASONS = new Set([
  'user_gesture_required',
  'save_failed',
  'operation_failed',
  '',
]);

function codeOf(reason: unknown) {
  return typeof reason === 'object' && reason !== null && 'code' in reason
    ? String(reason.code)
    : '';
}

function failure(reason: unknown) {
  const code = codeOf(reason);
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
            : code === 'export_storage_unavailable'
              ? "The Exports folder can't be used. Check the workspace folder in Settings › System, then export again."
              : code === 'export_runtime_missing'
                ? 'Exports need Browser Automation. Install it in Settings › System, then export again.'
                : 'The export could not be completed. Any partial local copy is retained; no complete download is available for this attempt.';
  return { denied, text, needsBrowser: code === 'export_runtime_missing' };
}

export default function ArtifactExports(props: ArtifactExportsProps) {
  const [pages, setPages] = useState('all');
  const [range, setRange] = useState('');
  const [pptxMode, setPptxMode] = useState<'screenshot' | 'structured'>(
    'screenshot',
  );
  const [result, setResult] = useState<ArtifactExportResult | null>(null);
  // The export saved last: the panel keeps only its line (F17).
  const [saved, setSaved] = useState('');
  const [working, setWorking] = useState<ArtifactExportOptions['format']>();
  const [error, setError] = useState('');
  const [needsBrowser, setNeedsBrowser] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const operation = useRef<symbol | null>(null);
  const scope = useRef(props);
  useEffect(() => {
    scope.current = props;
  }, [props]);
  useEffect(() => {
    setResult(null);
    setSaved('');
    setError('');
    setNotice('');
  }, [props.resourceId]);

  const current = result?.resource_id === props.resourceId ? result : null;

  async function exclusive(task: (resourceId: string) => Promise<void>) {
    if (operation.current || !props.visible) return;
    const identity = Symbol('export');
    operation.current = identity;
    setBusy(true);
    setError('');
    setNotice('');
    const resourceId = props.resourceId;
    try {
      await task(resourceId);
    } catch (reason) {
      if (scope.current.resourceId !== resourceId) return;
      const issue = failure(reason);
      setError(issue.text);
      setNeedsBrowser(issue.needsBrowser);
      if (issue.denied) {
        setResult(null);
        setSaved('');
      }
    } finally {
      if (operation.current === identity) {
        operation.current = null;
        setBusy(false);
        setWorking(undefined);
      }
    }
  }

  function exportAs(format: ArtifactExportOptions['format']) {
    void exclusive(async (resourceId) => {
      const revision = props.resourceRevision;
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
      setWorking(format);
      setResult(null);
      setSaved('');
      const next = await props.create(options, revision);
      if (scope.current.resourceId !== resourceId) return;
      if (
        next.status !== 'ready' ||
        next.resource_id !== resourceId ||
        next.resource_revision !== revision
      )
        throw { code: 'export_incomplete' };
      setResult(next);
      // One click exports, then asks where to save it. A browser that needs a
      // fresh click by then offers Download instead.
      await save(next.export_id, resourceId, true);
    });
  }

  async function save(exportId: string, resourceId: string, quiet = false) {
    const outcome = await props.download(exportId);
    if (scope.current.resourceId !== resourceId) return;
    if (outcome.status === 'ok') setSaved(exportId);
    else if (
      outcome.status === 'unavailable' &&
      !SAVE_REASONS.has(outcome.reason ?? '')
    )
      // The export itself is gone or no longer allowed: as an export failure.
      throw { code: outcome.reason };
    else if (!(
      quiet &&
      outcome.status === 'unavailable' &&
      outcome.reason === 'user_gesture_required'
    ))
      setNotice(saveProblem(outcome, 'the export'));
  }

  function download() {
    void exclusive(async (resourceId) => {
      if (!current || current.resource_id !== resourceId) return;
      await save(current.export_id, resourceId);
    });
  }

  if (!props.visible) return null;
  const copy = current && saved === current.export_id ? current : null;
  const label = (format: string) =>
    FORMATS.find((item) => item.format === format)?.label ??
    format.toUpperCase();
  return (
    <section
      className="studio-section stack"
      aria-label="Design export"
      aria-busy={busy}
    >
      {!copy && <p>Pick a format to export the saved design.</p>}
      {!copy && (
        <div className="export-formats" role="group" aria-label="Export format">
          {FORMATS.map((item) => (
            <Button
              key={item.format}
              aria-label={`Export as ${item.label}`}
              disabled={
                busy ||
                props.updating ||
                props.pageCount < 1 ||
                (pages === 'range' && !range.trim())
              }
              onClick={() => exportAs(item.format)}
            >
              {working === item.format ? 'Exporting…' : item.label}
            </Button>
          ))}
        </div>
      )}
      {!copy && (
        <Disclosure summary="Options">
          <div className="stack">
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
            <Field label="PowerPoint slides">
              <Select
                aria-label="PPTX mode"
                value={pptxMode}
                disabled={busy}
                onChange={(event) =>
                  setPptxMode(event.target.value as 'screenshot' | 'structured')
                }
              >
                <option value="screenshot">High fidelity (pictures)</option>
                <option value="structured">Editable text and shapes</option>
              </Select>
            </Field>
            <p className="muted">
              {props.pageCount} {props.pageCount === 1 ? 'page' : 'pages'} · PNG
              of several pages comes as a ZIP.
            </p>
          </div>
        </Disclosure>
      )}
      {props.updating && !busy && (
        <p className="muted">Waiting for the saved version…</p>
      )}
      {error && (
        <ErrorState
          title="Export unavailable"
          action={
            needsBrowser ? (
              <AppLink to="/settings/system#browser.install">
                Set up Browser Automation
              </AppLink>
            ) : undefined
          }
        >
          {error}
        </ErrorState>
      )}
      {current && (
        <div className="export-result" role="status">
          {copy ? (
            <p>
              <strong>Saved</strong> · {copy.filename}
            </p>
          ) : (
            <p>
              {current.filename} · {Math.ceil(current.size_bytes / 1024)} KB ·{' '}
              {current.page_count} {current.page_count === 1 ? 'page' : 'pages'}
            </p>
          )}
          {current.resource_revision !== props.resourceRevision && (
            <p>This export contains an earlier saved version.</p>
          )}
          {current.warnings.includes('external_assets_unavailable') && (
            <p>
              Some pictures from the web couldn't be included offline. Check the
              file before sharing it.
            </p>
          )}
          <div className="action-cluster">
            {copy ? (
              <>
                {/* Once the file is saved the panel keeps only this line (F17). */}
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    setResult(null);
                    setSaved('');
                    setNotice('');
                  }}
                >
                  Export again
                </Button>
              </>
            ) : (
              <Button variant="primary" disabled={busy} onClick={download}>
                Download {label(current.format)}
              </Button>
            )}
          </div>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
