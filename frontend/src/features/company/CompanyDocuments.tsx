import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  FileText,
  FileUp,
  RefreshCw,
  Trash2,
  Unplug,
  Upload,
} from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type { CompanyDocument, CompanyDocumentsView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useOverlay } from '../../ui/overlays';
import { EntityList, EntityRow, IconButton, Select } from '../../ui/primitives';
import { SettingsGroup } from '../settings/anatomy';
import {
  disconnectedWords,
  documentStatus,
  documentWorking,
  lockdownWords,
  outcomeWords,
  sizeWords,
  typesWords,
  unreachableWords,
} from './words';
import './company.css';

export type CompanyDocumentsController = Pick<
  ClientController,
  | 'companyDocuments'
  | 'uploadCompanyDocument'
  | 'replaceCompanyDocument'
  | 'removeCompanyDocument'
>;

/** How often the list reads itself again while the server works on a document. */
export const COMPANY_DOCUMENTS_POLL_MS = 5000;

const DONE = new Set(['uploaded', 'replaced', 'removed']);

function extension(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot).toLowerCase() : '';
}

/**
 * Settings › Documents: the company documents of the spaces the person owns
 * (Row-Bot for Teams, B3). Add, replace and delete go to the company's
 * server with this Row-Bot's sign-in; each document shows its status and
 * why it waits or failed. Nothing shows unless the person owns a space: with
 * no company joined, or none of its spaces theirs, the page is as it was.
 * The list reads itself again only while the server works on a document and
 * the page is in view.
 */
export default function CompanyDocuments({
  controller,
}: {
  controller: CompanyDocumentsController;
}) {
  const [view, setView] = useState<CompanyDocumentsView | null>(null);
  const [space, setSpace] = useState<string | undefined>(undefined);
  const [reload, setReload] = useState(0);
  // Whether the person owned a space when the company last answered: only
  // then does a server that can't be asked now say so here.
  const [owner, setOwner] = useState(false);

  useEffect(() => {
    let current = true;
    const read = async () => {
      try {
        const next = await controller.companyDocuments(space);
        if (current) setView(next);
      } catch {
        // No company documents here (outside the app, or its read failed):
        // the group stays as it was.
      }
    };
    void read();
    return () => {
      current = false;
    };
  }, [controller, space, reload]);

  useEffect(() => {
    if (view?.state === 'ready') setOwner(view.spaces.length > 0);
  }, [view]);

  const working =
    view?.state === 'ready' && view.documents.some(documentWorking);
  // Each read (and each answer) starts the wait for the next one again.
  useEffect(() => {
    if (!working) return;
    let timer: number | undefined;
    const schedule = () => {
      window.clearTimeout(timer);
      if (document.visibilityState === 'visible')
        timer = window.setTimeout(
          () => setReload((value) => value + 1),
          COMPANY_DOCUMENTS_POLL_MS,
        );
    };
    schedule();
    document.addEventListener('visibilitychange', schedule);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', schedule);
    };
  }, [working, view, reload]);

  if (!view || view.state === 'not_joined') return null;
  if (view.state === 'ready' ? view.spaces.length === 0 : !owner) return null;
  return (
    <OwnedDocuments
      view={view}
      controller={controller}
      onView={setView}
      onSpace={setSpace}
      onRetry={() => setReload((value) => value + 1)}
    />
  );
}

function OwnedDocuments({
  view,
  controller,
  onView,
  onSpace,
  onRetry,
}: {
  view: CompanyDocumentsView;
  controller: CompanyDocumentsController;
  onView: (view: CompanyDocumentsView) => void;
  onSpace: (space: string) => void;
  onRetry: () => void;
}) {
  const overlay = useOverlay();
  const input = useRef<HTMLInputElement>(null);
  // The document a picked file replaces; null adds a new one.
  const target = useRef<CompanyDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; alert: boolean } | null>(
    null,
  );
  const company = view.company ?? 'Your company';
  const spaceId = view.space_id;
  const spaceName =
    view.spaces.find((item) => item.id === spaceId)?.name ?? 'this space';
  const ready = view.state === 'ready';

  function pick(entry: CompanyDocument | null) {
    target.current = entry;
    setNotice(null);
    input.current?.click();
  }

  async function act(name: string, send: () => Promise<CompanyDocumentsView>) {
    setBusy(true);
    try {
      const next = await send();
      onView(next);
      setNotice(
        next.state === 'ready' && next.outcome
          ? {
              text: outcomeWords(next.outcome, name, next),
              alert: !DONE.has(next.outcome),
            }
          : null,
      );
    } catch (cause) {
      setNotice({ text: clientError(cause).message, alert: true });
    } finally {
      setBusy(false);
    }
  }

  function chosen(file: File) {
    // The server's limits, checked before anything is sent.
    const refusal =
      file.size > view.max_upload_bytes
        ? 'too_large'
        : !view.supported_types.includes(extension(file.name))
          ? 'unsupported_type'
          : null;
    if (refusal) {
      setNotice({ text: outcomeWords(refusal, file.name, view), alert: true });
      return;
    }
    const replacing = target.current;
    if (!replacing) {
      if (spaceId)
        void act(file.name, () =>
          controller.uploadCompanyDocument(spaceId, file, file.name),
        );
      return;
    }
    overlay.open({
      kind: 'alert',
      title: `Replace ${replacing.title}?`,
      description: `${file.name} takes its place. Everyone who reads ${spaceName} finds the new file’s text from then on.`,
      confirmLabel: 'Replace',
      cancelLabel: 'Keep it',
      onConfirm: () =>
        void act(file.name, () =>
          controller.replaceCompanyDocument(replacing.id, file, file.name),
        ),
    });
  }

  function confirmDelete(entry: CompanyDocument) {
    overlay.open({
      kind: 'alert',
      title: `Delete ${entry.title}?`,
      description: `Everyone who reads ${spaceName} loses it from ${company}’s brain. Facts taken from it stay, without their source.`,
      confirmLabel: 'Delete',
      cancelLabel: 'Keep it',
      onConfirm: () =>
        void act(entry.title, () => controller.removeCompanyDocument(entry.id)),
    });
  }

  const actions = ready && (
    <>
      {view.spaces.length > 1 && (
        <Select
          className="company-space-picker"
          aria-label="Space"
          value={spaceId ?? ''}
          disabled={busy}
          onChange={(event) => onSpace(event.currentTarget.value)}
        >
          {view.spaces.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </Select>
      )}
      <IconButton
        label={`Add a document to ${spaceName}`}
        variant="secondary"
        disabled={busy || !spaceId}
        onClick={() => pick(null)}
      >
        <Upload size={16} aria-hidden />
      </IconButton>
    </>
  );

  return (
    <SettingsGroup
      title="Company documents"
      anchor="company-documents"
      className="company-documents"
      note={
        ready
          ? `What you add to ${spaceName} goes to ${company}’s brain, for everyone who reads that space. Add ${typesWords(view.supported_types)} files, up to ${sizeWords(view.max_upload_bytes)} each.`
          : undefined
      }
      meta={actions || undefined}
    >
      <input
        ref={input}
        type="file"
        hidden
        tabIndex={-1}
        accept={view.supported_types.join(',')}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) chosen(file);
        }}
      />
      {notice && (
        <p
          role={notice.alert ? 'alert' : 'status'}
          className="settings-divided company-documents-notice"
        >
          {notice.text}
        </p>
      )}
      {view.state === 'disconnected' && (
        <div className="settings-divided company-documents-state">
          <Unplug size={16} aria-hidden />
          <p>{disconnectedWords(company)}</p>
          <Link className="button small" to="/settings/company">
            Sign in again
          </Link>
        </div>
      )}
      {(view.state === 'locked_down' || view.state === 'unreachable') && (
        <div className="settings-divided company-documents-state">
          <p>
            {view.state === 'locked_down'
              ? lockdownWords({ company, policy_reason: null })
              : unreachableWords(company, view.reason)}
          </p>
          <IconButton
            size="sm"
            label={view.state === 'locked_down' ? 'Retry' : 'Try again'}
            onClick={onRetry}
          >
            <RefreshCw size={15} aria-hidden />
          </IconButton>
        </div>
      )}
      {ready && view.documents.length === 0 && (
        <p className="settings-divided muted">
          {`No documents in ${spaceName} yet.`}
        </p>
      )}
      {ready && view.documents.length > 0 && (
        <EntityList label={`Documents in ${spaceName}`}>
          {view.documents.map((entry) => {
            const status = documentStatus(entry);
            return (
              <EntityRow
                key={entry.id}
                title={entry.title}
                icon={<FileText size={16} aria-hidden />}
                status={{ tone: status.tone, label: status.label }}
                meta={[
                  entry.file_name,
                  sizeWords(entry.byte_count),
                  status.detail,
                ]
                  .filter(Boolean)
                  .join(' · ')}
                action={
                  <>
                    <IconButton
                      size="sm"
                      label={`Replace ${entry.title}`}
                      disabled={busy}
                      onClick={() => pick(entry)}
                    >
                      <FileUp size={15} aria-hidden />
                    </IconButton>
                    <IconButton
                      size="sm"
                      label={`Delete ${entry.title}`}
                      disabled={busy}
                      onClick={() => confirmDelete(entry)}
                    >
                      <Trash2 size={15} aria-hidden />
                    </IconButton>
                  </>
                }
              />
            );
          })}
        </EntityList>
      )}
    </SettingsGroup>
  );
}
