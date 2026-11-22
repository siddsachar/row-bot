import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { TranscriptRow } from '../../api/types';
import { TranscriptMessage } from '../shell/TranscriptMessage';
import {
  SkillOfferContext,
  chatSource,
  type SkillOffer,
  type SkillOffers,
} from './skill-offers';

const controller = vi.hoisted(() => ({
  // Read aloud asks once whether its voice is installed.
  dictationCapability: async () => ({ read_aloud_reason: 'available' }),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller }),
}));

const reply = {
  id: 'assistant:checkpoint:reply-1',
  message_id: 'reply-1',
  role: 'assistant',
  blocks: [{ id: 'b', type: 'markdown', text: 'Your report is saved.' }],
} as unknown as TranscriptRow;

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

function shown(
  value: SkillOffer | undefined,
  props: { usedTools?: boolean; latest?: boolean } = {},
) {
  const offers: SkillOffers = {
    offer: (id) => (id === value?.message_id ? value : undefined),
    save: vi.fn(),
    openSkill: vi.fn(),
    dismiss: vi.fn(),
  };
  render(
    <SkillOfferContext.Provider value={offers}>
      <TranscriptMessage
        row={reply}
        conversationId="conversation-a"
        usedTools={props.usedTools ?? true}
        latest={props.latest ?? true}
      />
    </SkillOfferContext.Provider>,
  );
  return offers;
}

it('offers Save as skill on any finished reply that used tools, and none without tools', () => {
  const offers = shown(undefined);
  fireEvent.click(screen.getByRole('button', { name: 'Save as skill' }));
  expect(offers.save).toHaveBeenCalledWith('reply-1');
  // No offer from Row-Bot: no hint line, only the toolbar's button.
  expect(screen.queryByRole('group', { name: 'Save as a skill' })).toBeNull();
});

it('has no Save as skill on a reply without tools, or in an incognito chat', () => {
  shown(undefined, { usedTools: false });
  expect(screen.queryByRole('button', { name: 'Save as skill' })).toBeNull();
  // An incognito chat provides no offers at all.
  render(
    <TranscriptMessage
      row={reply}
      conversationId="conversation-b"
      usedTools
      latest
    />,
  );
  expect(screen.queryByRole('button', { name: 'Save as skill' })).toBeNull();
});

it('names the task in the offer under the newest reply, with Not now', () => {
  const value = offer();
  const offers = shown(value);
  const hint = screen.getByRole('group', { name: 'Save as a skill' });
  expect(hint).toHaveTextContent('Save “Monthly expense report” as a skill?');
  fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
  expect(offers.dismiss).toHaveBeenCalledWith(value);
  fireEvent.click(screen.getAllByRole('button', { name: 'Save as skill' })[0]);
  expect(offers.save).toHaveBeenCalledWith('reply-1');
});

it('shows the offer line only under the newest reply', () => {
  shown(offer(), { latest: false });
  expect(screen.queryByRole('group', { name: 'Save as a skill' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Save as skill' })).toBeVisible();
});

it('reopens a draft, and a saved skill opens in Settings › Skills', () => {
  shown(offer({ status: 'drafted', proposal_id: 'proposal_1' }));
  expect(
    screen.getByRole('button', { name: 'Open skill draft' }),
  ).toBeVisible();
  expect(screen.queryByRole('group', { name: 'Save as a skill' })).toBeNull();
});

it('marks a saved skill and opens it', () => {
  const offers = shown(
    offer({ status: 'saved', skill_id: 'monthly_expense_report' }),
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Saved as a skill: Monthly expense report',
    }),
  );
  expect(offers.openSkill).toHaveBeenCalledWith('monthly_expense_report');
});

it('reads where a proposal came from only for drafts from a chat', () => {
  expect(
    chatSource({
      origin: 'skill_offer',
      source: { conversation_id: 'c', message_id: 'm', title: 'Expenses' },
    }),
  ).toEqual({ conversation_id: 'c', message_id: 'm', title: 'Expenses' });
  expect(
    chatSource({ source: { conversation_id: 'c', message_id: 'm' } }),
  ).toBeNull();
  expect(chatSource({ origin: 'skill_offer' })).toBeNull();
});
