import type { McpConfigurationReceipt } from './CapabilitySettings';
import type {
  McpCatalogReview,
  McpTestedCatalogPage,
} from './McpCatalogAcceptance';
import type { McpPolicyIntent, McpPolicyPage } from './McpPolicyControls';
import type {
  McpRuntimeCommand,
  McpRuntimeReceipt,
  McpRuntimeReview,
  McpRuntimeState,
} from './McpRuntimeControls';

/** The reviewed MCP owners "Add and connect" chains (U53). */
export type AddConnectApi = {
  runtime: (serverId: string) => Promise<McpRuntimeState>;
  reviewRuntime: (
    payload: McpRuntimeCommand['payload'],
  ) => Promise<McpRuntimeReview>;
  executeRuntime: (
    command: McpRuntimeCommand,
    review: McpRuntimeReview,
  ) => Promise<McpRuntimeReceipt>;
  catalog: (query: {
    server_id: string;
    test_command_id: string;
    query: string;
  }) => Promise<McpTestedCatalogPage>;
  reviewCatalog: (body: {
    configuration_revision: string;
    server_id: string;
    test_command_id: string;
  }) => Promise<McpCatalogReview>;
  policy: (query: {
    server_id: string | null;
    query: string;
  }) => Promise<McpPolicyPage>;
  reviewPolicy: (body: {
    configuration_revision: string;
    intent: McpPolicyIntent;
  }) => Promise<{ configuration_revision: string; nonce?: string }>;
  executeConfiguration: (
    command:
      | {
          command_id: string;
          type: 'mcp.catalog.accept';
          payload: {
            configuration_revision: string;
            server_id: string;
            test_command_id: string;
          };
        }
      | {
          command_id: string;
          type: 'mcp.configuration.control';
          payload: { configuration_revision: string; intent: McpPolicyIntent };
        },
    review: { nonce?: string },
  ) => Promise<McpConfigurationReceipt>;
};

export type AddConnectStep = 'test' | 'accept' | 'enable' | 'connect';

/** Where "Add and connect" stopped, and why, in words. */
export class AddConnectStopped extends Error {
  constructor(
    readonly step: AddConnectStep,
    message: string,
  ) {
    super(message);
  }
}

async function runtimeStep(
  api: AddConnectApi,
  serverId: string,
  operation: 'test' | 'connect',
) {
  const state = await api.runtime(serverId);
  if (!state.configuration_revision)
    throw new AddConnectStopped(operation, 'The saved server changed.');
  const command: McpRuntimeCommand = {
    command_id: crypto.randomUUID(),
    type: 'mcp.runtime.control',
    payload: {
      resource_revision: state.configuration_revision,
      server_id: serverId,
      operation,
      expected_runtime_id: null,
    },
  };
  const review = await api.reviewRuntime(command.payload);
  const receipt = await api.executeRuntime(command, review);
  return { command, receipt };
}

/** The saved-permission owners that turn MCP and a server on. */
export type TurnOnApi = Pick<
  AddConnectApi,
  'policy' | 'reviewPolicy' | 'executeConfiguration'
>;

async function policyChange(api: TurnOnApi, intent: McpPolicyIntent) {
  const page = await api.policy({
    server_id: 'server_id' in intent ? intent.server_id : null,
    query: '',
  });
  if (!page.revision)
    throw new AddConnectStopped('enable', 'MCP settings are unavailable.');
  const review = await api.reviewPolicy({
    configuration_revision: page.revision,
    intent,
  });
  const receipt = await api.executeConfiguration(
    {
      command_id: crypto.randomUUID(),
      type: 'mcp.configuration.control',
      payload: { configuration_revision: page.revision, intent },
    },
    review,
  );
  if (receipt.status !== 'completed')
    throw new AddConnectStopped('enable', 'Row-Bot couldn’t turn it on.');
}

/**
 * Turns MCP and one server on, each the same reviewed change as its switch;
 * "Add and connect" and "Turn on & connect" (B262) both use it.
 */
export async function turnOnServer(api: TurnOnApi, serverId: string) {
  const policy = await api.policy({ server_id: serverId, query: '' });
  if (policy.global_enabled !== true)
    await policyChange(api, { operation: 'global_enabled', enabled: true });
  if (policy.server_enabled !== true)
    await policyChange(api, {
      operation: 'server_enabled',
      server_id: serverId,
      enabled: true,
    });
}

/**
 * "Add and connect" for an MCP server just saved (U53): Test it, accept the
 * tools it offers (each keeps the approval it needs, so destructive tools
 * still ask), turn MCP and the server on, then connect. Every step is the
 * same reviewed action as its own button below; it stops at the first step
 * that needs the person, and the server stays saved.
 */
export async function addAndConnect(
  api: AddConnectApi,
  serverId: string,
  onStep: (step: AddConnectStep) => void,
): Promise<{ tools: number }> {
  onStep('test');
  const tested = await runtimeStep(api, serverId, 'test');
  if (
    tested.receipt.status !== 'completed' ||
    tested.receipt.mcp_runtime?.state !== 'tested'
  )
    throw new AddConnectStopped(
      'test',
      tested.receipt.mcp_runtime?.code === 'mcp_connection_failed'
        ? 'The server didn’t start or answer its test.'
        : 'The test didn’t finish yet.',
    );
  onStep('accept');
  const catalog = await api.catalog({
    server_id: serverId,
    test_command_id: tested.command.command_id,
    query: '',
  });
  if (catalog.manual_selection_required || !catalog.configuration_revision)
    throw new AddConnectStopped(
      'accept',
      'Choose which of its tools to allow below.',
    );
  const body = {
    configuration_revision: catalog.configuration_revision,
    server_id: serverId,
    test_command_id: tested.command.command_id,
  };
  const reviewed = await api.reviewCatalog(body);
  const accepted = await api.executeConfiguration(
    {
      command_id: crypto.randomUUID(),
      type: 'mcp.catalog.accept',
      payload: body,
    },
    reviewed,
  );
  if (accepted.status !== 'completed')
    throw new AddConnectStopped('accept', 'Row-Bot couldn’t accept its tools.');
  onStep('enable');
  await turnOnServer(api, serverId);
  onStep('connect');
  const connected = await runtimeStep(api, serverId, 'connect');
  if (connected.receipt.mcp_runtime?.state !== 'connected')
    throw new AddConnectStopped('connect', 'It didn’t connect yet.');
  return { tools: catalog.total ?? catalog.items.length };
}
