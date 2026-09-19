import { useEffect, useSyncExternalStore } from 'react';
import { Button, Field, Input } from '../../ui/primitives';
import type {
  McpConfigurationReceipt,
  McpConfigurationReview,
} from './CapabilitySettings';

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
const label = (value: boolean | null) =>
  value === null ? 'Unknown' : value ? 'Enabled' : 'Disabled';

export default function McpCatalogAcceptance({
  session,
  load,
  review,
  execute,
}: McpCatalogAcceptanceProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const validPage = (page: McpTestedCatalogPage) =>
    page.schema_version === 1 &&
    page.server_id === session.serverId &&
    page.test_command_id === session.testCommandId &&
    page.items.length <= 50;
  const read = async (query: string, cursor?: string) => {
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
      if (!validPage(page)) throw Error();
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
  };
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
      !current.active ||
      current.busy ||
      current.pending ||
      !current.page?.configuration_revision ||
      !available
    )
      return;
    const command: McpCatalogCommand = {
      command_id: crypto.randomUUID(),
      type: 'mcp.catalog.accept',
      payload: {
        configuration_revision: current.page.configuration_revision,
        server_id: session.serverId,
        test_command_id: session.testCommandId,
      },
    };
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '' });
    try {
      const result = await review(command.payload, abort.signal);
      if (abort.signal.aborted) return;
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
      session.update({
        reviewed: { command, review: result },
        busy: '',
        message: `Review complete for all ${result.tool_count} tested tools. Accept tools explicitly saves these definitions and defaults.`,
      });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The tested catalog could not be reviewed. Configuration may have changed.',
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
      ) {
        session.update({
          pending: null,
          busy: '',
          page: current.page
            ? { ...current.page, availability: 'accepted' }
            : null,
          message:
            'Tested tools accepted. Review saved tool permissions before connecting; no server was retested or started.',
        });
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
            'The original acceptance is unconfirmed. Check its receipt before making another change.',
        });
      }
    } catch {
      session.update({
        busy: '',
        message:
          'The original acceptance is unconfirmed. Check its receipt before making another change.',
      });
    }
  };
  return (
    <section aria-label="Accept tested MCP tools" className="settings-section">
      <h3>Tested tools</h3>
      <p>
        Review the saved result of this Test. Reading or accepting it does not
        reconnect or retest the server.
      </p>
      {state.page && (
        <p>
          {state.page.total === null
            ? 'Tool count unavailable.'
            : `${state.page.total} matching tested tools.`}
        </p>
      )}
      {state.page?.manual_selection_required && (
        <p role="status">
          This server overlaps native capabilities or requires extra review. New
          tools remain disabled until individually enabled.
        </p>
      )}
      {state.page && !available && (
        <p role="status">
          Catalog: {state.page.availability.replaceAll('_', ' ')}.{' '}
          {state.page.availability === 'stale'
            ? 'Run an explicit new Test for the changed configuration.'
            : ''}
        </p>
      )}
      <Field label="Filter tested tools">
        <Input
          value={state.query}
          maxLength={128}
          disabled={locked}
          onChange={(event) => session.update({ query: event.target.value })}
        />
      </Field>
      <div className="button-row">
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
      </div>
      <ul>
        {state.page?.items.map((tool) => (
          <li key={tool.tool_id}>
            <strong>{tool.name}</strong> — {label(tool.enabled_after_accept)}{' '}
            after acceptance.{' '}
            {tool.requires_approval
              ? 'Approval required.'
              : 'Existing approval policy applies.'}
          </li>
        ))}
      </ul>
      <div className="button-row">
        <Button
          disabled={locked || !available}
          onClick={() => void requestReview()}
        >
          Review acceptance
        </Button>
        <Button
          disabled={locked || !state.reviewed}
          onClick={() => void save(state.reviewed)}
        >
          Accept tools
        </Button>
        {state.pending && (
          <Button
            disabled={!state.active || Boolean(state.busy)}
            onClick={() => void save(state.pending)}
          >
            Check original acceptance
          </Button>
        )}
      </div>
      {state.message && <p role="status">{state.message}</p>}
    </section>
  );
}
