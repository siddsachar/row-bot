import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { WaitView } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { createTaskEditSessions } from '../tasks/task-edit-sessions';
import { WaitsStrip } from './Waits';

const CI: WaitView = {
  wait_id: 'a1b2c3d4e5f6',
  conversation_id: 'conversation-a',
  conversation_title: 'Ship 5.3.1',
  kind: 'check',
  label: 'CI on PR 590 is green',
  how: 'checks every 2 min',
  until: '2026-10-28T15:00:00+00:00',
  status: 'waiting',
};

/** A server time as this device writes it: its own zone and clock style (M5). */
const clock = (iso: string) =>
  new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(
    new Date(iso),
  );

const WATCH: WaitView = {
  wait_id: 'b1b2c3d4e5f6',
  conversation_id: 'conversation-a',
  conversation_title: 'Ship 5.3.1',
  kind: 'watch',
  label: 'PR 590 is ready',
  how: 'every 30 min',
  until: '2026-10-29T15:00:00+00:00',
  status: 'waiting',
  watch: {
    mode: 'judged',
    target: 'Is PR 590 ready to merge?',
    checks: 3,
    changes: 1,
    max_changes: 20,
    checked_at: '2026-10-28T14:20:00+00:00',
    result: 'Checks still running (3 of 5).',
    trouble: false,
    checking: false,
    tokens: 12_400,
    cost_usd: 0.04,
    ended: '',
    can_extend: true,
    workflow: {
      name: 'Watch: PR 590 is ready',
      prompt: 'Check: Is PR 590 ready to merge? Tell me only what changed.',
      every_minutes: 30,
    },
  },
};

function Where() {
  const location = useLocation();
  return <output aria-label="location">{location.search}</output>;
}

function setup(
  items: WaitView[],
  control: unknown = { schema_version: 1, items },
) {
  const snapshot = {
    conversations: [],
    handshake: {
      client_session_id: 'session',
      server_epoch: 'epoch',
      instance_id: 'instance',
    },
  };
  const controller = {
    getSnapshot: () => snapshot,
    subscribe: () => () => undefined,
    waits: vi.fn(async () => ({ schema_version: 1, items })),
    stopWait: vi.fn(async () => ({ schema_version: 1, items: [] })),
    controlWatch: vi.fn(async () => {
      if (control instanceof Error) throw control;
      return control;
    }),
  } as unknown as ClientController & {
    stopWait: ReturnType<typeof vi.fn>;
    controlWatch: ReturnType<typeof vi.fn>;
  };
  const taskEditSessions = createTaskEditSessions(controller);
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <RuntimeContext.Provider
        value={{ controller, platform: {} as ClientPlatform, taskEditSessions }}
      >
        <WaitsStrip conversationId="conversation-a" />
        <Where />
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  return { controller, taskEditSessions };
}

it('shows what this conversation waits for, and Stop ends it (5.3.1)', async () => {
  const { controller } = setup([
    CI,
    { ...CI, wait_id: 'f6e5d4c3b2a1', conversation_id: 'conversation-b' },
  ]);

  const strip = await screen.findByRole('region', { name: 'Waiting' });
  expect(strip).toHaveTextContent(
    'Waiting for CI on PR 590 is greenchecks every 2 min',
  );
  expect(screen.getAllByRole('listitem')).toHaveLength(1);

  await userEvent.click(screen.getByRole('button', { name: 'Stop waiting' }));
  expect(controller.stopWait).toHaveBeenCalledWith('a1b2c3d4e5f6');
  await waitFor(() =>
    expect(screen.queryByRole('region', { name: 'Waiting' })).toBeNull(),
  );
});

it("says when a time wait carries on in this device's zone and clock style (M5)", async () => {
  setup([
    {
      ...CI,
      kind: 'time',
      label: 'the deploy has had time to finish',
      how: '',
      at: '2026-10-28T08:25:00+00:00',
    },
  ]);
  const strip = await screen.findByRole('region', { name: 'Waiting' });
  expect(strip).toHaveTextContent(
    `Waiting for the deploy has had time to finishat ${clock('2026-10-28T08:25:00Z')}`,
  );
});

it('shows nothing when this conversation waits for nothing', async () => {
  const { controller } = setup([{ ...CI, conversation_id: 'conversation-b' }]);
  await waitFor(() => expect(controller.waits).toHaveBeenCalled());
  expect(screen.queryByRole('region', { name: 'Waiting' })).toBeNull();
});

it('shows a watch as a card with its last check and what it used, and its controls (B4)', async () => {
  const { controller } = setup([WATCH]);
  const card = (await screen.findByText('PR 590 is ready')).closest('li')!;
  expect(card).toHaveTextContent('Watching PR 590 is ready');
  expect(card).toHaveTextContent(
    'every 30 min · 1 of 20 changes · 12k tokens · $0.04',
  );
  expect(card).toHaveTextContent(
    `Checked ${clock('2026-10-28T14:20:00Z')}: Checks still running (3 of 5).`,
  );
  expect(
    screen.queryByRole('button', { name: 'Make this a workflow' }),
  ).toBeNull();

  await userEvent.click(screen.getByRole('button', { name: 'Check now' }));
  expect(controller.controlWatch).toHaveBeenCalledWith('b1b2c3d4e5f6', {
    action: 'check_now',
  });
  await userEvent.click(screen.getByRole('button', { name: 'Pause watching' }));
  expect(controller.controlWatch).toHaveBeenCalledWith('b1b2c3d4e5f6', {
    action: 'pause',
  });
  await userEvent.click(screen.getByRole('button', { name: 'Stop watching' }));
  expect(controller.stopWait).toHaveBeenCalledWith('b1b2c3d4e5f6');
});

it('shows a paused watch with Resume and no Check now', async () => {
  setup([{ ...WATCH, status: 'paused' }]);
  expect(await screen.findByText(/Paused:/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Resume watching' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: 'Check now' })).toBeNull();
});

it('says why a watch could not check, in its card', async () => {
  setup([
    {
      ...WATCH,
      watch: {
        ...WATCH.watch!,
        trouble: true,
        result: "Couldn't check: it didn't finish in 60 seconds",
      },
    },
  ]);
  const card = (await screen.findByText('PR 590 is ready')).closest('li')!;
  expect(card).toHaveClass('is-trouble');
  expect(card).toHaveTextContent("Couldn't check: it didn't finish");
});

it('offers to make a watch a workflow when extending would pass 7 days', async () => {
  const limit = Object.assign(new Error('limit'), {
    code: 'watch_lifetime_max',
  });
  setup([WATCH], limit);
  await userEvent.click(
    await screen.findByRole('button', { name: 'Watch 24 hours longer' }),
  );
  expect(await screen.findByText(/at most 7 days/)).toHaveTextContent(
    'A watch lasts at most 7 days. Make it a workflow to keep checking.',
  );
  expect(
    screen.getByRole('button', { name: 'Make this a workflow' }),
  ).toBeInTheDocument();
});

it('opens the workflow editor filled from an ended watch; saving it puts the watch away', async () => {
  const ended: WaitView = {
    ...WATCH,
    status: 'ended',
    watch: { ...WATCH.watch!, ended: 'capped', changes: 20 },
  };
  const { controller, taskEditSessions } = setup([ended]);
  const card = (await screen.findByText('PR 590 is ready')).closest('li')!;
  expect(card).toHaveTextContent('Stopped watching PR 590 is ready');
  expect(card).toHaveTextContent('Stopped after 20 changes.');
  expect(screen.queryByRole('button', { name: 'Pause watching' })).toBeNull();

  await userEvent.click(
    screen.getByRole('button', { name: 'Make this a workflow' }),
  );
  expect(screen.getByLabelText('location')).toHaveTextContent('?tab=workflows');
  const opened = taskEditSessions.getSnapshot().selected!;
  expect(opened.label).toBe('Watch: PR 590 is ready');
  expect(opened.session.taskId).toBe('');
  expect(opened.session.seed).toEqual(
    expect.objectContaining({
      name: 'Watch: PR 590 is ready',
      prompts: ['Check: Is PR 590 ready to merge? Tell me only what changed.'],
      schedule: 'interval_minutes:30',
      enabled: true,
    }),
  );
  // Nothing is made, and the watch is kept, until the person saves.
  expect(controller.stopWait).not.toHaveBeenCalled();
  opened.session.afterCreate?.();
  expect(controller.stopWait).toHaveBeenCalledWith('b1b2c3d4e5f6');
});
