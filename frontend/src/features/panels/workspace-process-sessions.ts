import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  Command,
  CommandReceipt,
  WorkspaceProcessReview as Review,
  ResourceView,
} from '../../api/types';
import {
  createWorkspaceProcessesSession,
  type WorkspaceProcessAttempt,
  type WorkspaceProcessReview,
  type WorkspaceProcessesProps,
} from './WorkspaceProcesses';

/** One controller lifetime owns bounded process intents, including closed panels. */
export function createWorkspaceProcessSessions(controller: ClientController) {
  type Entry = ReturnType<typeof make>;
  const entries = new Map<string, Entry>();
  let disposed = false;
  const auth = () => {
    const value = controller.getSnapshot().handshake;
    return value
      ? JSON.stringify([
          value.instance_id,
          value.server_epoch,
          value.client_session_id,
        ])
      : '';
  };
  let identity = auth();
  function make(conversation: string, resource: ResourceView) {
    const scope = {
      conversation_id: conversation,
      resource_id: resource.binding.resource_id,
      binding_id: resource.binding.binding_id,
      binding_revision: resource.binding.revision,
    };
    const session = createWorkspaceProcessesSession(scope);
    const ownedIdentity = identity;
    let review: Review | null = null;
    type ProcessInfo = NonNullable<CommandReceipt['workspace_process']>;
    const attempts = new Map<
      string,
      {
        command: Command;
        result?: Promise<ProcessInfo>;
        settled?: ProcessInfo;
        rejection?: string;
      }
    >();
    const guard = () => {
      sync();
      if (
        disposed ||
        !ownedIdentity ||
        auth() !== ownedIdentity ||
        session.getSnapshot().revoked
      )
        throw clientError({ code: 'authentication_required' });
      const state = controller.getSnapshot();
      const current = state.workspace?.resources.find(
        (value) => value.binding.binding_id === scope.binding_id,
      );
      if (
        state.loadingConversation ||
        state.selectedConversationId !== conversation ||
        state.workspace?.conversation_id !== conversation ||
        !current?.available ||
        current.binding.kind !== 'workspace' ||
        current.binding.resource_id !== scope.resource_id ||
        current.binding.revision !== scope.binding_revision
      )
        throw clientError({ code: 'resource_binding_revoked' });
    };
    const query = async <T>(read: () => Promise<T>) => {
      guard();
      const value = await read();
      guard();
      return value;
    };
    const dispatch = (key: string, command: Command) => {
      guard();
      const prior = attempts.get(key);
      if (
        prior &&
        JSON.stringify(prior.command.payload) !==
          JSON.stringify(command.payload)
      )
        throw clientError({ code: 'idempotency_mismatch' });
      if (prior?.rejection) throw clientError({ code: prior.rejection });
      if (prior?.settled) return Promise.resolve(prior.settled);
      if (prior?.result) return prior.result;
      if (!prior && command.type === 'workspace.process.start') {
        for (const [id, value] of attempts) {
          if (
            value.command.type === 'workspace.process.start' &&
            (value.settled?.quiesced || value.rejection) &&
            !value.result &&
            id !== 'start:' + session.getSnapshot().attempt?.command_id
          )
            attempts.delete(id);
        }
        if (
          [...attempts.values()].filter(
            (value) => value.command.type === 'workspace.process.start',
          ).length >= 32
        )
          throw clientError({ code: 'process_limit' });
      }
      if (!prior && attempts.size >= 64)
        throw clientError({ code: 'process_limit' });
      const attempt = prior ?? { command: structuredClone(command) };
      attempts.set(key, attempt);
      const perform = async () => {
        let receipt: CommandReceipt | null = null;
        if (prior) {
          try {
            receipt = await controller.receipt(attempt.command.command_id);
          } catch (error) {
            if (clientError(error).code !== 'not_found') throw error;
          }
        }
        guard();
        if (receipt?.status !== 'completed' && receipt?.status !== 'rejected')
          receipt = prior
            ? await controller.retryCommand(
                conversation,
                attempt.command,
                attempt.command.command_id,
              )
            : await controller.command(
                conversation,
                attempt.command,
                attempt.command.command_id,
              );
        guard();
        if (receipt.command_id !== attempt.command.command_id)
          throw clientError({ code: 'operation_uncertain' });
        if (receipt.status === 'rejected') {
          attempt.rejection = clientError({
            code: receipt.code ?? 'action_denied',
          }).code;
          throw clientError({ code: attempt.rejection });
        }
        let value = receipt.workspace_process;
        const processId =
          attempt.command.type === 'workspace.process.start'
            ? attempt.command.command_id
            : (attempt.command.payload as { process_id: string }).process_id;
        if (
          !value ||
          value.process_id !== processId ||
          value.command_id !== processId ||
          receipt.conversation_id !== conversation ||
          receipt.resource_id !== scope.resource_id ||
          receipt.binding_id !== scope.binding_id ||
          receipt.binding_revision !== scope.binding_revision
        )
          throw clientError({ code: receipt.code || 'operation_uncertain' });
        if (attempt.settled?.quiesced && !value.quiesced)
          value = attempt.settled;
        if (receipt.status === 'completed') {
          if (attempt.command.type === 'workspace.process.start')
            attempt.settled = value;
          else {
            attempts.delete(key);
            const start = attempts.get('start:' + processId);
            if (start && value.quiesced) start.settled = value;
          }
        }
        return value;
      };
      attempt.result = perform().finally(() => {
        attempt.result = undefined;
      });
      return attempt.result;
    };
    const start = (
      attempt: WorkspaceProcessAttempt,
      evidence: WorkspaceProcessReview,
    ) => {
      guard();
      if (
        attempt.snapshot.resource_id !== scope.resource_id ||
        attempt.snapshot.conversation_id !== conversation ||
        attempt.snapshot.binding_id !== scope.binding_id ||
        attempt.snapshot.binding_revision !== scope.binding_revision
      )
        return Promise.reject(
          clientError({ code: 'resource_binding_revoked' }),
        );
      const prior = attempts.get('start:' + attempt.command_id);
      if (prior) {
        const payload = prior.command.payload as {
          command: string;
          target: { resource_revision: string };
        };
        if (
          payload.command !== attempt.command ||
          payload.target.resource_revision !==
            attempt.snapshot.resource_revision
        )
          return Promise.reject(clientError({ code: 'idempotency_mismatch' }));
        return dispatch('start:' + attempt.command_id, prior.command);
      }
      if (
        !review ||
        review.command_id !== attempt.command_id ||
        review.command !== attempt.command ||
        evidence.approval_id !== review.nonce ||
        evidence.command_id !== attempt.command_id ||
        evidence.command !== attempt.command ||
        evidence.resource_id !== scope.resource_id ||
        evidence.conversation_id !== conversation ||
        evidence.binding_id !== scope.binding_id ||
        evidence.binding_revision !== scope.binding_revision ||
        evidence.resource_revision !== attempt.snapshot.resource_revision ||
        review.resource_revision !== attempt.snapshot.resource_revision
      )
        return Promise.reject(clientError({ code: 'process_review_stale' }));
      return dispatch('start:' + attempt.command_id, {
        command_id: attempt.command_id,
        client_session_id:
          controller.getSnapshot().handshake!.client_session_id,
        type: 'workspace.process.start',
        expected_revision: review.conversation_revision,
        payload: {
          target: { kind: 'workspace', ...scopeTarget(review) },
          command: review.command,
          policy_revision: review.policy_revision,
          action_digest: review.action_digest,
          nonce: review.nonce,
        },
      });
    };
    const cleanup = (kind: 'stop' | 'recover', process: string) => {
      guard();
      const prior = attempts.get(kind + ':' + process);
      if (prior) return dispatch(kind + ':' + process, prior.command);
      const snapshot = session.getSnapshot().snapshot;
      if (!snapshot)
        return Promise.reject(clientError({ code: 'resource_unavailable' }));
      return dispatch(kind + ':' + process, {
        command_id: crypto.randomUUID(),
        client_session_id:
          controller.getSnapshot().handshake!.client_session_id,
        type:
          kind === 'stop'
            ? 'workspace.process.stop'
            : 'workspace.process.recover',
        expected_revision: '0',
        payload: {
          target: { kind: 'workspace', ...scopeTarget(snapshot) },
          process_id: process,
        },
      });
    };
    const api: Pick<
      WorkspaceProcessesProps,
      | 'load'
      | 'loadRecovery'
      | 'output'
      | 'review'
      | 'start'
      | 'stop'
      | 'recover'
    > = {
      load: (signal) =>
        query(() =>
          controller.workspaceProcesses(conversation, scope.binding_id, signal),
        ),
      loadRecovery: (cursor, signal) =>
        query(() =>
          controller.workspaceProcessRecovery(
            conversation,
            scope.binding_id,
            cursor,
            signal,
          ),
        ),
      output: (process, cursor, signal) =>
        query(() =>
          controller.workspaceProcessOutput(
            conversation,
            scope.binding_id,
            process,
            cursor,
            signal,
          ),
        ),
      review: async (attempt) => {
        const result = await query(() =>
          controller.reviewWorkspaceProcess(conversation, scope.binding_id, {
            command_id: attempt.command_id,
            command: attempt.command,
          }),
        );
        guard();
        if (
          result.binding_revision !== scope.binding_revision ||
          result.resource_id !== scope.resource_id ||
          result.binding_id !== scope.binding_id ||
          result.conversation_id !== conversation ||
          result.command_id !== attempt.command_id ||
          result.command !== attempt.command
        )
          throw clientError({ code: 'process_review_stale' });
        review = result;
        return {
          ...result,
          decision: result.policy_decision === 'block' ? 'denied' : 'approved',
          approval_id: result.nonce,
        };
      },
      start,
      stop: (process) => cleanup('stop', process),
      recover: (process) => cleanup('recover', process),
    };
    return {
      session,
      scope,
      api,
      retained() {
        const state = session.getSnapshot();
        return (
          !!state.draft ||
          !!state.attempt ||
          !!state.activity ||
          !!state.controls.size ||
          state.processes.some((value) => !value.quiesced) ||
          [...attempts.values()].some(
            (value) =>
              !!value.result || (!value.rejection && !value.settled?.quiesced),
          )
        );
      },
      purge() {
        review = null;
        attempts.clear();
        session.dispose();
      },
    };
  }
  function sync() {
    const current = auth();
    if (current !== identity) {
      entries.forEach((entry) => entry.purge());
      entries.clear();
      identity = current;
    }
    const state = controller.getSnapshot();
    entries.forEach((entry) => {
      if (
        state.loadingConversation ||
        state.workspace?.conversation_id !== entry.scope.conversation_id
      )
        return;
      const resource = state.workspace.resources.find(
        (value) => value.binding.binding_id === entry.scope.binding_id,
      );
      if (
        !resource?.available ||
        resource.binding.resource_id !== entry.scope.resource_id ||
        resource.binding.kind !== 'workspace' ||
        resource.binding.revision !== entry.scope.binding_revision
      )
        entry.session.revoke();
    });
  }
  const unsubscribe = controller.subscribe(sync);
  return {
    forResource(conversation: string, resource: ResourceView) {
      sync();
      if (
        !identity ||
        disposed ||
        resource.binding.kind !== 'workspace' ||
        !resource.available
      )
        return null;
      const state = controller.getSnapshot();
      const current = state.workspace?.resources.find(
        (value) => value.binding.binding_id === resource.binding.binding_id,
      );
      if (
        state.loadingConversation ||
        state.selectedConversationId !== conversation ||
        state.workspace?.conversation_id !== conversation ||
        !current?.available ||
        current.binding.kind !== 'workspace' ||
        current.binding.resource_id !== resource.binding.resource_id ||
        current.binding.revision !== resource.binding.revision
      )
        return null;
      const key = JSON.stringify([
        conversation,
        resource.binding.resource_id,
        resource.binding.binding_id,
        resource.binding.revision,
      ]);
      const existing = entries.get(key);
      if (existing)
        return existing.session.getSnapshot().revoked ? null : existing;
      if (entries.size >= 8) {
        const clean = [...entries].find(([, entry]) => !entry.retained());
        if (clean) {
          clean[1].purge();
          entries.delete(clean[0]);
        }
      }
      if (entries.size >= 8) return null;
      const entry = make(conversation, resource);
      entries.set(key, entry);
      return entry;
    },
    hasRetained: () => [...entries.values()].some((entry) => entry.retained()),
    dispose() {
      disposed = true;
      unsubscribe();
      entries.forEach((entry) => entry.purge());
      entries.clear();
    },
  };
}

function scopeTarget(value: {
  resource_id: string;
  resource_revision: string;
  binding_id: string;
  binding_revision: string;
}) {
  return {
    resource_id: value.resource_id,
    resource_revision: value.resource_revision,
    binding_id: value.binding_id,
    binding_revision: value.binding_revision,
  };
}
