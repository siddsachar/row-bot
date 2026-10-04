import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { RefreshCw, Search } from 'lucide-react';
import { Button, IconButton, Input, Toggle } from '../../ui/primitives';
import { SettingsGroup, SettingsItem, StatusLine } from './anatomy';
import type {
  McpConfigurationReceipt,
  McpConfigurationReview,
} from './CapabilitySettings';
import { mcpRevision, reviewFresh } from './mcp-revision';

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
  mutationsDisabled?: boolean;
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

/** Two intents change the same switch (only `enabled` differs). */
function sameTarget(left: McpPolicyIntent, right: McpPolicyIntent) {
  return (
    JSON.stringify({ ...left, enabled: null }) ===
    JSON.stringify({ ...right, enabled: null })
  );
}

/**
 * One saved-permission scope (all of MCP, or one server): the passive read,
 * and each change reviewed and saved in one step. The page's "Use MCP
 * servers" switch and a server's details (B262) both use it.
 */
function useMcpPolicy({
  session,
  load,
  review,
  execute,
  mutationsDisabled = false,
}: McpPolicyControlsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { page, pending } = state;
  const locked = !state.active || Boolean(state.busy || pending);
  const canSave = Boolean(
    page?.revision &&
    ['available', 'missing', 'partial'].includes(page.availability),
  );
  const read = useCallback(
    async (query: string, cursor?: string, keepMessage = false) => {
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
          ...(keepMessage ? {} : { message: '' }),
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
    },
    [session, load],
  );
  // A save in any MCP panel changes the revision: read the permissions again.
  useEffect(
    () =>
      mcpRevision.subscribe((source) => {
        const current = session.getSnapshot();
        if (source !== session && current.page)
          void read(current.filter, current.cursor, true);
      }),
    [session, read],
  );
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
  const requestReview = async () => {
    const current = session.getSnapshot();
    if (
      mutationsDisabled ||
      !current.active ||
      current.busy ||
      current.pending ||
      !current.draft ||
      !current.page?.revision ||
      !canSave
    )
      return;
    const intent = current.draft;
    const sent: { command?: McpPolicyCommand } = {};
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '' });
    try {
      const result = await reviewFresh(
        (revision) => {
          sent.command = {
            command_id: crypto.randomUUID(),
            type: 'mcp.configuration.control',
            payload: { configuration_revision: revision, intent },
          };
          return review(sent.command.payload, abort.signal);
        },
        current.page.revision,
        async () => {
          const page = await load(
            {
              server_id: current.serverId,
              query: current.filter,
              cursor: current.cursor,
            },
            abort.signal,
          );
          if (page.server_id !== current.serverId) return null;
          session.update({ page });
          return page.revision;
        },
      );
      const command = sent.command;
      if (abort.signal.aborted || !command) return;
      if (
        result.configuration_revision !== command.payload.configuration_revision
      )
        throw Error();
      const attempt = { command, review: result };
      session.update({ reviewed: attempt, busy: '', message: '' });
      void save(attempt);
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'This permission could not be validated. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };
  const save = async (attempt: Attempt | null) => {
    const current = session.getSnapshot();
    if (
      mutationsDisabled ||
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
      ) {
        session.update({
          pending: null,
          draft: null,
          draftLabel: '',
          busy: '',
          page: page ? { ...page, revision: null } : null,
          message: 'Permission saved.',
        });
        // Every MCP panel reads the new revision; this one shows it now.
        mcpRevision.saved(session);
        const saved = session.getSnapshot();
        void read(saved.filter, saved.cursor, true);
      } else if (result.status === 'rejected')
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
  const choose = (draft: McpPolicyIntent, draftLabel: string) => {
    if (mutationsDisabled || locked || !canSave) return;
    session.update({ draft, draftLabel, reviewed: null, message: '' });
    void requestReview();
  };
  /** A switch for one intent: shows the change being saved at once. */
  const toggle = (
    label: string,
    value: boolean | null,
    intent: (enabled: boolean) => McpPolicyIntent,
    lockedValue = false,
  ) => {
    // While a change is being saved its switch shows the new value.
    const draft = state.busy || pending ? state.draft : null;
    const checked =
      draft && sameTarget(draft, intent(true)) ? draft.enabled : value === true;
    return (
      <Toggle
        label={label}
        checked={checked}
        disabled={mutationsDisabled || locked || !canSave || lockedValue}
        onChange={(event) =>
          choose(
            intent(event.target.checked),
            `${event.target.checked ? 'Turn on' : 'Turn off'} ${label}`,
          )
        }
      />
    );
  };
  return {
    state,
    page,
    pending,
    locked,
    read,
    save,
    toggle,
    mutationsDisabled,
  };
}

/** The outcome of the last change, and its original while unconfirmed. */
function PolicyOutcome({
  policy,
  session,
}: {
  policy: ReturnType<typeof useMcpPolicy>;
  session: McpPolicySession;
}) {
  const { state, pending, locked } = policy;
  return (
    <>
      {state.draft && (
        <div className="action-cluster">
          <span>Selected change: {state.draftLabel}.</span>
          <Button
            disabled={locked}
            onClick={() =>
              session.update({ draft: null, draftLabel: '', reviewed: null })
            }
          >
            Discard selected change
          </Button>
        </div>
      )}
      {pending && (
        <Button
          disabled={
            policy.mutationsDisabled || !state.active || Boolean(state.busy)
          }
          onClick={() => void policy.save(pending)}
        >
          Check original permission change
        </Button>
      )}
      {state.message && <p role="status">{state.message}</p>}
    </>
  );
}

/**
 * "Use MCP servers" (B262): the page's first switch, the same reviewed
 * change as MCP's saved permission.
 */
export function McpGlobalSwitch(props: McpPolicyControlsProps) {
  const policy = useMcpPolicy(props);
  const { page, state } = policy;
  return (
    <SettingsItem
      label="Use MCP servers"
      help="Lets Row-Bot connect to the servers below."
      layout="inline"
      anchor="mcp-enabled"
      bind={false}
      status={
        page?.availability === 'recovery_required' ? (
          <StatusLine
            tone="warning"
            more={[page.global_enabled === null ? 'status unknown' : '']}
          >
            An interrupted configuration save needs recovery before changes
          </StatusLine>
        ) : page && page.global_enabled === null ? (
          <StatusLine>Status unknown</StatusLine>
        ) : undefined
      }
      control={policy.toggle(
        'Use MCP servers',
        page?.global_enabled ?? null,
        (enabled) => ({
          operation: 'global_enabled',
          enabled,
        }),
      )}
    >
      {(state.draft || policy.pending || state.message) && (
        <PolicyOutcome policy={policy} session={props.session} />
      )}
    </SettingsItem>
  );
}

/**
 * A server's saved permissions in its details (B262): a switch for each
 * tool and whether it asks first, then the server, resource and prompt
 * access. Nothing here connects, tests or disconnects the server.
 */
export default function McpPolicyControls(props: McpPolicyControlsProps) {
  const policy = useMcpPolicy(props);
  const { state, page, locked, read, toggle } = policy;
  const serverId = state.serverId ?? '';
  const on = page?.items.filter((tool) => tool.enabled === true).length ?? 0;
  return (
    <section
      aria-label="Saved MCP permissions"
      className="settings-mcp-policy stack"
    >
      {page?.availability === 'recovery_required' && (
        <p role="status">
          An interrupted configuration save needs recovery before changes.
        </p>
      )}
      <SettingsGroup
        title="Tools"
        meta={
          page
            ? page.total === null
              ? 'Tool count unavailable'
              : `${on} of ${page.total} on`
            : undefined
        }
      >
        {page && page.total !== null && page.total > 8 && (
          <form
            className="settings-mcp-tool-search settings-divided"
            role="search"
            aria-label="Search tools"
            onSubmit={(event) => {
              event.preventDefault();
              void read(state.query);
            }}
          >
            <Search size={14} aria-hidden />
            <Input
              type="search"
              aria-label="Search tools"
              placeholder="Search tools"
              value={state.query}
              maxLength={128}
              disabled={locked}
              onChange={(event) =>
                props.session.update({ query: event.target.value })
              }
            />
          </form>
        )}
        {page?.items.map((tool) => (
          <SettingsItem
            key={tool.tool_id}
            label={tool.name}
            help={
              tool.destructive
                ? 'Changes things'
                : 'Review tool effects before use'
            }
            status={
              tool.approval_locked ? (
                <StatusLine>Always asks first</StatusLine>
              ) : undefined
            }
            layout="inline"
            bind={false}
            control={
              <>
                <label className="settings-mcp-ask">
                  {toggle(
                    `Ask before ${tool.name} runs`,
                    tool.requires_approval,
                    (enabled) => ({
                      operation: 'tool_approval',
                      server_id: serverId,
                      tool_id: tool.tool_id,
                      enabled,
                    }),
                    tool.approval_locked,
                  )}
                  <span aria-hidden>Ask first</span>
                </label>
                {toggle(`Use ${tool.name}`, tool.enabled, (enabled) => ({
                  operation: 'tool_enabled',
                  server_id: serverId,
                  tool_id: tool.tool_id,
                  enabled,
                }))}
              </>
            }
          />
        ))}
        {page && page.items.length === 0 && (
          <p className="settings-mcp-empty settings-divided">
            No tools saved yet. Test the server to find them.
          </p>
        )}
        {(state.cursor || page?.next_cursor) && (
          <div className="action-cluster settings-divided settings-mcp-pages">
            <Button
              disabled={locked || !state.cursor}
              onClick={() => void read(state.filter)}
            >
              First permission page
            </Button>
            <Button
              disabled={locked || !page?.next_cursor}
              onClick={() =>
                void read(state.filter, page?.next_cursor ?? undefined)
              }
            >
              Next permission page
            </Button>
          </div>
        )}
      </SettingsGroup>
      <SettingsGroup
        title="Permissions"
        meta={
          <IconButton
            size="sm"
            label="Refresh permissions"
            disabled={!state.active || Boolean(state.busy)}
            onClick={() => void read(state.filter, state.cursor)}
          >
            <RefreshCw size={15} aria-hidden />
          </IconButton>
        }
      >
        {page && (
          <>
            <SettingsItem
              label="Server access"
              help="Row-Bot can use this server at all."
              layout="inline"
              bind={false}
              control={toggle(
                'Server access',
                page.server_enabled,
                (enabled) => ({
                  operation: 'server_enabled',
                  server_id: serverId,
                  enabled,
                }),
              )}
            />
            <SettingsItem
              label="Resources"
              help="Chats can read what the server shares."
              layout="inline"
              bind={false}
              control={toggle(
                'Resource access',
                page.resources_enabled,
                (enabled) => ({
                  operation: 'utility_enabled',
                  server_id: serverId,
                  utility: 'resources',
                  enabled,
                }),
              )}
            />
            <SettingsItem
              label="Prompts"
              help="Chats can use the server’s saved prompts."
              layout="inline"
              bind={false}
              control={toggle(
                'Prompt access',
                page.prompts_enabled,
                (enabled) => ({
                  operation: 'utility_enabled',
                  server_id: serverId,
                  utility: 'prompts',
                  enabled,
                }),
              )}
            />
          </>
        )}
      </SettingsGroup>
      <PolicyOutcome policy={policy} session={props.session} />
    </section>
  );
}
