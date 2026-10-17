import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import type { LearningComparison, LearningSnapshot } from '../../api/types';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import {
  AdvancedSection,
  ComparisonTable,
  PersonalModelNeeds,
  UsesSection,
  VersionReview,
  VersionsSection,
} from './PersonalModelUse';
import {
  advancedState,
  comparedModel,
  comparisonState,
  fakeIO,
  learningSnapshot,
  noDifferenceComparison,
  trialUse,
} from './test-data';

afterEach(() => vi.unstubAllGlobals());

function wrap(children: ReactNode) {
  return render(
    <RuntimeContext.Provider
      value={{ controller: {} as never, platform: createFakePlatform() }}
    >
      <OverlayProvider>{children}</OverlayProvider>
    </RuntimeContext.Provider>,
  );
}

function phone() {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('max-width: 767px'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

function onSnapshot(overrides: Partial<LearningSnapshot> = {}) {
  return fakeIO(
    learningSnapshot({
      state: 'on',
      personal_model: comparedModel(),
      ...overrides,
    }),
  );
}

it('shows the comparison as a table with real headers and every count with its denominator', () => {
  wrap(<ComparisonTable comparison={comparisonState()} />);
  const table = screen.getByRole('table', {
    name: 'Tasks passed by each model, out of the number in brackets',
  });
  expect(
    within(table)
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent),
  ).toEqual(['Kind of work', 'GPT-6.1 Sol', 'Starting model', 'v1 (new)']);
  const lookups = within(table).getByRole('rowheader', {
    name: /^Document lookups \(12\)/,
  });
  expect(lookups).toHaveTextContent('Better');
  const row = lookups.closest('tr')!;
  expect(
    within(row)
      .getAllByRole('cell')
      .map((cell) => cell.textContent),
  ).toEqual(['11', '6', '10']);
  // A task an arm couldn't run (a provider error) shows its own denominator.
  const transforms = within(table)
    .getByRole('rowheader', { name: /^Structured transforms \(10\)/ })
    .closest('tr')!;
  expect(within(transforms).getAllByRole('cell')[2]).toHaveTextContent(
    '7 of 9',
  );
  expect(transforms).toHaveTextContent('No clear difference');
  const time = within(table)
    .getByRole('rowheader', { name: 'Time per task' })
    .closest('tr')!;
  expect(
    within(time)
      .getAllByRole('cell')
      .map((cell) => cell.textContent),
  ).toEqual(['22 s', '6.2 s', '4.8 s']);
  const cloud = within(table)
    .getByRole('rowheader', { name: 'Cloud tokens per task' })
    .closest('tr')!;
  expect(
    within(cloud)
      .getAllByRole('cell')
      .map((cell) => cell.textContent),
  ).toEqual(['18K', '0', '0']);
});

it('shows the comparison as labelled rows on a phone, never a squeezed table', () => {
  phone();
  wrap(<ComparisonTable comparison={comparisonState()} />);
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.getByText(/^Document lookups \(12\)/)).toHaveTextContent(
    'Document lookups (12) · Better',
  );
  expect(
    screen.getByText(
      'GPT-6.1 Sol: 11 passed · Starting model: 6 passed · v1 (new): 10 passed',
    ),
  ).toBeVisible();
  expect(
    screen.getByText(
      'GPT-6.1 Sol: 9 passed · Starting model: 7 passed · v1 (new): 7 of 9 passed',
    ),
  ).toBeVisible();
});

it('reviews v1 in the shared review sheet: use it for one kind of work, with Experiment details', async () => {
  const user = userEvent.setup();
  const io = onSnapshot();
  const closed = vi.fn();
  wrap(
    <VersionReview
      open
      onOpenChange={closed}
      io={io}
      model={comparedModel()}
      comparison={comparisonState()}
    />,
  );
  const sheet = await screen.findByRole('dialog', {
    name: 'Personal model v1',
  });
  expect(within(sheet).getByRole('region', { name: 'Why' })).toHaveTextContent(
    'Compared with the starting model it was trained from and your main model',
  );
  expect(within(sheet).getByRole('table')).toBeVisible();
  expect(sheet).toHaveTextContent(
    'Coding tasks need Docker or Podman, so they were left out.',
  );
  expect(sheet).toHaveTextContent('GPT-6.1 Sol’s results were reused');

  await user.click(
    within(sheet).getByRole('button', { name: 'More for Personal model v1' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Experiment details' }),
  );
  const details = within(sheet).getByRole('region', {
    name: 'Experiment details',
  });
  for (const part of ['Data', 'Method', 'Versions', 'Every run', 'Files'])
    expect(within(details).getByRole('region', { name: part })).toBeVisible();
  expect(details).toHaveTextContent(
    '154 examples of your work from 41 conversations; 17 newer examples held back to check it.',
  );
  expect(details).toHaveTextContent(
    'Training on answers from OpenAI, Anthropic or Google models may be limited by their terms.',
  );
  expect(details).toHaveTextContent(
    'Tasks it hadn’t seen, v1 (new): 16 of 22 passed',
  );
  expect(details).toHaveTextContent(
    'Every model had the same budget: 150K tokens a task.',
  );
  // File names only here (and in Export…), never on a main screen (O54).
  expect(
    within(details).getByText('row-bot-personal-v1-Q8_0.gguf'),
  ).toBeVisible();

  await user.click(
    within(sheet).getByRole('button', {
      name: 'Use v1 for document lookups',
    }),
  );
  expect(io.run).toHaveBeenCalledWith('use_version', {
    version: 1,
    kind: 'document_lookups',
  });
  expect(closed).toHaveBeenCalledWith(false);
});

it('says plainly when v1 is no better, and offers nothing to use', async () => {
  const user = userEvent.setup();
  const io = onSnapshot();
  wrap(
    <VersionReview
      open
      onOpenChange={vi.fn()}
      io={io}
      model={comparedModel()}
      comparison={noDifferenceComparison()}
    />,
  );
  const sheet = await screen.findByRole('dialog', {
    name: 'Personal model v1',
  });
  expect(sheet).toHaveTextContent(
    'No clear difference from the starting model. Not used.',
  );
  expect(within(sheet).queryByRole('button', { name: /^Use v1/ })).toBeNull();
  await user.click(within(sheet).getByRole('button', { name: 'Close' }));
  expect(io.run).toHaveBeenCalledWith('review_version', { version: 1 });
});

it('follows a comparison while it runs, with Stop', async () => {
  const user = userEvent.setup();
  const comparison: LearningComparison = comparisonState({
    state: 'running',
    done: 12,
    total: 96,
    rows: [],
  });
  const io = onSnapshot();
  wrap(
    <VersionsSection
      io={io}
      model={comparedModel({ comparison })}
      onReview={vi.fn()}
    />,
  );
  const status = screen.getByRole('status');
  expect(status).toHaveTextContent(
    'Comparing v1 with GPT-6.1 Sol and its starting model · 12 of 96 tasks',
  );
  expect(
    within(status).getByRole('progressbar', { name: 'Comparing v1' }),
  ).toHaveAttribute('value', '12');
  expect(status).toHaveTextContent(
    'Stops while you use Row-Bot and goes on later.',
  );
  await user.click(screen.getByRole('button', { name: 'Stop' }));
  expect(io.run).toHaveBeenCalledWith('stop_compare', {});
});

it('asks before using v1 (D24): Use v1 for document lookups starts a trial, with Undo on its notice', async () => {
  const user = userEvent.setup();
  const io = onSnapshot();
  wrap(<VersionsSection io={io} model={comparedModel()} onReview={vi.fn()} />);
  const section = screen.getByRole('region', { name: 'Versions' });
  expect(within(section).getByRole('table')).toBeVisible();
  await user.click(
    within(section).getByRole('button', {
      name: 'Use v1 for document lookups',
    }),
  );
  expect(io.run).toHaveBeenCalledWith('use_version', {
    version: 1,
    kind: 'document_lookups',
  });
  // The notice's Undo goes back at once.
  const undo = await screen.findByRole('button', { name: 'Undo' });
  await act(async () => fireEvent.click(undo));
  expect(io.run).toHaveBeenLastCalledWith('undo_use', {
    kind: 'document_lookups',
  });
});

it('exports v1 to a folder, listing the files it saves', async () => {
  const user = userEvent.setup();
  const io = onSnapshot();
  wrap(<VersionsSection io={io} model={comparedModel()} onReview={vi.fn()} />);
  await user.click(screen.getByRole('button', { name: 'More for v1' }));
  await user.click(await screen.findByRole('menuitem', { name: 'Export…' }));
  const dialog = await screen.findByRole('dialog', { name: 'Export v1' });
  const folder = within(dialog).getByRole('textbox', { name: 'Folder' });
  expect(folder).toHaveValue('C:\\Users\\me\\Documents');
  expect(dialog).toHaveTextContent(
    'A new folder named row-bot-personal-v1 is made inside it.',
  );
  expect(await within(dialog).findByText('MODEL_CARD.md')).toBeVisible();
  expect(dialog).toHaveTextContent(/Files \(2\.1\sGB in all\)/);
  await user.clear(folder);
  expect(within(dialog).getByRole('button', { name: 'Export' })).toBeDisabled();
  await user.type(folder, 'D:\\Models');
  await user.click(within(dialog).getByRole('button', { name: 'Export' }));
  expect(io.run).toHaveBeenCalledWith('export_version', {
    version: 1,
    folder: 'D:\\Models',
  });
});

it('deletes a version only after saying what goes back to the main model', async () => {
  const user = userEvent.setup();
  const io = onSnapshot();
  wrap(
    <VersionsSection
      io={io}
      model={comparedModel({ uses: [trialUse()] })}
      onReview={vi.fn()}
    />,
  );
  await user.click(screen.getByRole('button', { name: 'More for v1' }));
  await user.click(await screen.findByRole('menuitem', { name: 'Delete v1…' }));
  const dialog = await screen.findByRole('dialog', { name: 'Delete v1?' });
  expect(dialog).toHaveAccessibleDescription(
    'Document lookups go back to your main model at once.',
  );
  expect(dialog).toHaveTextContent(/Its files \(2\sGB\) are deleted/);
  await user.click(within(dialog).getByRole('button', { name: 'Delete v1' }));
  expect(io.run).toHaveBeenCalledWith('delete_version', { version: 1 });
});

it('shows a trial with its numbers, Keep when it is due and Undo at once', async () => {
  const user = userEvent.setup();
  const io = onSnapshot();
  wrap(
    <UsesSection
      io={io}
      model={comparedModel({ uses: [trialUse()] })}
      mainName="GPT-6.1 Sol"
    />,
  );
  const section = screen.getByRole('region', { name: 'In use for' });
  expect(section).toHaveTextContent('Document lookups · v1');
  // The date in the reader's own format (CI is en-US).
  expect(section).toHaveTextContent(
    /On trial since (13 Oct|Oct 13) · 18 requests: 17 answered, 1 finished by GPT-6\.1 Sol/,
  );
  await user.click(within(section).getByRole('button', { name: 'Keep' }));
  expect(io.run).toHaveBeenCalledWith('keep_version', {
    kind: 'document_lookups',
  });
  await user.click(
    within(section).getByRole('button', {
      name: 'Undo v1 for document lookups',
    }),
  );
  expect(io.run).toHaveBeenLastCalledWith('undo_use', {
    kind: 'document_lookups',
  });
});

it('says a kept version is in use, with Undo and no Keep', () => {
  wrap(
    <UsesSection
      io={onSnapshot()}
      model={comparedModel({
        uses: [trialUse({ state: 'in_use', keep_due: false })],
      })}
      mainName="GPT-6.1 Sol"
    />,
  );
  const section = screen.getByRole('region', { name: 'In use for' });
  expect(section).toHaveTextContent(/In use since (13 Oct|Oct 13)/);
  expect(within(section).queryByRole('button', { name: 'Keep' })).toBeNull();
  expect(
    within(section).getByRole('button', {
      name: 'Undo v1 for document lookups',
    }),
  ).toBeVisible();
});

it('lists what needs the person: review a comparison, keep a trial, retrain without a deleted chat', async () => {
  const user = userEvent.setup();
  const io = onSnapshot({
    personal_model: comparedModel({ uses: [trialUse()] }),
    needs_you: [
      {
        id: 'compare:v1',
        type: 'compare',
        version: 1,
        kind: null,
        title: 'Personal model v1 is ready to compare',
      },
      {
        id: 'keep:document_lookups',
        type: 'keep',
        version: 1,
        kind: 'document_lookups',
        title: 'Keep v1 for document lookups?',
      },
      {
        id: 'retrain:v1',
        type: 'retrain',
        version: 1,
        kind: null,
        title: 'A deleted chat was used to train v1.',
      },
    ],
  });
  const review = vi.fn();
  wrap(<PersonalModelNeeds io={io} onReview={review} />);
  const [compare, keep, retrain] = screen.getAllByRole('listitem');
  await user.click(within(compare).getByRole('button', { name: 'Review' }));
  expect(review).toHaveBeenCalledWith(1);

  expect(keep).toHaveTextContent('Keep v1 for document lookups?');
  expect(keep).toHaveTextContent('18 requests: 17 answered, 1 finished by');
  await user.click(within(keep).getByRole('button', { name: 'Not now' }));
  expect(io.run).toHaveBeenLastCalledWith('keep_not_now', {
    kind: 'document_lookups',
  });

  expect(retrain).toHaveTextContent(
    'A deleted chat was used to train v1.Deleting a chat doesn’t remove what a model already learned from it.',
  );
  await user.click(
    within(retrain).getByRole('button', { name: 'Retrain without it' }),
  );
  expect(io.run).toHaveBeenLastCalledWith('retrain_without', { version: 1 });
  await user.click(within(retrain).getByRole('button', { name: 'Not now' }));
  expect(io.run).toHaveBeenLastCalledWith('dismiss', { item_id: 'retrain:v1' });
});

it('fills Advanced with the recommended values, saves a change and resets to recommended', async () => {
  const user = userEvent.setup();
  const io = onSnapshot();
  const { rerender } = wrap(
    <AdvancedSection io={io} advanced={advancedState()} />,
  );
  await user.click(screen.getByText('Advanced'));
  const rank = screen.getByRole('combobox', { name: 'LoRA rank' });
  expect(rank).toHaveValue('16');
  expect(
    within(rank).getByRole('option', { name: '16 (recommended)' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('combobox', { name: 'Context length' }),
  ).toBeDisabled();
  expect(screen.getByText('The most this graphics card fits.')).toBeVisible();
  expect(screen.getByRole('combobox', { name: 'Method' })).toBeDisabled();
  expect(screen.getByText('Corrections: not available yet')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Reset to recommended' }),
  ).toBeDisabled();
  const save = screen.getByRole('button', { name: 'Save' });
  expect(save).toBeDisabled();
  await user.selectOptions(rank, '32');
  await user.click(save);
  expect(io.run).toHaveBeenCalledWith('save_advanced', {
    advanced: expect.objectContaining({ rank: 32, epochs: 1 }),
  });

  const changed = advancedState({
    values: { ...advancedState().values, rank: 32 },
    changed: true,
  });
  rerender(
    <RuntimeContext.Provider
      value={{ controller: {} as never, platform: createFakePlatform() }}
    >
      <OverlayProvider>
        <AdvancedSection io={io} advanced={changed} />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
  await user.click(
    screen.getByRole('button', { name: 'Reset to recommended' }),
  );
  expect(io.run).toHaveBeenLastCalledWith('reset_advanced', {});
});
