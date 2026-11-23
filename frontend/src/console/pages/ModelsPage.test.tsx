import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { renderConsole, sent } from '../testing';

afterEach(() => vi.unstubAllGlobals());

const ANTHROPIC = '/api/admin/endpoints/e1000000-0000-4000-8000-000000000002';

test('an endpoint key is write-only: never shown back, "Key saved" instead', async () => {
  const api = renderConsole(
    '/models?tab=endpoints',
    'populated',
    (url, method) =>
      method === 'PATCH'
        ? {
            status: 200,
            body: {
              id: 'e1000000-0000-4000-8000-000000000002',
              name: 'Anthropic',
              api: 'anthropic',
              base_url: 'https://api.anthropic.com',
              has_key: true,
              timeout_seconds: 120,
              enabled: true,
            },
          }
        : undefined,
  );
  const list = await screen.findByRole('list', { name: 'Endpoints' });
  expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent(
    'Anthropic · https://api.anthropic.com · Key saved',
  );

  await userEvent.click(screen.getByRole('button', { name: 'Edit Anthropic' }));
  const key = screen.getByLabelText(/^API key/);
  expect(key).toHaveAttribute('type', 'password');
  expect(key).toHaveValue('');
  expect(key).toHaveAttribute('placeholder', 'Key saved');
  expect(screen.getByText(/Leave this empty to keep it/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  expect(await screen.findByText('Anthropic is saved')).toBeInTheDocument();
  expect(sent(api, 'PATCH', ANTHROPIC)).toEqual({
    name: 'Anthropic',
    base_url: 'https://api.anthropic.com',
    timeout_seconds: 120,
    enabled: true,
  });

  await userEvent.click(screen.getByRole('button', { name: 'Edit Anthropic' }));
  await userEvent.type(screen.getByLabelText(/^API key/), 'sk-new-key');
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  await vi.waitFor(() =>
    expect(sent(api, 'PATCH', ANTHROPIC)).toMatchObject({
      api_key: 'sk-new-key',
    }),
  );
  expect(screen.queryByText('sk-new-key')).not.toBeInTheDocument();
});

test('moving an endpoint to another host warns that its key is not kept', async () => {
  renderConsole('/models?tab=endpoints');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Edit Anthropic' }),
  );
  const url = screen.getByLabelText(/^Base URL/);
  await userEvent.clear(url);
  await userEvent.type(url, 'https://proxy.example.com');
  expect(screen.getByText("The saved key won't be kept")).toBeInTheDocument();
});

test('adding an endpoint sends its type and key once', async () => {
  const api = renderConsole('/models?tab=endpoints', 'empty');
  await userEvent.click(
    await screen.findByRole('button', { name: 'Add endpoint' }),
  );
  await userEvent.type(screen.getByLabelText(/^Name/), 'GPU box');
  await userEvent.type(
    screen.getByLabelText(/^Base URL/),
    'http://gpu-box:8000/v1',
  );
  await userEvent.type(screen.getByLabelText(/^API key/), 'local-secret');
  await userEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', {
      name: 'Add endpoint',
    }),
  );
  expect(await screen.findByText('GPU box is saved')).toBeInTheDocument();
  expect(sent(api, 'POST', '/api/admin/endpoints')).toEqual({
    name: 'GPU box',
    base_url: 'http://gpu-box:8000/v1',
    timeout_seconds: 60,
    enabled: true,
    api_key: 'local-secret',
    api: 'openai',
  });
  expect(screen.getByRole('list', { name: 'Endpoints' })).toHaveTextContent(
    'Key saved',
  );
});

test('the catalog, budgets and usage read in plain words', async () => {
  renderConsole('/models?tab=catalog');
  const catalog = await screen.findByRole('list', { name: 'Catalog models' });
  expect(within(catalog).getAllByRole('listitem')[0]).toHaveTextContent(
    'company-chatDefaultChat · on GPU box · qwen3.5-27b · 0.00 in / 0.00 out per million tokens',
  );

  await userEvent.click(screen.getByRole('tab', { name: 'Budgets' }));
  const budgets = await screen.findByRole('list', { name: 'Budgets' });
  expect(budgets).toHaveTextContent(
    'Group · Warns at 200.00 · Stops at 300.00 a month',
  );
  expect(budgets).toHaveTextContent('Ada Lovelace');

  await userEvent.click(screen.getByRole('tab', { name: 'Usage' }));
  const table = await screen.findByRole('table');
  expect(
    within(table)
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent),
  ).toEqual(['Person', 'Calls', 'Input tokens', 'Output tokens', 'Cost']);
  expect(
    within(table).getByRole('rowheader', { name: 'Ada Lovelace' }),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole('radio', { name: 'By model' }));
  expect(
    await screen.findByRole('rowheader', { name: 'claude-sonnet' }),
  ).toBeInTheDocument();
});

test('nothing set up yet: each tab says what to do first', async () => {
  renderConsole('/models?tab=catalog', 'empty');
  expect(
    await screen.findByText(
      'Add an endpoint first, then the models people may choose from it.',
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Add model' })).toBeDisabled();
  await userEvent.click(screen.getByRole('tab', { name: 'Usage' }));
  expect(
    await screen.findByRole('heading', {
      name: 'No model calls in this period',
    }),
  ).toBeInTheDocument();
});
