import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { InstallCountSnapshot } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import { Privacy, type PrivacyIo } from './InstallCount';

const sent: InstallCountSnapshot = {
  available: true,
  turned_off_here: false,
  enabled: true,
  service: 'row-bot-count.row-bot.workers.dev',
  version: '5.3.0',
  os: 'macos',
  last_sent_on: new Date().toISOString().slice(0, 10),
  can_delete: true,
};

function show(
  value: InstallCountSnapshot,
  remove: PrivacyIo['remove'] = async () => ({
    outcome: 'deleted',
    snapshot: { ...value, enabled: false, can_delete: false },
  }),
) {
  const io = {
    load: vi.fn(async () => value),
    choose: vi.fn(async (enabled: boolean) => ({ ...value, enabled })),
    remove: vi.fn(remove),
  };
  render(
    <MemoryRouter>
      <OverlayProvider>
        <Privacy io={io} />
      </OverlayProvider>
    </MemoryRouter>,
  );
  return io;
}

const summary = (privacy: HTMLElement, name: string) =>
  within(privacy).getByText(name, { selector: 'summary' });

it('is one row with a switch, its status, and details closed until asked for', async () => {
  const io = show(sent);
  const privacy = await screen.findByRole('region', { name: 'Privacy' });
  const toggle = await within(privacy).findByRole('switch', {
    name: 'Share install stats',
  });
  expect(toggle).toBeChecked();
  expect(privacy).toHaveTextContent('Install stats');
  expect(privacy).toHaveTextContent('last sent today');
  for (const name of ['Details', 'What else Row-Bot contacts'])
    expect(summary(privacy, name).closest('details')).not.toHaveAttribute(
      'open',
    );
  fireEvent.click(summary(privacy, 'Details'));
  expect(privacy).toHaveTextContent(
    'the Row-Bot version (5.3.0) and the system (macOS)',
  );
  fireEvent.click(toggle);
  await waitFor(() => expect(io.choose).toHaveBeenCalledWith(false));
  expect(await screen.findByText('Install stats turned off')).toBeVisible();
});

it('deletes what was sent from the details, only after confirming', async () => {
  const io = show(sent);
  const privacy = await screen.findByRole('region', { name: 'Privacy' });
  fireEvent.click(summary(privacy, 'Details'));
  fireEvent.click(
    within(privacy).getByRole('button', { name: 'Delete what was sent…' }),
  );
  const confirm = await screen.findByRole('alertdialog', {
    name: 'Delete what was sent?',
  });
  expect(confirm).toHaveTextContent('turns install stats off');
  expect(io.remove).not.toHaveBeenCalled();
  fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
  await waitFor(() => expect(io.remove).toHaveBeenCalledTimes(1));
  expect(
    await screen.findByText('Deleted what was sent. Install stats are off.'),
  ).toBeVisible();
  expect(
    screen.getByRole('switch', { name: 'Share install stats' }),
  ).not.toBeChecked();
  expect(
    screen.queryByRole('button', { name: 'Delete what was sent…' }),
  ).toBeNull();
});

it('says so when the stats service could not be reached to delete', async () => {
  show(sent, async () => ({
    outcome: 'failed',
    snapshot: { ...sent, enabled: false },
  }));
  const privacy = await screen.findByRole('region', { name: 'Privacy' });
  fireEvent.click(summary(privacy, 'Details'));
  fireEvent.click(
    within(privacy).getByRole('button', { name: 'Delete what was sent…' }),
  );
  fireEvent.click(
    within(
      await screen.findByRole('alertdialog', { name: 'Delete what was sent?' }),
    ).getByRole('button', { name: 'Delete' }),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Install stats are off, but Row-Bot couldn’t reach its stats service',
  );
});

it('names the set-up that turned install stats off, with no switch', async () => {
  show({ ...sent, available: false, turned_off_here: true, can_delete: false });
  expect(
    await screen.findByText(/Off on this server \(ROW_BOT_INSTALL_COUNT=0\)/),
  ).toBeVisible();
  expect(
    screen.queryByRole('switch', { name: 'Share install stats' }),
  ).toBeNull();
});

it('lists what else Row-Bot contacts, one line each, even while dormant', async () => {
  const io = show({
    ...sent,
    available: false,
    service: '',
    last_sent_on: null,
    can_delete: false,
  });
  await waitFor(() => expect(io.load).toHaveBeenCalled());
  const privacy = screen.getByRole('region', { name: 'Privacy' });
  expect(
    within(privacy).queryByRole('switch', { name: 'Share install stats' }),
  ).toBeNull();
  fireEvent.click(summary(privacy, 'What else Row-Bot contacts'));
  const items = within(privacy).getAllByRole('listitem');
  expect(
    items.map((item) => item.querySelector('strong')?.textContent),
  ).toEqual(['Update checks', 'Connection checks', 'Catalogs']);
  expect(privacy).toHaveTextContent('OpenRouter’s public list');
  expect(privacy).toHaveTextContent('models.dev’s, at most hourly');
  expect(
    within(privacy).getByRole('link', { name: 'Settings › Updates' }),
  ).toHaveAttribute('href', '/settings/updates');
});
