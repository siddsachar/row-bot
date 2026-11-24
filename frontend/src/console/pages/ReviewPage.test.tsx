import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { renderConsole, sent } from '../testing';

afterEach(() => vi.unstubAllGlobals());

const COMPANY = 'a0000000-0000-4000-8000-000000000001';
const SALES = 'a0000000-0000-4000-8000-000000000003';
const COMMISSION = 'c0000000-0000-4000-8000-000000000001';
const PARKING = 'c0000000-0000-4000-8000-000000000002';
const CHECKLIST = 'c0000000-0000-4000-8000-000000000003';

async function rows() {
  const list = await screen.findByRole('list', { name: 'Shares' });
  return within(list).getAllByRole('listitem');
}

test('the queue lists what waits, oldest first, with who shared it where, and filters by space and status', async () => {
  const api = renderConsole('/review');
  const waiting = await rows();
  expect(waiting).toHaveLength(3);
  expect(waiting[0]).toHaveTextContent('Release checklist notes');
  expect(waiting[0]).toHaveTextContent('Engineering · Shared by Grace Hopper');
  expect(waiting[2]).toHaveTextContent('Sales team · Shared by Ada Lovelace');
  expect(waiting[0]).toHaveTextContent('Waiting');
  expect(
    screen.getByText('3 shares are waiting for review.'),
  ).toBeInTheDocument();
  expect(api.calls.at(-1)?.path).toBe('/api/brain/review?status=pending');

  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: 'Space' }),
    'Sales team',
  );
  expect(
    await screen.findByText('1 share is waiting for review.'),
  ).toBeInTheDocument();
  expect(await rows()).toHaveLength(1);
  expect(api.calls.at(-1)?.path).toBe(
    `/api/brain/review?space=${SALES}&status=pending`,
  );

  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: 'Show' }),
    'All',
  );
  const all = await rows();
  expect(all).toHaveLength(2);
  expect(screen.getByText('Discount approvals')).toBeInTheDocument();
  expect(
    all.find((row) => row.textContent?.includes('Discount')),
  ).toHaveTextContent('Approved by Sam Rivera');
  // Decided shares can't be decided again.
  expect(
    screen.queryByRole('button', { name: 'Approve “Discount approvals”' }),
  ).not.toBeInTheDocument();

  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: 'Show' }),
    'Withdrawn',
  );
  expect(
    await screen.findByText('No shares match these filters.'),
  ).toBeInTheDocument();
});

test('a share’s details show what was shared, where it came from, and why one was rejected', async () => {
  renderConsole('/review?status=all');
  await rows();
  await userEvent.click(
    screen.getByRole('button', { name: 'Show details for Commission timing' }),
  );
  expect(
    screen.getByText(
      'Commission is paid on the 5th working day of each month, for the month before.',
    ),
  ).toBeVisible();
  expect(screen.getByText('From a chat on 12 Nov')).toBeVisible();

  await userEvent.click(
    screen.getByRole('button', { name: 'Show details for Lunch on Fridays' }),
  );
  expect(
    screen.getByText(
      'Only in the London office: we will add it to its own page.',
    ),
  ).toBeVisible();
  expect(screen.getByText('Why it was rejected')).toBeVisible();
});

test('approving as sent says what happens first, then makes it a fact', async () => {
  const api = renderConsole('/review');
  await rows();
  await userEvent.click(
    screen.getByRole('button', { name: 'Approve “Commission timing”' }),
  );
  const dialog = screen.getByRole('dialog');
  expect(dialog).toHaveTextContent('Approve “Commission timing”?');
  expect(dialog).toHaveTextContent(
    'This becomes a fact in Sales team that everyone in Sales team can see.',
  );
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Approve' }),
  );
  expect(
    await screen.findByText('“Commission timing” is now a fact in Sales team'),
  ).toBeInTheDocument();
  expect(sent(api, 'POST', `/api/brain/review/${COMMISSION}/approve`)).toEqual(
    {},
  );
  expect(await rows()).toHaveLength(2);
  expect(
    screen.getByText('2 shares are waiting for review.'),
  ).toBeInTheDocument();
});

test('approving after editing sends only what changed, and can move the share to another space', async () => {
  const api = renderConsole('/review');
  await rows();
  await userEvent.click(
    screen.getByRole('button', { name: 'Approve “Commission timing”' }),
  );
  const dialog = screen.getByRole('dialog');
  const title = within(dialog).getByLabelText(/^Title/);
  await userEvent.clear(title);
  await userEvent.type(title, 'When commission is paid');
  await userEvent.selectOptions(
    within(dialog).getByRole('combobox', { name: 'Space' }),
    'Company',
  );
  expect(dialog).toHaveTextContent(
    'This becomes a fact in Company that everyone in the company can see.',
  );
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Approve' }),
  );
  expect(
    await screen.findByText(
      '“When commission is paid” is now a fact in Company',
    ),
  ).toBeInTheDocument();
  expect(sent(api, 'POST', `/api/brain/review/${COMMISSION}/approve`)).toEqual({
    title: 'When commission is paid',
    space_id: COMPANY,
  });
});

test('a share longer than a fact holds is approved only once shortened', async () => {
  const api = renderConsole('/review');
  await rows();
  await userEvent.click(
    screen.getByRole('button', { name: 'Approve “Release checklist notes”' }),
  );
  const dialog = screen.getByRole('dialog');
  const approve = within(dialog).getByRole('button', { name: 'Approve' });
  expect(approve).toBeDisabled();
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    /^A fact holds up to 2,000 characters\. Shorten this by [\d,]+ characters to approve it\.$/,
  );
  const fact = within(dialog).getByLabelText(/^Fact/);
  await userEvent.clear(fact);
  await userEvent.type(fact, 'Run the full test matrix before a release.');
  expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();
  expect(dialog).toHaveTextContent('42 of 2,000 characters');
  await userEvent.click(approve);
  expect(
    await screen.findByText(
      '“Release checklist notes” is now a fact in Engineering',
    ),
  ).toBeInTheDocument();
  expect(sent(api, 'POST', `/api/brain/review/${CHECKLIST}/approve`)).toEqual({
    text: 'Run the full test matrix before a release.',
  });
});

test('rejecting asks for a reason and says who reads it', async () => {
  const api = renderConsole('/review');
  await rows();
  await userEvent.click(
    screen.getByRole('button', {
      name: 'Reject “Parking at the harbour office”',
    }),
  );
  const dialog = screen.getByRole('dialog');
  expect(dialog).toHaveTextContent(
    'Nothing is added to Company. Lin Okafor sees that it was rejected, with your reason.',
  );
  const reject = within(dialog).getByRole('button', { name: 'Reject' });
  expect(reject).toBeDisabled();
  await userEvent.type(
    within(dialog).getByLabelText(/^Reason/),
    'Reception already sends this to every visitor.',
  );
  await userEvent.click(reject);
  expect(
    await screen.findByText('“Parking at the harbour office” is rejected'),
  ).toBeInTheDocument();
  expect(sent(api, 'POST', `/api/brain/review/${PARKING}/reject`)).toEqual({
    reason: 'Reception already sends this to every visitor.',
  });
  expect(
    screen.queryByRole('button', {
      name: 'Reject “Parking at the harbour office”',
    }),
  ).not.toBeInTheDocument();
});

test('a share someone else decided in the meantime says so in the dialog', async () => {
  renderConsole('/review', 'populated', (url, method) =>
    method === 'POST' && url.pathname.endsWith('/approve')
      ? {
          status: 409,
          body: { error: 'submission_decided', status: 'rejected' },
        }
      : undefined,
  );
  await rows();
  await userEvent.click(
    screen.getByRole('button', { name: 'Approve “Commission timing”' }),
  );
  const dialog = screen.getByRole('dialog');
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Approve' }),
  );
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    'Someone already decided this share. Reload the page to see what happened.',
  );
});

test('nothing waiting says what the page is for', async () => {
  renderConsole('/review', 'empty');
  expect(
    await screen.findByRole('heading', { name: 'Nothing to review' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText('Nothing is waiting for review.'),
  ).toBeInTheDocument();
});

test('a queue that cannot load offers to try again', async () => {
  let fail = true;
  renderConsole('/review', 'populated', (url) =>
    fail && url.pathname === '/api/brain/review'
      ? { status: 500, body: { error: 'internal' } }
      : undefined,
  );
  const error = await screen.findByRole('alert');
  expect(error).toHaveTextContent("Couldn't load the review queue");
  fail = false;
  await userEvent.click(
    within(error).getByRole('button', { name: 'Try again' }),
  );
  expect(await rows()).toHaveLength(3);
});
