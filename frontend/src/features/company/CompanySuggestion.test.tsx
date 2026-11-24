import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  CompanyView,
  ConversationModelStatus,
  ModelChoice,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import ModelPicker from '../shell/ModelPicker';

// The composer's model picker with a joined company's models (C2): their own
// group under the company's name, and the company's suggestion while no model
// is chosen.

const COMPANY = 'rowbot.example.test';

const models: ModelChoice[] = [
  {
    provider_id: 'codex',
    model_ref: 'model:codex:gpt-5.6-sol',
    label: 'GPT-5.6-Sol - ChatGPT / Codex',
    available: true,
    billing: 'subscription',
  },
  {
    provider_id: 'company',
    model_ref: 'model:company:qwen3-32b',
    label: `Qwen3 32B - ${COMPANY}`,
    available: true,
  },
  {
    provider_id: 'company',
    model_ref: 'model:company:claude-team',
    label: `Claude for the team - ${COMPANY}`,
    available: true,
  },
];

const joined: CompanyView = {
  schema_version: 1,
  state: 'joined',
  company: COMPANY,
  origin: `https://${COMPANY}`,
  name: 'Ada Lovelace',
  email: 'ada@example.test',
  joined_at: '2026-11-23T09:00:00+00:00',
  computer_name: 'Fixture laptop',
  rejoining: false,
  failure: null,
  notice: null,
  on_this_computer: true,
  model_count: 2,
  suggested_model_ref: 'model:company:qwen3-32b',
  suggested_model_name: 'Qwen3 32B',
};

function show(
  current: string | undefined,
  listed = models,
  {
    view = joined,
    status,
  }: { view?: CompanyView; status?: ConversationModelStatus | null } = {},
) {
  const company = vi.fn(async (_action?: string) => view);
  const onChoose = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <RuntimeContext.Provider
      value={{
        controller: { company } as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <ModelPicker
        models={listed}
        current={current}
        status={
          status !== undefined
            ? status
            : current
              ? { state: 'ready', local: false }
              : null
        }
        disabled={false}
        open
        onOpenChange={onOpenChange}
        onChoose={onChoose}
        onConnect={vi.fn()}
        onManage={vi.fn()}
        onThinking={vi.fn()}
        reasoning={null}
        thinkingLabel="Default"
      />
    </RuntimeContext.Provider>,
  );
  return { company, onChoose, onOpenChange };
}

it('lists the company’s models as their own group, named after the company', async () => {
  show('model:codex:gpt-5.6-sol');

  const group = await screen.findByRole('group', {
    name: new RegExp(COMPANY.replace(/\./g, '\\.')),
  });
  expect(group).toHaveTextContent('Qwen3 32B');
  expect(group).toHaveTextContent('Claude for the team');
});

it('before a model is chosen, offers the company’s suggestion and chooses it only when pressed', async () => {
  const { company, onChoose, onOpenChange } = show(undefined);

  expect(
    await screen.findByText(`${COMPANY} suggests Qwen3 32B`),
  ).toBeVisible();
  expect(company).toHaveBeenCalledWith('status');
  expect(onChoose).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Use Qwen3 32B' }));

  expect(onChoose).toHaveBeenCalledWith(
    expect.objectContaining({ model_ref: 'model:company:qwen3-32b' }),
  );
  expect(onOpenChange).toHaveBeenCalledWith(false);
});

it('says nothing once a model is chosen, and asks nothing when no company model is listed', async () => {
  const chosen = show('model:codex:gpt-5.6-sol');
  await screen.findByRole('listbox', { name: 'Models' });
  await waitFor(() => expect(chosen.company).toHaveBeenCalledWith('status'));
  expect(screen.queryByText(/suggests/)).toBeNull();

  const personal = show(undefined, [models[0]]);
  await waitFor(() =>
    expect(screen.getAllByRole('listbox', { name: 'Models' })).toHaveLength(2),
  );
  expect(screen.queryByText(/suggests/)).toBeNull();
  expect(personal.company).not.toHaveBeenCalled();
});

it('when the chosen model can’t be used (the company’s policy took it away), offers the suggestion and chooses nothing', async () => {
  const { onChoose } = show('model:openai:gpt-5', models, {
    status: {
      state: 'unavailable',
      reason: 'Your company turned off personal API keys',
      fix: 'choose',
      local: false,
    },
  });

  expect(
    await screen.findByText(`${COMPANY} suggests Qwen3 32B`),
  ).toBeVisible();
  expect(onChoose).not.toHaveBeenCalled();
});

it('locked down: one calm line with a Retry, and no suggestion', async () => {
  const locked: CompanyView = {
    ...joined,
    policy_state: 'locked_down',
    policy_reason: 'lease_expired',
  };
  const { company } = show(undefined, models, { view: locked });

  expect(
    await screen.findByText(
      `Row-Bot can’t reach ${COMPANY}, so it’s using only what’s safe on this computer until it can.`,
    ),
  ).toBeVisible();
  expect(screen.queryByText(/suggests/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(company).toHaveBeenCalledWith('check'));
});
