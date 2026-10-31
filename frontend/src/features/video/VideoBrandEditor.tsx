import { useEffect, useState } from 'react';
import { clientError } from '../../api/errors';
import type { MediaItem, SettingsChoice, VideoBrand } from '../../api/types';
import { useRuntime } from '../../runtime';
import { Button, Field, Select, Skeleton } from '../../ui/primitives';

const CAPTIONS: [VideoBrand['captions'], string][] = [
  ['box', 'In a box'],
  ['bold', 'Bold, outlined'],
  ['title', 'Large, centred'],
];
const CORNERS: [VideoBrand['logo_corner'], string][] = [
  ['top-left', 'Top left'],
  ['top-right', 'Top right'],
  ['bottom-left', 'Bottom left'],
  ['bottom-right', 'Bottom right'],
];
const MOODS: VideoBrand['music_mood'][] = ['calm', 'upbeat', 'focused', 'warm'];
const FRAMINGS: [VideoBrand['framing'], string][] = [
  ['framed', 'Framed on the colour'],
  ['full', 'Edge to edge'],
];
const CAMERAS: [VideoBrand['camera'], string][] = [
  ['follow', 'Follows what is done'],
  ['still', 'Stays still'],
];
const SPEEDS: [number, string][] = [
  [0.9, 'Slower'],
  [1, 'Normal'],
  [1.1, 'A little faster'],
  [1.25, 'Faster'],
];

/** The local voices Row-Bot speaks in (Settings › Voice), for a video's lines. */
export function useVoiceChoices(): SettingsChoice[] {
  const { controller } = useRuntime();
  const [voices, setVoices] = useState<SettingsChoice[]>([]);
  useEffect(() => {
    const abort = new AbortController();
    Promise.resolve()
      .then(() => controller.settingsSnapshot(abort.signal))
      .then(
        (snapshot) => setVoices(snapshot?.voice.tts_voice_options ?? []),
        () => undefined,
      );
    return () => abort.abort();
  }, [controller]);
  return voices;
}

/**
 * The brand kit (5.3.1 B3): one look for every video. Its colour fills title
 * cards, titles and badges, its caption style sets how lines show, its logo (a
 * picture from Library › Media) sits in a corner, its intro and outro are cards
 * before and after, its footage and camera set how recordings are shown, its
 * voice and speed say the lines, and its music mood is what Row-Bot picks. A
 * version can turn it off; changing the kit applies the next time a video is
 * made.
 */
export function VideoBrandEditor({ onSaved }: { onSaved: () => void }) {
  const { controller } = useRuntime();
  const [brand, setBrand] = useState<VideoBrand | null>(null);
  const [saved, setSaved] = useState<VideoBrand | null>(null);
  const [pictures, setPictures] = useState<MediaItem[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const voices = useVoiceChoices();

  useEffect(() => {
    const abort = new AbortController();
    controller.videoBrand(abort.signal).then(
      (value) => {
        if (!value) return;
        setBrand(value);
        setSaved(value);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    controller.media({ kind: 'image' }, abort.signal).then(
      (page) => setPictures(page?.items ?? []),
      () => undefined,
    );
    return () => abort.abort();
  }, [controller]);

  if (!brand)
    return error ? (
      <p role="alert" className="video-panel-error">
        {error}
      </p>
    ) : (
      <Skeleton label="Opening the brand kit" />
    );
  const set = (patch: Partial<VideoBrand>) => setBrand({ ...brand, ...patch });
  const changed = JSON.stringify(brand) !== JSON.stringify(saved);

  async function save() {
    if (!brand) return;
    setBusy(true);
    setError('');
    try {
      const next = await controller.saveVideoBrand(brand);
      if (next) {
        setBrand(next);
        setSaved(next);
        onSaved();
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="video-brand" role="group" aria-label="Brand kit">
      <div className="video-brand-grid">
        <Field label="Colour" layout="row">
          <input
            type="color"
            className="video-brand-colour"
            aria-label="Colour"
            value={brand.colour}
            onChange={(event) => set({ colour: event.target.value })}
          />
        </Field>
        <Field label="Caption style" layout="row">
          <Select
            aria-label="Caption style"
            value={brand.captions}
            onChange={(event) =>
              set({ captions: event.target.value as VideoBrand['captions'] })
            }
          >
            {CAPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Logo" layout="row">
          <Select
            aria-label="Logo"
            value={brand.logo}
            onChange={(event) => set({ logo: event.target.value })}
          >
            <option value="">None</option>
            {pictures.map((item) => (
              <option key={item.ref} value={item.ref}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
        {brand.logo && (
          <Field label="Logo corner" layout="row">
            <Select
              aria-label="Logo corner"
              value={brand.logo_corner}
              onChange={(event) =>
                set({
                  logo_corner: event.target.value as VideoBrand['logo_corner'],
                })
              }
            >
              {CORNERS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <Field label="Intro" layout="row">
          <input
            className="input"
            aria-label="Intro"
            placeholder="A card before the first scene"
            maxLength={120}
            value={brand.intro}
            onChange={(event) => set({ intro: event.target.value })}
          />
        </Field>
        <Field label="Outro" layout="row">
          <input
            className="input"
            aria-label="Outro"
            placeholder="A card after the last, e.g. row-bot.ai"
            maxLength={120}
            value={brand.outro}
            onChange={(event) => set({ outro: event.target.value })}
          />
        </Field>
        <Field label="Footage" layout="row">
          <Select
            aria-label="Footage"
            value={brand.framing}
            onChange={(event) =>
              set({ framing: event.target.value as VideoBrand['framing'] })
            }
          >
            {FRAMINGS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Camera" layout="row">
          <Select
            aria-label="Camera"
            value={brand.camera}
            onChange={(event) =>
              set({ camera: event.target.value as VideoBrand['camera'] })
            }
          >
            {CAMERAS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Voice" layout="row">
          <Select
            aria-label="Voice"
            value={brand.voice}
            onChange={(event) => set({ voice: event.target.value })}
          >
            <option value="">As in Settings</option>
            {voices.map((voice) => (
              <option key={voice.value} value={voice.value}>
                {voice.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Voice speed" layout="row">
          <Select
            aria-label="Voice speed"
            value={String(brand.voice_speed)}
            onChange={(event) =>
              set({ voice_speed: Number(event.target.value) })
            }
          >
            {SPEEDS.map(([value, label]) => (
              <option key={value} value={String(value)}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Music mood" layout="row">
          <Select
            aria-label="Music mood"
            value={brand.music_mood}
            onChange={(event) =>
              set({
                music_mood: event.target.value as VideoBrand['music_mood'],
              })
            }
          >
            {MOODS.map((mood) => (
              <option key={mood} value={mood}>
                {mood[0].toUpperCase() + mood.slice(1)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {error && (
        <p role="alert" className="video-panel-error">
          {error}
        </p>
      )}
      <div className="video-brand-actions">
        <span>Used for every video from the next one made.</span>
        <Button
          variant="primary"
          disabled={!changed || busy}
          onClick={() => void save()}
        >
          Save brand kit
        </Button>
      </div>
    </div>
  );
}
