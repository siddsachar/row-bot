import { useEffect, useRef, useState, type FormEvent } from 'react';
import { CheckCircle2, Download, Eye, RefreshCw, Search } from 'lucide-react';
import type {
  SkillHubEntryView,
  SkillHubInstallCommand,
  SkillHubInstallReceipt,
  SkillHubPreview,
  SkillHubPreviewRequest,
  SkillHubSearchRequest,
  SkillHubSearchResult,
} from '../../api/types';
import { aborted, clientError } from '../../api/errors';
import { ModalTask } from '../../ui/overlays';
import {
  Button,
  CompactAction,
  Disclosure,
  ErrorState,
  Input,
  Select,
  Skeleton,
  StatusDot,
  Toggle,
} from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';

const hubSourceLabels: Record<string, string> = {
  github: 'GitHub',
  skills_sh: 'skills.sh',
  browse_sh: 'browse.sh',
  clawhub: 'ClawHub',
  lobehub: 'LobeHub',
};
const PAGE = 24;
const MAX_RESULTS = 96;
// While a source is still answering, ask again; the server holds each request
// a few seconds for it, so this bounds the wait to well under a minute.
const FOLLOW_UPS = 6;

export type PublicSkillHubIO = {
  search: (
    request: SkillHubSearchRequest,
    signal?: AbortSignal,
  ) => Promise<SkillHubSearchResult>;
  preview: (
    request: SkillHubPreviewRequest,
    signal?: AbortSignal,
  ) => Promise<SkillHubPreview>;
  install: (
    command: SkillHubInstallCommand,
    signal?: AbortSignal,
  ) => Promise<SkillHubInstallReceipt>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<SkillHubInstallReceipt>;
};

function pendingKey(ownerKey: string): string {
  return `row-bot-skill-hub-install:${ownerKey}`;
}
function readPending(ownerKey: string): string {
  try {
    return sessionStorage.getItem(pendingKey(ownerKey)) ?? '';
  } catch {
    return '';
  }
}
function retainPending(ownerKey: string, id: string): void {
  try {
    if (id) sessionStorage.setItem(pendingKey(ownerKey), id);
    else sessionStorage.removeItem(pendingKey(ownerKey));
  } catch {
    /* storage can be unavailable */
  }
}
function sourceLabel(id: string): string {
  return hubSourceLabels[id] ?? id;
}
const names = new Intl.ListFormat('en', { type: 'conjunction' });

export default function PublicSkillHub({
  io,
  ownerKey,
  onInstalled,
}: {
  io: PublicSkillHubIO;
  ownerKey: string;
  onInstalled?: () => void;
}) {
  const [query, setQuery] = useState('');
  const [source, setSource] = useState<SkillHubSearchRequest['source']>('all');
  const [results, setResults] = useState<SkillHubSearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [selected, setSelected] = useState<SkillHubEntryView | null>(null);
  const [preview, setPreview] = useState<SkillHubPreview | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [available, setAvailable] = useState(true);
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState('');
  const [receipt, setReceipt] = useState<SkillHubInstallReceipt | null>(null);
  const [pending, setPending] = useState(() => readPending(ownerKey));
  const [recovering, setRecovering] = useState(false);
  const [recoverError, setRecoverError] = useState('');
  const [receiptMissing, setReceiptMissing] = useState(false);
  const lastSearch = useRef<SkillHubSearchRequest | null>(null);
  const searchAbort = useRef<AbortController | null>(null);
  const previewAbort = useRef<AbortController | null>(null);
  const receiptAbort = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      searchAbort.current?.abort();
      previewAbort.current?.abort();
      receiptAbort.current?.abort();
    },
    [],
  );

  async function runSearch(request: SkillHubSearchRequest, followUp = 0) {
    searchAbort.current?.abort();
    const abort = new AbortController();
    searchAbort.current = abort;
    lastSearch.current = request;
    setSearching(true);
    if (!followUp) setSearchError('');
    try {
      const next = await io.search(request, abort.signal);
      if (abort.signal.aborted) return;
      setResults(next);
      // Sources still answering: show what came back and ask again.
      if (
        followUp < FOLLOW_UPS &&
        next.source_statuses.some((row) => row.status === 'pending')
      ) {
        void runSearch({ ...request, refresh: false }, followUp + 1);
        return;
      }
    } catch (cause) {
      if (abort.signal.aborted || aborted(cause)) return;
      setSearchError(clientError(cause).message);
    }
    setSearching(false);
  }

  function submit(event?: FormEvent, refresh = false) {
    event?.preventDefault();
    void runSearch({ query, source, refresh, limit: PAGE });
  }

  async function view(entry: SkillHubEntryView, revision: string) {
    previewAbort.current?.abort();
    const abort = new AbortController();
    previewAbort.current = abort;
    setSelected(entry);
    setPreview(null);
    setPreviewError('');
    setReceipt(null);
    setInstallError('');
    setAvailable(true);
    try {
      const next = await io.preview(
        { revision, entry_id: entry.id },
        abort.signal,
      );
      if (!abort.signal.aborted) setPreview(next);
    } catch (cause) {
      if (abort.signal.aborted || aborted(cause)) return;
      setPreviewError(clientError(cause).message);
    }
  }

  function closeView() {
    previewAbort.current?.abort();
    setSelected(null);
    setPreview(null);
    setReceipt(null);
  }

  function accept(result: SkillHubInstallReceipt) {
    setReceipt(result);
    setPending('');
    setReceiptMissing(false);
    retainPending(ownerKey, '');
    if (!result.success) return;
    onInstalled?.();
    if (lastSearch.current)
      void runSearch({ ...lastSearch.current, refresh: false });
  }

  const installed = Boolean(preview?.entry.installed || receipt?.success);
  async function install() {
    if (!preview || installing || pending || preview.scan.blocked || installed)
      return;
    const id = crypto.randomUUID();
    const command: SkillHubInstallCommand = {
      command_id: id,
      preview_id: preview.preview_id,
      content_hash: preview.content_hash,
      make_available: available,
    };
    setPending(id);
    retainPending(ownerKey, id);
    setInstalling(true);
    setInstallError('');
    // No abort signal: leaving would not stop the install, only lose its
    // result, which the retained command id recovers.
    try {
      accept(await io.install(command));
    } catch (cause) {
      setInstallError(clientError(cause).message);
    } finally {
      setInstalling(false);
    }
  }

  async function recover() {
    if (recovering || !pending) return;
    receiptAbort.current?.abort();
    const abort = new AbortController();
    receiptAbort.current = abort;
    setRecovering(true);
    setRecoverError('');
    try {
      accept(await io.receipt(pending, abort.signal));
    } catch (cause) {
      if (abort.signal.aborted || aborted(cause)) return;
      const failure = clientError(cause);
      setRecoverError(failure.message);
      setReceiptMissing(failure.code === 'skill_receipt_missing');
    } finally {
      setRecovering(false);
    }
  }

  const waiting =
    results?.source_statuses
      .filter((row) => row.status === 'pending')
      .map((row) => sourceLabel(row.source_id)) ?? [];
  // Sources that failed, answered in part, or only had earlier results.
  const problems =
    results?.source_statuses.filter(
      (row) =>
        row.status === 'error' ||
        row.status === 'stale' ||
        (row.status === 'partial' && row.message),
    ) ?? [];
  const recovery = pending && !installing && (
    <div className="card stack">
      <p>
        An installation may still be running. Check the original result before
        trying again.
      </p>
      {recoverError && (
        <ErrorState title="Result not found">{recoverError}</ErrorState>
      )}
      <Button disabled={recovering} onClick={() => void recover()}>
        Check original result
      </Button>
      {receiptMissing && (
        <Button
          variant="ghost"
          disabled={recovering}
          onClick={() => {
            retainPending(ownerKey, '');
            setPending('');
            setReceiptMissing(false);
            setRecoverError('');
          }}
        >
          Clear lost record after inspecting Skill Library
        </Button>
      )}
    </div>
  );
  const outcome = receipt && (
    <p
      role="status"
      className="skill-hub-result"
      data-success={receipt.success ? 'true' : undefined}
    >
      {receipt.success && <CheckCircle2 size={16} aria-hidden />}
      <span>{receipt.message}</span>
    </p>
  );

  return (
    <section
      className="settings-snapshot-section stack"
      id="public-skill-hub"
      aria-labelledby="public-skill-hub-title"
    >
      <header className="settings-snapshot-heading">
        <div>
          <h3 id="public-skill-hub-title">Browse public skills</h3>
          <p>
            Search public sources, inspect the exact files and scanner findings,
            then install locally.
          </p>
        </div>
      </header>
      <form
        className="settings-list-toolbar"
        role="search"
        aria-label="Search public skills"
        onSubmit={submit}
      >
        <label className="settings-inline-search">
          <span className="visually-hidden">Search or source URL</span>
          <Search size={14} aria-hidden />
          <Input
            type="search"
            maxLength={2000}
            placeholder="Search, or paste a GitHub or skills.sh link"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <Select
          aria-label="Source"
          value={source}
          onChange={(event) =>
            setSource(event.target.value as SkillHubSearchRequest['source'])
          }
        >
          <option value="all">All sources</option>
          <option value="github">GitHub</option>
          <option value="skills_sh">skills.sh</option>
          <option value="browse_sh">browse.sh</option>
          <option value="clawhub">ClawHub</option>
          <option value="lobehub">LobeHub</option>
        </Select>
        <Button type="submit" variant="primary">
          Search
        </Button>
        {results && (
          <CompactAction
            label="Refresh public skill search"
            onClick={() => submit(undefined, true)}
          >
            <RefreshCw size={16} aria-hidden />
          </CompactAction>
        )}
      </form>
      {searchError && (
        <ErrorState title="The search didn't finish">{searchError}</ErrorState>
      )}
      {searching && !results && <Skeleton label="Searching public sources" />}
      {searching && results && !waiting.length && (
        <p className="skill-hub-waiting" role="status">
          <StatusDot tone="info" pulse label="Searching" />
          <span>Searching public sources…</span>
        </p>
      )}
      {results && (
        <>
          <p role="status" className="settings-list-count">
            {results.entries.length}{' '}
            {results.entries.length === 1 ? 'skill' : 'skills'}
            {results.has_more ? ' shown' : ''}
          </p>
          {waiting.length > 0 &&
            (searching ? (
              <p className="skill-hub-waiting" role="status">
                <StatusDot tone="info" pulse label="Searching" />
                <span>Still searching {names.format(waiting)}…</span>
              </p>
            ) : (
              <p className="muted">
                {names.format(waiting)} didn't answer in time. Refresh to try
                again.
              </p>
            ))}
          {problems.map((row) => (
            <p className="muted" key={row.source_id}>
              {row.message ||
                `${sourceLabel(row.source_id)} couldn't be searched.`}
              {row.status === 'stale' ? ' Showing earlier results.' : ''}
            </p>
          ))}
          {results.entries.length === 0 && !waiting.length && (
            <p>No public skills found.</p>
          )}
          <ul className="settings-row-list skill-hub-results">
            {results.entries.map((entry) => (
              <li key={entry.id}>
                <span className="settings-row-list-text">
                  <strong>{entry.name}</strong>
                  <small>
                    {sourceLabel(entry.source)} ·{' '}
                    {humanizeToken(entry.trust_level).toLowerCase()}
                    {entry.installed && (
                      <>
                        {' '}
                        <span className="settings-skill-badge">Installed</span>
                      </>
                    )}
                  </small>
                  {entry.description && (
                    <small className="skill-hub-description">
                      {entry.description}
                    </small>
                  )}
                </span>
                <CompactAction
                  label={`View ${entry.name}`}
                  onClick={() => void view(entry, results.revision)}
                >
                  <Eye size={16} aria-hidden />
                </CompactAction>
              </li>
            ))}
          </ul>
          {results.has_more && results.entries.length < MAX_RESULTS && (
            <Button
              disabled={searching}
              onClick={() =>
                lastSearch.current &&
                void runSearch({
                  ...lastSearch.current,
                  refresh: false,
                  limit: Math.min(
                    MAX_RESULTS,
                    (lastSearch.current.limit ?? PAGE) + PAGE,
                  ),
                })
              }
            >
              Load more skills
            </Button>
          )}
        </>
      )}
      {!selected && recovery}
      {!selected && outcome}
      <ModalTask
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) closeView();
        }}
        title={selected?.name ?? 'Public skill'}
        description={
          selected
            ? `${sourceLabel(selected.source)} · ${humanizeToken(selected.trust_level).toLowerCase()}`
            : ''
        }
        dismissible={!installing}
        className="skill-hub-dialog"
      >
        {previewError ? (
          <ErrorState
            title="This skill didn't open"
            action={
              selected &&
              results && (
                <Button onClick={() => void view(selected, results.revision)}>
                  Try again
                </Button>
              )
            }
          >
            {previewError}
          </ErrorState>
        ) : !preview ? (
          <Skeleton label="Reading the skill's files" />
        ) : (
          <div className="stack skill-hub-preview">
            <p className="skill-hub-scan">
              <StatusDot
                tone={
                  preview.scan.blocked
                    ? 'danger'
                    : preview.scan.findings.length
                      ? 'warning'
                      : 'success'
                }
                label={preview.scan.blocked ? 'Blocked' : 'Checked'}
              />
              <span>
                {preview.scan.blocked
                  ? 'The safety scan blocked this skill.'
                  : preview.scan.findings.length
                    ? 'The safety scan found things to look at.'
                    : 'The safety scan found nothing to worry about.'}{' '}
                About {preview.scan.token_estimate} tokens.
              </span>
            </p>
            {preview.scan.findings.length > 0 && (
              <ul className="skill-hub-findings">
                {preview.scan.findings.map((finding, index) => (
                  <li key={`${finding.code}:${index}`}>
                    <strong>{humanizeToken(finding.severity)}:</strong>{' '}
                    {finding.message}
                    {finding.path ? ` · ${finding.path}` : ''}
                  </li>
                ))}
              </ul>
            )}
            <pre
              className="skill-hub-skill-md"
              tabIndex={0}
              aria-label="SKILL.md"
            >
              {preview.primary_text}
            </pre>
            <Disclosure summary="Files" meta={preview.files.length}>
              <ul className="skill-hub-files">
                {preview.files.map((file) => (
                  <li key={file}>{file}</li>
                ))}
              </ul>
            </Disclosure>
            <div className="skill-hub-install">
              <label className="skill-hub-available">
                <Toggle
                  label="Available in chats"
                  checked={available}
                  disabled={
                    installing ||
                    installed ||
                    preview.scan.blocked ||
                    Boolean(pending)
                  }
                  onChange={(event) => setAvailable(event.target.checked)}
                />
                <span>
                  <strong>Available in chats</strong>
                  <small>
                    {available
                      ? 'Use it in your chats as soon as it is installed.'
                      : 'It installs switched off; turn it on under Installed.'}
                  </small>
                </span>
              </label>
              <Button
                variant="primary"
                aria-busy={installing || undefined}
                disabled={
                  installing ||
                  installed ||
                  preview.scan.blocked ||
                  Boolean(pending)
                }
                onClick={() => void install()}
              >
                <Download size={16} aria-hidden /> Install skill
              </Button>
            </div>
            {installed && !receipt && (
              <p className="muted">
                Already installed as {preview.skill_name}.
              </p>
            )}
            {installError && (
              <ErrorState title="Not installed">{installError}</ErrorState>
            )}
          </div>
        )}
        {outcome}
        {recovery}
      </ModalTask>
    </section>
  );
}
