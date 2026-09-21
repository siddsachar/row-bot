import { fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { expect, it, vi } from 'vitest';
import type { SlashCommandSpec } from '../../api/types';
import SlashPalette, { type SlashPaletteHandle } from './SlashPalette';

const commands: SlashCommandSpec[] = [
  {
    id: 'status',
    token: '/status',
    aliases: ['/health'],
    label: 'Status',
    description: 'Show local runtime status.',
    icon: '●',
    category: 'Runtime',
    argument_mode: 'none',
    argument_hint: '',
    handler_kind: 'status',
  },
  {
    id: 'stop',
    token: '/stop',
    aliases: [],
    label: 'Stop',
    description: 'Stop the current generation.',
    icon: '■',
    category: 'Conversation',
    argument_mode: 'none',
    argument_hint: '',
    handler_kind: 'stop_generation',
  },
];

function Harness({ text = 'before /st after' }: { text?: string }) {
  const ref = createRef<SlashPaletteHandle>();
  const choose = vi.fn();
  return (
    <>
      <textarea
        aria-label="Draft"
        defaultValue={text}
        onKeyDown={(event) => {
          if (ref.current?.key(event)) event.preventDefault();
        }}
      />
      <SlashPalette
        ref={ref}
        text={text}
        cursor={text.indexOf('/st') + 3}
        commands={commands}
        disabled={false}
        onChoose={choose}
      />
      <output data-testid="chosen">{choose.mock.calls.length}</output>
    </>
  );
}

it('matches a slash token at the cursor without consuming surrounding draft text', () => {
  render(<Harness />);
  const palette = screen.getByRole('listbox', { name: 'Slash commands' });
  expect(palette).toHaveTextContent('/status');
  expect(palette).toHaveTextContent('/stop');
  expect(screen.getByRole('textbox', { name: 'Draft' })).toHaveValue(
    'before /st after',
  );
});

it('supports keyboard selection and escape dismissal', () => {
  render(<Harness />);
  const draft = screen.getByRole('textbox', { name: 'Draft' });
  fireEvent.keyDown(draft, { key: 'ArrowDown' });
  expect(screen.getByRole('option', { name: /\/stopStop/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  fireEvent.keyDown(draft, { key: 'Escape' });
  expect(screen.queryByRole('listbox')).toBeNull();
});

it('shows an honest empty result and supports mouse choice', () => {
  const { rerender } = render(<Harness text="/missing" />);
  expect(screen.getByText('No slash commands match.')).toHaveTextContent(
    'No slash commands match.',
  );
  rerender(<Harness text="/status" />);
  fireEvent.click(screen.getByRole('option', { name: /\/statusStatus/ }));
  expect(screen.queryByRole('listbox')).toBeNull();
});
