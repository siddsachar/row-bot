import {
  isCommand,
  isEvent,
  validateWire,
} from '../../../contracts/client-platform/v1/typescript/client';
import { aborted, clientError, failureStatus } from './errors';
import { Acknowledgements } from './acknowledgements';
import { isPanelDescriptor } from './types';
import type {
  ClientState,
  DictationScope,
  DictationHandle,
  DictationResult,
  ArtifactSetupOptions,
  ClientPanelSuggestion,
  ClientTransport,
  Command,
  CommandReceipt,
  EventRecord,
  Snapshot,
  SubscriptionView,
  TranscriptPage,
} from './types';

const INITIAL: ClientState = {
  status: 'loading',
  error: null,
  connection: 'none',
  handshake: null,
  conversations: [],
  conversationGroup: 'all',
  hasMoreConversations: false,
  loadingConversations: false,
  conversationListError: null,
  selectedConversationId: null,
  conversation: null,
  projection: null,
  workspace: null,
  activity: [],
  history: null,
  historyFocus: null,
  search: null,
  searching: false,
  draftStatus: 'saved',
  hasMoreTranscript: false,
  loadingConversation: false,
  suggestions: [],
  revision: 0,
};
const DELAYS = [1000, 2000, 4000, 8000, 15000, 30000];

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const cancel = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', cancel);
      resolve();
    }, ms);
    signal.addEventListener('abort', cancel, { once: true });
  });
}

function intentVerifier(
  target: string | null,
  command: Command,
): Promise<string> {
  const canonical = JSON.stringify(
    [target, command],
    (_key, value: unknown) => {
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        return Object.fromEntries(
          Object.entries(value).sort(([a], [b]) => a.localeCompare(b)),
        );
      }
      return value;
    },
  );
  return crypto.subtle
    .digest('SHA-256', new TextEncoder().encode(canonical))
    .then((buffer) =>
      [...new Uint8Array(buffer)]
        .map((value) => value.toString(16).padStart(2, '0'))
        .join(''),
    );
}

/** One authenticated connection owner. Presentation stores never receive session proofs. */
export class ClientController {
  private state: ClientState = { ...INITIAL };
  private listeners = new Set<() => void>();
  private lifetime = new AbortController();
  private selection = new AbortController();
  private observer: AbortController | null = null;
  private activeSubscription: string | null = null;
  private startPromise: Promise<void> | null = null;
  private reconnectPromise: Promise<void> | null = null;
  private authenticationNumber = 0;
  private visible = true;
  private online = true;
  private disposed = false;
  private retiredSubscriptions = new Set<string>();
  private selectionNumber = 0;
  private dictationStartNumber = 0;
  private dictationLease: string | null = null;
  private appliedDictation: string | null = null;
  private conversationCursor: string | undefined;
  private conversationListNumber = 0;
  private transcriptCursor: string | undefined;
  private transcriptRequest = false;
  private searchNumber = 0;
  private historyNumber = 0;
  private drafts = new Map<
    string,
    { text: string; attachments: import('./types').AttachmentView[] }
  >();
  private draftRevisions = new Map<string, string>();
  private draftWrites = new Set<string>();
  private dirtyDrafts = new Set<string>();
  private browserCommandAttempts = new Set<string>();
  private draftStates = new Map<string, ClientState['draftStatus']>();
  private seen = new Set<string>();
  private sequences = new Map<string, bigint>();
  private commandClaims = new Map<
    string,
    {
      verifier: Promise<string>;
      result: Promise<CommandReceipt>;
      failed: boolean;
      settled: boolean;
    }
  >();
  readonly metrics = {
    appliedEvents: 0,
    duplicateEvents: 0,
    resets: 0,
    reconnects: 0,
    polls: 0,
    acknowledgements: 0,
    notifications: 0,
    maxBatch: 0,
  };

  constructor(
    private readonly transport: ClientTransport,
    private readonly random: () => number = Math.random,
  ) {}
  getSnapshot = (): ClientState => this.state;
  getSelectionVersion = (): number => this.selectionNumber;
  dictationScope(): DictationScope | null {
    const { handshake, selectedConversationId, loadingConversation, status } =
      this.state;
    if (
      !handshake ||
      !selectedConversationId ||
      loadingConversation ||
      status !== 'ready' ||
      this.disposed
    )
      return null;
    return {
      conversationId: selectedConversationId,
      clientSessionId: handshake.client_session_id,
      serverEpoch: handshake.server_epoch,
      selectionKey: String(this.selectionNumber),
    };
  }
  private sameDictationScope(scope: DictationScope): boolean {
    const current = this.dictationScope();
    return Boolean(
      current &&
      Object.keys(current).every(
        (key) =>
          current[key as keyof DictationScope] ===
          scope[key as keyof DictationScope],
      ),
    );
  }
  async dictationCapability(signal?: AbortSignal) {
    return (
      this.transport.dictationCapability?.(signal) ?? {
        schema_version: 1 as const,
        browser_dictation_available: false,
        native_capture_available: false as const,
        reason: 'host_unavailable' as const,
      }
    );
  }
  private voiceHandle(
    scope: DictationScope,
    handle: DictationHandle,
    cleanup = false,
  ) {
    const h = this.state.handshake;
    if (
      !h ||
      h.client_session_id !== scope.clientSessionId ||
      h.server_epoch !== scope.serverEpoch ||
      handle.conversation_id !== scope.conversationId ||
      handle.server_epoch !== scope.serverEpoch ||
      (!cleanup && !this.sameDictationScope(scope))
    )
      throw { code: 'voice_session_expired' };
  }
  async startTalk(
    scope: DictationScope,
    body: import('./types').TalkStart,
    signal: AbortSignal,
  ) {
    if (!this.sameDictationScope(scope) || !this.transport.startTalk)
      throw { code: 'voice_session_expired' };
    const result = await this.transport.startTalk(
      scope.conversationId,
      body,
      signal,
    );
    validateWire('TalkSnapshot', result);
    if (
      result.handle.conversation_id !== scope.conversationId ||
      result.handle.server_epoch !== scope.serverEpoch
    )
      throw { code: 'protocol_incompatible' };
    return result;
  }
  async startRealtime(
    scope: DictationScope,
    body: import('./types').TalkStart,
    signal: AbortSignal,
  ) {
    if (!this.sameDictationScope(scope) || !this.transport.startRealtime)
      throw { code: 'voice_session_expired' };
    const result = await this.transport.startRealtime(
      scope.conversationId,
      body,
      signal,
    );
    validateWire('RealtimeStart', result);
    if (
      result.snapshot.handle.conversation_id !== scope.conversationId ||
      result.snapshot.handle.server_epoch !== scope.serverEpoch
    )
      throw { code: 'protocol_incompatible' };
    return result;
  }
  async voiceControl<M extends 'talk' | 'realtime'>(
    scope: DictationScope,
    mode: M,
    handle: DictationHandle,
    action: 'stop' | 'heartbeat',
    signal?: AbortSignal,
  ) {
    this.voiceHandle(scope, handle, action === 'stop');
    if (!this.transport.voiceControl) throw { code: 'capability_unavailable' };
    const result = await this.transport.voiceControl(
      mode,
      handle,
      action,
      signal,
    );
    validateWire(mode === 'talk' ? 'TalkSnapshot' : 'RealtimeSnapshot', result);
    if (
      Object.keys(handle).some(
        (key) =>
          result.handle[key as keyof DictationHandle] !==
          handle[key as keyof DictationHandle],
      )
    )
      throw { code: 'protocol_incompatible' };
    return result;
  }
  async transcribeTalk(
    scope: DictationScope,
    handle: DictationHandle,
    utterance: string,
    audio: Blob,
    signal: AbortSignal,
  ) {
    this.voiceHandle(scope, handle);
    if (!this.transport.transcribeTalk)
      throw { code: 'capability_unavailable' };
    const result = await this.transport.transcribeTalk(
      handle,
      utterance,
      audio,
      signal,
    );
    validateWire('TalkResult', result);
    if (
      result.utterance_id !== utterance ||
      Object.keys(handle).some(
        (key) =>
          result.snapshot.handle[key as keyof DictationHandle] !==
          handle[key as keyof DictationHandle],
      )
    )
      throw { code: 'protocol_incompatible' };
    return result;
  }
  async talkOutput(
    scope: DictationScope,
    handle: DictationHandle,
    run: string,
    output: string,
    signal: AbortSignal,
  ) {
    this.voiceHandle(scope, handle);
    if (!this.transport.talkOutput) throw { code: 'capability_unavailable' };
    const result = await this.transport.talkOutput(handle, run, output, signal);
    this.voiceHandle(scope, handle);
    return result;
  }
  async realtimeEvent(
    scope: DictationScope,
    handle: DictationHandle,
    event: import('./voice_realtime').RealtimeEvent,
    signal: AbortSignal,
  ) {
    this.voiceHandle(scope, handle);
    if (!this.transport.realtimeEvent) throw { code: 'capability_unavailable' };
    const input = validateWire<import('./types').RealtimeEvent>(
      'RealtimeEvent',
      event,
    );
    const result = await this.transport.realtimeEvent(handle, input, signal);
    validateWire('RealtimeEventResult', result);
    if (
      result.event_id !== event.event_id ||
      Object.keys(handle).some(
        (key) =>
          result.snapshot.handle[key as keyof DictationHandle] !==
          handle[key as keyof DictationHandle],
      )
    )
      throw { code: 'protocol_incompatible' };
    return result;
  }
  async realtimeExchange(
    scope: DictationScope,
    handle: DictationHandle,
    sdp: string,
    signal: AbortSignal,
  ) {
    this.voiceHandle(scope, handle);
    if (!this.transport.realtimeExchange)
      throw { code: 'capability_unavailable' };
    const result = await this.transport.realtimeExchange(handle, sdp, signal);
    this.voiceHandle(scope, handle);
    return result;
  }
  async voiceRun(
    scope: DictationScope,
    mode: 'talk' | 'realtime',
    handle: DictationHandle,
    signal: AbortSignal,
  ) {
    this.voiceHandle(scope, handle);
    if (!this.transport.voiceRun) throw { code: 'capability_unavailable' };
    const result = await this.transport.voiceRun(mode, handle, signal);
    this.voiceHandle(scope, handle);
    validateWire('VoiceRunView', result);
    if (
      Object.keys(handle).some(
        (key) =>
          result.handle[key as keyof DictationHandle] !==
          handle[key as keyof DictationHandle],
      )
    )
      throw { code: 'protocol_incompatible' };
    return result;
  }
  async startDictation(
    scope: DictationScope,
    requestId: string,
    signal: AbortSignal,
  ) {
    if (!this.sameDictationScope(scope) || !this.transport.startDictation)
      throw { code: 'voice_session_expired' };
    const ticket = ++this.dictationStartNumber;
    const result = await this.transport.startDictation(
      scope.conversationId,
      requestId,
      signal,
    );
    validateWire('DictationSnapshot', result);
    if (
      this.sameDictationScope(scope) &&
      ticket === this.dictationStartNumber &&
      !signal.aborted
    ) {
      if (
        result.handle.conversation_id !== scope.conversationId ||
        result.handle.server_epoch !== scope.serverEpoch
      )
        throw { code: 'protocol_incompatible' };
      if (this.dictationLease !== result.handle.lease_id) {
        this.dictationLease = result.handle.lease_id;
        this.appliedDictation = null;
      }
    }
    // Return a late handle so the captured component can stop that exact lease.
    return result;
  }
  async transcribeDictation(
    scope: DictationScope,
    handle: DictationHandle,
    utterance: string,
    audio: Blob,
    signal: AbortSignal,
  ) {
    if (
      !this.sameDictationScope(scope) ||
      handle.conversation_id !== scope.conversationId ||
      handle.server_epoch !== scope.serverEpoch ||
      !this.transport.transcribeDictation
    )
      throw { code: 'voice_session_expired' };
    const result = await this.transport.transcribeDictation(
      handle,
      utterance,
      audio,
      signal,
    );
    validateWire('DictationResult', result);
    if (
      result.utterance_id !== utterance ||
      (Object.keys(handle) as Array<keyof DictationHandle>).some(
        (key) => result.snapshot.handle[key] !== handle[key],
      )
    )
      throw { code: 'protocol_incompatible' };
    return result;
  }
  async stopDictation(
    scope: DictationScope,
    handle: DictationHandle,
    signal?: AbortSignal,
  ) {
    const current = this.state.handshake;
    if (
      !current ||
      current.client_session_id !== scope.clientSessionId ||
      current.server_epoch !== scope.serverEpoch ||
      handle.conversation_id !== scope.conversationId ||
      handle.server_epoch !== scope.serverEpoch ||
      !this.transport.stopDictation
    )
      throw { code: 'voice_session_expired' };
    const result = await this.transport.stopDictation(handle, signal);
    validateWire('DictationSnapshot', result);
    if (
      (Object.keys(handle) as Array<keyof DictationHandle>).some(
        (key) => result.handle[key] !== handle[key],
      )
    )
      throw { code: 'protocol_incompatible' };
    return result;
  }
  applyDictation(
    scope: DictationScope,
    result: DictationResult,
  ): 'applied' | 'stale' | 'draft_full' {
    if (result.snapshot.state !== 'completed' || !result.snapshot.quiesced)
      return 'stale';
    if (
      !this.sameDictationScope(scope) ||
      result.snapshot.handle.lease_id !== this.dictationLease ||
      result.snapshot.handle.conversation_id !== scope.conversationId ||
      result.snapshot.handle.server_epoch !== scope.serverEpoch
    )
      return 'stale';
    const identity = `${result.snapshot.handle.lease_id}:${result.utterance_id}`;
    if (identity === this.appliedDictation) return 'applied';
    const draft = this.getDraft(scope.conversationId);
    const addition = result.text.trim();
    const text = addition
      ? `${draft.text}${draft.text && !/\s$/.test(draft.text) ? ' ' : ''}${addition}`
      : draft.text;
    if (text.length > 200000) return 'draft_full';
    this.appliedDictation = identity;
    if (addition) this.setDraft(scope.conversationId, { ...draft, text });
    return 'applied';
  }
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private update(patch: Partial<ClientState>): void {
    if (this.disposed) return;
    if (
      patch.projection &&
      (patch.projection.rows.length > 200 ||
        new TextEncoder().encode(JSON.stringify(patch.projection)).byteLength >
          2 * 1024 * 1024)
    ) {
      throw new Error('protocol_incompatible');
    }
    this.state = { ...this.state, ...patch, revision: this.state.revision + 1 };
    this.metrics.notifications += 1;
    this.listeners.forEach((listener) => listener());
  }
  private failed(error: unknown): void {
    if (aborted(error) || this.disposed) return;
    const safe = clientError(error);
    const status = failureStatus(safe);
    if (status === 'unauthorized' || status === 'incompatible') {
      this.authenticationNumber += 1;
      this.selectionNumber += 1;
      this.lifetime.abort();
      this.lifetime = new AbortController();
      this.selection.abort();
      this.stopObservation();
      this.transport.clearSession();
      this.drafts.clear();
      this.draftRevisions.clear();
      this.browserCommandAttempts.clear();
      this.update({
        handshake: null,
        conversations: [],
        conversationListError: null,
        conversation: null,
        projection: null,
        workspace: null,
        activity: [],
        history: null,
        search: null,
        selectedConversationId: null,
        suggestions: [],
        hasMoreConversations: false,
        hasMoreTranscript: false,
      });
    }
    this.update({
      status,
      error: safe,
      connection: 'none',
      loadingConversation: false,
      loadingConversations: false,
    });
  }
  start(): Promise<void> {
    if (this.disposed || !this.online) return Promise.resolve();
    if (this.startPromise) return this.startPromise;
    this.startPromise = this.bootstrap();
    return this.startPromise;
  }
  private async bootstrap(): Promise<void> {
    const authentication = ++this.authenticationNumber;
    const signal = this.lifetime.signal;
    try {
      this.update({
        status: 'loading',
        error: null,
        handshake: null,
        loadingConversations: false,
      });
      const result = validateWire<import('./types').HandshakeView>(
        'HandshakeView',
        await this.transport.connect(signal),
      );
      if (this.disposed || authentication !== this.authenticationNumber) return;
      const handshake: NonNullable<ClientState['handshake']> = {
        models: result.models,
        capabilities: result.capabilities,
        catalog_stale: result.catalog_stale,
        protocol_version: result.protocol_version,
        minimum_client_version: result.minimum_client_version,
        instance_id: result.instance_id,
        server_epoch: result.server_epoch,
        client_session_id: result.client_session_id,
        client_group_id: result.client_group_id,
        authentication_kind: result.authentication_kind,
        client_compatibility: result.client_compatibility ?? 'unknown',
        application_capabilities: result.application_capabilities ?? [],
        presentation_capabilities: result.presentation_capabilities ?? [],
        policy_revision: result.policy_revision,
        session_ttl_seconds: result.session_ttl_seconds,
        native_adapter: result.native_adapter,
        limits: result.limits,
      };
      // Publishing readiness can synchronously start route selection. Resume a
      // pre-handshake intent only if no authenticated selection started since.
      const selection = this.selectionNumber;
      this.update({ handshake, status: 'ready' });
      await this.drainRetiredSubscriptions(authentication, signal);
      if (this.disposed || authentication !== this.authenticationNumber) return;
      // Library and selected history are independent authenticated reads. A
      // slow sidebar refresh must not delay recovery of the active conversation.
      const library = this.loadMoreConversations(true);
      let opening: Promise<void> | undefined;
      if (
        authentication === this.authenticationNumber &&
        selection === this.selectionNumber &&
        this.state.handshake &&
        this.state.selectedConversationId
      ) {
        opening = this.selectConversation(this.state.selectedConversationId);
      }
      await Promise.all([library, opening]);
    } catch (error) {
      if (authentication === this.authenticationNumber) this.failed(error);
    }
  }

  async loadMoreConversations(reset = false): Promise<void> {
    const authentication = this.authenticationNumber;
    if (
      !this.online ||
      this.disposed ||
      !this.state.handshake ||
      (!reset && this.state.loadingConversations) ||
      (!reset && !this.state.hasMoreConversations)
    )
      return;
    const ticket = ++this.conversationListNumber;
    this.update({ loadingConversations: true, conversationListError: null });
    try {
      const page = validateWire<import('./types').ConversationPage>(
        'ConversationPage',
        await this.transport.listConversations(
          reset ? undefined : this.conversationCursor,
          this.lifetime.signal,
          this.state.conversationGroup,
        ),
      );
      if (
        this.disposed ||
        authentication !== this.authenticationNumber ||
        ticket !== this.conversationListNumber
      )
        return;
      if (
        page.has_more &&
        (!page.next_cursor ||
          (!reset && page.next_cursor === this.conversationCursor))
      )
        throw new Error('protocol_incompatible');
      this.conversationCursor = page.next_cursor ?? undefined;
      const rows = new Map(
        (reset ? [] : this.state.conversations).map((row) => [row.id, row]),
      );
      page.items.forEach((row) => rows.set(row.id, row));
      this.update({
        conversations: [...rows.values()].slice(-1000),
        hasMoreConversations: page.has_more,
        loadingConversations: false,
      });
    } catch (error) {
      if (
        authentication === this.authenticationNumber &&
        ticket === this.conversationListNumber
      ) {
        if (aborted(error) || this.disposed) return;
        const safe = clientError(error);
        if (safe.recovery === 'authenticate' || safe.recovery === 'update')
          this.failed(error);
        else
          this.update({
            conversationListError: safe,
            loadingConversations: false,
          });
      }
    }
  }
  async setConversationGroup(
    group: ClientState['conversationGroup'],
  ): Promise<void> {
    if (this.state.conversationGroup === group) return;
    this.conversationCursor = undefined;
    this.update({
      conversationGroup: group,
      conversations: [],
      hasMoreConversations: true,
    });
    await this.loadMoreConversations(true);
  }

  async selectConversation(id: string): Promise<void> {
    if (this.disposed) return;
    if (!this.online) {
      if (id !== this.state.selectedConversationId) {
        this.transcriptCursor = undefined;
        this.update({
          selectedConversationId: id,
          conversation: null,
          projection: null,
          hasMoreTranscript: false,
          loadingConversation: false,
        });
      }
      return;
    }
    if (!this.state.handshake && this.state.status !== 'loading') return;
    const ticket = ++this.selectionNumber;
    this.historyNumber += 1;
    this.selection.abort();
    this.selection = new AbortController();
    this.stopObservation();
    this.transcriptCursor = undefined;
    this.transcriptRequest = false;
    this.update({
      selectedConversationId: id,
      conversation: null,
      projection: null,
      workspace: null,
      activity: [],
      history: null,
      historyFocus: null,
      loadingConversation: true,
      hasMoreTranscript: false,
      connection: 'none',
    });
    // Selection is local intent even while an authenticated bootstrap is pending.
    if (!this.state.handshake) return;
    try {
      const opened = this.transport.openConversation
        ? validateWire<import('./types').ConversationOpenView>(
            'ConversationOpenView',
            await this.transport.openConversation(id, this.selection.signal),
          )
        : null;
      const [conversation, page, workspace, draft] = opened
        ? [opened.conversation, opened.history, opened.workspace, opened.draft]
        : await Promise.all([
            this.transport.getConversation(id, this.selection.signal),
            this.transport.history
              ? this.transport.history(
                  id,
                  undefined,
                  undefined,
                  this.selection.signal,
                )
              : this.transport.getTranscript(
                  id,
                  undefined,
                  this.selection.signal,
                ),
            this.transport.workspace?.(id, this.selection.signal) ??
              Promise.resolve(null),
            this.transport.draft?.(id, this.selection.signal) ??
              Promise.resolve(null),
          ]);
      if (ticket !== this.selectionNumber || this.disposed) return;
      validateWire('ConversationView', conversation);
      validateWire('TranscriptPage', page);
      if (
        conversation.id !== id ||
        page.conversation_id !== id ||
        (draft && draft.conversation_id !== id) ||
        (workspace && workspace.conversation_id !== id)
      )
        throw new Error('protocol_incompatible');
      if (draft && !this.dirtyDrafts.has(id)) {
        this.drafts.set(id, {
          text: draft.text,
          attachments: draft.attachments,
        });
        this.draftRevisions.set(id, draft.revision);
        this.draftStates.set(id, 'saved');
      } else if (draft && !this.draftRevisions.has(id)) {
        this.draftRevisions.set(id, draft.revision);
        // Typing while the initial read is pending must not strand an unsaved
        // draft. A pre-existing saved draft needs review before replacement.
        if (draft.text || draft.attachments.length) {
          this.draftStates.set(id, 'conflict');
        } else {
          void this.saveDraft(id);
        }
      }
      this.trimCleanDrafts();
      this.transcriptCursor = page.next_cursor ?? undefined;
      this.update({
        conversation,
        workspace,
        draftStatus: this.draftStates.get(id) ?? 'saved',
        projection: this.pageSnapshot(page),
        hasMoreTranscript: page.has_more,
        loadingConversation: false,
        status: 'ready',
        error: null,
      });
      if (this.visible) this.beginObservation(id, ticket, true);
    } catch (error) {
      if (ticket === this.selectionNumber) this.failed(error);
    }
  }
  private pageSnapshot(page: TranscriptPage): Snapshot {
    return {
      conversation_id: page.conversation_id,
      server_epoch: page.server_epoch,
      projection_revision: page.projection_revision,
      cursor: page.cursor,
      checkpoint_revision: page.checkpoint_revision,
      rows: page.rows,
      generation: page.generation,
    };
  }
  async loadMoreTranscript(): Promise<void> {
    const id = this.state.selectedConversationId;
    if (
      !this.online ||
      this.disposed ||
      !this.state.handshake ||
      !id ||
      !this.transcriptCursor ||
      this.transcriptRequest ||
      !this.state.hasMoreTranscript
    )
      return;
    const ticket = this.selectionNumber;
    const cursor = this.transcriptCursor;
    this.transcriptRequest = true;
    try {
      const page = await this.transport.getTranscript(
        id,
        cursor,
        this.selection.signal,
      );
      if (ticket !== this.selectionNumber || this.disposed) return;
      const current = this.state.projection;
      if (
        !current ||
        page.conversation_id !== id ||
        page.checkpoint_revision !== current.checkpoint_revision
      ) {
        await this.selectConversation(id);
        return;
      }
      if (page.has_more && (!page.next_cursor || page.next_cursor === cursor))
        throw new Error('protocol_incompatible');
      const rows = new Map(current.rows.map((row) => [row.id, row]));
      page.rows.forEach((row) => rows.set(row.id, row));
      this.transcriptCursor = page.next_cursor ?? undefined;
      this.update({
        projection: { ...current, rows: [...rows.values()].slice(-200) },
        hasMoreTranscript: page.has_more,
      });
    } catch (error) {
      if (ticket === this.selectionNumber) this.failed(error);
    } finally {
      if (ticket === this.selectionNumber) this.transcriptRequest = false;
    }
  }

  private stopObservation(terminal = false): void {
    this.observer?.abort();
    this.observer = null;
    const subscription = this.activeSubscription;
    this.activeSubscription = null;
    if (subscription) void this.retireSubscription(subscription, terminal);
  }
  private queueRetiredSubscription(subscription: string): void {
    if (this.disposed) return;
    if (
      this.retiredSubscriptions.size >= 256 &&
      !this.retiredSubscriptions.has(subscription)
    ) {
      this.failed(new Error('protocol_incompatible'));
      return;
    }
    this.retiredSubscriptions.add(subscription);
  }
  private async retireSubscription(
    subscription: string,
    terminal = false,
  ): Promise<void> {
    if (!this.online || !this.state.handshake) {
      this.queueRetiredSubscription(subscription);
      return;
    }
    const authentication = this.authenticationNumber;
    try {
      await this.transport.unsubscribe(
        subscription,
        terminal ? undefined : this.lifetime.signal,
        terminal,
      );
      this.retiredSubscriptions.delete(subscription);
    } catch (error) {
      if (clientError(error).code === 'not_found') {
        this.retiredSubscriptions.delete(subscription);
      } else if (
        authentication !== this.authenticationNumber &&
        this.online &&
        this.state.handshake &&
        !this.disposed
      ) {
        await this.retireSubscription(subscription);
      } else this.queueRetiredSubscription(subscription);
    }
  }
  private async drainRetiredSubscriptions(
    authentication: number,
    signal: AbortSignal,
  ): Promise<void> {
    for (const subscription of [...this.retiredSubscriptions]) {
      signal.throwIfAborted();
      if (authentication !== this.authenticationNumber || !this.online) return;
      try {
        await this.transport.unsubscribe(subscription, signal);
      } catch (error) {
        if (clientError(error).code !== 'not_found') throw error;
      }
      signal.throwIfAborted();
      if (authentication !== this.authenticationNumber || !this.online) return;
      this.retiredSubscriptions.delete(subscription);
    }
  }
  private beginObservation(
    id: string,
    ticket: number,
    freshlyOpened = false,
  ): void {
    if (!this.online || this.disposed || !this.state.handshake) return;
    this.stopObservation();
    const abort = new AbortController();
    this.observer = abort;
    void this.observe(id, ticket, abort.signal, freshlyOpened);
  }
  private install(snapshot: Snapshot, cursor: string, refresh = true): void {
    if (
      snapshot.conversation_id !== this.state.selectedConversationId ||
      snapshot.cursor !== cursor
    )
      throw new Error('protocol_incompatible');
    this.seen.clear();
    this.sequences.clear();
    this.metrics.resets += 1;
    // The snapshot and its cursor are one immutable cut, installed in one notification.
    this.update({ projection: snapshot, status: 'ready', error: null });
    if (refresh) void this.refreshWorkspace();
  }
  private apply(record: EventRecord): 'applied' | 'duplicate' | 'reset' {
    const event = record.event;
    const current = this.state.projection;
    if (
      !isEvent(event) ||
      !current ||
      event.conversation_id !== current.conversation_id
    )
      throw new Error('protocol_incompatible');
    if (
      event.server_epoch !== current.server_epoch ||
      event.type === 'projection.reset'
    )
      return 'reset';
    if (
      this.seen.has(event.event_id) ||
      BigInt(event.projection_revision) <= BigInt(current.projection_revision)
    ) {
      this.metrics.duplicateEvents += 1;
      return 'duplicate';
    }
    const key = `${event.source ?? 'runtime'}:${event.source_stream_id}:${event.source_epoch}`;
    const previous = this.sequences.get(key);
    const start = BigInt(event.source_sequence_start),
      end = BigInt(event.source_sequence_end);
    if (previous !== undefined && start !== previous + 1n) return 'reset';
    if (previous === undefined && this.sequences.size >= 256) return 'reset';
    if (
      BigInt(event.projection_revision) !==
      BigInt(current.projection_revision) + 1n
    )
      return 'reset';
    // Exact checkpoint adoption and resource replacement remain server-owned.
    if (
      event.type === 'transcript.checkpoint' ||
      event.type === 'transcript.settled' ||
      event.type === 'resource.changed'
    )
      return 'reset';
    this.sequences.set(key, end);
    this.seen.add(event.event_id);
    if (this.seen.size > 4096)
      this.seen.delete(this.seen.values().next().value!);
    const next: Snapshot = {
      ...current,
      projection_revision: event.projection_revision,
      cursor: record.cursor,
    };
    if (event.type === 'generation.state') next.generation = event.payload;
    if (event.type === 'transcript.delta') {
      const delta = event.payload;
      const index = current.rows.findIndex((row) => row.id === delta.row_id);
      const row =
        index < 0
          ? { id: delta.row_id, role: 'assistant' as const, blocks: [] }
          : current.rows[index];
      const text =
        row.blocks.map((block) => block.text).join('') +
        delta.public_text_delta;
      if (text.length > 262144 || (index < 0 && current.rows.length >= 200))
        return 'reset';
      const changed = {
        ...row,
        render_revision: delta.render_revision,
        blocks: [{ type: 'text' as const, text }],
      };
      next.rows =
        index < 0
          ? [...current.rows, changed]
          : current.rows.map((value, i) => (i === index ? changed : value));
    }
    this.metrics.appliedEvents += 1;
    this.metrics.maxBatch = Math.max(this.metrics.maxBatch, 1);
    const activity = [
      'tool.activity',
      'agent.activity',
      'queue.updated',
      'queue.changed',
      'media.available',
      'media.error',
      'approval.required',
      'generation.error',
      'steering.queued',
      'steering.consumed',
    ].includes(event.type)
      ? [...this.state.activity, record].slice(-200)
      : this.state.activity;
    this.update({ projection: next, activity });
    if (
      event.type === 'tool.activity' &&
      [
        'browser_navigate',
        'browser_click',
        'browser_type',
        'browser_scroll',
        'browser_snapshot',
        'browser_back',
        'browser_tab',
      ].includes(event.payload.tool_name ?? '') &&
      this.state.conversation?.id === event.conversation_id
    )
      this.suggestPanel({
        type: 'panel.suggested',
        conversation_id: event.conversation_id,
        conversation_revision: this.state.conversation.revision,
        descriptor: {
          panel_kind: 'browser.live',
          title: 'Managed browser',
          required_capabilities: ['browser_navigate'],
        },
      });
    if (event.type === 'generation.state' && event.payload.quiesced)
      void this.refreshWorkspace();
    return 'applied';
  }
  private async observe(
    id: string,
    ticket: number,
    signal: AbortSignal,
    freshlyOpened = false,
  ): Promise<void> {
    let subscription: SubscriptionView | null = null;
    let firstSubscription = freshlyOpened;
    let cursor = '';
    let failures = 0;
    let streamFailures = 0;
    let resetsWithoutProgress = 0;
    let idle = 2000;
    let acknowledgements: Acknowledgements | null = null;
    const retireObserved = async (subscriptionId: string) => {
      const previous = acknowledgements;
      acknowledgements = null;
      // Cancel trailing cuts and drain the issued ACK within its bounded grace
      // before retiring. A cancelled observer aborts that ACK immediately.
      await previous?.close();
      await this.retireSubscription(subscriptionId);
    };
    const alive = () =>
      !signal.aborted && !this.disposed && ticket === this.selectionNumber;
    try {
      while (alive()) {
        try {
          if (!subscription) {
            subscription = validateWire<SubscriptionView>(
              'SubscriptionView',
              await this.transport.subscribe(id, signal),
            );
            if (!alive()) {
              await this.retireSubscription(subscription.subscription_id);
              return;
            }
            this.activeSubscription = subscription.subscription_id;
            acknowledgements?.close();
            const subscriptionId = subscription.subscription_id;
            acknowledgements = new Acknowledgements(
              async (cut, acknowledgementSignal) => {
                if (!alive()) return;
                await this.transport.acknowledge(
                  subscriptionId,
                  cut,
                  acknowledgementSignal,
                );
                if (alive() && !acknowledgementSignal.aborted)
                  this.metrics.acknowledgements += 1;
              },
              (error) => {
                if (alive()) this.failed(error);
              },
              undefined,
              signal,
            );
            cursor = subscription.cursor;
            const openedCut = this.state.projection;
            const unchangedOpen =
              firstSubscription &&
              openedCut &&
              openedCut.server_epoch === subscription.snapshot.server_epoch &&
              openedCut.projection_revision ===
                subscription.snapshot.projection_revision;
            this.install(subscription.snapshot, cursor, !unchangedOpen);
            firstSubscription = false;
            await this.transport.acknowledge(
              subscription.subscription_id,
              cursor,
              signal,
            );
            if (!alive()) return;
            this.metrics.acknowledgements += 1;
          }
          if (streamFailures < 2) {
            this.update({ connection: 'sse', status: 'ready', error: null });
            for await (const record of this.transport.observe(
              subscription.subscription_id,
              cursor,
              signal,
            )) {
              if (!alive()) return;
              const disposition =
                'event' in record ? this.apply(record) : 'reset';
              if (disposition === 'reset') {
                if (++resetsWithoutProgress > 3)
                  throw new Error('protocol_incompatible');
                const previous = subscription.subscription_id;
                subscription = null;
                if (this.activeSubscription === previous)
                  this.activeSubscription = null;
                await retireObserved(previous);
                break;
              }
              if (disposition === 'applied') resetsWithoutProgress = 0;
              // Never move a cursor backwards on duplicate/reordered delivery.
              cursor = this.state.projection!.cursor;
              acknowledgements?.offer(cursor);
              failures = 0;
            }
            if (!alive()) return;
            if (subscription) throw new TypeError('stream disconnected');
          } else {
            this.update({ connection: 'poll', status: 'ready', error: null });
            const page = validateWire<import('./types').EventPage>(
              'EventPage',
              await this.transport.poll(
                subscription.subscription_id,
                cursor,
                signal,
              ),
            );
            if (!alive()) return;
            this.metrics.polls += 1;
            if (page.snapshot_required) {
              if (!page.snapshot) {
                if (++resetsWithoutProgress > 3)
                  throw new Error('protocol_incompatible');
                const previous = subscription.subscription_id;
                subscription = null;
                if (this.activeSubscription === previous)
                  this.activeSubscription = null;
                await retireObserved(previous);
                continue;
              }
              this.install(page.snapshot, page.snapshot.cursor);
            }
            let reset = false;
            this.metrics.maxBatch = Math.max(
              this.metrics.maxBatch,
              Math.min(256, page.events.length),
            );
            for (let index = 0; index < page.events.length; index++) {
              if (index && index % 256 === 0) await wait(0, signal);
              if (!alive()) return;
              if (this.apply(page.events[index]) === 'reset') {
                reset = true;
                break;
              }
            }
            if (reset) {
              if (++resetsWithoutProgress > 3)
                throw new Error('protocol_incompatible');
              const previous = subscription.subscription_id;
              subscription = null;
              if (this.activeSubscription === previous)
                this.activeSubscription = null;
              await retireObserved(previous);
              continue;
            }
            if (page.events.length) resetsWithoutProgress = 0;
            cursor = this.state.projection!.cursor;
            await this.transport.acknowledge(
              subscription.subscription_id,
              cursor,
              signal,
            );
            this.metrics.acknowledgements += 1;
            failures = 0;
            idle = page.events.length ? 2000 : Math.min(idle * 2, 30000);
            await wait(idle, signal);
          }
        } catch (error) {
          if (!alive() || aborted(error)) return;
          const safe = clientError(error);
          if (safe.recovery === 'authenticate' || safe.recovery === 'update') {
            this.failed(error);
            return;
          }
          failures += 1;
          streamFailures += 1;
          if (failures > DELAYS.length) {
            this.failed(new TypeError('Disconnected'));
            return;
          }
          this.metrics.reconnects += 1;
          this.update({
            status: 'reconnecting',
            connection: 'none',
            error: safe,
          });
          await wait(
            DELAYS[failures - 1] *
              (0.8 + Math.max(0, Math.min(1, this.random())) * 0.2),
            signal,
          );
        }
      }
    } catch (error) {
      if (alive()) this.failed(error);
    } finally {
      acknowledgements?.close();
      if (
        subscription &&
        this.activeSubscription === subscription.subscription_id
      ) {
        this.activeSubscription = null;
        await this.retireSubscription(subscription.subscription_id);
      }
    }
  }

  getDraft(id: string) {
    return this.drafts.get(id) ?? { text: '', attachments: [] };
  }
  private trimCleanDrafts(): void {
    // The retained server draft remains authoritative. Keep the selected draft
    // and at most 31 other clean drafts; never evict unsaved or active work.
    const candidates = new Set([
      ...this.drafts.keys(),
      ...this.draftRevisions.keys(),
      ...this.draftStates.keys(),
    ]);
    const clean = [...candidates].filter(
      (id) =>
        id !== this.state.selectedConversationId &&
        !this.dirtyDrafts.has(id) &&
        !this.draftWrites.has(id) &&
        this.draftStates.get(id) !== 'conflict',
    );
    for (const id of clean.slice(0, Math.max(0, clean.length - 31))) {
      this.drafts.delete(id);
      this.draftRevisions.delete(id);
      this.draftStates.delete(id);
    }
  }
  setDraft(
    id: string,
    draft: { text: string; attachments: import('./types').AttachmentView[] },
  ): void {
    if (draft.text.length > 200000 || draft.attachments.length > 32) return;
    this.drafts.set(id, draft);
    this.dirtyDrafts.add(id);
    this.setDraftStatus(id, 'saving');
    void this.saveDraft(id);
  }
  private async saveDraft(id: string): Promise<void> {
    if (
      !this.transport.saveDraft ||
      !this.draftRevisions.has(id) ||
      this.draftWrites.has(id) ||
      !this.state.handshake
    )
      return;
    this.draftWrites.add(id);
    const authentication = this.authenticationNumber;
    try {
      while (authentication === this.authenticationNumber && !this.disposed) {
        const draft = this.drafts.get(id);
        if (!draft) break;
        this.setDraftStatus(id, 'saving');
        const result = await this.transport.saveDraft(
          id,
          {
            expected_revision: this.draftRevisions.get(id)!,
            text: draft.text,
            attachment_refs: draft.attachments.map((a) => a.attachment_ref),
          },
          this.lifetime.signal,
        );
        if (authentication !== this.authenticationNumber) break;
        this.draftRevisions.set(id, result.revision);
        if (this.drafts.get(id) === draft) {
          this.dirtyDrafts.delete(id);
          this.setDraftStatus(id, 'saved');
          break;
        }
      }
    } catch (error) {
      if (authentication === this.authenticationNumber)
        this.setDraftStatus(
          id,
          (error as { code?: string }).code === 'draft_revision_conflict'
            ? 'conflict'
            : 'failed',
        );
    } finally {
      this.draftWrites.delete(id);
      this.trimCleanDrafts();
    }
  }
  async refreshWorkspace(): Promise<void> {
    const id = this.state.selectedConversationId,
      ticket = this.selectionNumber;
    if (!id || !this.transport.workspace || !this.state.handshake) return;
    try {
      const [workspace, conversation] = await Promise.all([
        this.transport.workspace(id, this.selection.signal),
        this.transport.getConversation(id, this.selection.signal),
      ]);
      if (ticket === this.selectionNumber && !this.selection.signal.aborted)
        this.update({ workspace, conversation });
    } catch (error) {
      if (!aborted(error) && ticket === this.selectionNumber)
        this.failed(error);
    }
  }
  private setDraftStatus(id: string, status: ClientState['draftStatus']): void {
    this.draftStates.set(id, status);
    if (this.state.selectedConversationId === id)
      this.update({ draftStatus: status });
  }
  savedDraft = (id: string, signal?: AbortSignal) =>
    this.query(() => this.transport.draft?.(id, signal));
  async resolveDraft(
    id: string,
    revision: string,
    keepLocal: boolean,
  ): Promise<void> {
    const saved = await this.savedDraft(id);
    if (saved.revision !== revision) throw { code: 'draft_revision_conflict' };
    if (this.draftWrites.has(id)) throw { code: 'operation_uncertain' };
    this.draftRevisions.set(id, saved.revision);
    if (keepLocal) {
      await this.saveDraft(id);
      if (this.dirtyDrafts.has(id))
        throw {
          code:
            this.draftStates.get(id) === 'conflict'
              ? 'draft_revision_conflict'
              : 'draft_save_failed',
        };
    } else {
      this.drafts.set(id, { text: saved.text, attachments: saved.attachments });
      this.dirtyDrafts.delete(id);
      this.setDraftStatus(id, 'saved');
      this.trimCleanDrafts();
    }
  }
  retryDraft(id: string): Promise<void> {
    return this.saveDraft(id);
  }
  hasUnsavedDraft(): boolean {
    return this.dirtyDrafts.size > 0;
  }
  async searchLibrary(
    query: string,
    conversation?: string,
    cursor?: string,
  ): Promise<void> {
    const ticket = ++this.searchNumber,
      authentication = this.authenticationNumber;
    if (!query.trim()) {
      this.update({ search: null, searching: false });
      return;
    }
    this.update({ searching: true });
    try {
      const page = await this.query(() =>
        this.transport.search?.(
          query,
          conversation,
          cursor,
          this.lifetime.signal,
        ),
      );
      if (
        ticket === this.searchNumber &&
        authentication === this.authenticationNumber
      )
        this.update({ search: page, searching: false });
    } catch (error) {
      if (ticket === this.searchNumber) {
        this.update({ searching: false });
        throw error;
      }
    }
  }
  async showHistory(message?: string, cursor?: string): Promise<void> {
    const id = this.state.selectedConversationId,
      selection = this.selectionNumber,
      ticket = ++this.historyNumber;
    if (
      !id ||
      this.state.loadingConversation ||
      this.state.conversation?.id !== id
    )
      return;
    try {
      const page = await this.query(() =>
        this.transport.history?.(id, message, cursor, this.selection.signal),
      );
      if (selection === this.selectionNumber && ticket === this.historyNumber)
        this.update({ history: page, historyFocus: message ?? null });
    } catch (error) {
      if (
        aborted(error) ||
        selection !== this.selectionNumber ||
        ticket !== this.historyNumber
      )
        return;
      throw error;
    }
  }
  showLatest(): void {
    this.historyNumber += 1;
    this.update({ history: null, historyFocus: null });
  }
  private async query<T>(operation: () => Promise<T> | undefined): Promise<T> {
    const authentication = this.authenticationNumber;
    if (!this.state.handshake || !this.online || this.disposed)
      throw clientError({ code: 'authentication_required' });
    const promise = operation();
    if (!promise) throw clientError({ code: 'capability_unavailable' });
    try {
      const value = await promise;
      if (
        authentication !== this.authenticationNumber ||
        !this.state.handshake ||
        this.disposed
      )
        throw clientError({ code: 'capability_revoked' });
      return value;
    } catch (error) {
      if (
        authentication === this.authenticationNumber &&
        clientError(error).recovery === 'authenticate'
      )
        this.failed(error);
      throw error;
    }
  }
  recentConversations = (signal?: AbortSignal) =>
    this.query(() =>
      this.transport.listConversations(undefined, signal, 'all'),
    );
  library = (
    kind: 'artifact' | 'workspace',
    cursor?: string,
    signal?: AbortSignal,
  ) => this.query(() => this.transport.library?.(kind, cursor, signal));
  messageText = (
    conversation: string,
    message: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.messageText?.(conversation, message, cursor, signal),
    );
  delegatedActivity = (
    conversation: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.authenticatedResult(async (combined) => {
      if (!this.transport.delegatedActivity)
        throw { code: 'capability_unavailable' };
      return this.transport.delegatedActivity(conversation, cursor, combined);
    }, signal);
  delegatedRun = (conversation: string, run: string, signal?: AbortSignal) =>
    this.authenticatedResult(async (combined) => {
      if (!this.transport.delegatedRun)
        throw { code: 'capability_unavailable' };
      return this.transport.delegatedRun(conversation, run, combined);
    }, signal);
  queue = (
    conversation: string,
    generation?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.queue?.(conversation, generation, cursor, signal),
    );
  workspaceFor = (conversation: string, signal?: AbortSignal) =>
    this.query(() => this.transport.workspace?.(conversation, signal));
  steering = (
    conversation: string,
    generation?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.steering?.(conversation, generation, cursor, signal),
    );
  deckSetup = (signal?: AbortSignal) =>
    this.query(() => this.transport.deckSetup?.(signal));
  providerStatus = (signal?: AbortSignal) =>
    this.query(() => this.transport.providerStatus?.(signal));
  liveProviderStatus = (signal?: AbortSignal) =>
    this.query(() => this.transport.liveProviderStatus?.(signal));
  refreshLiveProvider = (provider: string) =>
    this.authenticatedResult((signal) => {
      if (!this.transport.refreshLiveProvider)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.refreshLiveProvider(provider, signal);
    });
  liveProviderRefresh = (signal?: AbortSignal) =>
    this.query(() => this.transport.liveProviderRefresh?.(signal));
  testLiveProviderRuntime = (provider: string) =>
    this.authenticatedResult((signal) => {
      if (!this.transport.testLiveProviderRuntime)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.testLiveProviderRuntime(provider, signal);
    });
  mcpConfiguration = (query: string, cursor?: string, signal?: AbortSignal) =>
    this.query(() => this.transport.mcpConfiguration?.(query, cursor, signal));
  mcpPolicy = (
    query: { server_id: string | null; query: string; cursor?: string },
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.mcpPolicy?.(
        query.server_id,
        query.query,
        query.cursor,
        signal,
      ),
    );
  reviewMcpPolicy = (body: unknown, signal?: AbortSignal) => {
    const input = validateWire<import('./types').McpPolicyRequest>(
      'McpPolicyRequest',
      body,
    );
    return this.query(() => this.transport.reviewMcpPolicy?.(input, signal));
  };
  mcpTestedCatalog = (
    query: {
      server_id: string;
      test_command_id: string;
      query: string;
      cursor?: string;
    },
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.mcpTestedCatalog?.(
        query.server_id,
        query.test_command_id,
        query.query,
        query.cursor,
        signal,
      ),
    );
  reviewMcpCatalog = (body: unknown, signal?: AbortSignal) => {
    const input = validateWire<import('./types').McpCatalogRequest>(
      'McpCatalogRequest',
      body,
    );
    return this.query(() => this.transport.reviewMcpCatalog?.(input, signal));
  };
  reviewMcpConfiguration = (body: unknown, signal?: AbortSignal) => {
    const input = validateWire<import('./types').McpConfigurationReviewRequest>(
      'McpConfigurationReviewRequest',
      body,
    );
    return this.query(() =>
      this.transport.reviewMcpConfiguration?.(input, signal),
    );
  };
  executeMcpConfiguration = async (
    original: {
      command_id: string;
      type:
        | 'mcp.configuration.save'
        | 'mcp.configuration.control'
        | 'mcp.catalog.accept';
      payload:
        | { configuration_revision: string; intent: unknown }
        | import('./types').McpCatalogRequest;
    },
    review: { nonce?: string },
  ) => {
    const handshake = this.state.handshake;
    if (!handshake || !review.nonce)
      throw clientError({ code: 'approval_expired' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      payload: { ...original.payload, nonce: review.nonce },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) =>
      this.transport.command(null, command, original.command_id, signal),
    );
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return {
      command_id: result.command_id,
      status: result.status,
      mcp_configuration: result.mcp_configuration ?? undefined,
    };
  };
  defaultModel = (signal?: AbortSignal) =>
    this.query(() => this.transport.defaultModel?.(signal));
  knowledgeEditor = (entity: string | null, signal?: AbortSignal) =>
    this.query(() => this.transport.knowledgeEditor?.(entity, signal));
  knowledgeEntityDetail = (entity: string, signal?: AbortSignal) =>
    this.query(() => this.transport.knowledgeEntityDetail?.(entity, signal));
  knowledgeRecalls = (signal?: AbortSignal) =>
    this.query(() => this.transport.knowledgeRecalls?.(signal));
  knowledgeChangeLog = (signal?: AbortSignal) =>
    this.query(() => this.transport.knowledgeChangeLog?.(signal));
  reviewKnowledgeMaintenance = (
    body: import('./types').KnowledgeMaintenanceRequest,
    signal?: AbortSignal,
  ) =>
    this.query(() => this.transport.reviewKnowledgeMaintenance?.(body, signal));
  knowledgeMaintenanceReceipt = (command: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.knowledgeMaintenanceReceipt?.(command, signal),
    );
  executeKnowledgeMaintenance = async (
    original: Omit<
      import('./types').KnowledgeMaintenanceCommand,
      'client_session_id'
    >,
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = validateWire<import('./types').KnowledgeMaintenanceCommand>(
      'KnowledgeMaintenanceCommand',
      { ...original, client_session_id: handshake.client_session_id },
    );
    return this.authenticatedResult((signal) => {
      if (!this.transport.executeKnowledgeMaintenance)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeKnowledgeMaintenance(command, signal);
    });
  };
  knowledgeRelations = (
    entity: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.knowledgeRelations?.(entity, cursor, signal),
    );
  reviewKnowledgeRelation = (
    action: import('./types').KnowledgeRelationReviewRequest['action'],
    payload: Record<string, unknown>,
    signal?: AbortSignal,
  ) => {
    const input = validateWire<
      import('./types').KnowledgeRelationReviewRequest
    >('KnowledgeRelationReviewRequest', { action, payload });
    return this.query(() =>
      this.transport.reviewKnowledgeRelation?.(input, signal),
    );
  };
  knowledgeRelationReceipt = (command: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.knowledgeRelationReceipt?.(command, signal),
    );
  executeKnowledgeRelation = async (original: {
    command_id: string;
    type: import('./types').KnowledgeRelationReviewRequest['action'];
    payload: Record<string, unknown>;
  }) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeKnowledgeRelation)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeKnowledgeRelation(command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  reviewKnowledge = (
    action: import('./types').KnowledgeReviewRequest['action'],
    payload: Record<string, unknown>,
    signal?: AbortSignal,
  ) => {
    const input = validateWire<import('./types').KnowledgeReviewRequest>(
      'KnowledgeReviewRequest',
      { action, payload },
    );
    return this.query(() => this.transport.reviewKnowledge?.(input, signal));
  };
  knowledgeReceipt = (command: string, signal?: AbortSignal) =>
    this.query(() => this.transport.knowledgeReceipt?.(command, signal));
  executeKnowledge = async (original: {
    command_id: string;
    type: import('./types').KnowledgeReviewRequest['action'];
    payload: Record<string, unknown>;
  }) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeKnowledge)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeKnowledge(command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  wikiStatus = (folderGrant?: string, signal?: AbortSignal) =>
    this.query(() => this.transport.wikiStatus?.(folderGrant, signal));
  openWikiFolder = (signal?: AbortSignal) =>
    this.query(() => this.transport.openWikiFolder?.(signal));
  wikiArticles = (folderGrant: string, cursor?: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.wikiArticles?.(folderGrant, cursor, signal),
    );
  wikiArticle = (folderGrant: string, article: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.wikiArticle?.(folderGrant, article, signal),
    );
  reviewWiki = (
    action: import('./types').WikiReviewRequest['action'],
    folderGrant: string,
    payload: Record<string, unknown>,
    signal?: AbortSignal,
  ) => {
    const input = validateWire<import('./types').WikiReviewRequest>(
      'WikiReviewRequest',
      { action, folder_grant: folderGrant, payload },
    );
    return this.query(() => this.transport.reviewWiki?.(input, signal));
  };
  wikiReceipt = (folderGrant: string, command: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.wikiReceipt?.(folderGrant, command, signal),
    );
  executeWiki = async (original: {
    command_id: string;
    type: import('./types').WikiReviewRequest['action'];
    payload: Record<string, unknown>;
  }) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeWiki)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeWiki(command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  channels = (query: string, signal?: AbortSignal) =>
    this.query(() => this.transport.channels?.(query, signal));
  reviewChannel = (
    body: import('./types').ChannelActionRequest,
    signal?: AbortSignal,
  ) => this.query(() => this.transport.reviewChannel?.(body, signal));
  channelReceipt = (channel: string, command: string, signal?: AbortSignal) =>
    this.query(() => this.transport.channelReceipt?.(channel, command, signal));
  executeChannel = async (original: {
    command_id: string;
    type: string;
    payload: Record<string, unknown>;
  }) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeChannel)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeChannel(command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  plugins = (
    query: string,
    source: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() => this.transport.plugins?.(query, source, cursor, signal));
  plugin = (plugin: string, signal?: AbortSignal) =>
    this.query(() => this.transport.plugin?.(plugin, signal));
  reviewPlugin = (
    plugin: string,
    action: import('./types').PluginReviewRequest['action'],
    payload: Record<string, unknown>,
    signal?: AbortSignal,
  ) => {
    const body = validateWire<import('./types').PluginReviewRequest>(
      'PluginReviewRequest',
      { action, payload },
    );
    return this.query(() =>
      this.transport.reviewPlugin?.(plugin, body, signal),
    );
  };
  pluginReceipt = (plugin: string, command: string, signal?: AbortSignal) =>
    this.query(() => this.transport.pluginReceipt?.(plugin, command, signal));
  executePlugin = async (
    plugin: string,
    original: {
      command_id: string;
      type: string;
      payload: Record<string, unknown>;
    },
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executePlugin)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executePlugin(plugin, command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  skills = (
    query: string,
    source?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => this.query(() => this.transport.skills?.(query, source, cursor, signal));
  skill = (skill: string, signal?: AbortSignal) =>
    this.query(() => this.transport.skill?.(skill, signal));
  skillProposals = (signal?: AbortSignal) =>
    this.query(() => this.transport.skillProposals?.(signal));
  reviewSkill = (
    action: import('./types').SkillReviewRequest['action'],
    payload: Record<string, unknown>,
    signal?: AbortSignal,
  ) => {
    const body = validateWire<import('./types').SkillReviewRequest>(
      'SkillReviewRequest',
      { action, payload },
    );
    return this.query(() => this.transport.reviewSkill?.(body, signal));
  };
  skillReceipt = (command: string, signal?: AbortSignal) =>
    this.query(() => this.transport.skillReceipt?.(command, signal));
  executeSkill = async (original: {
    command_id: string;
    type: string;
    payload: Record<string, unknown>;
  }) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeSkill)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeSkill(command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  conversationActions = (conversation: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.conversationActions?.(conversation, signal),
    );
  reviewConversationAction = (
    conversation: string,
    action:
      | 'conversation.rename'
      | 'conversation.pin'
      | 'conversation.archive'
      | 'conversation.export',
    revision: string,
    fields: Record<string, unknown>,
    signal?: AbortSignal,
  ) => {
    const body = validateWire<
      import('./types').ConversationActionReviewRequest
    >('ConversationActionReviewRequest', {
      type: action,
      expected_revision: revision,
      payload: fields,
    });
    return this.query(() =>
      this.transport.reviewConversationAction?.(conversation, body, signal),
    );
  };
  conversationActionReceipt = (
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.conversationActionReceipt?.(conversation, command, signal),
    );
  executeConversationAction = async (
    conversation: string,
    original: {
      command_id: string;
      type:
        | 'conversation.rename'
        | 'conversation.pin'
        | 'conversation.archive'
        | 'conversation.export';
      expected_revision: string;
      payload: Record<string, unknown> & {
        checkpoint_revision: string;
        action_digest: string;
      };
    },
    review: {
      conversation_id: string;
      action: string;
      revision: string;
      checkpoint_revision: string;
      action_digest: string;
      review_id?: string;
    },
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    if (
      !review.review_id ||
      review.conversation_id !== conversation ||
      review.action !== original.type ||
      review.revision !== original.expected_revision ||
      review.checkpoint_revision !== original.payload.checkpoint_revision ||
      review.action_digest !== original.payload.action_digest
    )
      throw clientError({ code: 'conversation_review_changed' });
    const command = validateWire<import('./types').ConversationActionCommand>(
      'ConversationActionCommand',
      {
        ...original,
        client_session_id: handshake.client_session_id,
        payload: { ...original.payload, review_id: review.review_id },
      },
    );
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeConversationAction)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeConversationAction(
        conversation,
        command,
        signal,
      );
    });
    if (
      result.command_id !== original.command_id ||
      result.action !== original.type
    )
      throw clientError({ code: 'protocol_incompatible' });
    return {
      ...result,
      code: result.code ?? undefined,
      conversation: result.conversation ?? undefined,
      export: result.export ?? undefined,
    };
  };
  browserControls = (conversation: string, signal?: AbortSignal) =>
    this.query(() => this.transport.browserControls?.(conversation, signal));
  reviewBrowserControl = (
    conversation: string,
    action: import('./types').BrowserReview['action'],
    payload: import('./types').BrowserReviewRequest['payload'],
    signal?: AbortSignal,
  ) => {
    const body = validateWire<import('./types').BrowserReviewRequest>(
      'BrowserReviewRequest',
      { action, type: action, payload },
    );
    return this.query(() =>
      this.transport.reviewBrowserControl?.(conversation, body, signal),
    );
  };
  browserControlReceipt = (
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.browserControlReceipt?.(conversation, command, signal),
    );
  executeBrowserControl = async (
    conversation: string,
    original: {
      command_id: string;
      type: import('./types').BrowserReview['action'];
      payload: Record<string, unknown> & { nonce: string; revision: string };
    },
    review: import('./types').BrowserReview,
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    if (
      review.conversation_id !== conversation ||
      review.action !== original.type ||
      review.revision !== original.payload.revision ||
      review.nonce !== original.payload.nonce
    )
      throw clientError({ code: 'browser_revision_conflict' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult(async (signal) => {
      if (
        !this.transport.executeBrowserControl ||
        !this.transport.browserControlReceipt
      )
        throw clientError({ code: 'unsupported_command' });
      if (this.browserCommandAttempts.has(original.command_id)) {
        try {
          return await this.transport.browserControlReceipt(
            conversation,
            original.command_id,
            signal,
          );
        } catch (cause) {
          if (clientError(cause).code !== 'not_found') throw cause;
        }
      }
      this.browserCommandAttempts.add(original.command_id);
      return this.transport.executeBrowserControl(
        conversation,
        command,
        signal,
      );
    });
    if (
      result.command_id !== original.command_id ||
      result.action !== original.type ||
      result.conversation_id !== conversation
    )
      throw clientError({ code: 'protocol_incompatible' });
    if (result.status !== 'partial')
      this.browserCommandAttempts.delete(original.command_id);
    return result;
  };
  goals = (
    conversation: string,
    query: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.goals?.(conversation, query, cursor, signal),
    );
  goal = (conversation: string, goal: string, signal?: AbortSignal) =>
    this.query(() => this.transport.goal?.(conversation, goal, signal));
  reviewGoal = (
    conversation: string,
    body: import('./types').GoalCommandPayload,
    signal?: AbortSignal,
  ) =>
    this.query(() => this.transport.reviewGoal?.(conversation, body, signal));
  goalReceipt = (conversation: string, command: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.goalReceipt?.(conversation, command, signal),
    );
  executeGoal = async (
    conversation: string,
    original: {
      command_id: string;
      type: 'goal.control';
      payload: Record<string, unknown>;
    },
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeGoal)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeGoal(conversation, command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  profiles = (
    query: string,
    scope?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() => this.transport.profiles?.(query, scope, cursor, signal));
  profile = (profile: string, signal?: AbortSignal) =>
    this.query(() => this.transport.profile?.(profile, signal));
  reviewProfile = (
    body: import('./types').ProfileCommandPayload,
    signal?: AbortSignal,
  ) => this.query(() => this.transport.reviewProfile?.(body, signal));
  profileReceipt = (profile: string, command: string, signal?: AbortSignal) =>
    this.query(() => this.transport.profileReceipt?.(profile, command, signal));
  executeProfile = async (original: {
    command_id: string;
    type: 'profile.mutate';
    payload: Record<string, unknown>;
  }) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeProfile)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeProfile(command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  reviewDocumentUpload = (
    files: import('./types').DocumentUploadReviewRequest['files'],
  ) => {
    const input = validateWire<import('./types').DocumentUploadReviewRequest>(
      'DocumentUploadReviewRequest',
      { files },
    );
    return this.query(() => this.transport.reviewDocumentUpload?.(input.files));
  };
  private documentUploadResult(
    value: import('./types').DocumentUploadReceipt,
    command: string,
  ) {
    if (value.command_id !== command)
      throw clientError({ code: 'protocol_incompatible' });
    return {
      ...value,
      code: value.code ?? undefined,
      batch_id: value.batch_id ?? undefined,
      processing: value.processing ?? undefined,
      files: value.files ?? undefined,
    };
  }
  documentUploadReceipt = async (command: string) =>
    this.documentUploadResult(
      await this.query(() => this.transport.documentUploadReceipt?.(command)),
      command,
    );
  uploadDocuments = async (
    original: {
      command_id: string;
      type: 'document.upload';
      payload: {
        files: import('./types').DocumentUploadReviewRequest['files'];
        review_id: string;
      };
    },
    files: readonly File[],
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.uploadDocuments)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.uploadDocuments(command, files, signal);
    });
    return this.documentUploadResult(result, original.command_id);
  };
  reviewDocumentProcessing = async (
    conversation: string,
    batch: string,
    revision: string,
  ) => {
    const input = validateWire<
      import('./types').DocumentProcessingReviewRequest
    >('DocumentProcessingReviewRequest', { batch_id: batch, revision });
    const result = await this.query(() =>
      this.transport.reviewDocumentProcessing?.(conversation, input),
    );
    if (result.conversation_id !== conversation || result.batch_id !== batch)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  private documentProcessingResult(
    value: import('./types').DocumentProcessingReceipt,
    command: string,
  ) {
    if (value.command_id !== command)
      throw clientError({ code: 'protocol_incompatible' });
    return {
      ...value,
      code: value.code ?? undefined,
      batch_id: value.batch_id ?? undefined,
      processing: value.processing ?? undefined,
    };
  }
  documentProcessingReceipt = async (conversation: string, command: string) =>
    this.documentProcessingResult(
      await this.query(() =>
        this.transport.documentProcessingReceipt?.(conversation, command),
      ),
      command,
    );
  executeDocumentProcessing = async (
    conversation: string,
    original: {
      command_id: string;
      type: 'document.batch.process';
      payload: {
        conversation_id: string;
        batch_id: string;
        revision: string;
        review_id: string;
      };
    },
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (
      !isCommand(command) ||
      original.payload.conversation_id !== conversation
    )
      throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeDocumentProcessing)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeDocumentProcessing(
        conversation,
        command,
        signal,
      );
    });
    return this.documentProcessingResult(result, original.command_id);
  };
  mcpRuntime = (server: string, signal?: AbortSignal) =>
    this.query(() => this.transport.mcpRuntime?.(server, signal));
  documentQueue = (
    options: { kind: 'batches' | 'jobs'; batch_id?: string; cursor?: string },
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.documentQueue?.(
        options.kind,
        options.batch_id,
        options.cursor,
        signal,
      ),
    );
  reviewDocumentControl = (
    action: import('./types').DocumentControlReviewRequest['action'],
    payload: Record<string, unknown>,
  ) => {
    const input = validateWire<import('./types').DocumentControlReviewRequest>(
      'DocumentControlReviewRequest',
      { action, payload },
    );
    return this.query(() => this.transport.reviewDocumentControl?.(input));
  };
  private documentControlResult(
    value: import('./types').DocumentControlReceipt,
    command: string,
  ) {
    if (value.command_id !== command)
      throw clientError({ code: 'protocol_incompatible' });
    return {
      ...value,
      code: value.code ?? undefined,
      outcome: value.outcome ?? undefined,
      batch_ids: value.batch_ids ?? undefined,
      count: value.count ?? undefined,
      retained_work: value.retained_work ?? undefined,
    };
  }
  documentControlReceipt = async (command: string) =>
    this.documentControlResult(
      await this.query(() => this.transport.documentControlReceipt?.(command)),
      command,
    );
  executeDocumentControl = async (original: {
    command_id: string;
    type: import('./types').DocumentControlReviewRequest['action'];
    payload: Record<string, unknown>;
  }) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeDocumentControl)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeDocumentControl(command, signal);
    });
    return this.documentControlResult(result, original.command_id);
  };
  runtimeInstallation = async (runtime: string, signal?: AbortSignal) => {
    const result = await this.query(() =>
      this.transport.runtimeInstallation?.(runtime, signal),
    );
    if (result.runtime_id !== runtime)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  reviewRuntimeInstallation = (body: unknown, signal?: AbortSignal) => {
    const input = validateWire<
      import('./types').RuntimeInstallationReviewRequest
    >('RuntimeInstallationReviewRequest', body);
    return this.query(() =>
      this.transport.reviewRuntimeInstallation?.(input, signal),
    );
  };
  runtimeInstallationReceipt = async (
    runtime: string,
    command: string,
    signal?: AbortSignal,
  ) => {
    const result = await this.query(() =>
      this.transport.runtimeInstallationReceipt?.(runtime, command, signal),
    );
    if (
      result.command_id !== command ||
      result.installation.runtime_id !== runtime
    )
      throw clientError({ code: 'protocol_incompatible' });
    return { ...result, code: result.code ?? undefined };
  };
  executeRuntimeInstallation = async (
    original: {
      command_id: string;
      type: string;
      payload: Record<string, unknown>;
    },
    review: { nonce?: string } | null,
    signal?: AbortSignal,
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      payload: {
        ...original.payload,
        ...(review ? { nonce: review.nonce } : {}),
      },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((owned) => {
      if (!this.transport.executeRuntimeInstallation)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeRuntimeInstallation(
        command,
        signal ? AbortSignal.any([signal, owned]) : owned,
      );
    });
    if (
      result.command_id !== original.command_id ||
      result.installation.runtime_id !== original.payload.runtime_id
    )
      throw clientError({ code: 'protocol_incompatible' });
    return { ...result, code: result.code ?? undefined };
  };
  reviewMcpRuntime = (body: unknown, signal?: AbortSignal) => {
    const request = validateWire<import('./types').McpRuntimeReviewRequest>(
      'McpRuntimeReviewRequest',
      body,
    );
    return this.query(() => this.transport.reviewMcpRuntime?.(request, signal));
  };
  executeMcpRuntime = async (
    original: {
      command_id: string;
      type: 'mcp.runtime.control';
      payload: import('./types').McpRuntimeReviewRequest;
    },
    review: { nonce?: string },
  ) => {
    const handshake = this.state.handshake;
    if (!handshake || !review.nonce)
      throw clientError({ code: 'approval_expired' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      payload: { ...original.payload, nonce: review.nonce },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) =>
      this.transport.command(null, command, original.command_id, signal),
    );
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return {
      command_id: result.command_id,
      status: result.status,
      mcp_runtime: result.mcp_runtime ?? undefined,
    };
  };
  subscriptionAccounts = (signal?: AbortSignal) =>
    this.query(() => this.transport.subscriptionAccounts?.(signal));
  reviewDocumentRemoval = async (document: string | null) => {
    const result = await this.query(() =>
      this.transport.reviewDocumentRemoval?.(document),
    );
    return {
      ...result,
      source_command_id: result.source_command_id ?? undefined,
      removal_id: result.removal_id ?? undefined,
    };
  };
  reviewDocumentRemovalRetry = async (command: string) => {
    const result = await this.query(() =>
      this.transport.reviewDocumentRemovalRetry?.(command),
    );
    return {
      ...result,
      source_command_id: result.source_command_id ?? undefined,
      removal_id: result.removal_id ?? undefined,
    };
  };
  documentRemovalReceipt = async (command: string) => {
    const result = await this.query(() =>
      this.transport.documentRemovalReceipt?.(command),
    );
    return {
      ...result,
      code: result.code ?? undefined,
      removal: result.removal ?? undefined,
    };
  };
  executeDocumentRemoval = async (original: {
    command_id: string;
    type: 'document.remove' | 'document.removal.retry';
    payload: Record<string, unknown>;
  }) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'session_expired' });
    const command = structuredClone({
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    });
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeDocumentRemoval)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.executeDocumentRemoval(command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return {
      ...result,
      code: result.code ?? undefined,
      removal: result.removal ?? undefined,
    };
  };
  buddy = (conversation: string, signal?: AbortSignal) =>
    this.query(() => this.transport.buddy?.(conversation, signal));
  buddyPacks = (conversation: string, cursor?: string, signal?: AbortSignal) =>
    this.query(() => this.transport.buddyPacks?.(conversation, cursor, signal));
  buddyPack = (conversation: string, pack: string, signal?: AbortSignal) =>
    this.query(() => this.transport.buddyPack?.(conversation, pack, signal));
  buddyMedia = (
    conversation: string,
    pack: string,
    asset: string,
    revision: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.buddyMedia?.(conversation, pack, asset, revision, signal),
    );
  reviewBuddy = (
    conversation: string,
    body: import('./types').BuddyHatchRequest,
    signal?: AbortSignal,
  ) =>
    this.query(() => this.transport.reviewBuddy?.(conversation, body, signal));
  buddyReceipt = (
    conversation: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.buddyReceipt?.(conversation, command, signal),
    );
  executeBuddy = async (
    conversation: string,
    original: {
      command_id: string;
      type: string;
      payload: Record<string, unknown>;
    },
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'session_expired' });
    const command = structuredClone({
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    });
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeBuddy)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.executeBuddy(conversation, command, signal);
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  subscriptionProbes = (signal?: AbortSignal) =>
    this.query(() => this.transport.subscriptionProbes?.(signal));
  reviewSubscriptionProbe = (
    body: import('./types').SubscriptionProbeRequest,
    signal?: AbortSignal,
  ) => this.query(() => this.transport.reviewSubscriptionProbe?.(body, signal));
  subscriptionProbeReceipt = (command: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.subscriptionProbeReceipt?.(command, signal),
    );
  subscriptionProbeStatus = async (command: string, signal?: AbortSignal) => {
    const value = await this.query(() =>
      this.transport.subscriptionProbeStatus?.(command, signal),
    );
    if (value.operation && value.operation.command_id !== command)
      throw clientError({ code: 'protocol_incompatible' });
    return value.operation;
  };
  cancelSubscriptionProbe = async (command: string) => {
    const value = await this.authenticatedResult((signal) => {
      if (!this.transport.cancelSubscriptionProbe)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.cancelSubscriptionProbe(command, signal);
    });
    if (value.command_id !== command)
      throw clientError({ code: 'protocol_incompatible' });
    return value;
  };
  applySubscriptionProbe = async (
    review: import('./types').SubscriptionProbeReview,
    commandId: string,
  ) => {
    const captured = validateWire<import('./types').SubscriptionProbeReview>(
      'SubscriptionProbeReview',
      review,
    );
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'session_expired' });
    const command = {
      command_id: commandId,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      type: 'provider.subscription.probe',
      payload: {
        provider_id: captured.provider_id,
        provider_revision: captured.provider_revision,
        kind: captured.kind,
        model_ref: captured.model_ref,
        nonce: captured.nonce,
      },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const value = await this.authenticatedResult((signal) => {
      if (!this.transport.applySubscriptionProbe)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.applySubscriptionProbe(command, signal);
    });
    if (
      value.command_id !== commandId ||
      value.result.provider_id !== captured.provider_id ||
      value.result.kind !== captured.kind ||
      value.result.model_ref !== captured.model_ref
    )
      throw clientError({ code: 'protocol_incompatible' });
    return value.result;
  };
  subscriptionOptions = (signal?: AbortSignal) =>
    this.query(() => this.transport.subscriptionOptions?.(signal));
  reviewSubscriptionOptions = (
    body: import('./types').SubscriptionOptionsRequest,
    signal?: AbortSignal,
  ) =>
    this.query(() => this.transport.reviewSubscriptionOptions?.(body, signal));
  subscriptionOptionsReceipt = (command: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.subscriptionOptionsReceipt?.(command, signal),
    );
  applySubscriptionOptions = async (
    review: import('./types').SubscriptionOptionsReview,
    commandId: string,
  ) => {
    const captured = validateWire<import('./types').SubscriptionOptionsReview>(
      'SubscriptionOptionsReview',
      review,
    );
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'session_expired' });
    const kinds = {
      reference: 'provider.subscription.reference',
      client_id_save: 'provider.subscription.client_id.save',
      client_id_reset: 'provider.subscription.client_id.reset',
    } as const;
    const command = {
      command_id: commandId,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      type: kinds[captured.operation],
      payload: {
        provider_id: captured.provider_id,
        provider_revision: captured.provider_revision,
        value: captured.value,
        nonce: captured.nonce,
      },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.applySubscriptionOptions)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.applySubscriptionOptions(command, signal);
    });
    if (result.command_id !== commandId)
      throw clientError({ code: 'protocol_incompatible' });
    return result.options;
  };
  cancelSubscriptionStart = (command: string) =>
    this.authenticatedResult((signal) => {
      if (!this.transport.cancelSubscriptionStart)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.cancelSubscriptionStart(command, signal);
    });
  reviewSubscriptionAction = (body: unknown, signal?: AbortSignal) => {
    const request = validateWire<import('./types').SubscriptionActionRequest>(
      'SubscriptionActionRequest',
      body,
    );
    return this.query(() =>
      this.transport.reviewSubscriptionAction?.(request, signal),
    );
  };
  subscriptionFlow = (
    identity: { flow_id: string; server_epoch: string },
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.subscriptionFlow?.(
        identity.flow_id,
        identity.server_epoch,
        signal,
      ),
    );
  subscriptionReceipt = (
    provider: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.subscriptionReceipt?.(provider, command, signal),
    );
  applySubscriptionAction = async (
    intent: unknown,
    review: import('./types').SubscriptionActionReview,
    commandId: string,
  ) => {
    const request = validateWire<import('./types').SubscriptionActionRequest>(
      'SubscriptionActionRequest',
      intent,
    );
    const handshake = this.state.handshake;
    if (!handshake || !review.nonce)
      throw clientError({ code: 'approval_expired' });
    if (
      request.provider_id !== review.provider_id ||
      request.provider_revision !== review.provider_revision ||
      request.operation !== review.operation ||
      (request.flow_id ?? null) !== review.flow_id ||
      (request.server_epoch ?? null) !== review.server_epoch
    )
      throw clientError({ code: 'approval_expired' });
    const { operation, ...payload } = request;
    const command = {
      command_id: commandId,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      type: `provider.subscription.${operation}`,
      payload: { ...payload, nonce: review.nonce },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.subscriptionAction)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.subscriptionAction(command, signal);
    });
    if (result.command_id !== commandId)
      throw clientError({ code: 'protocol_incompatible' });
    return { ...result, flow: result.flow ?? undefined };
  };
  cancelSubscriptionFlow = async (
    identity: import('./types').SubscriptionFlowSnapshot,
    commandId: string,
  ) => {
    const accounts = await this.subscriptionAccounts();
    const intent = {
      provider_id: identity.provider_id,
      provider_revision: accounts.revision,
      operation: 'cancel',
      flow_id: identity.flow_id,
      server_epoch: identity.server_epoch,
    };
    const review = await this.reviewSubscriptionAction(intent);
    return this.applySubscriptionAction(intent, review, commandId);
  };
  reviewDefaultModel = (
    body: import('./types').DefaultModelReviewRequest,
    signal?: AbortSignal,
  ) => this.query(() => this.transport.reviewDefaultModel?.(body, signal));
  defaultModelReceipt = (command: string, signal?: AbortSignal) =>
    this.query(() => this.transport.defaultModelReceipt?.(command, signal));
  executeDefaultModel = async (
    review: import('./types').DefaultModelReviewRequest & { nonce?: string },
    commandId: string,
  ) => {
    const handshake = this.state.handshake;
    if (!handshake || !review.nonce)
      throw clientError({ code: 'approval_expired' });
    const command = {
      command_id: commandId,
      client_session_id: handshake.client_session_id,
      type: 'provider.default_model.save',
      expected_revision: '0',
      payload: {
        settings_revision: review.settings_revision,
        provider_id: review.provider_id,
        model_id: review.model_id,
        nonce: review.nonce,
      },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) =>
      this.transport.command(null, command, commandId, signal),
    );
    if (
      result.status !== 'completed' ||
      result.command_id !== commandId ||
      !result.selection
    )
      throw clientError({ code: 'operation_uncertain' });
    return result.selection;
  };
  providerSettings = (provider: string, signal?: AbortSignal) =>
    this.query(() => this.transport.providerSettings?.(provider, signal));
  reviewProviderSettings = (
    provider: string,
    body: import('./types').ProviderSettingsReviewRequest,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.reviewProviderSettings?.(provider, body, signal),
    );
  providerSettingsReceipt = (
    provider: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.providerSettingsReceipt?.(provider, command, signal),
    );
  artifactReviewDraft = (
    conversation: string,
    binding: string,
    body: import('./types').ArtifactReviewDraftRequest,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.artifactReviewDraft?.(conversation, binding, body, signal),
    );
  reviewArtifactPreset = (
    conversation: string,
    binding: string,
    body: import('./types').ArtifactPresetReviewRequest,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.reviewArtifactPreset?.(
        conversation,
        binding,
        body,
        signal,
      ),
    );
  stageArtifactUpload = (
    conversation: string,
    file: File,
    commandId: string,
    signal?: AbortSignal,
  ) =>
    this.authenticatedResult((current) => {
      if (!this.transport.stageArtifactUpload)
        return Promise.reject({ code: 'capability_unavailable' });
      return this.transport.stageArtifactUpload(
        conversation,
        file,
        commandId,
        current,
      );
    }, signal);
  executeArtifactDesign = (
    conversation: string,
    commandId: string,
    type:
      | 'artifact.design.control'
      | 'artifact.asset.upload'
      | 'artifact.preset.mutate',
    payload: Record<string, unknown>,
    expectedRevision: string,
  ) => {
    const command = {
      command_id: commandId,
      client_session_id: this.state.handshake?.client_session_id,
      type,
      expected_revision: expectedRevision,
      payload,
    };
    if (!isCommand(command))
      return Promise.reject(clientError({ code: 'invalid_command' }));
    return this.authenticatedResult(async (signal) => {
      const result = await this.transport.command(
        conversation,
        command,
        commandId,
        signal,
      );
      if (
        result.status === 'completed' &&
        this.state.selectedConversationId === conversation
      )
        await this.refreshWorkspace();
      return result;
    });
  };
  providerConfiguration = (query = '', cursor?: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.providerConfiguration?.(query, cursor, signal),
    );
  reviewProviderConfiguration = (
    body: import('./types').ProviderConfigurationReviewRequest,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.reviewProviderConfiguration?.(body, signal),
    );
  providerConfigurationReceipt = (command: string, signal?: AbortSignal) =>
    this.query(() =>
      this.transport.providerConfigurationReceipt?.(command, signal),
    );
  executeProviderConfiguration = async (
    type: import('./types').ProviderConfigurationReviewRequest['operation'],
    configuration_revision: string,
    fields: import('./types').ProviderConfigurationReviewRequest['fields'],
    commandId: string,
    nonce: string,
  ) => {
    const command = {
      command_id: commandId,
      client_session_id: this.state.handshake?.client_session_id,
      type,
      expected_revision: '0',
      payload: { configuration_revision, fields, nonce },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) =>
      this.transport.command(null, command, commandId, signal),
    );
    if (
      result.command_id !== commandId ||
      result.status !== 'completed' ||
      !result.configuration_revision
    )
      throw clientError({ code: 'operation_uncertain' });
    return { configuration_revision: result.configuration_revision };
  };
  executeProviderCredential = async (
    provider: string,
    revision: string,
    operation: 'save' | 'clear' | 'restore',
    value: string | undefined,
    commandId: string,
    nonce: string,
  ): Promise<import('./types').ProviderSettingsSnapshot> => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      command_id: commandId,
      client_session_id: handshake.client_session_id,
      type: `${provider.startsWith('custom_openai_') ? 'provider.custom_credential' : 'provider.credential'}.${operation}`,
      expected_revision: '0',
      payload: {
        provider_id: provider,
        provider_revision: revision,
        nonce,
        ...(operation === 'save' ? { value } : {}),
      },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    // The bounded credential session owns the private intent. Do not add
    // write-only secrets to the general conversation command retention map.
    const result = await this.authenticatedResult((signal) =>
      this.transport.command(null, command, commandId, signal),
    );
    if (
      result.status !== 'completed' ||
      result.command_id !== commandId ||
      result.credential?.provider_id !== provider
    )
      throw clientError({ code: 'provider_credential_unconfirmed' });
    return { ...result.credential, display_name: provider };
  };
  savedEntities = (
    query = '',
    entityType?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.savedEntities?.(query, entityType, cursor, signal),
    );
  knowledgeEntities = (
    query = '',
    entityType?: string,
    status?: string,
    source?: string,
    tier?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.knowledgeEntities?.(
        query,
        entityType,
        status,
        source,
        tier,
        cursor,
        signal,
      ),
    );
  savedDocuments = (
    query = '',
    status?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.savedDocuments?.(query, status, cursor, signal),
    );
  cachedTools = (
    source?: import('./types').ToolCatalogPage['items'][number]['source'],
    query = '',
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.cachedTools?.(source, query, cursor, signal),
    );
  savedTasks = (
    query = '',
    enabled?: boolean,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.savedTasks?.(query, enabled, cursor, signal),
    );
  taskEditor = (task: string, signal?: AbortSignal) =>
    this.query(() => this.transport.taskEditor?.(task, signal));
  taskGraph = (task: string, signal?: AbortSignal) =>
    this.query(() => this.transport.taskGraph?.(task, signal));
  taskSettings = (task: string, signal?: AbortSignal) =>
    this.query(() => this.transport.taskSettings?.(task, signal));
  workspaceProcesses = (
    conversation: string,
    binding: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.workspaceProcesses?.(conversation, binding, signal),
    );
  workspaceProcessRecovery = (
    conversation: string,
    binding: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.workspaceProcessRecovery?.(
        conversation,
        binding,
        cursor,
        signal,
      ),
    );
  workspaceProcessOutput = (
    conversation: string,
    binding: string,
    process: string,
    cursor: number,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.workspaceProcessOutput?.(
        conversation,
        binding,
        process,
        cursor,
        signal,
      ),
    );
  reviewWorkspaceProcess = (
    conversation: string,
    binding: string,
    body: import('./types').WorkspaceProcessReviewRequest,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.reviewWorkspaceProcess?.(
        conversation,
        binding,
        body,
        signal,
      ),
    );
  reviewTaskSettings = (
    task: string,
    fields: import('./types').TaskSettingsFields,
    signal?: AbortSignal,
  ) =>
    this.query(() => this.transport.reviewTaskSettings?.(task, fields, signal));
  downloadTaskWebhook = (
    task: string,
    revision: string,
    signal?: AbortSignal,
  ) =>
    this.authenticatedResult((current) => {
      if (!this.transport.downloadTaskWebhook)
        throw clientError({ code: 'dependency_unavailable' });
      return this.transport.downloadTaskWebhook(task, revision, current);
    }, signal);
  prepareArtifactShare = (
    conversation: string,
    binding: string,
    options: import('./types').ArtifactShareOptions,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.prepareArtifactShare?.(
        conversation,
        binding,
        options,
        signal,
      ),
    );
  artifactShareChannels = (cursor?: string, signal?: AbortSignal) =>
    this.query(() => this.transport.artifactShareChannels?.(cursor, signal));
  workspaceImports = (
    conversation: string,
    binding: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.workspaceImports?.(conversation, binding, cursor, signal),
    );
  developerRepository = (
    conversation: string,
    binding: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.developerRepository?.(conversation, binding, signal),
    );
  reviewDeveloperRepository = (
    conversation: string,
    binding: string,
    action: import('./types').DeveloperRepositoryReview['action'],
    payload: import('./types').DeveloperRepositoryReviewRequest['payload'],
    signal?: AbortSignal,
  ) => {
    const body = validateWire<
      import('./types').DeveloperRepositoryReviewRequest
    >('DeveloperRepositoryReviewRequest', { action, payload });
    return this.query(() =>
      this.transport.reviewDeveloperRepository?.(
        conversation,
        binding,
        body,
        signal,
      ),
    );
  };
  developerRepositoryReceipt = (
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.developerRepositoryReceipt?.(
        conversation,
        binding,
        command,
        signal,
      ),
    );
  executeDeveloperRepository = async (
    conversation: string,
    binding: string,
    original: {
      command_id: string;
      type: import('./types').DeveloperRepositoryReview['action'];
      payload: Record<string, unknown>;
    },
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const command = {
      ...original,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeDeveloperRepository)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeDeveloperRepository(
        conversation,
        binding,
        command,
        signal,
      );
    });
    if (result.command_id !== original.command_id)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  workspaceImportPatch = (
    conversation: string,
    binding: string,
    pending: string,
    revision: string,
    offset: number,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.workspaceImportPatch?.(
        conversation,
        binding,
        pending,
        revision,
        offset,
        signal,
      ),
    );
  reviewWorkspaceUndo = (
    conversation: string,
    binding: string,
    changeSet: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.reviewWorkspaceUndo?.(
        conversation,
        binding,
        changeSet,
        signal,
      ),
    );
  workspaceUndoReceipt = (
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.workspaceUndoReceipt?.(
        conversation,
        binding,
        command,
        signal,
      ),
    );
  reviewWorkspaceUndoRecovery = (
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.reviewWorkspaceUndoRecovery?.(
        conversation,
        binding,
        command,
        signal,
      ),
    );
  executeWorkspaceUndo = async (
    conversation: string,
    binding: string,
    review: import('./types').WorkspaceUndoReview,
    commandId: string,
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    if (
      review.conversation_id !== conversation ||
      review.binding_id !== binding
    )
      throw clientError({ code: 'resource_binding_revoked' });
    const { nonce, ...reviewed } = review;
    const command = {
      command_id: commandId,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      type: 'workspace.undo',
      payload: { review: reviewed, nonce },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeWorkspaceUndo)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeWorkspaceUndo(
        conversation,
        binding,
        command,
        signal,
      );
    });
    if (
      result.command_id !== commandId ||
      result.conversation_id !== conversation ||
      result.resource_id !== review.resource_id ||
      result.change_set_id !== review.change_set_id
    )
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  reviewWorkspaceImport = (
    conversation: string,
    binding: string,
    pending: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.reviewWorkspaceImport?.(
        conversation,
        binding,
        pending,
        signal,
      ),
    );
  workspaceImportReceipt = (
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.workspaceImportReceipt?.(
        conversation,
        binding,
        command,
        signal,
      ),
    );
  reviewWorkspaceImportRecovery = (
    conversation: string,
    binding: string,
    command: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.reviewWorkspaceImportRecovery?.(
        conversation,
        binding,
        command,
        signal,
      ),
    );
  executeWorkspaceImport = async (
    conversation: string,
    binding: string,
    review: import('./types').WorkspaceImportReview,
    commandId: string,
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    const { nonce, ...reviewed } = review;
    const command = {
      command_id: commandId,
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      type: 'workspace.import',
      payload: { review: reviewed, nonce },
    };
    if (!isCommand(command)) throw clientError({ code: 'invalid_command' });
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeWorkspaceImport)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeWorkspaceImport(
        conversation,
        binding,
        command,
        signal,
      );
    });
    if (
      result.command_id !== commandId ||
      result.conversation_id !== conversation ||
      result.resource_id !== review.resource_id
    )
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  workspaceEditableFile = (
    conversation: string,
    binding: string,
    path: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.workspaceEditableFile?.(
        conversation,
        binding,
        path,
        signal,
      ),
    );
  taskRunReview = (task: string, signal?: AbortSignal) =>
    this.query(() => this.transport.taskRunReview?.(task, signal));
  taskRuns = (task: string, cursor?: string, signal?: AbortSignal) =>
    this.query(() => this.transport.taskRuns?.(task, cursor, signal));
  taskRun = (task: string, run: string, signal?: AbortSignal) =>
    this.query(() => this.transport.taskRun?.(task, run, signal));
  taskApprovals = (
    task: string,
    run: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() => this.transport.taskApprovals?.(task, run, cursor, signal));
  artifactExport = (
    conversation: string,
    binding: string,
    exportId: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.artifactExport?.(conversation, binding, exportId, signal),
    );
  artifactDownload = (
    conversation: string,
    binding: string,
    descriptor: import('./types').ArtifactExport,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.artifactDownload?.(
        conversation,
        binding,
        descriptor,
        signal,
      ),
    );
  cachedModels = (
    providerId?: string,
    query = '',
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.cachedModels?.(providerId, query, cursor, signal),
    );
  modelsSettings = (signal?: AbortSignal) =>
    this.query(() => this.transport.modelsSettings?.(signal));
  updateModelSurface = (body: import('./types').ModelSurfaceMutation) =>
    this.authenticatedResult((signal) => {
      if (!this.transport.updateModelSurface)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.updateModelSurface(body, signal);
    });
  updateModelContext = (body: import('./types').ModelContextMutation) =>
    this.authenticatedResult((signal) => {
      if (!this.transport.updateModelContext)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.updateModelContext(body, signal);
    });
  agentRuntimeSettings = (signal?: AbortSignal) =>
    this.query(() => this.transport.agentRuntimeSettings?.(signal));
  saveAgentRuntimeSettings = (
    body: import('./types').AgentRuntimeSettingsState,
  ) =>
    this.authenticatedResult((signal) => {
      if (!this.transport.saveAgentRuntimeSettings)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.saveAgentRuntimeSettings(body, signal);
    });
  resetAgentRuntimeSettings = () =>
    this.authenticatedResult((signal) => {
      if (!this.transport.resetAgentRuntimeSettings)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.resetAgentRuntimeSettings(signal);
    });
  modelCatalogSummary = (surface: string, signal?: AbortSignal) =>
    this.query(() => this.transport.modelCatalogSummary?.(surface, signal));
  modelCatalogPage = (
    surface: string,
    providerId?: string,
    query = '',
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.modelCatalogPage?.(
        surface,
        providerId,
        query,
        cursor,
        signal,
      ),
    );
  refreshModelsCatalog = () =>
    this.authenticatedResult((signal) => {
      if (!this.transport.refreshModelsCatalog)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.refreshModelsCatalog(signal);
    });
  refreshModelCameras = () =>
    this.authenticatedResult((signal) => {
      if (!this.transport.refreshModelCameras)
        throw clientError({ code: 'capability_unavailable' });
      return this.transport.refreshModelCameras(signal);
    });
  settingsSnapshot = (signal?: AbortSignal) =>
    this.query(() => this.transport.settingsSnapshot?.(signal));
  reviewSettingsMutation = (
    body: import('./types').SettingsMutationRequest,
    signal?: AbortSignal,
  ) => this.query(() => this.transport.reviewSettingsMutation?.(body, signal));
  settingsMutationReceipt = (command: string, signal?: AbortSignal) =>
    this.query(() => this.transport.settingsMutationReceipt?.(command, signal));
  executeSettingsMutation = async (
    request: import('./types').SettingsMutationRequest,
    review: import('./types').SettingsMutationReview,
    commandId: string,
  ) => {
    const handshake = this.state.handshake;
    if (!handshake) throw clientError({ code: 'authentication_required' });
    if (
      request.settings_revision !== review.settings_revision ||
      request.page !== review.page ||
      request.field !== review.field
    )
      throw clientError({ code: 'invalid_command' });
    const command = {
      command_id: commandId,
      client_session_id: handshake.client_session_id,
      type: 'settings.update' as const,
      payload: {
        ...request,
        action_digest: review.action_digest,
        review_id: review.review_id,
      },
    };
    validateWire('SettingsMutationCommand', command);
    const result = await this.authenticatedResult((signal) => {
      if (!this.transport.executeSettingsMutation)
        throw clientError({ code: 'unsupported_command' });
      return this.transport.executeSettingsMutation(command, signal);
    });
    if (result.command_id !== commandId)
      throw clientError({ code: 'protocol_incompatible' });
    return result;
  };
  artifactSetup = (
    mode: NonNullable<ArtifactSetupOptions['mode']>,
    signal?: AbortSignal,
  ) => this.query(() => this.transport.artifactSetup?.(mode, signal));
  pickFolder = (signal?: AbortSignal) =>
    this.query(() => this.transport.pickFolder?.(signal));
  artifactPreview = (
    conversation: string,
    binding: string,
    page?: string,
    revision?: string,
    signal?: AbortSignal,
    authoring?: import('./types').ArtifactAuthoring,
  ) =>
    this.query(() =>
      this.transport.artifactPreview?.(
        conversation,
        binding,
        page,
        revision,
        signal,
        authoring,
      ),
    );
  artifactLifecycle = (
    conversation: string,
    binding: string,
    expectedRevision: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.artifactLifecycle?.(
        conversation,
        binding,
        expectedRevision,
        signal,
      ),
    );
  artifactStaticPreview = (
    conversation: string,
    binding: string,
    pageId: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.artifactStaticPreview?.(
        conversation,
        binding,
        pageId,
        signal,
      ),
    );
  designControls = (
    conversation: string,
    binding: string,
    options: import('./types').DesignControlOptions,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.designControls?.(conversation, binding, options, signal),
    );
  designReview = (
    conversation: string,
    binding: string,
    options: import('./types').DesignReviewOptions,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.designReview?.(conversation, binding, options, signal),
    );
  designPresentation = (
    conversation: string,
    binding: string,
    options: import('./types').DesignPresentationOptions,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.designPresentation?.(
        conversation,
        binding,
        options,
        signal,
      ),
    );
  artifactEditing = (
    conversation: string,
    binding: string,
    pageId?: string,
    pageCursor?: string,
    elementCursor?: string,
    historyCursor?: string,
    elementId?: string,
    limit = 25,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.artifactEditing?.(
        conversation,
        binding,
        pageId,
        pageCursor,
        elementCursor,
        historyCursor,
        elementId,
        limit,
        signal,
      ),
    );
  inspector = (
    conversation: string,
    binding: string,
    refresh?: boolean,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.inspector?.(conversation, binding, refresh, signal),
    );
  changes = (
    conversation: string,
    binding: string,
    revision?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.changes?.(conversation, binding, revision, cursor, signal),
    );
  directory = (
    conversation: string,
    binding: string,
    path?: string,
    cursor?: string,
    revision?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.directory?.(
        conversation,
        binding,
        path,
        cursor,
        revision,
        signal,
      ),
    );
  file = (
    conversation: string,
    binding: string,
    path: string,
    offset?: number,
    revision?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.file?.(
        conversation,
        binding,
        path,
        offset,
        revision,
        signal,
      ),
    );
  approval = (id: string, signal?: AbortSignal) =>
    this.query(() => this.transport.approval?.(id, signal));
  diff = (
    conversation: string,
    binding: string,
    path: string,
    snapshot: string,
    offset?: number,
    revision?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.diff?.(
        conversation,
        binding,
        path,
        snapshot,
        offset,
        revision,
        signal,
      ),
    );
  changeSets = (
    conversation: string,
    binding: string,
    revision: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.changeSets?.(
        conversation,
        binding,
        revision,
        cursor,
        signal,
      ),
    );
  changeSetFiles = (
    conversation: string,
    binding: string,
    change: string,
    revision: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.changeSetFiles?.(
        conversation,
        binding,
        change,
        revision,
        cursor,
        signal,
      ),
    );
  content = (
    conversation: string,
    message: string,
    cursor?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.content?.(conversation, message, cursor, signal),
    );
  async intent(
    target: string | null,
    type: Command['type'],
    payload: object,
    revision: string,
    identity: string = crypto.randomUUID(),
  ): Promise<CommandReceipt> {
    const session = this.state.handshake?.client_session_id;
    if (!session) throw clientError({ code: 'authentication_required' });
    const command = {
      command_id: identity,
      client_session_id: session,
      type,
      payload,
      expected_revision: revision,
    } as Command;
    const receipt = await this.command(target, command, identity);
    if (target === this.state.selectedConversationId)
      await this.refreshWorkspace();
    await this.loadMoreConversations(true);
    return receipt;
  }

  setVisible(visible: boolean): void {
    if (visible === this.visible || this.disposed) return;
    this.visible = visible;
    if (!visible) {
      this.stopObservation();
      this.update({ connection: 'none' });
    } else if (
      this.online &&
      this.state.selectedConversationId &&
      this.state.handshake
    )
      this.beginObservation(
        this.state.selectedConversationId,
        this.selectionNumber,
      );
  }
  /** Suspend network work without stopping backend execution or replaying commands. */
  setOnline(online: boolean): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (online === this.online)
      return this.reconnectPromise ?? Promise.resolve();
    this.online = online;
    if (online) return this.reconnect();
    this.authenticationNumber += 1;
    this.selectionNumber += 1;
    this.lifetime.abort();
    this.lifetime = new AbortController();
    this.selection.abort();
    this.transcriptRequest = false;
    this.stopObservation();
    this.startPromise = null;
    this.reconnectPromise = null;
    this.transport.clearSession(true);
    this.update({
      status: 'disconnected',
      error: clientError(new TypeError('Offline')),
      connection: 'none',
      handshake: null,
      loadingConversation: false,
      loadingConversations: false,
    });
    return Promise.resolve();
  }
  /** Advisory presentation only. The wire decoder still rejects unsupported v1 events. */
  suggestPanel(suggestion: ClientPanelSuggestion): void {
    if (
      !this.state.handshake ||
      suggestion.type !== 'panel.suggested' ||
      !isPanelDescriptor(suggestion.descriptor) ||
      !/^(0|[1-9][0-9]{0,19})$/.test(suggestion.conversation_revision)
    )
      return;
    const conversation =
      this.state.conversation?.id === suggestion.conversation_id
        ? this.state.conversation
        : this.state.conversations.find(
            (row) => row.id === suggestion.conversation_id,
          );
    if (
      !conversation ||
      BigInt(suggestion.conversation_revision) < BigInt(conversation.revision)
    )
      return;
    const key = this.suggestionKey(suggestion);
    if (
      this.state.suggestions.some((value) => this.suggestionKey(value) === key)
    )
      return;
    this.update({
      suggestions: [
        ...this.state.suggestions,
        structuredClone(suggestion),
      ].slice(-20),
    });
  }
  dismissSuggestion(suggestion: ClientPanelSuggestion): void {
    const key = this.suggestionKey(suggestion);
    this.update({
      suggestions: this.state.suggestions.filter(
        (value) => this.suggestionKey(value) !== key,
      ),
    });
  }
  private suggestionKey(suggestion: ClientPanelSuggestion): string {
    const descriptor = suggestion.descriptor;
    return JSON.stringify([
      suggestion.conversation_id,
      descriptor.panel_kind,
      descriptor.resource_ref ?? '',
      descriptor.subresource_key ?? '',
    ]);
  }
  async reconnect(): Promise<void> {
    if (this.disposed || !this.online) return;
    if (this.reconnectPromise) return this.reconnectPromise;
    const operation = (async () => {
      this.stopObservation();
      this.selection.abort();
      this.selectionNumber += 1;
      this.lifetime.abort();
      this.lifetime = new AbortController();
      this.startPromise = null;
      await this.start();
    })();
    this.reconnectPromise = operation;
    try {
      await operation;
    } finally {
      if (this.reconnectPromise === operation) this.reconnectPromise = null;
    }
  }

  /** Caller supplies stable command/key identities. No queued or automatic mutation replay. */
  command(
    target: string | null,
    command: Command,
    key: string,
  ): Promise<CommandReceipt> {
    if (this.disposed)
      return Promise.reject(new DOMException('Cancelled', 'AbortError'));
    if (!this.online)
      return Promise.reject(clientError(new TypeError('Offline')));
    if (command.client_session_id !== this.state.handshake?.client_session_id)
      return Promise.reject(clientError({ code: 'authentication_required' }));
    if (!isCommand(command))
      return Promise.reject(clientError({ code: 'invalid_command' }));
    if (this.state.status !== 'ready')
      return Promise.reject(clientError({ code: 'operation_uncertain' }));
    const authentication = this.authenticationNumber;
    const signal = this.lifetime.signal;
    const submitted = structuredClone(command);
    const verifier = intentVerifier(target, submitted);
    const previous = this.commandClaims.get(key);
    if (previous)
      return Promise.all([previous.verifier, verifier]).then(
        ([expected, actual]) => {
          if (expected !== actual)
            throw clientError({ code: 'idempotency_mismatch' });
          return previous.result;
        },
      );
    if (this.commandClaims.size >= 256) {
      for (const [oldKey, claim] of this.commandClaims) {
        if (claim.settled && !claim.failed) {
          this.commandClaims.delete(oldKey);
          break;
        }
      }
    }
    if (this.commandClaims.size >= 256)
      return Promise.reject(clientError({ code: 'operation_uncertain' }));
    let dispatched = false;
    const current = () => {
      if (
        signal.aborted ||
        authentication !== this.authenticationNumber ||
        this.disposed
      )
        throw dispatched
          ? clientError({ code: 'operation_uncertain' })
          : new DOMException('Cancelled', 'AbortError');
    };
    const result = verifier
      .then(() => {
        current();
        dispatched = true;
        return this.transport.command(target, submitted, key, signal);
      })
      .then((receipt) => {
        current();
        const claim = this.commandClaims.get(key);
        if (claim) claim.settled = true;
        return receipt;
      })
      .catch((error) => {
        const claim = this.commandClaims.get(key);
        if (claim) claim.failed = true;
        current();
        if (aborted(error)) throw error;
        if (clientError(error).recovery === 'authenticate') this.failed(error);
        throw clientError(error);
      });
    this.commandClaims.set(key, {
      verifier,
      result,
      failed: false,
      settled: false,
    });
    return result;
  }
  /** Explicit user retry only. The identical key/body remains bound at the server. */
  async retryCommand(
    target: string | null,
    command: Command,
    key: string,
  ): Promise<CommandReceipt> {
    const previous = this.commandClaims.get(key);
    if (
      !previous ||
      (await previous.verifier) !== (await intentVerifier(target, command))
    )
      return Promise.reject(clientError({ code: 'idempotency_mismatch' }));
    if (!previous.failed) {
      const receipt = await previous.result;
      const savedTaskRecovery =
        [
          'task.create',
          'task.update',
          'task.graph.update',
          'task.settings.update',
          'task.webhook.rotate',
        ].includes(command.type) &&
        receipt.status === 'partial' &&
        receipt.task_saved === true;
      const savedWorkspaceRecovery =
        command.type === 'workspace.edit' &&
        receipt.status === 'partial' &&
        receipt.workspace_edit?.status === 'partial';
      if (!savedTaskRecovery && !savedWorkspaceRecovery) return receipt;
    }
    this.commandClaims.delete(key);
    return this.command(target, command, key);
  }
  private async authenticatedResult<T>(
    operation: (signal: AbortSignal) => Promise<T>,
    signal?: AbortSignal,
  ): Promise<T> {
    if (this.disposed) throw new DOMException('Cancelled', 'AbortError');
    if (!this.online) throw clientError(new TypeError('Offline'));
    if (!this.state.handshake)
      throw clientError({ code: 'authentication_required' });
    const authentication = this.authenticationNumber;
    const combined = signal
      ? AbortSignal.any([this.lifetime.signal, signal])
      : this.lifetime.signal;
    try {
      combined.throwIfAborted();
      const result = await operation(combined);
      combined.throwIfAborted();
      if (authentication !== this.authenticationNumber || this.disposed)
        throw new DOMException('Cancelled', 'AbortError');
      return result;
    } catch (error) {
      if (
        aborted(error) ||
        combined.aborted ||
        authentication !== this.authenticationNumber
      )
        throw new DOMException('Cancelled', 'AbortError');
      const safe = clientError(error);
      if (safe.recovery === 'authenticate' || safe.recovery === 'update')
        this.failed(error);
      throw safe;
    }
  }
  receipt(id: string, signal?: AbortSignal) {
    return this.authenticatedResult(
      (current) => this.transport.receipt(id, current),
      signal,
    );
  }
  upload(conversation: string, file: File, signal?: AbortSignal) {
    return this.authenticatedResult(
      (current) => this.transport.upload(conversation, file, current),
      signal,
    );
  }
  attachmentMetadata(reference: string, signal?: AbortSignal) {
    return this.authenticatedResult(
      (current) => this.transport.attachmentMetadata(reference, current),
      signal,
    );
  }
  terminalRead(terminal: string, cursor: number, signal?: AbortSignal) {
    return this.authenticatedResult(
      (current) => this.transport.terminalRead(terminal, cursor, current),
      signal,
    );
  }
  terminalInput(terminal: string, data: string, signal?: AbortSignal) {
    return this.authenticatedResult(
      (current) => this.transport.terminalInput(terminal, data, current),
      signal,
    );
  }
  terminalResize(
    terminal: string,
    cols: number,
    rows: number,
    signal?: AbortSignal,
  ) {
    return this.authenticatedResult(
      (current) => this.transport.terminalResize(terminal, cols, rows, current),
      signal,
    );
  }
  terminalDisconnect(terminal: string, signal?: AbortSignal) {
    return this.authenticatedResult(
      (current) => this.transport.terminalDisconnect(terminal, current),
      signal,
    );
  }
  download(reference: string, signal?: AbortSignal) {
    return this.authenticatedResult(
      (current) => this.transport.download(reference, current),
      signal,
    );
  }
  dispose(): void {
    this.stopObservation(true);
    this.disposed = true;
    this.lifetime.abort();
    this.selection.abort();
    this.transport.clearSession();
    this.listeners.clear();
    this.commandClaims.clear();
    this.browserCommandAttempts.clear();
    this.seen.clear();
    this.sequences.clear();
    this.retiredSubscriptions.clear();
  }
}
