import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  ChevronRight,
  Folder,
  FolderGit2,
  FolderOpen,
  GitBranchPlus,
} from 'lucide-react';
import type { WorkspaceFolderPage } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { useDialogFooter } from '../../ui/dialog-footer';
import { Button, InlineEmpty, Input, Skeleton } from '../../ui/primitives';

/** A folder chosen in the browser: its path under the root and where it is, in words. */
export type RootFolderChoice = { path: string; name: string; label: string };

type Purpose = 'folder' | 'parent';

/** "Row-Bot › Projects › tide-app" from the page's own crumbs. */
function where(page: WorkspaceFolderPage) {
  return page.crumbs.map((crumb) => crumb.name).join(' › ');
}

function host(url: string) {
  const trimmed = url.trim();
  const ssh = /^git@([^:]+):/.exec(trimmed);
  if (ssh) return ssh[1];
  try {
    return new URL(trimmed).hostname;
  } catch {
    return '';
  }
}

/** The folder a clone makes: the repository's own name. */
export function cloneName(url: string) {
  const last = url
    .trim()
    .replace(/[/\\]+$/, '')
    .split(/[/:]/)
    .at(-1);
  return (last ?? '').replace(/\.git$/i, '') || 'the repository';
}

/**
 * Row-Bot's workspace folder, folder by folder (F19): how a browser, a
 * phone or another computer chooses a code folder on the computer Row-Bot
 * runs on, where no folder picker can. Only folders inside the workspace
 * folder are listed, and the server checks every one it is asked for. A
 * folder opens with a tap; the one action uses the folder shown. "Clone
 * from URL" clones into the folder shown, after the person confirms the
 * download where their approvals ask.
 */
export function FolderBrowser({
  purpose,
  actionLabel,
  onUse,
  onCancel,
  cancelLabel = 'Cancel',
  clone,
  busy = false,
  title,
  dock = false,
}: {
  /** A code folder to work in, or a place to clone or create one in. */
  purpose: Purpose;
  /** The one action's words for the folder shown ("Use tide-app", "Clone here"). */
  actionLabel?: (name: string, isRoot: boolean) => string;
  onUse: (choice: RootFolderChoice) => void;
  onCancel?: () => void;
  /** The cancel action's words ("Not now" on a card in the chat). */
  cancelLabel?: string;
  /** Offer Clone from URL; `confirm` asks before the download (Ask). */
  clone?: {
    confirm: boolean;
    onClone: (url: string, into: RootFolderChoice) => void;
  };
  busy?: boolean;
  title?: string;
  /**
   * In a dialog: its actions take the dialog footer's place (one way out,
   * the primary always in view), and it scrolls itself into view.
   */
  dock?: boolean;
}) {
  const { controller } = useRuntime();
  const [path, setPath] = useState('');
  const [page, setPage] = useState<WorkspaceFolderPage | null>(null);
  const [problem, setProblem] = useState<{
    message: string;
    code: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [cloning, setCloning] = useState(false);
  const [url, setUrl] = useState('');
  const [confirming, setConfirming] = useState(false);
  const headingId = useId();
  const urlId = useId();

  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setProblem(null);
    controller
      .workspaceFolders(path, abort.signal)
      .then((result) => {
        if (abort.signal.aborted) return;
        setPage(result);
        setLoading(false);
      })
      .catch((cause) => {
        if (abort.signal.aborted) return;
        const error = clientError(cause);
        setProblem({ message: error.message, code: error.code });
        setLoading(false);
      });
    return () => abort.abort();
  }, [controller, path, attempt]);

  const shown = page && page.path === path ? page : null;
  const choice: RootFolderChoice | null = shown
    ? { path: shown.path, name: shown.name, label: where(shown) }
    : null;
  const canUse =
    Boolean(choice) && !busy && (purpose === 'parent' || !shown?.is_root);
  const label = shown
    ? (actionLabel?.(shown.name, shown.is_root) ?? `Use ${shown.name}`)
    : 'Use this folder';
  const repository = cloneName(url);
  const cloneReady = Boolean(url.trim()) && Boolean(choice) && !busy;

  function open(next: string) {
    setConfirming(false);
    setPath(next);
  }
  function startClone() {
    if (!clone || !choice || !cloneReady) return;
    if (clone.confirm && !confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    clone.onClone(url.trim(), choice);
  }

  const actions =
    clone && cloning ? (
      !confirming && (
        <div className="folder-browser-actions">
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              setCloning(false);
              setConfirming(false);
            }}
          >
            Back
          </Button>
          <Button variant="primary" disabled={!cloneReady} onClick={startClone}>
            <GitBranchPlus size={14} aria-hidden />
            Clone here
          </Button>
        </div>
      )
    ) : (
      <div className="folder-browser-actions">
        {onCancel && (
          <Button variant="ghost" disabled={busy} onClick={onCancel}>
            {cancelLabel}
          </Button>
        )}
        {clone && (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => setCloning(true)}
          >
            <GitBranchPlus size={14} aria-hidden />
            Clone from URL
          </Button>
        )}
        <Button
          variant="primary"
          disabled={!canUse}
          onClick={() => choice && onUse(choice)}
        >
          {label}
        </Button>
      </div>
    );
  const footer = useDialogFooter(dock);
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    // Opened in a dialog: the list comes into view (the actions are in the footer).
    if (dock) section.current?.scrollIntoView?.({ block: 'start' });
  }, [dock]);

  return (
    <section
      ref={section}
      className="folder-browser"
      aria-labelledby={headingId}
      aria-busy={loading || busy}
    >
      <div className="folder-browser-head">
        <strong id={headingId}>
          {title ??
            (purpose === 'parent' ? 'Choose where' : 'Choose a code folder')}
        </strong>
        <nav className="folder-browser-crumbs" aria-label="Folder path">
          <ol>
            {(shown?.crumbs ?? page?.crumbs ?? []).map((crumb, index, all) => (
              <li key={crumb.path || 'root'}>
                {index > 0 && (
                  <ChevronRight
                    className="folder-browser-separator"
                    aria-hidden
                  />
                )}
                {index === all.length - 1 ? (
                  <span aria-current="location">{crumb.name}</span>
                ) : (
                  <button
                    type="button"
                    className="folder-browser-crumb"
                    disabled={busy}
                    onClick={() => open(crumb.path)}
                  >
                    {crumb.name}
                  </button>
                )}
              </li>
            ))}
          </ol>
        </nav>
      </div>
      {problem ? (
        <div className="folder-browser-problem" role="alert">
          <p>{problem.message}</p>
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              if (path) open('');
              else setAttempt((value) => value + 1);
            }}
          >
            {path
              ? `Back to ${page?.crumbs[0]?.name ?? 'the workspace folder'}`
              : 'Try again'}
          </Button>
        </div>
      ) : loading && !shown ? (
        <Skeleton label="Reading folders" />
      ) : shown && shown.folders.length === 0 ? (
        <InlineEmpty
          className="folder-browser-empty"
          icon={<FolderOpen size={16} />}
        >
          {shown.is_root
            ? `No folders in ${shown.name} yet. Clone a repository here, or put a folder in it on this computer.`
            : purpose === 'folder'
              ? `No folders inside ${shown.name}. Use it, or go back.`
              : `No folders inside ${shown.name}.`}
        </InlineEmpty>
      ) : shown ? (
        <ul
          className="folder-browser-list"
          aria-label={`Folders in ${shown.name}`}
        >
          {shown.folders.map((folder) => (
            <li key={folder.path}>
              <button
                type="button"
                className="folder-browser-row"
                aria-label={
                  folder.is_git ? `${folder.name}, Git repository` : folder.name
                }
                disabled={busy}
                onClick={() => open(folder.path)}
              >
                {folder.is_git ? (
                  <FolderGit2 aria-hidden />
                ) : (
                  <Folder aria-hidden />
                )}
                <span className="folder-browser-name">{folder.name}</span>
                {folder.is_git && (
                  <span className="folder-browser-tag" aria-hidden>
                    Git
                  </span>
                )}
                <ChevronRight className="folder-browser-open" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {shown?.truncated && (
        <p className="folder-browser-note muted">
          Only the first {shown.folders.length} folders are listed.
        </p>
      )}
      {purpose === 'folder' && shown?.is_root && !(clone && cloning) && (
        <p className="folder-browser-note muted">
          Open the folder to work in, then use it.
        </p>
      )}
      {clone && cloning && (
        <div
          className="folder-browser-clone"
          role="group"
          aria-label="Clone from URL"
        >
          <label htmlFor={urlId}>Repository address</label>
          <Input
            id={urlId}
            value={url}
            maxLength={2048}
            disabled={busy}
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            placeholder="https://github.com/team/project.git"
            onChange={(event) => {
              setUrl(event.target.value);
              setConfirming(false);
            }}
          />
          {confirming && choice ? (
            <div
              className="folder-browser-confirm"
              role="group"
              aria-label="Confirm the download"
            >
              <p>
                <strong>Clone {repository}?</strong> Row-Bot downloads it
                {host(url) ? ` from ${host(url)}` : ''} into {choice.label},
                with this computer&apos;s own Git sign-in.
              </p>
              <div className="action-cluster">
                <Button disabled={busy} onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  disabled={!cloneReady}
                  onClick={startClone}
                >
                  Clone
                </Button>
              </div>
            </div>
          ) : (
            <p className="muted folder-browser-note">
              Downloads into {choice?.label ?? 'this folder'}, with this
              computer&apos;s own Git sign-in.
            </p>
          )}
        </div>
      )}
      {footer ? createPortal(actions, footer) : actions}
    </section>
  );
}
