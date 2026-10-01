import { useMemo, useSyncExternalStore } from 'react';
import { Download, MoreHorizontal, RefreshCw } from 'lucide-react';
import { useRuntime } from '../../runtime';
import type { ClientController } from '../../api/controller';
import { Menu, type MenuAction } from '../../ui/primitives';
import { SettingsItem } from '../settings/anatomy';
import type {
  McpRuntimeInstallationSession,
  RuntimeInstallationCallbacks,
} from './McpRuntimeInstallation';
import {
  McpRuntimeInstallation,
  RuntimeInstallationNote,
  createMcpRuntimeInstallationSession,
  describeRuntime,
  runtimeBlocked,
  runtimeName,
} from './McpRuntimeInstallation';

type Runtime = 'uv' | 'node';
const RUNTIMES: Runtime[] = ['uv', 'node'];

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

function runtimeCallbacks(
  controller: ClientController,
  runtime: Runtime,
): RuntimeInstallationCallbacks {
  return {
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
  };
}

export default function RuntimeInstallations() {
  const { controller, runtimeInstallationsOwner } = useRuntime();
  const sessions = runtimeInstallationsOwner?.get();
  if (!sessions) return null;
  return <RuntimesRow sessions={sessions} controller={controller} />;
}

/**
 * Settings › MCP › Runtimes (B262): uv and Node.js as compact status chips
 * with one Install, Retry or Cancel each. A system copy found on this
 * computer is used as is; Row-Bot's own copy is offered only on request, in
 * the row's ⋯.
 */
export function RuntimesRow({
  sessions,
  controller,
}: {
  sessions: Record<Runtime, McpRuntimeInstallationSession>;
  controller: ClientController;
}) {
  const callbacks = useMemo(
    () => ({
      uv: runtimeCallbacks(controller, 'uv'),
      node: runtimeCallbacks(controller, 'node'),
    }),
    [controller],
  );
  const states = {
    uv: useSyncExternalStore(sessions.uv.subscribe, sessions.uv.getSnapshot),
    node: useSyncExternalStore(
      sessions.node.subscribe,
      sessions.node.getSnapshot,
    ),
  };
  const actions: MenuAction[] = [
    {
      label: 'Check again',
      icon: <RefreshCw size={16} />,
      onSelect: () =>
        RUNTIMES.forEach((runtime) => sessions[runtime].refresh()),
    },
    ...RUNTIMES.filter(
      (runtime) => describeRuntime(states[runtime]).ownCopy,
    ).map((runtime) => ({
      label: `Install Row-Bot’s own ${runtimeName(runtime)}`,
      icon: <Download size={16} />,
      disabled: runtimeBlocked(states[runtime]),
      onSelect: () => void sessions[runtime].install(callbacks[runtime]),
    })),
  ];
  const notes = RUNTIMES.filter(
    (runtime) =>
      states[runtime].message || describeRuntime(states[runtime]).ownCopy,
  );
  return (
    <SettingsItem
      label="Runtimes"
      help="For servers that run on this computer."
      anchor="mcp-runtimes"
      bind={false}
      className="settings-mcp-runtimes"
      status={
        notes.length > 0 && (
          <>
            {notes.map((runtime) => (
              <RuntimeInstallationNote
                key={runtime}
                session={sessions[runtime]}
              />
            ))}
          </>
        )
      }
      control={
        <>
          {RUNTIMES.map((runtime) => (
            <McpRuntimeInstallation
              key={runtime}
              session={sessions[runtime]}
              callbacks={callbacks[runtime]}
            />
          ))}
        </>
      }
      trailing={
        <Menu
          label="More runtime actions"
          iconOnly
          variant="ghost"
          className="icon-action icon-action-sm"
          actions={actions}
        >
          <MoreHorizontal size={16} aria-hidden />
        </Menu>
      }
    />
  );
}
