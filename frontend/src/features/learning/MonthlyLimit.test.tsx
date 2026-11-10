import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';
import { MonthlyLimitPicker } from './MonthlyLimit';

/** The picker as a saved setting uses it: what it answers becomes the saved value. */
function Saved({
  initial,
  onChange,
}: {
  initial: number | null;
  onChange: (limit: number | null) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <label htmlFor="limit">Monthly limit</label>
      <MonthlyLimitPicker
        id="limit"
        value={value}
        onChange={(limit) => {
          onChange(limit);
          setValue(limit);
        }}
      />
      <button type="button">Elsewhere</button>
    </>
  );
}

function show(initial: number | null) {
  const onChange = vi.fn();
  render(<Saved initial={initial} onChange={onChange} />);
  return {
    onChange,
    menu: screen.getByRole('combobox', { name: 'Monthly limit' }),
    field: () =>
      screen.queryByRole('textbox', {
        name: 'Custom monthly limit, in millions of tokens',
      }),
  };
}

it('offers the fixed choices, a custom amount, no limit and no model calls', () => {
  const { menu, field, onChange } = show(2_000_000);

  expect(menu).toHaveDisplayValue('2M tokens');
  expect(
    Array.from(menu.querySelectorAll('option'), (option) => option.textContent),
  ).toEqual([
    '500K tokens',
    '1M tokens',
    '2M tokens',
    '5M tokens',
    '10M tokens',
    '20M tokens',
    '50M tokens',
    'Custom amount…',
    'No limit',
    'No model calls',
  ]);
  expect(field()).toBeNull();

  fireEvent.change(menu, { target: { value: 'none' } });
  expect(onChange).toHaveBeenLastCalledWith(null);
  expect(menu).toHaveDisplayValue('No limit');
  fireEvent.change(menu, { target: { value: '0' } });
  expect(onChange).toHaveBeenLastCalledWith(0);
  fireEvent.change(menu, { target: { value: '50000000' } });
  expect(onChange).toHaveBeenLastCalledWith(50_000_000);
});

it('a custom amount is typed in millions and saves when Enter is pressed or the field is left', () => {
  const { menu, field, onChange } = show(2_000_000);

  fireEvent.change(menu, { target: { value: 'custom' } });
  // Nothing is saved by choosing Custom amount; the field starts from the saved limit.
  expect(onChange).not.toHaveBeenCalled();
  expect(field()).toHaveFocus();
  expect(field()).toHaveValue('2');
  expect(screen.getByText('M tokens')).toBeVisible();

  fireEvent.change(field()!, { target: { value: '75' } });
  fireEvent.keyDown(field()!, { key: 'Enter' });
  expect(onChange).toHaveBeenLastCalledWith(75_000_000);
  expect(menu).toHaveDisplayValue('Custom amount…');
  expect(field()).toHaveValue('75');

  // Rounded to the nearest 0.1M; leaving the field saves it.
  fireEvent.change(field()!, { target: { value: ' 120.44 ' } });
  fireEvent.blur(field()!);
  expect(onChange).toHaveBeenLastCalledWith(120_400_000);
  expect(field()).toHaveValue('120.4');
  expect(onChange).toHaveBeenCalledTimes(2);
});

it('a custom amount out of bounds or not a number says what fits and saves nothing', () => {
  const { menu, field, onChange } = show(null);
  fireEvent.change(menu, { target: { value: 'custom' } });
  expect(field()).toHaveValue('');

  for (const typed of ['0.04', '1001', 'lots', '-5']) {
    fireEvent.change(field()!, { target: { value: typed } });
    fireEvent.keyDown(field()!, { key: 'Enter' });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Enter 0.1 to 1,000 million tokens, like 75 or 2.5.',
    );
    expect(field()).toHaveAttribute('aria-invalid', 'true');
    expect(field()).toHaveAccessibleDescription(
      'Enter 0.1 to 1,000 million tokens, like 75 or 2.5.',
    );
  }
  expect(onChange).not.toHaveBeenCalled();

  // The bounds themselves are amounts.
  fireEvent.change(field()!, { target: { value: '1000' } });
  fireEvent.keyDown(field()!, { key: 'Enter' });
  expect(onChange).toHaveBeenLastCalledWith(1_000_000_000);
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.change(field()!, { target: { value: '0.1' } });
  fireEvent.keyDown(field()!, { key: 'Enter' });
  expect(onChange).toHaveBeenLastCalledWith(100_000);
});

it('a custom amount that is one of the choices shows as that choice', () => {
  const { menu, field, onChange } = show(null);
  fireEvent.change(menu, { target: { value: 'custom' } });
  fireEvent.change(field()!, { target: { value: '50' } });
  fireEvent.keyDown(field()!, { key: 'Enter' });

  expect(onChange).toHaveBeenLastCalledWith(50_000_000);
  expect(menu).toHaveDisplayValue('50M tokens');
  expect(field()).toBeNull();
});

it('a saved amount that is not a choice shows as a custom amount', () => {
  const { menu, field } = show(3_000_000);
  expect(menu).toHaveDisplayValue('Custom amount…');
  expect(field()).toHaveValue('3');
});

it('leaving an empty field or pressing Escape goes back to the saved limit without saving', () => {
  const { menu, field, onChange } = show(null);
  fireEvent.change(menu, { target: { value: 'custom' } });
  fireEvent.blur(field()!);
  expect(menu).toHaveDisplayValue('No limit');
  expect(field()).toBeNull();

  fireEvent.change(menu, { target: { value: 'custom' } });
  fireEvent.change(field()!, { target: { value: '9999' } });
  fireEvent.keyDown(field()!, { key: 'Escape' });
  expect(menu).toHaveDisplayValue('No limit');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});
