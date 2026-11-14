import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import ProactiveSettings from './ProactiveSettings';
import { fakeIO, moment, settings } from '../proactive/test-data';

function show(io: ReturnType<typeof fakeIO>) {
  return render(
    <MemoryRouter>
      <ProactiveSettings io={io} />
    </MemoryRouter>,
  );
}

describe('Settings › Proactive', () => {
  it('changes the level at once and shows what each level brings up', async () => {
    const io = fakeIO();
    show(io);
    expect(
      await screen.findByText(/Things wait on Home\. Buddy speaks only/),
    ).toBeVisible();
    await userEvent.click(screen.getByRole('radio', { name: 'Balanced' }));
    expect(io.changeSettings).toHaveBeenCalledWith({ level: 'balanced' });
    expect(await screen.findByText('An example day at Balanced')).toBeVisible();
    expect(screen.getByText(/Buddy speaks at good moments/)).toBeVisible();
  });

  it('turns a source off, and brings a quieted kind back', async () => {
    const io = fakeIO();
    show(io);
    expect(
      await screen.findByText(/Quieted: you left the last few alone/),
    ).toBeVisible();
    await userEvent.click(screen.getByRole('switch', { name: 'Mail' }));
    expect(io.changeSettings).toHaveBeenCalledWith({
      sources: { mail: false },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Bring back' }));
    await waitFor(() =>
      expect(io.changeSettings).toHaveBeenCalledWith({ kinds: { date: true } }),
    );
    expect(io.changeSettings).toHaveBeenCalledWith({ kinds: { date: false } });
  });

  it('lists your rules to edit or delete', async () => {
    const io = fakeIO({
      settings: settings({
        rules: [
          {
            rule_id: 'bbbbbbbbbbbb',
            type: 'never_about',
            kind: '',
            topic: 'the offsite',
            hour: -1,
            words: 'Never about the offsite',
          },
        ],
      }),
    });
    show(io);
    const rules = await screen.findByRole('list', { name: 'Your rules' });
    await userEvent.click(
      within(rules).getByRole('button', {
        name: 'Delete: Never about the offsite',
      }),
    );
    expect(io.deleteRule).toHaveBeenCalledWith('bbbbbbbbbbbb');
    expect(await screen.findByText(/No rules yet/)).toBeVisible();
  });

  it('shows every check in the Activity log, and planned moments with Cancel', async () => {
    const io = fakeIO({
      moments: [moment({ state: 'pending', title: 'Brief for 1:1 with Ana' })],
    });
    show(io);
    expect(
      await screen.findByText('Checked mail, calendar: nothing new.'),
    ).toBeVisible();
    expect(screen.getByText('Brief for 1:1 with Ana · planned')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(io.feedback).toHaveBeenCalledWith('0123456789abcdef', {
      action: 'dismiss',
    });
  });
});
