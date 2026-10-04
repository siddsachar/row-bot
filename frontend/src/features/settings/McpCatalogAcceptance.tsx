import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { Button, Input } from '../../ui/primitives';
import type {
  McpConfigurationReceipt,
  McpConfigurationReview,
} from './CapabilitySettings';
import { mcpRevision, reviewFresh } from './mcp-revision';

export type McpTestedCatalogPage = {
  schema_version: 1;
  configuration_revision: string | null;
  server_id: string;
  test_command_id: string;
  availability: string;
  manual_selection_required: boolean | null;
  items: {
    tool_id: string;
    name: string;
    enabled_after_accept: boolean | null;
    requires_approval: boolean;
    destructive: boolean;
    effect?: 'read_only' | 'mutation' | 'interaction' | 'unknown';
  }[];
  total: number | null;
  next_cursor: string | null;
};
export type McpCatalogCommand = {
  command_id: string;
  type: 'mcp.catalog.accept';
  payload: {
    configuration_revision: string;
    server_id: string;
    test_command_id: string;
  };
};
export type McpCatalogReview = McpConfigurationReview & {
  server_id: string;
  test_command_id: string;
  tool_count: number;
  manual_selection_required: boolean;
};
type Attempt = { command: McpCatalogCommand; review: McpCatalogReview };
type State = {
  active: boolean;
  page: McpTestedCatalogPage | null;
  query: string;
  filter: string;
  cursor?: string;
  reviewed: Attempt | null;
  pending: Attempt | null;
  busy: string;
  message: string;
};

/** One bounded exact Test source owned by the authenticated settings lifetime. */
export function createMcpCatalogSession(
  serverId: string,
  testCommandId: string,
) {
  let state: State = {
    active: true,
    page: null,
    query: '',
    filter: '',
    reviewed: null,
    pending: null,
    busy: '',
    message: '',
  };
  const listeners = new Set<() => void>();
  const reads = new Set<AbortController>();
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((notify) => notify());
  };
  return {
    serverId,
    testCommandId,
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
      if (state.active) reads.add(abort);
      else abort.abort();
      return abort;
    },
    endRead: (abort: AbortController) => {
      reads.delete(abort);
    },
    hasRetained: () =>
      state.active && Boolean(state.query || state.reviewed || state.pending),
    dispose: () => {
      reads.forEach((abort) => abort.abort());
      reads.clear();
      state = {
        active: false,
        page: null,
        query: '',
        filter: '',
        reviewed: null,
        pending: null,
        busy: '',
        message: 'Sign in again to review tested tools.',
      };
      listeners.forEach((notify) => notify());
    },
  };
}
export type McpCatalogSession = ReturnType<typeof createMcpCatalogSession>;
export type McpCatalogAcceptanceProps = {
  mutationsDisabled?: boolean;
  session: McpCatalogSession;
  load: (
    query: {
      server_id: string;
      test_command_id: string;
      query: string;
      cursor?: string;
    },
    signal: AbortSignal,
  ) => Promise<McpTestedCatalogPage>;
  review: (
    payload: McpCatalogCommand['payload'],
    signal: AbortSignal,
  ) => Promise<McpCatalogReview>;
  execute: (
    command: McpCatalogCommand,
    review: McpCatalogReview,
  ) => Promise<McpConfigurationReceipt>;
};
/** What a tested tool does once its catalog is accepted. */
const label = (value: boolean | null) =>
  value === null
    ? 'Unknown after you accept'
    : value
      ? 'On after you accept'
      : 'Stays off until you turn it on';
const CATALOG_STATES: Record<string, string> = {
  accepted: 'These tools are accepted.',
  stale:
    'The server changed since this Test. Test it again to accept its tools.',
  recovery_required:
    'An interrupted save needs to finish first. Check the saved servers above.',
  unavailable: 'These tested tools aren’t available. Test the server again.',
};

export default function McpCatalogAcceptance({
  session,
  load,
  review,
  execute,
  mutationsDisabled = false,
}: McpCatalogAcceptanceProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const read = useCallback(
    async (query: string, cursor?: string) => {
      const current = session.getSnapshot();
      if (!current.active || current.busy) return;
      const abort = session.beginRead();
      session.update({ busy: 'load', reviewed: null });
      try {
        const page = await load(
          {
            server_id: session.serverId,
            test_command_id: session.testCommandId,
            query,
            cursor,
          },
          abort.signal,
        );
        if (abort.signal.aborted) return;
        if (
          page.schema_version !== 1 ||
          page.server_id !== session.serverId ||
          page.test_command_id !== session.testCommandId ||
          page.items.length > 50
        )
          throw Error();
        session.update({ page, filter: query, cursor, busy: '', message: '' });
      } catch {
        if (!abort.signal.aborted)
          session.update({
            busy: '',
            message:
              'Tested tools are unavailable or changed. Return to the first page.',
          });
      } finally {
        session.endRead(abort);
      }
    },
    [session, load],
  );
  // Another MCP panel saved: a catalog still waiting for Accept reads the new revision.
  useEffect(
    () =>
      mcpRevision.subscribe((source) => {
        const current = session.getSnapshot();
        if (source !== session && current.page?.availability === 'available')
          void read(current.filter, current.cursor);
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
        server_id: session.serverId,
        test_command_id: session.testCommandId,
        query: current.filter,
        cursor: current.cursor,
      },
      abort.signal,
    )
      .then((page) => {
        if (abort.signal.aborted) return;
        if (
          page.schema_version !== 1 ||
          page.server_id !== session.serverId ||
          page.test_command_id !== session.testCommandId ||
          page.items.length > 50
        )
          throw Error();
        session.update({ page, busy: '' });
      })
      .catch(() => {
        if (!abort.signal.aborted)
          session.update({
            busy: '',
            message: 'Tested tools are unavailable. Refresh to try again.',
          });
      })
      .finally(() => session.endRead(abort));
  }, [session, load]);
  const available = Boolean(
    state.page?.configuration_revision &&
    state.page.availability === 'available',
  );
  const locked = !state.active || Boolean(state.busy || state.pending);
  const requestReview = async () => {
    const current = session.getSnapshot();
    if (
      mutationsDisabled ||
      !current.active ||
      current.busy ||
      current.pending ||
      !current.page?.configuration_revision ||
      !available
    )
      return;
    const sent: { command?: McpCatalogCommand } = {};
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '' });
    try {
      const result = await reviewFresh(
        (revision) => {
          sent.command = {
            command_id: crypto.randomUUID(),
            type: 'mcp.catalog.accept',
            payload: {
              configuration_revision: revision,
              server_id: session.serverId,
              test_command_id: session.testCommandId,
            },
          };
          return review(sent.command.payload, abort.signal);
        },
        current.page.configuration_revision,
        async () => {
          const page = await load(
            {
              server_id: session.serverId,
              test_command_id: session.testCommandId,
              query: current.filter,
              cursor: current.cursor,
            },
            abort.signal,
          );
          if (page.test_command_id !== session.testCommandId) return null;
          session.update({ page });
          return page.availability === 'available'
            ? page.configuration_revision
            : null;
        },
      );
      const command = sent.command;
      if (abort.signal.aborted || !command) return;
      if (
        result.configuration_revision !==
          command.payload.configuration_revision ||
        result.server_id !== session.serverId ||
        result.test_command_id !== session.testCommandId ||
        !Number.isInteger(result.tool_count) ||
        result.tool_count < 0 ||
        result.tool_count > 1000
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
            'The tested catalog could not be validated. Configuration may have changed.',
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
          busy: '',
          page: current.page
            ? { ...current.page, availability: 'accepted' }
            : null,
          message:
            'Tools accepted. Check their switches under Tools before you connect; nothing was retested or started.',
        });
        mcpRevision.saved(session);
      } else if (result.status === 'rejected') {
        session.update({
          pending: null,
          busy: '',
          message: 'Acceptance was rejected. Refresh and review a new request.',
        });
      } else {
        session.update({
          busy: '',
          message:
            "Row-Bot couldn't confirm the tools were accepted. Check again before another change.",
        });
      }
    } catch {
      session.update({
        busy: '',
        message:
          "Row-Bot couldn't confirm the tools were accepted. Check again before another change.",
      });
    }
  };
  const total = state.page?.total ?? null;
  return (
    <section
      aria-label="Accept tested MCP tools"
      className="settings-group settings-mcp-catalog"
    >
      <header className="settings-group-head">
        <h3>Tested tools</h3>
        <p>Found by the last Test; accepting doesn’t retest the server.</p>
      </header>
      <div className="settings-group-surface">
        <div className="settings-divided settings-mcp-catalog-head">
          {state.page && (
            <p>
              {total === null
                ? 'Tool count unavailable.'
                : `${total} ${total === 1 ? 'tool' : 'tools'} found.`}
            </p>
          )}
          {state.page?.manual_selection_required && (
            <p role="status">
              Some of its tools overlap Row-Bot’s own or need a closer look. New
              tools stay off until you turn each one on.
            </p>
          )}
          {state.page && !available && (
            <p role="status">
              {CATALOG_STATES[state.page.availability] ??
                'These tested tools can’t be accepted right now.'}
            </p>
          )}
          {((total ?? 0) > 8 || state.filter) && (
            <Input
              type="search"
              aria-label="Filter tested tools"
              placeholder="Filter tools"
              value={state.query}
              maxLength={128}
              disabled={locked}
              onChange={(event) =>
                session.update({ query: event.target.value })
              }
              onKeyDown={(event) => {
                if (event.key === 'Enter') void read(state.query);
              }}
            />
          )}
        </div>
        <p>
          Read / Change / High impact describe the tested catalog. Unknown
          effects stay in High impact. Annotations do not override approval
          policy. Row-Bot exposure is separate from upstream account grants.
        </p>
        {(['Read', 'Change', 'High impact'] as const).map((group) => {
          const tools =
            state.page?.items.filter(
              (tool) =>
                (tool.destructive || !tool.effect || tool.effect === 'unknown'
                  ? 'High impact'
                  : tool.effect === 'read_only'
                    ? 'Read'
                    : 'Change') === group,
            ) ?? [];
          return tools.length ? (
            <section key={group} aria-label={`${group} tools`}>
              <h4>{group}</h4>
              <ul className="settings-mcp-catalog-list">
                {tools.map((tool) => (
                  <li key={tool.tool_id} className="settings-divided">
                    <details>
                      <summary>{tool.name}</summary>
                      <p>
                        Effect: {tool.effect ?? 'unknown'}.{' '}
                        {tool.requires_approval
                          ? 'Approval required by the current policy.'
                          : 'Existing profile and runtime approval rules apply.'}
                      </p>
                    </details>
                    <span>
                      {label(tool.enabled_after_accept)}
                      {tool.requires_approval ? ' · asks first' : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null;
        })}
        <div className="settings-divided settings-mcp-catalog-actions">
          <Button
            variant="primary"
            disabled={mutationsDisabled || locked || !available}
            onClick={() => void requestReview()}
          >
            Accept tools
          </Button>
          {state.pending && (
            <Button
              disabled={
                mutationsDisabled || !state.active || Boolean(state.busy)
              }
              onClick={() => void save(state.pending)}
            >
              Check original acceptance
            </Button>
          )}
          {(state.cursor ||
            state.page?.next_cursor ||
            state.filter ||
            (!state.page && state.message)) && (
            <>
              <Button disabled={locked} onClick={() => void read(state.query)}>
                First page
              </Button>
              <Button
                disabled={locked || !state.page?.next_cursor}
                onClick={() =>
                  void read(state.filter, state.page?.next_cursor ?? undefined)
                }
              >
                Next page
              </Button>
            </>
          )}
          {state.message && <p role="status">{state.message}</p>}
        </div>
      </div>
    </section>
  );
}
