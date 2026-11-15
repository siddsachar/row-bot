import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import SafeMarkdown from './chat-parity-markdown';

const BUDGET = [
  '| Item | Cost | Due |',
  '| --- | ---: | --- |',
  '| **Rent** | £1,200 | 1 Dec 2026 |',
  '| Food | £320 | 12 Nov 2026 |',
  '| Phone | £45 | 9 Nov 2026 |',
].join('\n');

function column(name: number) {
  return within(screen.getByRole('table'))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[name].textContent);
}

it('sorts a Markdown table from its headers: ascending, descending, then as written', () => {
  render(<SafeMarkdown text={BUDGET} />);
  const cost = screen.getByRole('button', { name: /Cost/ });
  const header = cost.closest('th')!;
  expect(header).toHaveAttribute('aria-sort', 'none');
  expect(column(1)).toEqual(['£1,200', '£320', '£45']);

  fireEvent.click(cost);
  expect(header).toHaveAttribute('aria-sort', 'ascending');
  expect(column(1)).toEqual(['£45', '£320', '£1,200']);

  fireEvent.click(cost);
  expect(header).toHaveAttribute('aria-sort', 'descending');
  expect(column(1)).toEqual(['£1,200', '£320', '£45']);

  fireEvent.click(cost);
  expect(header).toHaveAttribute('aria-sort', 'none');
  expect(column(0)).toEqual(['Rent', 'Food', 'Phone']);
});

it('sorts dates by date and moves the sort to the header last chosen', () => {
  render(<SafeMarkdown text={BUDGET} />);
  fireEvent.click(screen.getByRole('button', { name: /Cost/ }));
  fireEvent.click(screen.getByRole('button', { name: /Due/ }));
  expect(
    screen.getByRole('button', { name: /Cost/ }).closest('th'),
  ).toHaveAttribute('aria-sort', 'none');
  expect(column(2)).toEqual(['9 Nov 2026', '12 Nov 2026', '1 Dec 2026']);
});

it('sorts from the keyboard and right-aligns number columns', async () => {
  const user = userEvent.setup();
  render(<SafeMarkdown text={BUDGET} />);
  await user.tab();
  await user.tab();
  expect(screen.getByRole('button', { name: /Item/ })).toHaveFocus();
  await user.keyboard('{Enter}');
  expect(column(0)).toEqual(['Food', 'Phone', 'Rent']);
  const cells = screen.getAllByRole('cell');
  expect(cells[1]).toHaveAttribute('data-numeric', 'true');
  expect(cells[0]).not.toHaveAttribute('data-numeric');
});

it('names each header’s sort in its tooltip', async () => {
  const user = userEvent.setup();
  render(<SafeMarkdown text={BUDGET} />);
  await user.hover(screen.getByRole('button', { name: /Cost/ }));
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Sort by Cost');
});

it('copies the table as CSV in the order it is shown', async () => {
  const copyText = vi.fn().mockResolvedValue(true);
  render(<SafeMarkdown text={BUDGET} copyText={copyText} />);
  fireEvent.click(screen.getByRole('button', { name: /Cost/ }));
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Copy as CSV' })),
  );
  expect(copyText).toHaveBeenCalledExactlyOnceWith(
    [
      'Item,Cost,Due',
      'Phone,£45,9 Nov 2026',
      'Food,£320,12 Nov 2026',
      'Rent,"£1,200",1 Dec 2026',
    ].join('\r\n'),
  );
});
