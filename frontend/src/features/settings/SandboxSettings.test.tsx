import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { RowBotSandboxPage, RowBotSandboxView } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { SandboxSettings } from './SandboxSettings';

const PACKS: RowBotSandboxPage['packs'] = [
  { pack: 'empty', name: 'Empty' },
  { pack: 'shop', name: 'A small shop, with its own web app' },
];

function box(patch: Partial<RowBotSandboxView> = {}): RowBotSandboxView {
  return {
    sandbox_id: 'box-0a0b0c0d',
    name: 'Demo shop',
    pack: 'shop',
    pack_name: 'A small shop, with its own web app',
    status: 'running',
    url: 'http://127.0.0.1:51234/app-v2/',
    problem: '',
    ...patch,
  };
}

function setup(page: RowBotSandboxPage) {
  const controller = {
    rowBotSandboxes: vi.fn(async () => page),
    createRowBotSandbox: vi.fn(async () => box({ status: 'stopped' })),
    actOnRowBotSandbox: vi.fn(async () => box({ status: 'stopped' })),
    rowBotSandboxLink: vi.fn(async () => ({
      url: 'http://127.0.0.1:51234/app-v2/#sign-in=once',
    })),
    deleteRowBotSandbox: vi.fn(async () => ({ ...page, items: [] })),
  } as unknown as ClientController & Record<string, ReturnType<typeof vi.fn>>;
  const platform = {
    openExternal: vi.fn(async () => null),
  } as unknown as ClientPlatform & Record<string, ReturnType<typeof vi.fn>>;
  render(
    <RuntimeContext.Provider value={{ controller, platform }}>
      <SandboxSettings />
    </RuntimeContext.Provider>,
  );
  return { controller, platform };
}

it('lists sandboxes with their state, opens a running one signed in, and confirms before deleting', async () => {
  const { controller, platform } = setup({
    items: [box()],
    packs: PACKS,
    available: true,
  });
  const list = await screen.findByRole('list', { name: 'Sandboxes' });
  const row = within(list).getByRole('listitem');
  expect(row).toHaveTextContent('Demo shop');
  expect(row).toHaveTextContent('Running');
  await userEvent.click(
    within(row).getByRole('button', { name: 'Open Demo shop' }),
  );
  expect(platform.openExternal).toHaveBeenCalledWith(
    'http://127.0.0.1:51234/app-v2/#sign-in=once',
  );
  await userEvent.click(
    within(row).getByRole('button', { name: 'Delete Demo shop' }),
  );
  expect(controller.deleteRowBotSandbox).not.toHaveBeenCalled();
  expect(within(row).getByRole('group', { name: 'Confirm' })).toHaveTextContent(
    'Delete this sandbox and everything in it?',
  );
  await userEvent.click(within(row).getByRole('button', { name: 'Delete' }));
  expect(controller.deleteRowBotSandbox).toHaveBeenCalledWith('box-0a0b0c0d');
});

it('makes a new sandbox from a pack', async () => {
  const { controller } = setup({ items: [], packs: PACKS, available: true });
  const form = await screen.findByRole('group', { name: 'New sandbox' });
  await userEvent.type(within(form).getByLabelText('Name'), 'Shop demo');
  await userEvent.selectOptions(
    within(form).getByLabelText('Starts with'),
    'A small shop, with its own web app',
  );
  await userEvent.click(
    within(form).getByRole('button', { name: 'New sandbox' }),
  );
  expect(controller.createRowBotSandbox).toHaveBeenCalledWith({
    name: 'Shop demo',
    pack: 'shop',
  });
});

it('shows nothing inside a sandbox, which starts none of its own', async () => {
  const { controller } = setup({ items: [], packs: PACKS, available: false });
  await vi.waitFor(() => expect(controller.rowBotSandboxes).toHaveBeenCalled());
  expect(
    screen.queryByRole('heading', { name: 'Sandboxes' }),
  ).not.toBeInTheDocument();
});
