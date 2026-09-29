import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';
import PromptTextarea, { type PromptVariable } from './PromptTextarea';

function Harness({
  initial = '',
  variables,
  onValue = () => {},
}: {
  initial?: string;
  variables?: readonly PromptVariable[];
  onValue?: (value: string) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <label>
      Prompt 1
      <PromptTextarea
        value={value}
        variables={variables}
        onChange={(next) => {
          setValue(next);
          onValue(next);
        }}
      />
    </label>
  );
}

function type(box: HTMLTextAreaElement, text: string) {
  fireEvent.change(box, { target: { value: text } });
  box.setSelectionRange(text.length, text.length);
  fireEvent.select(box);
}

it('suggests variables after {{ and inserts the chosen one (parity row 19)', () => {
  const onValue = vi.fn();
  render(<Harness onValue={onValue} />);
  const box = screen.getByRole('combobox', {
    name: 'Prompt 1',
  }) as HTMLTextAreaElement;
  expect(box).toHaveAttribute('aria-expanded', 'false');
  type(box, 'News for {{d');
  expect(box).toHaveAttribute('aria-expanded', 'true');
  const list = screen.getByRole('listbox', { name: 'Variables' });
  expect(
    Array.from(list.querySelectorAll('[role=option]')).map(
      (option) => option.textContent,
    ),
  ).toEqual(["{{date}}Today's date", '{{day}}Day of the week']);
  fireEvent.keyDown(box, { key: 'ArrowDown' });
  fireEvent.keyDown(box, { key: 'Enter' });
  expect(onValue).toHaveBeenLastCalledWith('News for {{day}}');
  expect(box).toHaveAttribute('aria-expanded', 'false');
});

it('offers earlier steps by name and closes on Escape without changing the text', () => {
  const onValue = vi.fn();
  render(
    <Harness
      onValue={onValue}
      variables={[{ token: 'step.fetch.output', label: 'Result of Fetch' }]}
    />,
  );
  const box = screen.getByRole('combobox', {
    name: 'Prompt 1',
  }) as HTMLTextAreaElement;
  type(box, 'Use {{fe');
  const options = screen.getAllByRole('option');
  expect(options.map((option) => option.textContent)).toEqual([
    '{{step.fetch.output}}Result of Fetch',
  ]);
  onValue.mockClear();
  fireEvent.keyDown(box, { key: 'Escape' });
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(onValue).not.toHaveBeenCalled();
  type(box, 'Use {{fet');
  fireEvent.mouseDown(screen.getByRole('option'));
  fireEvent.click(screen.getByRole('option'));
  expect(onValue).toHaveBeenLastCalledWith('Use {{step.fetch.output}}');
});

it('stays quiet once a variable is closed or in read-only prompts', () => {
  const { unmount } = render(<Harness />);
  const box = screen.getByRole('combobox', {
    name: 'Prompt 1',
  }) as HTMLTextAreaElement;
  type(box, 'On {{date}} summarize');
  expect(screen.queryByRole('listbox')).toBeNull();
  unmount();
  render(
    <label>
      Prompt 1
      <PromptTextarea value="{{" readOnly onChange={() => {}} />
    </label>,
  );
  const readOnly = screen.getByRole('textbox', { name: 'Prompt 1' });
  fireEvent.select(readOnly);
  expect(screen.queryByRole('listbox')).toBeNull();
});
