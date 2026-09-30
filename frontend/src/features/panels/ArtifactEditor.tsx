import { useEffect, useRef, useState, type ReactNode } from 'react';
import type {
  ArtifactEditingState,
  ArtifactEditPayload,
  CommandReceipt,
} from '../../api/types';
import { absoluteTime, humanizeToken, relativeTime } from '../../ui/format';
import { Button, ErrorState, Input, Skeleton } from '../../ui/primitives';
import { DesignGroup, DesignRow } from './ArtifactDesignStyles';

export type ArtifactEditingOptions = {
  pageId?: string;
  pageCursor?: string;
  elementCursor?: string;
  historyCursor?: string;
  elementId?: string;
  limit?: number;
};
export type ArtifactEditorProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  /**
   * Which part this instance shows: the page's name and notes, the selected
   * text's own text, or the saved versions.
   */
  view?: 'page' | 'text' | 'history';
  /** More page settings shown first under Page (its size). */
  pageExtras?: ReactNode;
  pageId?: string;
  selectedElementId?: string;
  /** The selected element is no longer in the saved design. */
  onSelectionLost?: (elementId: string) => void;
  onPageChange: (pageId: string) => void;
  load: (
    options: ArtifactEditingOptions,
    signal: AbortSignal,
  ) => Promise<ArtifactEditingState>;
  edit: (
    payload: Omit<ArtifactEditPayload, 'target'>,
    expectedRevision: string,
  ) => Promise<CommandReceipt>;
  generateNotes?: (
    pageId: string,
    revision: string,
  ) => Promise<{
    resource_revision: string;
    status?: 'saved' | 'unchanged' | 'partial';
  }>;
  onEdited?: () => void;
};
type Draft = {
  revision: string;
  title?: string;
  notes?: string;
  texts: Record<string, string>;
};
type ListKind = 'page' | 'element' | 'history';

const OPERATION_WORDS: Record<string, string> = {
  project_properties: 'renaming',
  page_properties: 'a page edit',
  text: 'a text edit',
  restore: 'restoring a version',
  brand: 'a brand change',
  style: 'a style change',
  preset: 'applying a preset',
  hotspot: 'an interaction change',
  image: 'an image change',
  review_fix: 'a review fix',
  review_fix_all: 'fixing safe issues',
  asset_insert: 'inserting an asset',
  asset_remove: 'removing an asset',
  block_insert: 'inserting a block',
  page_add: 'adding a page',
  page_delete: 'deleting a page',
  canvas_size: 'a size change',
};

/** A saved version's label in words ("Before a text edit"). */
export function historyLabel(label: string): string {
  const panel = /^Before panel (\w+)$/.exec(label);
  if (panel)
    return `Before ${OPERATION_WORDS[panel[1]] ?? humanizeToken(panel[1]).toLowerCase()}`;
  if (/^Before /.test(label)) return label;
  const page = /_page_(\d+)$/.exec(label);
  const words = humanizeToken(
    label
      .replace(/_ui$/, '')
      .replace(/_page_\d+$/, '')
      .replace(/_\d+$/, ''),
  );
  if (words && page)
    return `Before: ${words.toLowerCase()} (page ${Number(page[1]) + 1})`;
  return words ? `Before: ${words.toLowerCase()}` : 'Saved version';
}

function errorCode(error: unknown) {
  return typeof error === 'object' && error !== null && 'code' in error
    ? String(error.code)
    : '';
}

function failure(error: unknown) {
  const code = errorCode(error);
  if (
    [
      'action_denied',
      'capability_revoked',
      'resource_binding_revoked',
      'not_found',
      'resource_unavailable',
    ].includes(code)
  )
    return {
      denied: true,
      text: 'Access to this design changed. Review its binding before continuing.',
    };
  if (code === 'resource_revision_conflict' || code === 'revision_conflict')
    return {
      denied: false,
      text: 'This design changed. Your draft is kept; reload the saved values to continue.',
    };
  if (code === 'history_unavailable')
    return {
      denied: false,
      text: 'That saved version is unavailable. The design is unchanged.',
    };
  if (code === 'element_unavailable')
    return {
      denied: false,
      text: "That text is no longer in the design, so the edit wasn't saved. Choose the text again.",
    };
  if (code === 'page_unavailable')
    return {
      denied: false,
      text: 'That page is no longer in the design. Choose a page to continue.',
    };
  if (code === 'editing_record_too_large')
    return {
      denied: false,
      text: 'This saved record exceeds the panel limit. It remains available in Designer Studio.',
    };
  return {
    denied: false,
    text: 'The design could not be updated. Your draft is kept.',
  };
}

function dirty(draft?: Draft) {
  return (
    !!draft &&
    (draft.title !== undefined ||
      draft.notes !== undefined ||
      Object.keys(draft.texts).length > 0)
  );
}

/**
 * Page, text and history editing for the Design inspector. Fields save when
 * they lose focus (or on Enter); a conflict keeps the draft and asks for the
 * saved values first. Nothing is sent until a field is committed.
 */
export default function ArtifactEditor(props: ArtifactEditorProps) {
  const { resourceId, resourceRevision, pageId, selectedElementId, visible } =
    props;
  const view = props.view ?? 'page';
  const [state, setState] = useState<ArtifactEditingState | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [selection, setSelection] = useState({
    resourceId,
    pageId: '',
    elementId: '',
  });
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const epoch = useRef(0);
  const saveScope = useRef<symbol | null>(null);
  const operation = useRef<symbol | null>(null);
  const abort = useRef<AbortController | null>(null);
  const callbacks = useRef(props);
  useEffect(() => {
    callbacks.current = props;
  }, [props]);

  useEffect(() => {
    saveScope.current = Symbol('design selection');
    setNotice('');
    return () => {
      saveScope.current = null;
    };
  }, [resourceId, pageId, selectedElementId, visible]);

  useEffect(() => {
    const request = ++epoch.current;
    abort.current?.abort();
    if (!visible) return;
    const controller = new AbortController();
    abort.current = controller;
    setLoading(true);
    setError('');
    callbacks.current
      .load({ pageId, elementId: selectedElementId }, controller.signal)
      .then(
        (result) => {
          if (controller.signal.aborted || request !== epoch.current) return;
          if (
            result.resource_id !== resourceId ||
            (pageId && result.page_id !== pageId)
          ) {
            saveScope.current = null;
            setState(null);
            setError(
              'The panel returned a different design or page. Reload this design.',
            );
          } else {
            setState(result);
            setSelection({
              resourceId,
              pageId: result.page_id,
              elementId: selectedElementId ?? '',
            });
            const key = `${resourceId}:${result.page_id}`;
            setDrafts((current) =>
              dirty(current[key])
                ? current
                : {
                    ...current,
                    [key]: { revision: result.resource_revision, texts: {} },
                  },
            );
          }
          setLoading(false);
        },
        (reason: unknown) => {
          if (controller.signal.aborted || request !== epoch.current) return;
          setLoading(false);
          // The selected element is gone: the page is read without it.
          if (
            selectedElementId &&
            errorCode(reason) === 'element_unavailable'
          ) {
            callbacks.current.onSelectionLost?.(selectedElementId);
            return;
          }
          const result = failure(reason);
          setError(result.text);
          if (result.denied) {
            saveScope.current = null;
            setState(null);
            setDrafts({});
          }
        },
      );
    return () => {
      controller.abort();
      abort.current?.abort();
    };
  }, [
    resourceId,
    resourceRevision,
    pageId,
    selectedElementId,
    visible,
    refresh,
  ]);

  const current =
    state?.resource_id === resourceId && (!pageId || state.page_id === pageId)
      ? state
      : null;
  const key = `${resourceId}:${current?.page_id ?? ''}`;
  const draft = drafts[key];
  const stale =
    !!current && dirty(draft) && draft.revision !== current.resource_revision;
  const elementId =
    selection.resourceId === resourceId && selection.pageId === current?.page_id
      ? selection.elementId
      : '';
  const element = current?.elements.find((item) => item.id === elementId);
  const blocked = loading || saving || stale || !current;

  function change(fields: Partial<Draft>) {
    if (!current) return;
    setDrafts((all) => ({
      ...all,
      [key]: {
        ...(all[key] ?? { revision: current.resource_revision, texts: {} }),
        ...fields,
      },
    }));
  }

  function discard(fields: ('title' | 'notes')[] | { text: string }) {
    setDrafts((all) => {
      const next = { ...all[key], texts: { ...all[key]?.texts } };
      if (Array.isArray(fields)) fields.forEach((field) => delete next[field]);
      else delete next.texts[fields.text];
      return { ...all, [key]: next };
    });
  }

  async function more(kind: ListKind) {
    if (!current || loading || saving) return;
    const cursor = current[`${kind}_next_cursor`];
    if (!cursor) return;
    const request = ++epoch.current;
    const controller = new AbortController();
    abort.current?.abort();
    abort.current = controller;
    setLoading(true);
    setError('');
    const options = { pageId: current.page_id, [`${kind}Cursor`]: cursor };
    try {
      const next = await callbacks.current.load(options, controller.signal);
      if (controller.signal.aborted || request !== epoch.current) return;
      if (
        next.resource_id !== resourceId ||
        next.page_id !== current.page_id ||
        next.resource_revision !== current.resource_revision
      )
        throw { code: 'resource_revision_conflict' };
      const field =
        kind === 'page' ? 'pages' : kind === 'element' ? 'elements' : 'history';
      const existingIds = new Set(current[field].map((item) => item.id));
      setState({
        ...current,
        [field]: [
          ...current[field],
          ...next[field].filter((item) => !existingIds.has(item.id)),
        ],
        [`${kind}_next_cursor`]: next[`${kind}_next_cursor`],
      });
    } catch (reason) {
      if (controller.signal.aborted || request !== epoch.current) return;
      const result = failure(reason);
      setError(result.text);
      if (result.denied) {
        setState(null);
        setDrafts({});
      }
    } finally {
      if (request === epoch.current) setLoading(false);
    }
  }

  async function save(payload: Omit<ArtifactEditPayload, 'target'>) {
    if (!current || blocked || operation.current !== null) return;
    const admitted = Symbol('artifact edit');
    operation.current = admitted;
    const request = saveScope.current;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const receipt = await callbacks.current.edit(
        payload,
        current.resource_revision,
      );
      if (request !== saveScope.current) return;
      if (
        receipt.status !== 'completed' ||
        receipt.resource_id !== resourceId ||
        !receipt.resource_revision
      )
        throw { code: receipt.code ?? 'save_incomplete' };
      setDrafts((all) => {
        const next = {
          ...all[key],
          revision: receipt.resource_revision!,
          texts: { ...all[key]?.texts },
        };
        // Only the fields this save sent are settled; other drafts stay.
        if (payload.operation === 'page_properties') {
          if (payload.title != null) delete next.title;
          if (payload.notes != null) delete next.notes;
        }
        if (payload.operation === 'text' && payload.element_id)
          delete next.texts[payload.element_id];
        return { ...all, [key]: next };
      });
      setNotice(
        payload.operation === 'restore' ? 'Version restored.' : 'Saved.',
      );
      callbacks.current.onEdited?.();
      setRefresh((value) => value + 1);
    } catch (reason) {
      if (request !== saveScope.current) return;
      const result = failure(reason);
      setError(result.text);
      if (result.denied) {
        setState(null);
        setDrafts({});
      }
    } finally {
      if (operation.current === admitted) {
        operation.current = null;
        setSaving(false);
      }
    }
  }

  // Auto-save: a field committed while another save or reload runs (tabbing
  // from notes to the title) waits for it instead of being dropped.
  const queued = useRef<Set<string>>(new Set());
  function commitPage(field: 'title' | 'notes') {
    if (!current || !draft) return;
    if (operation.current || loading || saving) {
      queued.current.add(field);
      return;
    }
    const value = draft[field];
    if (value === undefined) return;
    const saved = field === 'title' ? current.page_title : current.page_notes;
    if (value === saved) {
      discard([field]);
      return;
    }
    if (field === 'title' && !value.trim()) {
      discard(['title']);
      setNotice('A page needs a title; the saved title is kept.');
      return;
    }
    void save({
      operation: 'page_properties',
      page_id: current.page_id,
      [field]: value,
    });
  }

  function commitText(id: string, saved: string) {
    if (!current || !draft) return;
    if (operation.current || loading || saving) {
      queued.current.add(`text:${id}`);
      return;
    }
    const value = draft.texts[id];
    if (value === undefined) return;
    if (value === saved) {
      discard({ text: id });
      return;
    }
    void save({
      operation: 'text',
      page_id: current.page_id,
      element_id: id,
      text: value,
    });
  }

  useEffect(() => {
    if (loading || saving || !current || stale || !queued.current.size) return;
    const [next] = queued.current;
    queued.current.delete(next);
    if (next === 'title' || next === 'notes') commitPage(next);
    else {
      const id = next.slice('text:'.length);
      const element = current.elements.find((item) => item.id === id);
      if (element) commitText(id, element.text);
    }
    // Commits read the latest drafts; only settling work releases the queue.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, saving, current, stale]);

  async function generateNotes() {
    if (
      !current ||
      blocked ||
      dirty(draft) ||
      !props.generateNotes ||
      operation.current
    )
      return;
    const admitted = Symbol('speaker notes');
    operation.current = admitted;
    const request = saveScope.current;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const outcome = await props.generateNotes(
        current.page_id,
        current.resource_revision,
      );
      if (request !== saveScope.current) return;
      if (outcome.status === 'partial') throw new Error('notes_unconfirmed');
      setNotice(
        outcome.status === 'unchanged'
          ? 'Speaker notes are already up to date.'
          : 'Speaker notes generated.',
      );
      callbacks.current.onEdited?.();
      setRefresh((value) => value + 1);
    } catch {
      if (request === saveScope.current)
        setError(
          'Note generation was not confirmed. Check the original design command before retrying.',
        );
    } finally {
      if (operation.current === admitted) {
        operation.current = null;
        setSaving(false);
      }
    }
  }

  if (!visible) return null;
  const pageWord = current?.mode === 'deck' ? 'Slide' : 'Page';
  const status = saving ? 'Saving…' : notice;
  return (
    <section
      className="design-editor"
      aria-label={view === 'history' ? 'Design history' : 'Design editing'}
      aria-busy={loading || saving}
    >
      {error && (
        <ErrorState
          title="That didn't work"
          action={
            <Button
              disabled={loading || saving}
              onClick={() => setRefresh((value) => value + 1)}
            >
              Retry
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {loading && !current && view !== 'text' && (
        <Skeleton
          label={
            view === 'history' ? 'Loading design history' : 'Loading this page'
          }
        />
      )}
      {current && stale && (
        <div className="inspector-callout" role="status">
          <p>
            The saved design changed. Your draft is kept below; reload the saved
            values before editing again.
          </p>
          <Button
            disabled={saving}
            onClick={() =>
              setDrafts((all) => ({
                ...all,
                [key]: { revision: current.resource_revision, texts: {} },
              }))
            }
          >
            Reload saved values
          </Button>
        </div>
      )}
      {current &&
        view === 'text' &&
        element &&
        (element.editable ? (
          <textarea
            aria-label="Element text"
            className="input design-text-field"
            rows={2}
            maxLength={20000}
            value={draft?.texts[element.id] ?? element.text}
            disabled={saving || stale}
            onChange={(event) =>
              change({
                texts: {
                  ...draft?.texts,
                  [element.id]: event.target.value,
                },
              })
            }
            onBlur={() => commitText(element.id, element.text)}
          />
        ) : (
          <p className="muted design-text-field">
            This text is too long to change here. Double-click it on the page or
            ask Row-Bot to change it.
          </p>
        ))}
      {current && view === 'page' && (
        <>
          <DesignGroup title={pageWord}>
            {props.pageExtras}
            <DesignRow label="Name">
              <Input
                aria-label="Page name"
                maxLength={200}
                value={draft?.title ?? current.page_title}
                disabled={saving || stale}
                onChange={(event) => change({ title: event.target.value })}
                onBlur={() => commitPage('title')}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') event.currentTarget.blur();
                  if (event.key === 'Escape') discard(['title']);
                }}
              />
            </DesignRow>
          </DesignGroup>
          <DesignGroup
            title="Notes"
            action={
              props.generateNotes &&
              ['deck', 'storyboard'].includes(current.mode) && (
                <Button
                  variant="ghost"
                  className="design-group-link"
                  disabled={blocked || dirty(draft)}
                  onClick={() => void generateNotes()}
                >
                  Write with Row-Bot
                </Button>
              )
            }
          >
            <textarea
              aria-label="Page notes"
              className="input"
              rows={3}
              maxLength={20000}
              placeholder="Speaker notes and comments"
              value={draft?.notes ?? current.page_notes}
              disabled={saving || stale}
              onChange={(event) => change({ notes: event.target.value })}
              onBlur={() => commitPage('notes')}
            />
            {props.generateNotes &&
              ['deck', 'storyboard'].includes(current.mode) && (
                <small className="muted">
                  Write with Row-Bot uses the current model and may incur
                  provider charges.
                </small>
              )}
          </DesignGroup>
        </>
      )}
      {current && view === 'history' && (
        <>
          <p className="inspector-meta">
            {current.history_count}{' '}
            {current.history_count === 1 ? 'saved version' : 'saved versions'}.
            Restoring keeps the current version in history too.
          </p>
          {dirty(draft) && (
            <p className="muted">
              Save or reload your draft before restoring a saved version.
            </p>
          )}
          {!current.history_count && (
            <p className="muted">History is saved before your first edit.</p>
          )}
          <ol className="design-history" aria-label="Saved versions">
            {current.history.map((item) => {
              const label = historyLabel(item.label);
              const seconds = Number(item.id);
              return (
                <li key={item.id} data-available={item.available || undefined}>
                  <span className="design-history-label">{label}</span>
                  <span className="design-history-meta">
                    {item.author === 'agent'
                      ? 'Row-Bot'
                      : item.author === 'user'
                        ? 'You'
                        : 'Unknown'}{' '}
                    · {item.page_count}{' '}
                    {item.page_count === 1 ? 'page' : 'pages'}
                    {Number.isFinite(seconds) && seconds > 0 && (
                      <>
                        {' '}
                        ·{' '}
                        <time
                          dateTime={new Date(seconds * 1000).toISOString()}
                          title={absoluteTime(seconds)}
                        >
                          {relativeTime(seconds)}
                        </time>
                      </>
                    )}
                  </span>
                  <Button
                    className="design-history-restore"
                    aria-label={`Restore ${item.label || item.id}`}
                    disabled={blocked || dirty(draft) || !item.available}
                    onClick={() =>
                      void save({ operation: 'restore', snapshot_id: item.id })
                    }
                  >
                    Restore
                  </Button>
                </li>
              );
            })}
          </ol>
          {current.history_next_cursor && (
            <Button
              disabled={loading || saving}
              onClick={() => void more('history')}
            >
              Load more history
            </Button>
          )}
        </>
      )}
      <p className="inspector-status" role="status">
        {status}
      </p>
    </section>
  );
}
