import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ResourceView } from '../../api/types';
import ResourceTargets from './ResourceTargets';

const resource = (
  binding: string,
  kind: 'artifact' | 'workspace' = 'artifact',
  title = 'Saved resource',
): ResourceView => ({
  resource_ref: `conversation-a:${binding}`,
  conversation_revision: '1',
  binding: {
    binding_id: binding,
    resource_id: `saved-${binding}`,
    kind,
    role: 'context',
    revision: '1',
  },
  title,
  resource_revision: '7',
  available: true,
});
async function menu(name: string) {
  await act(async () =>
    fireEvent.keyDown(screen.getByRole('button', { name }), { key: 'Enter' }),
  );
  return within(screen.getByRole('menu'));
}

it('shows explicit None without choosing a target merely because a resource is bound or focused', async () => {
  const onChange = vi.fn();
  render(
    <ResourceTargets
      resources={[resource('deck'), resource('folder', 'workspace')]}
      selected={[]}
      onChange={onChange}
    />,
  );
  expect(screen.getByRole('button', { name: 'Deck target' })).toHaveTextContent(
    'Deck · None',
  );
  expect(
    screen.getByRole('button', { name: 'Folder target' }),
  ).toHaveTextContent('Folder · None');
  const trigger = screen.getByRole('button', { name: 'Deck target' });
  act(() => trigger.focus());
  const choices = await menu('Deck target');
  act(() => choices.getByRole('menuitem', { name: /Saved resource/ }).focus());
  expect(onChange).not.toHaveBeenCalled();
});

it.each([
  ['artifact', 'Deck target'],
  ['workspace', 'Folder target'],
] as const)(
  'changes only the explicit %s binding and supports None',
  async (kind, label) => {
    const onChange = vi.fn();
    render(
      <ResourceTargets
        resources={[resource('chosen', kind)]}
        selected={['chosen']}
        onChange={onChange}
      />,
    );
    let choices = await menu(label);
    await act(async () =>
      fireEvent.click(choices.getByRole('menuitem', { name: /^None/ })),
    );
    expect(onChange).toHaveBeenLastCalledWith(kind, null);
    choices = await menu(label);
    await act(async () =>
      fireEvent.click(
        choices.getByRole('menuitem', { name: /Saved resource/ }),
      ),
    );
    expect(onChange).toHaveBeenLastCalledWith(kind, 'chosen');
    expect(onChange).toHaveBeenCalledTimes(2);
  },
);

it('disambiguates duplicate names and retains the exact chosen resource and binding identity', async () => {
  const onChange = vi.fn();
  render(
    <ResourceTargets
      resources={[resource('first'), resource('second')]}
      selected={['second']}
      onChange={onChange}
    />,
  );
  expect(screen.getByRole('button', { name: 'Deck target' })).toHaveTextContent(
    'saved-second',
  );
  const choices = await menu('Deck target');
  expect(
    choices.getByRole('menuitem', { name: /Saved resource · saved-first/ }),
  ).toBeVisible();
  await act(async () =>
    fireEvent.click(
      choices.getByRole('menuitem', { name: /Saved resource · saved-second/ }),
    ),
  );
  expect(onChange).toHaveBeenCalledExactlyOnceWith('artifact', 'second');
});

it('keeps an unavailable selected target visible and disabled until explicit None', async () => {
  const onChange = vi.fn();
  const unavailable = { ...resource('revoked'), available: false };
  render(
    <ResourceTargets
      resources={[unavailable]}
      selected={['revoked']}
      onChange={onChange}
    />,
  );
  expect(screen.getByRole('button', { name: 'Deck target' })).toHaveTextContent(
    'Saved resource (unavailable)',
  );
  const choices = await menu('Deck target');
  const item = choices.getByRole('menuitem', {
    name: /Saved resource \(unavailable\)/,
  });
  expect(item).toHaveAttribute('aria-disabled', 'true');
  fireEvent.click(item);
  expect(onChange).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(choices.getByRole('menuitem', { name: /^None/ })),
  );
  expect(onChange).toHaveBeenCalledWith('artifact', null);
});

it('does not infer targets from order, titles or resource updates and omits irrelevant kind menus', () => {
  const onChange = vi.fn();
  const a = resource('a', 'workspace', 'Folder A');
  const b = resource('b', 'workspace', 'Folder B');
  const view = render(
    <ResourceTargets resources={[a, b]} selected={['a']} onChange={onChange} />,
  );
  expect(screen.queryByRole('button', { name: 'Deck target' })).toBeNull();
  view.rerender(
    <ResourceTargets
      resources={[b, { ...a, title: 'Renamed A', resource_revision: '8' }]}
      selected={['a']}
      onChange={onChange}
    />,
  );
  expect(
    screen.getByRole('button', { name: 'Folder target' }),
  ).toHaveTextContent('Renamed A');
  expect(onChange).not.toHaveBeenCalled();
  view.rerender(
    <ResourceTargets resources={[]} selected={['a']} onChange={onChange} />,
  );
  expect(screen.queryByRole('button')).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});
