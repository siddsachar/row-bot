import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import AppTemplates from './AppTemplates';
import StepApps from './StepApps';

function show(ui: React.ReactNode, controller: Record<string, unknown>) {
  const fake = {
    integrationIcons: vi.fn(async (ids: string[]) => ({
      schema_version: 1,
      items: ids.map((id) => ({
        id,
        data: 'data:image/svg+xml;base64,PHN2Zy8+',
        mono: false,
      })),
    })),
    ...controller,
  } as unknown as ClientController;
  render(
    <RuntimeContext.Provider
      value={{ controller: fake, platform: {} as ClientPlatform }}
    >
      <MemoryRouter>{ui}</MemoryRouter>
    </RuntimeContext.Provider>,
  );
}

const template = (
  id: string,
  name: string,
  app: string,
  connected: boolean,
) => ({
  id,
  name,
  description: `${name} description`,
  icon: '🔀',
  schedule_label: 'Every day at 09:00',
  apps: [
    {
      app_id: app.toLowerCase(),
      name: app,
      icon: `letter:${app[0]}`,
      connected,
    },
  ],
});

it('starts a template whose app is connected, and offers Connect for one whose app is not', async () => {
  const onCreated = vi.fn();
  const controller = {
    workflowTemplates: vi.fn(async () => ({
      schema_version: 1 as const,
      items: [
        template(
          'github_pr_digest',
          'GitHub pull-request digest',
          'GitHub',
          true,
        ),
        template('linear_triage', 'Linear triage', 'Linear', false),
      ],
    })),
    createFromWorkflowTemplate: vi.fn(async () => ({
      task_id: 'task-1',
      name: 'GitHub pull-request digest',
    })),
  };
  show(
    <AppTemplates
      load={controller.workflowTemplates}
      create={controller.createFromWorkflowTemplate}
      onCreated={onCreated}
    />,
    controller,
  );
  expect(
    await screen.findByRole('region', { name: 'Start from a template' }),
  ).toBeVisible();
  expect(screen.getByRole('link', { name: 'Connect Linear' })).toHaveAttribute(
    'href',
    '/settings/apps/linear',
  );
  expect(screen.getAllByText('Every day at 09:00 · starts off')).toHaveLength(
    2,
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Use template: GitHub pull-request digest',
    }),
  );
  await waitFor(() =>
    expect(onCreated).toHaveBeenCalledWith(
      'task-1',
      'GitHub pull-request digest',
    ),
  );
  expect(controller.createFromWorkflowTemplate).toHaveBeenCalledWith(
    'github_pr_digest',
  );
  expect(screen.getByRole('status')).toHaveTextContent('switched off');
});

it('limits a step to the apps chosen, and to none means every app', async () => {
  const onChange = vi.fn();
  const load = vi.fn(async () => [
    { id: 'mcp:github', name: 'GitHub', icon: 'letter:G' },
    { id: 'mcp:notion', name: 'Notion', icon: 'letter:N' },
  ]);
  show(<StepApps load={load} value={['mcp:github']} onChange={onChange} />, {});
  const github = await screen.findByRole('switch', {
    name: 'Use GitHub in this step',
  });
  expect(github).toBeChecked();
  fireEvent.click(
    screen.getByRole('switch', { name: 'Use Notion in this step' }),
  );
  expect(onChange).toHaveBeenLastCalledWith(['mcp:github', 'mcp:notion']);
  fireEvent.click(github);
  expect(onChange).toHaveBeenLastCalledWith(null); // None chosen: every app the workflow may use.
});
