import { useEffect, useSyncExternalStore } from 'react';
import { Button, Field, Input } from '../../ui/primitives';
import type {
  McpConfigurationReceipt,
  McpConfigurationReview,
} from './CapabilitySettings';

export type McpPolicyPage = {
  schema_version: 1;
  revision: string | null;
  server_id: string | null;
  availability: string;
  global_enabled: boolean | null;
  server_enabled: boolean | null;
  resources_enabled: boolean | null;
  prompts_enabled: boolean | null;
  items: {
    tool_id: string;
    name: string;
    enabled: boolean | null;
    requires_approval: boolean | null;
    approval_locked: boolean;
    destructive: boolean | null;
  }[];
  total: number | null;
  next_cursor: string | null;
};
export type McpPolicyIntent =
  | { operation: 'global_enabled'; enabled: boolean }
  | { operation: 'server_enabled'; server_id: string; enabled: boolean }
  | {
      operation: 'tool_enabled' | 'tool_approval';
      server_id: string;
      tool_id: string;
      enabled: boolean;
    }
  | {
      operation: 'utility_enabled';
      server_id: string;
      utility: 'resources' | 'prompts';
      enabled: boolean;
    };
export type McpPolicyCommand = {
  command_id: string;
  type: 'mcp.configuration.control';
  payload: { configuration_revision: string; intent: McpPolicyIntent };
};
type Attempt = { command: McpPolicyCommand; review: McpConfigurationReview };
type State = {
  active: boolean;
  serverId: string | null;
  page: McpPolicyPage | null;
  query: string;
  filter: string;
  cursor?: string;
  draft: McpPolicyIntent | null;
  draftLabel: string;
  reviewed: Attempt | null;
  pending: Attempt | null;
  busy: string;
  message: string;
};

/** Retained by the authenticated runtime; one draft and original intent per scope. */
export function createMcpPolicySession(serverId: string | null = null) {
  let state: State = {
    active: true,
    serverId,
    page: null,
    query: '',
    filter: '',
    draft: null,
    draftLabel: '',
    reviewed: null,
    pending: null,
    busy: '',
    message: '',
  };
  const listeners = new Set<() => void>();
  const aborters = new Set<AbortController>();
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((notify) => notify());
  };
  return {
    getSnapshot: () => state,
    subscribe: (notify: () => void) => {
      listeners.add(notify);
      return () => {
        listeners.delete(notify);
      };
    },
    update,
    beginRead: () => {
      const abort = new AbortController();
      if (!state.active) abort.abort();
      else aborters.add(abort);
      return abort;
    },
    endRead: (abort: AbortController) => {
      aborters.delete(abort);
    },
    hasRetained: () =>
      state.active && Boolean(state.draft || state.reviewed || state.pending),
    dispose: () => {
      aborters.forEach((abort) => abort.abort());
      aborters.clear();
      state = {
        active: false,
        serverId,
        page: null,
        query: '',
        filter: '',
        draft: null,
        draftLabel: '',
        reviewed: null,
        pending: null,
        busy: '',
        message: 'Sign in again to manage MCP permissions.',
      };
      listeners.forEach((notify) => notify());
    },
  };
}
export type McpPolicySession = ReturnType<typeof createMcpPolicySession>;
export type McpPolicyControlsProps = {
  session: McpPolicySession;
  load: (
    query: { server_id: string | null; query: string; cursor?: string },
    signal: AbortSignal,
  ) => Promise<McpPolicyPage>;
  review: (
    payload: McpPolicyCommand['payload'],
    signal: AbortSignal,
  ) => Promise<McpConfigurationReview>;
  execute: (
    command: McpPolicyCommand,
    review: McpConfigurationReview,
  ) => Promise<McpConfigurationReceipt>;
};
const booleanLabel = (value: boolean | null) =>
  value === null ? 'Unknown' : value ? 'Enabled' : 'Disabled';

export default function McpPolicyControls({
  session,
  load,
  review,
  execute,
}: McpPolicyControlsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { page, pending, reviewed } = state;
  const locked = !state.active || Boolean(state.busy || pending);
  const canSave = Boolean(
    page?.revision &&
    ['available', 'missing', 'partial'].includes(page.availability),
  );
  const read = async (query: string, cursor?: string) => {
    const current = session.getSnapshot();
    if (!current.active || current.busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'load', reviewed: null });
    try {
      const result = await load(
        { server_id: current.serverId, query, cursor },
        abort.signal,
      );
      if (abort.signal.aborted) return;
      if (
        result.schema_version !== 1 ||
        result.server_id !== current.serverId ||
        result.items.length > 50
      )
        throw Error();
      session.update({
        page: result,
        filter: query,
        cursor,
        busy: '',
        message: '',
      });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'Saved permissions are unavailable or changed. Return to the first page.',
        });
    } finally {
      session.endRead(abort);
    }
  };
  useEffect(() => {
    const current = session.getSnapshot();
    if (!current.active || current.page || current.busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'load' });
    void load(
      {
        server_id: current.serverId,
        query: current.filter,
        cursor: current.cursor,
      },
      abort.signal,
    )
      .then(
        (result) => {
          if (abort.signal.aborted) return;
          if (
            result.schema_version !== 1 ||
            result.server_id !== current.serverId ||
            result.items.length > 50
          )
            session.update({
              busy: '',
              message:
                'Saved permissions are unavailable. Refresh to try again.',
            });
          else session.update({ page: result, busy: '' });
        },
        () => {
          if (!abort.signal.aborted)
            session.update({
              busy: '',
              message:
                'Saved permissions are unavailable. Refresh to try again.',
            });
        },
      )
      .finally(() => session.endRead(abort));
  }, [session, load]);
  const choose = (draft: McpPolicyIntent, draftLabel: string) => {
    if (locked || !canSave) return;
    session.update({ draft, draftLabel, reviewed: null, message: '' });
  };
  const requestReview = async () => {
    const current = session.getSnapshot();
    if (
      !current.active ||
      current.busy ||
      current.pending ||
      !current.draft ||
      !current.page?.revision ||
      !canSave
    )
      return;
    const command: McpPolicyCommand = {
      command_id: crypto.randomUUID(),
      type: 'mcp.configuration.control',
      payload: {
        configuration_revision: current.page.revision,
        intent: current.draft,
      },
    };
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '' });
    try {
      const result = await review(command.payload, abort.signal);
      if (abort.signal.aborted) return;
      if (
        result.configuration_revision !== command.payload.configuration_revision
      )
        throw Error();
      session.update({
        reviewed: { command, review: result },
        busy: '',
        message:
          'Review complete. Save permission explicitly applies this change.',
      });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'This permission could not be reviewed. Refresh and review again.',
        });
    } finally {
      session.endRead(abort);
    }
  };
  const save = async (attempt: Attempt | null) => {
    const current = session.getSnapshot();
    if (
      !attempt ||
      !current.active ||
      current.busy ||
      (current.pending !== attempt && current.reviewed !== attempt)
    )
      return;
    session.update({
      pending: attempt,
      reviewed: null,
      busy: 'save',
      message: '',
    });
    try {
      const result = await execute(attempt.command, attempt.review);
      if (result.command_id !== attempt.command.command_id) throw Error();
      if (
        result.status === 'completed' &&
        result.mcp_configuration?.status === 'saved'
      )
        session.update({
          pending: null,
          draft: null,
          draftLabel: '',
          busy: '',
          page: page ? { ...page, revision: null } : null,
          message:
            'Permission saved. Refresh to view current settings. Connection cleanup was not requested.',
        });
      else if (result.status === 'rejected')
        session.update({
          pending: null,
          busy: '',
          reviewed: null,
          page: page ? { ...page, revision: null } : null,
          message: 'Permission change rejected. Refresh and review again.',
        });
      else
        session.update({
          busy: '',
          message:
            'Save outcome is not confirmed. Check the original change before saving another permission.',
        });
    } catch {
      session.update({
        busy: '',
        message:
          'Save outcome is uncertain. Check the original change; it will not be blindly saved again.',
      });
    }
  };
  const toggle = (
    label: string,
    value: boolean | null,
    intent: (enabled: boolean) => McpPolicyIntent,
    lockedApproval = false,
  ) => (
    <div role="group" aria-label={label}>
      <span>
        {label}: {booleanLabel(value)}.{' '}
      </span>
      {value !== true && (
        <Button
          disabled={locked || !canSave}
          onClick={() => choose(intent(true), `Enable ${label}`)}
        >
          Enable {label}
        </Button>
      )}
      {value !== false && (
        <Button
          disabled={locked || !canSave || lockedApproval}
          onClick={() => choose(intent(false), `Disable ${label}`)}
        >
          Disable {label}
        </Button>
      )}
      {lockedApproval && (
        <span>Required safety approval cannot be lowered.</span>
      )}
    </div>
  );
  return (
    <section aria-label="Saved MCP permissions" className="settings-section">
      <h3>Saved permissions</h3>
      <p>
        These settings authorize future MCP access. They do not connect, test or
        disconnect a server, or change the native MCP tool switch.
      </p>
      <Button
        disabled={!state.active || Boolean(state.busy)}
        onClick={() => void read(state.filter, state.cursor)}
      >
        Refresh permissions
      </Button>
      {page && (
        <>
          <p role="status">
            {page.availability === 'recovery_required'
              ? 'An interrupted configuration save needs recovery before changes.'
              : `Saved permissions: ${page.availability}.`}
          </p>
          {toggle('MCP access', page.global_enabled, (enabled) => ({
            operation: 'global_enabled',
            enabled,
          }))}
          {state.serverId && (
            <>
              {toggle('Server access', page.server_enabled, (enabled) => ({
                operation: 'server_enabled',
                server_id: state.serverId!,
                enabled,
              }))}
              {toggle('Resource access', page.resources_enabled, (enabled) => ({
                operation: 'utility_enabled',
                server_id: state.serverId!,
                utility: 'resources',
                enabled,
              }))}
              {toggle('Prompt access', page.prompts_enabled, (enabled) => ({
                operation: 'utility_enabled',
                server_id: state.serverId!,
                utility: 'prompts',
                enabled,
              }))}
              <Field label="Search saved tool permissions">
                <Input
                  value={state.query}
                  maxLength={128}
                  disabled={locked}
                  onChange={(event) =>
                    session.update({ query: event.target.value })
                  }
                />
              </Field>
              <Button disabled={locked} onClick={() => void read(state.query)}>
                Search permissions
              </Button>
              <p>
                {page.total === null
                  ? 'Saved tool count unavailable'
                  : `${page.total} saved tool permissions`}
                .
              </p>
              <ul>
                {page.items.map((tool) => (
                  <li key={tool.tool_id}>
                    <strong>{tool.name}</strong>
                    {toggle(`${tool.name} access`, tool.enabled, (enabled) => ({
                      operation: 'tool_enabled',
                      server_id: state.serverId!,
                      tool_id: tool.tool_id,
                      enabled,
                    }))}
                    {toggle(
                      `${tool.name} approval`,
                      tool.requires_approval,
                      (enabled) => ({
                        operation: 'tool_approval',
                        server_id: state.serverId!,
                        tool_id: tool.tool_id,
                        enabled,
                      }),
                      tool.approval_locked,
                    )}
                  </li>
                ))}
              </ul>
              <Button
                disabled={locked || !state.cursor}
                onClick={() => void read(state.filter)}
              >
                First permission page
              </Button>
              <Button
                disabled={locked || !page.next_cursor}
                onClick={() =>
                  void read(state.filter, page.next_cursor ?? undefined)
                }
              >
                Next permission page
              </Button>
            </>
          )}
        </>
      )}
      {state.draft && <p>Selected change: {state.draftLabel}.</p>}
      <Button
        disabled={locked || !canSave || !state.draft}
        onClick={() => void requestReview()}
      >
        Review permission
      </Button>
      <Button
        disabled={locked || !reviewed}
        onClick={() => void save(reviewed)}
      >
        Save permission
      </Button>
      {state.draft && (
        <Button
          disabled={locked}
          onClick={() =>
            session.update({ draft: null, draftLabel: '', reviewed: null })
          }
        >
          Discard selected change
        </Button>
      )}
      {pending && (
        <Button
          disabled={!state.active || Boolean(state.busy)}
          onClick={() => void save(pending)}
        >
          Check original permission change
        </Button>
      )}
      {state.message && <p role="status">{state.message}</p>}
    </section>
  );
}
