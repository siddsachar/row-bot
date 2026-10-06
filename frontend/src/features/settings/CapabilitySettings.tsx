import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  Braces,
  Globe2,
  MoreHorizontal,
  PanelRight,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  SquareTerminal,
  TextCursorInput,
  Trash2,
  X,
} from 'lucide-react';
import { ModalTask } from '../../ui/overlays';
import { clientError } from '../../api/errors';
import {
  Button,
  Field,
  IconButton,
  Input,
  Menu,
  Segmented,
  StatusDot,
} from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';
import {
  SettingsAdvanced,
  SettingsGroup,
  SettingsItem,
  SettingsPageMenu,
  SettingsStatus,
  StatusLine,
} from './anatomy';
import { mcpRevision, reviewFresh } from './mcp-revision';

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
    requirements?: {
      id: 'node' | 'uv' | 'playwright-chrome' | 'docker' | 'other';
      label: string;
      available: boolean;
      managed: boolean;
      installable: boolean;
      source: string;
    }[];
  }[];
  total: number | null;
  next_cursor: string | null;
};
export type McpConfigurationIntent = {
  operation: 'add' | 'edit' | 'rename' | 'import' | 'delete';
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
    runtime_cleanup?: string;
    code?: string | null;
  };
};
type Attempt = {
  command: McpConfigurationCommand;
  review: McpConfigurationReview;
};
type Pair = { key: string; value: string };
type Draft = {
  operation: McpConfigurationIntent['operation'];
  serverId: string;
  name: string;
  transport: string;
  launch: string;
  /** One argument per line (a JSON array is still read as before). */
  arguments: string;
  /** Environment variables (a local command) or headers (HTTP), masked. */
  env: Pair[];
  headers: Pair[];
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
  env: [],
  headers: [],
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
  /**
   * Where the message is shown: next to what was clicked ("editor", "list"),
   * or at the top of the page ("page").
   */
  origin: string;
  active: boolean;
  /** A server waiting for the Remove confirmation (its row or details). */
  removing: { server_id: string; name: string } | null;
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
    origin: 'page',
    active: true,
    removing: null,
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
    /** Asks to remove a saved server; the page shows the confirmation. */
    confirmRemove: (server_id: string, name: string) =>
      update({ removing: { server_id, name } }),
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
          ([key, value]) =>
            JSON.stringify(value) !==
            JSON.stringify(emptyDraft()[key as keyof Draft]),
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
        origin: 'page',
        active: false,
        removing: null,
      };
      listeners.forEach((notify) => notify());
    },
  };
}
export type CapabilitySettingsSession = ReturnType<
  typeof createCapabilitySettingsSession
>;
export type CapabilitySettingsProps = {
  /** Opens a saved server's details (connection, tools, permissions). */
  onConnection?: (serverId: string, name: string) => void;
  /** A saved server was removed (its details close). */
  onRemoved?: (serverId: string) => void;
  /** Show only this saved server (Apps › an app › Advanced settings). */
  only?: string;
  /** Open the add dialog at once (Apps › Advanced › Add a custom connection). */
  startAdd?: boolean;
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

/**
 * Name and value rows for environment variables or headers (U53): values
 * are masked like any secret and never read back from the server.
 */
function PairRows({
  label,
  rows,
  hint,
  onChange,
}: {
  label: string;
  rows: Pair[];
  hint: string;
  onChange: (rows: Pair[]) => void;
}) {
  const singular = label === 'Headers' ? 'header' : 'variable';
  return (
    <div className="stack settings-mcp-pairs" role="group" aria-label={label}>
      <strong>{label}</strong>
      <small className="settings-help">{hint}</small>
      {rows.map((row, index) => (
        <div className="settings-mcp-pair" key={index}>
          <Input
            aria-label={`${label} name ${index + 1}`}
            placeholder="Name"
            value={row.key}
            maxLength={256}
            autoComplete="off"
            onChange={(event) =>
              onChange(
                rows.map((item, at) =>
                  at === index ? { ...item, key: event.target.value } : item,
                ),
              )
            }
          />
          <Input
            aria-label={`${label} value ${index + 1}`}
            placeholder="Value"
            type="password"
            value={row.value}
            maxLength={16384}
            autoComplete="new-password"
            onChange={(event) =>
              onChange(
                rows.map((item, at) =>
                  at === index ? { ...item, value: event.target.value } : item,
                ),
              )
            }
          />
          <IconButton
            size="sm"
            label={`Remove ${singular} ${index + 1}`}
            onClick={() => onChange(rows.filter((_, at) => at !== index))}
          >
            <X size={15} aria-hidden />
          </IconButton>
        </div>
      ))}
      <Button
        className="settings-link"
        onClick={() => onChange([...rows, { key: '', value: '' }])}
      >
        <Plus size={14} aria-hidden />
        Add {singular}
      </Button>
    </div>
  );
}

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
    if (draft.arguments.trim()) {
      const text = draft.arguments.trim();
      const args: unknown = text.startsWith('[')
        ? JSON.parse(text)
        : text
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean);
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
    // Key–value rows (U53): values stay masked on the page and write-only.
    const pairs = (rows: Pair[]) => {
      const named = rows.filter((row) => row.key.trim());
      if (!named.length) return null;
      return Object.fromEntries(
        named.map((row) => [row.key.trim(), row.value]),
      );
    };
    const env = pairs(draft.env);
    const headers = pairs(draft.headers);
    if (env) fields.env = env;
    if (headers) fields.headers = headers;
  }
  return {
    operation: draft.operation,
    ...(draft.operation === 'add' ? {} : { server_id: draft.serverId }),
    fields,
  };
}

type AddMode = 'manual' | 'json';

function transportLabel(transport: string) {
  return transport === 'stdio'
    ? 'On this computer'
    : transport === 'streamable_http'
      ? 'HTTP'
      : transport === 'sse'
        ? 'SSE'
        : 'Unknown type';
}

/** One saved server: status in words, one action by state, the rest in ⋯. */
function ServerRow({
  server,
  locked,
  canSave,
  onDetails,
  onEdit,
  onRename,
  onRemove,
}: {
  server: McpConfigurationPage['items'][number];
  locked: boolean;
  canSave: boolean;
  onDetails?: () => void;
  onEdit: () => void;
  onRename: () => void;
  onRemove: () => void;
}) {
  const missing = (server.requirements ?? []).filter(
    (requirement) => !requirement.available,
  );
  const uses = (server.requirements ?? []).filter(
    (requirement) => requirement.available,
  );
  const Icon = server.transport === 'stdio' ? SquareTerminal : Globe2;
  return (
    <SettingsItem
      className="settings-mcp-server"
      icon={<Icon size={16} aria-hidden />}
      tone={server.transport === 'stdio' ? '4' : '2'}
      bind={false}
      label={
        <>
          {onDetails ? (
            <button
              type="button"
              className="settings-mcp-server-name"
              aria-label={`${server.name} details`}
              onClick={onDetails}
            >
              {server.name}
            </button>
          ) : (
            <span className="settings-mcp-server-name">{server.name}</span>
          )}
          <StatusDot
            tone={server.enabled ? 'success' : 'neutral'}
            label={server.enabled ? 'On' : 'Off'}
            showLabel
          />
        </>
      }
      help={[
        transportLabel(server.transport),
        server.tool_count == null
          ? ''
          : `${server.tool_count} ${server.tool_count === 1 ? 'tool' : 'tools'}`,
        ...uses.map((requirement) => `uses ${requirement.label}`),
      ]
        .filter(Boolean)
        .join(' · ')}
      status={
        missing.length > 0 ? (
          <StatusLine tone="warning">
            {missing
              .map((requirement) =>
                requirement.source === 'unknown'
                  ? `Couldn’t check ${requirement.label}`
                  : requirement.installable
                    ? `Needs ${requirement.label}; install it under Runtimes`
                    : `Needs ${requirement.label}; set it up on this computer`,
              )
              .join(' · ')}
          </StatusLine>
        ) : undefined
      }
      trailing={
        <Menu
          label={`More actions for ${server.name}`}
          iconOnly
          variant="ghost"
          className="icon-action icon-action-sm"
          actions={[
            ...(onDetails
              ? [
                  {
                    label: 'Details',
                    icon: <PanelRight size={16} />,
                    onSelect: onDetails,
                  },
                ]
              : []),
            {
              label: 'Edit settings…',
              icon: <Pencil size={16} />,
              disabled: locked,
              onSelect: onEdit,
            },
            {
              label: 'Rename…',
              icon: <TextCursorInput size={16} />,
              disabled: locked,
              onSelect: onRename,
            },
            {
              label: 'Remove server…',
              icon: <Trash2 size={16} />,
              danger: true,
              disabled: locked || !canSave,
              onSelect: onRemove,
            },
          ]}
        >
          <MoreHorizontal size={16} aria-hidden />
        </Menu>
      }
    ></SettingsItem>
  );
}

/**
 * Settings › MCP › Servers (B262): the saved servers first, each with one
 * action by state and the rest in its ⋯; Add server opens one dialog
 * (Browse, Manual, Paste JSON) that ends by opening the new server's
 * details. Every save is the same reviewed configuration command as before.
 */
export default function CapabilitySettings({
  onConnection,
  onRemoved,
  only,
  startAdd,
  session,
  load,
  review,
  execute,
}: CapabilitySettingsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [mode, setMode] = useState<AddMode>(() =>
    session.getSnapshot().draft.operation === 'import' ? 'json' : 'manual',
  );
  const [searchOpen, setSearchOpen] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  useEffect(() => () => clearTimeout(searchTimer.current), []);
  // Another MCP panel saved: read the saved servers again for the new revision.
  useEffect(
    () =>
      mcpRevision.subscribe((source) => {
        const current = session.getSnapshot();
        if (source === session || !current.active || !current.page) return;
        const abort = session.beginRead();
        void load(
          { query: current.filter, cursor: current.cursor },
          abort.signal,
        )
          .then(
            (result) => {
              if (!abort.signal.aborted && result.items.length <= 50)
                session.update({ page: result });
            },
            () => undefined, // The next review reads again on a conflict.
          )
          .finally(() => session.endRead(abort));
      }),
    [session, load],
  );
  const { page, draft, busy, pending } = state;
  const locked = Boolean(busy || pending || !state.active);
  const nameInvalid =
    Boolean(draft.name) &&
    draft.operation !== 'edit' &&
    draft.operation !== 'import' &&
    !/^[A-Za-z0-9][A-Za-z0-9 _().-]{0,127}$/.test(draft.name);
  // A new server needs its name and command (or address) before saving.
  const incomplete =
    (draft.operation === 'add' &&
      (!draft.name.trim() || !draft.launch.trim())) ||
    (draft.operation === 'import' && !draft.imported.trim());
  const canSave = Boolean(
    state.active &&
    page?.revision &&
    ['available', 'missing'].includes(page.availability),
  );
  const hasDraft = Object.entries(draft).some(
    ([key, value]) =>
      JSON.stringify(value) !==
      JSON.stringify(emptyDraft()[key as keyof Draft]),
  );
  useEffect(() => {
    const current = session.getSnapshot();
    if (!current.active || current.busy) return;
    if (current.page) {
      // Opened again: show what is saved and running now (a plan may have changed it), keeping any draft.
      const abort = session.beginRead();
      void load({ query: current.filter, cursor: current.cursor }, abort.signal)
        .then(
          (result) => {
            if (!abort.signal.aborted && result.items.length <= 50)
              session.update({ page: boundedPage(result) });
          },
          () => undefined, // The page already shown stays; the next review reads again.
        )
        .finally(() => session.endRead(abort));
      return;
    }
    const abort = session.beginRead();
    session.update({ busy: 'load' });
    void load({ query: current.filter, cursor: current.cursor }, abort.signal)
      .then(
        (result) => {
          if (!abort.signal.aborted) {
            if (result.items.length > 50)
              session.update({
                busy: '',
                origin: 'page',
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
              origin: 'page',
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
          origin: 'page',
          message:
            'Saved MCP settings changed or are unavailable. Return to the first page.',
        });
    } finally {
      session.endRead(abort);
    }
  };
  /** Reads the list again (after a save or a row's command), keeping the message. */
  const reread = async () => {
    const current = session.getSnapshot();
    await refresh(current.filter, current.cursor);
    if (current.message && !session.getSnapshot().message)
      session.update({ message: current.message });
  };
  const edit = (patch: Partial<Draft>) =>
    session.update({
      draft: { ...draft, ...patch },
      reviewed: null,
      message: '',
    });
  const requestReview = async (
    override?: McpConfigurationIntent,
    origin = 'editor',
  ) => {
    if (
      session.getSnapshot().busy ||
      session.getSnapshot().pending ||
      !session.getSnapshot().active ||
      !canSave ||
      !page?.revision
    )
      return;
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '', origin });
    try {
      const intent = override ?? buildIntent(draft);
      if (new TextEncoder().encode(JSON.stringify(intent)).length > 128 * 1024)
        throw Error();
      const sent: { command?: McpConfigurationCommand } = {};
      const result = await reviewFresh(
        (revision) => {
          sent.command = {
            command_id: crypto.randomUUID(),
            type: 'mcp.configuration.save',
            payload: { configuration_revision: revision, intent },
          };
          return review(sent.command.payload, abort.signal);
        },
        page.revision,
        async () => {
          // A page cursor names the old revision; read the first page again.
          const fresh = boundedPage(
            await load({ query: session.getSnapshot().filter }, abort.signal),
          );
          session.update({ page: fresh, cursor: undefined });
          return fresh.revision;
        },
      );
      const command = sent.command;
      if (
        !command ||
        result.configuration_revision !== command.payload.configuration_revision
      )
        throw Error();
      if (!abort.signal.aborted) {
        const attempt = { command, review: result };
        session.update({
          reviewed: attempt,
          busy: '',
          message: '',
        });
        void save(attempt);
      }
    } catch (cause) {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message: (cause as { code?: string } | null)?.code
            ? clientError(cause).message
            : 'The settings could not be validated. Check the fields and try again.',
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
        const intent = attempt.command.payload.intent;
        const deleted = intent.operation === 'delete';
        const added =
          intent.operation === 'add' || intent.operation === 'import';
        session.update({
          pending: null,
          draft: emptyDraft(),
          busy: '',
          // The dialog closes; the outcome shows above the list.
          origin: 'list',
          page: page
            ? {
                ...page,
                revision: null,
                items: deleted
                  ? page.items.filter(
                      (item) => item.server_id !== intent.server_id,
                    )
                  : page.items,
                total:
                  deleted && page.total !== null ? page.total - 1 : page.total,
              }
            : null,
          message: deleted
            ? 'Server removed. Its connection is stopped or no longer running.'
            : added
              ? 'Added. It stays turned off until you connect it.'
              : 'Saved. It stays turned off until you connect it.',
        });
        setDialogOpen(false);
        mcpRevision.saved(session);
        if (deleted && intent.server_id) onRemoved?.(intent.server_id);
        // Show the saved list as it is now; keep the outcome message.
        await reread();
        const serverId = (
          result.mcp_configuration as { server_ids?: string[] } | undefined
        )?.server_ids?.[0];
        const nameOf = (id: string) =>
          session
            .getSnapshot()
            .page?.items.find((item) => item.server_id === id)?.name ??
          (typeof intent.fields?.name === 'string'
            ? intent.fields.name
            : 'New server');
        // Adding ends by opening the new server's details (B262). A renamed
        // server has a new identity, so a view scoped to the old one follows it.
        if (serverId && (added || (only && only !== serverId)))
          onConnection?.(serverId, nameOf(serverId));
      } else if (result.mcp_configuration?.code === 'mcp_cleanup_incomplete') {
        session.update({
          busy: '',
          page: page ? { ...page, revision: null } : null,
          message:
            'Server settings were deleted, but connection cleanup is incomplete. Retry the original command.',
        });
      } else if (result.status === 'rejected')
        session.update({
          pending: null,
          busy: '',
          page: page ? { ...page, revision: null } : null,
          message:
            'The save was rejected. Refresh and check your settings again.',
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
  // The message (and Check original save) sits next to what was clicked; if
  // that place is gone (the dialog closed), it shows above the list, or at
  // the top while the list can't be read.
  const shown = new Set([
    ...(page ? ['list'] : []),
    ...(dialogOpen ? ['editor'] : []),
  ]);
  const origin = shown.has(state.origin)
    ? state.origin
    : page
      ? 'list'
      : 'page';
  const note = (at: string) =>
    origin === at && (state.message || pending) ? (
      <div className="settings-mcp-note">
        {state.message && <p role="status">{state.message}</p>}
        {pending && (
          <Button
            disabled={Boolean(busy) || !state.active}
            onClick={() => void save(pending)}
          >
            Check original save
          </Button>
        )}
      </div>
    ) : null;
  const chooseMode = (next: AddMode) => {
    setMode(next);
    const current = session.getSnapshot().draft;
    if (next === 'json' && current.operation !== 'import')
      session.update({ draft: { ...current, operation: 'import' } });
    if (next !== 'json' && current.operation === 'import')
      session.update({ draft: { ...current, operation: 'add' } });
  };
  const openAdd = () => {
    const current = session.getSnapshot().draft;
    if (current.operation === 'edit' || current.operation === 'rename') {
      session.update({ draft: emptyDraft(), reviewed: null, message: '' });
      chooseMode('manual');
    } else if (current.operation === 'import') chooseMode('json');
    else if (hasDraft) chooseMode('manual');
    setDialogOpen(true);
  };
  const addOnOpen = useRef(startAdd);
  useEffect(() => {
    if (addOnOpen.current && page) {
      addOnOpen.current = false;
      openAdd();
    }
  }); // Opens once the saved servers have loaded.
  const openEditor = (
    operation: 'edit' | 'rename',
    server: McpConfigurationPage['items'][number],
  ) => {
    session.update({
      draft: {
        ...emptyDraft(),
        operation,
        serverId: server.server_id,
        name: operation === 'rename' ? server.name : '',
        transport: server.transport === 'unknown' ? 'stdio' : server.transport,
      },
      reviewed: null,
      message: '',
    });
    setDialogOpen(true);
  };
  const editing = draft.operation === 'edit' || draft.operation === 'rename';
  const editedName =
    page?.items.find((item) => item.server_id === draft.serverId)?.name ??
    'server';
  const total = page ? (page.total ?? page.items.length) : 0;
  const connectedCount =
    page?.items.filter((server) => server.connection_present).length ?? 0;
  const tools =
    page?.items.reduce(
      (sum, server) =>
        sum +
        (server.enabled && server.connection_present
          ? (server.tool_count ?? 0)
          : 0),
      0,
    ) ?? 0;
  // A search field once the list is long; before that an icon opens one.
  const manyServers = total > 6;
  const searchShown = manyServers || searchOpen || Boolean(state.query);
  const tab = editing ? 'manual' : mode;
  return (
    <section
      aria-label="MCP configuration"
      className="settings-section capability-page settings-mcp-page"
    >
      {page && (
        <>
          <SettingsStatus
            tone={
              page.enabled === false
                ? undefined
                : connectedCount
                  ? 'success'
                  : 'neutral'
            }
            more={[
              connectedCount
                ? `${tools} ${tools === 1 ? 'tool' : 'tools'} available`
                : '',
            ]}
          >
            {page.enabled === false
              ? 'MCP is off'
              : total === 0
                ? 'No servers yet'
                : `${connectedCount} of ${total} ${total === 1 ? 'server' : 'servers'} connected`}
          </SettingsStatus>
          <SettingsPageMenu
            label="More MCP actions"
            actions={[
              {
                label: 'Refresh',
                icon: <RefreshCw size={16} />,
                disabled: Boolean(busy) || !state.active,
                onSelect: () => void refresh(),
              },
            ]}
          />
          <SettingsGroup
            title="Servers"
            anchor="mcp-servers"
            className="settings-mcp-servers"
            meta={
              <>
                {searchShown ? (
                  <form
                    className="settings-mcp-search"
                    role="search"
                    aria-label="Search servers"
                    onSubmit={(event) => {
                      event.preventDefault();
                      clearTimeout(searchTimer.current);
                      void refresh(state.query);
                    }}
                  >
                    <Search size={14} aria-hidden />
                    <Input
                      type="search"
                      aria-label="Search servers"
                      placeholder="Search servers"
                      value={state.query}
                      disabled={locked}
                      maxLength={128}
                      autoFocus={searchOpen && !manyServers}
                      onBlur={() => {
                        if (!state.query) setSearchOpen(false);
                      }}
                      onKeyDown={(event) => {
                        if (event.key !== 'Escape' || manyServers) return;
                        event.preventDefault();
                        session.update({ query: '' });
                        setSearchOpen(false);
                        if (state.filter) void refresh('');
                      }}
                      onChange={(event) => {
                        const query = event.target.value;
                        session.update({ query });
                        clearTimeout(searchTimer.current);
                        searchTimer.current = setTimeout(
                          () => void refresh(query),
                          350,
                        );
                      }}
                    />
                  </form>
                ) : (
                  <IconButton
                    size="sm"
                    label="Search servers"
                    disabled={locked || total === 0}
                    onClick={() => setSearchOpen(true)}
                  >
                    <Search size={15} aria-hidden />
                  </IconButton>
                )}
                <Button
                  variant="primary"
                  className="small"
                  disabled={locked}
                  onClick={openAdd}
                >
                  <Plus size={15} aria-hidden />
                  Add server
                </Button>
              </>
            }
          >
            {page.availability === 'recovery_required' && (
              <p role="status" className="settings-divided settings-mcp-empty">
                An interrupted save requires recovery before new changes.
              </p>
            )}
            {hasDraft && !dialogOpen && (
              <p className="settings-divided settings-mcp-empty">
                {editing
                  ? `Unsaved changes to ${editedName}.`
                  : 'You have an unsaved server.'}{' '}
                <Button
                  className="settings-link"
                  onClick={() => (editing ? setDialogOpen(true) : openAdd())}
                >
                  Continue
                </Button>
                {!pending && (
                  <Button
                    className="settings-link"
                    disabled={locked}
                    onClick={() =>
                      session.update({
                        draft: emptyDraft(),
                        reviewed: null,
                        message: '',
                      })
                    }
                  >
                    Discard
                  </Button>
                )}
              </p>
            )}
            {note('list')}
            {page.items
              .filter((server) => !only || server.server_id === only)
              .map((server) => (
                <ServerRow
                  key={server.server_id}
                  server={server}
                  locked={locked}
                  canSave={canSave}
                  onDetails={
                    onConnection
                      ? () => onConnection(server.server_id, server.name)
                      : undefined
                  }
                  onEdit={() => openEditor('edit', server)}
                  onRename={() => openEditor('rename', server)}
                  onRemove={() =>
                    session.confirmRemove(server.server_id, server.name)
                  }
                />
              ))}
            {page.items.length === 0 && (
              <p className="settings-divided settings-mcp-empty">
                {state.filter
                  ? 'No saved servers match.'
                  : 'Add a server to give Row-Bot new tools.'}
              </p>
            )}
            {(state.cursor || page.next_cursor) && (
              <div className="action-cluster settings-divided settings-mcp-pages">
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
              </div>
            )}
          </SettingsGroup>
          <SettingsAdvanced meta="Diagnostics">
            <section className="stack" aria-label="MCP diagnostics">
              <p>
                Saved configuration:{' '}
                {humanizeToken(page.availability).toLowerCase()}. MCP:{' '}
                {page.enabled === null
                  ? 'unknown'
                  : page.enabled
                    ? 'on'
                    : 'off'}
                . {page.total ?? 'Unknown'} saved servers; {page.items.length}{' '}
                on this page.
              </p>
              {page.items.map((server) => (
                <p key={server.server_id}>
                  {server.name}: {server.runtime_status ?? 'not started'};
                  connection{' '}
                  {server.connection_present === null
                    ? 'unknown'
                    : server.connection_present
                      ? 'present'
                      : 'absent'}
                  ; {server.tool_count ?? 'unknown'} tools.
                </p>
              ))}
            </section>
          </SettingsAdvanced>
        </>
      )}
      <ModalTask
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        title={
          draft.operation === 'edit'
            ? `Edit ${editedName}`
            : draft.operation === 'rename'
              ? `Rename ${editedName}`
              : 'Add a server'
        }
        description={
          editing
            ? 'Saving leaves it turned off until you connect it again.'
            : 'Browse a directory, fill in the details, or paste a configuration.'
        }
        className="settings-mcp-add-dialog"
      >
        {!editing && (
          <Segmented
            label="How to add a server"
            value={mode}
            onChange={chooseMode}
            options={[
              {
                value: 'manual' as const,
                label: 'Manual',
                icon: <Pencil size={14} aria-hidden />,
              },
              {
                value: 'json' as const,
                label: 'Paste JSON',
                icon: <Braces size={14} aria-hidden />,
              },
            ]}
          />
        )}
        {tab === 'json' ? (
          <fieldset
            className="stack settings-mcp-form"
            disabled={locked || !canSave}
          >
            <Field label="Server configuration (JSON)">
              <textarea
                className="input"
                rows={8}
                data-initial-focus
                placeholder={'{ "mcpServers": { … } }'}
                value={draft.imported}
                maxLength={131072}
                onChange={(event) => edit({ imported: event.target.value })}
              />
            </Field>
            <small className="settings-help">
              A standard mcpServers block. Each server in it is added turned
              off.
            </small>
            <div className="action-cluster">
              <Button
                variant="primary"
                disabled={incomplete}
                onClick={() => void requestReview()}
              >
                Add from JSON
              </Button>
            </div>
            {note('editor')}
          </fieldset>
        ) : (
          <fieldset
            className="stack settings-mcp-form"
            disabled={locked || !canSave}
          >
            {draft.operation !== 'edit' && (
              <Field
                label={
                  draft.operation === 'rename'
                    ? 'New server name'
                    : 'Server name'
                }
              >
                <Input
                  data-initial-focus
                  value={draft.name}
                  maxLength={128}
                  aria-invalid={nameInvalid || undefined}
                  onChange={(event) => edit({ name: event.target.value })}
                />
              </Field>
            )}
            {nameInvalid && (
              // The rule the server applies, said before saving (U53).
              <small role="alert" className="settings-dialog-error">
                A server name starts with a letter or number and uses letters,
                numbers, spaces and _ ( ) . - only.
              </small>
            )}
            {draft.operation !== 'rename' && (
              <>
                <Segmented
                  label="Where it runs"
                  value={draft.transport}
                  onChange={(transport) => edit({ transport })}
                  options={[
                    { value: 'stdio', label: 'This computer' },
                    { value: 'streamable_http', label: 'HTTP' },
                    { value: 'sse', label: 'SSE' },
                  ]}
                />
                <Field
                  label={
                    draft.transport === 'stdio'
                      ? draft.operation === 'edit'
                        ? 'New command'
                        : 'Command'
                      : draft.operation === 'edit'
                        ? 'New address'
                        : 'Address'
                  }
                >
                  <Input
                    data-initial-focus={draft.operation === 'edit' || undefined}
                    value={draft.launch}
                    maxLength={16384}
                    autoComplete="off"
                    placeholder={
                      draft.transport === 'stdio'
                        ? 'npx'
                        : 'https://example.com/mcp'
                    }
                    onChange={(event) => edit({ launch: event.target.value })}
                  />
                </Field>
                {draft.transport === 'stdio' && (
                  <Field label="Arguments (one per line)">
                    <textarea
                      className="input"
                      rows={3}
                      value={draft.arguments}
                      maxLength={65536}
                      autoComplete="off"
                      onChange={(event) =>
                        edit({ arguments: event.target.value })
                      }
                    />
                  </Field>
                )}
                <PairRows
                  label={
                    draft.transport === 'stdio'
                      ? 'Environment variables'
                      : 'Headers'
                  }
                  rows={draft.transport === 'stdio' ? draft.env : draft.headers}
                  onChange={(rows) =>
                    edit(
                      draft.transport === 'stdio'
                        ? { env: rows }
                        : { headers: rows },
                    )
                  }
                  hint={
                    draft.operation === 'edit'
                      ? 'Values stay hidden. Leave empty to keep the saved ones.'
                      : 'Values stay hidden; use them for keys and tokens.'
                  }
                />
                <details className="settings-mcp-preview">
                  <summary>More settings</summary>
                  <Field label="Additional settings (JSON)">
                    <textarea
                      className="input"
                      rows={2}
                      value={draft.extra}
                      maxLength={131072}
                      autoComplete="off"
                      onChange={(event) => edit({ extra: event.target.value })}
                    />
                  </Field>
                  <small className="settings-help">
                    Rarely needed: cwd, connect_timeout, tool_timeout,
                    output_limit. Omitted values stay saved.
                  </small>
                </details>
              </>
            )}
            <div className="action-cluster">
              <Button
                variant="primary"
                disabled={nameInvalid || incomplete}
                onClick={() => void requestReview()}
              >
                {draft.operation === 'add'
                  ? 'Add'
                  : draft.operation === 'rename'
                    ? 'Rename'
                    : 'Save'}
              </Button>
            </div>
            {note('editor')}
          </fieldset>
        )}
        {!editing && (
          <p className="settings-help settings-mcp-dialog-foot">
            Adding opens the server’s details next.
          </p>
        )}
      </ModalTask>
      <ModalTask
        open={state.removing !== null}
        onOpenChange={(open) => {
          if (!open) session.update({ removing: null });
        }}
        title={`Remove ${state.removing?.name ?? 'server'}?`}
        description="This deletes its saved settings and keys and stops its connection."
      >
        <div className="button-row">
          <Button onClick={() => session.update({ removing: null })}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={locked || !canSave}
            onClick={() => {
              const target = session.getSnapshot().removing;
              if (!target) return;
              session.update({ removing: null });
              void requestReview(
                { operation: 'delete', server_id: target.server_id },
                'list',
              );
            }}
          >
            Remove server
          </Button>
        </div>
      </ModalTask>
      {!page && note('page')}
    </section>
  );
}
