import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ConversationView } from '../../api/types';
import SettingsConversationPicker, {
  resolveSettingsConversation,
  settingsConversationChoices,
} from './SettingsConversationPicker';

const conversations: ConversationView[] = Array.from(
  { length: 55 },
  (_, index) => ({
    id: `private-id-${index + 1}`,
    revision: '1',
    title: `Saved conversation ${index + 1}`,
    pinned: false,
  }),
);

it('resolves an explicit, selected, then recent conversation fallback', () => {
  expect(
    resolveSettingsConversation(conversations, 'private-id-4', 'private-id-3'),
  ).toBe('private-id-4');
  expect(
    resolveSettingsConversation(conversations, 'missing', 'private-id-3'),
  ).toBe('private-id-3');
  expect(
    resolveSettingsConversation(conversations, 'missing', 'also-missing'),
  ).toBe('private-id-1');
});

it('shows a bounded title-only selector without changing global selection', () => {
  const onChange = vi.fn();
  render(
    <SettingsConversationPicker
      conversations={conversations}
      conversationId="private-id-55"
      onChange={onChange}
    />,
  );

  const picker = screen.getByRole('combobox', {
    name: 'Conversation context',
  });
  expect(
    settingsConversationChoices(conversations, 'private-id-55'),
  ).toHaveLength(50);
  expect(screen.getAllByRole('option')).toHaveLength(50);
  expect(screen.getByRole('option', { name: 'Saved conversation 55' })).toBe(
    picker.querySelector('option[value="private-id-55"]'),
  );
  expect(picker).not.toHaveTextContent('private-id');

  fireEvent.change(picker, { target: { value: 'private-id-2' } });
  expect(onChange).toHaveBeenCalledWith('private-id-2');
});
