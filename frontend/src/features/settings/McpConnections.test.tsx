import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import McpConnectionsPanel, { createMcpConnections } from './McpConnections';
import type { McpPolicyPage } from './McpPolicyControls';
import type { McpRuntimeState } from './McpRuntimeControls';

describe('authenticated MCP catalog ownership', () => {
  it('retains exact pending Test catalog across server navigation and prevents capacity eviction', () => {
    const owner = createMcpConnections(1);
    const server = 'a'.repeat(64),
      another = 'b'.repeat(64);
    owner.select(server, 'First');
    const catalog = owner.catalog(server, 'original-test')!;
    catalog.update({ query: 'retained review filter' });
    owner.select(another, 'Second');
    expect(owner.getSnapshot().selected).toBe(server);
    expect(owner.catalog(server, 'new-test')).toBeNull();
    expect(owner.catalog(server, 'original-test')).toBe(catalog);
    expect(owner.hasRetained()).toBe(true);
    owner.dispose();
    expect(catalog.getSnapshot().active).toBe(false);
    expect(catalog.getSnapshot().query).toBe('');
    expect(owner.hasRetained()).toBe(false);
  });

  it('replaces a settled catalog with the exact later Test without accumulating views', () => {
    const owner = createMcpConnections(1),
      server = 'a'.repeat(64);
    owner.select(server, 'First');
    const original = owner.catalog(server, 'first-test')!;
    const next = owner.catalog(server, 'second-test')!;
    expect(original.getSnapshot().active).toBe(false);
    expect(next.testCommandId).toBe('second-test');
    expect(owner.catalogs(server)).toEqual([next]);
  });

  it('gives a row its connection session without opening the details, and never evicts the open one', () => {
    const owner = createMcpConnections(1);
    const open = 'a'.repeat(64),
      row = 'b'.repeat(64);
    owner.select(open, 'Open');
    // The open server's session stays: a row's command waits instead.
    expect(owner.runtime(row, 'Row')).toBeNull();
    owner.close();
    expect(owner.getSnapshot().selected).toBe('');
    const session = owner.runtime(row, 'Row')!;
    expect(owner.peek(row)).toBe(session);
    expect(owner.getSnapshot().selected).toBe('');
  });
});

const serverId = 'c'.repeat(64);
const runtimeState: McpRuntimeState = {
  schema_version: 1,
  server_id: serverId,
  configuration_revision: 'd'.repeat(64),
  cleanup_revision: null,
  availability: 'available',
  runtime_id: null,
  state: 'missing',
  session_quiesced: null,
  enabled: true,
};
const policyPage: McpPolicyPage = {
  schema_version: 1,
  revision: 'e'.repeat(64),
  server_id: serverId,
  availability: 'available',
  global_enabled: true,
  server_enabled: true,
  resources_enabled: false,
  prompts_enabled: false,
  total: 0,
  next_cursor: null,
  items: [],
};

function panel(onRemove = vi.fn()) {
  const owner = createMcpConnections();
  render(
    <McpConnectionsPanel
      owner={owner}
      load={vi.fn().mockResolvedValue(runtimeState)}
      review={vi.fn()}
      execute={vi.fn()}
      policy={{
        load: vi.fn().mockResolvedValue(policyPage),
        review: vi.fn(),
        execute: vi.fn(),
      }}
      onRemove={onRemove}
    />,
  );
  return { owner, onRemove };
}

describe('a server’s details drawer (B262)', () => {
  it('opens for the chosen server with its connection, tools and permissions', async () => {
    const { owner } = panel();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    act(() => owner.select(serverId, 'context7'));
    const drawer = screen.getByRole('dialog', { name: 'context7' });
    expect(
      within(drawer).getByRole('region', { name: 'Connection' }),
    ).toBeVisible();
    expect(
      await within(drawer).findByRole('button', { name: 'Connect' }),
    ).toBeVisible();
    expect(within(drawer).getByRole('button', { name: 'Test' })).toBeVisible();
    expect(
      await within(drawer).findByRole('switch', { name: 'Server access' }),
    ).toBeChecked();
    expect(within(drawer).getByText('Tools')).toBeVisible();
  });

  it('asks to remove the server from its Danger zone, and closes on request', async () => {
    const { owner, onRemove } = panel();
    act(() => owner.select(serverId, 'context7'));
    const drawer = screen.getByRole('dialog', { name: 'context7' });
    fireEvent.click(within(drawer).getByText('Danger zone'));
    fireEvent.click(
      within(drawer).getByRole('button', { name: 'Remove server…' }),
    );
    expect(onRemove).toHaveBeenCalledWith(serverId, 'context7');
    fireEvent.click(
      within(drawer).getByRole('button', { name: 'Close server details' }),
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(owner.getSnapshot().selected).toBe('');
    // The server's retained sessions stay for its row.
    expect(owner.peek(serverId)).toBeDefined();
  });
});
