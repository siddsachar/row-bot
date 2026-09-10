import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import type {
  CommandReceipt,
  ConversationWorkspace,
  DeckSetupOptions,
  PanelDescriptor,
  ResourceChoice,
  ResourceChoicePage,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useClientState, useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Field, Input, Select, Skeleton } from '../../ui/primitives';
import { setupSessions, type SetupDraft } from './setup-state';
import type { HomeSetupEntry } from './Home';

/** The target is captured by the opener. Domain setup never reads panel focus. */
export default function ResourceSetup({
  conversationId,
  onPanel,
  initialEntry,
}: {
  conversationId: string | null;
  onPanel: (panel: PanelDescriptor) => void;
  initialEntry?: HomeSetupEntry;
}) {
  const { controller } = useRuntime();
  const client = useClientState();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const location = useLocation();
  const presentationContext = useRef({
    route: location.key,
    instance: client.handshake?.instance_id,
    session: client.handshake?.client_session_id,
  });
  useLayoutEffect(() => {
    presentationContext.current = {
      route: location.key,
      instance: client.handshake?.instance_id,
      session: client.handshake?.client_session_id,
    };
  }, [
    location.key,
    client.handshake?.instance_id,
    client.handshake?.client_session_id,
  ]);
  const scope = setupSessions.scope(
    client.handshake?.instance_id ?? 'unavailable',
    conversationId,
  );
  const record = useSyncExternalStore(
    useCallback((notify) => setupSessions.subscribe(scope, notify), [scope]),
    useCallback(() => setupSessions.read(scope), [scope]),
  );
  const {
    kind,
    mode,
    selected,
    template,
    canvas,
    name,
    brief,
    generate,
    receipt,
    generationId,
    generationReceipt,
  } = record;
  const [options, setOptions] = useState<DeckSetupOptions | null>(null);
  const [library, setLibrary] = useState<ResourceChoicePage | null>(null);
  const [folder, setFolder] = useState<{ grant: string; name: string } | null>(
    null,
  );
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [review, setReview] = useState<ConversationWorkspace | null>(null);
  const unknown =
    record.commandId &&
    (!receipt || !confirmed || receipt.status === 'admitting')
      ? record.commandId
      : null;
  const alive = useRef(true);
  const operation = useRef(false);
  const queryNumber = useRef(0);
  const continuation = useRef<AbortController | null>(null);
  const appliedEntry = useRef(false);
  const checkedEntry = useRef(false);
  function update(patch: Partial<SetupDraft>): boolean {
    try {
      setupSessions.update(scope, patch);
      return true;
    } catch {
      setError(
        'Setup recovery could not be saved in this tab. Allow session storage before submitting.',
      );
      return false;
    }
  }
  const setKind = (kind: SetupDraft['kind']) => update({ kind });
  const setMode = (mode: SetupDraft['mode']) => update({ mode });
  const setSelected = (selected: ResourceChoice | null) => update({ selected });
  const setTemplate = (template: string) => update({ template });
  const setCanvas = (canvas: string) => update({ canvas });
  const setName = (name: string) => update({ name });
  const setBrief = (brief: string) => update({ brief });
  const setGenerate = (generate: boolean) => update({ generate });
  useEffect(() => {
    if (!initialEntry || appliedEntry.current) return;
    appliedEntry.current = true;
    const current = setupSessions.read(scope);
    if (current.commandId || current.generationId) {
      setError(
        'Review the earlier setup receipt before starting another resource. Your new selection has not replaced it.',
      );
      return;
    }
    try {
      setupSessions.update(scope, {
        kind: initialEntry.kind,
        mode: initialEntry.mode,
        selected:
          initialEntry.mode === 'existing' &&
          initialEntry.resource?.kind === initialEntry.kind
            ? initialEntry.resource
            : null,
      });
    } catch {
      setError(
        'Setup recovery could not be saved in this tab. Allow session storage before submitting.',
      );
    }
  }, [initialEntry, scope]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    setFolder(null);
    setReview(null);
    setConfirmed(false);
  }, [client.handshake?.client_session_id, scope]);
  useEffect(() => {
    const abort = new AbortController();
    const current = setupSessions.read(scope);
    setConfirmed(!current.commandId);
    if (current.commandId) {
      void controller
        .receipt(current.commandId, abort.signal)
        .then((result) => {
          if (abort.signal.aborted) return;
          setupSessions.confirm(scope, current.commandId!, result);
          setConfirmed(true);
        })
        .catch(() => {
          if (!abort.signal.aborted)
            setError(
              'The earlier setup needs reconciliation. Check its receipt before trying again.',
            );
        });
    }
    if (current.generationId) {
      void controller
        .receipt(current.generationId, abort.signal)
        .then((result) => {
          if (!abort.signal.aborted)
            setupSessions.confirm(scope, current.generationId!, result, true);
        })
        .catch(() => {
          if (!abort.signal.aborted)
            setError(
              'First draft generation has an uncertain outcome. Check its receipt.',
            );
        });
    }
    return () => abort.abort();
  }, [controller, scope, client.handshake?.client_session_id]);
  useEffect(() => {
    const abort = new AbortController();
    const ticket = ++queryNumber.current;
    const load = async () => {
      try {
        if (kind === 'artifact' && mode === 'create') {
          const result = await controller.deckSetup(abort.signal);
          if (!abort.signal.aborted && ticket === queryNumber.current)
            setOptions(result);
        } else if (mode === 'existing') {
          const result = await controller.library(
            kind,
            undefined,
            abort.signal,
          );
          if (!abort.signal.aborted && ticket === queryNumber.current) {
            setLibrary(result);
            const entry = initialEntry?.resource;
            if (entry && !checkedEntry.current && kind === entry.kind) {
              checkedEntry.current = true;
              const fresh = result.items.find(
                (item) => item.resource_id === entry.resource_id,
              );
              const saved = setupSessions.read(scope);
              if (
                !saved.commandId &&
                !saved.generationId &&
                saved.selected?.resource_id === entry.resource_id
              ) {
                if (
                  !fresh ||
                  !fresh.available ||
                  fresh.revision !== entry.revision
                ) {
                  setupSessions.update(scope, { selected: null });
                  setError(
                    'This saved resource changed or is unavailable. Choose its current entry before opening it.',
                  );
                } else {
                  setupSessions.update(scope, { selected: fresh });
                }
              }
            }
          }
        }
      } catch (cause) {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      }
    };
    void load();
    return () => {
      abort.abort();
      continuation.current?.abort();
      continuation.current = null;
    };
  }, [controller, kind, mode, initialEntry, scope]);
  async function moreResources() {
    if (!library?.next_cursor || mode !== 'existing') return;
    continuation.current?.abort();
    const abort = new AbortController();
    continuation.current = abort;
    const ticket = queryNumber.current;
    const current = () =>
      alive.current &&
      !abort.signal.aborted &&
      ticket === queryNumber.current &&
      continuation.current === abort;
    try {
      const result = await controller.library(
        kind,
        library.next_cursor,
        abort.signal,
      );
      if (current()) setLibrary(result);
    } catch (cause) {
      if (current()) setError(clientError(cause).message);
    } finally {
      if (continuation.current === abort) continuation.current = null;
    }
  }
  function openConversation(target: string) {
    if (controller.getSnapshot().selectedConversationId !== target)
      void controller.selectConversation(target);
    navigate(`/conversations/${target}`);
  }
  function capturePresentation() {
    return {
      ...presentationContext.current,
      selection: controller.getSnapshot().selectedConversationId,
      version: controller.getSelectionVersion(),
    };
  }
  function present(
    result: CommandReceipt,
    initiating: ReturnType<typeof capturePresentation>,
  ) {
    if (
      result.status !== 'completed' ||
      !result.conversation_id ||
      !result.binding_id
    )
      return;
    const focused = controller.getSnapshot().selectedConversationId;
    if (
      !alive.current ||
      focused !== initiating.selection ||
      controller.getSelectionVersion() !== initiating.version ||
      presentationContext.current.route !== initiating.route ||
      presentationContext.current.instance !== initiating.instance ||
      presentationContext.current.session !== initiating.session ||
      (conversationId !== null && focused !== conversationId)
    ) {
      overlay.notify(
        'Resource setup completed. Open its conversation when ready.',
      );
      return;
    }
    if (!conversationId) openConversation(result.conversation_id);
    onPanel({
      panel_kind:
        result.resource_kind === 'artifact'
          ? 'artifact.preview'
          : 'workspace.inspector',
      resource_kind: result.resource_kind!,
      resource_ref: `${result.conversation_id}:${result.binding_id}`,
      resource_revision: result.resource_revision ?? '',
      title: (
        name ||
        selected?.name ||
        folder?.name ||
        (kind === 'artifact' ? 'Deck' : 'Coding workspace')
      ).slice(0, 160),
    });
  }
  async function reviewGeneration() {
    if (!receipt?.conversation_id || !confirmed || operation.current) return;
    setBusy(true);
    try {
      const value = await controller.workspaceFor(receipt.conversation_id);
      if (alive.current && value.conversation_id === receipt.conversation_id)
        setReview(value);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function firstDraft() {
    const current = setupSessions.read(scope);
    if (
      operation.current ||
      current.generationId ||
      !review ||
      !receipt?.conversation_id ||
      !confirmed ||
      !brief.trim()
    )
      return;
    const resource = review.resources.find(
      (item) => item.binding.binding_id === receipt.binding_id,
    );
    if (
      !resource ||
      !resource.available ||
      !review.controls.model_selection ||
      !review.actions.some(
        (action) => action.action === 'generate' && action.ready,
      )
    )
      return;
    operation.current = true;
    setBusy(true);
    setError('');
    const identity = crypto.randomUUID();
    try {
      setupSessions.reserve(scope, identity, true);
      const result = await controller.intent(
        receipt.conversation_id,
        'conversation.submit',
        {
          submission_id: crypto.randomUUID(),
          text: brief,
          attachment_refs: [],
          model_selection: review.controls.model_selection,
          write_targets: [
            {
              kind: 'artifact',
              binding_id: resource.binding.binding_id,
              resource_id: resource.binding.resource_id,
              binding_revision: resource.binding.revision,
              resource_revision: resource.resource_revision,
            },
          ],
        },
        review.revision,
        identity,
      );
      setupSessions.confirm(scope, identity, result, true);
    } catch (cause) {
      if (alive.current) setError(clientError(cause).message);
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function perform(
    action: 'default' | 'repair' | 'new_conversation' = 'default',
  ) {
    if (operation.current || setupSessions.read(scope).commandId) return;
    if (
      action === 'new_conversation' &&
      (conversationId ||
        kind !== 'workspace' ||
        mode !== 'existing' ||
        !selected?.available)
    )
      return;
    operation.current = true;
    setBusy(true);
    setError('');
    const commandId = crypto.randomUUID();
    const initiating = capturePresentation();
    try {
      const current = conversationId
        ? await controller.workspaceFor(conversationId)
        : null;
      const payload =
        mode === 'create'
          ? {
              kind,
              intent: 'create',
              ...(kind === 'artifact'
                ? {
                    deck: {
                      template_id: template,
                      aspect_ratio: canvas,
                      name,
                      brief,
                    },
                  }
                : { folder_grant: folder?.grant }),
            }
          : {
              kind,
              intent:
                action !== 'default' ? action : conversationId ? 'add' : 'open',
              resource_id: selected?.resource_id,
              expected_resource_revision: selected?.revision,
              ...(action === 'repair' && selected?.origin_conversation_id
                ? { expected_origin_id: selected.origin_conversation_id }
                : {}),
            };
      setupSessions.reserve(scope, commandId);
      const result = await controller.intent(
        conversationId,
        'resource.setup',
        payload,
        current?.revision ?? '0',
        commandId,
      );
      setupSessions.confirm(scope, commandId, result);
      if (alive.current) {
        setConfirmed(true);
        if (result.status === 'partial')
          setError(
            'The resource was retained, but setup is incomplete. Review the confirmed stages before continuing.',
          );
      }
      present(result, initiating);
    } catch (cause) {
      if (alive.current) setError(clientError(cause).message);
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function continueSetup() {
    if (!receipt?.resource_id || !confirmed || operation.current) return;
    operation.current = true;
    setBusy(true);
    setError('');
    const identity = crypto.randomUUID();
    const initiating = capturePresentation();
    try {
      const current = receipt.conversation_id
        ? await controller.workspaceFor(receipt.conversation_id)
        : null;
      const previous = await controller.receipt(record.commandId!);
      if (previous.status !== 'partial' && previous.status !== 'admitting') {
        setupSessions.confirm(scope, record.commandId!, previous);
        return;
      }
      setupSessions.update(scope, { commandId: identity });
      setConfirmed(false);
      const result = await controller.intent(
        previous.conversation_id ?? null,
        'resource.continue',
        {
          setup_command_id: previous.setup_command_id,
          expected_resource_revision: previous.resource_revision,
          ...(previous.setup_intent === 'repair' &&
          selected?.origin_conversation_id
            ? { expected_origin_id: selected.origin_conversation_id }
            : {}),
        },
        current?.revision ?? '0',
        identity,
      );
      setupSessions.confirm(scope, identity, result);
      if (alive.current) {
        setConfirmed(true);
        if (result.status !== 'completed')
          setError(
            'Setup is still incomplete. Review the current resource and confirmed stages.',
          );
      }
      present(result, initiating);
    } catch (cause) {
      if (alive.current) setError(clientError(cause).message);
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function recover(generation = false) {
    const value = setupSessions.read(scope),
      identity = generation ? value.generationId : value.commandId;
    if (!identity) return;
    setBusy(true);
    try {
      const result = await controller.receipt(identity);
      setupSessions.confirm(scope, identity, result, generation);
      if (alive.current) {
        if (!generation) setConfirmed(true);
        setError(`Request ${result.status}. Confirmed resources are retained.`);
      }
    } catch (cause) {
      if (alive.current) setError(clientError(cause).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  function reset() {
    try {
      setupSessions.reset(scope);
      setConfirmed(true);
      setReview(null);
      setFolder(null);
      setError('');
    } catch {
      setError(
        'Reconcile the pending setup and generation receipts before starting another resource.',
      );
    }
  }
  return (
    <div className="stack resource-setup">
      {receipt ? (
        <section className="stack" aria-label="Setup outcome">
          <p>
            {!confirmed
              ? 'Checking saved setup outcome…'
              : receipt.status === 'completed'
                ? 'Resource ready'
                : 'Setup partially completed'}
          </p>
          <ul>
            {receipt.confirmed_stages?.map((stage) => (
              <li key={stage}>{stage}</li>
            ))}
          </ul>
          <small>Resource {receipt.resource_id}</small>
          {receipt.status === 'partial' && (
            <Button
              disabled={busy || !confirmed}
              onClick={() => void continueSetup()}
            >
              Continue setup
            </Button>
          )}
          {receipt.conversation_id && (
            <Button
              disabled={!confirmed}
              onClick={() => {
                openConversation(receipt.conversation_id!);
                overlay.close();
              }}
            >
              Open conversation
            </Button>
          )}
          {confirmed && receipt.status === 'rejected' && (
            <Button
              onClick={() => {
                update({ commandId: null, receipt: null });
                setConfirmed(true);
              }}
            >
              Review setup inputs again
            </Button>
          )}
          {confirmed &&
            receipt.status === 'completed' &&
            generate &&
            brief.trim() &&
            receipt.resource_kind === 'artifact' && (
              <section className="stack" aria-label="First draft generation">
                <p>
                  The Deck is saved. Review the current controls before
                  submitting the first draft.
                </p>
                {!generationId && (
                  <Button
                    disabled={busy}
                    onClick={() => void reviewGeneration()}
                  >
                    Review generation controls
                  </Button>
                )}
                {review && !generationId && (
                  <>
                    <p>
                      Model:{' '}
                      {review.controls.model_selection?.model_ref ??
                        'Not configured'}
                    </p>
                    <p>
                      Profile:{' '}
                      {review.profiles.find(
                        (profile) => profile.id === review.controls.profile_id,
                      )?.label ??
                        (review.controls.profile_id || 'Default profile')}{' '}
                      · Runtime: {review.controls.runtime_mode} · Approval:{' '}
                      {review.controls.approval_mode}
                    </p>
                    <p>
                      Target:{' '}
                      {review.resources.find(
                        (resource) =>
                          resource.binding.binding_id === receipt.binding_id,
                      )?.title ?? 'Unavailable'}{' '}
                      · Binding {receipt.binding_id}
                    </p>
                    <Button
                      disabled={
                        busy ||
                        !review.controls.model_selection ||
                        !review.resources.some(
                          (resource) =>
                            resource.binding.binding_id ===
                              receipt.binding_id && resource.available,
                        ) ||
                        !review.actions.some(
                          (action) =>
                            action.action === 'generate' && action.ready,
                        )
                      }
                      variant="primary"
                      onClick={() => void firstDraft()}
                    >
                      Generate first draft
                    </Button>
                    <Button
                      onClick={() => {
                        navigate('/settings');
                        overlay.close();
                      }}
                    >
                      Open settings
                    </Button>
                  </>
                )}
                {generationId && (
                  <>
                    <p>
                      {generationReceipt
                        ? `Generation request ${generationReceipt.status}.`
                        : 'Generation outcome is awaiting confirmation.'}
                    </p>
                    <Button disabled={busy} onClick={() => void recover(true)}>
                      Check generation receipt
                    </Button>
                    {generationReceipt?.status === 'rejected' && (
                      <Button
                        onClick={() => {
                          update({
                            generationId: null,
                            generationReceipt: null,
                          });
                          setReview(null);
                        }}
                      >
                        Review generation again
                      </Button>
                    )}
                  </>
                )}
              </section>
            )}
          {confirmed &&
            receipt.status === 'completed' &&
            (!generationId ||
              (generationReceipt &&
                ['accepted', 'completed', 'rejected'].includes(
                  generationReceipt.status,
                ))) && (
              <Button disabled={busy} onClick={reset}>
                Start another resource
              </Button>
            )}
        </section>
      ) : record.commandId ? (
        <p role="status">
          An earlier setup request is pending reconciliation. Check its receipt
          before starting another resource.
        </p>
      ) : (
        <>
          <div className="setup-grid">
            <Field label="Resource type">
              <Select
                disabled={busy}
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value as typeof kind);
                  setSelected(null);
                  setLibrary(null);
                }}
              >
                <option value="artifact">Deck</option>
                <option value="workspace">Coding workspace</option>
              </Select>
            </Field>
            <Field label="Choose resource">
              <Select
                disabled={busy}
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value as typeof mode);
                  setSelected(null);
                }}
              >
                <option value="create">
                  {kind === 'artifact'
                    ? 'Create a Deck'
                    : 'Register an existing folder'}
                </option>
                <option value="existing">Open saved resource</option>
              </Select>
            </Field>
          </div>
          {mode === 'existing' ? (
            <div className="stack" aria-label="Saved resources">
              {library ? (
                <>
                  {library.items.map((item) => (
                    <Button
                      key={item.resource_id}
                      disabled={!item.available || busy}
                      aria-pressed={selected?.resource_id === item.resource_id}
                      onClick={() => setSelected(item)}
                    >
                      <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                        {item.name}
                        <small style={{ display: 'block' }}>
                          Resource ID: {item.resource_id}
                        </small>
                      </span>
                      {item.origin_status === 'repair_required'
                        ? ' · Original conversation missing'
                        : ''}
                      {!item.available ? ' · Unavailable in this client' : ''}
                    </Button>
                  ))}
                  {library.next_cursor && (
                    <Button onClick={() => void moreResources()}>
                      More saved resources
                    </Button>
                  )}
                </>
              ) : (
                <Skeleton label="Loading saved resources" />
              )}
            </div>
          ) : kind === 'artifact' ? (
            <>
              {options ? (
                <>
                  <div className="setup-grid">
                    <Field label="Template">
                      <Select
                        disabled={busy}
                        value={template}
                        onChange={(e) => setTemplate(e.target.value)}
                      >
                        {options.templates.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.label}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Canvas">
                      <Select
                        disabled={busy}
                        value={canvas}
                        onChange={(e) => setCanvas(e.target.value)}
                      >
                        {options.canvases.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.label}
                          </option>
                        ))}
                      </Select>
                    </Field>
                  </div>
                  <p className="muted">
                    {options.default_brand}. A blank Deck does not require a
                    model or brief.
                  </p>
                </>
              ) : (
                <Skeleton label="Loading Deck defaults" />
              )}
              <Field label="Name (optional)">
                <Input
                  disabled={busy}
                  value={name}
                  maxLength={120}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Untitled Deck"
                />
              </Field>
              <Field label="Brief (optional)">
                <textarea
                  className="input"
                  disabled={busy}
                  value={brief}
                  maxLength={16000}
                  onChange={(e) => setBrief(e.target.value)}
                />
              </Field>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={generate}
                  disabled={busy || !brief.trim()}
                  onChange={(e) => setGenerate(e.target.checked)}
                />
                Review first draft generation after creation, using this Deck as
                the write target
              </label>
            </>
          ) : (
            <div className="stack">
              <p>
                Choose an existing folder on this computer. Registration saves
                its name and location. Source files and Git state remain
                unchanged.
              </p>
              <p className="muted">
                Tools follow the conversation’s approval policy. The Inspector
                is read-only.
              </p>
              <Button
                disabled={busy}
                onClick={() => {
                  void controller
                    .pickFolder()
                    .then((result) => {
                      if (!alive.current) return;
                      if (result.status === 'selected' && result.grant_id)
                        setFolder({
                          grant: result.grant_id,
                          name: result.name ?? 'Selected folder',
                        });
                      else if (result.status === 'unavailable')
                        setError(
                          'Folder selection requires the local desktop window.',
                        );
                    })
                    .catch((e) => setError(clientError(e).message));
                }}
              >
                Choose existing folder
              </Button>
              {folder && <p>Selected: {folder.name}</p>}
            </div>
          )}
          {selected?.origin_status === 'repair_required' && !conversationId ? (
            <Button
              disabled={busy}
              onClick={() =>
                overlay.open({
                  kind: 'alert',
                  title: 'Repair original conversation?',
                  description:
                    'Create a new conversation for this resource and replace its missing origin association.',
                  confirmLabel: 'Repair and open',
                  onConfirm: () => {
                    overlay.close();
                    void perform('repair');
                  },
                })
              }
            >
              Repair missing origin
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={
                busy ||
                (mode === 'existing'
                  ? !selected
                  : kind === 'artifact'
                    ? !options
                    : !folder)
              }
              className="setup-submit"
              onClick={() => void perform()}
            >
              {busy
                ? 'Setting up…'
                : mode === 'existing'
                  ? conversationId
                    ? 'Add to this conversation'
                    : 'Open resource'
                  : kind === 'artifact'
                    ? generate
                      ? 'Create and review first draft'
                      : 'Create Deck'
                    : 'Register folder'}
            </Button>
          )}
          {!conversationId && kind === 'workspace' && mode === 'existing' && (
            <div className="stack">
              <p className="muted">
                Start a separate conversation. The workspace’s original
                conversation stays unchanged.
              </p>
              <Button
                disabled={busy || !selected?.available}
                onClick={() => void perform('new_conversation')}
              >
                New conversation with this workspace
              </Button>
            </div>
          )}
        </>
      )}
      {!record.commandId && (
        <Button disabled={busy} onClick={reset}>
          Discard setup inputs
        </Button>
      )}
      {error && <p role="alert">{error}</p>}
      {unknown && (
        <Button disabled={busy} onClick={() => void recover()}>
          Check setup receipt
        </Button>
      )}
      {busy && (
        <p role="status">
          Setup is in progress. Closing this dialog does not undo confirmed
          stages.
        </p>
      )}
    </div>
  );
}
