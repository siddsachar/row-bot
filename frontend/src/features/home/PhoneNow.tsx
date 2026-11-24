import type { ReactNode } from 'react';
import { Bot, MessageSquare, Terminal, Workflow } from 'lucide-react';
import type { ConversationView, TaskSummary } from '../../api/types';
import { relativeTime } from '../../ui/format';
import { agentWorkLabel } from '../shell/agent-activity';
import {
  durationWords,
  processLive,
  processName,
  runningSeconds,
  useBackgroundProcesses,
  type ProcessIo,
} from '../shell/background-processes';
import { waitingOnYou } from '../shell/needs';
import { plural } from './home-format';

/** One row of Now: what runs, in words, and the place it opens. */
export type NowItem = {
  key: string;
  icon: ReactNode;
  tone: 'accent' | '2' | '3' | '4';
  title: string;
  meta: string;
  label: string;
  onOpen: () => void;
};

/** A reply is being written, or its agents are at work. */
export function working(row: ConversationView): boolean {
  return (
    row.activity_state === 'active' ||
    Boolean(
      row.generation_state?.some(
        (state) => state.status === 'running' || state.status === 'stopping',
      ),
    )
  );
}

function writing(row: ConversationView) {
  return Boolean(
    row.generation_state?.some(
      (state) => state.status === 'running' && !state.quiesced,
    ),
  );
}

const titleOf = (row: ConversationView) => row.title || 'Untitled conversation';

/**
 * What runs right now, for the phone's Home (D2): chats writing a reply or
 * pursuing a goal, their agents, workflow runs and background processes, each
 * opening its chat or workflow. Only data Home already reads: the listed
 * conversations, the workflows, and the processes Monitor lists (read while
 * `processes` is given).
 */
export function usePhoneNow({
  conversations,
  tasks,
  processes,
  onOpenConversation,
  onOpenWorkflows,
  now,
}: {
  conversations: readonly ConversationView[];
  tasks: readonly TaskSummary[] | null;
  processes: ProcessIo | null;
  onOpenConversation: (id: string) => void;
  onOpenWorkflows: (taskId?: string) => void;
  now: Date;
}): NowItem[] {
  const feed = useBackgroundProcesses(processes);
  const busy = conversations.filter(
    (row) => working(row) && !waitingOnYou(row),
  );
  const ids = new Set(busy.map((row) => row.id));
  const helpers = (id: string) =>
    busy.filter((row) => row.parent_conversation_id === id).length;
  const chats = busy
    // A helper shows under its chat; one whose chat is quiet shows alone.
    .filter(
      (row) =>
        !row.parent_conversation_id || !ids.has(row.parent_conversation_id),
    )
    .map((row): NowItem => {
      const agents = helpers(row.id);
      const doing = row.parent_conversation_id
        ? 'Agent working'
        : writing(row)
          ? 'Writing a reply'
          : agentWorkLabel(row.activity_phase);
      return {
        key: `chat:${row.id}`,
        icon: row.parent_conversation_id ? (
          <Bot size={16} />
        ) : (
          <MessageSquare size={16} />
        ),
        tone: row.parent_conversation_id ? '3' : 'accent',
        title: titleOf(row),
        meta: agents ? `${doing} · ${plural(agents, 'agent')} working` : doing,
        label: `Open ${titleOf(row)}`,
        onOpen: () => onOpenConversation(row.id),
      };
    });
  const runs = (tasks ?? [])
    .filter((task) => task.active_run)
    .map((task): NowItem => {
      const run = task.active_run!;
      const steps =
        run.steps_total > 1
          ? ` · step ${Math.min(run.steps_done + 1, run.steps_total)} of ${run.steps_total}`
          : '';
      return {
        key: `run:${task.id}`,
        icon: <Workflow size={16} />,
        tone: '4',
        title: task.name,
        meta: `Workflow running${steps} · started ${relativeTime(run.started_at, now)}`,
        label: `Open ${task.name}`,
        onOpen: () => onOpenWorkflows(task.id),
      };
    });
  const running = feed.items.filter(processLive).map((process): NowItem => {
    const name = processName(process);
    return {
      key: `process:${process.process_id}`,
      icon: <Terminal size={16} />,
      tone: '2',
      title: name,
      meta: `${process.conversation_title || 'A conversation'} · running ${durationWords(
        runningSeconds(process, feed.readAt, now.getTime()),
      )}`,
      label: `Open ${process.conversation_title || 'its conversation'}: ${name}`,
      onOpen: () => onOpenConversation(process.conversation_id),
    };
  });
  return [...chats, ...runs, ...running];
}
