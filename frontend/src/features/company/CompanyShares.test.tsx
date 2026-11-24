import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type {
  CompanyLock,
  CompanyShare,
  CompanySharesView,
} from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import CompanyShares, { type CompanySharesController } from './CompanyShares';

// Settings › Company's Your shares (Row-Bot for Teams, B4b): each share's
// status in words (waiting, approved and moved, its fact deleted, rejected
// with the reason), withdraw only while waiting and only after its confirm,
// and the company's lock.

const SALES = '5a000000-0000-4000-8000-000000000002';
const ENGINEERING = '5a000000-0000-4000-8000-000000000003';

function share(over: Partial<CompanyShare>): CompanyShare {
  return {
    id: 'b0000000-0000-4000-8000-000000000001',
    title: 'Pricing notes',
    text: 'Our prices go up in January.',
    source_note: 'From a chat on 20 Nov 2026',
    space_id: SALES,
    space_name: 'Sales',
    moved: false,
    status: 'pending',
    submitted_at: '2026-11-20T09:00:00+00:00',
    decided_at: null,
    reason: null,
    fact_id: null,
    ...over,
  };
}

const WAITING = share({});
const MOVED = share({
  id: 'b0000000-0000-4000-8000-000000000002',
  title: 'Deploy checklist',
  status: 'approved',
  space_id: ENGINEERING,
  space_name: 'Engineering',
  moved: true,
  decided_at: '2026-11-21T09:00:00+00:00',
  fact_id: 'c0000000-0000-4000-8000-000000000009',
});
const APPROVED = share({
  id: 'b0000000-0000-4000-8000-000000000003',
  title: 'Renewals',
  status: 'approved',
  decided_at: '2026-11-21T09:00:00+00:00',
  fact_id: 'c0000000-0000-4000-8000-000000000008',
});
const FACT_GONE = share({
  id: 'b0000000-0000-4000-8000-000000000004',
  title: 'Old office hours',
  status: 'approved',
  decided_at: '2026-11-21T09:00:00+00:00',
});
const REJECTED = share({
  id: 'b0000000-0000-4000-8000-000000000005',
  title: 'Holiday rules',
  status: 'rejected',
  decided_at: '2026-11-22T09:00:00+00:00',
  reason: 'The handbook already says this.',
});

function sharesView(
  changes: Partial<CompanySharesView> = {},
): CompanySharesView {
  return {
    schema_version: 1,
    state: 'ready',
    company: 'rowbot.example.test',
    reason: null,
    allowed: true,
    spaces: [],
    shares: [WAITING, MOVED, APPROVED, FACT_GONE, REJECTED],
    truncated: false,
    outcome: null,
    words: null,
    ...changes,
  };
}

function show(
  controller: Partial<CompanySharesController>,
  lock?: CompanyLock,
) {
  return render(
    <OverlayProvider>
      <CompanyShares
        controller={controller as CompanySharesController}
        lock={lock}
      />
    </OverlayProvider>,
  );
}

function row(title: string): HTMLElement {
  const found = screen
    .getAllByRole('listitem')
    .find((item) => within(item).queryByText(title));
  if (!found) throw new Error(`no row for ${title}`);
  return found;
}

it('shows nothing with no company joined, or outside the app', async () => {
  const companyShares = vi.fn(async () =>
    sharesView({ state: 'not_joined', company: null, shares: [] }),
  );
  const { unmount } = show({ companyShares });
  await waitFor(() => expect(companyShares).toHaveBeenCalledOnce());
  await act(async () => {});
  expect(screen.queryByText('Your shares')).toBeNull();
  unmount();

  show({});
  await act(async () => {});
  expect(screen.queryByText('Your shares')).toBeNull();
});

it('says what became of each share, a move included', async () => {
  show({ companyShares: vi.fn(async () => sharesView()) });

  expect(
    await screen.findByRole('heading', { name: 'Your shares' }),
  ).toBeVisible();
  expect(row('Pricing notes')).toHaveTextContent('Waiting for review');
  expect(row('Pricing notes')).toHaveTextContent('Sales');
  expect(row('Deploy checklist')).toHaveTextContent('Approved');
  expect(row('Deploy checklist')).toHaveTextContent(
    'Moved to Engineering, a fact there now',
  );
  expect(row('Renewals')).toHaveTextContent('A fact in Sales now');
  expect(row('Old office hours')).toHaveTextContent(
    'Its fact was deleted later',
  );
  expect(row('Holiday rules')).toHaveTextContent('Rejected');
  expect(row('Holiday rules')).toHaveTextContent(
    'Why: The handbook already says this.',
  );

  // Only a waiting share can be withdrawn.
  expect(
    within(row('Pricing notes')).getByRole('button', {
      name: 'Withdraw “Pricing notes”',
    }),
  ).toBeVisible();
  for (const title of [
    'Deploy checklist',
    'Renewals',
    'Old office hours',
    'Holiday rules',
  ])
    expect(within(row(title)).queryByRole('button')).toBeNull();
});

it('withdraws a waiting share only after its confirm', async () => {
  const withdrawCompanyShare = vi.fn(async () =>
    sharesView({
      shares: [MOVED, APPROVED, FACT_GONE, REJECTED],
      outcome: 'withdrawn',
    }),
  );
  show({
    companyShares: vi.fn(async () => sharesView()),
    withdrawCompanyShare,
  });
  const withdraw = await screen.findByRole('button', {
    name: 'Withdraw “Pricing notes”',
  });

  fireEvent.click(withdraw);
  const kept = await screen.findByRole('alertdialog', {
    name: 'Withdraw “Pricing notes”?',
  });
  expect(kept).toHaveTextContent('This deletes it. Nobody will see it.');
  fireEvent.click(within(kept).getByRole('button', { name: 'Keep it' }));
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  expect(withdrawCompanyShare).not.toHaveBeenCalled();

  fireEvent.click(
    screen.getByRole('button', { name: 'Withdraw “Pricing notes”' }),
  );
  const confirm = await screen.findByRole('alertdialog', {
    name: 'Withdraw “Pricing notes”?',
  });
  fireEvent.click(within(confirm).getByRole('button', { name: 'Withdraw' }));
  await waitFor(() =>
    expect(withdrawCompanyShare).toHaveBeenCalledWith(WAITING.id),
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    '“Pricing notes” was withdrawn.',
  );
  expect(screen.queryByText('Pricing notes')).toBeNull();
});

it('says why a share couldn’t be withdrawn', async () => {
  const words = 'It was already approved, so it can’t be withdrawn.';
  show({
    companyShares: vi.fn(async () => sharesView()),
    withdrawCompanyShare: vi.fn(async () =>
      sharesView({ outcome: 'decided', words }),
    ),
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Withdraw “Pricing notes”' }),
  );
  const confirm = await screen.findByRole('alertdialog');
  fireEvent.click(within(confirm).getByRole('button', { name: 'Withdraw' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(words);
});

it('shows the company’s lock while it turns sharing off', async () => {
  show(
    {
      companyShares: vi.fn(async () =>
        sharesView({ allowed: false, shares: [] }),
      ),
    },
    {
      setting: 'data.share_to_company',
      title: 'Allow Share to company',
      value: 'Off',
      words: 'Your company turned off Share to company',
    },
  );

  expect(
    await screen.findByText('Your company turned off Share to company'),
  ).toBeVisible();
  expect(screen.getByText('Set by your company')).toBeVisible();
  expect(screen.getByText('Nothing shared yet.')).toBeVisible();
});
