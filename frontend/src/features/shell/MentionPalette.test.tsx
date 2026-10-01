import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useRef, useState } from 'react';
import { expect, it, vi } from 'vitest';
import MentionPalette, { type MentionItem } from './MentionPalette';
import type { SlashPaletteHandle } from './SlashPalette';

function Harness({
  items,
  onConsume,
}: {
  items: MentionItem[];
  onConsume: (token: { start: number; end: number }) => void;
}) {
  const [text, setText] = useState('');
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLTextAreaElement>(null);
  const palette = useRef<SlashPaletteHandle>(null);
  return (
    <>
      <textarea
        ref={input}
        aria-label="Message"
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setCursor(event.target.selectionStart ?? event.target.value.length);
        }}
        onKeyDown={(event) => {
          if (palette.current?.key(event)) event.preventDefault();
        }}
      />
      <MentionPalette
        ref={palette}
        text={text}
        cursor={cursor}
        items={items}
        disabled={false}
        inputRef={input}
        onConsume={onConsume}
      />
    </>
  );
}

it('offers agents, resources and files after "@" and applies the choice', async () => {
  const profile = vi.fn();
  const target = vi.fn();
  const consume = vi.fn();
  const items: MentionItem[] = [
    {
      id: 'profile:',
      group: 'Agents',
      label: 'Default',
      current: true,
      icon: null,
      onChoose: vi.fn(),
    },
    {
      id: 'profile:analyst',
      group: 'Agents',
      label: 'Data Analyst',
      icon: null,
      onChoose: profile,
    },
    {
      id: 'resource:landing',
      group: 'Resources',
      label: 'Landing page',
      icon: null,
      onChoose: target,
    },
  ];
  render(<Harness items={items} onConsume={consume} />);
  const input = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(input, {
    target: { value: 'Ask @ana', selectionStart: 8 },
  });
  const palette = screen.getByRole('listbox', { name: 'Mentions' });
  expect(within(palette).getAllByRole('option')).toHaveLength(1);
  expect(input).toHaveAttribute('aria-controls', palette.id);
  await act(async () => fireEvent.keyDown(input, { key: 'Enter' }));
  expect(consume).toHaveBeenCalledWith(
    expect.objectContaining({ start: 4, end: 8 }),
  );
  expect(profile).toHaveBeenCalledOnce();
  expect(screen.queryByRole('listbox', { name: 'Mentions' })).toBeNull();

  fireEvent.change(input, { target: { value: '@', selectionStart: 1 } });
  const all = screen.getByRole('listbox', { name: 'Mentions' });
  expect(within(all).getByRole('group', { name: 'Resources' })).toBeVisible();
  expect(within(all).getByRole('option', { name: /^Default/ })).toHaveAttribute(
    'aria-current',
    'true',
  );
  fireEvent.click(within(all).getByRole('option', { name: /^Landing page/ }));
  expect(target).toHaveBeenCalledOnce();
});

it('closes on Escape and ignores text without a mention', () => {
  render(<Harness items={[]} onConsume={vi.fn()} />);
  const input = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(input, { target: { value: 'email me', selectionStart: 8 } });
  expect(screen.queryByRole('listbox', { name: 'Mentions' })).toBeNull();
  fireEvent.change(input, { target: { value: '@x', selectionStart: 2 } });
  expect(screen.getByRole('status')).toHaveTextContent('Nothing matches.');
  fireEvent.keyDown(input, { key: 'Escape' });
  expect(screen.queryByRole('listbox', { name: 'Mentions' })).toBeNull();
});

it('opens again when a dismissed "@" is deleted and typed again', () => {
  const items = [
    {
      id: 'file',
      group: 'Files',
      label: 'Attach a file…',
      icon: null,
      onChoose: vi.fn(),
    },
  ];
  render(<Harness items={items} onConsume={vi.fn()} />);
  const input = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(input, { target: { value: '@', selectionStart: 1 } });
  fireEvent.keyDown(input, { key: 'Escape' });
  expect(screen.queryByRole('listbox', { name: 'Mentions' })).toBeNull();
  fireEvent.change(input, { target: { value: '', selectionStart: 0 } });
  fireEvent.change(input, { target: { value: '@', selectionStart: 1 } });
  expect(screen.getByRole('listbox', { name: 'Mentions' })).toBeVisible();
});
