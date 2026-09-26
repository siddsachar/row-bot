import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import KnowledgeList, { sourceWords } from './KnowledgeList';
import type { KnowledgeGraphNode } from './KnowledgeHome';

const revision = 'b'.repeat(64);

function node(
  id: string,
  overrides: Partial<KnowledgeGraphNode> = {},
): KnowledgeGraphNode {
  return {
    id,
    revision,
    subject: id,
    description: '',
    entity_type: 'fact',
    source: 'manual',
    updated_at: '2026-09-01T10:00:00Z',
    relation_count: 0,
    orphan: true,
    is_user: false,
    ...overrides,
  };
}

const nodes: KnowledgeGraphNode[] = [
  node('beta', {
    subject: 'beta release',
    entity_type: 'project',
    source: 'extraction',
    updated_at: '2026-09-20T10:00:00Z',
    relation_count: 2,
  }),
  node('alpha', {
    subject: 'Alpha project',
    entity_type: 'fact',
    source: 'manual',
    updated_at: '2026-09-10T10:00:00Z',
    relation_count: 5,
  }),
  node('carol', {
    subject: 'Carol',
    entity_type: 'person',
    source: 'document',
    updated_at: '2026-08-01T10:00:00Z',
    relation_count: 2,
  }),
];

function table() {
  return screen.getByRole('table', { name: 'Knowledge entities' });
}

/** Memory names in rendered row order (header and spacer rows excluded). */
function listedSubjects() {
  const [, body] = within(table()).getAllByRole('rowgroup');
  return within(body)
    .queryAllByRole('button')
    .map((button) => button.textContent);
}

function header(name: string) {
  return within(table()).getByRole('columnheader', { name });
}

describe('KnowledgeList', () => {
  it('lists every memory with readable type, source, links, and time', () => {
    render(
      <KnowledgeList nodes={nodes} selectedId={null} onSelect={vi.fn()} />,
    );
    const row = screen
      .getByRole('button', { name: 'beta release' })
      .closest('tr') as HTMLElement;
    const cells = within(row).getAllByRole('cell');
    expect(cells.map((cell) => cell.textContent?.trim())).toEqual([
      'beta release',
      'Project',
      'From conversations',
      '2',
      expect.any(String),
    ]);
    const time = within(cells[4]).getByText(
      (_, element) => element?.tagName === 'TIME',
    );
    expect(time).toHaveAttribute('datetime', '2026-09-20T10:00:00.000Z');
    expect(time.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}T/);
    expect(table()).toHaveAttribute('aria-rowcount', '4');
  });

  it('sorts by links, most connected first, until a column header is chosen', async () => {
    const user = userEvent.setup();
    render(
      <KnowledgeList nodes={nodes} selectedId={null} onSelect={vi.fn()} />,
    );
    // Equal link counts fall back to a stable id order.
    expect(listedSubjects()).toEqual([
      'Alpha project',
      'beta release',
      'Carol',
    ]);
    expect(header('Links')).toHaveAttribute('aria-sort', 'descending');
    expect(header('Memory')).toHaveAttribute('aria-sort', 'none');

    await user.click(within(table()).getByRole('button', { name: 'Memory' }));
    expect(header('Memory')).toHaveAttribute('aria-sort', 'ascending');
    expect(header('Links')).toHaveAttribute('aria-sort', 'none');
    // Case-insensitive, so "beta release" sits between Alpha and Carol.
    expect(listedSubjects()).toEqual([
      'Alpha project',
      'beta release',
      'Carol',
    ]);

    await user.click(within(table()).getByRole('button', { name: 'Memory' }));
    expect(header('Memory')).toHaveAttribute('aria-sort', 'descending');
    expect(listedSubjects()).toEqual([
      'Carol',
      'beta release',
      'Alpha project',
    ]);

    await user.click(within(table()).getByRole('button', { name: 'Updated' }));
    expect(header('Updated')).toHaveAttribute('aria-sort', 'descending');
    expect(listedSubjects()).toEqual([
      'beta release',
      'Alpha project',
      'Carol',
    ]);

    await user.click(within(table()).getByRole('button', { name: 'Type' }));
    expect(header('Type')).toHaveAttribute('aria-sort', 'ascending');
    expect(listedSubjects()).toEqual([
      'Alpha project',
      'Carol',
      'beta release',
    ]);

    await user.click(within(table()).getByRole('button', { name: 'Source' }));
    expect(header('Source')).toHaveAttribute('aria-sort', 'ascending');
    expect(listedSubjects()).toEqual([
      'Carol',
      'beta release',
      'Alpha project',
    ]);

    await user.click(within(table()).getByRole('button', { name: 'Links' }));
    expect(header('Links')).toHaveAttribute('aria-sort', 'descending');
    await user.click(within(table()).getByRole('button', { name: 'Links' }));
    expect(header('Links')).toHaveAttribute('aria-sort', 'ascending');
    expect(listedSubjects()).toEqual([
      'beta release',
      'Carol',
      'Alpha project',
    ]);
  });

  it('selects a memory by keyboard, row button, or row click, once each', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <KnowledgeList nodes={nodes} selectedId={null} onSelect={onSelect} />,
    );
    screen.getByRole('button', { name: 'Carol' }).focus();
    await user.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledExactlyOnceWith('carol');

    onSelect.mockClear();
    await user.click(screen.getByRole('button', { name: 'Alpha project' }));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith('alpha');

    onSelect.mockClear();
    const row = screen
      .getByRole('button', { name: 'beta release' })
      .closest('tr') as HTMLElement;
    await user.click(within(row).getByText('From conversations'));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith('beta');
  });

  it('marks the selected memory without marking the others', () => {
    render(
      <KnowledgeList nodes={nodes} selectedId="carol" onSelect={vi.fn()} />,
    );
    const selected = screen.getByRole('button', { name: 'Carol' });
    expect(selected).toHaveAttribute('aria-current', 'true');
    expect(selected.closest('tr')).toHaveAttribute('data-selected', 'true');
    const other = screen.getByRole('button', { name: 'Alpha project' });
    expect(other).not.toHaveAttribute('aria-current');
    expect(other.closest('tr')).not.toHaveAttribute('data-selected');
  });

  it('renders only the rows in view and keeps the scroll height honest', () => {
    const many = Array.from({ length: 200 }, (_, index) =>
      node(`m${String(index).padStart(3, '0')}`, {
        subject: `Memory ${String(index).padStart(3, '0')}`,
      }),
    );
    const { container } = render(
      <KnowledgeList nodes={many} selectedId={null} onSelect={vi.fn()} />,
    );
    expect(table()).toHaveAttribute('aria-rowcount', '201');
    // jsdom has no layout: the list assumes a 600px viewport of 36px rows,
    // plus ten rows of overscan.
    let subjects = listedSubjects();
    expect(subjects).toHaveLength(27);
    expect(subjects[0]).toBe('Memory 000');
    expect(subjects.at(-1)).toBe('Memory 026');
    const spacers = container.querySelectorAll('.knowledge-list-spacer');
    expect(spacers).toHaveLength(1);
    expect(spacers[0]).toHaveAttribute('aria-hidden', 'true');
    expect(spacers[0].querySelector('td')).toHaveStyle({
      height: `${(200 - 27) * 36}px`,
    });

    const scroller = screen.getByLabelText('Memory list');
    scroller.scrollTop = 100 * 36;
    fireEvent.scroll(scroller);
    subjects = listedSubjects();
    expect(subjects[0]).toBe('Memory 090');
    expect(subjects.at(-1)).toBe('Memory 126');
    const firstRow = screen
      .getByRole('button', { name: 'Memory 090' })
      .closest('tr');
    expect(firstRow).toHaveAttribute('aria-rowindex', '92');
    const [top, bottom] = container.querySelectorAll('.knowledge-list-spacer');
    expect(top.querySelector('td')).toHaveStyle({ height: `${90 * 36}px` });
    expect(bottom.querySelector('td')).toHaveStyle({
      height: `${(200 - 127) * 36}px`,
    });
  });

  it('scrolls a memory chosen elsewhere into view', () => {
    const many = Array.from({ length: 200 }, (_, index) =>
      node(`m${String(index).padStart(3, '0')}`),
    );
    const { rerender } = render(
      <KnowledgeList nodes={many} selectedId={null} onSelect={vi.fn()} />,
    );
    const scroller = screen.getByLabelText('Memory list');
    expect(scroller.scrollTop).toBe(0);
    rerender(
      <KnowledgeList nodes={many} selectedId="m150" onSelect={vi.fn()} />,
    );
    expect(scroller.scrollTop).toBe(150 * 36);
  });
});

describe('sourceWords', () => {
  it('names the graph source buckets in words people use', () => {
    expect(sourceWords('manual')).toBe('Saved by you');
    expect(sourceWords('extraction')).toBe('From conversations');
    expect(sourceWords('document')).toBe('From documents');
    expect(sourceWords('wiki')).toBe('Wiki and Dream Cycle');
    expect(sourceWords('other')).toBe('Other');
    expect(sourceWords('custom_import')).toBe('Custom import');
  });
});
