import { useEffect, useSyncExternalStore } from 'react';
import { Button, Field, Input, Skeleton } from '../../ui/primitives';

export type ConversationAction =
  | 'conversation.rename'
  | 'conversation.pin'
  | 'conversation.archive'
  | 'conversation.export';
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
export type ConversationActionsProps = {
  conversationId: string;
  session: ConversationActionsSession;
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
  download: (reference: string, fileName: string) => Promise<void>;
  onChanged?: (
    conversation: NonNullable<ConversationActionReceipt['conversation']>,
  ) => void;
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

export default function ConversationActions({
  conversationId,
  session,
  load,
  review,
  execute,
  download,
  onChanged,
}: ConversationActionsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const wrongOwner = state.conversationId !== conversationId;
  const locked =
    wrongOwner || !state.active || Boolean(state.busy || state.pending);

  useEffect(() => {
    const current = session.getSnapshot();
    if (
      current.conversationId !== conversationId ||
      !current.active ||
      current.snapshot ||
      current.busy
    )
      return;
    const abort = session.beginRead();
    session.update({ busy: 'load', message: '' });
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
      const result = await review(
        conversationId,
        action,
        snapshot.revision,
        fields,
        abort.signal,
      );
      if (
        result.schema_version !== 1 ||
        result.conversation_id !== conversationId ||
        result.action !== action ||
        result.revision !== snapshot.revision ||
        result.checkpoint_revision !== snapshot.checkpoint_revision
      )
        throw Error();
      const command: ConversationActionCommand = {
        command_id: crypto.randomUUID(),
        type: action,
        expected_revision: result.revision,
        payload: {
          ...fields,
          checkpoint_revision: result.checkpoint_revision,
          action_digest: result.action_digest,
          ...(action === 'conversation.export'
            ? { export_title: result.fields.title }
            : {}),
        },
      };
      if (!abort.signal.aborted)
        session.update({
          reviewed: { command, review: result },
          busy: '',
          message: 'Review complete. Apply this exact action to continue.',
        });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The conversation changed or this action is unavailable. Refresh and review it again.',
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
            message: 'Conversation export is ready to download.',
          });
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
            message: 'Conversation action completed.',
          });
          onChanged?.(receipt.conversation);
        }
      } else if (receipt.status === 'rejected') {
        session.update({
          busy: '',
          pending: null,
          message:
            'The conversation action was rejected. Refresh and review it again.',
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

  const saveExport = async () => {
    const current = session.getSnapshot();
    if (!current.exported || locked) return;
    session.update({ busy: 'download', message: '' });
    try {
      await download(
        current.exported.attachment_ref,
        current.exported.file_name,
      );
      session.update({ busy: '', message: 'Conversation export downloaded.' });
    } catch {
      session.update({
        busy: '',
        message: 'The saved export could not be downloaded. Try again.',
      });
    }
  };

  if (wrongOwner)
    return (
      <p role="alert">
        This action belongs to another conversation. Return to that conversation
        to finish or check it.
      </p>
    );
  if (!state.snapshot && state.busy === 'load')
    return <Skeleton label="Loading conversation actions" />;

  return (
    <section aria-label="Conversation actions" className="settings-section">
      <h2>Conversation actions</h2>
      <p>
        Rename, pin, or export this saved conversation. History and bound
        resources stay in place.
      </p>
      {state.snapshot && (
        <>
          <Field label="Conversation name">
            <Input
              value={state.title}
              maxLength={120}
              disabled={locked || !state.snapshot.capabilities.rename.available}
              onChange={(event) =>
                session.update({
                  title: event.target.value,
                  reviewed: null,
                  message: '',
                })
              }
            />
          </Field>
          <div className="button-row">
            <Button
              disabled={locked || !state.title.trim()}
              onClick={() =>
                void requestReview('conversation.rename', {
                  title: state.title.trim(),
                })
              }
            >
              Review rename
            </Button>
            <Button
              disabled={locked || !state.snapshot.capabilities.pin.available}
              onClick={() =>
                void requestReview('conversation.pin', {
                  pinned: !state.snapshot!.pinned,
                })
              }
            >
              Review {state.snapshot.pinned ? 'unpin' : 'pin'}
            </Button>
            <Button
              disabled={locked || !state.snapshot.capabilities.export.available}
              onClick={() => void requestReview('conversation.export', {})}
            >
              Review export
            </Button>
          </div>
          {!state.snapshot.capabilities.archive.available && (
            <p role="status">
              Archive is unavailable because saved conversations do not yet have
              a reversible archive owner. Deletion remains a separate confirmed
              action.
            </p>
          )}
        </>
      )}
      {state.reviewed && (
        <section aria-label="Conversation action review" className="surface">
          <h3>Review action</h3>
          <p>{state.reviewed.review.summary}</p>
          {state.reviewed.review.disclosures.map((disclosure) => (
            <p key={disclosure}>{disclosure}</p>
          ))}
          <Button disabled={locked} onClick={() => void apply(state.reviewed)}>
            Apply reviewed action
          </Button>
        </section>
      )}
      {state.pending && (
        <Button
          disabled={!state.active || Boolean(state.busy)}
          onClick={() => void apply(state.pending)}
        >
          Check original action
        </Button>
      )}
      {state.exported && (
        <Button disabled={locked} onClick={() => void saveExport()}>
          Download conversation export
        </Button>
      )}
      {state.message && <p role="status">{state.message}</p>}
    </section>
  );
}
