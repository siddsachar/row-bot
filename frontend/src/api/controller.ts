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
  pickFolder = (signal?: AbortSignal) =>
    this.query(() => this.transport.pickFolder?.(signal));
  artifactPreview = (
    conversation: string,
    binding: string,
    page?: string,
    revision?: string,
    signal?: AbortSignal,
  ) =>
    this.query(() =>
      this.transport.artifactPreview?.(
        conversation,
        binding,
        page,
        revision,
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
    if (!previous.failed) return previous.result;
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
    this.seen.clear();
    this.sequences.clear();
    this.retiredSubscriptions.clear();
  }
}
