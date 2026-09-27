import type {
  ClientState,
  ConversationView,
  TranscriptRow,
} from '../api/types';
import type { BuddySnapshot } from '../features/shell/BuddyControls';
import { keyArgument, stepVerb } from '../features/shell/tool-activity';

type Block = TranscriptRow['blocks'][number];

/**
 * A compact plain-text reading of Markdown for the 380×230 desktop Buddy:
 * images and links keep their words, fences, headings, list markers, tags and
 * emphasis marks go. Nothing is rendered as HTML.
 */
export function plainText(value: string): string {
  let text = value
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^[ \t]*```[^\n]*$/gm, '')
    .replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, '')
    .replace(/^[ \t]*(?:[-+*]|\d+[.)])[ \t]+/gm, '')
    .replace(/<[^>]+>/g, '')
    .replace(/`/g, '')
    .replace(/\*\*|__/g, '');
  text = text.replace(/\r\n?/g, '\n');
  const lines = text
    .split('\n')
    .map((line) => line.replace(/[ \t]+/g, ' ').trim());
  const compact: string[] = [];
  for (const line of lines)
    if (line || (compact.length && compact[compact.length - 1]))
      compact.push(line);
  return compact.join('\n').trim();
}

function blockText(block: Block): string {
  switch (block.type) {
    case 'text':
    case 'markdown':
      return block.text;
    case 'chart':
      return block.text || 'Chart';
    case 'mermaid':
      return 'Diagram';
    case 'youtube':
      return block.title;
    case 'attachment':
      return block.name;
  }
}

export function rowText(row: TranscriptRow): string {
  return plainText(row.blocks.map(blockText).filter(Boolean).join('\n'));
}

/** The latest assistant words, and whether they belong to the latest turn. */
export function latestResponse(rows: readonly TranscriptRow[]): {
  text: string;
  current: boolean;
} {
  let afterUser = true;
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const row = rows[index];
    if (row.role === 'user') afterUser = false;
    if (row.role !== 'assistant') continue;
    const text = rowText(row);
    if (text) return { text, current: afterUser };
  }
  return { text: '', current: false };
}

export type OverlayPhase =
  | 'idle'
  | 'thinking'
  | 'tool'
  | 'streaming'
  | 'approval'
  | 'stopping'
  | 'stopped'
  | 'interrupted'
  | 'failed'
  | 'completed';

type Activity = ClientState['activity'];

/** The step the run is on, in words: "Searching the web · weather in Oslo". */
export function progressLabel(activity: Activity): string {
  for (let index = activity.length - 1; index >= 0; index -= 1) {
    const event = activity[index].event;
    if (event.type === 'generation.activity') return 'Thinking…';
    if (event.type !== 'tool.activity') continue;
    const status = event.payload.status ?? 'pending';
    if (status !== 'pending') return 'Thinking…';
    const name = event.payload.tool_name || event.payload.group_name || 'tool';
    const argument = keyArgument(event.payload.safe_input);
    return argument
      ? `${stepVerb(name, 'pending')} · ${argument}`
      : `${stepVerb(name, 'pending')}…`;
  }
  return 'Thinking…';
}

export function overlayPhase(
  state: Pick<ClientState, 'projection' | 'activity'>,
  failedGeneration: string | null,
): OverlayPhase {
  const generation = state.projection?.generation;
  if (!generation) return 'idle';
  if (generation.status === 'waiting_approval' && generation.approval_id)
    return 'approval';
  if (generation.status === 'stopping') return 'stopping';
  const running = !generation.quiesced;
  if (running) {
    const last = state.activity.at(-1)?.event;
    if (
      last?.type === 'tool.activity' &&
      (last.payload.status ?? 'pending') === 'pending'
    )
      return 'tool';
    return latestResponse(state.projection?.rows ?? []).current
      ? 'streaming'
      : 'thinking';
  }
  if (generation.status === 'interrupted') return 'interrupted';
  if (failedGeneration && failedGeneration === generation.generation_id)
    return 'failed';
  if (generation.status === 'stopped') return 'stopped';
  if (generation.status === 'completed') return 'completed';
  return 'idle';
}

/** The avatar mirrors the sidebar Buddy's activity mapping. */
export function avatarActivity(
  phase: OverlayPhase,
  connected: boolean,
): NonNullable<BuddySnapshot['activity']> {
  if (!connected) return 'disconnected';
  switch (phase) {
    case 'thinking':
      return 'thinking';
    case 'tool':
      return 'tool';
    case 'streaming':
      return 'streaming';
    case 'approval':
      return 'approval';
    case 'stopping':
      return 'stopping';
    case 'stopped':
    case 'interrupted':
      return 'stopped';
    case 'failed':
      return 'error';
    case 'completed':
      return 'completed';
    default:
      return 'idle';
  }
}

export function kindLabel(
  category: ConversationView['category'] | undefined,
): string {
  return (
    { chat: 'Chat', designer: 'Design', code: 'Code', workflow: 'Workflow' }[
      category ?? 'chat'
    ] ?? 'Chat'
  );
}

export function phaseLabel(phase: OverlayPhase): string {
  return {
    idle: 'Ready',
    thinking: 'Thinking…',
    tool: 'Working…',
    streaming: 'Responding…',
    approval: 'Waiting for approval',
    stopping: 'Stopping…',
    stopped: 'Stopped',
    interrupted: 'Interrupted',
    failed: 'Could not finish',
    completed: 'Ready',
  }[phase];
}

export const isLive = (phase: OverlayPhase) =>
  phase === 'thinking' ||
  phase === 'tool' ||
  phase === 'streaming' ||
  phase === 'stopping';
