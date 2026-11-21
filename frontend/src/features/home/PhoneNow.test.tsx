import { renderHook, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type {
  BackgroundProcessView,
  ConversationView,
  GenerationState,
  TaskSummary,
} from '../../api/types';
import { usePhoneNow } from './PhoneNow';

const now = new Date(2026, 8, 26, 9, 30, 0);

function chat(
  id: string,
  title: string,
  extra: Partial<ConversationView> = {},
): ConversationView {
  return { id, revision: 'r', title, pinned: false, ...extra };
}

function generation(status: GenerationState['status']): GenerationState {
  return {
    execution_id: 'e',
    conversation_id: 'c',
    generation_id: 'g',
    pass_id: 'p',
    status,
    revision: 'r',
    cancel_requested: false,
    quiesced: false,
    cleanup_complete: false,
    external_outcome: 'not_applicable',
    approval_id: null,
    can_stop: true,
  };
}

function workflow(extra: Partial<TaskSummary> = {}): TaskSummary {
  return {
    id: 'wf-summary',
    name: 'Launch Summary',
    description: '',
    icon: '',
    enabled: true,
    notify_only: false,
    step_count: 3,
    schedule: null,
    at: null,
    last_run: null,
    last_status: null,
    conversation_id: null,
    agent_profile_id: 'builtin:row_bot_default',
    approval_mode: 'block',
    ...extra,
  };
}

function process(
  extra: Partial<BackgroundProcessView> = {},
): BackgroundProcessView {
  return {
    process_id: 'proc-dev',
    conversation_id: 'chat-site',
    conversation_title: 'Landing page',
    command: 'npm run dev',
    name: 'Dev server',
    folder: '',
    kind: 'process',
    status: 'running',
    exit_code: null,
    started_at: '',
    ended_at: null,
    runtime_seconds: 125,
    local_urls: [],
    pid: 1,
    stopped_by: '',
    env_names: [],
    output_size: 0,
    ...extra,
  };
}

function nowRows({
  conversations = [] as ConversationView[],
  tasks = [] as TaskSummary[],
  processes = [] as BackgroundProcessView[],
} = {}) {
  const onOpenConversation = vi.fn();
  const onOpenWorkflows = vi.fn();
  const io = processes.length
    ? {
        load: vi.fn().mockResolvedValue({
          items: processes,
          running: processes.length,
          limit_per_conversation: 5,
          limit_total: 20,
        }),
        output: vi.fn(),
        stop: vi.fn(),
      }
    : null;
  const hook = renderHook(() =>
    usePhoneNow({
      conversations,
      tasks,
      processes: io,
      onOpenConversation,
      onOpenWorkflows,
      now,
    }),
  );
  return { hook, onOpenConversation, onOpenWorkflows };
}

it('lists chats writing a reply or with agents at work, never one waiting on the person', () => {
  const { hook, onOpenConversation } = nowRows({
    conversations: [
      chat('chat-launch', 'Demo launch checklist', {
        generation_state: [generation('running')],
      }),
      chat('helper-1', 'Research', {
        parent_conversation_id: 'chat-launch',
        activity_state: 'active',
      }),
      chat('helper-2', 'Draft', {
        parent_conversation_id: 'chat-launch',
        activity_state: 'active',
      }),
      chat('chat-agents', 'Trip plan', {
        activity_state: 'active',
        activity_phase: 'background',
      }),
      chat('chat-waiting', 'Release notes', {
        generation_state: [generation('waiting_approval')],
      }),
      chat('chat-quiet', 'Old chat'),
    ],
  });
  expect(hook.result.current.map(({ title, meta }) => [title, meta])).toEqual([
    ['Demo launch checklist', 'Writing a reply · 2 agents working'],
    ['Trip plan', 'Background agents working'],
  ]);
  hook.result.current[0].onOpen();
  expect(onOpenConversation).toHaveBeenCalledWith('chat-launch');
});

it('lists a helper alone when its chat is quiet', () => {
  const { hook } = nowRows({
    conversations: [
      chat('chat-quiet', 'Old chat'),
      chat('helper', 'Research', {
        parent_conversation_id: 'chat-quiet',
        activity_state: 'active',
      }),
    ],
  });
  expect(hook.result.current.map(({ title, meta }) => [title, meta])).toEqual([
    ['Research', 'Agent working'],
  ]);
});

it('lists a running workflow with its step, opening its runs', () => {
  const { hook, onOpenWorkflows } = nowRows({
    tasks: [
      workflow({
        active_run: {
          id: 'run-1',
          status: 'running',
          started_at: new Date(now.getTime() - 2 * 60_000).toISOString(),
          steps_done: 1,
          steps_total: 3,
        },
      }),
      workflow({ id: 'wf-idle', name: 'Morning brief' }),
    ],
  });
  expect(hook.result.current.map(({ title, meta }) => [title, meta])).toEqual([
    [
      'Launch Summary',
      'Workflow running · step 2 of 3 · started 2 minutes ago',
    ],
  ]);
  hook.result.current[0].onOpen();
  expect(onOpenWorkflows).toHaveBeenCalledWith('wf-summary');
});

it('lists running background processes with their chat, never finished ones', async () => {
  const { hook, onOpenConversation } = nowRows({
    processes: [
      process(),
      process({ process_id: 'proc-done', status: 'finished', name: 'Build' }),
    ],
  });
  await waitFor(() => expect(hook.result.current).toHaveLength(1));
  expect(hook.result.current[0]).toMatchObject({
    title: 'Dev server',
    meta: expect.stringMatching(/^Landing page · running 2m/),
  });
  hook.result.current[0].onOpen();
  expect(onOpenConversation).toHaveBeenCalledWith('chat-site');
});
