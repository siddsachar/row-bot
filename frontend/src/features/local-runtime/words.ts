import type { LocalModelOption, LocalModelRuntimeView } from '../../api/types';

/** A download or disk size in decimal units, as a file manager says it: "5.7 GB", "38 MB". */
export function sizeWords(bytes: number): string {
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(bytes / 1_000_000))} MB`;
}

/** What a smaller model of the generation before says about itself. */
export const OLDER_NOTE =
  'A smaller model for this computer (older generation). For long tasks use a larger model or a provider.';

/** "Qwen3.8 27B · 2-bit" for the builds of the default model; a smaller older model by its name. */
export function modelLabel(
  model: Pick<LocalModelOption, 'name' | 'quantization' | 'older'>,
): string {
  return model.older || !model.quantization
    ? model.name
    : `${model.name} · ${model.quantization}`;
}

/** How a model would run here, in a few words (the badge in the model list). A Mac has no separate
 * graphics card: its model fits the memory it shares (M2). */
export function fitWords(fit: LocalModelOption['fit'], mac = false): string {
  switch (fit) {
    case 'gpu':
      return mac ? 'Fits this Mac’s memory' : 'Fits your graphics card';
    case 'partial':
      return 'Partly on the graphics card (slower)';
    case 'cpu':
      return 'Runs on the processor (slower)';
    default:
      return 'Too big for this computer';
  }
}

/** What Repair checks again: a Mac's memory (it has no separate graphics card), else the card. */
export function repairWords(mac = false): string {
  return mac
    ? 'Repair: check this Mac’s memory again'
    : 'Repair: check this computer’s graphics card again';
}

/** What a model reads at the window it is served at here: "reads 128K". */
export function readsWords(tokens: number): string {
  return `reads ${Math.round(tokens / 1024)}K`;
}

/** The fact line under the title: "Qwen3.5 9B · fits your RTX 4070 (12 GB) · reads 128K · 7.3 GB download". */
export function factLine(
  view: LocalModelRuntimeView,
  model: LocalModelOption,
): string {
  const where =
    model.fit === 'gpu'
      ? `fits your ${view.computer}`
      : model.fit === 'too_big'
        ? 'too big for this computer'
        : `runs on ${view.computer} (slower)`;
  const size = model.download_bytes
    ? `${sizeWords(model.download_bytes)} download`
    : 'downloaded';
  const reads =
    model.fit === 'too_big' ? '' : ` · ${readsWords(model.context_tokens)}`;
  return `${modelLabel(model)} · ${where}${reads} · ${size}`;
}

/** The plain fix line for a refused or failed download. */
export function failureWords(view: LocalModelRuntimeView): string | null {
  const failure = view.failure;
  if (!failure) return null;
  switch (failure.reason) {
    case 'network':
      return 'The download stopped: no connection. It resumes when you press Download.';
    case 'out_of_disk':
      return failure.needed_bytes != null && failure.free_bytes != null
        ? `Not enough disk space: ${sizeWords(failure.needed_bytes)} needed, ${sizeWords(failure.free_bytes)} free.`
        : 'Not enough disk space for this download.';
    case 'mismatch':
      return 'A downloaded file didn’t match its fingerprint, so Row-Bot deleted it. Press Download to try again.';
    case 'blocked':
      return 'This computer blocked the downloaded program (an antivirus or App Control). Allow it, then press Download again.';
    case 'size_changed':
      return 'The download size changed. Check the new size and press Download again.';
    case 'unavailable':
      return 'This computer can’t run a model here. Use a provider instead.';
    case 'busy':
      return 'Another download is running. Wait for it, or stop it first.';
    default:
      return 'The download didn’t finish. Press Download to try again.';
  }
}

/** "about 4 min" from the speed seen so far, or '' while it is still unknown. */
export function remainingWords(
  remainingBytes: number,
  bytesPerSecond: number | null,
): string {
  if (!bytesPerSecond || bytesPerSecond <= 0 || remainingBytes <= 0) return '';
  const seconds = remainingBytes / bytesPerSecond;
  if (seconds < 60) return 'less than a minute';
  if (seconds < 3600) return `about ${Math.round(seconds / 60)} min`;
  const hours = seconds / 3600;
  return `about ${hours < 10 ? hours.toFixed(1) : Math.round(hours)} h`;
}

/**
 * The models the sliders offer (W4): Qwen3.8 27B at the build sized to this
 * computer's card (the largest that fits on it, else the smallest), then the
 * smaller models of the generation before. A build already downloaded stays
 * listed, so it can be chosen again.
 */
export function choiceModels(view: LocalModelRuntimeView): LocalModelOption[] {
  const builds = view.models.filter((model) => !model.older);
  const sized =
    builds.find((model) => model.fit === 'gpu') ?? builds[builds.length - 1];
  return view.models.filter(
    (model) => model.older || model === sized || model.installed,
  );
}

/** The model Download would fetch now: the chosen one, else the recommendation. */
export function pickedModel(
  view: LocalModelRuntimeView,
  chosen: string | null,
): LocalModelOption | null {
  const id = chosen ?? view.recommended ?? null;
  return view.models.find((model) => model.id === id) ?? null;
}
