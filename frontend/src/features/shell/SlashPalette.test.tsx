import { act, fireEvent, render, screen } from '@testing-library/react';
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
    icon: 'monitor_heart',
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
    icon: 'stop_circle',
    category: 'Conversation',
    argument_mode: 'none',
    argument_hint: '',
    handler_kind: 'stop_generation',
  },
];

function Harness({
  text = 'before /st after',
  specs = commands,
  disabled = false,
}: {
  text?: string;
  specs?: SlashCommandSpec[];
  disabled?: boolean;
}) {
  const ref = createRef<SlashPaletteHandle>();
  const inputRef = createRef<HTMLTextAreaElement>();
  const choose = vi.fn();
  return (
    <>
      <textarea
        ref={inputRef}
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
        commands={specs}
        disabled={disabled}
        inputRef={inputRef}
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
  expect(draft).toHaveAttribute('aria-expanded', 'true');
  expect(draft.getAttribute('aria-controls')).toBe(
    screen.getByRole('listbox').id,
  );
  expect(draft.getAttribute('aria-activedescendant')).toBe(
    screen.getByRole('option', { name: '/stop Stop' }).id,
  );
  expect(screen.getByRole('option', { name: '/stop Stop' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  fireEvent.keyDown(draft, { key: 'Escape' });
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(draft).not.toHaveAttribute('aria-activedescendant');
});

it('renders local SVG icons and a safe fallback without exposing raw icon names', () => {
  render(
    <Harness
      text="/"
      specs={[
        ...commands,
        {
          ...commands[0],
          id: 'custom',
          token: '/custom',
          icon: '__proto__',
        },
      ]}
    />,
  );
  const options = screen.getAllByRole('option');
  expect(options).toHaveLength(3);
  for (const option of options) {
    expect(option.querySelector('.slash-palette-icon svg')).not.toBeNull();
    expect(option).not.toHaveTextContent(/monitor_heart|stop_circle|__proto__/);
  }
});

it('shows an honest empty result and supports mouse choice', () => {
  const { rerender } = render(<Harness text="/missing" />);
  expect(screen.getByText('No slash commands match.')).toHaveTextContent(
    'No slash commands match.',
  );
  rerender(<Harness text="/status" />);
  fireEvent.click(screen.getByRole('option', { name: '/status Status' }));
  expect(screen.queryByRole('listbox')).toBeNull();
});

it('removes the listbox relationship when commands are disabled', () => {
  const view = render(<Harness text="/st" />);
  const draft = screen.getByRole('textbox', { name: 'Draft' });
  expect(draft).toHaveAttribute('aria-controls');
  view.rerender(<Harness text="/st" disabled />);
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(draft).not.toHaveAttribute('aria-controls');
});

it('steps aside while another composer control has focus and returns with the composer (B5)', () => {
  render(
    <>
      <Harness />
      <button type="button">Approvals</button>
    </>,
  );
  const draft = screen.getByRole('textbox', { name: 'Draft' });
  act(() => draft.focus());
  expect(screen.getByRole('listbox', { name: 'Slash commands' })).toBeVisible();
  act(() => screen.getByRole('button', { name: 'Approvals' }).focus());
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(draft).not.toHaveAttribute('aria-expanded');
  act(() => draft.focus());
  expect(screen.getByRole('listbox', { name: 'Slash commands' })).toBeVisible();
  // Pointer presses inside the palette keep composer focus.
  const press = fireEvent.mouseDown(screen.getByRole('listbox'));
  expect(press).toBe(false);
});
