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
