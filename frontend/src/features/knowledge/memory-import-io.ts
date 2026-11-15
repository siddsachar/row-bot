import type {
  MemoryImportApply,
  MemoryImportBatch,
  MemoryImportCandidatePage,
  MemoryImportPreview,
  MemoryImportPreviewRequest,
  MemoryImportReceipt,
  MemoryImportSources,
  UploadCancelled,
} from '../../api/types';

/** What the Import memories dialog asks of the server (the controller, or a fake in tests). */
export type MemoryImportIO = {
  sources(signal?: AbortSignal): Promise<MemoryImportSources>;
  begin(): Promise<MemoryImportBatch>;
  batch(batch: string, signal?: AbortSignal): Promise<MemoryImportBatch>;
  discard(batch: string): Promise<UploadCancelled>;
  upload(
    batch: string,
    file: File,
    progress: (sent: number) => void,
    signal?: AbortSignal,
  ): Promise<string>;
  removeFile(batch: string, file: string): Promise<UploadCancelled>;
  addText(batch: string, text: string): Promise<MemoryImportBatch>;
  step(
    batch: string,
    step: 'scan' | 'conversations' | 'cancel',
  ): Promise<MemoryImportBatch>;
  candidates(
    batch: string,
    signal?: AbortSignal,
  ): Promise<MemoryImportCandidatePage>;
  preview(
    batch: string,
    items: MemoryImportPreviewRequest['items'],
  ): Promise<MemoryImportBatch>;
  readPreview(
    batch: string,
    signal?: AbortSignal,
  ): Promise<MemoryImportPreview>;
  apply(
    batch: string,
    command: Omit<MemoryImportApply, 'client_session_id'>,
  ): Promise<MemoryImportReceipt>;
  undo(batch: string, command: string): Promise<MemoryImportReceipt>;
};

type ImportController = {
  memoryImportSources: MemoryImportIO['sources'];
  beginMemoryImport: () => Promise<MemoryImportBatch>;
  memoryImport: MemoryImportIO['batch'];
  discardMemoryImport: MemoryImportIO['discard'];
  uploadMemoryImportFile: MemoryImportIO['upload'];
  removeMemoryImportFile: MemoryImportIO['removeFile'];
  addMemoryImportText: MemoryImportIO['addText'];
  memoryImportStep: MemoryImportIO['step'];
  memoryImportCandidates: MemoryImportIO['candidates'];
  previewMemoryImport: MemoryImportIO['preview'];
  memoryImportPreview: MemoryImportIO['readPreview'];
  applyMemoryImport: MemoryImportIO['apply'];
  undoMemoryImport: MemoryImportIO['undo'];
};

export function memoryImportIO(controller: ImportController): MemoryImportIO {
  return {
    sources: (signal) => controller.memoryImportSources(signal),
    begin: () => controller.beginMemoryImport(),
    batch: (batch, signal) => controller.memoryImport(batch, signal),
    discard: (batch) => controller.discardMemoryImport(batch),
    upload: (batch, file, progress, signal) =>
      controller.uploadMemoryImportFile(batch, file, progress, signal),
    removeFile: (batch, file) => controller.removeMemoryImportFile(batch, file),
    addText: (batch, text) => controller.addMemoryImportText(batch, text),
    step: (batch, step) => controller.memoryImportStep(batch, step),
    candidates: (batch, signal) =>
      controller.memoryImportCandidates(batch, signal),
    preview: (batch, items) => controller.previewMemoryImport(batch, items),
    readPreview: (batch, signal) =>
      controller.memoryImportPreview(batch, signal),
    apply: (batch, command) => controller.applyMemoryImport(batch, command),
    undo: (batch, command) => controller.undoMemoryImport(batch, command),
  };
}

export const CATEGORIES = [
  'fact',
  'preference',
  'person',
  'project',
  'skill',
  'event',
  'place',
  'organisation',
  'concept',
  'media',
] as const;
export type Category = (typeof CATEGORIES)[number];

const CATEGORY_WORDS: Record<Category, string> = {
  fact: 'About you',
  preference: 'Preference',
  person: 'Person',
  project: 'Project',
  skill: 'Skill',
  event: 'Event',
  place: 'Place',
  organisation: 'Organisation',
  concept: 'Idea',
  media: 'Media',
};

export function categoryWords(category: string) {
  return CATEGORY_WORDS[category as Category] ?? 'Other';
}

/** The words a job's failure code shows, without the code itself. */
export function jobProblem(code: string | null | undefined): string {
  switch (code) {
    case 'import_model_unavailable':
      return "Your chat model didn't answer. Check it in Settings › Models, then try again.";
    case 'model_selection_required':
      return 'Choose a chat model in Settings › Models first.';
    case 'import_scan_failed':
      return "Row-Bot couldn't read those files. Check they are the export you downloaded, then try again.";
    default:
      return 'Something went wrong. Try again.';
  }
}

export function plural(count: number, one: string, many = `${one}s`) {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

export function shortDate(iso: string | null | undefined) {
  if (!iso) return '';
  const date = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year:
      date.getFullYear() === new Date().getFullYear() ? undefined : 'numeric',
  });
}

export function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/** "ChatGPT and Claude", "ChatGPT, Claude and Gemini". */
export function listWords(names: readonly string[]) {
  if (names.length < 2) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

export function receiptWords(receipt: MemoryImportReceipt) {
  const saved = receipt.added + receipt.superseded;
  const parts = [];
  if (saved) parts.push(`${plural(saved, 'new memory', 'new memories')}`);
  if (receipt.updated)
    parts.push(`${receipt.updated.toLocaleString()} updated`);
  if (receipt.skipped)
    parts.push(`${receipt.skipped.toLocaleString()} already saved`);
  return parts.join(' · ') || 'Nothing new';
}
