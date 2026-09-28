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
};

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
