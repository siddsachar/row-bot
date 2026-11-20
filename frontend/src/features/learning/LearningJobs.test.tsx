import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { LearningJob } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import LearningHome from './LearningHome';
import { fakeIO, learningMethod, learningSnapshot } from './test-data';

async function openSheet(jobs: LearningJob[]) {
  render(
    <MemoryRouter>
      <OverlayProvider>
        <LearningHome
          io={fakeIO(
            learningSnapshot({ state: 'on', method: learningMethod({ jobs }) }),
          )}
          view="overview"
          onViewChange={vi.fn()}
          onOpenSettings={vi.fn()}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    screen.getByRole('button', { name: 'How Row-Bot learns: details' }),
  );
  return screen.findByRole('dialog', { name: 'How Row-Bot learns' });
}

const DRAFTING: LearningJob = {
  job: 'drafting',
  by: 'personal',
  version: 3,
  main_model: 'GPT-6.1 Sol',
  since: '2026-10-28T02:10:00+00:00',
  reason: '',
  last: {
    at: '2026-10-28T02:10:00+00:00',
    version: 3,
    asked: 24,
    personal_passed: 22,
    main_passed: 23,
    verdict: 'qualified',
  },
  tokens: 120_000,
  seconds: 360,
};

it('Jobs says who does each job, its latest test with its denominators, and that it never judges itself', async () => {
  const sheet = await openSheet([
    DRAFTING,
    {
      job: 'practice',
      by: 'main',
      version: null,
      main_model: 'GPT-6.1 Sol',
      since: '2026-10-28T02:20:00+00:00',
      reason: 'not_matched',
      last: {
        at: '2026-10-28T02:20:00+00:00',
        version: 3,
        asked: 40,
        personal_passed: 28,
        main_passed: 36,
        verdict: 'not_matched',
      },
      tokens: 0,
      seconds: 0,
    },
  ]);

  const jobs = within(sheet).getByRole('region', {
    name: 'Jobs your personal model does',
  });
  expect(within(jobs).getByText('Drafting changes')).toBeVisible();
  expect(within(jobs).getByText(/^Personal model v3 · since /)).toBeVisible();
  expect(
    within(jobs).getByText(
      /Passed its test \(.+\): 22 of 24 usable drafts for v3, 23 for GPT-6\.1 Sol\. This month: 120K tokens, 6 min on the graphics card\./,
    ),
  ).toBeVisible();
  expect(within(jobs).getByText('Making practice tasks')).toBeVisible();
  expect(
    within(jobs).getByText(
      /^Back with GPT-6\.1 Sol: its last test wasn’t close enough\. Not yet \(.+\): 28 of 40 practice tasks kept for v3, 36 for GPT-6\.1 Sol\. It needs to come within 5 points of GPT-6\.1 Sol\.$/,
    ),
  ).toBeVisible();
  expect(
    within(jobs).getByText(
      'It takes over a job only after passing that job’s own test against GPT-6.1 Sol, and it never judges its own next version.',
    ),
  ).toBeVisible();
});

it('without the jobs switch the sheet has no Jobs section', async () => {
  const sheet = await openSheet([]);

  expect(
    within(sheet).queryByRole('region', {
      name: 'Jobs your personal model does',
    }),
  ).toBeNull();
});
