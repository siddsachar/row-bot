import * as wire from '../../../contracts/client-platform/v1/typescript/client';
import f01 from '../../../contracts/client-platform/v1/fixtures/F-P01.json';
import f02 from '../../../contracts/client-platform/v1/fixtures/F-P02.json';
import f03 from '../../../contracts/client-platform/v1/fixtures/F-P03.json';
import f04 from '../../../contracts/client-platform/v1/fixtures/F-P04.json';
import f05 from '../../../contracts/client-platform/v1/fixtures/F-P05.json';
import f06 from '../../../contracts/client-platform/v1/fixtures/F-P06.json';
import f07 from '../../../contracts/client-platform/v1/fixtures/F-P07.json';
import f08 from '../../../contracts/client-platform/v1/fixtures/F-P08.json';
import f09 from '../../../contracts/client-platform/v1/fixtures/F-P09.json';
import f10 from '../../../contracts/client-platform/v1/fixtures/F-P10.json';
import type { ClientTransport, ConversationListGroup } from './types';

/** The server's listing groups (`client_platform._LIST_GROUPS`) over a row. */
function inGroup(
  row: wire.ConversationView,
  group: ConversationListGroup,
): boolean {
  const kinds = new Set((row.resource_bindings ?? []).map(({ kind }) => kind));
  const design = row.category === 'designer' || kinds.has('artifact');
  const code = row.category === 'code' || kinds.has('workspace');
  const workflow = row.category === 'workflow';
  return {
    all: true,
    pinned: row.pinned,
    artifact: design,
    workspace: code,
    workflow,
    chat: !design && !code && !workflow,
  }[group];
}

export type Recording = {
  fixture_id: string;
  records: { schema: string; value: unknown }[];
  delivery_order: number[];
  expected_final_snapshot: unknown;
};
export const recordings: readonly Recording[] = [
  f01,
  f02,
  f03,
  f04,
  f05,
  f06,
  f07,
  f08,
  f09,
  f10,
];
export function recorded<T>(id: string, schema: string): T[] {
  return recordings
    .find((item) => item.fixture_id === id)!
    .records.filter((item) => item.schema === schema)
    .map((item) => wire.validateWire<T>(schema, structuredClone(item.value)));
}

/** A fake monotonic clock retains the accepted Windows fixture-clock correction. */
export class FixtureClock {
  private ticks = 1000;
  now = (): number => this.ticks;
  advance(milliseconds: number): void {
    if (!Number.isFinite(milliseconds) || milliseconds < 0)
      throw new Error('Clock cannot move backwards');
    this.ticks += milliseconds;
  }
}

export type FixtureScenario =
  'normal' | 'incompatible' | 'unauthorized' | 'disconnected';
export class FixtureTransport implements ClientTransport {
  scenario: FixtureScenario;
  readonly clock = new FixtureClock();
  readonly counters = {
    connects: 0,
    subscribes: 0,
    unsubscribes: 0,
    active: 0,
    streams: 0,
    maxStreams: 0,
    polls: 0,
    commands: 0,
    acks: [] as string[],
    listeners: 0,
    transcriptPages: 0,
    transcriptRowsDelivered: 0,
  };
  private subscriptions = new Map<string, string>();
  private queues = new Map<string, (wire.EventRecord | wire.StreamReset)[]>();
  private wakes = new Map<string, () => void>();
  private snapshots = new Map<string, wire.Snapshot>();
  private receipts = new Map<string, wire.CommandReceipt>();
  private profileReceipts = new Map<string, wire.ProfileReceipt>();
  readonly profilesData: wire.ProfileSummary[] = [
    {
      id: 'builtin:general',
      slug: 'general',
      display_name: 'General Assistant',
      description: 'A safe default for everyday work.',
      when_to_use: 'General requests.',
      scope: 'system',
      surface_scope: 'global',
      source: 'builtin',
      group: 'Everyday',
      icon: 'auto_awesome',
      enabled: true,
      editable: false,
      revision: '1',
      capability: 'read_only',
      allow_tools: [],
      skills: [],
      context_mode: 'auto',
      workspace_mode: 'auto',
      approval_mode: 'inherit',
      instructions_preview: '',
      instructions_truncated: true,
    },
  ];
  private transcriptSizes = new Map<string, 1000 | 10000>();
  private expireReplay = false;
  private readonly transcriptTemplate = wire.validateWire<wire.TranscriptPage>(
    'TranscriptPage',
    structuredClone(
      f05.records.find((record) => record.schema === 'TranscriptPage')!.value,
    ),
  );
  fixtureTranscriptRowCount: 1000 | 10000 | null = null;
  readonly conversations: wire.ConversationView[];

  constructor(
    options: { scenario?: FixtureScenario; conversationCount?: number } = {},
  ) {
    this.scenario = options.scenario ?? 'normal';
    const base = recorded<wire.ConversationView>(
      'F-P08',
      'ConversationView',
    )[0];
    this.conversations = Array.from(
      { length: options.conversationCount ?? 3 },
      (_, index) => ({
        ...base,
        id: index === 0 ? 'conversation-a' : `conversation-${index + 1}`,
        title:
          index === 0
            ? 'A place for your ideas'
            : `Sample conversation ${index + 1}`,
        resource_bindings: [],
      }),
    );
    const snapshot = wire.validateWire<wire.Snapshot>(
      'Snapshot',
      structuredClone(f01.expected_final_snapshot),
    );
    this.conversations.forEach((row) =>
      this.snapshots.set(row.id, {
        ...structuredClone(snapshot),
        conversation_id: row.id,
        generation: null,
      }),
    );
  }
  private available(signal?: AbortSignal): void {
    signal?.throwIfAborted();
    if (this.scenario === 'unauthorized')
      throw { status: 401, code: 'session_expired' };
    if (this.scenario === 'disconnected')
      throw new TypeError('Fixture offline');
    if (this.scenario === 'incompatible')
      throw recorded<wire.Problem>('F-P06', 'Problem')[0];
  }
  async connect(signal?: AbortSignal): Promise<wire.HandshakeView> {
    this.counters.connects += 1;
    this.available(signal);
    return recorded<wire.HandshakeView>('F-P06', 'HandshakeView')[0];
  }
  async listConversations(
    cursor?: string,
    signal?: AbortSignal,
    group: ConversationListGroup = 'all',
  ): Promise<wire.ConversationPage> {
    this.available(signal);
    const listed = this.conversations.filter((row) => inGroup(row, group));
    const start = cursor ? Number(cursor) : 0;
    const items = listed.slice(start, start + 50);
    const has_more = start + items.length < listed.length;
    return {
      items,
      has_more,
      next_cursor: has_more ? String(start + items.length) : null,
    };
  }
  async getConversation(
    id: string,
    signal?: AbortSignal,
  ): Promise<wire.ConversationView> {
    this.available(signal);
    const row = this.conversations.find((value) => value.id === id);
    if (!row) throw { code: 'not_found', status: 404 };
    return structuredClone(row);
  }
  async getTranscript(
    id: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<wire.TranscriptPage> {
    this.available(signal);
    if (this.transcriptSizes.has(id)) {
      const start = cursor ? Number(cursor.replace('fixture-history-', '')) : 0;
      if (
        !Number.isSafeInteger(start) ||
        start < 0 ||
        start >= this.transcriptSizes.get(id)!
      )
        throw { code: 'cursor_expired' };
      const page = this.transcriptPage(id, start);
      this.counters.transcriptPages += 1;
      this.counters.transcriptRowsDelivered += page.rows.length;
      return page;
    }
    if (id === 'history-fixture') {
      const pages = recorded<wire.TranscriptPage>('F-P05', 'TranscriptPage');
      const index = cursor
        ? pages.findIndex((page) => page.next_cursor === cursor) + 1
        : 0;
      const page = pages[Math.max(0, index)];
      return { ...page, conversation_id: id };
    }
    const snapshot = this.snapshots.get(id);
    if (!snapshot) throw { code: 'not_found', status: 404 };
    return {
      ...structuredClone(snapshot),
      has_more: false,
      next_cursor: null,
      previous_cursor: null,
    };
  }
  setSnapshot(snapshot: wire.Snapshot): void {
    this.snapshots.set(snapshot.conversation_id, structuredClone(snapshot));
  }
  /** Foundation calibration only: materialize each accepted-shape page on demand. */
  setTranscriptSize(count: 1000 | 10000, id = 'conversation-a'): void {
    if (![1000, 10000].includes(count) || !this.snapshots.has(id))
      throw new Error('Invalid fixture workload');
    this.transcriptSizes.set(id, count);
    this.fixtureTranscriptRowCount = count;
    this.counters.transcriptPages = 0;
    this.counters.transcriptRowsDelivered = 0;
    const page = this.transcriptPage(id, 0);
    this.snapshots.set(id, {
      conversation_id: id,
      server_epoch: page.server_epoch,
      projection_revision: page.projection_revision,
      cursor: page.cursor,
      checkpoint_revision: page.checkpoint_revision,
      rows: page.rows,
      generation: null,
    });
  }
  private transcriptPage(id: string, start: number): wire.TranscriptPage {
    const count = this.transcriptSizes.get(id)!;
    const end = Math.min(start + 100, count);
    const template = this.transcriptTemplate;
    return wire.validateWire<wire.TranscriptPage>('TranscriptPage', {
      ...template,
      conversation_id: id,
      rows: Array.from({ length: end - start }, (_, offset) => {
        const index = start + offset;
        return {
          ...template.rows[0],
          id: `fixture-history-row-${index}`,
          message_id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
          blocks: [
            {
              type: 'text',
              text: `Synthetic calibration row ${index}. ${'Bounded fixture text. '.repeat(8)}`,
            },
          ],
        };
      }),
      has_more: end < count,
      next_cursor: end < count ? `fixture-history-${end}` : null,
      previous_cursor: start
        ? `fixture-history-${Math.max(0, start - 100)}`
        : null,
    });
  }
  /** One canonical expired-replay reset on the next streaming observation. */
  expireNextReplay(): void {
    this.expireReplay = true;
  }
  /** Valid accepted delta shape with monotonic fixture revision/sequence and current epoch. */
  emitTextDelta(text = 'x', id = 'conversation-a'): wire.EventRecord {
    const snapshot = this.snapshots.get(id);
    if (!snapshot || text.length > 4096)
      throw new Error('Invalid fixture delta');
    const template = recorded<wire.Event>('F-P01', 'Event').find(
      (event) => event.type === 'transcript.delta',
    )!;
    if (template.type !== 'transcript.delta')
      throw new Error('Invalid fixture template');
    const revision = String(BigInt(snapshot.projection_revision) + 1n);
    const rowId = 'fixture-live-row';
    const existing = snapshot.rows.find((row) => row.id === rowId);
    const content =
      (existing?.blocks
        .map((block) => ('text' in block ? block.text : ''))
        .join('') ?? '') + text;
    if (content.length > 262144) throw new Error('Fixture text limit reached');
    const record: wire.EventRecord = {
      cursor: `fixture-event-${revision}`,
      event: wire.validateWire<wire.Event>('Event', {
        ...template,
        event_id: `fixture-delta-${id}-${revision}`,
        topic: `conversation.${id}`,
        conversation_id: id,
        server_epoch: snapshot.server_epoch,
        projection_revision: revision,
        source_stream_id: id,
        source_epoch: snapshot.server_epoch,
        source_sequence_start: revision,
        source_sequence_end: revision,
        payload: {
          ...template.payload,
          row_id: rowId,
          render_revision: revision,
          public_text_delta: text,
        },
      }),
    };
    const row: wire.TranscriptRow = {
      id: rowId,
      role: 'assistant',
      render_revision: revision,
      blocks: [{ type: 'text', text: content }],
    };
    this.snapshots.set(id, {
      ...snapshot,
      projection_revision: revision,
      cursor: record.cursor,
      rows: [...snapshot.rows.filter((value) => value.id !== rowId), row].slice(
        -200,
      ),
    });
    this.emit(record);
    return record;
  }
  async subscribe(
    id: string,
    signal?: AbortSignal,
  ): Promise<wire.SubscriptionView> {
    this.available(signal);
    const snapshot = this.snapshots.get(id);
    if (!snapshot) throw { code: 'not_found', status: 404 };
    const subscription_id = `00000000-0000-4000-8000-${String(++this.counters.subscribes).padStart(12, '0')}`;
    this.subscriptions.set(subscription_id, id);
    this.queues.set(subscription_id, []);
    this.counters.active = this.subscriptions.size;
    return {
      subscription_id,
      snapshot: structuredClone(snapshot),
      cursor: snapshot.cursor,
    };
  }
  async *observe(
    subscription: string,
    _cursor: string,
    signal: AbortSignal,
    _notices?: wire.NoticePosition,
  ): AsyncGenerator<
    wire.EventRecord | wire.StreamReset | { notice: wire.NoticeFrame }
  > {
    this.available(signal);
    this.counters.streams += 1;
    this.counters.maxStreams = Math.max(
      this.counters.maxStreams,
      this.counters.streams,
    );
    try {
      if (this.expireReplay) {
        this.expireReplay = false;
        yield { snapshot_required: true, recovery: 'resubscribe' };
        return;
      }
      while (!signal.aborted && this.subscriptions.has(subscription)) {
        this.available(signal);
        const item = this.queues.get(subscription)?.shift();
        if (item) {
          yield item;
          continue;
        }
        await new Promise<void>((resolve) => {
          const wake = () => {
            signal.removeEventListener('abort', wake);
            this.wakes.delete(subscription);
            this.counters.listeners -= 1;
            resolve();
          };
          this.counters.listeners += 1;
          this.wakes.set(subscription, wake);
          signal.addEventListener('abort', wake, { once: true });
        });
      }
    } finally {
      this.counters.streams -= 1;
    }
  }
  emit(record: wire.EventRecord | wire.StreamReset): void {
    this.clock.advance(1);
    for (const [subscription, id] of this.subscriptions) {
      if ('event' in record && record.event.conversation_id !== id) continue;
      const queue = this.queues.get(subscription)!;
      // Slow fixtures reset rather than allowing an unbounded pending queue.
      if (queue.length >= 256)
        queue.splice(0, queue.length, {
          snapshot_required: true,
          recovery: 'resubscribe',
        });
      else queue.push(structuredClone(record));
      this.wakes.get(subscription)?.();
    }
  }
  disconnect(): void {
    this.scenario = 'disconnected';
    [...this.wakes.values()].forEach((wake) => wake());
  }
  /** Deletes a conversation as another window would: it and its subscriptions go, and open streams end. */
  deleteElsewhere(id: string): string[] {
    this.snapshots.delete(id);
    const ended = [...this.subscriptions]
      .filter(([, conversation]) => conversation === id)
      .map(([subscription]) => subscription);
    ended.forEach((subscription) => this.subscriptions.delete(subscription));
    ended.forEach((subscription) => this.wakes.get(subscription)?.());
    return ended;
  }
  async poll(
    subscription: string,
    cursor: string,
    signal?: AbortSignal,
  ): Promise<wire.EventPage> {
    this.available(signal);
    this.counters.polls += 1;
    const records = this.queues.get(subscription) ?? [];
    const snapshot_required = records.some((record) => !('event' in record));
    const events = records
      .splice(0, 256)
      .filter((record): record is wire.EventRecord => 'event' in record);
    const snapshot = snapshot_required
      ? this.snapshots.get(this.subscriptions.get(subscription)!)
      : undefined;
    return {
      snapshot_required,
      ...(snapshot ? { snapshot: structuredClone(snapshot) } : {}),
      events,
      cursor: events.at(-1)?.cursor ?? cursor,
    };
  }
  async acknowledge(
    _subscription: string,
    cursor: string,
    signal?: AbortSignal,
  ): Promise<wire.Acknowledged> {
    this.available(signal);
    this.counters.acks.push(cursor);
    return { acknowledged: true };
  }
  async unsubscribe(
    subscription: string,
    signal?: AbortSignal,
  ): Promise<wire.Unsubscribed> {
    signal?.throwIfAborted();
    if (this.subscriptions.delete(subscription))
      this.counters.unsubscribes += 1;
    this.queues.delete(subscription);
    this.wakes.get(subscription)?.();
    this.counters.active = this.subscriptions.size;
    return { unsubscribed: true };
  }
  async command(
    target: string | null,
    command: wire.Command,
    _key: string,
    signal?: AbortSignal,
  ): Promise<wire.CommandReceipt> {
    this.available(signal);
    this.counters.commands += 1;
    if (command.type === 'conversation.create') {
      const existing = this.receipts.get(command.command_id);
      if (existing) return existing;
      const payload = command.payload as wire.CreatePayload;
      const selected = payload.agent_profile_id
        ? this.profilesData.find(
            (profile) =>
              profile.id === payload.agent_profile_id && profile.enabled,
          )
        : null;
      if (payload.agent_profile_id && !selected)
        throw { code: 'invalid_command', status: 400 };
      const id = `fixture-${command.command_id}`;
      const base = this.conversations[0];
      this.conversations.unshift({
        ...base,
        id,
        title: payload.title ?? 'New conversation',
      });
      this.snapshots.set(id, {
        ...structuredClone(this.snapshots.get(base.id)!),
        conversation_id: id,
        rows: [],
      });
      const created: wire.CommandReceipt = {
        command_id: command.command_id,
        conversation_id: id,
        status: 'completed',
      };
      this.receipts.set(command.command_id, created);
      return created;
    }
    if (command.type === 'conversation.delete') {
      const index = this.conversations.findIndex((row) => row.id === target);
      if (index < 0) throw { code: 'not_found', status: 404 };
      if (this.conversations[index].revision !== command.expected_revision)
        throw { code: 'revision_conflict', status: 409 };
      this.conversations.splice(index, 1);
      const deleted: wire.CommandReceipt = {
        command_id: command.command_id,
        conversation_id: target,
        status: 'DeleteCompleted',
      };
      this.receipts.set(command.command_id, deleted);
      return deleted;
    }
    const receipt: wire.CommandReceipt = {
      command_id: command.command_id,
      conversation_id: target,
      status: 'completed',
    };
    this.receipts.set(command.command_id, receipt);
    return receipt;
  }
  async receipt(
    id: string,
    signal?: AbortSignal,
  ): Promise<wire.CommandReceipt> {
    this.available(signal);
    const result = this.receipts.get(id);
    if (!result) throw { code: 'not_found', status: 404 };
    return result;
  }
  async profiles(
    query: string,
    scope?: string,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<wire.ProfilePage> {
    this.available(signal);
    const matching = this.profilesData.filter(
      (profile) =>
        (!scope || profile.scope === scope) &&
        `${profile.display_name} ${profile.slug} ${profile.description}`
          .toLowerCase()
          .includes(query.toLowerCase()),
    );
    const offset = cursor ? Number(cursor) : 0;
    return {
      schema_version: 1,
      scope: 'global',
      revision: 'a'.repeat(64),
      items: matching.slice(offset, offset + 50),
      total: matching.length,
      next_cursor: offset + 50 < matching.length ? String(offset + 50) : null,
    };
  }
  async profile(id: string, signal?: AbortSignal): Promise<wire.ProfileDetail> {
    this.available(signal);
    const profile = this.profilesData.find((item) => item.id === id);
    if (!profile) throw { code: 'not_found', status: 404 };
    return {
      schema_version: 1,
      profile: {
        ...profile,
        instruction_edit: {
          mode: 'replace_only',
          stored: profile.instructions_truncated,
        },
      },
    };
  }
  async profileInstructions(
    id: string,
    signal?: AbortSignal,
  ): Promise<wire.ProfileInstructions> {
    this.available(signal);
    const profile = this.profilesData.find((item) => item.id === id);
    if (!profile) throw { code: 'not_found', status: 404 };
    return {
      schema_version: 1,
      profile_id: id,
      revision: profile.revision,
      instructions: profile.instructions_truncated
        ? `Instructions for ${profile.display_name}.`
        : '',
      truncated: false,
    };
  }
  async reviewProfile(
    body: wire.ProfileCommandPayload,
    signal?: AbortSignal,
  ): Promise<wire.ProfileReview> {
    this.available(signal);
    return {
      ...body,
      schema_version: 1,
      changes: {},
      action_digest: 'b'.repeat(64),
      disclosures:
        body.operation === 'delete' ? ['This removes the profile.'] : [],
      review_id: 'c'.repeat(64),
    };
  }
  async executeProfile(
    command: wire.Command,
    signal?: AbortSignal,
  ): Promise<wire.ProfileReceipt> {
    this.available(signal);
    const previous = this.profileReceipts.get(command.command_id);
    if (previous) return previous;
    if (command.type !== 'profile.mutate')
      throw { code: 'invalid_command', status: 400 };
    const payload = command.payload as wire.ProfileCommandWirePayload;
    const index = this.profilesData.findIndex(
      (item) => item.id === payload.profile_id,
    );
    const original = this.profilesData[index];
    if (
      payload.operation !== 'create' &&
      (!original || (payload.operation !== 'duplicate' && !original.editable))
    )
      throw { code: 'invalid_command', status: 400 };
    let result: wire.ProfileSummary | null = null;
    if (payload.operation === 'create' && payload.fields) {
      const fields = payload.fields;
      result = {
        id: `fixture:${fields.slug}`,
        slug: fields.slug,
        display_name: fields.display_name,
        description: fields.description,
        when_to_use: fields.when_to_use,
        scope: 'user',
        surface_scope: 'global',
        source: 'user_created',
        group: '',
        icon: '',
        enabled: fields.enabled,
        editable: true,
        revision: '1',
        capability: fields.capability,
        allow_tools: fields.allow_tools,
        skills: fields.skills,
        context_mode: fields.context_mode,
        workspace_mode: fields.workspace_mode,
        approval_mode: fields.approval_mode,
        instructions_preview: '',
        instructions_truncated: Boolean(fields.instructions),
      };
      this.profilesData.push(result);
    } else if (payload.operation === 'duplicate' && original) {
      result = {
        ...original,
        id: `fixture:${payload.target_slug}`,
        slug: payload.target_slug!,
        display_name: payload.target_name!,
        source: 'user_created',
        scope: 'user',
        group: '',
        icon: '',
        editable: true,
        revision: '1',
      };
      this.profilesData.push(result);
    } else if (payload.operation === 'delete')
      this.profilesData.splice(index, 1);
    else if (original) {
      result = {
        ...original,
        enabled:
          payload.operation === 'enable'
            ? true
            : payload.operation === 'disable'
              ? false
              : original.enabled,
        ...(payload.operation === 'edit' && payload.fields
          ? {
              ...payload.fields,
              instructions_preview: '' as const,
              instructions_truncated:
                payload.fields.instructions === null
                  ? original.instructions_truncated
                  : Boolean(payload.fields.instructions),
            }
          : {}),
        revision: String(Number(original.revision) + 1),
      };
      this.profilesData[index] = result;
    }
    const receipt: wire.ProfileReceipt = {
      command_id: command.command_id,
      status: 'completed',
      operation: payload.operation,
      profile: result,
      profile_id: result?.id ?? payload.profile_id,
    };
    this.profileReceipts.set(command.command_id, receipt);
    return receipt;
  }
  async upload(
    _conversation: string,
    file: File,
    signal?: AbortSignal,
  ): Promise<wire.AttachmentView> {
    this.available(signal);
    if (file.size > 26214400) throw { code: 'payload_too_large' };
    return {
      ...recorded<wire.AttachmentView>('F-P07', 'AttachmentView')[0],
      name: file.name,
      size_bytes: file.size,
    };
  }
  async download(_reference: string, signal?: AbortSignal): Promise<Blob> {
    this.available(signal);
    return new Blob(['Synthetic fixture download']);
  }
  async attachmentMetadata(
    reference: string,
    signal?: AbortSignal,
  ): Promise<wire.AttachmentView> {
    this.available(signal);
    return {
      ...recorded<wire.AttachmentView>('F-P07', 'AttachmentView')[0],
      attachment_ref: reference,
    };
  }
  async terminalRead(
    _terminal: string,
    cursor: number,
    signal?: AbortSignal,
  ): Promise<wire.NativeTerminalOutput> {
    this.available(signal);
    return {
      cursor,
      latest: cursor,
      truncated: false,
      frames: [],
      status: 'running',
    };
  }
  async terminalInput(
    _terminal: string,
    _data: string,
    signal?: AbortSignal,
  ): Promise<wire.NativeTerminalChanged> {
    this.available(signal);
    return { ok: true };
  }
  async terminalResize(
    _terminal: string,
    _cols: number,
    _rows: number,
    signal?: AbortSignal,
  ): Promise<wire.NativeTerminalChanged> {
    this.available(signal);
    return { ok: true };
  }
  async terminalDisconnect(
    _terminal: string,
    signal?: AbortSignal,
  ): Promise<wire.NativeTerminalClosed> {
    this.available(signal);
    return { disconnected: true };
  }
  clearSession(_preserveResumeIdentity = false): void {
    /* No credential or durable data is retained by fixtures. */
  }
}
