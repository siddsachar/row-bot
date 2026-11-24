import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Pause, Trash2 } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type { PlaceWords } from '../../api/place';
import type { SpeechModelOption, SpeechModelsView } from '../../api/types';
import { useOverlay } from '../../ui/overlays';
import { IconButton } from '../../ui/primitives';
import { SettingsItem, StatusLine } from '../settings/anatomy';
import { remainingWords, sizeWords } from '../local-runtime/words';

const DOWNLOADING_MS = 1000;

/** Why the recommended model suits this computer, in a few plain words. */
export function reasonWords(reason: SpeechModelsView['reason']): string {
  switch (reason) {
    case 'gpu':
      return 'streams on your graphics card as you speak';
    case 'mac':
      return 'fast on this Mac';
    case 'no_gpu_runtime':
      return 'fast on the processor (the speech runtime doesn’t use graphics cards yet)';
    default:
      return 'fast on this computer’s processor';
  }
}

/** The plain fix line for a refused or failed download. */
export function speechFailureWords(view: SpeechModelsView): string | null {
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
      return 'The download didn’t match its fingerprint, so Row-Bot deleted it. Press Download to try again.';
    case 'size_changed':
      return 'The download size changed. Check the new size and press Download again.';
    case 'busy':
      return 'Another speech model is downloading. Wait for it, or stop it first.';
    case 'unavailable':
      return 'Faster speech models need Row-Bot’s voice components.';
    default:
      return 'The download didn’t finish. Press Download to try again.';
  }
}

/** The view, read now and every second while a download runs, with the speed seen so far. */
export function useSpeechModels(
  controller: ClientController | undefined,
  onFinished?: () => void,
) {
  const [view, setView] = useState<SpeechModelsView | null>(null);
  const [speed, setSpeed] = useState<number | null>(null);
  const last = useRef<{ at: number; done: number } | null>(null);
  const was = useRef<string | null>(null);
  const timer = useRef(0);
  const stopped = useRef(false);
  const finished = useRef(onFinished);
  finished.current = onFinished;

  const accept = useCallback((next: SpeechModelsView | null | undefined) => {
    if (!next || stopped.current) return;
    const download = next.state === 'downloading' ? next.download : null;
    if (download && download.phase === 'downloading') {
      const now = Date.now();
      const before = last.current;
      if (before && now > before.at && download.done_bytes >= before.done) {
        const instant =
          ((download.done_bytes - before.done) * 1000) / (now - before.at);
        setSpeed((old) => (old == null ? instant : old * 0.7 + instant * 0.3));
      }
      last.current = { at: now, done: download.done_bytes };
    } else {
      last.current = null;
      setSpeed(null);
    }
    if (was.current === 'downloading' && next.state !== 'downloading')
      finished.current?.();
    was.current = next.state;
    setView(next);
  }, []);

  const read = useCallback(async () => {
    window.clearTimeout(timer.current);
    if (stopped.current || !controller) return;
    let next: SpeechModelsView | null | undefined = null;
    try {
      next = await controller.speechModels();
      accept(next);
    } catch {
      /* Best effort: shown once it can be read. */
    }
    if (!stopped.current && next?.state === 'downloading')
      timer.current = window.setTimeout(() => void read(), DOWNLOADING_MS);
  }, [accept, controller]);

  useEffect(() => {
    stopped.current = false;
    void read();
    return () => {
      stopped.current = true;
      window.clearTimeout(timer.current);
    };
  }, [read]);

  const update = useCallback(
    (next: SpeechModelsView | null | undefined) => {
      accept(next);
      if (next?.state === 'downloading') {
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => void read(), DOWNLOADING_MS);
      }
    },
    [accept, read],
  );
  return { view, speed, update };
}

function languageWords(language: string | null | undefined): string {
  if (!language) return 'Any language';
  try {
    return (
      new Intl.DisplayNames(['en'], { type: 'language' }).of(language) ??
      language
    );
  } catch {
    return language;
  }
}

/**
 * Settings › Voice › Talk: the streaming speech models Row-Bot can download,
 * the one recommended for this computer first. Download, Stop and Remove are
 * icon buttons; nothing downloads until Download is pressed.
 */
export default function SpeechModels({
  controller,
  place,
  view,
  speed,
  update,
  onTalkChanged,
}: {
  controller: ClientController;
  place: PlaceWords;
  view: SpeechModelsView;
  speed: number | null;
  update: (next: SpeechModelsView | null | undefined) => void;
  /** Removing the model Talk listens with switches Talk back to Whisper. */
  onTalkChanged?: () => void;
}) {
  const overlay = useOverlay();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const recommended = view.models.find(
    (model) => model.id === view.recommended,
  );
  const rows = [
    ...view.models.filter((model) => model.id === view.recommended),
    ...view.models.filter((model) => model.id !== view.recommended),
  ];
  const download =
    view.state === 'downloading' && view.download ? view.download : null;
  const unavailable = view.state === 'unavailable';
  const failure = speechFailureWords(view);

  async function act(
    run: () => Promise<SpeechModelsView | null | undefined>,
    done?: (next: SpeechModelsView) => string | null,
  ) {
    setBusy(true);
    setMessage(null);
    try {
      const next = await run();
      update(next);
      if (next && done) setMessage(done(next));
    } catch (cause) {
      setMessage(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  function confirmRemove(model: SpeechModelOption) {
    overlay.open({
      kind: 'alert',
      title: `Remove ${model.name}?`,
      description: `Row-Bot deletes it and frees about ${sizeWords(model.installed_bytes)}.${
        view.talk_engine === model.engine_id
          ? ' Talk goes back to listening with Whisper.'
          : ''
      } You can download it again any time.`,
      confirmLabel: 'Remove',
      cancelLabel: 'Keep',
      onConfirm: () => {
        overlay.close();
        void act(
          () => controller.removeSpeechModel(model.id),
          (next) => {
            if (next.talk_engine !== view.talk_engine) onTalkChanged?.();
            return next.freed_bytes
              ? `Removed. ${sizeWords(next.freed_bytes)} freed.`
              : 'Removed.';
          },
        );
      },
    });
  }

  function facts(model: SpeechModelOption): string {
    const language = languageWords(model.language);
    if (model.installed)
      return `${sizeWords(model.installed_bytes)} ${place.on} · ${language}`;
    const resumes =
      view.download?.phase === 'stopped' &&
      view.download.model_id === model.id &&
      model.download_bytes < model.size_bytes;
    return `${sizeWords(model.download_bytes)} download${resumes ? ' left' : ''} · ${language} · ${model.licence}`;
  }

  return (
    <SettingsItem
      label="Faster speech models"
      help={
        recommended
          ? `${recommended.name} is recommended for ${place.name}: ${reasonWords(view.reason)}.`
          : undefined
      }
      layout="stacked"
      bind={false}
      className="voice-speech-models"
      status={
        failure ? (
          <StatusLine tone="danger">{failure}</StatusLine>
        ) : unavailable ? (
          <StatusLine tone="warning">
            Faster speech models need Row-Bot’s voice components.
          </StatusLine>
        ) : undefined
      }
    >
      <ul className="voice-speech-model-list" aria-label="Faster speech models">
        {rows.map((model) => {
          const here = download?.model_id === model.id ? download : null;
          const checking = here?.phase === 'checking';
          const percent =
            here && here.total_bytes
              ? Math.min(100, (here.done_bytes / here.total_bytes) * 100)
              : 0;
          const left = here
            ? remainingWords(here.total_bytes - here.done_bytes, speed)
            : '';
          const inUse = model.installed && view.talk_engine === model.engine_id;
          return (
            <li key={model.id} data-installed={model.installed || undefined}>
              <div className="voice-speech-model-text">
                <span className="voice-speech-model-name">
                  <strong>{model.name}</strong>
                  {model.id === view.recommended && (
                    <span className="status-chip">Recommended</span>
                  )}
                  {inUse && <span className="status-chip success">In use</span>}
                </span>
                {here ? (
                  <span className="voice-speech-model-progress">
                    <progress
                      aria-label={`Downloading ${model.name}`}
                      max={100}
                      value={checking ? undefined : percent}
                    />
                    <small role="status">
                      {checking
                        ? `Checking ${model.name}…`
                        : `${Math.floor(percent)}% of ${sizeWords(here.total_bytes)}${left ? ` · ${left}` : ''}`}
                    </small>
                  </span>
                ) : (
                  <small>{facts(model)}</small>
                )}
              </div>
              <span className="voice-speech-model-actions">
                {here ? (
                  <IconButton
                    size="sm"
                    label="Stop the download; it resumes later"
                    disabled={busy || checking}
                    onClick={() =>
                      void act(() => controller.stopSpeechModelDownload())
                    }
                  >
                    <Pause size={14} aria-hidden />
                  </IconButton>
                ) : model.installed ? (
                  <IconButton
                    size="sm"
                    label={`Remove ${model.name}`}
                    disabled={busy}
                    onClick={() => confirmRemove(model)}
                  >
                    <Trash2 size={14} aria-hidden />
                  </IconButton>
                ) : (
                  <IconButton
                    size="sm"
                    label={`Download ${model.name} (${sizeWords(model.download_bytes)}) and listen with it`}
                    disabled={busy || unavailable || Boolean(download)}
                    onClick={() =>
                      void act(() =>
                        controller.installSpeechModel(
                          model.id,
                          model.download_bytes,
                        ),
                      )
                    }
                  >
                    <Download size={14} aria-hidden />
                  </IconButton>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {message && (
        <p className="local-model-settings-message" role="status">
          {message}
        </p>
      )}
    </SettingsItem>
  );
}
