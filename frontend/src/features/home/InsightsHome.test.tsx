import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  InsightCommand,
  InsightProposalView,
  InsightReceipt,
  InsightView,
  InsightsSnapshot,
} from '../../api/types';
import InsightsHome from './InsightsHome';
import { OverlayProvider } from '../../ui/overlays';

const proposal: InsightProposalView = {
  id: 'proposal-test',
  title: 'Improve a skill',
  proposal_type: 'create_skill',
  executable: true,
  status: 'ready',
  risk: 'low',
  rationale: 'The synthetic skill is missing.',
  verification_plan: 'Check the saved skill.',
  preview: '{"name":"example","instructions":"Summarize the fixture"}',
  open_thread_id: '',
  feedback_body: '',
  support_url: '',
};

const insight: InsightView = {
  id: 'ins-test',
  title: 'Synthetic finding',
  body: 'A local fake observation.',
  suggestion: 'Inspect the proposal.',
  category: 'skill_proposal',
  severity: 'warning',
  status: 'new',
  found_at: '',
  out_of_date: '',
  proposals: [proposal],
};

const snapshot: InsightsSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  curator_report: null,
  items: [insight],
};

function withItems(...items: InsightView[]): InsightsSnapshot {
  return { ...snapshot, items };
}

function controllerFor(
  value: InsightsSnapshot,
  execute?: (command: InsightCommand) => InsightsSnapshot,
) {
  const insights = vi.fn(async () => value);
  const executeInsight = vi.fn(
    async (command: InsightCommand): Promise<InsightReceipt> => ({
      command_id: command.command_id,
      status: 'completed',
      summary: `Done: ${command.action}.`,
      snapshot: execute ? execute(command) : value,
    }),
  );
  return {
    insights,
    executeInsight,
    controller: { insights, executeInsight } as unknown as ClientController,
  };
}

const feed = () => screen.getByRole('list', { name: 'Insights feed' });
const row = (title: string) =>
  within(feed())
    .getByRole('heading', { name: title })
    .closest('li') as HTMLElement;
const proposalDetails = (summary: string) =>
  screen.getByText(summary).closest('details') as HTMLElement;

beforeEach(() => sessionStorage.clear());

it('shows a proposal preview and applies only after a click', async () => {
  const { controller, executeInsight } = controllerFor(snapshot);
  render(<InsightsHome controller={controller} />);
  await screen.findByRole('heading', { name: 'Synthetic finding' });
  const item = row('Synthetic finding');
  expect(executeInsight).not.toHaveBeenCalled();
  expect(within(item).getByText('Warning')).toHaveClass('visually-hidden');
  expect(within(item).getByText('Skill proposal')).toBeVisible();

  // Proposal actions stay behind Why until the reader asks.
  expect(
    screen.queryByRole('button', { name: 'Apply proposal' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    within(item).getByRole('button', { name: 'Review new skill' }),
  );
  const why = within(item).getByRole('button', { name: 'Why' });
  expect(why).toHaveAttribute('aria-expanded', 'true');

  const details = proposalDetails('Improve a skill · New skill · Ready');
  expect(details).toHaveTextContent('RiskLow');
  expect(details).toHaveTextContent('The synthetic skill is missing.');
  expect(details).toHaveTextContent('Checked by: Check the saved skill.');
  expect(within(details).getByText('Summarize the fixture')).not.toBeVisible();
  fireEvent.click(within(details).getByText(/Improve a skill/));
  expect(details).toHaveAttribute('open');

  // The preview is labelled fields, never the raw JSON string.
  const fields = within(details).getAllByRole('term');
  expect(fields.map((term) => term.textContent)).toEqual([
    'Risk',
    'Name',
    'Instructions',
  ]);
  expect(within(details).getByText('example')).toBeVisible();
  expect(within(details).getByText('Summarize the fixture')).toBeVisible();
  expect(details.textContent).not.toMatch(/[{}"]/);
  expect(executeInsight).not.toHaveBeenCalled();

  fireEvent.click(
    within(details).getByRole('button', { name: 'Apply proposal' }),
  );
  expect(await screen.findByText('Done: apply.')).toBeVisible();
  expect(executeInsight).toHaveBeenCalledOnce();
  expect(executeInsight.mock.calls[0][0]).toMatchObject({
    action: 'apply',
    insight_id: 'ins-test',
    proposal_id: 'proposal-test',
    revision: snapshot.revision,
  });
});

it('shows feedback actions only for the applicable proposal state', async () => {
  const feedback = structuredClone(snapshot);
  feedback.items[0].proposals[0] = {
    ...proposal,
    proposal_type: 'send_feedback',
    preview: '',
    feedback_body: 'Synthetic redacted report',
    support_url: 'https://example.test/support',
  };
  const writeClipboard = vi.fn(async () => ({
    status: 'ok' as const,
    value: null,
  }));
  const { rerender } = render(
    <InsightsHome
      controller={controllerFor(feedback).controller}
      writeClipboard={writeClipboard}
    />,
  );
  await screen.findByRole('heading', { name: 'Synthetic finding' });

  // The row's one suggested action copies the redacted feedback.
  fireEvent.click(screen.getByRole('button', { name: 'Copy feedback' }));
  expect(await screen.findByText('Feedback copied.')).toBeVisible();
  expect(writeClipboard).toHaveBeenCalledWith('Synthetic redacted report');

  fireEvent.click(screen.getByRole('button', { name: 'Why' }));
  let details = proposalDetails('Improve a skill · Send feedback · Ready');
  expect(
    within(details).getByRole('button', { name: 'Copy feedback' }),
  ).toBeInTheDocument();
  expect(
    within(details).getByRole('button', { name: 'Apply proposal' }),
  ).toBeInTheDocument();
  expect(
    within(details).queryByRole('link', { name: 'Open support destination' }),
  ).toBeNull();

  const rejected = structuredClone(feedback);
  rejected.items[0].proposals[0].status = 'rejected';
  rerender(
    <InsightsHome
      controller={controllerFor(rejected).controller}
      writeClipboard={writeClipboard}
    />,
  );
  details = (
    await screen.findByText('Improve a skill · Send feedback · Rejected')
  ).closest('details') as HTMLElement;
  expect(screen.queryByRole('button', { name: 'Copy feedback' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Apply proposal' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Reject proposal' })).toBeNull();
  const support = within(details).getByRole('link', {
    name: 'Open support destination',
  });
  expect(support).toHaveAttribute('href', 'https://example.test/support');
  expect(support).toHaveAttribute('target', '_blank');
  expect(support).toHaveAttribute('rel', 'noopener noreferrer');
});

it('offers no Apply for a review-only proposal and says when an insight may be out of date (B124)', async () => {
  const review: InsightView = {
    ...insight,
    found_at: '2026-09-28T09:00:00Z',
    out_of_date:
      'Found while another model was in use, so it may no longer apply.',
    proposals: [
      {
        ...proposal,
        title: 'Review overlap',
        proposal_type: 'consolidate_skills',
        executable: false,
      },
    ],
  };
  const { controller, executeInsight } = controllerFor(withItems(review));
  render(<InsightsHome controller={controller} />);
  await screen.findByRole('heading', { name: 'Synthetic finding' });
  const item = row('Synthetic finding');
  expect(
    within(item).getByText(
      'Found while another model was in use, so it may no longer apply.',
    ),
  ).toBeVisible();
  expect(item.querySelector('time')).toHaveAttribute(
    'datetime',
    '2026-09-28T09:00:00Z',
  );

  fireEvent.click(
    within(item).getByRole('button', { name: 'Review merge skills' }),
  );
  const details = proposalDetails('Review overlap · Merge skills · Ready');
  fireEvent.click(within(details).getByText(/Review overlap/));
  expect(
    within(details).queryByRole('button', { name: 'Apply proposal' }),
  ).toBeNull();
  expect(details).toHaveTextContent(
    "Review only: Row-Bot can't make this change. Make it yourself if you agree, then reject the proposal.",
  );
  expect(
    within(details).getByRole('button', { name: 'Reject proposal' }),
  ).toBeEnabled();
  expect(executeInsight).not.toHaveBeenCalled();
});

it('toggles the Why detail for one insight at a time', async () => {
  const second: InsightView = {
    ...insight,
    id: 'ins-second',
    title: 'Second finding',
    body: 'Another fake observation.',
    suggestion: '',
    proposals: [],
  };
  const { controller } = controllerFor(withItems(insight, second));
  render(<InsightsHome controller={controller} />);
  await screen.findByRole('heading', { name: 'Second finding' });

  const firstWhy = within(row('Synthetic finding')).getByRole('button', {
    name: 'Why',
  });
  const secondWhy = within(row('Second finding')).getByRole('button', {
    name: 'Why',
  });
  const body = document.getElementById(
    firstWhy.getAttribute('aria-controls')!,
  )!;
  expect(firstWhy).toHaveAttribute('aria-expanded', 'false');
  expect(body).not.toBeVisible();

  fireEvent.click(firstWhy);
  expect(firstWhy).toHaveAttribute('aria-expanded', 'true');
  expect(secondWhy).toHaveAttribute('aria-expanded', 'false');
  expect(body).toBeVisible();
  expect(body).toHaveTextContent('Suggested: Inspect the proposal.');

  fireEvent.click(secondWhy);
  expect(
    within(row('Second finding')).getByText(/No proposals yet/),
  ).toBeVisible();

  fireEvent.click(firstWhy);
  expect(firstWhy).toHaveAttribute('aria-expanded', 'false');
  expect(body).not.toBeVisible();
});

it('pins, unpins, and dismisses an insight only when asked', async () => {
  let current = snapshot;
  const { controller, executeInsight } = controllerFor(snapshot, (command) => {
    const [item] = current.items;
    current =
      command.action === 'dismiss'
        ? withItems()
        : withItems({
            ...item,
            status: command.action === 'pin' ? 'pinned' : 'new',
          });
    return current;
  });
  render(<InsightsHome controller={controller} />);
  await screen.findByRole('heading', { name: 'Synthetic finding' });
  expect(executeInsight).not.toHaveBeenCalled();
  expect(screen.getByText('1 insight')).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: 'Pin' }));
  const unpin = await screen.findByRole('button', { name: 'Unpin' });
  expect(unpin).toHaveAttribute('aria-pressed', 'true');
  expect(executeInsight.mock.calls[0][0]).toMatchObject({
    action: 'pin',
    insight_id: 'ins-test',
  });
  expect(screen.getByText('1 insight · 1 pinned')).toBeVisible();
  expect(within(row('Synthetic finding')).getByText('Pinned')).toBeVisible();

  fireEvent.click(unpin);
  const pin = await screen.findByRole('button', { name: 'Pin' });
  expect(pin).toHaveAttribute('aria-pressed', 'false');
  expect(executeInsight.mock.calls[1][0]).toMatchObject({
    action: 'unpin',
    insight_id: 'ins-test',
  });

  fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
  expect(
    await screen.findByText(
      'No active insights. New ones appear after analysis.',
    ),
  ).toBeVisible();
  expect(executeInsight.mock.calls[2][0]).toMatchObject({
    action: 'dismiss',
    insight_id: 'ins-test',
  });
  expect(executeInsight).toHaveBeenCalledTimes(3);
});

it('filters the feed to pinned insights', async () => {
  const pinned: InsightView = {
    ...insight,
    id: 'ins-pinned',
    title: 'Pinned finding',
    status: 'pinned',
  };
  const { controller } = controllerFor(withItems(insight, pinned));
  const { rerender } = render(<InsightsHome controller={controller} />);
  await screen.findByRole('heading', { name: 'Pinned finding' });

  const show = screen.getByRole('radiogroup', { name: 'Show insights' });
  expect(within(show).getByRole('radio', { name: 'All' })).toBeChecked();
  expect(within(feed()).getAllByRole('heading')).toHaveLength(2);

  fireEvent.click(within(show).getByRole('radio', { name: 'Pinned' }));
  expect(
    within(feed())
      .getAllByRole('heading')
      .map((heading) => heading.textContent),
  ).toEqual(['Pinned finding']);

  rerender(
    <InsightsHome controller={controllerFor(withItems(insight)).controller} />,
  );
  expect(await screen.findByText('No pinned insights.')).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: 'All' }));
  expect(
    screen.getByRole('heading', { name: 'Synthetic finding' }),
  ).toBeVisible();
});

it('offers one suggested action per insight for its proposal state', async () => {
  const openConversation = vi.fn();
  const items: InsightView[] = [
    {
      ...insight,
      id: 'ins-open',
      title: 'Open thread finding',
      proposals: [
        {
          ...proposal,
          id: 'p-open',
          proposal_type: 'investigate',
          status: 'applied',
          open_thread_id: 'thread-7',
        },
      ],
    },
    {
      ...insight,
      id: 'ins-investigate',
      title: 'Investigate finding',
      proposals: [
        { ...proposal, id: 'p-investigate', proposal_type: 'investigate' },
      ],
    },
    {
      ...insight,
      id: 'ins-empty',
      title: 'Empty finding',
      proposals: [],
    },
    {
      ...insight,
      id: 'ins-done',
      title: 'Settled finding',
      proposals: [{ ...proposal, id: 'p-done', status: 'verified' }],
    },
  ];
  const { controller, executeInsight } = controllerFor(withItems(...items));
  render(
    <InsightsHome
      controller={controller}
      openConversation={openConversation}
    />,
  );
  await screen.findByRole('heading', { name: 'Settled finding' });

  const actions = (title: string) =>
    within(row(title))
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label') ?? button.textContent);
  expect(actions('Open thread finding')).toEqual([
    'Why',
    'Open investigation',
    'Pin',
    'Dismiss',
  ]);
  expect(actions('Investigate finding')).toEqual([
    'Why',
    'Investigate',
    'Pin',
    'Dismiss',
  ]);
  expect(actions('Empty finding')).toEqual([
    'Why',
    'Suggest a fix',
    'Pin',
    'Dismiss',
  ]);
  expect(actions('Settled finding')).toEqual(['Why', 'Pin', 'Dismiss']);
  expect(executeInsight).not.toHaveBeenCalled();

  fireEvent.click(
    within(row('Open thread finding')).getByRole('button', {
      name: 'Open investigation',
    }),
  );
  expect(openConversation).toHaveBeenCalledWith('thread-7');
  expect(executeInsight).not.toHaveBeenCalled();

  fireEvent.click(
    within(row('Empty finding')).getByRole('button', { name: 'Suggest a fix' }),
  );
  expect(await screen.findByText('Done: generate.')).toBeVisible();
  expect(executeInsight.mock.calls[0][0]).toMatchObject({
    action: 'generate',
    insight_id: 'ins-empty',
    proposal_id: '',
  });

  fireEvent.click(
    within(row('Investigate finding')).getByRole('button', {
      name: 'Investigate',
    }),
  );
  expect(await screen.findByText('Done: apply.')).toBeVisible();
  expect(executeInsight.mock.calls[1][0]).toMatchObject({
    action: 'apply',
    insight_id: 'ins-investigate',
    proposal_id: 'p-investigate',
  });
});

it('refreshes and analyzes the skill library only when asked', async () => {
  const { controller, insights, executeInsight } = controllerFor(snapshot);
  render(<InsightsHome controller={controller} />);
  await screen.findByRole('heading', { name: 'Synthetic finding' });
  expect(insights).toHaveBeenCalledOnce();
  expect(executeInsight).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Refresh insights' }));
  await act(async () => {});
  expect(insights).toHaveBeenCalledTimes(2);

  fireEvent.click(
    screen.getByRole('button', { name: 'Analyze skill library' }),
  );
  expect(await screen.findByText('Done: review_skills.')).toBeVisible();
  expect(executeInsight.mock.calls[0][0]).toMatchObject({
    action: 'review_skills',
    insight_id: '',
    proposal_id: '',
  });
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

it('offers Undo after Dismiss and brings the insight back (decision 19)', async () => {
  let current: InsightsSnapshot = withItems(insight);
  const { controller, executeInsight } = controllerFor(current, (command) => {
    current =
      command.action === 'dismiss'
        ? { ...withItems(), revision: 'r'.repeat(64) }
        : withItems(insight);
    return current;
  });
  render(
    <OverlayProvider>
      <InsightsHome controller={controller} />
    </OverlayProvider>,
  );
  await screen.findByRole('heading', { name: 'Synthetic finding' });
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
  await screen.findByText(
    'No active insights. New ones appear after analysis.',
  );
  await act(async () =>
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' })),
  );
  expect(executeInsight.mock.calls[1][0]).toMatchObject({
    action: 'restore',
    insight_id: 'ins-test',
    revision: 'r'.repeat(64),
  });
  expect(
    await screen.findByRole('heading', { name: 'Synthetic finding' }),
  ).toBeVisible();
});
