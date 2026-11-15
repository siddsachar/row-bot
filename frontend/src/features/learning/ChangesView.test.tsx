import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import LearningHome, { type LearningView } from './LearningHome';
import type { LearningIO } from './learning-state';
import {
  draftedChange,
  fakeIO,
  learningSnapshot,
  overnight,
  testedChange,
} from './test-data';

function show(io: LearningIO, view: LearningView = 'overview') {
  return render(
    <MemoryRouter>
      <OverlayProvider>
        <LearningHome
          io={io}
          view={view}
          onViewChange={vi.fn()}
          onOpenSettings={vi.fn()}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
}

const on = (overrides = {}) => learningSnapshot({ state: 'on', ...overrides });

it('a tested change waits in Needs you, and its review shows the test with and without it', () => {
  const io = fakeIO(on({ changes: [testedChange()] }));
  show(io);

  const needs = screen.getByRole('list', { name: 'Changes ready to review' });
  expect(
    within(needs).getByText('Pick documents by ID, not title'),
  ).toBeVisible();
  expect(within(needs).getByText('Lesson for document lookups')).toBeVisible();
  fireEvent.click(
    within(needs).getByRole('button', {
      name: 'Review Pick documents by ID, not title',
    }),
  );

  const sheet = screen.getByRole('dialog', {
    name: 'Pick documents by ID, not title',
  });
  const table = within(sheet).getByRole('table', {
    name: 'Tasks passed without and with this change',
  });
  expect(
    within(table).getByRole('rowheader', {
      name: 'Document lookups it hadn’t seen (12)',
    }),
  ).toBeVisible();
  expect(within(table).getAllByText('10 passed')).toHaveLength(2);
  expect(
    within(sheet).getByText(/Small test, so expect some variation\./),
  ).toBeVisible();
  expect(
    within(sheet).getByRole('button', { name: 'Use this change' }),
  ).toBeVisible();
  expect(within(sheet).getByRole('button', { name: 'Not now' })).toBeVisible();
});

it('Use this change says Now in use with Undo, and Undo takes it back out', async () => {
  const io = fakeIO(on({ changes: [testedChange()] }), {
    after: (action) =>
      action === 'use_change'
        ? { changes: [testedChange({ state: 'in_use', used_by: 'person' })] }
        : {},
  });
  io.run = vi.fn(async (action, fields = {}) => {
    const snapshot = learningSnapshot({
      ...io.snapshot,
      ...(action === 'use_change'
        ? { changes: [testedChange({ state: 'in_use', used_by: 'person' })] }
        : {}),
    });
    io.snapshot = snapshot;
    return {
      command_id: '00000000-0000-4000-8000-000000000000',
      status: 'completed' as const,
      summary: action === 'use_change' ? 'Now in use.' : 'Undone.',
      snapshot,
      signal_id: null,
      ...fields,
    };
  });
  show(io, 'changes');

  fireEvent.click(
    screen.getByRole('button', {
      name: 'Review Pick documents by ID, not title',
    }),
  );
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Use this change' }));
  });
  expect(io.run).toHaveBeenCalledWith('use_change', {
    change_id: 'proposal_ready',
  });
  expect(await screen.findByText('Now in use.')).toBeVisible();
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  });
  expect(io.run).toHaveBeenCalledWith('undo_change', {
    change_id: 'proposal_ready',
  });
});

it('Edit marks a change edited, and its review asks to test again before use', async () => {
  const io = fakeIO(
    on({
      changes: [
        testedChange({
          state: 'edited',
          text: 'Choose by document ID and say which one you picked.',
        }),
      ],
    }),
  );
  show(io, 'changes');

  const list = screen.getByRole('list', { name: 'Changes being tested' });
  expect(within(list).getByText('Edited: test again')).toBeVisible();
  fireEvent.click(
    within(list).getByRole('button', {
      name: 'Review Pick documents by ID, not title',
    }),
  );
  const sheet = screen.getByRole('dialog', {
    name: 'Pick documents by ID, not title',
  });
  expect(
    within(sheet).getByText('Edited: test again before using.'),
  ).toBeVisible();
  expect(
    within(sheet).queryByRole('button', { name: 'Use this change' }),
  ).toBeNull();
  await act(async () => {
    fireEvent.click(within(sheet).getByRole('button', { name: 'Test again' }));
  });
  expect(io.run).toHaveBeenCalledWith('test_change', {
    change_id: 'proposal_ready',
  });
});

it('Edit opens the words to change and saves them as an edit', async () => {
  const io = fakeIO(on({ changes: [testedChange()] }));
  show(io, 'changes');
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Review Pick documents by ID, not title',
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const editor = screen.getByRole('dialog', { name: 'Edit lesson' });
  fireEvent.change(
    within(editor).getByRole('textbox', { name: 'The lesson' }),
    {
      target: { value: 'Choose by ID.' },
    },
  );
  await act(async () => {
    fireEvent.click(within(editor).getByRole('button', { name: 'Save' }));
  });
  expect(io.run).toHaveBeenCalledWith('edit_change', {
    change_id: 'proposal_ready',
    text: 'Choose by ID.',
  });
});

it('Don’t suggest this again is in the … menu, beside Experiment details', async () => {
  const user = userEvent.setup();
  const io = fakeIO(on({ changes: [testedChange()] }));
  show(io, 'changes');
  await user.click(
    screen.getByRole('button', {
      name: 'Review Pick documents by ID, not title',
    }),
  );
  const sheet = screen.getByRole('dialog', {
    name: 'Pick documents by ID, not title',
  });
  await user.click(
    within(sheet).getByRole('button', { name: 'More for this change' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Experiment details' }),
  );
  expect(
    within(sheet).getByText(/\+25 points \(95% interval 8\.3 to 41\.7\)/),
  ).toBeVisible();
  await user.click(
    within(sheet).getByRole('button', { name: 'More for this change' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Don’t suggest this again' }),
  );
  expect(io.run).toHaveBeenCalledWith('dont_suggest_change', {
    change_id: 'proposal_ready',
  });
});

it('In use rows have Undo; one used by itself says so, and Overnight lists it with Undo', async () => {
  const used = testedChange({
    state: 'in_use',
    used_by: 'automatic',
    used_at: new Date(Date.now() - 3_600_000).toISOString(),
  });
  const io = fakeIO(
    on({
      changes: [used],
      overnight: overnight({ tested: 3, ready: 1, not_better: 2, used: 1 }),
    }),
  );
  const view = show(io);

  expect(
    screen.getByText('Tested 3 ideas: 1 ready, 2 not better'),
  ).toBeVisible();
  expect(
    screen.getByText(
      /^Put “Pick documents by ID, not title” into use by itself/,
    ),
  ).toBeVisible();
  view.unmount();

  show(io, 'changes');
  const list = screen.getByRole('list', { name: 'Changes in use' });
  expect(within(list).getByText('In use · by itself')).toBeVisible();
  await act(async () => {
    fireEvent.click(
      within(list).getByRole('button', {
        name: 'Undo Pick documents by ID, not title',
      }),
    );
  });
  expect(io.run).toHaveBeenCalledWith('undo_change', {
    change_id: 'proposal_ready',
  });
});

it('a change that wasn’t better is listed under Not used with why', () => {
  show(
    fakeIO(
      on({
        changes: [
          testedChange({
            state: 'not_used',
            not_used: 'not_better',
            test: {
              ...testedChange().test!,
              verdict: 'no_difference',
            },
          }),
          draftedChange({
            id: 'p_down',
            state: 'not_used',
            not_used: 'turned_down',
          }),
        ],
      }),
    ),
    'changes',
  );
  fireEvent.click(screen.getByRole('radio', { name: 'Not used' }));
  const list = screen.getByRole('list', { name: 'Changes not used' });
  expect(within(list).getByText('Not better')).toBeVisible();
  expect(within(list).getByText('Turned down')).toBeVisible();
});

it('Overview shows the test running now, in tasks done', () => {
  show(
    fakeIO(
      on({
        changes: [draftedChange({ state: 'testing' })],
        testing_now: {
          change_id: 'proposal_1',
          title: 'Use the current policy',
          done: 9,
          total: 24,
        },
      }),
    ),
  );
  expect(
    screen.getByText('Testing “Use the current policy” on 24 tasks · 9 done'),
  ).toBeVisible();
  expect(
    screen.getByRole('progressbar', {
      name: 'Testing “Use the current policy”',
    }),
  ).toBeVisible();
});

it('Lessons: one in use says how often it was used and can be turned off; a saved one can be tested', async () => {
  const io = fakeIO(on());
  io.lessons = vi.fn(async () => ({
    total: 2,
    items: [
      {
        id: 'proposal_9',
        title: 'Pick documents by ID',
        text: 'When two documents share a title, choose by ID.',
        origin: 'learning' as const,
        status: 'in_use' as const,
        kind: 'document_lookups' as const,
        used: 3,
        change_id: 'proposal_9',
        updated_at: '',
      },
      {
        id: 'mem_2',
        title: 'Shell: pip needs --user on macOS',
        text: '',
        origin: 'agent_saved' as const,
        status: 'not_tested' as const,
        kind: null,
        used: 0,
        change_id: '',
        updated_at: '',
      },
    ],
  }));
  show(io, 'lessons');
  const list = await screen.findByRole('list', { name: 'Lessons' });
  expect(within(list).getByText('In use')).toBeVisible();
  expect(
    within(list).getByText('Document lookups · used 3 times this month'),
  ).toBeVisible();
  expect(within(list).getByText('Not tested')).toBeVisible();
  await act(async () => {
    fireEvent.click(
      within(list).getByRole('button', {
        name: 'Test Shell: pip needs --user on macOS',
      }),
    );
  });
  expect(io.run).toHaveBeenCalledWith('test_lesson', { change_id: 'mem_2' });
  const user = userEvent.setup();
  await user.click(
    within(list).getByRole('button', { name: 'More for Pick documents by ID' }),
  );
  await user.click(await screen.findByRole('menuitem', { name: 'Turn off' }));
  expect(io.run).toHaveBeenCalledWith('undo_change', {
    change_id: 'proposal_9',
  });
});

it('Lessons: one whose memory was removed elsewhere says so and offers no Turn off', async () => {
  const io = fakeIO(on());
  io.lessons = vi.fn(async () => ({
    total: 1,
    items: [
      {
        id: 'proposal_9',
        title: 'Pick documents by ID',
        text: 'When two documents share a title, choose by ID.',
        origin: 'learning' as const,
        status: 'removed' as const,
        kind: 'document_lookups' as const,
        used: 0,
        change_id: 'proposal_9',
        updated_at: '',
      },
    ],
  }));
  show(io, 'lessons');
  const list = await screen.findByRole('list', { name: 'Lessons' });
  expect(within(list).getByText('Memory removed')).toBeVisible();
  expect(
    within(list).getByText(
      'Document lookups · its memory was removed, so Row-Bot doesn’t use it',
    ),
  ).toBeVisible();
  const user = userEvent.setup();
  await user.click(
    within(list).getByRole('button', { name: 'More for Pick documents by ID' }),
  );
  expect(await screen.findByRole('menuitem', { name: 'Delete' })).toBeVisible();
  expect(screen.queryByRole('menuitem', { name: 'Turn off' })).toBeNull();
});
