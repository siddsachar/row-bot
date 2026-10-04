import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRuntime } from '../../runtime';
import type {
  IntegrationItem,
  McpAuthStatus,
  McpConfigurationPage,
  McpTarget,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { readRetainedCommand, retainCommand } from '../../api/retained-command';
import { Button, Field, Input, Toggle } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import RuntimeInstallations from '../mcp/RuntimeInstallations';
import McpPolicyControls, {
  createMcpPolicySession,
} from '../settings/McpPolicyControls';
import McpCatalogAcceptance, {
  createMcpCatalogSession,
} from '../settings/McpCatalogAcceptance';
import { useSetupOperations } from './setup-operations';
import { turnOnServer } from '../settings/mcp-add-connect';

type Confirmation = {
  title: string;
  lines: string[];
  apply: () => Promise<void>;
};

/** The existing MCP owners, scoped to one standalone connection or bundle child. */
export default function McpSetup({
  item,
  onChanged,
}: {
  item: IntegrationItem;
  onChanged: (removed?: boolean) => void | Promise<void>;
}) {
  const { controller, platform } = useRuntime();
  const changed = useRef(onChanged);
  useEffect(() => {
    changed.current = onChanged;
  }, [onChanged]);
  const target: McpTarget = item.target ?? null;
  const server = item.owner_ref;
  const scope = `integration-mcp:${item.id}`;
  const operations = useSetupOperations(item);
  const retainSetup = operations.retain;
  const [sessionScope, setSessionScope] = useState({ server });
  const [page, setPage] = useState<McpConfigurationPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [auth, setAuth] = useState<McpAuthStatus | null>(null);
  const [authId, setAuthId] = useState(() => operations.read(scope + ':auth'));
  const [pending, setPending] = useState(() => operations.read(scope));
  const [catalogReviewRequested, setCatalogReviewRequested] = useState(false);
  const [testId, setTestId] = useState(() =>
    readRetainedCommand(scope + ':test'),
  );
  const [deleteCredentials, setDeleteCredentials] = useState(false);
  const [editingAccount, setEditingAccount] = useState(false);
  const [label, setLabel] = useState(item.account_label || '');
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const setup = item.setup;
  const authPending = Boolean(
    authId &&
    (!auth ||
      ['starting', 'waiting', 'uncertain', 'expired'].includes(auth.state)),
  );
  const mode = setup?.auth_mode ?? 'unknown';
  const policySession = useMemo(
    () => createMcpPolicySession(sessionScope.server),
    [sessionScope],
  );
  const catalogSession = useMemo(
    () =>
      testId ? createMcpCatalogSession(sessionScope.server, testId) : null,
    [sessionScope, testId],
  );
  const refresh = useCallback(async () => {
    setPage(
      await controller.mcpConfiguration('', undefined, undefined, target),
    );
  }, [controller, item.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    void refresh().catch((e) => setMessage(clientError(e).message));
  }, [refresh]);
  const run = async (action: () => Promise<void>, recovery = false) => {
    setBusy(true);
    setMessage('');
    try {
      await operations.run(action, recovery);
    } catch (e) {
      setMessage(clientError(e).message);
    } finally {
      setBusy(false);
    }
  };
  const receiveAuth = useCallback(
    (result: McpAuthStatus) => {
      setAuth(result);
      if (
        ['signed_in', 'disconnected', 'cancelled', 'failed'].includes(
          result.state,
        )
      ) {
        retainSetup(scope + ':auth', '');
        setAuthId('');
      }
    },
    [retainSetup, scope],
  );
  useEffect(() => {
    if (!authId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await controller.mcpAuthStatus(authId);
        if (!alive) return;
        receiveAuth(result);
        if (['starting', 'waiting'].includes(result.state))
          timer = setTimeout(() => void poll(), 2000);
        else
          void Promise.all([refresh(), changed.current()]).catch((e) => {
            if (alive) setMessage(clientError(e).message);
          });
      } catch (e) {
        if (alive) setMessage(clientError(e).message);
      }
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [authId, controller, refresh, receiveAuth]);
  const remember = (id: string) => {
    operations.retain(scope, id);
    setPending(id);
  };
  const inspectReceipt = async () => {
    const checked = await controller.reconcileIntegrationOperation(
      'mcp',
      pending,
    );
    const result = await controller.receipt(pending);
    setMessage(checked.message);
    if (result.mcp_runtime?.state === 'tested') {
      setTestId(pending);
      setCatalogReviewRequested(true);
      retainCommand(scope + ':test', pending);
    }
    if (result.status === 'completed' || result.status === 'rejected') {
      remember('');
      setSessionScope({ server });
    }
    await refresh();
    await onChanged();
  };
  const reviewAuth = async (
    mode: 'oauth' | 'api_key',
    action: 'start' | 'disconnect' = 'start',
  ) => {
    if (!page?.revision) return;
    const bindings = mode === 'api_key' ? (setup?.bindings ?? []) : [];
    const body = {
      server_id: server,
      configuration_revision: page.revision,
      target,
      mode,
      action,
      label,
      bindings,
    };
    const reviewed = await controller.reviewMcpAuth(body);
    setConfirmation({
      title:
        action === 'disconnect'
          ? 'Disconnect account'
          : mode === 'oauth'
            ? 'Sign in'
            : 'Save secret',
      lines: reviewed.disclosures,
      apply: async () => {
        const id = crypto.randomUUID();
        operations.retain(scope + ':auth', id);
        setAuthId(id);
        const result = await controller.executeMcpAuth({
          ...body,
          command_id: id,
          nonce: reviewed.nonce,
          values: mode === 'api_key' && action === 'start' ? secrets : null,
        });
        setSecrets({});
        setEditingAccount(false);
        receiveAuth(result);
        setMessage(result.message);
        await refresh();
        await onChanged();
      },
    });
  };
  const test = async () => {
    const current = await controller.mcpRuntime(server, undefined, target);
    if (!current.configuration_revision)
      throw new Error('Connection settings are unavailable.');
    const payload = {
      resource_revision: current.configuration_revision,
      server_id: server,
      operation: 'test' as const,
      expected_runtime_id: null,
      target,
    };
    const review = await controller.reviewMcpRuntime(payload);
    setConfirmation({
      title: 'Test connection',
      lines: [
        'Start this connection to inspect the tools it offers. The test keeps the connection and its parent off. No tools are invoked.',
      ],
      apply: async () => {
        const id = crypto.randomUUID();
        remember(id);
        const result = await controller.executeMcpRuntime(
          { command_id: id, type: 'mcp.runtime.control', payload },
          review,
        );
        if (result.status === 'completed' || result.status === 'rejected')
          remember('');
        if (result.mcp_runtime?.state === 'tested') {
          setTestId(id);
          setCatalogReviewRequested(true);
          retainCommand(scope + ':test', id);
          setMessage('Connection tested. Review the tools below.');
        } else
          setMessage(
            'The test did not complete. Check the retained operation before retrying.',
          );
        await refresh();
        await onChanged();
      },
    });
  };
  const connect = async () => {
    const policy = await controller.mcpPolicy(
      { server_id: server, query: '' },
      undefined,
      target,
    );
    setConfirmation({
      title: 'Turn on and connect',
      lines: [
        target?.kind === 'plugin'
          ? 'Allow this child connection. Its parent package must be on before it can be used.'
          : policy.global_enabled
            ? 'Allow this saved connection and its accepted tools.'
            : 'Turn on MCP globally and this connection. Other connections already marked on may become available.',
        'Existing tool approval and agent profile restrictions still apply.',
      ],
      apply: async () => {
        await turnOnServer(
          {
            policy: (query) => controller.mcpPolicy(query, undefined, target),
            reviewPolicy: (body) =>
              controller.reviewMcpPolicy({ ...body, target }),
            executeConfiguration: async (command, review) => {
              remember(command.command_id);
              const result = await controller.executeMcpConfiguration(
                { ...command, payload: { ...command.payload, target } },
                review,
              );
              if (result.status === 'completed' || result.status === 'rejected')
                remember('');
              return result;
            },
          },
          server,
          target?.kind === 'plugin',
        );
        const current = await controller.mcpRuntime(server, undefined, target);
        if (!current.configuration_revision)
          throw new Error('Reload this connection.');
        const payload = {
          resource_revision: current.configuration_revision,
          server_id: server,
          operation: 'connect' as const,
          expected_runtime_id: null,
          target,
        };
        const reviewed = await controller.reviewMcpRuntime(payload);
        const id = crypto.randomUUID();
        remember(id);
        const result = await controller.executeMcpRuntime(
          { command_id: id, type: 'mcp.runtime.control', payload },
          reviewed,
        );
        if (result.status === 'completed' || result.status === 'rejected')
          remember('');
        setMessage(
          result.mcp_runtime?.state === 'connected'
            ? 'Connected.'
            : 'Connection needs attention. Check the original operation.',
        );
        await refresh();
        await onChanged();
      },
    });
  };
  const prepare = async () => {
    if (!page?.revision) return;
    const body = {
      server_id: server,
      configuration_revision: page.revision,
      target,
    };
    const review = await controller.previewMcpPackage(body);
    setConfirmation({
      title: `Prepare ${review.name} ${review.version}`,
      lines: [
        ...review.disclosures,
        `${review.dependencies} pinned dependencies; ${review.license || 'license not declared'}.`,
        review.integrity,
      ],
      apply: async () => {
        const id = crypto.randomUUID();
        remember(id);
        const result = await controller.executeMcpPackage({
          ...body,
          command_id: id,
          preview_id: review.preview_id,
          digest: review.digest,
          nonce: review.nonce,
        });
        if (result.status === 'completed' || result.status === 'rejected')
          remember('');
        setMessage(
          result.status === 'completed'
            ? 'Dependencies prepared. Test the connection.'
            : 'Preparation needs recovery.',
        );
        await refresh();
        await onChanged();
      },
    });
  };
  const remove = async () => {
    if (!page?.revision || target?.kind === 'plugin') return;
    const payload = {
      configuration_revision: page.revision,
      intent: {
        operation: 'delete' as const,
        server_id: server,
        delete_credentials: deleteCredentials,
      },
    };
    const review = await controller.reviewMcpConfiguration(payload);
    setConfirmation({
      title: 'Remove connection',
      lines: [
        `Remove ${item.name}${item.account_label ? ` (${item.account_label})` : ''} and withdraw its tools. Shared runtimes, other connections and service data remain.`,
        deleteCredentials
          ? 'Explicit cleanup: clear only this connection’s protected credentials after stopping it. Remote revocation is not verified.'
          : 'Protected credentials remain in local secure storage. To clear them, cancel and choose credential cleanup, or disconnect the account first.',
        'Running-operation cleanup follows the connection owner. Incomplete cleanup retains the original receipt for recovery; work is never replayed.',
      ],
      apply: async () => {
        const id = crypto.randomUUID();
        remember(id);
        const result = await controller.executeMcpConfiguration(
          { command_id: id, type: 'mcp.configuration.save', payload },
          review,
        );
        if (result.status === 'completed' || result.status === 'rejected')
          remember('');
        await onChanged(result.status === 'completed');
      },
    });
  };
  const summary = page?.items.find((row) => row.server_id === server);
  return (
    <section
      className="stack"
      aria-label={`Set up ${item.name}`}
      aria-busy={busy}
    >
      {message && <p role="status">{message}</p>}
      {pending && (
        <Button
          disabled={operations.busy}
          onClick={() => void run(inspectReceipt, true)}
        >
          Check original setup operation
        </Button>
      )}
      {(pending || authId) && (
        <details>
          <summary>Recovery details</summary>
          <p>
            Original operation reference: <code>{pending || authId}</code>.
            Check this operation before retrying; an unavailable result never
            authorizes replay.
          </p>
        </details>
      )}
      {auth && <p role="status">{auth.message}</p>}
      {auth?.authorization_url && (
        <Button
          onClick={() => void platform.openExternal(auth.authorization_url!)}
        >
          Continue sign-in in browser
        </Button>
      )}
      {authId && (
        <Button
          disabled={operations.busy}
          onClick={() =>
            void run(async () => {
              receiveAuth(await controller.cancelMcpAuth(authId));
              await onChanged();
            }, true)
          }
        >
          Cancel or clear sign-in
        </Button>
      )}
      {authId && (
        <Button
          disabled={operations.busy}
          onClick={() =>
            void run(async () => {
              receiveAuth(await controller.mcpAuthStatus(authId));
              await refresh();
              await onChanged();
            }, true)
          }
        >
          Check original sign-in
        </Button>
      )}
      <p>
        Account:{' '}
        {item.account_label ||
          (mode === 'none'
            ? 'Not required for this connection'
            : 'Not connected')}
        . Profile access is checked when you choose a chat profile; setup does
        not change that profile.
      </p>
      <ol aria-label="Setup progress">
        <li>Review destination and requirements</li>
        <li>
          {setup?.execution === 'local'
            ? 'Prepare local runtime'
            : mode === 'none'
              ? 'No account setup needed'
              : mode === 'unknown' || mode === 'unsupported'
                ? 'Review authentication requirements'
                : 'Connect account'}
        </li>
        <li>Test and choose access</li>
        <li>Turn on and connect</li>
      </ol>
      <fieldset
        disabled={
          operations.blocked || page?.availability === 'recovery_required'
        }
        className="stack"
      >
        <legend>
          {setup?.execution === 'local'
            ? 'Prepare local tools'
            : 'Connect service'}
        </legend>
        <p>
          Destination:{' '}
          {setup?.destination ||
            'Read saved configuration to establish the destination.'}
        </p>
        <p>{setup?.account_requirements}</p>
        <p>{setup?.cost}</p>
        {mode === 'none' && (
          <p>No account fields are required for this connection.</p>
        )}
        {['unknown', 'unsupported'].includes(mode) && (
          <p role="status">
            {mode === 'unsupported'
              ? 'This authentication method is unsupported.'
              : 'Authentication requirements are unknown.'}{' '}
            Review publisher instructions in advanced configuration before
            providing credentials. A connection test may discover public tools;
            it does not establish OAuth support.
          </p>
        )}
        {['oauth', 'api_key'].includes(mode) && (
          <Field label="Account label">
            <Input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              maxLength={128}
              placeholder="Work or Personal"
            />
          </Field>
        )}
        {mode === 'oauth' &&
          (!setup?.credential_configured || editingAccount) && (
            <Button
              disabled={authPending}
              onClick={() => void run(() => reviewAuth('oauth'))}
            >
              Sign in
            </Button>
          )}
        {mode === 'api_key' &&
          (!setup?.credential_configured || editingAccount) && (
            <div className="stack">
              <p>
                Secrets are protected and bound only to this connection and
                destination. Work and Personal connections do not share
                credentials.
              </p>
              {setup?.bindings.map((binding) => (
                <Field
                  key={binding.key}
                  label={`${binding.name} (${binding.kind})`}
                >
                  <Input
                    type="password"
                    autoComplete="off"
                    value={secrets[binding.key] ?? ''}
                    onChange={(e) =>
                      setSecrets({ ...secrets, [binding.key]: e.target.value })
                    }
                  />
                </Field>
              ))}
              {!setup?.bindings.length ? (
                <p>
                  Required secret bindings are not declared. Review the
                  publisher configuration in the advanced editor.
                </p>
              ) : (
                <Button
                  disabled={
                    authPending ||
                    setup.bindings.some((binding) => !secrets[binding.key])
                  }
                  onClick={() => void run(() => reviewAuth('api_key'))}
                >
                  Review secret setup
                </Button>
              )}
            </div>
          )}
        {setup?.execution === 'local' && (
          <section className="stack" aria-label="Local runtime requirements">
            <p>
              Local execution uses the saved command, paths and environment
              reviewed during import. Tests execute that process; no tools are
              invoked. Review package dependencies before execution.
            </p>
            <ul>
              {summary?.requirements?.map((requirement) => (
                <li key={requirement.id}>
                  {requirement.label}:{' '}
                  {requirement.available
                    ? 'Available'
                    : requirement.installable
                      ? 'Installation required'
                      : 'Unavailable; manual setup required'}
                </li>
              ))}
            </ul>
            {summary?.requirements?.some(
              (r) => !r.available && r.installable,
            ) && <RuntimeInstallations />}
            {setup.package_required && (
              <Button onClick={() => void run(prepare)}>
                Inspect package requirements
              </Button>
            )}
            <p>
              Bootstrap scripts and incomplete dependency locks are unavailable.
              Inspect dependencies, integrity and disclosures before confirming
              preparation.
            </p>
          </section>
        )}
        {(!setup?.catalog_accepted || item.status === 'attention') && (
          <Button
            disabled={
              mode === 'unsupported' ||
              (['oauth', 'api_key'].includes(mode) &&
                !setup?.credential_configured) ||
              Boolean(setup?.package_required) ||
              Boolean(summary?.requirements?.some((r) => !r.available))
            }
            onClick={() => void run(test)}
          >
            {setup?.catalog_accepted
              ? 'Test connection again'
              : 'Test connection'}
          </Button>
        )}
        {setup?.catalog_accepted && item.status !== 'attention' && (
          <Button
            variant="primary"
            disabled={
              catalogReviewRequested ||
              item.status === 'ready' ||
              mode === 'unsupported' ||
              Boolean(setup?.package_required) ||
              Boolean(summary?.requirements?.some((r) => !r.available)) ||
              (['oauth', 'api_key'].includes(mode) &&
                !setup?.credential_configured)
            }
            onClick={() => void run(connect)}
          >
            Turn on and connect
          </Button>
        )}
      </fieldset>
      {catalogSession &&
        (!setup?.catalog_accepted || catalogReviewRequested) && (
          <McpCatalogAcceptance
            session={catalogSession}
            mutationsDisabled={operations.blocked}
            load={(query, signal) =>
              controller.mcpTestedCatalog(query, signal, target)
            }
            review={(body, signal) =>
              operations.run(() =>
                controller.reviewMcpCatalog({ ...body, target }, signal),
              )
            }
            execute={(command, review) =>
              operations.run(async () => {
                remember(command.command_id);
                const result = await controller.executeMcpConfiguration(
                  { ...command, payload: { ...command.payload, target } },
                  review,
                );
                if (
                  result.status === 'completed' ||
                  result.status === 'rejected'
                )
                  remember('');
                if (result.status === 'completed') {
                  setCatalogReviewRequested(false);
                  setMessage(
                    'Tools accepted. Choose access before connecting.',
                  );
                }
                await refresh();
                await onChanged();
                return result;
              })
            }
          />
        )}
      {(setup?.catalog_accepted || catalogSession) && (
        <section className="stack" aria-label="Choose access">
          <h3>Choose access</h3>
          <p>
            These switches control Row-Bot tool exposure. They do not change the
            grants issued by the upstream service. Tool descriptions and
            annotations are advisory; existing approval policy and profile
            restrictions remain in force.
          </p>
          <McpPolicyControls
            session={policySession}
            mutationsDisabled={operations.blocked}
            load={(query, signal) =>
              controller.mcpPolicy(query, signal, target)
            }
            review={(body, signal) =>
              operations.run(() =>
                controller.reviewMcpPolicy({ ...body, target }, signal),
              )
            }
            execute={(command, review) =>
              operations.run(async () => {
                remember(command.command_id);
                const result = await controller.executeMcpConfiguration(
                  { ...command, payload: { ...command.payload, target } },
                  review,
                );
                if (
                  result.status === 'completed' ||
                  result.status === 'rejected'
                )
                  remember('');
                await refresh();
                await onChanged();
                return result;
              })
            }
          />
          <p>
            Agent restrictions stay in the{' '}
            <a href="/app-v2/settings/profiles">agent profile library</a>.
          </p>
        </section>
      )}
      {setup?.credential_configured && ['oauth', 'api_key'].includes(mode) && (
        <Button
          disabled={operations.blocked}
          onClick={() => setEditingAccount((value) => !value)}
        >
          {editingAccount
            ? 'Cancel account edit'
            : 'Change account or credential'}
        </Button>
      )}
      {setup?.execution === 'hosted' && (
        <p>
          Check the hosted tool catalog explicitly with Test connection again.
          Changed tools require renewed acceptance. Row-Bot cannot roll back a
          vendor's remote deployment or its upstream grants.
        </p>
      )}
      <p>
        Turning Server access off stops tool exposure and keeps configuration,
        permissions and credentials. Already running work follows the runtime
        owner; this switch does not replay or silently cancel it.
      </p>
      <details>
        <summary>Advanced connection actions</summary>
        {item.status !== 'attention' && (
          <Button onClick={() => void run(test)} disabled={operations.blocked}>
            Test connection again
          </Button>
        )}
        {setup?.credential_configured && (
          <Button
            disabled={operations.blocked}
            onClick={() =>
              void run(() =>
                reviewAuth(
                  mode === 'api_key' ? 'api_key' : 'oauth',
                  'disconnect',
                ),
              )
            }
          >
            Disconnect account
          </Button>
        )}
        {target?.kind !== 'plugin' && (
          <Toggle
            label="Also clear this connection’s protected credentials when removing"
            checked={deleteCredentials}
            disabled={operations.blocked}
            onChange={(event) => setDeleteCredentials(event.target.checked)}
          />
        )}
        {target?.kind !== 'plugin' && (
          <Button
            disabled={operations.blocked}
            variant="danger"
            onClick={() => void run(remove)}
          >
            Remove connection
          </Button>
        )}
      </details>
      <ModalTask
        description="Review the effects for this connection."
        open={Boolean(confirmation)}
        onOpenChange={(open) => {
          if (!open && !busy) setConfirmation(null);
        }}
        title={confirmation?.title ?? 'Review setup'}
        ariaLabel="Review connection setup"
      >
        <ul>
          {confirmation?.lines.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
        <Button
          disabled={operations.blocked}
          variant="primary"
          onClick={() =>
            void run(async () => {
              const action = confirmation;
              setConfirmation(null);
              await action?.apply();
            })
          }
        >
          Confirm
        </Button>
      </ModalTask>
    </section>
  );
}
