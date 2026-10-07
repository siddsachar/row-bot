/**
 * Commit and pull request text suggested from what changed: the agent's own
 * change-set summaries when they cover the change, otherwise the changed
 * paths. Computed locally; nothing is sent to a model.
 */

export type SuggestionFile = { path: string; status: string };
export type SuggestionChangeSet = {
  summary: string;
  reverted: boolean;
  /** Paths recorded for this change set, when they were read. */
  paths?: string[];
};

export type Suggestion = {
  subject: string;
  body: string;
};

function verb(status: string) {
  const code = status.trim().toUpperCase();
  if (code === '??' || code.startsWith('A')) return 'Add';
  if (code.startsWith('D')) return 'Remove';
  if (code.startsWith('R')) return 'Rename';
  return 'Update';
}

function commonFolder(paths: string[]) {
  const parts = paths.map((path) => path.split('/').slice(0, -1));
  if (!parts.length) return '';
  const first = parts[0];
  let length = first.length;
  for (const other of parts.slice(1)) {
    let index = 0;
    while (index < length && other[index] === first[index]) index++;
    length = index;
  }
  return first.slice(0, length).join('/');
}

function name(path: string) {
  return path.split('/').at(-1) ?? path;
}

function clip(text: string, limit = 72) {
  const line = text.replace(/\s+/g, ' ').trim();
  return line.length > limit ? `${line.slice(0, limit - 1).trimEnd()}…` : line;
}

function list(paths: string[], limit = 12) {
  const shown = paths.slice(0, limit).map((path) => `- ${path}`);
  if (paths.length > limit) shown.push(`- …and ${paths.length - limit} more`);
  return shown.join('\n');
}

export function suggestCommit(
  files: SuggestionFile[],
  changeSets: SuggestionChangeSet[],
): Suggestion | null {
  if (!files.length) return null;
  const paths = files.map((file) => file.path);
  const changed = new Set(paths);
  const relevant = changeSets.filter(
    (set) =>
      !set.reverted &&
      set.summary.trim() &&
      (!set.paths || set.paths.some((path) => changed.has(path))),
  );
  if (relevant.length === 1) {
    return {
      subject: clip(relevant[0].summary),
      body: list(paths),
    };
  }
  let subject: string;
  if (files.length === 1)
    subject = `${verb(files[0].status)} ${name(files[0].path)}`;
  else {
    const verbs = new Set(files.map((file) => verb(file.status)));
    const action = verbs.size === 1 ? [...verbs][0] : 'Update';
    const folder = commonFolder(paths);
    subject = `${action} ${files.length} files${folder ? ` in ${folder}` : ''}`;
  }
  const body = relevant.length
    ? `${relevant.map((set) => `- ${clip(set.summary, 100)}`).join('\n')}\n\nFiles:\n${list(paths)}`
    : list(paths);
  return { subject: clip(subject), body };
}
