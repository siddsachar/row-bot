import type {
  EventRecord,
  TranscriptRow,
  TranscriptTraceGroup,
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
        });
      }
  return media;
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
  return items;
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
