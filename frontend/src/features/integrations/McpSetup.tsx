import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRuntime } from '../../runtime';
import type {
  IntegrationItem,
  McpAuthStatus,
  McpConfigurationPage,
  McpTarget,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { readRetainedCommand, retainCommand } from '../../api/retained-command';
import { Button, Field, Input, Select } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import RuntimeInstallations from '../mcp/RuntimeInstallations';
import McpPolicyControls, {
  createMcpPolicySession,
} from '../settings/McpPolicyControls';
import McpCatalogAcceptance, {
  createMcpCatalogSession,
} from '../settings/McpCatalogAcceptance';
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
  const target: McpTarget = item.target ?? null;
  const server = item.owner_ref;
  const scope = `integration-mcp:${item.id}`;
  const [page, setPage] = useState<McpConfigurationPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [auth, setAuth] = useState<McpAuthStatus | null>(null);
  const [authId, setAuthId] = useState(() =>
    readRetainedCommand(scope + ':auth'),
  );
  const [pending, setPending] = useState(() => readRetainedCommand(scope));
  const [testId, setTestId] = useState(() =>
    readRetainedCommand(scope + ':test'),
  );
  const [label, setLabel] = useState(item.account_label || '');
  const [secret, setSecret] = useState('');
  const [bindingName, setBindingName] = useState('Authorization');
  const [bindingKind, setBindingKind] = useState<'header' | 'env'>('header');
  const policySession = useMemo(() => createMcpPolicySession(server), [server]);
  const catalogSession = useMemo(
    () => (testId ? createMcpCatalogSession(server, testId) : null),
    [server, testId],
  );
  const refresh = useCallback(async () => {
    setPage(
      await controller.mcpConfiguration('', undefined, undefined, target),
    );
  }, [controller, item.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    void refresh().catch((e) => setMessage(clientError(e).message));
  }, [refresh]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage('');
    try {
      await action();
    } catch (e) {
      setMessage(clientError(e).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (!authId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await controller.mcpAuthStatus(authId);
        if (!alive) return;
        setAuth(result);
        if (['starting', 'waiting'].includes(result.state))
          timer = setTimeout(() => void poll(), 2000);
        else void refresh();
      } catch (e) {
        if (alive) setMessage(clientError(e).message);
      }
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [authId, controller, refresh]);
  const remember = (id: string) => {
    retainCommand(scope, id);
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
      retainCommand(scope + ':test', pending);
    }
    if (result.status === 'completed' || result.status === 'rejected')
      remember('');
    await refresh();
  };
  const reviewAuth = async (
    mode: 'oauth' | 'api_key',
    action: 'start' | 'disconnect' = 'start',
  ) => {
    if (!page?.revision) return;
    const bindings =
      mode === 'api_key'
        ? [
            {
              kind: bindingKind,
              name: bindingName,
              key: 'token',
              prefix:
                bindingKind === 'header' &&
                bindingName.toLowerCase() === 'authorization'
                  ? ('Bearer ' as const)
                  : ('' as const),
            },
          ]
        : [];
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
        retainCommand(scope + ':auth', id);
        setAuthId(id);
        const result = await controller.executeMcpAuth({
          ...body,
          command_id: id,
          nonce: reviewed.nonce,
          values:
            mode === 'api_key' && action === 'start' ? { token: secret } : null,
        });
        setSecret('');
        setAuth(result);
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
        if (result.status === 'completed') remember('');
        if (result.mcp_runtime?.state === 'tested') {
          setTestId(id);
          retainCommand(scope + ':test', id);
          setMessage('Connection tested. Review the tools below.');
        } else
          setMessage(
            'The test did not complete. Check the retained operation before retrying.',
          );
        await refresh();
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
            executeConfiguration: (command, review) =>
              controller.executeMcpConfiguration(
                { ...command, payload: { ...command.payload, target } },
                review,
              ),
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
        if (result.status === 'completed') remember('');
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
        if (result.status === 'completed') remember('');
        setMessage(
          result.status === 'completed'
            ? 'Dependencies prepared. Test the connection.'
            : 'Preparation needs recovery.',
        );
        await refresh();
      },
    });
  };
  const remove = async () => {
    if (!page?.revision || target?.kind === 'plugin') return;
    const payload = {
      configuration_revision: page.revision,
      intent: { operation: 'delete' as const, server_id: server },
    };
    const review = await controller.reviewMcpConfiguration(payload);
    setConfirmation({
      title: 'Remove connection',
      lines: [
        'Remove this saved connection and withdraw its tools. Delete its protected credentials after stopping the connection. Shared runtimes remain installed.',
      ],
      apply: async () => {
        const id = crypto.randomUUID();
        remember(id);
        const result = await controller.executeMcpConfiguration(
          { command_id: id, type: 'mcp.configuration.save', payload },
          review,
        );
        if (result.status === 'completed') remember('');
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
        <Button disabled={busy} onClick={() => void run(inspectReceipt)}>
          Check original setup operation
        </Button>
      )}
      <fieldset disabled={busy || Boolean(pending)} className="stack">
        <legend>Setup</legend>
        <Field label="Account label">
          <Input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={128}
            placeholder="Work or Personal"
          />
        </Field>
        {summary?.transport !== 'stdio' && (
          <Button onClick={() => void run(() => reviewAuth('oauth'))}>
            Sign in
          </Button>
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
            onClick={() =>
              void run(async () => {
                setAuth(await controller.cancelMcpAuth(authId));
                setAuthId('');
                retainCommand(scope + ':auth', '');
              })
            }
          >
            Cancel or clear sign-in
          </Button>
        )}
        <details>
          <summary>API key or token</summary>
          <div className="stack">
            <Field label="Bind secret to">
              <Select
                value={bindingKind}
                onChange={(e) =>
                  setBindingKind(e.target.value as 'header' | 'env')
                }
              >
                <option value="header">HTTP header</option>
                <option value="env">Process environment variable</option>
              </Select>
            </Field>
            <Field label="Header or variable name">
              <Input
                value={bindingName}
                onChange={(e) => setBindingName(e.target.value)}
                maxLength={128}
              />
            </Field>
            <Field label="Secret">
              <Input
                type="password"
                autoComplete="off"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
              />
            </Field>
            <Button
              disabled={!secret}
              onClick={() => void run(() => reviewAuth('api_key'))}
            >
              Review secret setup
            </Button>
          </div>
        </details>
        <div className="button-row">
          <Button onClick={() => void run(test)}>Test connection</Button>
          <Button
            variant="primary"
            disabled={summary?.tool_count == null}
            onClick={() => void run(connect)}
          >
            Turn on and connect
          </Button>
        </div>
        <details>
          <summary>Runtime requirements</summary>
          <RuntimeInstallations />
          <p>
            For imported npm commands, inspect and prepare the exact package
            before testing. Bootstrap scripts and incomplete dependency locks
            are unavailable.
          </p>
          <Button onClick={() => void run(prepare)}>
            Inspect package requirements
          </Button>
        </details>
        <Button
          onClick={() => void run(() => reviewAuth('oauth', 'disconnect'))}
        >
          Disconnect account
        </Button>
        {target?.kind !== 'plugin' && (
          <Button variant="danger" onClick={() => void run(remove)}>
            Remove connection
          </Button>
        )}
      </fieldset>
      {catalogSession && (
        <McpCatalogAcceptance
          session={catalogSession}
          load={(query, signal) =>
            controller.mcpTestedCatalog(query, signal, target)
          }
          review={(body, signal) =>
            controller.reviewMcpCatalog({ ...body, target }, signal)
          }
          execute={async (command, review) => {
            const result = await controller.executeMcpConfiguration(
              { ...command, payload: { ...command.payload, target } },
              review,
            );
            await refresh();
            return result;
          }}
        />
      )}
      <details>
        <summary>Access and tools</summary>
        <McpPolicyControls
          session={policySession}
          load={(query, signal) => controller.mcpPolicy(query, signal, target)}
          review={(body, signal) =>
            controller.reviewMcpPolicy({ ...body, target }, signal)
          }
          execute={async (command, review) => {
            const result = await controller.executeMcpConfiguration(
              { ...command, payload: { ...command.payload, target } },
              review,
            );
            await refresh();
            await onChanged();
            return result;
          }}
        />
        <p>
          Agent restrictions stay in the{' '}
          <a href="/app-v2/settings/profiles">agent profile library</a>.
        </p>
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
          disabled={busy}
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
