import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  CircleCheck,
  ListChecks,
  ListX,
  Square,
  Undo2,
} from 'lucide-react';
import type {
  MemoryImportBatch,
  MemoryImportCandidate,
  MemoryImportPreview,
  MemoryImportReceipt,
  MemoryImportSources,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  Disclosure,
  EmptyState,
  ErrorState,
  IconButton,
  InlineEmpty,
  Progress,
  Select,
  Skeleton,
  Stat,
  StatGroup,
  Toggle,
} from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import { PHONE_LAYOUT, useMedia } from '../../ui/use-media';
import {
  CandidateRow,
  FileDrop,
  PreviewRow,
  PromptCard,
  RecentImports,
  SourceGrid,
  VirtualList,
  sourceName,
  type Edit,
  type PickedFile,
} from './MemoryImportParts';
import {
  CATEGORIES,
  categoryWords,
  jobProblem,
  listWords,
  plural,
  shortDate,
  type MemoryImportIO,
} from './memory-import-io';
import './memory-import.css';

type Step = 'source' | 'working' | 'review' | 'preview' | 'done';
const POLL_MS = 600;

function problem(error: unknown) {
  return clientError(error).message;
}

function uuid() {
  return crypto.randomUUID();
}

/**
 * Knowledge › Import memories: bring what other assistants know about the
 * person into Row-Bot. Pick where from, add the export or paste the answer,
 * review what was found, see what each would do, then import (with Undo).
 * Nothing is saved before the last step.
 */
export default function MemoryImportDialog({
  open,
  onOpenChange,
  io,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: MemoryImportIO;
  /** Memories were imported or an import was undone: refresh what shows them. */
  onImported?: () => void;
}) {
  const phone = useMedia(PHONE_LAYOUT);
  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title="Import memories"
      description="Bring what other assistants know about you. Nothing is saved until you check it."
      kind={phone ? 'sheet' : 'dialog'}
      className="memory-import-dialog"
    >
      {open && (
        <ImportFlow
          io={io}
          phone={phone}
          onClose={() => onOpenChange(false)}
          onImported={onImported}
        />
      )}
    </ModalTask>
  );
}

function ImportFlow({
  io,
  phone,
  onClose,
  onImported,
}: {
  io: MemoryImportIO;
  phone: boolean;
  onClose: () => void;
  onImported?: () => void;
}) {
  const [sources, setSources] = useState<MemoryImportSources | null>(null);
  const [sourcesError, setSourcesError] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [pasted, setPasted] = useState('');
  const [step, setStep] = useState<Step>('source');
  const [batch, setBatch] = useState<MemoryImportBatch | null>(null);
  const [sending, setSending] = useState<{
    sent: number;
    total: number;
  } | null>(null);
  const [error, setError] = useState('');
  const [candidates, setCandidates] = useState<MemoryImportCandidate[]>([]);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(() => new Set());
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [category, setCategory] = useState('');
  const [origin, setOrigin] = useState('');
  const [withConversations, setWithConversations] = useState(false);
  const [preview, setPreview] = useState<MemoryImportPreview | null>(null);
  const [left, setLeft] = useState<ReadonlySet<string>>(() => new Set());
  const [receipt, setReceipt] = useState<MemoryImportReceipt | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const batchRef = useRef<string | null>(null);
  const applied = useRef(false);
  const uploads = useRef<AbortController | null>(null);
  const alive = useRef(true);

  const loadSources = useCallback(() => {
    setSourcesError('');
    const controller = new AbortController();
    io.sources(controller.signal).then(
      (value) => {
        if (!alive.current) return;
        setSources(value);
        setSourceId((current) => current || value.sources[0]?.id || '');
      },
      (reason: unknown) => {
        if (alive.current && !controller.signal.aborted)
          setSourcesError(problem(reason));
      },
    );
    return () => controller.abort();
  }, [io]);

  useEffect(() => {
    alive.current = true;
    const stop = loadSources();
    return () => {
      alive.current = false;
      stop();
      uploads.current?.abort();
      // Closed before importing: its files and everything found go.
      const id = batchRef.current;
      if (id && !applied.current) void io.discard(id).catch(() => undefined);
    };
  }, [io, loadSources]);

  const source = sources?.sources.find((item) => item.id === sourceId);

  /** Read the batch until its running step ends. */
  const follow = useCallback(
    async (id: string) => {
      for (;;) {
        if (!alive.current) return null;
        const view = await io.batch(id);
        if (!alive.current) return null;
        setBatch(view);
        if (view.job?.status !== 'running') return view;
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
    },
    [io],
  );

  const known = useRef<ReadonlySet<string>>(new Set());
  const loadCandidates = useCallback(
    async (id: string) => {
      const page = await io.candidates(id);
      if (!alive.current) return;
      // Newly found memories start chosen; the person's earlier choices stay.
      const seen = known.current;
      known.current = new Set(page.items.map((item) => item.candidate_id));
      setCandidates(page.items);
      setChosen((current) => {
        const next = new Set(current);
        for (const item of page.items)
          if (!seen.has(item.candidate_id)) next.add(item.candidate_id);
        return next;
      });
    },
    [io],
  );

  const findMemories = async () => {
    setError('');
    setStep('working');
    try {
      let id = batchRef.current;
      if (!id) {
        id = (await io.begin()).batch_id;
        batchRef.current = id;
      }
      const controller = new AbortController();
      uploads.current = controller;
      const waiting = files.filter((item) => item.state !== 'sent');
      const total = waiting.reduce((sum, item) => sum + item.file.size, 0);
      let done = 0;
      setSending(waiting.length ? { sent: 0, total } : null);
      for (const item of waiting) {
        setFiles((current) =>
          current.map((entry) =>
            entry.key === item.key ? { ...entry, state: 'sending' } : entry,
          ),
        );
        let fileId: string;
        try {
          fileId = await io.upload(
            id,
            item.file,
            (sent) => {
              setSending({ sent: done + sent, total });
              setFiles((current) =>
                current.map((entry) =>
                  entry.key === item.key ? { ...entry, sent } : entry,
                ),
              );
            },
            controller.signal,
          );
        } catch (reason) {
          setFiles((current) =>
            current.map((entry) =>
              entry.key === item.key ? { ...entry, state: 'failed' } : entry,
            ),
          );
          throw reason;
        }
        done += item.file.size;
        setFiles((current) =>
          current.map((entry) =>
            entry.key === item.key
              ? { ...entry, state: 'sent', fileId }
              : entry,
          ),
        );
      }
      setSending(null);
      if (pasted.trim()) {
        await io.addText(id, pasted);
        setPasted('');
      }
      setBatch(await io.step(id, 'scan'));
      const view = await follow(id);
      if (!view) return;
      if (view.job?.status === 'cancelled') {
        setStep('source');
        return;
      }
      if (view.job?.status === 'failed') {
        setError(jobProblem(view.job.code ?? 'import_scan_failed'));
        setStep('source');
        return;
      }
      await loadCandidates(id);
      setStep('review');
    } catch (reason) {
      if (!alive.current) return;
      setSending(null);
      setError(
        (reason as { name?: string })?.name === 'AbortError'
          ? ''
          : problem(reason),
      );
      setStep('source');
    }
  };

  const stopStep = async () => {
    uploads.current?.abort();
    const id = batchRef.current;
    if (id && batch?.job?.status === 'running')
      setBatch(await io.step(id, 'cancel').catch(() => batch));
  };

  const lookInConversations = async (on: boolean) => {
    setWithConversations(on);
    const id = batchRef.current;
    if (!on || !id || batch?.conversations.ran) {
      setChosen((current) => {
        const next = new Set(current);
        for (const item of candidates)
          if (item.origin === 'conversation')
            if (on) next.add(item.candidate_id);
            else next.delete(item.candidate_id);
        return next;
      });
      return;
    }
    setError('');
    try {
      setBatch(await io.step(id, 'conversations'));
      const view = await follow(id);
      if (!view) return;
      if (view.job?.status === 'failed') {
        setError(jobProblem(view.job.code));
        setWithConversations(false);
        return;
      }
      if (view.job?.status === 'cancelled') {
        setWithConversations(false);
        return;
      }
      await loadCandidates(id);
    } catch (reason) {
      if (!alive.current) return;
      setError(problem(reason));
      setWithConversations(false);
    }
  };

  const runPreview = async () => {
    const id = batchRef.current;
    if (!id) return;
    setError('');
    setStep('working');
    try {
      const items = candidates
        .filter((item) => chosen.has(item.candidate_id))
        .filter((item) => withConversations || item.origin === 'export')
        .map((item) => ({
          candidate_id: item.candidate_id,
          ...edits[item.candidate_id],
        }));
      setBatch(await io.preview(id, items));
      const view = await follow(id);
      if (!view) return;
      if (view.job?.status !== 'completed') {
        if (view.job?.status === 'failed') setError(jobProblem(view.job.code));
        setStep('review');
        return;
      }
      setPreview(await io.readPreview(id));
      setLeft(new Set());
      setStep('preview');
    } catch (reason) {
      if (!alive.current) return;
      setError(problem(reason));
      setStep('review');
    }
  };

  const runApply = async () => {
    const id = batchRef.current;
    if (!id || !preview) return;
    setError('');
    setBusy('apply');
    try {
      const result = await io.apply(id, {
        command_id: uuid(),
        preview_revision: preview.revision,
        exclude: [...left],
      });
      applied.current = true;
      setReceipt(result);
      setStep('done');
      onImported?.();
    } catch (reason) {
      const code = clientError(reason).code;
      setError(
        code === 'import_preview_stale'
          ? 'Some saved memories changed since this preview. Preview again before importing.'
          : problem(reason),
      );
    } finally {
      setBusy(null);
    }
  };

  const undo = async (target: MemoryImportReceipt) => {
    setBusy(target.batch_id);
    setError('');
    try {
      const result = await io.undo(target.batch_id, uuid());
      if (receipt?.batch_id === result.batch_id) setReceipt(result);
      setSources((current) =>
        current
          ? {
              ...current,
              recent: current.recent.map((item) =>
                item.batch_id === result.batch_id ? result : item,
              ),
            }
          : current,
      );
      setNotice('Import undone. The memories it added are gone.');
      onImported?.();
    } catch (reason) {
      setError(problem(reason));
    } finally {
      setBusy(null);
    }
  };

  const ready = files.length > 0 || pasted.trim().length > 0;
  return (
    <div className="memory-import" data-step={step}>
      <div className="memory-import-scroll">
        {error && <ErrorState title="That didn't work">{error}</ErrorState>}
        {notice && (
          <p role="status" className="memory-import-notice">
            {notice}
          </p>
        )}
        {step === 'source' &&
          (sourcesError ? (
            <ErrorState
              title="Import isn't available right now"
              action={
                <Button onClick={() => void loadSources()}>Try again</Button>
              }
            >
              {sourcesError}
            </ErrorState>
          ) : !sources ? (
            <Skeleton label="Loading where memories can come from" />
          ) : (
            <SourceStep
              sources={sources}
              sourceId={sourceId}
              onSource={setSourceId}
              files={files}
              pasted={pasted}
              onPasted={setPasted}
              busy={busy}
              onFiles={(picked) =>
                setFiles((current) => [
                  ...current,
                  ...picked.map((file) => ({
                    key: uuid(),
                    file,
                    sent: 0,
                    state: 'waiting' as const,
                  })),
                ])
              }
              onRemove={(key) => {
                const item = files.find((entry) => entry.key === key);
                setFiles((current) =>
                  current.filter((entry) => entry.key !== key),
                );
                const id = batchRef.current;
                if (id && item?.fileId)
                  void io.removeFile(id, item.fileId).catch(() => undefined);
              }}
              onUndo={(item) => void undo(item)}
            />
          ))}
        {step === 'working' && <WorkingStep batch={batch} sending={sending} />}
        {step === 'review' && batch && (
          <ReviewStep
            batch={batch}
            sources={sources}
            phone={phone}
            candidates={candidates}
            chosen={chosen}
            onChosen={setChosen}
            edits={edits}
            onEdits={setEdits}
            category={category}
            onCategory={setCategory}
            origin={origin}
            onOrigin={setOrigin}
            withConversations={withConversations}
            onConversations={(on) => void lookInConversations(on)}
            onStop={() => void stopStep()}
          />
        )}
        {step === 'preview' && preview && (
          <PreviewStep
            preview={preview}
            phone={phone}
            left={left}
            onLeft={setLeft}
          />
        )}
        {step === 'done' && receipt && <DoneStep receipt={receipt} />}
      </div>
      <footer className="memory-import-footer">
        {step === 'source' && (
          <Button
            variant="primary"
            disabled={!ready || !source}
            onClick={() => void findMemories()}
          >
            Find memories
          </Button>
        )}
        {step === 'working' && (
          <Button onClick={() => void stopStep()}>
            <Square size={14} aria-hidden /> Stop
          </Button>
        )}
        {step === 'review' && (
          <>
            <IconButton
              label="Back to where from"
              disabled={batch?.job?.status === 'running'}
              onClick={() => {
                setError('');
                setStep('source');
              }}
            >
              <ArrowLeft size={16} aria-hidden />
            </IconButton>
            <ReviewNext
              count={
                candidates.filter(
                  (item) =>
                    chosen.has(item.candidate_id) &&
                    (withConversations || item.origin === 'export'),
                ).length
              }
              disabled={batch?.job?.status === 'running'}
              onNext={() => void runPreview()}
            />
          </>
        )}
        {step === 'preview' && preview && (
          <>
            <IconButton
              label="Back to the memories found"
              disabled={busy !== null}
              onClick={() => {
                setError('');
                setStep('review');
              }}
            >
              <ArrowLeft size={16} aria-hidden />
            </IconButton>
            <Button
              variant="primary"
              disabled={busy !== null || saving(preview, left) === 0}
              onClick={() => void runApply()}
            >
              {busy === 'apply'
                ? 'Importing…'
                : `Import ${plural(saving(preview, left), 'memory', 'memories')}`}
            </Button>
          </>
        )}
        {step === 'done' && receipt && (
          <>
            {receipt.status === 'applied' && (
              <Button
                disabled={busy !== null}
                onClick={() => void undo(receipt)}
              >
                <Undo2 size={14} aria-hidden /> Undo import
              </Button>
            )}
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </>
        )}
      </footer>
    </div>
  );
}

function saving(preview: MemoryImportPreview, left: ReadonlySet<string>) {
  return preview.items.filter(
    (item) => item.action !== 'skip' && !left.has(item.candidate_id),
  ).length;
}

function ReviewNext({
  count,
  disabled,
  onNext,
}: {
  count: number;
  disabled: boolean;
  onNext: () => void;
}) {
  return (
    <Button
      variant="primary"
      disabled={disabled || count === 0}
      onClick={onNext}
    >
      {count ? `Preview ${plural(count, 'memory', 'memories')}` : 'Preview'}
    </Button>
  );
}

function SourceStep({
  sources,
  sourceId,
  onSource,
  files,
  pasted,
  onPasted,
  busy,
  onFiles,
  onRemove,
  onUndo,
}: {
  sources: MemoryImportSources;
  sourceId: string;
  onSource: (id: string) => void;
  files: readonly PickedFile[];
  pasted: string;
  onPasted: (text: string) => void;
  busy: string | null;
  onFiles: (files: File[]) => void;
  onRemove: (key: string) => void;
  onUndo: (receipt: MemoryImportReceipt) => void;
}) {
  const source = sources.sources.find((item) => item.id === sourceId);
  const name = source ? sourceName(source) : '';
  const fileRoute = source && source.export !== 'none';
  return (
    <div className="memory-import-step">
      <RecentImports items={sources.recent} busy={busy} onUndo={onUndo} />
      <h3 className="memory-import-heading">Where from?</h3>
      <SourceGrid
        sources={sources.sources}
        value={sourceId}
        onChange={onSource}
      />
      {source && (
        <section
          className="memory-import-route"
          aria-label={`Import from ${name}`}
        >
          <p className="memory-import-how">{source.how_to_get}</p>
          {fileRoute && (
            <FileDrop
              files={files}
              disabled={busy !== null}
              label={`Drop your ${name} export here`}
              maxBytes={sources.file_max_bytes}
              onAdd={onFiles}
              onRemove={onRemove}
            />
          )}
          {source.prompt_route && (
            <>
              <PromptCard prompt={sources.prompt} source={name} />
              <label className="memory-import-paste">
                <strong>2. Paste its answer here</strong>
                <textarea
                  className="input"
                  rows={5}
                  value={pasted}
                  maxLength={1_000_000}
                  placeholder="preference | 2026-05-01 | Likes short answers"
                  onChange={(event) => onPasted(event.target.value)}
                />
                <small>
                  If it says more remain, ask it to continue and paste the rest
                  too.
                </small>
              </label>
            </>
          )}
        </section>
      )}
    </div>
  );
}

function WorkingStep({
  batch,
  sending,
}: {
  batch: MemoryImportBatch | null;
  sending: { sent: number; total: number } | null;
}) {
  const job = batch?.job;
  let label = 'Starting…';
  let value: number | undefined;
  if (sending) {
    label = 'Sending your files to Row-Bot…';
    value = sending.total
      ? Math.round((sending.sent / sending.total) * 100)
      : 0;
  } else if (job?.kind === 'scan') {
    label = 'Reading your files…';
  } else if (job?.kind === 'preview') {
    label =
      job.total != null
        ? `Checking ${job.done.toLocaleString()} of ${job.total.toLocaleString()} against your memories…`
        : 'Checking against your memories…';
    value = job.total ? Math.round((job.done / job.total) * 100) : undefined;
  }
  return (
    <div className="memory-import-working" aria-live="polite">
      <Progress label={label} value={value} />
      <p className="memory-import-muted">
        Nothing is saved yet. You can stop at any time.
      </p>
    </div>
  );
}

function ReviewStep({
  batch,
  sources,
  phone,
  candidates,
  chosen,
  onChosen,
  edits,
  onEdits,
  category,
  onCategory,
  origin,
  onOrigin,
  withConversations,
  onConversations,
  onStop,
}: {
  batch: MemoryImportBatch;
  sources: MemoryImportSources | null;
  phone: boolean;
  candidates: readonly MemoryImportCandidate[];
  chosen: ReadonlySet<string>;
  onChosen: (chosen: ReadonlySet<string>) => void;
  edits: Record<string, Edit>;
  onEdits: (edits: Record<string, Edit>) => void;
  category: string;
  onCategory: (category: string) => void;
  origin: string;
  onOrigin: (origin: string) => void;
  withConversations: boolean;
  onConversations: (on: boolean) => void;
  onStop: () => void;
}) {
  const visible = useMemo(
    () =>
      candidates.filter(
        (item) =>
          (withConversations || item.origin === 'export') &&
          (!category ||
            (edits[item.candidate_id]?.category ?? item.category) ===
              category) &&
          (!origin || item.source === origin),
      ),
    [candidates, category, origin, withConversations, edits],
  );
  const included = candidates.filter(
    (item) => withConversations || item.origin === 'export',
  );
  const picked = included.filter((item) =>
    chosen.has(item.candidate_id),
  ).length;
  const sourceNames = new Map(
    batch.sources.map((item) => [item.source, item.label]),
  );
  const sourceOptions = [...new Set(included.map((item) => item.source))];
  const categories = CATEGORIES.filter((value) =>
    included.some(
      (item) => (edits[item.candidate_id]?.category ?? item.category) === value,
    ),
  );
  const setMany = (on: boolean) => {
    const next = new Set(chosen);
    for (const item of visible)
      if (on) next.add(item.candidate_id);
      else next.delete(item.candidate_id);
    onChosen(next);
  };
  const notes = batch.warnings.length + batch.skipped_count;
  const conversations = batch.conversations;
  const running = batch.job?.status === 'running' ? batch.job : null;
  const model = batch.model ?? sources?.model ?? null;
  return (
    <div className="memory-import-step">
      <p className="memory-import-counts" aria-live="polite">
        {plural(included.length, 'memory', 'memories')} found
        {batch.skipped_count
          ? ` · ${batch.skipped_count.toLocaleString()} skipped`
          : ''}
        {` · ${picked.toLocaleString()} chosen`}
      </p>
      {notes > 0 && (
        <Disclosure
          summary={`${plural(notes, 'note')} about your files`}
          className="memory-import-notes"
        >
          <ul>
            {batch.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
            {batch.skipped.map((item, index) => (
              <li key={`${index}:${item.name}`}>
                <strong>{item.name}</strong>: {item.reason}
              </li>
            ))}
            {batch.skipped_count > batch.skipped.length && (
              <li>
                And {batch.skipped_count - batch.skipped.length} more skipped.
              </li>
            )}
          </ul>
        </Disclosure>
      )}
      {conversations.available > 0 && (
        <div className="memory-import-conversations">
          <div className="memory-import-conversations-text">
            <strong id="memory-import-conversations-label">
              Also look for memories in{' '}
              {plural(conversations.available, 'past conversation')}
            </strong>
            <small>
              {model
                ? `Reads only what you wrote, newest first: up to ${conversations.limit.toLocaleString()} conversations or ${conversations.character_limit.toLocaleString()} characters. Uses ${model.name} ${model.on_this_computer ? 'on this computer' : `through ${model.provider}`}.`
                : 'Choose a chat model in Settings › Models to use this.'}
            </small>
            {running?.kind === 'conversations' && (
              <span className="memory-import-inline-progress">
                <Progress
                  label={
                    running.total
                      ? `Reading conversation ${Math.min(running.done + 1, running.total)} of ${running.total}…`
                      : 'Reading your conversations…'
                  }
                  value={
                    running.total
                      ? Math.round((running.done / running.total) * 100)
                      : undefined
                  }
                />
                <IconButton size="sm" label="Stop reading" onClick={onStop}>
                  <Square size={13} aria-hidden />
                </IconButton>
              </span>
            )}
            {conversations.ran && !running && withConversations && (
              <small role="status">
                Found{' '}
                {plural(conversations.found, 'more memory', 'more memories')} in{' '}
                {plural(conversations.read, 'conversation')}.
              </small>
            )}
          </div>
          <Toggle
            label="Also look in past conversations"
            aria-describedby="memory-import-conversations-label"
            checked={withConversations}
            disabled={!model || Boolean(running)}
            onChange={(event) => onConversations(event.target.checked)}
          />
        </div>
      )}
      {included.length === 0 ? (
        <EmptyState title="No memories found">
          {conversations.available > 0
            ? 'These files hold past chats but no saved memories. Turn on looking in past conversations, or paste the prompt’s answer.'
            : 'Check these are the export files you downloaded, or paste the prompt’s answer instead.'}
        </EmptyState>
      ) : (
        <>
          <div className="memory-import-filters">
            <Select
              aria-label="Type"
              value={category}
              onChange={(event) => onCategory(event.target.value)}
            >
              <option value="">All types</option>
              {categories.map((value) => (
                <option key={value} value={value}>
                  {categoryWords(value)}
                </option>
              ))}
            </Select>
            {sourceOptions.length > 1 && (
              <Select
                aria-label="Source"
                value={origin}
                onChange={(event) => onOrigin(event.target.value)}
              >
                <option value="">All sources</option>
                {sourceOptions.map((value) => (
                  <option key={value} value={value}>
                    {sourceNames.get(value) ?? value}
                  </option>
                ))}
              </Select>
            )}
            <span className="memory-import-filter-actions">
              <IconButton
                size="sm"
                label="Choose all shown"
                onClick={() => setMany(true)}
              >
                <ListChecks size={15} aria-hidden />
              </IconButton>
              <IconButton
                size="sm"
                label="Choose none shown"
                onClick={() => setMany(false)}
              >
                <ListX size={15} aria-hidden />
              </IconButton>
            </span>
          </div>
          {visible.length === 0 ? (
            <InlineEmpty>No memories match these filters.</InlineEmpty>
          ) : (
            <VirtualList
              items={visible}
              rowHeight={phone ? 84 : 64}
              label="Memories found"
              rowKey={(item) => item.candidate_id}
              render={(item) => (
                <CandidateRow
                  item={item}
                  edit={edits[item.candidate_id]}
                  checked={chosen.has(item.candidate_id)}
                  onCheck={(on) => {
                    const next = new Set(chosen);
                    if (on) next.add(item.candidate_id);
                    else next.delete(item.candidate_id);
                    onChosen(next);
                  }}
                  onEdit={(edit) => {
                    const next = { ...edits };
                    if (edit) next[item.candidate_id] = edit;
                    else delete next[item.candidate_id];
                    onEdits(next);
                  }}
                />
              )}
            />
          )}
        </>
      )}
    </div>
  );
}

function PreviewStep({
  preview,
  phone,
  left,
  onLeft,
}: {
  preview: MemoryImportPreview;
  phone: boolean;
  left: ReadonlySet<string>;
  onLeft: (left: ReadonlySet<string>) => void;
}) {
  const count = (action: string) =>
    preview.items.filter((item) => item.action === action).length;
  const [shown, setShown] = useState('');
  const items = shown
    ? preview.items.filter((item) => item.action === shown)
    : preview.items;
  return (
    <div className="memory-import-step">
      <StatGroup label="What importing does" className="memory-import-stats">
        <Stat label="New" value={count('add')} />
        <Stat label="Add to saved" value={count('update')} />
        <Stat label="Replace saved" value={count('supersede')} />
        <Stat label="Already saved" value={count('skip')} />
      </StatGroup>
      {count('supersede') > 0 && (
        <p className="memory-import-muted">
          A memory that is replaced is kept as history, so Row-Bot can still say
          what used to be true.
        </p>
      )}
      <div className="memory-import-filters">
        <Select
          aria-label="Show"
          value={shown}
          onChange={(event) => setShown(event.target.value)}
        >
          <option value="">Everything</option>
          <option value="add">New</option>
          <option value="update">Adds to a saved memory</option>
          <option value="supersede">Replaces a saved memory</option>
          <option value="skip">Already saved</option>
        </Select>
      </div>
      {items.length === 0 ? (
        <InlineEmpty>Nothing here.</InlineEmpty>
      ) : (
        <VirtualList
          items={items}
          rowHeight={phone ? 84 : 64}
          label="What each memory does"
          rowKey={(item) => item.candidate_id}
          render={(item) => (
            <PreviewRow
              item={item}
              checked={!left.has(item.candidate_id)}
              onCheck={(on) => {
                const next = new Set(left);
                if (on) next.delete(item.candidate_id);
                else next.add(item.candidate_id);
                onLeft(next);
              }}
            />
          )}
        />
      )}
    </div>
  );
}

function DoneStep({ receipt }: { receipt: MemoryImportReceipt }) {
  const undone = receipt.status === 'undone';
  return (
    <div className="memory-import-step memory-import-done" role="status">
      <span
        className="memory-import-done-icon"
        data-undone={undone}
        aria-hidden
      >
        {undone ? <Undo2 size={22} /> : <CircleCheck size={22} />}
      </span>
      <h3 className="memory-import-heading">
        {undone
          ? 'Import undone'
          : `Imported from ${listWords(receipt.sources) || 'another assistant'}`}
      </h3>
      {undone ? (
        <p className="memory-import-muted">
          The memories it added are gone and the ones it changed read as they
          did before.
        </p>
      ) : (
        <>
          <StatGroup label="What was imported" className="memory-import-stats">
            <Stat label="New" value={receipt.added + receipt.superseded} />
            <Stat label="Updated" value={receipt.updated} />
            <Stat label="Already saved" value={receipt.skipped} />
          </StatGroup>
          <p className="memory-import-muted">
            {receipt.failed
              ? `${plural(receipt.failed, 'memory', 'memories')} couldn't be saved. `
              : ''}
            You can undo this import until {shortDate(receipt.undo_until)}.
          </p>
        </>
      )}
    </div>
  );
}
