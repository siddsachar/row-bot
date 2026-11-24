import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { CompanySharesView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import { ShareDialog, titleFrom, type ShareController } from './ShareToCompany';
import { chatNoteWords } from './words';

// Share to company's dialog (Row-Bot for Teams, B4b): who sees it first, the
// space, the title and text prefilled from the reply, the counter and its
// limit, the note, Send for review, and the plain words of a refusal.

const SALES = {
  id: '5a000000-0000-4000-8000-000000000002',
  name: 'Sales',
  kind: 'group',
  is_owner: true,
} as const;
const COMPANY_SPACE = {
  id: '5a000000-0000-4000-8000-000000000001',
  name: 'Company',
  kind: 'company',
  is_owner: false,
} as const;
const TODAY = new Date('2026-11-24T12:00:00Z');
const REPLY =
  '## Renewal timing\nOrion Foods renews its contract every March.\nAsk early.';

function sharesView(
  changes: Partial<CompanySharesView> = {},
): CompanySharesView {
  return {
    schema_version: 1,
    state: 'ready',
    company: 'rowbot.example.test',
    reason: null,
    allowed: true,
    spaces: [SALES, COMPANY_SPACE],
    shares: [],
    truncated: false,
    outcome: null,
    words: null,
    ...changes,
  };
}

function Where() {
  const location = useLocation();
  return (
    <output aria-label="Where">{location.pathname + location.hash}</output>
  );
}

function show({
  text = REPLY,
  listed = sharesView(),
  answer = sharesView({ outcome: 'sent' }),
}: {
  text?: string;
  listed?: CompanySharesView;
  answer?: CompanySharesView;
} = {}) {
  const companyShares = vi.fn(async () => listed);
  const shareToCompany = vi.fn(async () => answer);
  const onClose = vi.fn();
  const controller = {
    company: vi.fn(),
    companyShares,
    shareToCompany,
  } as unknown as ShareController;
  render(
    <MemoryRouter initialEntries={['/conversations/chat-1']}>
      <OverlayProvider>
        <ShareDialog
          controller={controller}
          text={text}
          onClose={onClose}
          now={TODAY}
        />
        <Where />
      </OverlayProvider>
    </MemoryRouter>,
  );
  return { companyShares, shareToCompany, onClose };
}

it('says first who sees it, and prefills the title, text and note from the reply', async () => {
  const { companyShares } = show();

  const review = await screen.findByText(
    'Owners of Sales review this before anyone else sees it.',
  );
  expect(companyShares).toHaveBeenCalledWith('pending');
  const form = review.closest('form')!;
  expect(form.firstElementChild).toBe(review);
  expect(screen.getByLabelText('Space')).toHaveValue(SALES.id);
  expect(screen.getByLabelText('Title')).toHaveValue('Renewal timing');
  expect(screen.getByLabelText(/^Text/)).toHaveValue(REPLY);
  expect(screen.getByLabelText(/^Note/)).toHaveValue(chatNoteWords(TODAY));
  expect(chatNoteWords(TODAY)).toMatch(/^From a chat on .*2026/);
  expect(screen.getByText(`${REPLY.length} of 8,000 characters`)).toBeVisible();

  // Another space: the words follow it.
  fireEvent.change(screen.getByLabelText('Space'), {
    target: { value: COMPANY_SPACE.id },
  });
  expect(
    screen.getByText(
      'Owners of Company review this before anyone else sees it.',
    ),
  ).toBeVisible();
});

it('counts the text and sends nothing over 8,000 characters', async () => {
  show({ text: 'x'.repeat(8001) });

  expect(
    await screen.findByText('Shorten this by 1 character to send it.'),
  ).toBeVisible();
  const send = screen.getByRole('button', { name: 'Send for review' });
  expect(send).toBeDisabled();
  expect(screen.getByLabelText(/^Text/)).toHaveAttribute(
    'aria-invalid',
    'true',
  );

  fireEvent.change(screen.getByLabelText(/^Text/), {
    target: { value: 'x'.repeat(8003) },
  });
  expect(
    screen.getByText('Shorten this by 3 characters to send it.'),
  ).toBeVisible();
  fireEvent.change(screen.getByLabelText(/^Text/), {
    target: { value: 'Short.' },
  });
  expect(screen.getByText('6 of 8,000 characters')).toBeVisible();
  expect(send).toBeEnabled();

  // A title is needed too.
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: ' ' } });
  expect(send).toBeDisabled();
});

it('sends it for review, then says where with a way to Your shares', async () => {
  const { shareToCompany, onClose } = show();
  await screen.findByText(
    'Owners of Sales review this before anyone else sees it.',
  );
  fireEvent.change(screen.getByLabelText('Title'), {
    target: { value: '  Renewals  ' },
  });

  fireEvent.click(screen.getByRole('button', { name: 'Send for review' }));

  await waitFor(() =>
    expect(shareToCompany).toHaveBeenCalledWith(SALES.id, {
      title: 'Renewals',
      text: REPLY,
      source_note: chatNoteWords(TODAY),
    }),
  );
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(await screen.findByText('Sent to Sales for review')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Your shares' }));
  await waitFor(() =>
    expect(screen.getByLabelText('Where')).toHaveTextContent(
      '/settings/company#your-shares',
    ),
  );
});

it('says a refusal in plain words and keeps what was written', async () => {
  const words =
    'You have 20 shares waiting for review, the most rowbot.example.test allows. Withdraw one, or wait until its owners review some.';
  const { onClose } = show({
    answer: sharesView({ outcome: 'too_many_pending', words }),
  });
  await screen.findByText(
    'Owners of Sales review this before anyone else sees it.',
  );

  fireEvent.click(screen.getByRole('button', { name: 'Send for review' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(words);
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByLabelText(/^Text/)).toHaveValue(REPLY);
});

it('says why it can’t share now, with no Send', async () => {
  show({ listed: sharesView({ allowed: false, spaces: [SALES] }) });
  expect(
    await screen.findByText('Your company turned off Share to company.'),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Send for review' })).toBeNull();
});

it('takes a share’s first title from the reply’s first line', () => {
  expect(titleFrom('## Renewal timing\nMore')).toBe('Renewal timing');
  expect(titleFrom('\n\n  - **Bold** point\nMore')).toBe('Bold point');
  expect(titleFrom('1. First step')).toBe('First step');
  expect(titleFrom('2026 plan for `atlas`')).toBe('2026 plan for atlas');
  expect(Array.from(titleFrom('word '.repeat(40)))).toHaveLength(79);
});
