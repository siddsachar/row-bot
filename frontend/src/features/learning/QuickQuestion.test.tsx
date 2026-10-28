import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import LearningHome from './LearningHome';
import type { LearningIO } from './learning-state';
import { fakeIO, learningSnapshot, overnight } from './test-data';

function show(io: LearningIO) {
  return render(
    <MemoryRouter>
      <OverlayProvider>
        <LearningHome
          io={io}
          view="overview"
          onViewChange={vi.fn()}
          onOpenSettings={vi.fn()}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
}

const QUESTION = {
  id: 'qq_1',
  question: 'When two rota policies share a name, which should I use?',
  answers: ['The newest', 'Ask me'],
  kind: 'document_lookups' as const,
};

it('Needs you asks the quick question with its two answers and an ✕ that never asks it again', async () => {
  const io = fakeIO(
    learningSnapshot({ state: 'on', quick_question: QUESTION }),
    { after: () => ({ quick_question: null }) },
  );
  show(io);

  const needs = screen.getByRole('region', { name: 'Needs you' });
  expect(
    within(needs).getByText(`Quick question: ${QUESTION.question}`),
  ).toBeVisible();
  expect(within(needs).getByText('Document lookups')).toBeVisible();
  expect(
    within(needs).getByRole('button', { name: 'Don’t ask this again' }),
  ).toBeVisible();
  await userEvent.click(
    within(needs).getByRole('button', { name: 'The newest' }),
  );

  expect(io.run).toHaveBeenCalledWith('answer_question', {
    item_id: 'qq_1',
    answer: 'The newest',
  });
});

it('Waving the question away sends no answer', async () => {
  const io = fakeIO(
    learningSnapshot({ state: 'on', quick_question: QUESTION }),
  );
  show(io);

  await userEvent.click(
    screen.getByRole('button', { name: 'Don’t ask this again' }),
  );

  expect(io.run).toHaveBeenCalledWith('dismiss_question', {
    item_id: 'qq_1',
    answer: '',
  });
});

it('Overnight says what it practised on tasks it made up, and when coding practice waits for a container', () => {
  show(
    fakeIO(
      learningSnapshot({
        state: 'on',
        overnight: overnight({
          practised: 40,
          practised_passed: 33,
          practice_kind: 'document_lookups',
          practice_needs_container: true,
        }),
      }),
    ),
  );

  const night = screen.getByRole('region', { name: 'Overnight' });
  expect(
    within(night).getByText(
      'Practised 40 document tasks it made up: 33 passed',
    ),
  ).toBeVisible();
  expect(
    within(night).getByText(
      'Coding practice needs Docker or Podman, so none was made.',
    ),
  ).toBeVisible();
});

it('Overnight names no kind when it practised more than one, and says nothing when it practised none', () => {
  const { unmount } = show(
    fakeIO(
      learningSnapshot({
        state: 'on',
        overnight: overnight({ practised: 12, practised_passed: 9 }),
      }),
    ),
  );
  expect(
    screen.getByText('Practised 12 tasks it made up: 9 passed'),
  ).toBeVisible();
  unmount();

  show(fakeIO(learningSnapshot({ state: 'on', overnight: overnight() })));
  expect(screen.queryByText(/^Practised/)).toBeNull();
});
