import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import LearningHome from './LearningHome';
import type { LearningIO } from './learning-state';
import {
  fakeIO,
  learningMethod,
  learningSnapshot,
  overnight,
} from './test-data';

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

const on = (overrides = {}) =>
  learningSnapshot({ state: 'on', method: learningMethod(), ...overrides });

it('Overview names the learning method and its month in counts, with Details', async () => {
  show(fakeIO(on()));

  const section = screen.getByRole('region', { name: 'How Row-Bot learns' });
  expect(within(section).getByText(/^Method v3 · since /)).toBeVisible();
  expect(
    within(section).getByText('Kept 4 of 17 ideas this month · 1.1M tokens'),
  ).toBeVisible();

  await userEvent.click(
    within(section).getByRole('button', {
      name: 'How Row-Bot learns: details',
    }),
  );

  const sheet = await screen.findByRole('dialog', {
    name: 'How Row-Bot learns',
  });
  expect(within(sheet).getByText('Learning method v3')).toBeVisible();
  expect(
    within(sheet).getByText(/^Looks first at mistakes you corrected/),
  ).toBeVisible();
});

it('the track record shows each method on the same new problems with its denominators', async () => {
  show(fakeIO(on()));
  await userEvent.click(
    screen.getByRole('button', { name: 'How Row-Bot learns: details' }),
  );
  const sheet = await screen.findByRole('dialog', {
    name: 'How Row-Bot learns',
  });

  const table = within(sheet).getByRole('table', {
    name: 'Ideas kept by each method on the same new problems',
  });
  const rows = within(table).getAllByRole('row');
  expect(rows.map((row) => row.textContent)).toEqual([
    'MethodIdeas kept, per 1M tokensBetter on new problems',
    'v1 (before)00 of 1 idea · 1M tokens',
    'v3 (now)21 of 2 ideas · 500K tokens',
  ]);
  expect(
    within(sheet).getByText(
      /^Compared on 31 new problems at the same budget \(up to 1\.5M tokens each\)/,
    ),
  ).toBeVisible();
  const history = within(sheet).getByRole('region', { name: 'History' });
  expect(
    within(history).getByText('v3: tries a way of working first'),
  ).toBeVisible();
  expect(
    within(history).getByText('v2 tried and dropped: no better than v1'),
  ).toBeVisible();
  expect(within(history).getByText('v1: the first method')).toBeVisible();
  const never = within(sheet).getByRole('region', {
    name: 'What it can never change',
  });
  for (const item of [
    'Your goals and limits',
    'Approvals, tools and permissions',
    'The hidden tests that judge it',
    'The rules for keeping a change',
  ])
    expect(within(never).getByText(item)).toBeVisible();
});

it('a switch shows under Overnight and Undo goes back', async () => {
  const io = fakeIO(
    on({
      overnight: overnight({
        method: 'switched',
        method_version: 3,
        method_words: 'tries a way of working first',
      }),
    }),
    {
      after: (action) =>
        action === 'undo_method'
          ? { method: learningMethod({ version: 1, can_undo: false }) }
          : {},
    },
  );
  show(io);

  const night = screen.getByRole('region', { name: 'Overnight' });
  expect(
    within(night).getByText(
      'Changed how it learns (method v3): tries a way of working first',
    ),
  ).toBeVisible();
  await userEvent.click(
    within(night).getByRole('button', { name: 'Undo learning method v3' }),
  );

  expect(io.run).toHaveBeenCalledWith('undo_method');
});

it('a method tried and not better is one quiet Overnight line', () => {
  show(
    fakeIO(on({ overnight: overnight({ method: 'kept', method_version: 3 }) })),
  );

  expect(
    screen.getByText(
      'Tried a new way of learning; it wasn’t better. Kept method v3.',
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: /Undo learning method/ }),
  ).toBeNull();
});

it('before any comparison it says when one runs, in counts', async () => {
  show(
    fakeIO(
      on({
        method: learningMethod({
          version: 1,
          since: null,
          trials: [],
          history: [],
          can_undo: false,
        }),
      }),
    ),
  );
  await userEvent.click(
    screen.getByRole('button', { name: 'How Row-Bot learns: details' }),
  );
  const sheet = await screen.findByRole('dialog', {
    name: 'How Row-Bot learns',
  });

  expect(
    within(sheet).getByText(
      'Not compared with another method yet. A comparison runs once 30 new problems have built up (12 so far).',
    ),
  ).toBeVisible();
  expect(within(sheet).queryByRole('button', { name: /^Undo/ })).toBeNull();
});

it('when the month’s limit is used up it says so, not that comparing used its share', async () => {
  show(
    fakeIO(
      on({
        spending: {
          used_tokens: 2_000_000,
          limit_tokens: 2_000_000,
          resets_on: '2026-12-01',
          model_name: 'GPT-5.6 Sol',
          limit_reached: true,
        },
        method: learningMethod({
          trials: [],
          next_trial: { state: 'limit', fresh: 41, needed: 30 },
        }),
      }),
    ),
  );
  await userEvent.click(
    screen.getByRole('button', { name: 'How Row-Bot learns: details' }),
  );
  const sheet = await screen.findByRole('dialog', {
    name: 'How Row-Bot learns',
  });

  expect(
    within(sheet).getByText(/this month’s Learning limit is used up/),
  ).toBeVisible();
  expect(within(sheet).queryByText(/share for comparing methods/)).toBeNull();
});
