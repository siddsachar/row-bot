/** Learning snapshots and fakes for the Learning vitests (never real data). */
import { vi } from 'vitest';
import type {
  LearningHardware,
  LearningReceipt,
  LearningSnapshot,
  LearningTraining,
} from '../../api/types';
import type { LearningIO } from './learning-state';

export function learningSnapshot(
  overrides: Partial<LearningSnapshot> = {},
): LearningSnapshot {
  return {
    schema_version: 1,
    revision: 'a'.repeat(64),
    state: 'off',
    readable: true,
    sources: { my_chats: true, workflows_and_goals: true, past_chats: true },
    apply_mode: 'ask_first',
    spending: {
      used_tokens: 0,
      limit_tokens: 2_000_000,
      resets_on: '2026-11-01',
      model_name: 'GPT-5.6 Sol',
      limit_reached: false,
    },
    quiet_hours: { window_start: 1, window_end: 5, next_look_at: null },
    past_chats: { state: 'none', total: 0, done: 0 },
    personal_model: {
      train_in_quiet_hours: true,
      train_where: 'this_computer',
      starting_model: {
        id: 'qwen3.5-2b',
        name: 'Qwen3.5 2B',
        licence: 'Apache 2.0',
        download_bytes: 4_570_000_000,
      },
      download: 'not_asked',
      engine: 'not_downloaded',
      engine_download_bytes: 2_170_000_000,
    },
    training: trainingRun(),
    data: { exists: false, bytes: 0 },
    opted_out_conversations: [],
    owner_unset_channels: [],
    ...overrides,
  };
}

/** The personal model's training run (none by default: no engine yet). */
export function trainingRun(
  overrides: Partial<LearningTraining> = {},
): LearningTraining {
  return {
    state: 'none',
    job_id: null,
    version: null,
    step: 0,
    steps_total: null,
    checkpoint_step: null,
    seconds_left: null,
    pause_reason: null,
    resumes: null,
    failure: null,
    recovered: null,
    trigger: null,
    can_train_now: false,
    runner: { state: 'nothing_to_do', reason: 'no_engine' },
    ...overrides,
  };
}

/** A run of 1,000 steps for v1, at step 340 (UX §5's example). */
export function runningRun(
  overrides: Partial<LearningTraining> = {},
): LearningTraining {
  return trainingRun({
    state: 'running',
    job_id: 'job_1',
    version: 1,
    step: 340,
    steps_total: 1000,
    checkpoint_step: 340,
    seconds_left: 2400,
    trigger: 'train_now',
    runner: { state: 'training', reason: null },
    ...overrides,
  });
}

export const GPU_8GB: LearningHardware = {
  state: 'gpu',
  gpu: {
    name: 'NVIDIA GeForce RTX 5070 Laptop GPU',
    memory_gb: 8,
    label: 'NVIDIA GeForce RTX 5070 Laptop (8 GB)',
  },
  tier: {
    id: '8gb',
    label: '8 GB',
    certified: true,
    summary: 'Can train small models (up to about 2B) on your tasks.',
  },
  larger_tiers: [],
  starting_models: [
    {
      id: 'qwen3.5-2b',
      name: 'Qwen3.5 2B',
      licence: 'Apache 2.0',
      download_bytes: 4_570_000_000,
    },
  ],
  engine_download_bytes: 2_170_000_000,
  free_bytes: 512_000_000_000,
};

export const NO_GPU: LearningHardware = {
  state: 'no_gpu',
  gpu: null,
  tier: null,
  larger_tiers: [],
  starting_models: [],
  engine_download_bytes: 2_170_000_000,
  free_bytes: 512_000_000_000,
};

/** An IO whose commands answer with the next snapshot the test gives. */
export function fakeIO(
  snapshot: LearningSnapshot | null,
  options: {
    hardware?: LearningHardware;
    after?: (
      action: string,
      fields: Record<string, unknown>,
    ) => Partial<LearningSnapshot>;
  } = {},
) {
  const io: LearningIO = {
    snapshot,
    error: '',
    reload: vi.fn(async () => io.snapshot),
    run: vi.fn(async (action, fields = {}) => {
      const next = learningSnapshot({
        ...(io.snapshot ?? {}),
        ...(options.after?.(action, fields as Record<string, unknown>) ?? {}),
      });
      io.snapshot = next;
      const receipt: LearningReceipt = {
        command_id: '00000000-0000-4000-8000-000000000000',
        status: 'completed',
        summary:
          action === 'teach'
            ? 'Thanks. Row-Bot will learn from this.'
            : `Done: ${action}`,
        snapshot: next,
        signal_id: action === 'teach' ? 'sig_1' : null,
      };
      return receipt;
    }),
    hardware: vi.fn(async () => options.hardware ?? GPU_8GB),
    lessons: vi.fn(async () => ({ items: [], total: 0 })),
    conversation: vi.fn(async (id: string) => ({
      conversation_id: id,
      state: 'learning' as const,
      reason: '',
    })),
  };
  return io;
}
