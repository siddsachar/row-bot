import { useEffect, useSyncExternalStore } from 'react';
import { Button, Field, Input, Select } from '../../ui/primitives';

export type McpConfigurationPage = {
  schema_version: 1;
  revision: string | null;
  availability: string;
  enabled: boolean | null;
  items: {
    server_id: string;
    name: string;
    transport: string;
    enabled: boolean | null;
    runtime_status: string | null;
    configured_fields: string[];
    tool_count: number | null;
    connection_present: boolean | null;
  }[];
  total: number | null;
  next_cursor: string | null;
};
export type McpConfigurationIntent = {
  operation: 'add' | 'edit' | 'rename' | 'import';
  server_id?: string;
  fields?: Record<string, unknown>;
  import_json?: string;
};
export type McpConfigurationReview = {
  configuration_revision: string;
  action_digest: string;
  nonce?: string;
};
export type McpConfigurationCommand = {
  command_id: string;
  type: 'mcp.configuration.save';
  payload: { configuration_revision: string; intent: McpConfigurationIntent };
};
export type McpConfigurationReceipt = {
  command_id: string;
  status: string;
  mcp_configuration?: {
    status: string;
    revision: string | null;
    code?: string | null;
  };
};
type Attempt = {
  command: McpConfigurationCommand;
  review: McpConfigurationReview;
};
type Draft = {
  operation: McpConfigurationIntent['operation'];
  serverId: string;
  name: string;
  transport: string;
  launch: string;
  arguments: string;
  extra: string;
  imported: string;
};
const emptyDraft = (): Draft => ({
  operation: 'add',
  serverId: '',
  name: '',
  transport: 'stdio',
  launch: '',
  arguments: '',
  extra: '',
  imported: '',
});
type State = {
  page: McpConfigurationPage | null;
  query: string;
  filter: string;
  cursor?: string;
  draft: Draft;
  reviewed: Attempt | null;
  pending: Attempt | null;
  busy: string;
  message: string;
  active: boolean;
};

/** The authenticated runtime owns this session; unmount never disposes a command. */
export function createCapabilitySettingsSession() {
  let state: State = {
    page: null,
    query: '',
    filter: '',
    draft: emptyDraft(),
    reviewed: null,
    pending: null,
    busy: '',
    message: '',
    active: true,
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
      state.active &&
      Boolean(
        state.pending ||
        state.reviewed ||
        Object.entries(state.draft).some(
          ([key, value]) => value !== emptyDraft()[key as keyof Draft],
        ),
      ),
    dispose: () => {
      aborters.forEach((abort) => abort.abort());
      aborters.clear();
      state = {
        page: null,
        query: '',
        filter: '',
        draft: emptyDraft(),
        reviewed: null,
        pending: null,
        busy: '',
        message: 'Sign in again to manage MCP settings.',
        active: false,
      };
      listeners.forEach((notify) => notify());
    },
  };
}
export type CapabilitySettingsSession = ReturnType<
  typeof createCapabilitySettingsSession
>;
export type CapabilitySettingsProps = {
  onConnection?: (serverId: string, name: string) => void;
  session: CapabilitySettingsSession;
  load: (
    query: { query: string; cursor?: string },
    signal: AbortSignal,
  ) => Promise<McpConfigurationPage>;
  review: (
    payload: McpConfigurationCommand['payload'],
    signal: AbortSignal,
  ) => Promise<McpConfigurationReview>;
  execute: (
    command: McpConfigurationCommand,
    review: McpConfigurationReview,
  ) => Promise<McpConfigurationReceipt>;
};

function boundedPage(page: McpConfigurationPage): McpConfigurationPage {
  if (page.items.length > 50) throw Error('Invalid saved server page');
  return page;
}

function buildIntent(draft: Draft): McpConfigurationIntent {
  if (draft.operation === 'import')
    return { operation: 'import', import_json: draft.imported };
  const fields: Record<string, unknown> = {};
  if (draft.name) fields.name = draft.name;
  if (draft.operation !== 'rename') {
    if (draft.operation === 'add' || draft.launch) {
      fields.transport = draft.transport;
      fields[draft.transport === 'stdio' ? 'command' : 'url'] = draft.launch;
    }
    if (draft.arguments) {
      const args: unknown = JSON.parse(draft.arguments);
      if (!Array.isArray(args) || args.some((item) => typeof item !== 'string'))
        throw Error();
      fields.args = args;
    }
    if (draft.extra) {
      const extra: unknown = JSON.parse(draft.extra);
      if (!extra || Array.isArray(extra) || typeof extra !== 'object')
        throw Error();
      const allowed = [
        'cwd',
        'env',
        'headers',
        'connect_timeout',
        'tool_timeout',
        'output_limit',
      ];
      if (Object.keys(extra).some((key) => !allowed.includes(key)))
        throw Error();
      Object.assign(fields, extra);
    }
  }
  return {
    operation: draft.operation,
    ...(draft.operation === 'add' ? {} : { server_id: draft.serverId }),
    fields,
  };
}

export default function CapabilitySettings({
  onConnection,
  session,
  load,
  review,
  execute,
}: CapabilitySettingsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { page, draft, busy, pending, reviewed } = state;
  const locked = Boolean(busy || pending || !state.active);
  const canSave =
    state.active &&
    page?.revision &&
    ['available', 'missing'].includes(page.availability);
  useEffect(() => {
    const current = session.getSnapshot();
    if (!current.active || current.page || current.busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'load' });
    void load({ query: current.filter, cursor: current.cursor }, abort.signal)
      .then(
        (result) => {
          if (!abort.signal.aborted) {
            if (result.items.length > 50)
              session.update({
                busy: '',
                message:
                  'Saved MCP settings are unavailable. Refresh to try again.',
              });
            else session.update({ page: boundedPage(result), busy: '' });
          }
        },
        () => {
          if (!abort.signal.aborted)
            session.update({
              busy: '',
              message:
                'Saved MCP settings are unavailable. Refresh to try again.',
            });
        },
      )
      .finally(() => session.endRead(abort));
  }, [session, load]);
  const refresh = async (filter = state.filter, cursor?: string) => {
    if (session.getSnapshot().busy || !session.getSnapshot().active) return;
    const abort = session.beginRead();
    session.update({ busy: 'load', reviewed: null });
    try {
      const result = boundedPage(
        await load({ query: filter, cursor }, abort.signal),
      );
      if (!abort.signal.aborted)
        session.update({ page: result, filter, cursor, busy: '', message: '' });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'Saved MCP settings changed or are unavailable. Return to the first page.',
        });
    } finally {
      session.endRead(abort);
    }
  };
  const edit = (patch: Partial<Draft>) =>
    session.update({
      draft: { ...draft, ...patch },
      reviewed: null,
      message: '',
    });
  const requestReview = async () => {
    if (
      session.getSnapshot().busy ||
      session.getSnapshot().pending ||
      !session.getSnapshot().active ||
      !canSave ||
      !page?.revision
    )
      return;
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '' });
    try {
      const intent = buildIntent(draft);
      if (new TextEncoder().encode(JSON.stringify(intent)).length > 128 * 1024)
        throw Error();
      const command: McpConfigurationCommand = {
        command_id: crypto.randomUUID(),
        type: 'mcp.configuration.save',
        payload: { configuration_revision: page.revision, intent },
      };
      const result = await review(command.payload, abort.signal);
      if (
        result.configuration_revision !== command.payload.configuration_revision
      )
        throw Error();
      if (!abort.signal.aborted)
        session.update({
          reviewed: { command, review: result },
          busy: '',
          message:
            'Review complete. Save Disabled applies these settings without connecting the server.',
        });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The settings could not be reviewed. Check the fields and refresh saved settings.',
        });
    } finally {
      session.endRead(abort);
    }
  };
  const save = async (attempt: Attempt | null) => {
    if (!attempt || session.getSnapshot().busy || !session.getSnapshot().active)
      return;
    session.update({
      busy: 'save',
      pending: attempt,
      reviewed: null,
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
          draft: emptyDraft(),
          busy: '',
          page: page ? { ...page, revision: null } : null,
          message:
            'Saved disabled. Refresh to view the current configuration. Connection cleanup was not requested.',
        });
      } else if (result.status === 'rejected')
        session.update({
          pending: null,
          busy: '',
          page: page ? { ...page, revision: null } : null,
          message:
            'The save was rejected. Refresh and review your settings again.',
        });
      else
        session.update({
          busy: '',
          message:
            'Save outcome is uncertain. Check the original save; no new save can start until it is resolved.',
        });
    } catch {
      session.update({
        busy: '',
        message:
          'Save outcome is uncertain. Check the original save before making another change.',
      });
    }
  };
  return (
    <section aria-label="MCP configuration" className="settings-section">
      <h2>MCP servers</h2>
      <p>
        Manage saved server settings. Launch details and credentials are
        write-only.
      </p>
      <Field label="Search saved servers">
        <Input
          value={state.query}
          disabled={locked}
          onChange={(event) => session.update({ query: event.target.value })}
          maxLength={128}
        />
      </Field>
      <Button disabled={locked} onClick={() => void refresh(state.query)}>
        Search
      </Button>
      <Button
        disabled={Boolean(busy) || !state.active}
        onClick={() => void refresh()}
      >
        Refresh
      </Button>
      {page && (
        <>
          <p role="status">
            {page.total === null
              ? 'Saved server count unavailable'
              : `${page.total} saved servers`}
            .{' '}
            {page.availability === 'recovery_required'
              ? 'An interrupted save requires recovery before new changes.'
              : `Configuration: ${page.availability}.`}
          </p>
          <ul>
            {page.items.map((server) => (
              <li key={server.server_id}>
                <strong>{server.name}</strong> —{' '}
                {server.enabled === null
                  ? 'Enablement unknown'
                  : server.enabled
                    ? 'Enabled'
                    : 'Disabled'}
                ; {server.runtime_status ?? 'Runtime status unknown'}.
                <span>
                  {' '}
                  Configured fields:{' '}
                  {server.configured_fields.join(', ') || 'none'}.
                </span>
                {onConnection && (
                  <Button
                    onClick={() => onConnection(server.server_id, server.name)}
                  >
                    Connection {server.name}
                  </Button>
                )}
                <Button
                  disabled={locked}
                  onClick={() =>
                    session.update({
                      draft: {
                        ...emptyDraft(),
                        operation: 'edit',
                        serverId: server.server_id,
                        transport:
                          server.transport === 'unknown'
                            ? 'stdio'
                            : server.transport,
                      },
                      reviewed: null,
                    })
                  }
                >
                  Edit {server.name}
                </Button>
                <Button
                  disabled={locked}
                  onClick={() =>
                    session.update({
                      draft: {
                        ...emptyDraft(),
                        operation: 'rename',
                        serverId: server.server_id,
                        name: server.name,
                      },
                      reviewed: null,
                    })
                  }
                >
                  Rename {server.name}
                </Button>
              </li>
            ))}
          </ul>
          <Button
            disabled={locked || !state.cursor}
            onClick={() => void refresh(state.filter)}
          >
            First page
          </Button>
          <Button
            disabled={locked || !page.next_cursor}
            onClick={() =>
              void refresh(state.filter, page.next_cursor ?? undefined)
            }
          >
            Next page
          </Button>
        </>
      )}
      <fieldset disabled={locked || !canSave}>
        <legend>Save server settings</legend>
        <Field label="Operation">
          <Select
            value={draft.operation}
            onChange={(event) =>
              edit({ operation: event.target.value as Draft['operation'] })
            }
          >
            <option value="add">Add</option>
            <option value="edit" disabled={!draft.serverId}>
              Edit selected server
            </option>
            <option value="rename" disabled={!draft.serverId}>
              Rename selected server
            </option>
            <option value="import">Import JSON</option>
          </Select>
        </Field>
        {draft.operation === 'import' ? (
          <Field label="Server import JSON">
            <textarea
              value={draft.imported}
              maxLength={131072}
              onChange={(event) => edit({ imported: event.target.value })}
            />
          </Field>
        ) : (
          <>
            <Field
              label={
                draft.operation === 'rename' ? 'New server name' : 'Server name'
              }
            >
              <Input
                value={draft.name}
                maxLength={128}
                disabled={draft.operation === 'edit'}
                onChange={(event) => edit({ name: event.target.value })}
              />
            </Field>
            {draft.operation !== 'rename' && (
              <>
                <Field label="Transport">
                  <Select
                    value={draft.transport}
                    onChange={(event) =>
                      edit({ transport: event.target.value })
                    }
                  >
                    <option value="stdio">Local command</option>
                    <option value="streamable_http">HTTP</option>
                    <option value="sse">SSE</option>
                  </Select>
                </Field>
                <Field
                  label={
                    draft.transport === 'stdio' ? 'New command' : 'New URL'
                  }
                >
                  <Input
                    value={draft.launch}
                    maxLength={16384}
                    autoComplete="off"
                    onChange={(event) => edit({ launch: event.target.value })}
                  />
                </Field>
                <Field label="New arguments (JSON array)">
                  <Input
                    value={draft.arguments}
                    maxLength={65536}
                    autoComplete="off"
                    onChange={(event) =>
                      edit({ arguments: event.target.value })
                    }
                  />
                </Field>
                <Field label="Additional settings (JSON)">
                  <textarea
                    value={draft.extra}
                    maxLength={131072}
                    autoComplete="off"
                    onChange={(event) => edit({ extra: event.target.value })}
                  />
                </Field>
                <p>
                  Optional fields: cwd, env, headers, connect_timeout,
                  tool_timeout, output_limit. Omitted values remain saved; use
                  empty objects or arrays to clear them.
                </p>
              </>
            )}
          </>
        )}
        <Button onClick={() => void requestReview()}>Review settings</Button>
        <Button disabled={!reviewed} onClick={() => void save(reviewed)}>
          Save Disabled
        </Button>
      </fieldset>
      {pending && (
        <Button
          disabled={Boolean(busy) || !state.active}
          onClick={() => void save(pending)}
        >
          Check original save
        </Button>
      )}
      {state.message && <p role="status">{state.message}</p>}
    </section>
  );
}
