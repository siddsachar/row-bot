import { describe, expect, it } from 'vitest';
import { createMcpConnections } from './McpConnections';

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
});
