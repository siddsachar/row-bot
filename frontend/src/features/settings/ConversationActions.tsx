import {
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { FileDown, FileText, Trash2 } from 'lucide-react';
import type { ConversationView } from '../../api/types';
import type { CapabilityResult, SavedFile } from '../../platform';
import { parseTimestamp, relativeTime } from '../../ui/format';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  Input,
  SettingRow,
  Skeleton,
  Toggle,
} from '../../ui/primitives';
import {
  conversationKinds,
  type ConversationKind,
} from '../shell/conversation-groups';

export type ConversationAction =
  | 'conversation.rename'
  | 'conversation.pin'
  | 'conversation.archive'
  | 'conversation.export';
/** A conversation export: Markdown (the default) or PDF. */
export type ExportFormat = 'markdown' | 'pdf';
export type ConversationActionCapability = {
  available: boolean;
  code: string | null;
};
export type ConversationActionSnapshot = {
  schema_version: 1;
  conversation_id: string;
  revision: string;
  checkpoint_revision: string;
  title: string;
  pinned: boolean;
  capabilities: Record<
    'rename' | 'pin' | 'archive' | 'export',
    ConversationActionCapability
  >;
};
export type ConversationActionReview = {
  schema_version: 1;
  conversation_id: string;
  action: ConversationAction;
  revision: string;
  checkpoint_revision: string;
  fields: Record<string, unknown>;
  action_digest: string;
  summary: string;
  disclosures: string[];
  review_id?: string;
};
export type ConversationActionCommand = {
  command_id: string;
  type: ConversationAction;
  expected_revision: string;
  payload: Record<string, unknown> & {
    checkpoint_revision: string;
    action_digest: string;
  };
};
export type ConversationActionReceipt = {
  command_id: string;
  status: string;
  code?: string | null;
  action: ConversationAction;
  conversation?: {
    conversation_id: string;
    revision: string;
    title: string;
    pinned: boolean;
  };
  export?: {
    attachment_ref: string;
    file_name: string;
    size_bytes: number;
    checkpoint_revision: string;
  };
};

type Attempt = {
  command: ConversationActionCommand;
  review: ConversationActionReview;
};
type State = {
  active: boolean;
  conversationId: string;
  snapshot: ConversationActionSnapshot | null;
  title: string;
  reviewed: Attempt | null;
  pending: Attempt | null;
  exported: ConversationActionReceipt['export'] | null;
  busy: string;
  message: string;
};

export function createConversationActionsSession(conversationId: string) {
  let state: State = {
    active: true,
    conversationId,
    snapshot: null,
    title: '',
    reviewed: null,
    pending: null,
    exported: null,
    busy: '',
    message: '',
  };
  const listeners = new Set<() => void>();
  const reads = new Set<AbortController>();
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    update,
    beginRead: () => {
      const controller = new AbortController();
      if (state.active) reads.add(controller);
      else controller.abort();
      return controller;
    },
    endRead: (controller: AbortController) => reads.delete(controller),
    hasRetained: () =>
      state.active &&
      Boolean(state.reviewed || state.pending || state.exported),
    dispose: () => {
      reads.forEach((controller) => controller.abort());
      reads.clear();
      state = {
        active: false,
        conversationId: '',
        snapshot: null,
        title: '',
        reviewed: null,
        pending: null,
        exported: null,
        busy: '',
        message: 'Sign in again to manage conversation actions.',
      };
      listeners.forEach((listener) => listener());
    },
  };
}
export type ConversationActionsSession = ReturnType<
  typeof createConversationActionsSession
>;
export type ConversationActionsApi = {
  load: (
    conversationId: string,
    signal: AbortSignal,
  ) => Promise<ConversationActionSnapshot>;
  review: (
    conversationId: string,
    action: ConversationAction,
    revision: string,
    fields: Record<string, unknown>,
    signal: AbortSignal,
  ) => Promise<ConversationActionReview>;
  execute: (
    conversationId: string,
    command: ConversationActionCommand,
    review: ConversationActionReview,
  ) => Promise<ConversationActionReceipt>;
};
export type ConversationActionsProps = ConversationActionsApi & {
  conversationId: string;
  session: ConversationActionsSession;
  /** The platform's save: the Save dialog, Exports or a browser download. */
  save: (
    reference: string,
    fileName: string,
  ) => Promise<CapabilityResult<SavedFile>>;
  onChanged?: (
    conversation: NonNullable<ConversationActionReceipt['conversation']>,
  ) => void;
  /**
   * Opens the existing delete confirmation over this dialog. Its confirm
   * calls `closeActions`, so this dialog closes with it.
   */
  onDelete?: (closeActions: () => void) => void;
  initialPin?: boolean;
  /** Start this export as soon as the dialog opens (true means Markdown). */
  initialExport?: boolean | ExportFormat;
};

function checkedSnapshot(
  value: ConversationActionSnapshot,
  conversationId: string,
) {
  if (
    value.schema_version !== 1 ||
    value.conversation_id !== conversationId ||
    value.title.length > 256
  )
    throw Error();
  return value;
}

/** A review answers exactly what was asked, at the revision asked about. */
function checkedReview(
  result: ConversationActionReview,
  conversationId: string,
  action: ConversationAction,
  snapshot: ConversationActionSnapshot,
) {
  if (
    result.schema_version !== 1 ||
    result.conversation_id !== conversationId ||
    result.action !== action ||
    result.revision !== snapshot.revision ||
    result.checkpoint_revision !== snapshot.checkpoint_revision
  )
    throw Error();
  return result;
}

/** The one command a review allows: its fields, digest and revisions. */
function reviewedCommand(
  action: ConversationAction,
  fields: Record<string, unknown>,
  result: ConversationActionReview,
): ConversationActionCommand {
  return {
    command_id: crypto.randomUUID(),
    type: action,
    expected_revision: result.revision,
    payload: {
      ...(action === 'conversation.export' ? {} : fields),
      checkpoint_revision: result.checkpoint_revision,
      action_digest: result.action_digest,
      ...(action === 'conversation.export'
        ? {
            export_title: result.fields.title,
            ...(fields.format === 'pdf' ? { export_format: 'pdf' } : {}),
          }
        : {}),
    },
  };
}

export type ConversationActionOutcome =
  | { status: 'completed'; receipt: ConversationActionReceipt }
  /** Unavailable, changed or rejected: nothing was done. */
  | { status: 'failed' }
  /** Sent, but without a clear receipt. */
  | { status: 'uncertain' };

/**
 * One reviewed action without the dialog, for the Library's bulk Pin and
 * Export: the same read, review, revision checks and command.
 */
export async function runConversationAction(
  api: ConversationActionsApi,
  conversationId: string,
  action: 'conversation.pin' | 'conversation.export',
  fields: Record<string, unknown>,
  signal: AbortSignal,
): Promise<ConversationActionOutcome> {
  let attempt: Attempt;
  try {
    const snapshot = checkedSnapshot(
      await api.load(conversationId, signal),
      conversationId,
    );
    const capability = action === 'conversation.pin' ? 'pin' : 'export';
    if (!snapshot.capabilities[capability].available)
      return { status: 'failed' };
    const review = checkedReview(
      await api.review(
        conversationId,
        action,
        snapshot.revision,
        fields,
        signal,
      ),
      conversationId,
      action,
      snapshot,
    );
    attempt = { command: reviewedCommand(action, fields, review), review };
  } catch {
    return { status: 'failed' };
  }
  if (signal.aborted) return { status: 'failed' };
  try {
    const receipt = await api.execute(
      conversationId,
      attempt.command,
      attempt.review,
    );
    if (
      receipt.command_id !== attempt.command.command_id ||
      receipt.action !== action
    )
      return { status: 'uncertain' };
    if (receipt.status === 'completed') return { status: 'completed', receipt };
    return { status: receipt.status === 'rejected' ? 'failed' : 'uncertain' };
  } catch {
    return { status: 'uncertain' };
  }
}

const KIND_NAMES: Record<ConversationKind, string> = {
  designer: 'Design',
  code: 'Code',
  workflow: 'Workflow',
};

/** The dialog title: the saved name, following a rename made in it. */
function ActionsTitle({
  session,
  fallback,
}: {
  session: ConversationActionsSession;
  fallback: string;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  return <>{state.snapshot?.title || fallback}</>;
}

/**
 * The overlay around ConversationActions (B237): titled with the
 * conversation's name, a quiet "Chat · updated 5 minutes ago" line, and the
 * header's × as the one way to close.
 */
export function conversationActionsDialog(
  conversation: Pick<
    ConversationView,
    'title' | 'updated_at' | 'category' | 'resource_bindings'
  >,
  session: ConversationActionsSession,
  content: ReactNode,
) {
  const kinds = conversationKinds(conversation);
  const type = kinds.length
    ? kinds.map((kind) => KIND_NAMES[kind]).join(' · ')
    : 'Chat';
  return {
    className: 'conversation-actions-dialog',
    title: (
      <ActionsTitle
        session={session}
        fallback={conversation.title || 'Untitled conversation'}
      />
    ),
    description: parseTimestamp(conversation.updated_at)
      ? `${type} · updated ${relativeTime(conversation.updated_at)}`
      : type,
    content,
  };
}

export default function ConversationActions({
  conversationId,
  session,
  load,
  review,
  execute,
  save,
  onChanged,
  onDelete,
  initialPin,
  initialExport,
}: ConversationActionsProps) {
  const { notify, close } = useOverlay();
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const wrongOwner = state.conversationId !== conversationId;
  const locked =
    wrongOwner || !state.active || Boolean(state.busy || state.pending);
  const initialPinRequested = useRef(false);
  const mounted = useRef(false);
  const [closeRequested, setCloseRequested] = useState(false);
  // The switch shows where it was moved while that pin is reviewed and
  // saved, then the saved state (the old one if it did not go through).
  const [pinTarget, setPinTarget] = useState<boolean | null>(null);
  if (pinTarget !== null && !state.busy) setPinTarget(null);
  const id = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // Closing waits for the render that closed the confirmation on top of
  // this dialog; closing earlier would close the confirmation again.
  const closeDialog = useEffectEvent(() => close());
  useEffect(() => {
    if (closeRequested) closeDialog();
  }, [closeRequested]);

  // Every opening reads the conversation afresh (it may have been renamed or
  // continued since), unless a reviewed action from before still needs it.
  useEffect(() => {
    const current = session.getSnapshot();
    if (
      current.conversationId !== conversationId ||
      !current.active ||
      current.busy ||
      session.hasRetained()
    )
      return;
    const abort = session.beginRead();
    session.update({ snapshot: null, busy: 'load', message: '' });
    void load(conversationId, abort.signal)
      .then((snapshot) => {
        const checked = checkedSnapshot(snapshot, conversationId);
        if (!abort.signal.aborted)
          session.update({ snapshot: checked, title: checked.title, busy: '' });
      })
      .catch(() => {
        if (!abort.signal.aborted)
          session.update({
            busy: '',
            message:
              'Saved conversation actions are unavailable. Close and try again.',
          });
      })
      .finally(() => session.endRead(abort));
  }, [conversationId, load, session]);

  const requestReview = async (
    action: ConversationAction,
    fields: Record<string, unknown>,
  ) => {
    const current = session.getSnapshot();
    const snapshot = current.snapshot;
    if (!snapshot || locked) return;
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '' });
    try {
      const result = checkedReview(
        await review(
          conversationId,
          action,
          snapshot.revision,
          fields,
          abort.signal,
        ),
        conversationId,
        action,
        snapshot,
      );
      if (!abort.signal.aborted) {
        const attempt = {
          command: reviewedCommand(action, fields, result),
          review: result,
        };
        session.update({
          reviewed: attempt,
          busy: '',
          message: '',
        });
        void apply(attempt);
      }
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The conversation changed or this action is unavailable. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  const apply = async (attempt: Attempt | null) => {
    if (!attempt || wrongOwner || !state.active || state.busy) return;
    session.update({
      busy: 'apply',
      reviewed: null,
      pending: attempt,
      message: '',
    });
    try {
      const receipt = await execute(
        conversationId,
        attempt.command,
        attempt.review,
      );
      if (
        receipt.command_id !== attempt.command.command_id ||
        receipt.action !== attempt.command.type
      )
        throw Error();
      if (receipt.status === 'completed') {
        if (receipt.action === 'conversation.export') {
          if (!receipt.export) throw Error();
          session.update({
            busy: '',
            pending: null,
            exported: receipt.export,
          });
          void saveExport();
        } else {
          if (
            !receipt.conversation ||
            receipt.conversation.conversation_id !== conversationId
          )
            throw Error();
          const previous = session.getSnapshot().snapshot!;
          const snapshot: ConversationActionSnapshot = {
            ...previous,
            revision: receipt.conversation.revision,
            title: receipt.conversation.title,
            pinned: receipt.conversation.pinned,
          };
          session.update({
            snapshot,
            title: snapshot.title,
            busy: '',
            pending: null,
            exported: null,
            message:
              receipt.action === 'conversation.rename'
                ? 'Name saved.'
                : snapshot.pinned
                  ? 'Pinned.'
                  : 'Unpinned.',
          });
          onChanged?.(receipt.conversation);
        }
      } else if (receipt.status === 'rejected') {
        session.update({
          busy: '',
          pending: null,
          message:
            'The conversation action was rejected. Refresh and try again.',
        });
      } else {
        session.update({
          busy: '',
          message:
            'The original action is unconfirmed. Check it before starting another action.',
        });
      }
    } catch {
      session.update({
        busy: '',
        message:
          'The original action is unconfirmed. Check it before starting another action.',
      });
    }
  };

  // One click saves: the Save dialog, Exports while the desktop reconnects,
  // or a browser download (B238). A saved export closes the dialog, so the
  // floating notice (hidden while a dialog is open) confirms it at once.
  const saveExport = async () => {
    const current = session.getSnapshot();
    if (!current.exported || locked) return;
    session.update({ busy: 'download', message: '' });
    const result = await save(
      current.exported.attachment_ref,
      current.exported.file_name,
    ).catch((): CapabilityResult<SavedFile> => ({
      status: 'unavailable',
      reason: 'operation_failed',
    }));
    if (result.status !== 'ok') {
      session.update({
        busy: '',
        exported: result.status === 'cancelled' ? null : current.exported,
        message:
          result.status === 'cancelled'
            ? ''
            : result.reason === 'save_failed'
              ? 'Row-Bot couldn’t write the file there. Choose another folder and try again.'
              : result.reason === 'user_gesture_required'
                ? 'The export is ready. Choose Save export to save it.'
                : 'The export couldn’t be saved. Try again.',
      });
      return;
    }
    session.update({ busy: '', exported: null, message: '' });
    if (mounted.current) close();
    const saved = result.value;
    if (saved.kind === 'exports')
      notify(`Saved to ${saved.folder}`, undefined, {
        label: 'Show in folder',
        onAction: () =>
          void saved.reveal().then((shown) => {
            if (!shown)
              notify('Row-Bot couldn’t open the Exports folder.', 'warning');
          }),
      });
    else
      notify(
        saved.kind === 'file'
          ? 'Conversation export saved.'
          : 'Download started.',
      );
  };

  const requestInitialPin = useEffectEvent((pinned: boolean) => {
    void requestReview('conversation.pin', { pinned });
  });
  useEffect(() => {
    if (
      initialPin === undefined ||
      initialPinRequested.current ||
      !state.snapshot ||
      locked ||
      !state.snapshot.capabilities.pin.available ||
      state.snapshot.pinned === initialPin
    )
      return;
    initialPinRequested.current = true;
    requestInitialPin(initialPin);
  }, [initialPin, locked, state.snapshot]);
  const exportAs = (format: ExportFormat) =>
    requestReview('conversation.export', format === 'pdf' ? { format } : {});
  const requestInitialExport = useEffectEvent(() => {
    void exportAs(initialExport === 'pdf' ? 'pdf' : 'markdown');
  });
  useEffect(() => {
    if (
      !initialExport ||
      initialPinRequested.current ||
      !state.snapshot ||
      locked ||
      !state.snapshot.capabilities.export.available
    )
      return;
    initialPinRequested.current = true;
    requestInitialExport();
  }, [initialExport, locked, state.snapshot]);

  if (wrongOwner)
    return (
      <p role="alert">
        This action belongs to another conversation. Return to that conversation
        to finish or check it.
      </p>
    );

  const snapshot = state.snapshot;
  const name = state.title.trim();
  const renamable = Boolean(
    snapshot &&
    snapshot.capabilities.rename.available &&
    name &&
    name !== snapshot.title,
  );
  return (
    <div className="conversation-actions-panel">
      <div className="conversation-actions-rows">
        {!snapshot && state.busy === 'load' && (
          <Skeleton label="Loading conversation actions" />
        )}
        {snapshot && (
          <>
            {/* Enter or Save renames; leaving the field does not. */}
            <form
              className="conversation-actions-name"
              onSubmit={(event) => {
                event.preventDefault();
                if (renamable)
                  void requestReview('conversation.rename', { title: name });
              }}
            >
              <label
                className="conversation-actions-label"
                htmlFor={`${id}-name`}
              >
                Name
              </label>
              <div className="conversation-actions-name-row">
                <Input
                  id={`${id}-name`}
                  value={state.title}
                  maxLength={120}
                  aria-describedby={`${id}-name-help`}
                  readOnly={locked}
                  disabled={!snapshot.capabilities.rename.available}
                  onChange={(event) =>
                    session.update({
                      title: event.target.value,
                      reviewed: null,
                      message: '',
                    })
                  }
                />
                <Button type="submit" disabled={locked || !renamable}>
                  Save
                </Button>
              </div>
              <p id={`${id}-name-help`} className="conversation-actions-help">
                Enter saves the new name.
              </p>
            </form>
            <SettingRow
              label="Pin"
              htmlFor={`${id}-pin`}
              description="Keep it at the top of the sidebar."
              control={
                <Toggle
                  id={`${id}-pin`}
                  label="Pin"
                  checked={pinTarget ?? snapshot.pinned}
                  disabled={
                    !snapshot.capabilities.pin.available ||
                    Boolean(state.pending)
                  }
                  // Stays enabled while busy so keyboard focus is kept;
                  // changes are ignored until the last one ends.
                  onChange={(event) => {
                    if (locked) return;
                    setPinTarget(event.target.checked);
                    void requestReview('conversation.pin', {
                      pinned: event.target.checked,
                    });
                  }}
                />
              }
            />
            <SettingRow
              className="conversation-actions-export"
              label="Export"
              description="Saves a copy as a file."
              control={
                <>
                  <Button
                    disabled={locked || !snapshot.capabilities.export.available}
                    onClick={() => void exportAs('markdown')}
                  >
                    <FileText size={16} aria-hidden />
                    Markdown
                  </Button>
                  <Button
                    disabled={locked || !snapshot.capabilities.export.available}
                    onClick={() => void exportAs('pdf')}
                  >
                    <FileDown size={16} aria-hidden />
                    PDF
                  </Button>
                </>
              }
            />
          </>
        )}
        {state.pending && (
          <Button
            disabled={!state.active || Boolean(state.busy)}
            onClick={() => void apply(state.pending)}
          >
            Check original action
          </Button>
        )}
        {state.exported && !state.busy && (
          <Button onClick={() => void saveExport()}>Save export</Button>
        )}
        {state.message && (
          <p className="conversation-actions-status" role="status">
            {state.message}
          </p>
        )}
      </div>
      {onDelete && (
        <div className="conversation-actions-footer">
          <Button
            variant="ghost"
            className="conversation-actions-delete"
            disabled={locked}
            onClick={() => onDelete(() => setCloseRequested(true))}
          >
            <Trash2 size={16} aria-hidden />
            Delete conversation…
          </Button>
        </div>
      )}
    </div>
  );
}
