import { useEffect, useRef, useState } from 'react';
import { Download, Square } from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  LearningDownload,
  LearningPersonalModel,
  LearningSnapshot,
} from '../../api/types';
import { ModalTask, useNotify } from '../../ui/overlays';
import { Button, EntityRow, type Tone } from '../../ui/primitives';
import type { LearningAction, LearningIO } from './learning-state';
import { usePhone } from './use-phone';
import {
  DOWNLOAD_FAILURES,
  ENGINE_SOURCES,
  ENGINE_TELEMETRY,
  percentWords,
  progressWords,
  sizeWords,
} from './words';

type DownloadAction = {
  label: string;
  action: Extract<
    LearningAction,
    'start_download' | 'stop_download' | 'cancel_download'
  >;
  primary?: boolean;
};

/** What the person sees about the personal model's download (UX §1, §9). */
export type DownloadView = {
  title: string;
  detail?: string;
  /** A note under the detail (where it comes from, usage reporting). */
  note?: string;
  /** Bytes of the whole download; undefined where there is no real denominator. */
  progress?: { value: number; max: number };
  actions: DownloadAction[];
  /** For the status region: changes only at milestones, never every second. */
  announce: string;
};

function sizes(model: LearningPersonalModel) {
  return `the training engine (${sizeWords(model.engine_download_bytes)}) and ${model.starting_model.name} (${sizeWords(model.starting_model.download_bytes)})`;
}

function capital(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** What still needs downloading, in words: "the training engine and Qwen3.5 2B". */
export function missingWords(model: LearningPersonalModel) {
  const engine = model.engine !== 'installed';
  const starting = model.starting_model_state !== 'downloaded';
  if (engine && starting)
    return `the training engine and ${model.starting_model.name}`;
  if (engine) return 'the training engine';
  return model.starting_model.name;
}

/**
 * The download in words: "Downloading the training engine · 1.2 of 6.8 GB · 18%", "Download
 * paused at 3.1 of 6.8 GB.", "Needs 9.4 GB free; 6 GB free.", Ready to download, Repair, Update.
 * Never a raw state; a progress bar only with a real denominator (bytes).
 */
export function downloadView(
  model: LearningPersonalModel,
): DownloadView | null {
  const download: LearningDownload = model.download_state;
  const name = model.starting_model.name;
  const done = download.done_bytes;
  const total = download.total_bytes;
  const tryAgain: DownloadAction = {
    label: 'Try again',
    action: 'start_download',
    primary: true,
  };
  const cancel: DownloadAction = {
    label: 'Cancel',
    action: 'cancel_download',
  };
  const stop: DownloadAction = { label: 'Stop', action: 'stop_download' };
  switch (download.state) {
    case 'downloading': {
      if (download.phase === 'installing')
        return {
          title: 'Installing the training engine…',
          detail: `Downloaded ${sizeWords(done)}. Installing takes a few minutes.`,
          actions: [stop],
          announce: 'Installing the training engine.',
        };
      if (download.phase === 'checking')
        return {
          title: 'Checking the training engine…',
          detail: 'Making sure it can use the graphics card.',
          actions: [stop],
          announce: 'Checking the training engine.',
        };
      // O49: the title names both parts while the total holds both (the engine, then the model).
      const both =
        download.phase === 'model'
          ? total > model.starting_model.download_bytes
          : model.starting_model_state !== 'downloaded';
      const what = both
        ? `the training engine and ${name}`
        : download.phase === 'model'
          ? name
          : 'the training engine';
      const share = total ? Math.floor((done / total) * 10) * 10 : 0;
      return {
        title: total
          ? `Downloading ${what} · ${progressWords(done, total)}`
          : `Downloading ${what}`,
        detail: total
          ? both
            ? `${percentWords(done, total)} · now ${download.phase === 'model' ? name : 'the training engine'}`
            : percentWords(done, total)
          : undefined,
        progress: total ? { value: done, max: total } : undefined,
        actions: [stop],
        announce: `Downloading ${what}: ${share}% done.`,
      };
    }
    case 'paused':
      return {
        title: total
          ? `Download paused at ${progressWords(done, total)}.`
          : 'Download paused.',
        progress: total ? { value: done, max: total } : undefined,
        actions: [
          { label: 'Resume', action: 'start_download', primary: true },
          cancel,
        ],
        announce: 'Download paused.',
      };
    case 'not_enough_disk':
      return {
        title:
          download.needs_bytes != null && download.free_bytes != null
            ? `Needs ${sizeWords(download.needs_bytes)} free; ${sizeWords(download.free_bytes)} free.`
            : 'There isn’t enough free disk space for the download.',
        detail: 'Free some space on this computer, then try again.',
        actions: done ? [tryAgain, cancel] : [tryAgain],
        announce: 'Not enough free disk space for the download.',
      };
    case 'failed':
      return {
        title:
          DOWNLOAD_FAILURES[download.failure ?? 'network'] ??
          DOWNLOAD_FAILURES.network,
        actions: done ? [tryAgain, cancel] : [tryAgain],
        announce: 'The download stopped.',
      };
    case 'ready':
      if (
        model.engine === 'installed' &&
        model.starting_model_state === 'downloaded'
      )
        // An engine from before Row-Bot ran personal models itself (O75): only that part is missing.
        return {
          title: 'Ready to download',
          detail: `Row-Bot can now run your personal model itself. It needs one more part of the training engine (${sizeWords(Math.max(0, total - done))}).`,
          note: 'From GitHub (llama.cpp).',
          actions: [
            { label: 'Download', action: 'start_download', primary: true },
          ],
          announce: '',
        };
      return {
        title: 'Ready to download',
        detail: `${capital(sizes(model))}.${
          download.free_bytes != null
            ? ` ${sizeWords(download.free_bytes)} free on this computer.`
            : ''
        }`,
        note: `${ENGINE_SOURCES} ${ENGINE_TELEMETRY}`,
        actions: [
          { label: 'Download', action: 'start_download', primary: true },
        ],
        announce: '',
      };
    default:
      break;
  }
  if (model.engine === 'damaged')
    return {
      title: 'The training engine is damaged.',
      detail: `Repair downloads it again (${sizeWords(model.engine_download_bytes)}).`,
      actions: [{ label: 'Repair', action: 'start_download', primary: true }],
      announce: '',
    };
  if (model.engine === 'outdated')
    return {
      title: 'The training engine is from an earlier version of Row-Bot.',
      detail: `Update it to train here (${sizeWords(model.engine_download_bytes)}).`,
      actions: [{ label: 'Update', action: 'start_download', primary: true }],
      announce: '',
    };
  if (model.starting_model_state === 'damaged')
    return {
      title: `${name}’s files are damaged.`,
      detail: `Download them again (${sizeWords(model.starting_model.download_bytes)}).`,
      actions: [
        { label: 'Download again', action: 'start_download', primary: true },
      ],
      announce: '',
    };
  return null;
}

/** Cancel asks first: what arrived is deleted. */
export function CancelDownload({
  open,
  onOpenChange,
  bytes,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bytes: number;
  onConfirm: () => void;
}) {
  const phone = usePhone();
  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title="Cancel the download?"
      description={`${bytes ? `What it downloaded so far (${sizeWords(bytes)}) is deleted. ` : ''}You can download it again later.`}
      kind={phone ? 'sheet' : 'dialog'}
    >
      <div className="learning-sheet-actions">
        <Button onClick={() => onOpenChange(false)} data-initial-focus>
          Keep it
        </Button>
        <Button
          variant="danger"
          onClick={() => {
            onOpenChange(false);
            onConfirm();
          }}
        >
          Cancel download
        </Button>
      </div>
    </ModalTask>
  );
}

/** The download on the Personal model view, with Stop, Resume, Cancel and Try again. */
export function DownloadProgress({
  io,
  snapshot,
}: {
  io: LearningIO;
  snapshot: LearningSnapshot;
}) {
  const notify = useNotify();
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const model = snapshot.personal_model;
  const view = downloadView(model);
  const [said, setSaid] = useState('');
  const announced = useRef<string | null>(null);
  const announce = view?.announce ?? '';
  useEffect(() => {
    if (announced.current === null) {
      announced.current = announce;
      return;
    }
    if (announce && announce !== announced.current) {
      announced.current = announce;
      setSaid(announce);
    }
  }, [announce]);
  if (!view) return null;

  async function run(action: DownloadAction['action']) {
    setBusy(true);
    try {
      notify((await io.run(action)).summary);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="monitor-section" aria-label="Download">
      <header className="monitor-section-head">
        <h3>Download</h3>
      </header>
      <div
        className="learning-training"
        data-state={model.download_state.state}
      >
        <div className="learning-training-head">
          <div className="learning-training-words">
            <p className="learning-training-title">{view.title}</p>
            {view.detail && <p className="learning-muted">{view.detail}</p>}
            {view.note && <p className="learning-muted">{view.note}</p>}
          </div>
          <div className="learning-actions-inline">
            {view.actions.map((item) => (
              <Button
                key={`${item.action}-${item.label}`}
                variant={item.primary ? 'primary' : undefined}
                className="small"
                disabled={busy}
                onClick={() =>
                  item.action === 'cancel_download'
                    ? setCancelling(true)
                    : void run(item.action)
                }
              >
                {item.label}
              </Button>
            ))}
          </div>
        </div>
        {view.progress && (
          <progress
            aria-label="Download progress"
            max={view.progress.max}
            value={view.progress.value}
          />
        )}
      </div>
      <p className="visually-hidden" role="status" aria-live="polite">
        {said}
      </p>
      <CancelDownload
        open={cancelling}
        onOpenChange={setCancelling}
        bytes={model.download_state.done_bytes}
        onConfirm={() => void run('cancel_download')}
      />
    </section>
  );
}

/**
 * Monitor's row while the personal model downloads: "Training engine · Downloading · 45%",
 * "1.2 of 6.8 GB", with Stop (it pauses; Resume is on the Personal model view).
 */
export function downloadRowWords(model: LearningPersonalModel): {
  title: string;
  status: { tone: Tone; label: string };
  meta: string;
} {
  const download = model.download_state;
  const title =
    download.phase === 'model' ? model.starting_model.name : 'Training engine';
  const status =
    download.phase === 'installing'
      ? { tone: 'neutral' as Tone, label: 'Installing…' }
      : download.phase === 'checking'
        ? { tone: 'neutral' as Tone, label: 'Checking…' }
        : {
            tone: 'accent' as Tone,
            label: `Downloading · ${percentWords(download.done_bytes, download.total_bytes)}`,
          };
  return {
    title,
    status,
    meta: download.total_bytes
      ? `Personal model download · ${progressWords(download.done_bytes, download.total_bytes)}`
      : 'Personal model download',
  };
}

export function DownloadMonitorRow({
  model,
  busy,
  onStop,
  onOpen,
}: {
  model: LearningPersonalModel;
  busy: boolean;
  onStop: () => void;
  onOpen?: () => void;
}) {
  const words = downloadRowWords(model);
  return (
    <EntityRow
      className="process-row"
      title={words.title}
      icon={<Download size={16} />}
      status={words.status}
      meta={words.meta}
      action={
        <Button
          className="small"
          aria-label={`Stop downloading ${words.title}`}
          disabled={busy}
          onClick={onStop}
        >
          <Square size={12} aria-hidden />
          Stop
        </Button>
      }
      menuLabel={`More for ${words.title}`}
      menu={onOpen ? [{ label: 'Open Personal model', onSelect: onOpen }] : []}
    />
  );
}
