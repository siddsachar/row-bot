import * as Popover from '@radix-ui/react-popover';
import { ExternalLink, FileText, Info, ShieldAlert } from 'lucide-react';
import type { WorkspaceProjectNote } from '../../api/types';
import { Hint, IconButton } from '../../ui/primitives';

const LIMIT = 8_000;

function count(value: number) {
  return value.toLocaleString('en-GB');
}

/** The state of one file in the person's words. */
export function noteState(note: WorkspaceProjectNote): string {
  switch (note.state) {
    case 'used':
      return 'Used';
    case 'cut':
      return note.used_characters
        ? `Used in part: the first ${count(note.used_characters)} of ${count(note.characters)} characters`
        : `Not shown: the notes are at their ${count(LIMIT)}-character limit`;
    case 'not_used_injection':
      return 'Not used: it tries to give the agent new instructions';
    case 'not_used_secret':
      return 'Not used: it looks like it contains a password or key';
    case 'not_used_outside':
      return 'Not used: it links to a file outside this folder';
    case 'too_large':
      return 'Not used: it is larger than 64 KB';
    case 'unreadable':
      return "Not used: it couldn't be read as text";
  }
}

/** Flagged or refused: warned, and named as left out. */
const left = (note: WorkspaceProjectNote) =>
  note.state !== 'used' && note.state !== 'cut';
/** The budget ran out before this file: named as not shown, without a warning. */
const unshown = (note: WorkspaceProjectNote) =>
  note.state === 'cut' && !note.used_characters;

/**
 * The code folder's project notes on the details card: one quiet line naming
 * the files Row-Bot reads, and a popover with each file's state. Nothing shows
 * when the folder has none.
 */
export default function ProjectNotes({
  notes,
  onOpenFile,
}: {
  notes: readonly WorkspaceProjectNote[];
  onOpenFile: (path: string) => void;
}) {
  if (!notes.length) return null;
  const used = notes
    .filter((note) => !left(note) && !unshown(note))
    .map((note) => note.path);
  const notShown = notes.filter(unshown).map((note) => note.path);
  const leftOut = notes.filter(left).map((note) => note.path);
  const summary = [
    used.join(', '),
    notShown.length ? `${notShown.join(', ')} not shown` : '',
    leftOut.length ? `${leftOut.join(', ')} left out` : '',
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <div
      className="context-project-notes"
      data-state={leftOut.length ? 'warning' : undefined}
    >
      <span className="context-project-notes-line">
        {leftOut.length ? (
          <ShieldAlert size={14} aria-hidden />
        ) : (
          <FileText size={14} aria-hidden />
        )}
        <span>Project notes: {summary}</span>
      </span>
      <Popover.Root>
        <Hint label="Row-Bot reads these files' instructions for agents when it works here">
          <Popover.Trigger asChild>
            <IconButton
              label="About project notes"
              tooltip={false}
              size="sm"
              className="context-project-notes-info"
            >
              <Info size={14} aria-hidden />
            </IconButton>
          </Popover.Trigger>
        </Hint>
        <Popover.Portal>
          <Popover.Content
            className="popover surface-effect project-notes-popover"
            side="bottom"
            align="end"
            sideOffset={6}
            collisionPadding={12}
            aria-label="Project notes"
            tabIndex={-1}
            // Focus the popover itself: a first row's button would open its
            // tooltip over the list as it appears.
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              (event.currentTarget as HTMLElement | null)?.focus();
            }}
          >
            <div className="project-notes-head">
              <strong>Project notes</strong>
              <p>
                Row-Bot follows these files for how to build, test and style
                this code. They can't change approvals or ask it to send
                anything.
              </p>
            </div>
            <ul className="project-notes-list">
              {notes.map((note) => (
                <li
                  key={note.path}
                  data-state={left(note) ? 'warning' : 'used'}
                >
                  {left(note) ? (
                    <ShieldAlert size={16} aria-hidden />
                  ) : (
                    <FileText size={16} aria-hidden />
                  )}
                  <span className="project-notes-file">
                    <span>{note.path}</span>
                    <small>{noteState(note)}</small>
                  </span>
                  {note.state !== 'not_used_outside' && (
                    <Popover.Close asChild>
                      <IconButton
                        label={`Open ${note.path}`}
                        size="sm"
                        onClick={() => onOpenFile(note.path)}
                      >
                        <ExternalLink size={14} aria-hidden />
                      </IconButton>
                    </Popover.Close>
                  )}
                </li>
              ))}
            </ul>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  );
}
