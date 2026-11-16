import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type {
  ApprovalRuleDryRun,
  ApprovalRuleDryRunRequest,
  ApprovalRuleUpdate,
  ApprovalRuleView,
} from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import ApprovalRulesSettings, { type RulesIO } from './ApprovalRulesSettings';

const yesterday = new Date(Date.now() - 26 * 3600 * 1000).toISOString();

function rule(overrides: Partial<ApprovalRuleView> = {}): ApprovalRuleView {
  return {
    rule_id: '0123456789abcdef',
    kind: 'command',
    words: 'Always allow `npm test` in row-bot',
    app_name: '',
    tool_title: '',
    command: 'npm test',
    folder_name: 'row-bot',
    enabled: true,
    created_at: yesterday,
    last_used_at: yesterday,
    uses: 12,
    ...overrides,
  };
}

const app = rule({
  rule_id: 'fedcba9876543210',
  kind: 'app',
  words: 'Always allow Notion › Create a page',
  app_name: 'Notion',
  tool_title: 'Create a page',
  command: '',
  folder_name: '',
  uses: 0,
  last_used_at: null,
});

function dry(command: string, denied = 0): ApprovalRuleDryRun {
  return {
    schema_version: 1,
    rule: {
      ...rule(),
      command,
      words: `Always allow \`${command}\` in row-bot`,
    },
    days: 30,
    total: 4 + denied,
    counts: {
      approved_by_you: 4,
      denied,
      approved_by_reviewer: 0,
      allowed_in_chat: 0,
      changed: 0,
      asked: 0,
    },
    examples: [],
  };
}

function setup(rules: ApprovalRuleView[] = [rule(), app]) {
  let current = [...rules];
  const io = {
    list: vi.fn(async () => ({
      schema_version: 1 as const,
      items: current,
      max_rules: 100,
    })),
    update: vi.fn(async (ruleId: string, body: ApprovalRuleUpdate) => {
      if (body.command === 'pip install requests')
        throw { code: 'rule_command_refused', status: 422 };
      const found = current.find((item) => item.rule_id === ruleId)!;
      const next = {
        ...found,
        ...(body.enabled === undefined || body.enabled === null
          ? {}
          : { enabled: body.enabled }),
        ...(body.command
          ? {
              command: body.command,
              words: `Always allow \`${body.command}\` in row-bot`,
            }
          : {}),
      };
      current = current.map((item) => (item.rule_id === ruleId ? next : item));
      return next;
    }),
    remove: vi.fn(async (ruleId: string) => {
      current = current.filter((item) => item.rule_id !== ruleId);
      return { schema_version: 1 as const, items: current, max_rules: 100 };
    }),
    dryRun: vi.fn(async (body: ApprovalRuleDryRunRequest) => {
      if (body.command === 'pip install requests')
        throw { code: 'rule_command_refused', status: 422 };
      return dry(body.command ?? 'npm test', 1);
    }),
  } satisfies RulesIO;
  render(
    <OverlayProvider>
      <ApprovalRulesSettings io={io} />
    </OverlayProvider>,
  );
  return io;
}

describe('Settings › System › Activity › Always allow', () => {
  it('says nothing is allowed yet, and how a rule starts', async () => {
    setup([]);
    expect(await screen.findByText('No rules yet')).toBeVisible();
    expect(screen.getByText(/choose Always allow on its card/)).toBeVisible();
    expect(
      screen.getByText(/Rules never cover sending, posting, deleting, paying/),
    ).toBeVisible();
  });

  it('lists each rule in words with its use, and switches one off', async () => {
    const io = setup();
    const row = (await screen.findByText('npm test')).closest('li')!;
    expect(row).toHaveTextContent('Always allow npm test in row-bot');
    expect(row).toHaveTextContent(/Used 12 times · last/);
    const notion = screen.getByText('Notion › Create a page').closest('li')!;
    expect(notion).toHaveTextContent('Not used yet');
    // Only a command rule has anything to edit.
    expect(
      within(notion).queryByRole('button', { name: 'Edit rule' }),
    ).not.toBeInTheDocument();

    fireEvent.click(within(row).getByRole('switch', { name: 'Rule on' }));

    await waitFor(() =>
      expect(io.update).toHaveBeenCalledWith('0123456789abcdef', {
        enabled: false,
      }),
    );
    expect(await within(row).findByText(/^Off · /)).toBeVisible();
  });

  it('deletes a rule only after you confirm', async () => {
    const io = setup();
    const row = (await screen.findByText('npm test')).closest('li')!;
    fireEvent.click(within(row).getByRole('button', { name: 'Delete rule' }));
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Delete this rule?',
    });
    expect(io.remove).not.toHaveBeenCalled();

    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Delete rule' }),
    );

    await waitFor(() =>
      expect(io.remove).toHaveBeenCalledWith('0123456789abcdef'),
    );
    await waitFor(() =>
      expect(screen.queryByText('npm test')).not.toBeInTheDocument(),
    );
  });

  it('narrows a command with its dry run, and refuses a command a rule can’t cover', async () => {
    const io = setup();
    const row = (await screen.findByText('npm test')).closest('li')!;
    fireEvent.click(within(row).getByRole('button', { name: 'Edit rule' }));
    const sheet = await screen.findByRole('dialog', { name: 'Edit rule' });
    const field = within(sheet).getByRole('textbox');
    expect(
      await within(sheet).findByText(/You said no to one of these/),
    ).toBeVisible();

    fireEvent.change(field, { target: { value: 'pip install requests' } });
    expect(
      await within(sheet).findByText(/A rule can only cover one plain command/),
    ).toBeVisible();
    expect(within(sheet).getByRole('button', { name: 'Save' })).toBeDisabled();

    fireEvent.change(field, { target: { value: 'npm test -- --run' } });
    await waitFor(() =>
      expect(io.dryRun).toHaveBeenLastCalledWith(
        { rule_id: '0123456789abcdef', command: 'npm test -- --run' },
        expect.any(AbortSignal),
      ),
    );
    await waitFor(() =>
      expect(within(sheet).getByRole('button', { name: 'Save' })).toBeEnabled(),
    );
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(io.update).toHaveBeenCalledWith('0123456789abcdef', {
        command: 'npm test -- --run',
      }),
    );
    expect(await screen.findByText('npm test -- --run')).toBeVisible();
  });
});
