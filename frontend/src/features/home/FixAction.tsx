import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Cpu, LogIn, Play, RefreshCw, RotateCw, Wrench } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type { ProblemFix } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button, Hint, IconButton } from '../../ui/primitives';
import ConnectedAccountAuth from '../settings/AccountAuthControls';
import { taskRuns } from '../tasks/task-runs';

/** What a fix says on its button: one verb and the thing it acts on. */
export function fixLabel(fix: ProblemFix): string {
  switch (fix.kind) {
    case 'restart_channel':
      return `Restart ${fix.name}`;
    case 'reconnect_account':
      return `Reconnect ${fix.name}`;
    case 'choose_model':
      return 'Choose a model';
    case 'check_again':
      return `Check ${fix.name} again`;
    default:
      return fix.href?.startsWith('/settings/')
        ? `Open ${fix.name} settings`
        : `Open ${fix.name}`;
  }
}

const ICONS = {
  restart_channel: RotateCw,
  reconnect_account: LogIn,
  choose_model: Cpu,
  check_again: RefreshCw,
  open: Wrench,
} as const;

/**
 * Start a stopped channel through the reviewed `channel.control` path the
 * channel's Settings row uses: read its current revision, review `start`,
 * then run exactly the reviewed command. Never repeats an uncertain one.
 */
export async function restartChannel(
  controller: ClientController,
  channelId: string,
  name: string,
): Promise<string> {
  const page = await controller.channels('');
  const channel = page.items.find((item) => item.channel_id === channelId);
  if (!channel)
    throw new Error(`${name} isn't set up here any more. Open its settings.`);
  if (channel.running) return `${name} is running.`;
  const payload = {
    channel_id: channel.channel_id,
    revision: channel.revision,
    operation: 'start' as const,
    field_key: null,
    value: null,
    identity_id: null,
  };
  const review = await controller.reviewChannel(payload);
  if (
    review.channel_id !== payload.channel_id ||
    review.revision !== payload.revision ||
    review.operation !== payload.operation ||
    review.field_key !== null ||
    review.identity_id !== null
  )
    throw new Error(
      `The restart of ${name} couldn't be checked. Refresh and try again.`,
    );
  const receipt = await controller.executeChannel({
    command_id: crypto.randomUUID(),
    type: 'channel.control',
    payload: { ...payload, review_id: review.review_id },
  });
  if (receipt.status === 'completed' && receipt.code !== 'channel_start_failed')
    return `${name} is running again.`;
  if (receipt.status === 'completed')
    throw new Error(`${name} didn't start. Check its settings.`);
  if (receipt.status === 'rejected')
    throw new Error(`Row-Bot refused to restart ${name}. Check its settings.`);
  throw new Error(
    `Row-Bot couldn't confirm that ${name} restarted. Check its settings before trying again.`,
  );
}

type Look = 'icon' | 'text';

/** A button-like control: an icon with its name as a tooltip, or both. */
function FixButton({
  fix,
  look,
  busy,
  onClick,
}: {
  fix: ProblemFix;
  look: Look;
  busy?: boolean;
  onClick: () => void;
}) {
  const Icon = ICONS[fix.kind];
  const label = fixLabel(fix);
  return look === 'icon' ? (
    <IconButton
      size="sm"
      label={label}
      disabled={busy}
      aria-busy={busy || undefined}
      onClick={onClick}
    >
      <Icon size={15} aria-hidden />
    </IconButton>
  ) : (
    <Button
      className="small fix-action"
      disabled={busy}
      aria-busy={busy || undefined}
      onClick={onClick}
    >
      <Icon size={14} aria-hidden />
      {label}
    </Button>
  );
}

/** Opening the exact place where it is fixed by hand. */
function FixLink({ fix, look }: { fix: ProblemFix; look: Look }) {
  if (!fix.href) return null;
  const Icon = ICONS[fix.kind];
  const label = fixLabel(fix);
  return look === 'icon' ? (
    <Hint label={label}>
      <Link
        className="button ghost icon-button icon-action icon-action-sm"
        to={fix.href}
        aria-label={label}
      >
        <Icon size={15} aria-hidden />
      </Link>
    </Hint>
  ) : (
    <Link className="button small fix-action" to={fix.href}>
      <Icon size={14} aria-hidden />
      {label}
    </Link>
  );
}

function Note({ alert, children }: { alert?: boolean; children: ReactNode }) {
  return (
    <p role={alert ? 'alert' : 'status'} className="fix-action-note">
      {children}
    </p>
  );
}

function RestartChannelFix({
  fix,
  look,
  onFixed,
}: {
  fix: ProblemFix;
  look: Look;
  onFixed?: () => void;
}) {
  const { controller } = useRuntime();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState('');
  const [error, setError] = useState('');
  async function restart() {
    if (busy || !fix.target) return;
    setBusy(true);
    setError('');
    try {
      setDone(await restartChannel(controller, fix.target, fix.name));
      onFixed?.();
    } catch (cause) {
      setError(
        cause instanceof Error && !('code' in cause)
          ? cause.message
          : clientError(cause).message,
      );
    } finally {
      setBusy(false);
    }
  }
  if (done) return <Note>{done}</Note>;
  return (
    <>
      <FixButton
        fix={fix}
        look={look}
        busy={busy}
        onClick={() => void restart()}
      />
      {error && <Note alert>{error}</Note>}
    </>
  );
}

/** A Google or X sign-in renewed with its row's own reviewed actions. */
function ReconnectFix({
  fix,
  look,
  onFixed,
}: {
  fix: ProblemFix & { target: 'google' | 'x' };
  look: Look;
  onFixed?: () => void;
}) {
  return (
    <ConnectedAccountAuth account={fix.target} onChanged={onFixed}>
      {(auth) =>
        // Sign-in runs on the computer Row-Bot runs on; elsewhere, its row.
        auth.localOnly ? (
          <FixLink fix={{ ...fix, kind: 'open' }} look={look} />
        ) : (
          <>
            <FixButton
              fix={fix}
              look={look}
              busy={!auth.snapshot || auth.locked}
              onClick={auth.start}
            />
            {auth.feedback && (
              <div className="fix-action-note">{auth.feedback}</div>
            )}
          </>
        )
      }
    </ConnectedAccountAuth>
  );
}

/**
 * A problem's one fix (Phase 18): restart a channel or renew a sign-in in
 * place through the owners Settings uses, check again, or open the exact
 * setting. Monitor tiles, its Needs attention list and Overview's Needs you
 * rows use it.
 */
export default function FixAction({
  fix,
  look = 'text',
  onCheckAgain,
  onFixed,
}: {
  fix: ProblemFix;
  look?: Look;
  /** Run the checks again (Monitor); without it the fix opens its place. */
  onCheckAgain?: () => void;
  /** Something changed (a channel started, a sign-in renewed): re-read. */
  onFixed?: () => void;
}) {
  if (fix.kind === 'restart_channel' && fix.target)
    return <RestartChannelFix fix={fix} look={look} onFixed={onFixed} />;
  if (
    fix.kind === 'reconnect_account' &&
    (fix.target === 'google' || fix.target === 'x')
  )
    return (
      <ReconnectFix
        fix={{ ...fix, target: fix.target }}
        look={look}
        onFixed={onFixed}
      />
    );
  if (fix.kind === 'check_again' && onCheckAgain)
    return <FixButton fix={fix} look={look} onClick={onCheckAgain} />;
  return (
    <FixLink
      fix={fix.kind === 'check_again' ? { ...fix, kind: 'open' } : fix}
      look={look}
    />
  );
}

/**
 * A failed workflow's fix: run it again now, reviewed and started exactly as
 * Workflows' Run does (B254), so its approvals and delivery stay as set.
 */
export function RunAgain({
  taskId,
  name,
  onStarted,
}: {
  taskId: string;
  name: string;
  /** It started: re-read the workflows. */
  onStarted?: () => void;
}) {
  const { controller } = useRuntime();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState('');
  const [error, setError] = useState('');
  async function run() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await taskRuns(controller).run(await controller.taskRunReview(taskId));
      setDone(`${name} is running again.`);
      onStarted?.();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  if (done) return <Note>{done}</Note>;
  return (
    <>
      <IconButton
        size="sm"
        label={`Run ${name} again`}
        disabled={busy}
        aria-busy={busy || undefined}
        onClick={() => void run()}
      >
        <Play size={15} aria-hidden />
      </IconButton>
      {error && <Note alert>{error}</Note>}
    </>
  );
}
