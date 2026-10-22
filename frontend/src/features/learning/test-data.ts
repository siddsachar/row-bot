/** Learning snapshots and fakes for the Learning vitests (never real data). */
import { vi } from 'vitest';
import type {
  LearningAdvanced,
  LearningChangeView,
  LearningComparison,
  LearningDownload,
  LearningHardware,
  LearningOvernight,
  LearningPersonalModel,
  LearningReceipt,
  LearningSnapshot,
  LearningTraining,
  LearningUse,
  LearningVersion,
  LearningVersionDetails,
  LearningVersionRow,
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
    personal_model: personalModel(),
    training: trainingRun(),
    data: { exists: false, bytes: 0 },
    opted_out_conversations: [],
    owner_unset_channels: [],
    ...overrides,
  };
}

/** The personal model's download (nothing downloading by default). */
export function downloadState(
  overrides: Partial<LearningDownload> = {},
): LearningDownload {
  return {
    state: 'none',
    phase: null,
    done_bytes: 0,
    total_bytes: 0,
    needs_bytes: null,
    free_bytes: 512_000_000_000,
    failure: null,
    ...overrides,
  };
}

/** The personal model's choices and files (nothing downloaded by default). */
export function personalModel(
  overrides: Partial<LearningPersonalModel> = {},
): LearningPersonalModel {
  return {
    train_in_quiet_hours: true,
    train_where: 'this_computer',
    starting_model: {
      id: 'qwen3.5-2b',
      name: 'Qwen3.5 2B',
      licence: 'Apache 2.0',
      download_bytes: 4_571_272_453,
    },
    download: 'not_asked',
    engine: 'not_downloaded',
    engine_bytes: 0,
    engine_download_bytes: 2_217_998_432,
    starting_model_state: 'not_downloaded',
    starting_model_bytes: 0,
    download_state: downloadState(),
    ...overrides,
  };
}

/** Both downloaded: "Installed, 4 GB" and the starting model on this computer. */
export function installedModel(
  overrides: Partial<LearningPersonalModel> = {},
): LearningPersonalModel {
  return personalModel({
    download: 'waiting',
    engine: 'installed',
    engine_bytes: 4_033_251_988,
    starting_model_state: 'downloaded',
    starting_model_bytes: 4_571_279_000,
    ...overrides,
  });
}

/** The newest personal-model version (v1, ready in Ollama by default). */
export function versionState(
  overrides: Partial<LearningVersion> = {},
): LearningVersion {
  return {
    version: 1,
    phase: 'ready',
    step: null,
    failure: null,
    runtime: 'ollama',
    quantization: 'Q8_0',
    file_name: 'row-bot-personal-v1-Q8_0.gguf',
    file_bytes: 2_012_012_128,
    folder:
      'C:\\Users\\me\\AppData\\Local\\Row-Bot\\learning_models\\versions\\v1',
    command: null,
    needs_bytes: null,
    free_bytes: null,
    trained_at: '2026-10-16T19:30:00+00:00',
    examples: 154,
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
    version: vi.fn(async () => versionDetails()),
  };
  return io;
}

/** v1's comparison, done (UX §5's example): better at document lookups. */
export function comparisonState(
  overrides: Partial<LearningComparison> = {},
): LearningComparison {
  return {
    version: 1,
    state: 'done',
    done: 96,
    total: 96,
    compared_at: '2026-10-16T21:10:00+00:00',
    arms: [
      {
        key: 'base',
        label: 'the starting model',
        role: 'baseline',
        ran: 32,
        cached: 0,
        seconds_per_task: 6.2,
        cloud_tokens_per_task: 0,
      },
      {
        key: 'v1',
        label: 'v1 (new)',
        role: 'new',
        ran: 32,
        cached: 0,
        seconds_per_task: 4.8,
        cloud_tokens_per_task: 0,
      },
      {
        key: 'main',
        label: 'GPT-6.1 Sol',
        role: 'main',
        ran: 32,
        cached: 32,
        seconds_per_task: 21.5,
        cloud_tokens_per_task: 18_400,
      },
    ],
    rows: [
      {
        kind: 'document_lookups',
        label: 'Document lookups',
        total: 12,
        cells: [
          { key: 'base', passed: 6, counted: 12 },
          { key: 'v1', passed: 10, counted: 12 },
          { key: 'main', passed: 11, counted: 12 },
        ],
        verdict: 'better',
      },
      {
        kind: 'structured_transforms',
        label: 'Structured transforms',
        total: 10,
        cells: [
          { key: 'base', passed: 7, counted: 10 },
          { key: 'v1', passed: 7, counted: 9 },
          { key: 'main', passed: 9, counted: 10 },
        ],
        verdict: 'same',
      },
      {
        kind: 'own_work',
        label: 'Your own work',
        total: 10,
        cells: [
          { key: 'base', passed: 4, counted: 10 },
          { key: 'v1', passed: 5, counted: 10 },
          { key: 'main', passed: 8, counted: 10 },
        ],
        verdict: 'same',
      },
    ],
    verdict: 'better',
    line: 'Better at document lookups than the starting model: 10 of 12 passed, against 6.',
    recommend: 'document_lookups',
    note: 'Testing used 590K tokens (and 1.1M on this computer); GPT-6.1 Sol’s results were reused.',
    coding_left_out: true,
    baseline_unavailable: false,
    main_unavailable: null,
    reviewed: false,
    ...overrides,
  };
}

/** The same comparison when v1 is no better: said plainly, nothing to use. */
export function noDifferenceComparison(
  overrides: Partial<LearningComparison> = {},
): LearningComparison {
  const base = comparisonState();
  return comparisonState({
    rows: base.rows.map((row) => ({ ...row, verdict: 'same' as const })),
    verdict: 'no_difference',
    line: 'No clear difference from the starting model. Not used.',
    recommend: null,
    ...overrides,
  });
}

/** One ready version in the Versions list. */
export function versionRow(
  overrides: Partial<LearningVersionRow> = {},
): LearningVersionRow {
  return {
    version: 1,
    ready: true,
    file_bytes: 2_012_012_128,
    trained_at: '2026-10-16T19:30:00+00:00',
    compared: true,
    uses: [],
    ...overrides,
  };
}

/** v1 on trial for document lookups (due for "Keep v1…?" by default). */
export function trialUse(overrides: Partial<LearningUse> = {}): LearningUse {
  return {
    kind: 'document_lookups',
    label: 'Document lookups',
    version: 1,
    state: 'trial',
    since: '2026-10-13T09:00:00+00:00',
    previous_version: null,
    trial: {
      requests: 18,
      answered: 17,
      fallbacks: 1,
      seconds_per_request: 4.2,
    },
    total: {
      requests: 18,
      answered: 17,
      fallbacks: 1,
      seconds_per_request: 4.2,
    },
    keep_due: true,
    ...overrides,
  };
}

/** Advanced ›: the 8 GB recipe's values, nothing changed. */
export function advancedState(
  overrides: Partial<LearningAdvanced> = {},
): LearningAdvanced {
  const values = {
    starting_model: 'qwen3.5-2b',
    rank: 16,
    learning_rate: 0.0002,
    epochs: 1,
    context: 4096,
    method: 'standard' as const,
  };
  return {
    values,
    recommended: values,
    changed: false,
    choices: {
      starting_model: [{ id: 'qwen3.5-2b', name: 'Qwen3.5 2B' }],
      rank: [8, 16, 32, 64],
      learning_rate: [0.00005, 0.0001, 0.0002, 0.0003, 0.0005],
      epochs: [1, 2, 3],
      context: [4096],
      method: ['standard'],
    },
    data_mix: [
      { id: 'your_work', label: 'Your work', share: 100, available: true },
      { id: 'corrections', label: 'Corrections', share: 0, available: false },
      { id: 'practice', label: 'Practice', share: 0, available: false },
      { id: 'general', label: 'General replay', share: 0, available: false },
    ],
    ...overrides,
  };
}

/** v1's experiment details (the review sheet and Export… read them). */
export function versionDetails(
  overrides: Partial<LearningVersionDetails> = {},
): LearningVersionDetails {
  return {
    version: 1,
    data: {
      examples: 154,
      validation: 17,
      conversations: 41,
      first_at: '2026-10-01T08:00:00+00:00',
      last_at: '2026-10-16T18:00:00+00:00',
      providers: ['OpenAI'],
      left_out: [{ reason: 'too long', count: 3 }],
      terms_note: true,
    },
    method: {
      name: 'Standard',
      title: 'LoRA',
      rank: 16,
      learning_rate: 0.0002,
      epochs: 1,
      context: 4096,
      quantization: 'Q8_0',
    },
    versions: [
      { name: 'Starting model', value: 'Qwen3.5 2B' },
      { name: 'Training engine', value: 'Unsloth 2026.10.2' },
    ],
    runs: [
      {
        suite: 'Tasks it hadn’t seen',
        arm: 'v1 (new)',
        passed: 16,
        total: 22,
      },
      {
        suite: 'Tasks it hadn’t seen',
        arm: 'the starting model',
        passed: 12,
        total: 22,
      },
    ],
    files: [
      { name: 'row-bot-personal-v1-Q8_0.gguf', bytes: 2_012_012_128 },
      { name: 'adapter_model.safetensors', bytes: 43_000_000 },
      { name: 'MODEL_CARD.md', bytes: 4_000 },
      { name: 'manifest.json', bytes: 2_000 },
    ],
    budget_tokens_per_task: 150_000,
    comparison_note: '',
    ...overrides,
  };
}

/** The personal model with v1 ready and compared. */
export function comparedModel(
  overrides: Partial<LearningPersonalModel> = {},
): LearningPersonalModel {
  return installedModel({
    version: versionState(),
    versions: [versionRow()],
    comparison: comparisonState(),
    uses: [],
    advanced: advancedState(),
    export_folder: 'C:\\Users\\me\\Documents',
    ...overrides,
  });
}

/** A drafted change waiting to be tested (L3a; never real data). */
export function draftedChange(
  overrides: Partial<LearningChangeView> = {},
): LearningChangeView {
  return {
    id: 'proposal_1',
    change: 'lesson',
    title: 'Use the current policy',
    kind: 'document_lookups',
    state: 'waiting_to_test',
    created_at: new Date(Date.now() - 3_600_000).toISOString(),
    text: 'When two versions of a policy exist, answer from the newest approved one and say which.',
    why: {
      chats: 3,
      corrections: 2,
      stops: 1,
      retries: 0,
      denials: 0,
      failures: 0,
      imported: 0,
    },
    never_automatic: false,
    ...overrides,
  };
}

/** Last night's look (L3a): found 3, drafted 2 by default. */
export function overnight(
  overrides: Partial<LearningOvernight> = {},
): LearningOvernight {
  return {
    night: new Date().toISOString().slice(0, 10),
    state: 'done',
    found: 3,
    drafted: 2,
    left_out: 0,
    needs_examples: 0,
    looks_like_bug: 0,
    ...overrides,
  };
}
