import { useEffect, useRef, useState } from 'react';
import { File, FileText, Film, Image, Music } from 'lucide-react';
import { clientError } from '../../api/errors';
import type { OutputFilter, OutputPage, OutputView } from '../../api/types';
import { useRuntime } from '../../runtime';

/*
 * The outputs list (W3): one server list behind a chat's Outputs pane,
 * Library › Media and the phone's Files. Each thing made is one output with
 * its versions inside; working files and the person's own files show only
 * when asked for.
 */

export type OutputShow = NonNullable<OutputFilter['show']>;

export const OUTPUT_ICONS = {
  video: Film,
  image: Image,
  audio: Music,
  document: FileText,
  file: File,
} as const;

export const OUTPUT_KIND_WORDS: Record<OutputView['kind'], string> = {
  video: 'Video',
  image: 'Picture',
  audio: 'Sound',
  document: 'Document',
  file: 'File',
};

export const ROLE_WORDS: Record<OutputView['role'], string> = {
  final: 'Final',
  draft: 'Draft',
  working: 'Working file',
  upload: 'Added',
};

/** The three views every list offers, made first (the owner's chips). */
export const SHOW_OPTIONS: { value: OutputShow; label: string }[] = [
  { value: 'made', label: 'Made' },
  { value: 'uploads', label: 'Uploads' },
  { value: 'working', label: 'Working files' },
];

/**
 * An output as a person reads it: a file they added by its own file name;
 * a made one by its name, and a name a tool made from words ("waves-at-dusk",
 * "recording") as words ("Waves at dusk", "Recording"). A name the person or
 * the agent wrote ("Row-Bot demo", "Q3 budget") stays as written.
 */
export function outputTitle(
  output: Pick<OutputView, 'name' | 'file_name' | 'role'>,
) {
  if (output.role === 'upload') return output.file_name;
  const name = output.name;
  if (/\s/.test(name) || name !== name.toLowerCase()) return name;
  const words = name.replace(/-+/g, ' ').trim();
  return words.charAt(0).toLocaleUpperCase() + words.slice(1);
}

const MEDIA_SUFFIX =
  /\.(?:mp4|m4v|mov|webm|mkv|png|jpe?g|gif|webp|wav|mp3|m4a|ogg|flac|aac)$/i;

/**
 * A made file shown by its name in the chat's files ("Sign-up in 30
 * seconds.mp4"), named as its Outputs tile names it: a film, picture or
 * sound by its title, without "generated-" or its type. Any other file (a
 * PDF, a spreadsheet) keeps its file name, its type part of the name.
 */
export function madeTitle(fileName: string, mime: string) {
  if (!/^(?:video|image|audio)\//.test(mime)) return fileName;
  const name = fileName
    .replace(/^generated-(?=.)/, '')
    .replace(MEDIA_SUFFIX, '');
  return name
    ? outputTitle({ name, file_name: fileName, role: 'final' })
    : fileName;
}

/** A film's voice line as the video tool names it: "<film> <scene id>". */
const VOICE_LINE = /^(.+) s[0-9a-f]{6}(?:-\d+)?$/;

/** The film a voice line belongs to, or "" for any other output. */
export function voiceLineOf(
  output: Pick<OutputView, 'name' | 'kind' | 'role'>,
) {
  if (output.kind !== 'audio' || output.role !== 'working') return '';
  return VOICE_LINE.exec(output.name)?.[1] ?? '';
}

/**
 * Titles for one list of outputs: each as `outputTitle` reads it, but a
 * film's voice lines (which share the film's name and a scene id) as
 * "Voice 2 · Sign-up in 30 seconds", numbered in the order they were made,
 * so three lines read as three, and the number shows where a tile cuts the
 * name short.
 */
export function outputTitles(outputs: readonly OutputView[]) {
  const lines = new Map<string, OutputView[]>();
  for (const output of outputs) {
    const film = voiceLineOf(output);
    if (!film) continue;
    const key = `${output.conversation_id}\0${film}`;
    lines.set(key, [...(lines.get(key) ?? []), output]);
  }
  const titles = new Map<string, string>();
  for (const [key, members] of lines) {
    const film = key.slice(key.indexOf('\0') + 1);
    // Newest first in the list: the oldest is voice 1.
    const made = members
      .map((output, index) => ({ output, index }))
      .sort(
        (a, b) =>
          Date.parse(a.output.created_at) - Date.parse(b.output.created_at) ||
          b.index - a.index,
      );
    made.forEach(({ output }, index) =>
      titles.set(
        output.id,
        made.length > 1 ? `Voice ${index + 1} · ${film}` : `Voice · ${film}`,
      ),
    );
  }
  return (output: OutputView) => titles.get(output.id) ?? outputTitle(output);
}

/** "v2 of 3", or "" for an output with one version. */
export function versionWords(output: OutputView) {
  return output.version_count > 1
    ? `v${output.version} of ${output.version_count}`
    : '';
}

/** A refresh waits this long for the burst it is part of (a turn's files) to end. */
export const REFRESH_SETTLE_MS = 1000;

/**
 * One read of the outputs list, and again once `refresh` changes and holds
 * still for a moment; none while `enabled` is false (the open hasn't
 * settled). What it shows is always this filter's: another chat starts
 * empty and is read at once, and a refresh keeps the list until the new one
 * arrives.
 */
export function useOutputs(filter: OutputFilter, refresh = '', enabled = true) {
  const { controller } = useRuntime();
  const key = JSON.stringify(filter);
  const [read, setRead] = useState<{
    key: string;
    page: OutputPage | null;
    error: string;
  }>({ key, page: null, error: '' });
  const fetched = useRef('');
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    const load = () =>
      controller.outputs(JSON.parse(key) as OutputFilter, abort.signal).then(
        (result) => {
          if (abort.signal.aborted) return;
          fetched.current = key;
          setRead({ key, page: result ?? { items: [] }, error: '' });
        },
        (cause) => {
          if (!abort.signal.aborted)
            setRead((last) => ({
              key,
              page: last.key === key ? last.page : null,
              error: clientError(cause).message,
            }));
        },
      );
    const timer =
      fetched.current === key
        ? window.setTimeout(load, REFRESH_SETTLE_MS)
        : (load(), 0);
    return () => {
      window.clearTimeout(timer);
      abort.abort();
    };
  }, [controller, enabled, key, refresh]);
  return read.key === key
    ? { page: read.page, error: read.error }
    : { page: null, error: '' };
}
