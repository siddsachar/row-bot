import { useState, type FormEvent } from 'react';
import { Download, Eye, RefreshCw, Search } from 'lucide-react';
import type {
  SkillHubInstallCommand,
  SkillHubInstallReceipt,
  SkillHubPreview,
  SkillHubPreviewRequest,
  SkillHubSearchRequest,
  SkillHubSearchResult,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, ErrorState, Toggle } from '../../ui/primitives';

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
  const [preview, setPreview] = useState<SkillHubPreview | null>(null);
  const [makeAvailable, setMakeAvailable] = useState(false);
  const [pending, setPending] = useState(() => readPending(ownerKey));
  const [receiptMissing, setReceiptMissing] = useState(false);
  const [receipt, setReceipt] = useState<SkillHubInstallReceipt | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function search(event?: FormEvent, refresh = false) {
    event?.preventDefault();
    if (busy) return;
    setBusy('search');
    setError('');
    setPreview(null);
    try {
      setResults(await io.search({ query, source, refresh }));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  async function inspect(entryId: string) {
    if (busy || !results) return;
    setBusy('preview');
    setError('');
    setPreview(null);
    try {
      setPreview(
        await io.preview({ revision: results.revision, entry_id: entryId }),
      );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  function accept(result: SkillHubInstallReceipt) {
    setReceipt(result);
    setPending('');
    setReceiptMissing(false);
    retainPending(ownerKey, '');
    if (result.success) onInstalled?.();
  }

  async function install() {
    if (busy || pending || !preview || preview.scan.blocked) return;
    const id = crypto.randomUUID();
    const command: SkillHubInstallCommand = {
      command_id: id,
      preview_id: preview.preview_id,
      content_hash: preview.content_hash,
      make_available: makeAvailable,
    };
    setPending(id);
    retainPending(ownerKey, id);
    setBusy('install');
    setError('');
    try {
      accept(await io.install(command));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  async function recover() {
    if (busy || !pending) return;
    setBusy('receipt');
    setError('');
    try {
      accept(await io.receipt(pending));
    } catch (cause) {
      const failure = clientError(cause);
      setError(failure.message);
      setReceiptMissing(failure.code === 'skill_receipt_missing');
    } finally {
      setBusy('');
    }
  }

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
      <form className="button-row" onSubmit={(event) => void search(event)}>
        <label htmlFor="public-skill-query">Search or source URL</label>
        <input
          id="public-skill-query"
          className="input"
          value={query}
          maxLength={2000}
          onChange={(event) => setQuery(event.target.value)}
        />
        <label htmlFor="public-skill-source">Source</label>
        <select
          id="public-skill-source"
          className="input select"
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
        </select>
        <Button type="submit" disabled={Boolean(busy)}>
          <Search size={18} aria-hidden="true" /> Search
        </Button>
        {results && (
          <Button
            iconOnly
            aria-label="Refresh public skill search"
            title="Refresh public skill search"
            disabled={Boolean(busy)}
            onClick={() => void search(undefined, true)}
          >
            <RefreshCw size={18} aria-hidden="true" />
          </Button>
        )}
      </form>
      {error && (
        <ErrorState title="Public skills need attention">{error}</ErrorState>
      )}
      {busy === 'search' && <p role="status">Searching public sources…</p>}
      {results && (
        <>
          <p role="status">
            {results.entries.length} skills · {results.mode}
          </p>
          {results.error && <p role="alert">{results.error}</p>}
          {results.source_statuses.map((row) => (
            <p className="muted" key={row.source_id}>
              {row.source_id}: {row.status}
              {row.message ? ` · ${row.message}` : ''}
            </p>
          ))}
          {results.entries.length === 0 && <p>No public skills found.</p>}
          <ul className="stack">
            {results.entries.map((entry) => (
              <li className="card button-row" key={entry.id}>
                <span>
                  <strong>{entry.name}</strong> · {entry.source} ·{' '}
                  {entry.trust_level}
                  {entry.installed ? ' · installed' : ''}
                  <br />
                  {entry.description}
                </span>
                <Button
                  iconOnly
                  aria-label={`Inspect ${entry.name}`}
                  title={`Inspect ${entry.name}`}
                  disabled={Boolean(busy)}
                  onClick={() => void inspect(entry.id)}
                >
                  <Eye size={18} aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        </>
      )}
      {busy === 'preview' && <p role="status">Inspecting skill files…</p>}
      {preview && (
        <div className="card stack">
          <h4>{preview.entry.name}</h4>
          <p>
            {preview.scan.blocked
              ? 'Scanner blocked installation'
              : 'Scanner did not block installation'}{' '}
            · about {preview.scan.token_estimate} tokens
          </p>
          {preview.scan.findings.map((finding, index) => (
            <p key={`${finding.code}:${index}`}>
              {finding.severity}: {finding.message}
              {finding.path ? ` · ${finding.path}` : ''}
            </p>
          ))}
          <h5>SKILL.md preview</h5>
          <pre className="settings-break-word">{preview.primary_text}</pre>
          <h5>Files</h5>
          <ul>
            {preview.files.map((file) => (
              <li key={file}>{file}</li>
            ))}
          </ul>
          <label className="button-row">
            <span>Make available after install</span>
            <Toggle
              label="Make available after install"
              checked={makeAvailable}
              disabled={Boolean(busy) || Boolean(pending)}
              onChange={(event) => setMakeAvailable(event.target.checked)}
            />
          </label>
          <Button
            disabled={Boolean(busy) || Boolean(pending) || preview.scan.blocked}
            onClick={() => void install()}
          >
            <Download size={18} aria-hidden="true" /> Install skill
          </Button>
        </div>
      )}
      {pending && (
        <div className="card stack">
          <p>
            An installation may still be running. Check the original result
            before trying again.
          </p>
          <Button disabled={Boolean(busy)} onClick={() => void recover()}>
            Check original result
          </Button>
          {receiptMissing && (
            <Button
              variant="ghost"
              disabled={Boolean(busy)}
              onClick={() => {
                retainPending(ownerKey, '');
                setPending('');
                setReceiptMissing(false);
              }}
            >
              Clear lost record after inspecting Skill Library
            </Button>
          )}
        </div>
      )}
      {receipt && <p role="status">{receipt.message}</p>}
    </section>
  );
}
