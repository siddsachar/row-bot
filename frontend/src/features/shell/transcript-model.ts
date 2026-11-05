import type {
  EventRecord,
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
          caption: prompt(item.safe_input),
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

const GROUP_ORDER = [
  'uncertain',
  'blocked',
  'failed',
  'cancelled',
  'pending',
] as const;

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
        const items = group.items.map((item) =>
          item.status === 'pending' && finished.has(item.call_id)
            ? { ...item, ...finished.get(item.call_id)! }
            : item,
        );
        const counts: TranscriptTraceGroup['counts'] = {};
        for (const item of items)
          counts[item.status] = (counts[item.status] ?? 0) + 1;
        const status: TranscriptTraceItem['status'] =
          GROUP_ORDER.find((value) => counts[value]) ?? 'succeeded';
        return { ...group, items, counts, status };
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
    media.push({ reference, mime: record.event.payload.mime_type });
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
