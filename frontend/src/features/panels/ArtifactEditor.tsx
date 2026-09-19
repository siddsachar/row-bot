import { useEffect, useRef, useState } from 'react';
import type {
  ArtifactEditingState,
  ArtifactEditPayload,
  CommandReceipt,
} from '../../api/types';
import {
  Button,
  ErrorState,
  Field,
  Input,
  Select,
  Skeleton,
  Tabs,
} from '../../ui/primitives';

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
  pageId?: string;
  selectedElementId?: string;
  authoring?: boolean;
  onPageChange: (pageId: string) => void;
  onAuthoringChange?: (enabled: boolean) => void;
  load: (
    options: ArtifactEditingOptions,
    signal: AbortSignal,
  ) => Promise<ArtifactEditingState>;
  edit: (
    payload: Omit<ArtifactEditPayload, 'target'>,
    expectedRevision: string,
  ) => Promise<CommandReceipt>;
  onEdited?: () => void;
};
type Draft = {
  revision: string;
  name?: string;
  title?: string;
  notes?: string;
  texts: Record<string, string>;
};
type ListKind = 'page' | 'element' | 'history';

function failure(error: unknown) {
  const code =
    typeof error === 'object' && error !== null && 'code' in error
      ? error.code
      : '';
  if (
    [
      'action_denied',
      'capability_revoked',
      'resource_binding_revoked',
      'not_found',
      'resource_unavailable',
    ].includes(String(code))
  )
    return {
      denied: true,
      text: 'Access to this design changed. Review its binding before continuing.',
    };
  if (code === 'resource_revision_conflict' || code === 'revision_conflict')
    return {
      denied: false,
      text: 'This design changed. Your draft is preserved. Refresh to review the saved version.',
    };
  if (code === 'history_unavailable')
    return {
      denied: false,
      text: 'That history snapshot is unavailable. The saved design is preserved.',
    };
  if (code === 'element_unavailable' || code === 'page_unavailable')
    return {
      denied: false,
      text: 'The selected page or element changed. Refresh to select its current version.',
    };
  if (code === 'editing_record_too_large')
    return {
      denied: false,
      text: 'This saved record exceeds the panel limit. It remains available in Designer Studio.',
    };
  return {
    denied: false,
    text: 'The design could not be updated. Your draft is preserved; refresh to check the saved version.',
  };
}

function dirty(draft?: Draft) {
  return (
    !!draft &&
    (draft.name !== undefined ||
      draft.title !== undefined ||
      draft.notes !== undefined ||
      Object.keys(draft.texts).length > 0)
  );
}

export default function ArtifactEditor(props: ArtifactEditorProps) {
  const { resourceId, resourceRevision, pageId, selectedElementId, visible } =
    props;
  const [state, setState] = useState<ArtifactEditingState | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [tab, setTab] = useState('properties');
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
              'The panel returned a different design or page. Refresh this design.',
            );
          } else {
            setState(result);
            setSelection({
              resourceId,
              pageId: result.page_id,
              elementId: selectedElementId ?? result.elements[0]?.id ?? '',
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
          const result = failure(reason);
          setError(result.text);
          setLoading(false);
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
        if (payload.operation === 'project_properties') delete next.name;
        if (payload.operation === 'page_properties') {
          delete next.title;
          delete next.notes;
        }
        if (payload.operation === 'text' && payload.element_id)
          delete next.texts[payload.element_id];
        return { ...all, [key]: next };
      });
      setNotice('Changes saved.');
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

  if (!visible) return null;
  return (
    <section
      className="studio-section stack"
      aria-label="Design editing"
      aria-busy={loading || saving}
    >
      <div className="toolbar panel-toolbar action-cluster">
        <Button
          disabled={loading || saving}
          onClick={() => setRefresh((value) => value + 1)}
        >
          Refresh properties
        </Button>
        {props.onAuthoringChange && (
          <Button
            aria-pressed={props.authoring ?? false}
            disabled={saving || !current}
            onClick={() => props.onAuthoringChange?.(!props.authoring)}
          >
            Edit in preview
          </Button>
        )}
      </div>
      {error && (
        <ErrorState title="Design update unavailable">{error}</ErrorState>
      )}
      {loading && <Skeleton label="Loading design properties" />}
      {notice && <p role="status">{notice}</p>}
      {current && (
        <>
          {stale && (
            <p role="status">
              The saved design changed. Your draft is kept below. Reload saved
              values before making another edit.
            </p>
          )}
          {dirty(draft) && (
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
          )}
          <Tabs
            label="Design controls"
            value={tab}
            onChange={setTab}
            items={[
              {
                id: 'pages',
                label: 'Pages',
                content: (
                  <>
                    <p>
                      {current.pages.length} of {current.page_count} pages
                    </p>
                    <div
                      className="capability-summary"
                      role="list"
                      aria-label="Design pages"
                    >
                      {current.pages.map((page) => (
                        <div key={page.id} role="listitem">
                          <Button
                            disabled={saving}
                            aria-current={
                              page.id === current.page_id ? 'page' : undefined
                            }
                            onClick={() => props.onPageChange(page.id)}
                          >
                            {page.index + 1}. {page.title}
                          </Button>
                        </div>
                      ))}
                    </div>
                    {current.page_next_cursor && (
                      <Button
                        disabled={loading || saving}
                        onClick={() => void more('page')}
                      >
                        Load more pages
                      </Button>
                    )}
                  </>
                ),
              },
              {
                id: 'properties',
                label: 'Properties',
                content: (
                  <>
                    <p className="muted">
                      {current.mode.replaceAll('_', ' ')} ·{' '}
                      {current.canvas_width} × {current.canvas_height}
                    </p>
                    <Field label="Design name">
                      <Input
                        maxLength={200}
                        value={draft?.name ?? current.name}
                        disabled={saving}
                        onChange={(event) =>
                          change({ name: event.target.value })
                        }
                      />
                    </Field>
                    <Button
                      disabled={
                        blocked ||
                        draft?.name === undefined ||
                        !draft.name.trim()
                      }
                      onClick={() =>
                        void save({
                          operation: 'project_properties',
                          name: draft?.name,
                        })
                      }
                    >
                      Save design name
                    </Button>
                    <Field label="Page title">
                      <Input
                        maxLength={200}
                        value={draft?.title ?? current.page_title}
                        disabled={saving}
                        onChange={(event) =>
                          change({ title: event.target.value })
                        }
                      />
                    </Field>
                    <Field label="Page notes">
                      <textarea
                        className="input"
                        rows={3}
                        maxLength={20000}
                        value={draft?.notes ?? current.page_notes}
                        disabled={saving}
                        onChange={(event) =>
                          change({ notes: event.target.value })
                        }
                      />
                    </Field>
                    <Button
                      disabled={
                        blocked ||
                        (draft?.title === undefined &&
                          draft?.notes === undefined) ||
                        (draft.title !== undefined && !draft.title.trim())
                      }
                      onClick={() =>
                        void save({
                          operation: 'page_properties',
                          page_id: current.page_id,
                          title: draft?.title,
                          notes: draft?.notes,
                        })
                      }
                    >
                      Save page properties
                    </Button>
                    <Field label="Text element">
                      <Select
                        value={elementId}
                        disabled={saving}
                        onChange={(event) =>
                          setSelection({
                            resourceId,
                            pageId: current.page_id,
                            elementId: event.target.value,
                          })
                        }
                      >
                        {!current.elements.length && (
                          <option value="">
                            No editable text on this page
                          </option>
                        )}
                        {current.elements.map((item, index) => (
                          <option key={item.id} value={item.id}>
                            {item.tag}:{' '}
                            {item.editable
                              ? item.text.slice(0, 60)
                              : `Text ${index + 1} exceeds the edit limit`}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    {element?.editable ? (
                      <>
                        <Field
                          label="Element text"
                          hint="Plain text only. Apply saves this exact element."
                        >
                          <textarea
                            aria-label="Element text"
                            className="input"
                            rows={4}
                            maxLength={20000}
                            value={draft?.texts[element.id] ?? element.text}
                            disabled={saving}
                            onChange={(event) =>
                              change({
                                texts: {
                                  ...draft?.texts,
                                  [element.id]: event.target.value,
                                },
                              })
                            }
                          />
                        </Field>
                        <Button
                          disabled={
                            blocked || draft?.texts[element.id] === undefined
                          }
                          onClick={() =>
                            void save({
                              operation: 'text',
                              page_id: current.page_id,
                              element_id: element.id,
                              text: draft?.texts[element.id],
                            })
                          }
                        >
                          Apply text edit
                        </Button>
                      </>
                    ) : (
                      element && (
                        <p>
                          This text exceeds the editing limit. The complete
                          source is preserved in Designer Studio.
                        </p>
                      )
                    )}
                    <p className="muted">
                      {current.elements.length} of {current.element_count} text
                      elements
                    </p>
                    {current.element_next_cursor && (
                      <Button
                        disabled={loading || saving}
                        onClick={() => void more('element')}
                      >
                        Load more elements
                      </Button>
                    )}
                  </>
                ),
              },
              {
                id: 'history',
                label: 'History',
                content: (
                  <>
                    <p>
                      {current.history.length} of {current.history_count} saved
                      snapshots
                    </p>
                    {dirty(draft) && (
                      <p>
                        Save or reload your draft before restoring a saved
                        version.
                      </p>
                    )}
                    {!current.history_count && (
                      <p>History is saved before your first edit.</p>
                    )}
                    <div
                      className="capability-summary"
                      role="list"
                      aria-label="Design history"
                    >
                      {current.history.map((item) => (
                        <div role="listitem" key={item.id}>
                          <p>
                            {item.label || 'Saved version'} · {item.page_count}{' '}
                            pages · {item.author}
                          </p>
                          <Button
                            disabled={
                              blocked || dirty(draft) || !item.available
                            }
                            onClick={() =>
                              void save({
                                operation: 'restore',
                                snapshot_id: item.id,
                              })
                            }
                          >
                            Restore {item.label || item.id}
                          </Button>
                        </div>
                      ))}
                    </div>
                    {current.history_next_cursor && (
                      <Button
                        disabled={loading || saving}
                        onClick={() => void more('history')}
                      >
                        Load more history
                      </Button>
                    )}
                  </>
                ),
              },
            ]}
          />
        </>
      )}
    </section>
  );
}
