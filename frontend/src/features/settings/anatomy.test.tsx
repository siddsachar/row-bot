import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';
import { Input, Segmented, Toggle } from '../../ui/primitives';
import {
  SettingsGroup,
  SettingsHeaderSlot,
  SettingsItem,
  SettingsPageMenu,
  SettingsStatus,
  SettingsStatusSlot,
  StatusLine,
} from './anatomy';

it('names a row’s one control by its label and describes it with the help and status (B258)', () => {
  render(
    <SettingsGroup title="Identity">
      <SettingsItem
        label="Name"
        help="What Row-Bot calls itself in chat."
        status={<StatusLine tone="warning">Not saved yet</StatusLine>}
        control={<Input defaultValue="Row-Bot" />}
      />
    </SettingsGroup>,
  );
  const group = screen.getByRole('region', { name: 'Identity' });
  const name = within(group).getByRole('textbox', { name: 'Name' });
  expect(name).toHaveAccessibleDescription(
    'What Row-Bot calls itself in chat. Not saved yet',
  );
  expect(name).toHaveValue('Row-Bot');
});

it('leaves a control that names itself alone and keeps a switch beside its label', () => {
  function Rows() {
    const [bubbles, setBubbles] = useState('normal');
    return (
      <SettingsGroup label="Buddy">
        <SettingsItem
          label="Bubbles"
          help="Quiet hides them."
          bind={false}
          control={
            <Segmented
              label="Bubbles"
              value={bubbles}
              onChange={setBubbles}
              options={[
                { value: 'quiet', label: 'Quiet' },
                { value: 'normal', label: 'Normal' },
              ]}
            />
          }
        />
        <SettingsItem
          label="Show Buddy"
          layout="inline"
          control={<Toggle label="Show Buddy" defaultChecked />}
        />
      </SettingsGroup>
    );
  }
  render(<Rows />);
  const bubbles = screen.getByRole('radiogroup', { name: 'Bubbles' });
  expect(bubbles).not.toHaveAttribute('id');
  expect(screen.getByRole('switch', { name: 'Show Buddy' })).toBeChecked();
  expect(
    screen.getByRole('switch', { name: 'Show Buddy' }).closest('.settings-row'),
  ).toHaveAttribute('data-layout', 'inline');
});

it('puts extra content under the row only when there is some', () => {
  const { container, rerender } = render(
    <SettingsItem label="Speech model" control={<Input />}>
      {false}
      {null}
    </SettingsItem>,
  );
  expect(container.querySelector('.settings-row-extra')).toBeNull();
  rerender(
    <SettingsItem label="Speech model" control={<Input />}>
      <p role="alert">The change was rejected.</p>
    </SettingsItem>,
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    'The change was rejected.',
  );
});

it('shows one status line in the header slot, parts joined by a dot (B258)', () => {
  const slot = document.createElement('div');
  document.body.append(slot);
  render(
    <SettingsStatusSlot.Provider value={slot}>
      <SettingsStatus tone="success" more={['last ran today', '', null]}>
        Dream Cycle on
      </SettingsStatus>
    </SettingsStatusSlot.Provider>,
  );
  expect(slot).toHaveTextContent('Dream Cycle on·last ran today');
  expect(slot.querySelectorAll('.settings-status-sep')).toHaveLength(1);
  expect(slot.querySelector('[data-tone="success"]')).not.toBeNull();
  slot.remove();
});

it('keeps rare page actions in the header’s ⋯', async () => {
  const user = userEvent.setup();
  const slot = document.createElement('div');
  document.body.append(slot);
  const reread = vi.fn();
  render(
    <SettingsHeaderSlot.Provider value={slot}>
      <SettingsPageMenu
        label="More model actions"
        actions={[{ label: 'Re-read model settings', onSelect: reread }]}
      />
    </SettingsHeaderSlot.Provider>,
  );
  await user.click(
    within(slot).getByRole('button', { name: 'More model actions' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Re-read model settings' }),
  );
  expect(reread).toHaveBeenCalledOnce();
  slot.remove();
});
