import { useEffect, useState } from 'react';
import { FolderCode, FolderGit2, FolderOpen } from 'lucide-react';
import type { ApprovalSetup, CommandReceipt } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, Progress } from '../../ui/primitives';
import {
  FolderBrowser,
  cloneName,
  type RootFolderChoice,
} from './FolderBrowser';

type FolderSetup = ApprovalSetup & { kind: 'folder' | 'clone' };

const PICK = {
  folder: 'workspace:existing_folder',
  clone: 'workspace:clone_repository',
} as const;

/** Why a pick didn't happen, in the card's own words. */
class PickProblem extends Error {}

/**
 * The turn waits while the person brings in a code folder (B277): "Use an
 * existing folder" with Choose folder, a pick between registered folders
 * that share a name, or a repository with Choose where. Every choice is Add
 * resource's own: the desktop picker's grant, then the reviewed
 * `resource.setup` (a clone runs there, once, and an uncertain clone is only
 * ever inspected again), and only then is the waiting turn let go on.
 * Without the desktop picker (a browser, a phone, another computer) the
 * card offers Row-Bot's workspace folder, folder by folder, instead (F19).
 */
export default function FolderSetupCard({
  setup,
  reason,
  conversationId,
  ready,
  resolution,
  error,
  onDecide,
}: {
  setup: FolderSetup;
  reason: string;
  conversationId?: string;
  /** The approval was read and is still unanswered. */
  ready: boolean;
  resolution: string;
  error: string;
  onDecide: (decision: 'approve' | 'reject') => Promise<void>;
}) {
  const { controller, platform } = useRuntime();
  // null while unknown keeps Choose folder; a pick then explains itself.
  const [desktop, setDesktop] = useState<boolean | null>(null);
  const [working, setWorking] = useState(false);
  const [partial, setPartial] = useState<CommandReceipt | null>(null);
  // The folder is bound; only the turn's go-ahead is left (it can be retried).
  const [bound, setBound] = useState(false);
  const [failure, setFailure] = useState('');
  // Without a picker: the folder browser is open (a clone card opens it on Choose where).
  const [browsing, setBrowsing] = useState(setup.kind === 'folder');
  // The parent a clone went into from the browser, to check an uncertain clone again.
  const [parent, setParent] = useState<RootFolderChoice | null>(null);
  // Cloning from the browser asks first under Ask (and while the level is unknown).
  const [confirmDownload, setConfirmDownload] = useState(true);
  useEffect(() => {
    let current = true;
    Promise.resolve()
      .then(() => platform.discover())
      .then((result) => {
        if (current)
          setDesktop(
            result.status === 'ok' && result.value.kind === 'pywebview',
          );
      })
      .catch(() => {
        if (current) setDesktop(null);
      });
    return () => {
      current = false;
    };
  }, [platform]);
  useEffect(() => {
    if (desktop !== false || !conversationId) return;
    let current = true;
    Promise.resolve()
      .then(() => controller.workspaceFor(conversationId))
      .then((workspace) => {
        const level = workspace?.controls?.approval_mode;
        if (current) setConfirmDownload(level !== 'auto' && level !== 'full');
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [controller, conversationId, desktop]);
  const clone = setup.kind === 'clone';
  const choices = setup.folders ?? [];
  const title = clone
    ? 'Clone a repository'
    : choices.length
      ? 'Which code folder?'
      : 'Use an existing folder';
  const Icon = clone ? FolderGit2 : FolderOpen;
  const idle = ready && !working && !resolution;

  async function run(work: () => Promise<void>) {
    setWorking(true);
    setFailure('');
    try {
      await work();
    } catch (cause) {
      setFailure(
        cause instanceof PickProblem
          ? cause.message
          : clientError(cause).message,
      );
    } finally {
      setWorking(false);
    }
  }
  /** The desktop picker's one-shot grant, or null when cancelled. */
  async function pick(intent: 'resource_setup' | 'resource_continue') {
    const picked = await platform.selectFolder(undefined, {
      intentId: crypto.randomUUID(),
      intent,
      conversationId: conversationId ?? null,
      destination: PICK[setup.kind],
    });
    if (picked.status === 'cancelled') return null;
    if (
      picked.status === 'ok' &&
      'reference' in picked.value &&
      picked.value.kind === 'folder'
    )
      return picked.value.reference;
    throw new PickProblem(
      picked.status === 'unavailable' && picked.reason === 'native_reconnecting'
        ? 'Desktop features are reconnecting. Try again in a moment.'
        : 'Choosing a folder needs the Row-Bot desktop app.',
    );
  }
  async function settle(receipt: CommandReceipt) {
    if (receipt.status === 'completed') {
      setPartial(null);
      setBound(true);
      setBrowsing(false);
      // Bound: the waiting turn goes on in it.
      await onDecide('approve');
      return;
    }
    if (!receipt.resource_id) {
      setPartial(null);
      throw new PickProblem(
        "Row-Bot couldn't confirm the folder was made. Check the folder you chose before trying again; it won't make another one by itself.",
      );
    }
    setBrowsing(false);
    setPartial(receipt);
  }
  async function setUp(payload: object) {
    const current = await controller.workspaceFor(conversationId!);
    await settle(
      await controller.intent(
        conversationId!,
        'resource.setup',
        payload,
        current.revision,
      ),
    );
  }
  function choose() {
    if (desktop === false) {
      setBrowsing(true);
      return;
    }
    void run(async () => {
      const grant = await pick('resource_setup');
      if (!grant) return;
      await setUp({
        kind: 'workspace',
        intent: 'create',
        folder_grant: grant,
        // This card is how the clone asks: the person chose where it goes.
        ...(clone
          ? {
              clone_workspace: { repo_url: setup.repo_url },
              confirm_network: true,
            }
          : {}),
      });
    });
  }
  /** A folder chosen in the workspace folder: the code folder, or where the card's clone goes. */
  function useRoot(choice: RootFolderChoice) {
    void run(async () => {
      if (clone) setParent(choice);
      await setUp({
        kind: 'workspace',
        intent: 'create',
        root_folder: choice.path,
        ...(clone
          ? {
              clone_workspace: { repo_url: setup.repo_url },
              confirm_network: true,
            }
          : {}),
      });
    });
  }
  /** Not there yet: the person clones it from the folder card, into the folder shown. */
  function cloneRoot(url: string, into: RootFolderChoice) {
    void run(async () => {
      setParent(into);
      await setUp({
        kind: 'workspace',
        intent: 'create',
        root_folder: into.path,
        clone_workspace: { repo_url: url },
        confirm_network: true,
      });
    });
  }
  function pickRegistered(
    folder: NonNullable<ApprovalSetup['folders']>[number],
  ) {
    void run(() =>
      setUp({
        kind: 'workspace',
        intent: 'add',
        resource_id: folder.resource_id,
        expected_resource_revision: folder.revision,
      }),
    );
  }
  /** Finish a setup that stopped partway; a clone is never run again. */
  function finish() {
    const previous = partial;
    if (!previous) return;
    void run(async () => {
      const again = previous.folder_reselection_required;
      const fromRoot = again && desktop === false && parent !== null;
      const grant = again && !fromRoot ? await pick('resource_continue') : null;
      if (again && !fromRoot && !grant) return;
      const current = await controller.workspaceFor(conversationId!);
      await settle(
        await controller.intent(
          conversationId!,
          'resource.continue',
          {
            setup_command_id: previous.setup_command_id ?? previous.command_id,
            ...(previous.resource_revision
              ? { expected_resource_revision: previous.resource_revision }
              : {}),
            ...(grant ? { folder_grant: grant } : {}),
            ...(fromRoot && parent ? { root_folder: parent.path } : {}),
          },
          current.revision,
        ),
      );
    });
  }

  const unconfirmedClone = partial?.code === 'workspace_clone_unconfirmed';
  const canPick = Boolean(conversationId) && desktop !== false;
  // No picker here: the workspace folder is browsed in the card instead.
  const browser = Boolean(conversationId) && desktop === false;
  const showBrowser = browser && browsing && !partial && !bound;
  const cloningNow = working && (clone || parent !== null) && !partial;
  return (
    <aside
      className="approval-card folder-setup-card"
      data-kind="setup"
      aria-label={title}
    >
      <span className="approval-card-icon" aria-hidden>
        <Icon />
      </span>
      <div className="approval-card-context">
        <strong>{title}</strong>
        {clone && (
          <code className="approval-card-argument" title={setup.repo_url}>
            {setup.repo_url}
          </code>
        )}
        <span className="approval-card-reason">
          {canPick || browser
            ? reason
            : 'Choose the folder in the Row-Bot desktop app, or add it with Add resource, then Continue.'}
        </span>
      </div>
      {Boolean(conversationId) && choices.length > 0 && !partial && !bound && (
        <ul className="folder-setup-choices" aria-label="Code folders">
          {choices.map((folder) => (
            <li key={folder.resource_id}>
              <Button
                variant="ghost"
                disabled={!idle}
                onClick={() => pickRegistered(folder)}
              >
                <FolderCode aria-hidden />
                {folder.name}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {showBrowser && (
        <FolderBrowser
          purpose={clone ? 'parent' : 'folder'}
          title={
            clone
              ? `Where should ${cloneName(setup.repo_url ?? '')} go?`
              : choices.length
                ? 'Or choose another folder'
                : undefined
          }
          actionLabel={clone ? () => 'Clone here' : undefined}
          busy={!idle}
          onCancel={() => void onDecide('reject')}
          cancelLabel="Not now"
          onUse={useRoot}
          clone={
            clone ? undefined : { confirm: confirmDownload, onClone: cloneRoot }
          }
        />
      )}
      {cloningNow && (
        <div className="approval-card-status">
          <Progress
            label={`Cloning ${clone ? setup.label : 'the repository'}…`}
          />
        </div>
      )}
      {partial && (
        <p className="approval-card-status">
          {unconfirmedClone
            ? browser
              ? 'The clone may be incomplete. Check it to finish; Row-Bot never repeats an uncertain clone.'
              : 'The clone may be incomplete. Choose the same parent folder to check it; Row-Bot never repeats an uncertain clone.'
            : partial.folder_reselection_required
              ? browser
                ? 'The folder was set up but not finished. Continue to finish it.'
                : 'The folder was set up but not finished. Choose the same folder again to finish.'
              : `Setup didn't finish. ${clientError({ code: partial.code ?? '' }).message}`}
        </p>
      )}
      {/* The folder browser carries the card's actions while it is open. */}
      {!showBrowser && (
        <div className="approval-card-actions">
          <Button disabled={!idle} onClick={() => void onDecide('reject')}>
            Not now
          </Button>
          {(!canPick && !browser) || bound ? (
            <Button
              variant="primary"
              disabled={!idle}
              onClick={() => void onDecide('approve')}
            >
              Continue
            </Button>
          ) : partial ? (
            <Button variant="primary" disabled={!idle} onClick={finish}>
              {unconfirmedClone ? 'Check clone status' : 'Continue setup'}
            </Button>
          ) : (
            <Button variant="primary" disabled={!idle} onClick={choose}>
              {clone
                ? 'Choose where'
                : choices.length
                  ? 'Choose another folder'
                  : 'Choose folder'}
            </Button>
          )}
        </div>
      )}
      {resolution && (
        <p role="status" className="approval-card-status">
          {resolution === 'Approval submitted.'
            ? 'Going on in the folder…'
            : 'No folder was added.'}
        </p>
      )}
      {(failure || error) && (
        <p role="alert" className="approval-card-status">
          {failure || error}
        </p>
      )}
    </aside>
  );
}
