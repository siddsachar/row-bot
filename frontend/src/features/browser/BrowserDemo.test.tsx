import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  BrowserDemoPage,
  BrowserDemoStep,
  BrowserDemoSummary,
} from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import BrowserDemo, { clock, demoBlocked } from './BrowserDemo';

const mock = vi.hoisted(() => ({
  browserDemo: vi.fn(),
  controlBrowserDemo: vi.fn(),
  setBrowserDemoAlways: vi.fn(),
  sendBrowserDemoNarration: vi.fn(),
  draftDemoSkill: vi.fn(),
  trySkill: vi.fn(),
  skillOffers: vi.fn(),
  skills: vi.fn(),
  reviewSkill: vi.fn(),
  executeSkill: vi.fn(),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: mock, platform: {} }),
}));

const DEMO = 'a'.repeat(32);
function step(patch: Partial<BrowserDemoStep>): BrowserDemoStep {
  return {
    id: 1,
    at: 0,
    kind: 'click',
    text: '',
    site: 'example-bank.com',
    path: '/statements',
    title: 'Statements',
    value: '',
    secret: '',
    secret_text: '',
    always: false,
    input: false,
    unsafe: false,
    narration: '',
    ...patch,
  };
}
const STEPS = [
  step({ id: 1, kind: 'open', text: 'Opened example-bank.com/statements' }),
  step({
    id: 2,
    kind: 'type',
    text: 'Typed into the “Password” field',
    secret: 'password',
    secret_text: 'a password',
  }),
  step({
    id: 3,
    kind: 'type',
    text: 'Typed into the “Month” field',
    value: 'October 2026',
    input: true,
  }),
  step({ id: 4, text: 'Clicked the “Download statement” link' }),
];
const summary = (
  patch: Partial<BrowserDemoSummary> = {},
): BrowserDemoSummary => ({
  id: DEMO,
  status: 'recording',
  ended: '',
  steps: 4,
  elapsed_seconds: 72,
  ...patch,
});
const demoPage = (
  patch: Partial<NonNullable<BrowserDemoPage['demo']>> = {},
  ready = true,
): BrowserDemoPage => ({
  conversation_id: 'c',
  demo: {
    id: DEMO,
    status: 'recording',
    ended: '',
    elapsed_seconds: 72,
    max_steps: 200,
    max_seconds: 900,
    narrated: false,
    steps: STEPS,
    ...patch,
  },
  narration: ready
    ? { ready: true, code: '' }
    : { ready: false, code: 'narration_no_model' },
});

function show(current: BrowserDemoSummary | null, { signIn = false } = {}) {
  const stage = document.createElement('div');
  document.body.append(stage);
  const view = render(
    <MemoryRouter initialEntries={['/browser']}>
      <OverlayProvider>
        <Routes>
          <Route
            path="/browser"
            element={
              <BrowserDemo
                conversationId="c"
                current={current}
                signIn={signIn}
                chatTitle="Bank statements"
                stage={stage}
              />
            }
          />
          <Route path="/conversations/:id" element={<p>Try-it chat</p>} />
        </Routes>
      </OverlayProvider>
    </MemoryRouter>,
  );
  return { ...view, stage };
}

beforeEach(() => {
  vi.clearAllMocks();
  mock.browserDemo.mockResolvedValue(demoPage());
  mock.controlBrowserDemo.mockImplementation(
    async (_c: string, _d: string, action: string) =>
      action === 'discard'
        ? { conversation_id: 'c', demo: null, narration: null }
        : demoPage({ status: action === 'stop' ? 'stopped' : 'recording' }),
  );
  mock.setBrowserDemoAlways.mockImplementation(
    async (_c: string, _d: string, id: number, always: boolean) =>
      demoPage({
        steps: STEPS.map((item) =>
          item.id === id ? { ...item, always, input: !always } : item,
        ),
      }),
  );
});

it('says why Show Row-Bot how cannot start', () => {
  expect(demoBlocked({ page: false, acting: false, incognito: false })).toBe(
    'Open a page first, then show Row-Bot how',
  );
  expect(demoBlocked({ page: true, acting: true, incognito: false })).toBe(
    'Wait for Row-Bot to finish in the browser',
  );
  expect(demoBlocked({ page: true, acting: false, incognito: true })).toMatch(
    /incognito/,
  );
  expect(demoBlocked({ page: true, acting: false, incognito: false })).toBe('');
  expect(clock(72)).toBe('1:12');
});

it('shows recording with its steps and time, and Done, Undo and Discard', async () => {
  const user = userEvent.setup();
  show(summary());

  const bar = screen.getByRole('status', { name: 'Show Row-Bot how' });
  expect(bar).toHaveTextContent('Showing Row-Bot how 4 steps · 1:12');
  await user.click(within(bar).getByRole('button', { name: 'Undo last step' }));
  expect(mock.controlBrowserDemo).toHaveBeenCalledWith('c', DEMO, 'undo');

  await user.click(
    within(bar).getByRole('button', { name: 'Discard these steps' }),
  );
  const confirm = await screen.findByRole('alertdialog');
  expect(confirm).toHaveTextContent('Discard these steps?');
  await user.click(within(confirm).getByRole('button', { name: 'Discard' }));
  await waitFor(() =>
    expect(mock.controlBrowserDemo).toHaveBeenCalledWith('c', DEMO, 'discard'),
  );

  await user.click(within(bar).getByRole('button', { name: 'Done' }));
  expect(mock.controlBrowserDemo).toHaveBeenCalledWith('c', DEMO, 'stop');
});

it('counts a new recording from 0:00 however long the bar was open before (M6)', () => {
  vi.useFakeTimers({
    toFake: ['setInterval', 'clearInterval', 'performance'],
  });
  try {
    const bar = (current: BrowserDemoSummary | null) => (
      <MemoryRouter>
        <OverlayProvider>
          <BrowserDemo
            conversationId="c"
            current={current}
            signIn={false}
            chatTitle="Bank statements"
            stage={document.body}
          />
        </OverlayProvider>
      </MemoryRouter>
    );
    const view = render(bar(null));
    // The pane stays open half a minute before Show Row-Bot how is pressed.
    act(() => vi.advanceTimersByTime(30_000));
    view.rerender(bar(summary({ steps: 1, elapsed_seconds: 0 })));
    act(() => vi.advanceTimersByTime(2_000));
    const status = screen.getByRole('status', { name: 'Show Row-Bot how' });
    expect(status).toHaveTextContent('1 step · 0:02');
    // The server's next update moves the count on from its own time, never back.
    view.rerender(bar(summary({ steps: 2, elapsed_seconds: 2.5 })));
    act(() => vi.advanceTimersByTime(1_000));
    expect(status).toHaveTextContent('2 steps · 0:03');
  } finally {
    vi.useRealTimers();
  }
});

it('narration is off by default and says why when no local speech model is installed', async () => {
  mock.browserDemo.mockResolvedValue(demoPage({}, false));
  show(summary());

  const mic = await screen.findByRole('button', {
    name: 'Install a speech model in Settings › Voice to narrate',
  });
  expect(mic).toBeDisabled();
  expect(mic).toHaveAttribute('aria-pressed', 'false');
});

it('lists the steps so far: a sign-in step keeps nothing, a typed value can be kept as it is', async () => {
  const user = userEvent.setup();
  const { stage } = show(summary(), { signIn: true });

  expect(screen.getByRole('note')).toHaveTextContent(
    'Sign-in step. Row-Bot won’t see or keep what you type here.',
  );
  await user.click(screen.getByRole('button', { name: 'Steps so far' }));
  const panel = within(stage).getByRole('region', { name: 'Steps so far' });
  const items = await within(panel).findAllByRole('listitem');
  expect(items.map((item) => item.textContent)).toEqual([
    expect.stringContaining('Opened example-bank.com/statements'),
    expect.stringContaining('Not kept (a password): you’ll do this part'),
    expect.stringContaining('October 2026Input'),
    expect.stringContaining('Clicked the “Download statement” link'),
  ]);
  expect(items[1]).not.toHaveTextContent('October');
  await user.click(
    within(items[2]).getByRole('button', {
      name: 'Always type “October 2026”',
    }),
  );
  expect(mock.setBrowserDemoAlways).toHaveBeenCalledWith('c', DEMO, 3, true);
  expect(
    await within(panel).findByRole('button', {
      name: 'Make this an input again',
    }),
  ).toHaveAttribute('aria-pressed', 'true');
});

it('Done asks what it is for, drafts into the review sheet and Try it opens the new chat', async () => {
  const user = userEvent.setup();
  mock.browserDemo.mockResolvedValue(demoPage({ status: 'stopped' }));
  const draft = {
    proposal_id: 'proposal_1',
    proposal_status: 'ready',
    name: 'download_statement',
    display_name: 'Download a statement',
    icon: '🏦',
    description: 'Downloads a month’s statement.',
    when_to_use: 'When asked for a statement.',
    inputs: ['the month'],
    steps: ['Open example-bank.com/statements with `browser_navigate`.'],
    checks: [],
    instructions: 'Downloads a month’s statement.',
    why: 'You showed Row-Bot this task (4 steps on example-bank.com).',
    source_title: 'Bank statements',
    overlaps: [],
    errors: [],
    warnings: [],
    shown: [
      'Opened example-bank.com/statements',
      'Typed into the “Month” field · an input',
    ],
  };
  const offers = {
    conversation_id: 'c',
    revision: 'r',
    items: [
      {
        id: 'offer_1',
        message_id: `demo:${DEMO}`,
        origin: 'demo' as const,
        status: 'drafted' as const,
        task_name: 'Download a statement',
        why: draft.why,
        created_at: '2026-11-16T09:00:00+00:00',
        proposal_id: 'proposal_1',
        skill_id: null,
        draft,
      },
    ],
  };
  mock.draftDemoSkill.mockResolvedValue(offers);
  mock.skillOffers.mockResolvedValue(offers);
  mock.skills.mockResolvedValue({ revision: 'library-1' });
  mock.reviewSkill.mockResolvedValue({ review_id: 'review-1' });
  mock.executeSkill.mockResolvedValue({
    status: 'completed',
    skill_id: 'download_statement',
  });
  mock.trySkill.mockResolvedValue({
    conversation_id: 'try-1',
    text: 'Do “Download a statement” with:\n- the month: ',
  });
  show(summary({ status: 'stopped', ended: 'done' }));

  const finish = await screen.findByRole('dialog', {
    name: 'What is this for?',
  });
  await user.type(
    within(finish).getByRole('textbox', { name: /What is this for/ }),
    'Download my monthly statement',
  );
  await user.click(within(finish).getByRole('button', { name: 'Draft skill' }));
  await waitFor(() =>
    expect(mock.draftDemoSkill).toHaveBeenCalledWith(
      'c',
      DEMO,
      'Download my monthly statement',
      expect.any(AbortSignal),
    ),
  );
  const review = await screen.findByRole('dialog', {
    name: '🏦 Download a statement',
  });
  expect(within(review).getByText('Where it came from')).toBeVisible();
  await user.click(within(review).getByText('What you showed (2 steps)'));
  expect(
    within(review).getByText('Typed into the “Month” field · an input'),
  ).toBeVisible();

  await user.click(within(review).getByRole('button', { name: 'Approve' }));
  const tryIt = await within(review).findByRole('button', { name: /Try it/ });
  expect(within(review).getByRole('status')).toHaveTextContent(
    'Saved “Download a statement” as a skill.',
  );
  await user.click(tryIt);
  expect(mock.trySkill).toHaveBeenCalledWith('download_statement');
  expect(await screen.findByText('Try-it chat')).toBeVisible();
});
