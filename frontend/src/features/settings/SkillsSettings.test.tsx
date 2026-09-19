import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import SkillsSettings, {
  createSkillsSettingsSession,
  type SkillAction,
  type SkillDetail,
  type SkillPage,
  type SkillProposalPage,
  type SkillReceipt,
  type SkillReview,
  type SkillsSettingsIO,
} from './SkillsSettings';

function page(overrides: Partial<SkillPage> = {}): SkillPage {
  return {
    schema_version: 1,
    revision: 'a'.repeat(64),
    availability: 'available',
    total: 1,
    next_cursor: null,
    items: [
      {
        id: 'sample',
        display_name: 'Sample skill',
        icon: '✨',
        description: 'Saved workflow',
        source: 'user',
        version: '1.0',
        tags: ['local'],
        activation: {},
        available: true,
        pinned: false,
        editable: true,
        tool_guide: false,
        revision: 'b'.repeat(64),
        instructions_preview: 'Use approved inputs.',
        truncated: false,
      },
    ],
    ...overrides,
  };
}

const proposals: SkillProposalPage = {
  schema_version: 1,
  revision: 'missing',
  items: [],
  truncated: false,
};

function detail(): SkillDetail {
  return {
    schema_version: 1,
    library_revision: 'a'.repeat(64),
    skill: { ...page().items[0], instructions: 'Use approved inputs.' },
  };
}

function reviewed(
  action: SkillAction,
  payload: Record<string, unknown>,
): SkillReview {
  return {
    schema_version: 1,
    action,
    revision: String(payload.revision),
    target: String(payload.name ?? payload.proposal_id ?? 'imported'),
    before_revision: null,
    after: null,
    action_digest: 'c'.repeat(64),
    review_id: 'review-nonce',
  };
}

function io(changes: Partial<SkillsSettingsIO> = {}): SkillsSettingsIO {
  return {
    list: vi.fn(async () => page()),
    detail: vi.fn(async () => detail()),
    proposals: vi.fn(async () => proposals),
    review: vi.fn(async (action, payload) => reviewed(action, payload)),
    execute: vi.fn(async (command): Promise<SkillReceipt> => ({
      command_id: command.command_id,
      status: 'completed',
      action: command.type,
      skill_id: String(command.payload.name ?? 'sample'),
      revision: 'd'.repeat(64),
      code: null,
    })),
    receipt: vi.fn(async (commandId): Promise<SkillReceipt> => ({
      command_id: commandId,
      status: 'completed',
      action: 'skill.edit',
      skill_id: 'sample',
      revision: 'd'.repeat(64),
      code: null,
    })),
    ...changes,
  };
}

it('renders path-free skills as text and searches only on submission', async () => {
  const api = io({
    list: vi.fn(async () =>
      page({
        items: [
          {
            ...page().items[0],
            display_name: '<script>sentinel()</script>',
            description: '<img onerror=sentinel()>',
          },
        ],
      }),
    ),
  });
  const { container } = render(
    <SkillsSettings session={createSkillsSettingsSession()} io={api} />,
  );
  expect(
    await screen.findByText(/<script>sentinel\(\)<\/script>/),
  ).toBeVisible();
  expect(screen.getByText('<img onerror=sentinel()>')).toBeVisible();
  expect(container.querySelector('script,img')).toBeNull();
  await userEvent.type(screen.getByRole('searchbox'), '  saved  ');
  expect(api.list).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  expect(api.list).toHaveBeenLastCalledWith(
    'saved',
    undefined,
    undefined,
    expect.any(AbortSignal),
  );
  expect(container.textContent).not.toMatch(/[A-Z]:\\|\/Users\/|\/home\//);
});

it('presents compact skill rows with saved metrics, filters, and sorting', async () => {
  const original = page().items[0];
  const api = io({
    list: vi.fn(async () =>
      page({
        total: 3,
        items: [
          {
            ...original,
            id: 'zulu',
            display_name: 'Zulu bundled',
            source: 'bundled',
            pinned: true,
            editable: false,
          },
          {
            ...original,
            id: 'alpha',
            display_name: 'Alpha custom',
            available: false,
          },
          {
            ...original,
            id: 'beta',
            display_name: 'Beta custom',
          },
        ],
      }),
    ),
  });
  const { container } = render(
    <SkillsSettings session={createSkillsSettingsSession()} io={api} />,
  );
  expect(await screen.findByText('2 available shown')).toBeVisible();
  expect(screen.getByText('1 pinned shown')).toBeVisible();
  expect(screen.getByText('2 custom shown')).toBeVisible();
  const list = container.querySelector('.settings-skill-list')!;
  expect(list.querySelectorAll('.settings-skill-row')).toHaveLength(3);
  expect(list.querySelector('.surface')).toBeNull();
  expect(
    [...list.querySelectorAll('.settings-skill-summary strong')].map(
      (item) => item.textContent,
    ),
  ).toEqual(['✨ Alpha custom', '✨ Beta custom', '✨ Zulu bundled']);

  await userEvent.selectOptions(screen.getByLabelText('Filter'), 'pinned');
  expect(screen.queryByText('✨ Alpha custom')).not.toBeInTheDocument();
  expect(screen.getByText('✨ Zulu bundled')).toBeVisible();
  expect(screen.getByText(/1 shown of 3 matching skills/)).toBeVisible();

  await userEvent.selectOptions(screen.getByLabelText('Filter'), '');
  await userEvent.selectOptions(screen.getByLabelText('Sort'), 'pinned');
  expect(
    [...list.querySelectorAll('.settings-skill-summary strong')].map(
      (item) => item.textContent,
    ),
  ).toEqual(['✨ Zulu bundled', '✨ Alpha custom', '✨ Beta custom']);
});

it('reviews and applies availability without silently changing the row', async () => {
  const api = io();
  render(<SkillsSettings session={createSkillsSettingsSession()} io={api} />);
  const row = within(
    (await screen.findByText('✨ Sample skill')).closest('li')!,
  );
  fireEvent.click(row.getByRole('button', { name: 'Make unavailable' }));
  expect(await screen.findByText(/Review complete/)).toBeVisible();
  expect(row.getByText('Available')).toBeVisible();
  expect(row.getByRole('button', { name: 'Pin for new work' })).toHaveAttribute(
    'aria-pressed',
    'false',
  );
  expect(api.review).toHaveBeenCalledWith(
    'skill.preference',
    {
      revision: 'a'.repeat(64),
      name: 'sample',
      preference: 'availability',
      value: false,
    },
    expect.any(AbortSignal),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Apply reviewed change' }),
  );
  await screen.findByText('Skill change saved.');
  expect(api.execute).toHaveBeenCalledWith(
    expect.objectContaining({
      type: 'skill.preference',
      payload: expect.objectContaining({ review_id: 'review-nonce' }),
    }),
    expect.objectContaining({ action: 'skill.preference' }),
  );
});

it('creates and imports only through explicit review', async () => {
  const api = io();
  render(<SkillsSettings session={createSkillsSettingsSession()} io={api} />);
  await screen.findByText('✨ Sample skill');
  fireEvent.click(screen.getByRole('button', { name: 'Create skill' }));
  await userEvent.type(screen.getByLabelText('Skill name'), 'new_skill');
  await userEvent.type(screen.getByLabelText('Display name'), 'New skill');
  await userEvent.type(
    screen.getByLabelText('Instructions'),
    'Review this workflow.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Review create' }));
  await screen.findByText(/Review complete/);
  expect(api.execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel review' }));

  const text = '---\nname: imported\n---\nImported workflow.';
  expect(screen.getByLabelText('Import SKILL.md text')).not.toBeVisible();
  fireEvent.click(screen.getByText('Import a skill'));
  fireEvent.change(screen.getByLabelText('Import SKILL.md text'), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review import' }));
  await screen.findByText(/Review complete/);
  expect(api.review).toHaveBeenLastCalledWith(
    'skill.import',
    {
      revision: 'a'.repeat(64),
      content: text,
    },
    expect.any(AbortSignal),
  );
});

it('keeps supplemental imports and proposals closed in the resting view', async () => {
  const api = io({
    proposals: vi.fn(async () => ({
      ...proposals,
      items: [
        {
          id: 'proposal',
          type: 'create_skill' as const,
          title: 'Create a synthetic skill',
          rationale: 'Saved suggestion details',
          risk: 'low' as const,
          status: 'ready',
          preview: {},
        },
      ],
    })),
  });
  render(<SkillsSettings session={createSkillsSettingsSession()} io={api} />);
  await screen.findByText('✨ Sample skill');
  expect(screen.getByRole('button', { name: 'Create skill' })).toBeVisible();
  expect(screen.getByLabelText('Import SKILL.md text')).not.toBeVisible();
  expect(
    screen.getByText('Create a synthetic skill · ready'),
  ).not.toBeVisible();
  fireEvent.click(screen.getByText('Skill proposals'));
  expect(screen.getByText('Create a synthetic skill · ready')).toBeVisible();
});

it('keeps public discovery explicit and installation behind reviewed import', async () => {
  render(<SkillsSettings session={createSkillsSettingsSession()} io={io()} />);

  const browse = await screen.findByRole('link', { name: 'Browse skills' });
  expect(browse).toHaveAttribute('href', 'https://skills.sh/');
  expect(
    screen.getByText('Import a skill').closest('details'),
  ).not.toHaveAttribute('open');
});

it('opens, edits, duplicates, and offers destructive delete review', async () => {
  const api = io();
  render(<SkillsSettings session={createSkillsSettingsSession()} io={api} />);
  const row = within(
    (await screen.findByText('✨ Sample skill')).closest('li')!,
  );
  fireEvent.click(row.getByRole('button', { name: 'Open' }));
  expect(await screen.findByText('Use approved inputs.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Edit skill' }));
  fireEvent.change(screen.getByLabelText('Instructions'), {
    target: { value: 'Edited workflow.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review edit' }));
  await screen.findByText(/Review complete/);
  expect(api.review).toHaveBeenLastCalledWith(
    'skill.edit',
    expect.objectContaining({
      name: 'sample',
      skill_revision: 'b'.repeat(64),
      fields: expect.objectContaining({ instructions: 'Edited workflow.' }),
    }),
    expect.any(AbortSignal),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Cancel review' }));
  fireEvent.click(screen.getByRole('button', { name: 'Review duplicate' }));
  await screen.findByText(/Review complete/);
  expect(api.review).toHaveBeenLastCalledWith(
    'skill.duplicate',
    expect.objectContaining({ new_name: 'sample_custom' }),
    expect.any(AbortSignal),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Cancel review' }));
  fireEvent.click(screen.getByRole('button', { name: 'Review delete' }));
  expect(
    await screen.findByRole('button', { name: 'Apply reviewed change' }),
  ).toHaveClass('danger');
});

it('keeps the exact unconfirmed command and performs receipt-only recovery', async () => {
  const original = createSkillsSettingsSession();
  const api = io({
    execute: vi.fn(async (command): Promise<SkillReceipt> => ({
      command_id: command.command_id,
      status: 'partial',
      action: command.type,
      skill_id: 'sample',
      revision: null,
      code: 'skill_outcome_uncertain',
    })),
  });
  const first = render(<SkillsSettings session={original} io={api} />);
  const row = within(
    (await screen.findByText('✨ Sample skill')).closest('li')!,
  );
  fireEvent.click(row.getByRole('button', { name: 'Pin for new work' }));
  await screen.findByText(/Review complete/);
  fireEvent.click(
    screen.getByRole('button', { name: 'Apply reviewed change' }),
  );
  expect(
    await screen.findByText(/original change is unconfirmed/),
  ).toBeVisible();
  const command = original.getSnapshot().pending?.command;
  first.unmount();
  render(<SkillsSettings session={original} io={api} />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original receipt' }),
  );
  await screen.findByText('The original skill change is confirmed.');
  expect(api.receipt).toHaveBeenCalledWith(
    command?.command_id,
    expect.any(AbortSignal),
  );
  expect(api.execute).toHaveBeenCalledTimes(1);
});

it('redacts failures, rejects mismatched receipts, and aborts reads on disposal', async () => {
  let reject!: (reason: unknown) => void;
  const pending = new Promise<SkillPage>((_resolve, no) => {
    reject = no;
  });
  const api = io({
    list: vi.fn((_query, _source, _cursor, signal) => {
      signal.addEventListener('abort', () =>
        reject(new Error('private C:\\Users\\secret')),
      );
      return pending;
    }),
  });
  const session = createSkillsSettingsSession();
  render(<SkillsSettings session={session} io={api} />);
  expect(await screen.findByText('Loading saved skills')).toBeInTheDocument();
  act(() => session.dispose());
  expect((api.list as ReturnType<typeof vi.fn>).mock.calls[0][3].aborted).toBe(
    true,
  );
  expect(screen.queryByText(/private|Users\\secret/)).toBeNull();
  expect(screen.getByText('Sign in again to manage skills.')).toBeVisible();
});
