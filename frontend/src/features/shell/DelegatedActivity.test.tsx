import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { DelegatedActivityView, DelegatedRun } from '../../api/types';
import { AgentAvatar, agentSeed } from '../../ui/AgentAvatar';
import { OverlayProvider } from '../../ui/overlays';
import DelegatedActivity, {
  recentReads,
  type DelegatedRead,
} from './DelegatedActivity';

const run: DelegatedRun = {
  run_id: 'run-a',
  parent_conversation_id: 'parent-a',
  child_conversation_id: 'child-a',
  name: 'Research task',
  status: 'completed',
  summary: 'Public result',
};
const page: DelegatedActivityView = {
  conversation_id: 'parent-a',
  parent_conversation_id: null,
  items: [run],
  next_cursor: null,
  has_more: false,
};
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

function show(
  props: Partial<Parameters<typeof DelegatedActivity>[0]> = {},
): ReturnType<typeof render> {
  return render(
    <OverlayProvider>
      <DelegatedActivity
        conversationId="parent-a"
        refreshKey=""
        loadPage={async () => page}
        loadRun={async () => run}
        openConversation={async () => {}}
        {...props}
      />
    </OverlayProvider>,
  );
}

it('waits for deep-link authentication and loads automatically when ready', async () => {
  const load = vi.fn().mockResolvedValue({ ...page, items: [] });
  const props = {
    conversationId: 'parent-a',
    refreshKey: '',
    loadPage: load,
    loadRun: async () => run,
    openConversation: vi.fn().mockResolvedValue(undefined),
  };
  const view = render(
    <OverlayProvider>
      <DelegatedActivity {...props} ready={false} />
    </OverlayProvider>,
  );
  await act(async () => {});
  expect(load).not.toHaveBeenCalled();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  view.rerender(
    <OverlayProvider>
      <DelegatedActivity {...props} ready />
    </OverlayProvider>,
  );
  await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(props.openConversation).not.toHaveBeenCalled();
});

it('shows each agent on one line: icon, name, and its status in words (B240)', async () => {
  const user = userEvent.setup();
  const long = 'Partner portal price lists for Northwind, Contoso and Fabrikam';
  const statuses = [
    ['running', 'Working'],
    ['waiting_approval', 'Waiting for you'],
    ['failed', 'Failed'],
  ] as const;
  show({
    loadPage: async () => ({
      ...page,
      items: statuses.map(([status], index) => ({
        ...run,
        run_id: `run-${index}`,
        name: index === 0 ? long : `Task ${index}`,
        status,
        profile_id: index === 1 ? 'profile-7' : '',
      })),
    }),
  });
  for (const [index, [, word]] of statuses.entries()) {
    const name = index === 0 ? long : `Task ${index}`;
    const row = await screen.findByRole('button', { name: `${name}, ${word}` });
    // The status word shows beside the name, and a small icon before it.
    expect(within(row).getByText(word)).toBeVisible();
    expect(row.querySelector('.agent-avatar')).not.toBeNull();
  }
  // The icon follows the profile when there is one, else the run.
  const icon = (name: string) =>
    screen
      .getByRole('button', { name: new RegExp(`^${name},`) })
      .querySelector('.agent-avatar')!
      .getAttribute('data-avatar');
  const expected = (seed: string) =>
    render(<AgentAvatar seed={seed} />)
      .container.querySelector('.agent-avatar')!
      .getAttribute('data-avatar');
  expect(icon('Task 1')).toBe(expected(agentSeed('profile-7', 'run-1')));
  expect(icon('Task 2')).toBe(expected(agentSeed('', 'run-2')));
  // A long name is cut to one line; the full name is the row's tooltip.
  await user.tab();
  expect(await screen.findByRole('tooltip')).toHaveTextContent(long);
});

it('opens the agent’s conversation from its row, asking again for a queued one', async () => {
  const open = vi.fn().mockResolvedValue(undefined);
  const loadRun = vi
    .fn()
    .mockResolvedValue({ ...run, child_conversation_id: 'child-b' });
  show({
    loadPage: async () => ({
      ...page,
      items: [
        { ...run, status: 'running' },
        {
          ...run,
          run_id: 'run-b',
          name: 'Queued task',
          status: 'queued',
          child_conversation_id: null,
        },
      ],
    }),
    loadRun,
    openConversation: open,
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Research task, Working' }),
  );
  await waitFor(() => expect(open).toHaveBeenCalledWith('child-a'));
  expect(loadRun).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Queued task, Working' }));
  await waitFor(() => expect(open).toHaveBeenLastCalledWith('child-b'));
  expect(loadRun).toHaveBeenCalledWith('run-b');
});

it('says when an agent has no conversation yet instead of opening nothing', async () => {
  const open = vi.fn();
  show({
    loadPage: async () => ({
      ...page,
      items: [{ ...run, status: 'queued', child_conversation_id: null }],
    }),
    loadRun: async () => ({ ...run, child_conversation_id: null }),
    openConversation: open,
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Research task, Working' }),
  );
  expect(
    await screen.findByText("This agent's conversation isn't available yet."),
  ).toBeInTheDocument();
  expect(open).not.toHaveBeenCalled();
});

it('fences a late agent read after switching to another conversation', async () => {
  const pending = deferred<DelegatedRun>();
  const open = vi.fn().mockResolvedValue(undefined);
  const props = {
    conversationId: 'parent-a',
    refreshKey: '',
    loadPage: async () => ({
      ...page,
      items: [{ ...run, status: 'running', child_conversation_id: null }],
    }),
    loadRun: () => pending.promise,
    openConversation: open,
  };
  const view = render(
    <OverlayProvider>
      <DelegatedActivity {...props} />
    </OverlayProvider>,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Research task, Working' }),
  );
  view.rerender(
    <OverlayProvider>
      <DelegatedActivity
        {...props}
        conversationId="parent-b"
        loadPage={async () => ({
          ...page,
          conversation_id: 'parent-b',
          items: [],
        })}
      />
    </OverlayProvider>,
  );
  await act(async () => {
    pending.resolve(run);
  });
  expect(open).not.toHaveBeenCalled();
});

it('folds finished agents into one line under the live ones; a failure stays listed (B240)', async () => {
  show({
    loadPage: async () => ({
      ...page,
      items: [
        { ...run, run_id: 'done-1', name: 'Brand voice notes' },
        { ...run, run_id: 'done-2', name: 'Customer quotes' },
        { ...run, run_id: 'stop-1', name: 'Social posts', status: 'stopped' },
        { ...run, run_id: 'fail-1', name: 'Copy review', status: 'failed' },
        { ...run, run_id: 'live-1', name: 'Pricing scan', status: 'running' },
      ],
    }),
  });
  const fold = await screen.findByRole('button', {
    name: '2 done · 1 stopped',
  });
  expect(fold).toHaveAttribute('aria-expanded', 'false');
  const rows = screen
    .getAllByRole('button', { name: /, (Working|Failed|Done|Stopped)$/ })
    .map((button) => button.getAttribute('aria-label'));
  expect(rows).toEqual(['Pricing scan, Working', 'Copy review, Failed']);
  fireEvent.click(fold);
  expect(fold).toHaveAttribute('aria-expanded', 'true');
  expect(
    screen.getByRole('button', { name: 'Brand voice notes, Done' }),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Social posts, Stopped' }),
  ).toBeVisible();
});

it('keeps each 50-row page and can return to the first page', async () => {
  const first = {
    ...page,
    items: Array.from({ length: 50 }, (_, index) => ({
      ...run,
      run_id: `run-${index}`,
      name: `Task ${index}`,
      status: 'running',
    })),
    has_more: true,
    next_cursor: 'next',
  };
  const last = {
    ...page,
    items: [50, 51, 52].map((index) => ({
      ...run,
      run_id: `run-${index}`,
      name: `Task ${index}`,
      status: 'running',
    })),
  };
  const load = vi
    .fn()
    .mockResolvedValueOnce(first)
    .mockResolvedValueOnce(last)
    .mockResolvedValue(first);
  show({ loadPage: load });
  await screen.findByRole('button', { name: 'Task 0, Working' });
  expect(screen.getAllByRole('listitem')).toHaveLength(50);
  fireEvent.click(screen.getByRole('button', { name: 'More delegated tasks' }));
  await screen.findByRole('button', { name: 'Task 52, Working' });
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
  expect(
    screen.queryByRole('button', { name: 'Task 0, Working' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Return to first delegated tasks' }),
  );
  await screen.findByRole('button', { name: 'Task 0, Working' });
  expect(screen.getAllByRole('listitem')).toHaveLength(50);
  expect(load).toHaveBeenNthCalledWith(2, 'next', expect.any(AbortSignal));
});

it('aborts continuation on unmount and ignores its late response', async () => {
  const pending = deferred<DelegatedActivityView>();
  const load = vi
    .fn()
    .mockResolvedValueOnce({ ...page, has_more: true, next_cursor: 'next' })
    .mockReturnValue(pending.promise);
  const view = show({ loadPage: load });
  fireEvent.click(
    await screen.findByRole('button', { name: 'More delegated tasks' }),
  );
  const signal = load.mock.calls[1][1] as AbortSignal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => {
    pending.resolve(page);
  });
  expect(screen.queryByText('Research task')).not.toBeInTheDocument();
});

it('reports content once loads settle, without hiding existing work during a refresh', async () => {
  const first = deferred<DelegatedActivityView>();
  const refreshed = deferred<DelegatedActivityView>();
  const load = vi
    .fn()
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(refreshed.promise);
  const onContentChange = vi.fn();
  const props = {
    conversationId: 'parent-a',
    loadPage: load,
    loadRun: async () => run,
    openConversation: vi.fn().mockResolvedValue(undefined),
    onContentChange,
  };
  const view = render(
    <OverlayProvider>
      <DelegatedActivity {...props} refreshKey="first" />
    </OverlayProvider>,
  );
  await act(async () => first.resolve(page));
  expect(onContentChange).toHaveBeenLastCalledWith(true);
  onContentChange.mockClear();
  view.rerender(
    <OverlayProvider>
      <DelegatedActivity {...props} refreshKey="second" />
    </OverlayProvider>,
  );
  await act(async () => {});
  expect(onContentChange).not.toHaveBeenCalledWith(false);
  await act(async () => refreshed.resolve({ ...page, items: [] }));
  expect(onContentChange).toHaveBeenLastCalledWith(false);
});

it('treats a host without delegated activity as having nothing to show', async () => {
  const onContentChange = vi.fn();
  show({
    loadPage: vi.fn().mockRejectedValue({ code: 'capability_unavailable' }),
    onContentChange,
  });
  expect(
    await screen.findByText('No delegated agents in this conversation.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(onContentChange).toHaveBeenLastCalledWith(false);
});

it('reuses a recent read when the section remounts with the same activity', async () => {
  const load = vi.fn().mockResolvedValue(page);
  const onFirstPage = vi.fn();
  let stored: DelegatedRead | null = null;
  const recentRead = {
    get: () => stored,
    set: (read: DelegatedRead) => {
      stored = read;
    },
  };
  const mount = (refreshKey: string) =>
    show({ refreshKey, loadPage: load, recentRead, onFirstPage });
  const first = mount('event-1');
  expect(await screen.findByRole('button', { name: '1 done' })).toBeVisible();
  first.unmount();
  // The context rail remounts when a panel closes; nothing new happened.
  const second = mount('event-1');
  expect(await screen.findByRole('button', { name: '1 done' })).toBeVisible();
  expect(load).toHaveBeenCalledTimes(1);
  // The reused read still reaches the transcript and the header.
  expect(onFirstPage).toHaveBeenCalledTimes(2);
  second.unmount();
  // New agent activity always reads again.
  mount('event-2');
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole('button', { name: '1 done' })).toBeVisible();
});

it('keeps recent reads for several conversations and evicts the oldest', () => {
  const reads = recentReads(2);
  const read = (key: string) => ({ key, page, at: 1 });
  reads.set(read('a'));
  reads.set(read('b'));
  // Switching back to "a" finds its read; a third conversation evicts "b".
  expect(reads.get('a')?.key).toBe('a');
  reads.set(read('a'));
  reads.set(read('c'));
  expect(reads.get('b')).toBeNull();
  expect(reads.get('a')?.key).toBe('a');
  expect(reads.get('c')?.key).toBe('c');
});

it('reports how many agents are live and working once each page settles', async () => {
  const onLiveChange = vi.fn();
  const next = deferred<DelegatedActivityView>();
  const load = vi
    .fn()
    .mockResolvedValueOnce({
      ...page,
      items: [
        { ...run, run_id: 'run-a', status: 'running' },
        { ...run, run_id: 'run-b', status: 'waiting_approval' },
        { ...run, run_id: 'run-c', status: 'completed' },
      ],
    })
    .mockReturnValueOnce(next.promise);
  const props = {
    conversationId: 'parent-a',
    loadPage: load,
    loadRun: async () => run,
    openConversation: vi.fn().mockResolvedValue(undefined),
    onLiveChange,
  };
  const view = render(
    <OverlayProvider>
      <DelegatedActivity {...props} refreshKey="1" />
    </OverlayProvider>,
  );
  await waitFor(() =>
    expect(onLiveChange).toHaveBeenLastCalledWith({ live: 2, working: 1 }),
  );
  // A refresh in flight keeps the last count instead of reporting zero.
  view.rerender(
    <OverlayProvider>
      <DelegatedActivity {...props} refreshKey="2" />
    </OverlayProvider>,
  );
  await act(async () => {});
  expect(onLiveChange).toHaveBeenCalledTimes(1);
  await act(async () =>
    next.resolve({
      ...page,
      items: [{ ...run, run_id: 'run-a', status: 'completed' }],
    }),
  );
  await waitFor(() =>
    expect(onLiveChange).toHaveBeenLastCalledWith({ live: 0, working: 0 }),
  );
});

it('messages and stops a running agent from inside its row (parity row 9)', async () => {
  const working = { ...run, status: 'running' };
  const stopRun = vi.fn().mockResolvedValue(undefined);
  const messageRun = vi.fn().mockResolvedValue(undefined);
  show({
    loadPage: async () => ({ ...page, items: [working] }),
    stopRun,
    messageRun,
  });
  const actions = await screen.findByRole('group', {
    name: 'Research task actions',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: 'Message Research task' }),
  );
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Message to Research task' }),
    { target: { value: 'Also cover tides.' } },
  );
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send to agent' }));
  });
  expect(messageRun).toHaveBeenCalledWith('run-a', 'Also cover tides.');
  expect(
    await screen.findByText(
      'Message sent. The agent reads it at its next step.',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  await act(async () => {
    fireEvent.click(
      within(actions).getByRole('button', { name: 'Stop Research task' }),
    );
  });
  expect(stopRun).toHaveBeenCalledWith('run-a');
});

it('offers Stop and Message only while an agent is still going', async () => {
  show({
    loadPage: async () => ({
      ...page,
      items: [
        run,
        { ...run, run_id: 'run-f', name: 'Failed task', status: 'failed' },
        { ...run, run_id: 'run-s', name: 'Ending task', status: 'stopping' },
      ],
    }),
    stopRun: vi.fn(),
    messageRun: vi.fn(),
  });
  await screen.findByRole('button', { name: 'Failed task, Failed' });
  expect(
    screen.queryByRole('button', { name: 'Stop Failed task' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Message Failed task' }),
  ).not.toBeInTheDocument();
  // A stop already asked for leaves only Message.
  expect(
    screen.queryByRole('button', { name: 'Stop Ending task' }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Message Ending task' }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '1 done' }));
  expect(
    screen.queryByRole('button', { name: 'Stop Research task' }),
  ).not.toBeInTheDocument();
});

it('shows a child conversation its own agent as one compact row, with no button back (B242)', async () => {
  const stopRun = vi.fn().mockResolvedValue(undefined);
  show({
    conversationId: 'child-a',
    loadPage: async () => ({
      ...page,
      conversation_id: 'child-a',
      parent_conversation_id: 'parent-a',
      parent_title: 'Q4 launch plan',
      own_run: { ...run, status: 'running' },
      items: [],
    }),
    stopRun,
    messageRun: vi.fn(),
  });
  const own = await screen.findByRole('list', { name: 'This agent' });
  expect(
    within(own).getByRole('group', { name: 'Research task, Working' }),
  ).toBeVisible();
  expect(
    within(own).getByRole('button', { name: 'Message Research task' }),
  ).toBeVisible();
  await act(async () => {
    fireEvent.click(
      within(own).getByRole('button', { name: 'Stop Research task' }),
    );
  });
  expect(stopRun).toHaveBeenCalledWith('run-a');
  // The way back is the header's breadcrumb now.
  expect(screen.queryByRole('button', { name: /back to parent/i })).toBeNull();
  expect(
    screen.queryByText('No delegated agents in this conversation.'),
  ).toBeNull();
});

it('keeps "Message sent" in a child conversation while it re-reads (B163)', async () => {
  const messageRun = vi.fn().mockResolvedValue(undefined);
  let release = () => {};
  const child = {
    ...page,
    conversation_id: 'child-a',
    parent_conversation_id: 'parent-a',
    own_run: { ...run, status: 'waiting_approval' },
    items: [],
  };
  const loadPage = vi
    .fn()
    .mockResolvedValueOnce(child)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve(child);
        }),
    );
  show({ conversationId: 'child-a', loadPage, messageRun, stopRun: vi.fn() });
  const own = await screen.findByRole('list', { name: 'This agent' });
  fireEvent.click(
    within(own).getByRole('button', { name: 'Message Research task' }),
  );
  fireEvent.change(
    within(own).getByRole('textbox', { name: 'Message to Research task' }),
    { target: { value: 'Keep it short.' } },
  );
  await act(async () => {
    fireEvent.click(within(own).getByRole('button', { name: 'Send to agent' }));
  });
  expect(messageRun).toHaveBeenCalledWith('run-a', 'Keep it short.');
  await waitFor(() => expect(loadPage).toHaveBeenCalledTimes(2));
  expect(
    screen.getByText('Message sent. The agent reads it at its next step.'),
  ).toBeInTheDocument();
  await act(async () => release());
  expect(
    screen.getByText('Message sent. The agent reads it at its next step.'),
  ).toBeInTheDocument();
});

it('reports each settled first page, never a later one (B241, B242)', async () => {
  const onFirstPage = vi.fn();
  const load = vi
    .fn()
    .mockResolvedValueOnce({ ...page, has_more: true, next_cursor: 'next' })
    .mockResolvedValue({ ...page, items: [] });
  show({ loadPage: load, onFirstPage });
  fireEvent.click(
    await screen.findByRole('button', { name: 'More delegated tasks' }),
  );
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(onFirstPage).toHaveBeenCalledExactlyOnceWith(
    expect.objectContaining({ next_cursor: 'next' }),
  );
});

it('offers Resume and Dismiss for interrupted agent work (B220)', async () => {
  const resumeWork = vi.fn().mockResolvedValue(undefined);
  const dismissWork = vi
    .fn()
    .mockRejectedValueOnce({ code: 'revision_conflict' })
    .mockResolvedValue(undefined);
  const loadPage = vi.fn(async () => ({
    ...page,
    items: [{ ...run, status: 'interrupted' }],
  }));
  const view = (resumable: boolean) => (
    <OverlayProvider>
      <DelegatedActivity
        conversationId="parent-a"
        refreshKey=""
        loadPage={loadPage}
        loadRun={async () => run}
        openConversation={async () => {}}
        interrupted={{ resumable }}
        resumeWork={resumeWork}
        dismissWork={dismissWork}
      />
    </OverlayProvider>
  );
  const { rerender } = render(view(true));
  const work = await screen.findByRole('group', {
    name: 'Interrupted agent work',
  });
  await act(async () => {
    fireEvent.click(
      within(work).getByRole('button', { name: 'Resume agent work' }),
    );
  });
  expect(resumeWork).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(loadPage).toHaveBeenCalledTimes(2));

  // Nothing left to run: Dismiss only; a failure says why and stays.
  rerender(view(false));
  expect(
    within(work).queryByRole('button', { name: 'Resume agent work' }),
  ).not.toBeInTheDocument();
  await act(async () => {
    fireEvent.click(
      within(work).getByRole('button', { name: 'Dismiss agent work' }),
    );
  });
  expect(within(work).getByRole('alert')).not.toBeEmptyDOMElement();
  await act(async () => {
    fireEvent.click(
      within(work).getByRole('button', { name: 'Dismiss agent work' }),
    );
  });
  expect(dismissWork).toHaveBeenCalledTimes(2);
  expect(within(work).queryByRole('alert')).not.toBeInTheDocument();
  expect(resumeWork).toHaveBeenCalledTimes(1);
});

it('shows no interrupted-work controls when nothing waits on the person', async () => {
  show({ interrupted: null, resumeWork: vi.fn(), dismissWork: vi.fn() });
  await screen.findByRole('button', { name: '1 done' });
  expect(
    screen.queryByRole('group', { name: 'Interrupted agent work' }),
  ).not.toBeInTheDocument();
});
