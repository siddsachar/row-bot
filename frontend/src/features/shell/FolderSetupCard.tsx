import { useEffect, useState } from 'react';
import { FolderCode, FolderGit2, FolderOpen } from 'lucide-react';
import type { ApprovalSetup, CommandReceipt } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, Progress } from '../../ui/primitives';

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
 * ever inspected again), and only then is the waiting turn let go on. A
 * browser, which cannot pick folders on this computer, says where to do it.
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
    void run(async () => {
      const grant = await pick('resource_setup');
      if (!grant) return;
      await setUp({
        kind: 'workspace',
        intent: 'create',
        folder_grant: grant,
        ...(clone ? { clone_workspace: { repo_url: setup.repo_url } } : {}),
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
      const grant = previous.folder_reselection_required
        ? await pick('resource_continue')
        : null;
      if (previous.folder_reselection_required && !grant) return;
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
          },
          current.revision,
        ),
      );
    });
  }

  const unconfirmedClone = partial?.code === 'workspace_clone_unconfirmed';
  const canPick = Boolean(conversationId) && desktop !== false;
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
          {canPick
            ? reason
            : 'Choose the folder in the Row-Bot desktop app, or add it with Add resource, then Continue.'}
        </span>
      </div>
      {canPick && choices.length > 0 && !partial && !bound && (
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
      {working && clone && !partial && (
        <div className="approval-card-status">
          <Progress label={`Cloning ${setup.label}…`} />
        </div>
      )}
      {partial && (
        <p className="approval-card-status">
          {unconfirmedClone
            ? 'The clone may be incomplete. Choose the same parent folder to check it; Row-Bot never repeats an uncertain clone.'
            : partial.folder_reselection_required
              ? 'The folder was set up but not finished. Choose the same folder again to finish.'
              : `Setup didn't finish. ${clientError({ code: partial.code ?? '' }).message}`}
        </p>
      )}
      <div className="approval-card-actions">
        <Button disabled={!idle} onClick={() => void onDecide('reject')}>
          Not now
        </Button>
        {!canPick || bound ? (
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
