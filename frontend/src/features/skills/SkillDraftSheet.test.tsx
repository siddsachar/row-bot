import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { SkillOfferPage } from '../../api/types';
import SkillDraftSheet from './SkillDraftSheet';
import type { SkillOffer } from './skill-offers';

const mock = vi.hoisted(() => ({
  draftSkill: vi.fn(),
  skillOffers: vi.fn(),
  skills: vi.fn(),
  skill: vi.fn(),
  reviewSkill: vi.fn(),
  executeSkill: vi.fn(),
  writeClipboard: vi.fn(),
  notify: vi.fn(),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({
    controller: mock,
    platform: { writeClipboard: mock.writeClipboard },
  }),
}));
vi.mock('../../ui/overlays', async (original) => ({
  ...(await original<typeof import('../../ui/overlays')>()),
  useNotify: () => mock.notify,
}));

const DRAFT = {
  proposal_id: 'proposal_1',
  proposal_status: 'ready',
  name: 'monthly_expense_report',
  display_name: 'Monthly expense report',
  icon: '🧾',
  description: 'Builds a month’s expense report.',
  when_to_use: 'When asked for expenses.',
  inputs: ['the month'],
  steps: [
    'List the receipts with `workspace_list_directory`.',
    'Add them up with `calculate`.',
  ],
  checks: ['The PDF lists every receipt.'],
  instructions:
    'Builds a month’s expense report.\n\n## Steps\n1. List the receipts with `workspace_list_directory`.\n2. Add them up with `calculate`.',
  why: 'You said this comes round again. 5 steps with 4 different tools.',
  source_title: 'November expenses',
  overlaps: [],
  errors: [],
  warnings: [],
};

function offer(patch: Partial<SkillOffer> = {}): SkillOffer {
  return {
    id: 'offer_1',
    message_id: 'reply-1',
    origin: 'offered',
    status: 'offered',
    task_name: 'Monthly expense report',
    why: 'You said this comes round again.',
    created_at: '2026-11-15T09:00:00+00:00',
    proposal_id: null,
    skill_id: null,
    draft: null,
    ...patch,
  };
}
const drafted = offer({
  status: 'drafted',
  proposal_id: 'proposal_1',
  draft: DRAFT,
});
const page = (item: SkillOffer): SkillOfferPage => ({
  conversation_id: 'c',
  revision: 'r',
  items: [item],
});

function sheet(value: SkillOffer | undefined, onOpenChange = vi.fn()) {
  const onChanged = vi.fn();
  const view = render(
    <MemoryRouter>
      <SkillDraftSheet
        open
        conversationId="c"
        chatTitle="November expenses"
        messageId="reply-1"
        offer={value}
        onChanged={onChanged}
        onOpenChange={onOpenChange}
      />
    </MemoryRouter>,
  );
  return { ...view, onChanged, onOpenChange };
}

beforeEach(() => {
  vi.clearAllMocks();
  mock.skills.mockResolvedValue({ revision: 'library-1' });
  mock.reviewSkill.mockImplementation(async (action: string) => ({
    action,
    review_id: `review-${action}`,
  }));
  mock.executeSkill.mockImplementation(
    async (command: { command_id: string; type: string }) => ({
      command_id: command.command_id,
      status: 'completed',
      action: command.type,
      skill_id:
        command.type === 'skill.proposal.apply'
          ? 'monthly_expense_report'
          : null,
      revision: 'x',
      code: null,
    }),
  );
  mock.skillOffers.mockResolvedValue(page(drafted));
});

it('drafts the offer when it opens, then shows what the skill will do and why', async () => {
  let finish: (value: SkillOfferPage) => void = () => {};
  mock.draftSkill.mockReturnValue(new Promise((resolve) => (finish = resolve)));
  const { onChanged, rerender } = sheet(offer());

  expect(
    await screen.findByText('Drafting a skill from this chat…'),
  ).toBeVisible();
  expect(mock.draftSkill).toHaveBeenCalledWith(
    'c',
    'reply-1',
    'offer_1',
    expect.any(AbortSignal),
  );
  finish(page(drafted));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith(page(drafted)));
  rerender(
    <MemoryRouter>
      <SkillDraftSheet
        open
        conversationId="c"
        chatTitle="November expenses"
        messageId="reply-1"
        offer={drafted}
        onChanged={onChanged}
        onOpenChange={vi.fn()}
      />
    </MemoryRouter>,
  );
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText('🧾 Monthly expense report')).toBeVisible();
  expect(within(dialog).getByText(/From November expenses/)).toBeVisible();
  expect(
    within(dialog).getByRole('region', { name: 'Why Row-Bot offered this' }),
  ).toHaveTextContent('You said this comes round again.');
  expect(
    within(dialog).getByRole('region', { name: 'What it will do' }),
  ).toHaveTextContent('List the receipts with workspace_list_directory');
  for (const name of ['Approve', 'Edit', 'Reject'])
    expect(within(dialog).getByRole('button', { name })).toBeEnabled();
});

it('a draft that failed says so and tries again', async () => {
  mock.draftSkill.mockRejectedValueOnce({ code: 'skill_draft_refused' });
  mock.draftSkill.mockResolvedValueOnce(page(drafted));
  sheet(offer());
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Couldn’t draft a skill from this chat'.replace('’', "'"),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await waitFor(() => expect(mock.draftSkill).toHaveBeenCalledTimes(2));
});

it('an existing draft opens with no new draft, and Approve saves it with Undo', async () => {
  const { onOpenChange } = sheet(drafted);
  expect(mock.draftSkill).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }));

  await waitFor(() =>
    expect(mock.reviewSkill).toHaveBeenCalledWith('skill.proposal.apply', {
      revision: 'library-1',
      proposal_id: 'proposal_1',
      reason: '',
    }),
  );
  await waitFor(() => expect(mock.notify).toHaveBeenCalled());
  const [message, , action] = mock.notify.mock.calls[0];
  expect(message).toBe('Saved “Monthly expense report” as a skill.');
  expect(mock.executeSkill.mock.calls[0][0].payload.review_id).toBe(
    'review-skill.proposal.apply',
  );
  // The sheet closes: the reply shows it saved, and the notice can take it back.
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));

  mock.skill.mockResolvedValue({
    library_revision: 'library-2',
    skill: { revision: 'skill-1' },
  });
  action.onAction();
  await waitFor(() =>
    expect(mock.reviewSkill).toHaveBeenCalledWith('skill.delete', {
      revision: 'library-2',
      name: 'monthly_expense_report',
      skill_revision: 'skill-1',
    }),
  );
});

it('Edit keeps the person’s words: one per line, trimmed, saved as the edit', async () => {
  sheet(drafted);
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const form = screen.getByRole('form', { name: 'Edit the skill' });
  fireEvent.change(within(form).getByLabelText('Name'), {
    target: { value: 'Expense report ' },
  });
  fireEvent.change(within(form).getByLabelText(/^Steps/), {
    target: { value: 'List the receipts.\n\n  Add them up.  \nAdd them up.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

  await waitFor(() =>
    expect(mock.reviewSkill).toHaveBeenCalledWith('skill.proposal.edit', {
      revision: 'library-1',
      proposal_id: 'proposal_1',
      fields: {
        display_name: 'Expense report',
        description: DRAFT.description,
        when_to_use: DRAFT.when_to_use,
        inputs: ['the month'],
        steps: ['List the receipts.', 'Add them up.'],
        checks: DRAFT.checks,
      },
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Approve' })).toBeVisible(),
  );
});

it('an edit the server refuses stays open and says why', async () => {
  mock.reviewSkill.mockRejectedValueOnce({ code: 'skill_draft_secret' });
  sheet(drafted);
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Take out the password, key or token before saving.',
  );
  expect(screen.getByRole('form', { name: 'Edit the skill' })).toBeVisible();
});

it('Reject asks why, then closes', async () => {
  const onOpenChange = vi.fn();
  sheet(drafted, onOpenChange);
  fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
  fireEvent.click(screen.getByRole('button', { name: 'Wrong steps' }));
  await waitFor(() =>
    expect(mock.reviewSkill).toHaveBeenCalledWith('skill.proposal.reject', {
      revision: 'library-1',
      proposal_id: 'proposal_1',
      reason: 'Wrong steps',
    }),
  );
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
});
