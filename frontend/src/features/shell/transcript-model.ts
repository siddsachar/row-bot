import type {
  EventRecord,
  ToolActivity,
  TraceMediaReference,
  TranscriptRow,
  TranscriptTraceGroup,
  TranscriptTraceItem,
} from '../../api/types';
import { publicBlockText } from './TranscriptBlocks';
import type { GeneratedMedia } from './TranscriptMessage';
import { cardKey, tracedCards, type TranscriptCard } from './TranscriptCards';

export type TranscriptItem = {
  row: TranscriptRow;
  traces: TranscriptTraceGroup[];
  /** Chart blocks from folded tool results (B4). */
  embeds: TranscriptRow['blocks'];
  /** Generated media from this turn's tools, rendered once (B22). */
  media: GeneratedMedia[];
  /** Designs and code folders the turn created, connections it needs. */
  cards: TranscriptCard[];
  /** Earlier versions of the reply's newest video, folded under it. */
  earlier?: TranscriptCard[];
  /** Agents the turn started, one stub each (B241). */
  agents: TracedAgent[];
};

/** An agent a turn started, as the turn stored it (B241). */
export type TracedAgent = {
  run_id: string;
  name: string;
  /** Its status when the turn recorded it; the live feed supersedes it. */
  status: string;
  profile_id: string;
};

/** The tools that start agents; the others only look at existing ones. */
const STARTS_AGENTS = new Set(['delegate_work', 'agent_retry']);

/** The agents a turn started, once each, in the order it started them. */
export function tracedAgents(groups: TranscriptTraceGroup[]): TracedAgent[] {
  const agents = new Map<string, TracedAgent>();
  for (const group of groups)
    for (const item of group.items) {
      if (
        item.specialization?.kind !== 'delegated_agent' ||
        !STARTS_AGENTS.has(item.canonical_name)
      )
        continue;
      for (const run of item.specialization.agent_runs ?? [])
        agents.set(run.run_id, {
          run_id: run.run_id,
          name: run.display_name,
          status: run.status,
          profile_id: run.profile_id ?? '',
        });
    }
  return [...agents.values()];
}

/**
 * Where a speaker's turn begins (B271): a person's message after anything
 * else, the first reply after it. Follow-up rows of the same turn (more
 * activity, the next block of the reply) do not begin one.
 */
export function turnStarts(items: readonly TranscriptItem[]): boolean[] {
  return items.map((item, index) => {
    const previous = items[index - 1];
    return (
      !previous ||
      previous.row.role !== item.row.role ||
      Boolean(previous.row.note)
    );
  });
}

/**
 * Which rows offer Row-Bot's browser pane (U16): each reply that used the
 * browser offers it once, at its last row that did.
 */
export function browserOffers(items: readonly TranscriptItem[]): boolean[] {
  const offers = items.map(() => false);
  let last = -1;
  items.forEach((item, index) => {
    if (item.row.role !== 'assistant') {
      last = -1;
      return;
    }
    if (item.traces.some((group) => group.kind === 'browser')) {
      if (last >= 0) offers[last] = false;
      offers[index] = true;
      last = index;
    }
  });
  return offers;
}

function hasContent(row: TranscriptRow) {
  return (
    Boolean(row.content_ref && row.content_status === 'lazy') ||
    row.blocks.some(
      (block) =>
        (block.type !== 'text' && block.type !== 'markdown') ||
        Boolean(publicBlockText(block).trim()),
    )
  );
}

function prompt(safeInput: string | undefined) {
  if (!safeInput) return undefined;
  try {
    const value: unknown = JSON.parse(safeInput);
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const record = value as Record<string, unknown>;
      for (const key of ['prompt', 'description', 'instruction'])
        if (typeof record[key] === 'string' && record[key])
          return (record[key] as string).slice(0, 400);
    }
  } catch {
    /* A bounded non-JSON input has no prompt field. */
  }
  return undefined;
}

export function tracedMedia(groups: TranscriptTraceGroup[]): GeneratedMedia[] {
  const seen = new Set<string>();
  const media: GeneratedMedia[] = [];
  for (const group of groups)
    for (const item of group.items)
      for (const value of item.specialization?.media ?? []) {
        if (seen.has(value.media_ref)) continue;
        seen.add(value.media_ref);
        media.push({
          reference: value.media_ref,
          mime: value.mime_type,
          // The prompt the server kept names the picture and its download (T9).
          caption: value.prompt || prompt(item.safe_input),
          // A picture of Row-Bot's browser (F4), or the contact sheet Row-Bot
          // looked at (F5): not something generated.
          ...(item.canonical_name === 'Browser Screenshot'
            ? { label: 'Page screenshot' }
            : item.canonical_name === 'analyze_image'
              ? { label: 'Contact sheet' }
              : {}),
        });
      }
  return media;
}

/** A step that finished in the live turn, as its latest `tool.activity` says. */
export type FinishedStep = Pick<
  TranscriptTraceItem,
  'status' | 'safe_summary' | 'summary_truncated' | 'content_ref'
> & { specialization?: TranscriptTraceItem['specialization'] };

/** Steps the live turn finished: Row-Bot's question answered after its turn paused (N4), an approved action. */
export function finishedLiveSteps(
  activity: readonly EventRecord[],
): Map<string, FinishedStep> {
  const finished = new Map<string, FinishedStep>();
  for (const record of activity) {
    if (record.event.type !== 'tool.activity') continue;
    const value = record.event.payload;
    if (
      value.state !== 'tool_done' ||
      !value.tool_call_id ||
      !value.status ||
      value.status === 'pending'
    )
      continue;
    finished.set(value.tool_call_id, {
      status: value.status,
      safe_summary: value.safe_summary ?? '',
      summary_truncated: value.summary_truncated ?? false,
      content_ref: value.content_ref ?? '',
      ...(value.specialization ? { specialization: value.specialization } : {}),
    });
  }
  return finished;
}

const NO_BLOCKS: TranscriptRow['blocks'] = [];

/** The model step a live row streams: `assistant:live:<pass>:<segment>`. */
function liveSegment(row: TranscriptRow) {
  return row.id.startsWith('assistant:live:')
    ? row.id.slice(row.id.lastIndexOf(':') + 1)
    : '';
}

type Specialization = NonNullable<TranscriptTraceItem['specialization']>;

/** A step's specialization with the media it made added, once each. */
function withMedia(
  specialization: Specialization | null | undefined,
  media: readonly TraceMediaReference[] | undefined,
): Specialization | undefined {
  const known = specialization?.media ?? [];
  const added = (media ?? []).filter(
    (value) => !known.some((item) => item.media_ref === value.media_ref),
  );
  if (!added.length) return specialization ?? undefined;
  return {
    ...(specialization ?? { kind: 'media' }),
    media_kind: specialization?.media_kind || 'attachment',
    media: [...known, ...added],
  };
}

/**
 * A saved step read in before its result came (a re-read mid-turn) shows the
 * media the live turn says it made, in its own place.
 */
function withLiveMedia(
  rows: readonly TranscriptRow[],
  made: ReadonlyMap<string, TraceMediaReference[]>,
): readonly TranscriptRow[] {
  if (!made.size) return rows;
  const missing = (item: TranscriptTraceItem) =>
    withMedia(item.specialization, made.get(item.call_id)) !==
    item.specialization;
  let changed = false;
  const next = rows.map((row) => {
    if (!row.traces?.some((group) => group.items.some(missing))) return row;
    changed = true;
    return {
      ...row,
      traces: row.traces.map((group) =>
        group.items.some(missing)
          ? {
              ...group,
              items: group.items.map((item) =>
                missing(item)
                  ? {
                      ...item,
                      specialization: withMedia(
                        item.specialization,
                        made.get(item.call_id),
                      ),
                    }
                  : item,
              ),
            }
          : group,
      ),
    };
  });
  return changed ? next : rows;
}

/** The rows without the saved, still pending items of these calls; the same rows when none is there. */
function withoutSteps(
  rows: readonly TranscriptRow[],
  calls: ReadonlySet<string>,
): readonly TranscriptRow[] {
  if (!calls.size) return rows;
  let changed = false;
  const next = rows.map((row) => {
    if (
      !row.traces?.some((group) =>
        group.items.some(
          (item) => item.status === 'pending' && calls.has(item.call_id),
        ),
      )
    )
      return row;
    changed = true;
    return {
      ...row,
      traces: (row.traces ?? [])
        .map((group) =>
          withItems(
            group,
            group.items.filter(
              (item) => !(item.status === 'pending' && calls.has(item.call_id)),
            ),
          ),
        )
        .filter((group) => group.items.length),
    };
  });
  return changed ? next : rows;
}

/** A live tool step as one trace group, as the saved row will show it. */
function liveGroup(
  record: EventRecord,
  order: number,
  media: TraceMediaReference[] | undefined,
): TranscriptTraceGroup {
  const value = record.event.payload as ToolActivity;
  const status = value.status ?? 'pending';
  // Its media and cards with it, as its saved step carries them (B22).
  const specialization = withMedia(value.specialization, media);
  return {
    group_id: value.group_id || value.tool_call_id || record.event.event_id,
    name: value.group_name || value.tool_name || 'tool',
    kind: value.group_kind ?? 'generic',
    group_order: order,
    status,
    counts: { [status]: 1 },
    items: [
      {
        item_id: value.item_id || value.tool_call_id || record.event.event_id,
        group_id: value.group_id || value.tool_call_id || record.event.event_id,
        call_id: value.tool_call_id || record.event.event_id,
        result_message_id: value.message_id ?? '',
        call_order: 0,
        group_order: order,
        canonical_name: value.tool_name || 'tool',
        group_name: value.group_name || value.tool_name || 'tool',
        group_kind: value.group_kind ?? 'generic',
        status,
        safe_input: value.safe_input ?? '',
        safe_summary: value.safe_summary ?? '',
        summary_truncated: value.summary_truncated ?? false,
        content_ref: value.content_ref ?? '',
        ...(value.app ? { app: value.app } : {}),
        ...(status !== 'pending' && specialization ? { specialization } : {}),
      },
    ],
  };
}

export type LiveTurn = {
  /** The rows, with each finished live step under the model step that took it. */
  rows: readonly TranscriptRow[];
  /** Steps still running, or with no model step to sit under: the turn's live line. */
  running: TranscriptTraceGroup[];
  /** Every live step, wherever it shows. */
  all: TranscriptTraceGroup[];
};

/**
 * The turn in flight in the order it happened (5.3.3): each model step's
 * words, then the tool steps it took with what they made, then the next
 * step's words, as the saved turn reads after a reload. A step that wrote no
 * words gets a row of its own, between the steps around it. Steps still
 * running stay on the turn's live line at its end, the newest thing it does.
 */
export function liveTurn(
  saved: readonly TranscriptRow[],
  activity: readonly EventRecord[],
  turnRunning = false,
): LiveTurn {
  // Each step's latest event, in the order the steps began.
  const steps = new Map<string, { record: EventRecord; began: bigint }>();
  const made = new Map<string, TraceMediaReference[]>();
  for (const record of activity) {
    const event = record.event;
    if (event.type === 'tool.activity') {
      const key = event.payload.tool_call_id ?? event.event_id;
      steps.set(key, {
        record,
        began:
          steps.get(key)?.began ?? BigInt(event.projection_revision || '0'),
      });
    } else if (event.type === 'media.available' && event.payload.tool_call_id) {
      const known = made.get(event.payload.tool_call_id) ?? [];
      if (!known.some((media) => media.media_ref === event.payload.media_ref))
        made.set(event.payload.tool_call_id, [
          ...known,
          {
            media_ref: event.payload.media_ref,
            mime_type: event.payload.mime_type,
            ...(event.payload.prompt ? { prompt: event.payload.prompt } : {}),
          },
        ]);
    }
  }
  // A step the chat re-read mid-step is saved before it finishes: while it
  // still runs it stays on the live line. Under its words it read as done, and
  // the live line, left with nothing, said "Thinking…" (I31).
  const inFlight = new Set(
    [...steps]
      .filter(
        ([, { record }]) =>
          record.event.type === 'tool.activity' &&
          (record.event.payload.status ?? 'pending') === 'pending',
      )
      .map(([key]) => key),
  );
  // A step saved while it ran whose live event the chat no longer holds
  // still runs too: it stays on the live line, not under its words with
  // "Thinking…" below (R15).
  const turnStart = saved.map((row) => row.role).lastIndexOf('user');
  const savedRunning = turnRunning
    ? saved.slice(turnStart + 1).flatMap((row) =>
        (row.traces ?? []).flatMap((group) => {
          const items = group.items.filter(
            (item) => item.status === 'pending' && !steps.has(item.call_id),
          );
          return items.length ? [withItems(group, items)] : [];
        }),
      )
    : [];
  for (const group of savedRunning)
    for (const item of group.items) inFlight.add(item.call_id);
  const rows = withoutSteps(withLiveMedia(saved, made), inFlight);
  const settled = new Set(
    rows.flatMap((row) =>
      (row.traces ?? []).flatMap((group) =>
        group.items.map((item) => item.call_id),
      ),
    ),
  );
  const all: TranscriptTraceGroup[] = [...savedRunning];
  const running: TranscriptTraceGroup[] = [...savedRunning];
  const placed = new Map<
    string,
    { groups: TranscriptTraceGroup[]; began: bigint }
  >();
  for (const [key, { record, began }] of steps) {
    if (settled.has(key) || record.event.type !== 'tool.activity') continue;
    const value = record.event.payload;
    const group = liveGroup(record, all.length, made.get(key));
    all.push(group);
    if (group.status === 'pending' || !value.segment_id) {
      running.push(group);
      continue;
    }
    const step = placed.get(value.segment_id);
    if (step) step.groups.push(group);
    else placed.set(value.segment_id, { groups: [group], began });
  }
  if (!placed.size) return { rows, running, all };
  // A live row's words end before its own steps begin and after the steps
  // of the model steps before it: the projection revisions order them.
  const slots: { at: bigint; row: TranscriptRow }[] = [];
  const others: TranscriptRow[] = [];
  let first = -1;
  rows.forEach((row) => {
    const segment = liveSegment(row);
    if (!segment) {
      others.push(row);
      return;
    }
    if (first < 0) first = others.length;
    const step = placed.get(segment);
    placed.delete(segment);
    slots.push({
      at: BigInt(row.render_revision || '0'),
      row: step ? { ...row, traces: step.groups } : row,
    });
  });
  for (const [segment, step] of placed)
    slots.push({
      at: step.began,
      row: {
        id: `assistant:live-step:${segment}`,
        role: 'assistant',
        blocks: NO_BLOCKS,
        traces: step.groups,
      },
    });
  slots.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  // Tool steps at the turn's end that said nothing and made nothing to show
  // stay on its live line, one group with the step in progress, until words
  // or a picture follow them.
  for (let last = slots.at(-1); last; last = slots.at(-1)) {
    const traces = last.row.traces ?? [];
    if (
      liveSegment(last.row) ||
      tracedMedia(traces).length ||
      tracedCards(traces).length
    )
      break;
    running.unshift(...traces);
    slots.pop();
  }
  const at = first < 0 ? others.length : first;
  return {
    rows: [
      ...others.slice(0, at),
      ...slots.map((slot) => slot.row),
      ...others.slice(at),
    ],
    running,
    all,
  };
}

const GROUP_ORDER = [
  'uncertain',
  'blocked',
  'failed',
  'cancelled',
  'pending',
] as const;

/** A trace group with these items, its counts and status worked out again. */
function withItems(
  group: TranscriptTraceGroup,
  items: TranscriptTraceGroup['items'],
): TranscriptTraceGroup {
  const counts: TranscriptTraceGroup['counts'] = {};
  for (const item of items)
    counts[item.status] = (counts[item.status] ?? 0) + 1;
  const status: TranscriptTraceItem['status'] =
    GROUP_ORDER.find((value) => counts[value]) ?? 'succeeded';
  return { ...group, items, counts, status };
}

/**
 * Saved rows read as the live turn left them: a step still pending in its
 * saved row but finished live reads as finished in its own place, with its
 * card (so an answered question shows "You chose: …" where it was asked,
 * before the reply that follows), until the saved row catches up.
 */
export function settleFinishedSteps(
  rows: readonly TranscriptRow[],
  finished: ReadonlyMap<string, FinishedStep>,
): readonly TranscriptRow[] {
  if (!finished.size) return rows;
  let changed = false;
  const settled = rows.map((row) => {
    const traces = row.traces ?? [];
    if (
      !traces.some((group) =>
        group.items.some(
          (item) => item.status === 'pending' && finished.has(item.call_id),
        ),
      )
    )
      return row;
    changed = true;
    return {
      ...row,
      traces: traces.map((group) => {
        return withItems(
          group,
          group.items.map((item) =>
            item.status === 'pending' && finished.has(item.call_id)
              ? { ...item, ...finished.get(item.call_id)! }
              : item,
          ),
        );
      }),
    };
  });
  return changed ? settled : rows;
}

/**
 * Display items for a transcript: tool result rows fold into their parent,
 * their charts and generated media hoist onto that parent, and consecutive
 * assistant rows that only called tools merge into one activity row.
 */
export function buildTranscript(
  rows: readonly TranscriptRow[],
): TranscriptItem[] {
  const charts = new Map<string, TranscriptRow['blocks']>();
  for (const row of rows) {
    if (row.role !== 'tool' || !row.trace_parent_id) continue;
    const blocks = row.blocks.filter((block) => block.type === 'chart');
    if (blocks.length)
      charts.set(row.trace_parent_id, [
        ...(charts.get(row.trace_parent_id) ?? []),
        ...blocks,
      ]);
  }
  const items: TranscriptItem[] = [];
  for (const row of rows) {
    if (row.role === 'tool' && row.trace_parent_id) continue;
    const traces = row.traces ?? [];
    const item: TranscriptItem = {
      row,
      traces,
      embeds: charts.get(row.id) ?? [],
      media: tracedMedia(traces),
      cards: tracedCards(traces),
      agents: tracedAgents(traces),
    };
    const content = hasContent(row);
    const previous = items.at(-1);
    if (
      row.role === 'assistant' &&
      !content &&
      previous?.row.role === 'assistant' &&
      !hasContent(previous.row)
    ) {
      previous.traces = [...previous.traces, ...item.traces];
      previous.embeds = [...previous.embeds, ...item.embeds];
      previous.media = [
        ...previous.media,
        ...item.media.filter(
          (media) =>
            !previous.media.some(
              (known) => known.reference === media.reference,
            ),
        ),
      ];
      previous.cards = [
        ...previous.cards,
        ...item.cards.filter(
          (card) =>
            !previous.cards.some((known) => cardKey(known) === cardKey(card)),
        ),
      ];
      previous.agents = tracedAgents(previous.traces);
      continue;
    }
    if (
      !content &&
      !traces.length &&
      !item.embeds.length &&
      !item.cards.length &&
      row.role !== 'user'
    )
      continue;
    items.push(item);
  }
  return foldEarlierVideos(items);
}

const PARTS = [
  'traces',
  'embeds',
  'media',
  'cards',
  'earlier',
  'agents',
] as const;

function sameItem(a: TranscriptItem, b: TranscriptItem) {
  return (
    (a.row === b.row ||
      (a.row.blocks === b.row.blocks &&
        JSON.stringify({ ...a.row, blocks: null }) ===
          JSON.stringify({ ...b.row, blocks: null }))) &&
    PARTS.every(
      (part) =>
        a[part] === b[part] ||
        JSON.stringify(a[part]) === JSON.stringify(b[part]),
    )
  );
}

/**
 * Items that read the same as before keep their identity, so a token
 * streaming into the newest step renders that step alone, never every
 * earlier step's media again.
 */
export function keepUnchanged(
  previous: readonly TranscriptItem[],
  next: TranscriptItem[],
): TranscriptItem[] {
  const known = new Map(previous.map((item) => [item.row.id, item]));
  return next.map((item) => {
    const before = known.get(item.row.id);
    return before && sameItem(before, item) ? before : item;
  });
}

/** A video a reply made: a recording, an edit (not stills) or a video project. */
export function isVideoVersion(card: TranscriptCard) {
  return (
    card.kind === 'recording' ||
    card.kind === 'video_project' ||
    (card.kind === 'video_edit' && !card.frames)
  );
}

/**
 * Per reply, only the newest video shows in full: the earlier versions (the raw
 * recording, a trim, a zoom) fold under it, in the reply's last item that has
 * a video. The same reply reads the same live and after a reload.
 */
export function foldEarlierVideos(items: TranscriptItem[]): TranscriptItem[] {
  const folded = [...items];
  let start = 0;
  for (let index = 0; index <= items.length; index++) {
    if (index < items.length && items[index].row.role !== 'user') continue;
    const videos = folded
      .slice(start, index)
      .flatMap((item) => item.cards.filter(isVideoVersion));
    if (videos.length > 1) {
      const newest = videos.at(-1)!;
      const earlier = videos.slice(0, -1);
      for (let at = start; at < index; at++) {
        const item = folded[at];
        const holdsNewest = item.cards.includes(newest);
        if (!holdsNewest && !item.cards.some((card) => earlier.includes(card)))
          continue;
        folded[at] = {
          ...item,
          cards: item.cards.filter((card) => !earlier.includes(card)),
          ...(holdsNewest ? { earlier } : {}),
        };
      }
    }
    start = index + 1;
  }
  return folded;
}

/**
 * The turn in flight: when its live cards bring a newer video, the videos its
 * saved items already show fold under that one.
 */
export function foldLiveVideos(
  items: TranscriptItem[],
  pending: TranscriptCard[],
): {
  items: TranscriptItem[];
  pending: TranscriptCard[];
  earlier: TranscriptCard[];
} {
  const live = pending.filter(isVideoVersion);
  if (!live.length) return { items, pending, earlier: [] };
  const newest = live.at(-1)!;
  const turn = items.map((item) => item.row.role).lastIndexOf('user') + 1;
  const saved = items
    .slice(turn)
    .flatMap((item) => [
      ...(item.earlier ?? []),
      ...item.cards.filter(isVideoVersion),
    ]);
  return {
    items: saved.length
      ? items.map((item, index) =>
          index < turn ||
          (!item.earlier?.length && !item.cards.some(isVideoVersion))
            ? item
            : {
                ...item,
                cards: item.cards.filter((card) => !isVideoVersion(card)),
                earlier: undefined,
              },
        )
      : items,
    pending: pending.filter((card) => !isVideoVersion(card) || card === newest),
    earlier: [...saved, ...live.slice(0, -1)],
  };
}

/** Media announced live that no settled row renders yet. */
export function liveMedia(
  activity: readonly EventRecord[],
  settled: ReadonlySet<string>,
): GeneratedMedia[] {
  const seen = new Set<string>();
  const media: GeneratedMedia[] = [];
  for (const record of activity) {
    if (record.event.type !== 'media.available') continue;
    const reference = record.event.payload.media_ref;
    if (settled.has(reference) || seen.has(reference)) continue;
    seen.add(reference);
    const caption = record.event.payload.prompt;
    media.push({
      reference,
      mime: record.event.payload.mime_type,
      ...(caption ? { caption } : {}),
    });
  }
  return media;
}

/**
 * True while answer text is the newest thing the turn produced: its live row
 * changed after the last tool or reasoning event (B233).
 */
export function answerStreaming(
  rows: readonly TranscriptRow[],
  activity: readonly EventRecord[],
): boolean {
  const last = rows.at(-1);
  if (!last?.id.startsWith('assistant:live:') || !last.render_revision)
    return false;
  if (!hasContent(last)) return false;
  const latest = [...activity]
    .reverse()
    .find(
      (record) =>
        record.event.type === 'tool.activity' ||
        record.event.type === 'generation.activity',
    );
  return (
    !latest ||
    BigInt(last.render_revision) > BigInt(latest.event.projection_revision)
  );
}
