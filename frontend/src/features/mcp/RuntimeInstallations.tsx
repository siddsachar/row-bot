import { useMemo } from 'react';
import { useRuntime } from '../../runtime';
import type { ClientController } from '../../api/controller';
import type {
  McpRuntimeInstallationSession,
  RuntimeInstallationCallbacks,
} from './McpRuntimeInstallation';
import {
  McpRuntimeInstallation,
  createMcpRuntimeInstallationSession,
} from './McpRuntimeInstallation';

export function createRuntimeInstallations() {
  const node = createMcpRuntimeInstallationSession('node');
  const uv = createMcpRuntimeInstallationSession('uv');
  return {
    node,
    uv,
    hasRetained: () => node.hasRetained() || uv.hasRetained(),
    dispose() {
      node.dispose();
      uv.dispose();
    },
  };
}

export default function RuntimeInstallations() {
  const { controller, runtimeInstallationsOwner } = useRuntime();
  const sessions = runtimeInstallationsOwner?.get();
  if (!sessions) return null;
  return (
    <details
      id="managed-mcp-runtimes"
      className="settings-supplemental-disclosure"
      open={sessions.hasRetained() || undefined}
    >
      <summary>
        <span>
          <strong>Managed runtimes</strong>
          <small>Advanced Node.js and uv installation controls</small>
        </span>
      </summary>
      <section className="stack" aria-label="Managed runtimes">
        {(['node', 'uv'] as const).map((runtime) => (
          <RuntimeInstallation
            key={runtime}
            runtime={runtime}
            session={sessions[runtime]}
            controller={controller}
          />
        ))}
      </section>
    </details>
  );
}

function RuntimeInstallation({
  runtime,
  session,
  controller,
}: {
  runtime: 'node' | 'uv';
  session: McpRuntimeInstallationSession;
  controller: ClientController;
}) {
  const callbacks = useMemo<RuntimeInstallationCallbacks>(
    () => ({
      load: (signal) => controller.runtimeInstallation(runtime, signal),
      review: (operation, source_command_id, resource_revision, signal) =>
        controller.reviewRuntimeInstallation(
          {
            runtime_id: runtime,
            operation,
            source_command_id,
            resource_revision,
          },
          signal,
        ),
      execute: controller.executeRuntimeInstallation,
      receipt: (command, signal) =>
        controller.runtimeInstallationReceipt(runtime, command, signal),
    }),
    [controller, runtime],
  );
  return <McpRuntimeInstallation session={session} callbacks={callbacks} />;
}
