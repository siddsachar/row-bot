import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { InsightsSnapshot } from '../../api/types';
import InsightsHome from './InsightsHome';

const snapshot: InsightsSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  curator_report: null,
  items: [
    {
      id: 'ins-test',
      title: 'Synthetic finding',
      body: 'A local fake observation.',
      suggestion: 'Inspect the proposal.',
      category: 'skill_proposal',
      severity: 'medium',
      status: 'new',
      proposals: [
        {
          id: 'proposal-test',
          title: 'Improve a skill',
          proposal_type: 'create_skill',
          status: 'ready',
          risk: 'low',
          rationale: 'The synthetic skill is missing.',
          verification_plan: 'Check the saved skill.',
          preview: '{"name":"example"}',
          open_thread_id: '',
          feedback_body: '',
          support_url: '',
        },
      ],
    },
  ],
};

beforeEach(() => sessionStorage.clear());

it('shows a proposal preview and applies only after a click', async () => {
  const insights = vi.fn().mockResolvedValue(snapshot);
  const executeInsight = vi.fn().mockImplementation(async (command) => ({
    command_id: command.command_id,
    status: 'completed',
    summary: 'Proposal applied.',
    snapshot,
  }));
  const controller = {
    insights,
    executeInsight,
  } as unknown as ClientController;
  render(<InsightsHome controller={controller} />);
  await screen.findByText('Synthetic finding');
  expect(executeInsight).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText(/Improve a skill/));
  expect(screen.getByText('{"name":"example"}')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Apply proposal' }));
  await waitFor(() => expect(executeInsight).toHaveBeenCalledTimes(1));
  expect(executeInsight.mock.calls[0][0]).toMatchObject({
    action: 'apply',
    proposal_id: 'proposal-test',
    revision: snapshot.revision,
  });
});

it('shows feedback actions only for the applicable proposal state', async () => {
  const feedback = structuredClone(snapshot);
  feedback.items[0].proposals[0] = {
    ...feedback.items[0].proposals[0],
    proposal_type: 'send_feedback',
    feedback_body: 'Synthetic redacted report',
    support_url: 'https://example.test/support',
  };
  const controller = {
    insights: vi.fn().mockResolvedValue(feedback),
  } as unknown as ClientController;
  const { rerender } = render(<InsightsHome controller={controller} />);
  await screen.findByText('Synthetic finding');
  fireEvent.click(screen.getByText(/Improve a skill/));
  expect(screen.getByRole('button', { name: 'Copy feedback' })).toBeTruthy();
  expect(
    screen.queryByRole('link', { name: 'Open support destination' }),
  ).toBeNull();
  feedback.items[0].proposals[0].status = 'rejected';
  rerender(
    <InsightsHome
      controller={
        {
          insights: vi.fn().mockResolvedValue(feedback),
        } as unknown as ClientController
      }
    />,
  );
  await screen.findByText(/Improve a skill · send_feedback · rejected/);
  expect(screen.queryByRole('button', { name: 'Copy feedback' })).toBeNull();
  expect(
    screen.getByRole('link', { name: 'Open support destination' }),
  ).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Apply proposal' })).toBeNull();
});

it('renders the skill library report as human rows, never raw JSON (B9)', async () => {
  const report = structuredClone(snapshot);
  report.curator_report = {
    created_at: '2026-09-20T08:00:00',
    manual_skill_count: 21,
    finding_count: 3,
    proposal_count: 1,
    findings: [
      JSON.stringify({
        type: 'overlap',
        skill_names: ['code_delegation', 'code_delegation_custom'],
        score: 0.986,
        protected: true,
      }),
      JSON.stringify({
        type: 'skill_insight',
        insight_id: 'ins-1',
        title: 'Repeated research workflow',
        category: 'usage_pattern',
      }),
      'plain text finding',
    ],
  };
  const controller = {
    insights: vi.fn().mockResolvedValue(report),
  } as unknown as ClientController;
  render(<InsightsHome controller={controller} />);
  const list = await screen.findByRole('list', {
    name: 'Skill library findings',
  });
  expect(list).toHaveTextContent(
    'Code delegation and Code delegation custom overlap',
  );
  expect(list).toHaveTextContent('99% similar instructions');
  expect(list).toHaveTextContent('Protected · pinned or built in');
  expect(list).toHaveTextContent('Repeated research workflow');
  expect(list).toHaveTextContent('Usage pattern');
  expect(list).toHaveTextContent('plain text finding');
  expect(list.textContent).not.toMatch(/[{}"]|skill_names|overlap"/);
  expect(
    screen.getByText(/21 manual skills · 3 findings · 1 proposal/),
  ).toBeVisible();
});
