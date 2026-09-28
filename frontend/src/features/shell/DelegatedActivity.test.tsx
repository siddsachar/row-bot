import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { DelegatedActivityView, DelegatedRun } from '../../api/types';
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

it('opens public detail with focus return and explicitly navigates retained child history', async () => {
  const loadRun = vi.fn().mockResolvedValue(run);
  const open = vi.fn().mockResolvedValue(undefined);
  render(
    <OverlayProvider>
      <DelegatedActivity
        conversationId="parent-a"
        refreshKey=""
        loadPage={async () => page}
        loadRun={loadRun}
        openConversation={open}
      />
    </OverlayProvider>,
  );
  const opener = await screen.findByRole('button', { name: 'Research task' });
  opener.focus();
  fireEvent.click(opener);
  expect(await screen.findByText('Public result')).toBeInTheDocument();
  expect(open).not.toHaveBeenCalled();
  // The dialog closes with its own Close; no second "Back to parent" there.
  expect(screen.queryByRole('button', { name: 'Back to parent' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await act(async () => {});
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await waitFor(() => expect(opener).toHaveFocus());
  fireEvent.click(opener);
  await screen.findByText('Public result');
  fireEvent.click(screen.getByRole('button', { name: 'Open full thread' }));
  await act(async () => {});
  expect(open).toHaveBeenCalledExactlyOnceWith('child-a');
  expect(loadRun).toHaveBeenCalledTimes(3);
});

it('fences late A list/detail callbacks after selecting B and closes the prior overlay', async () => {
  const pending = deferred<DelegatedRun>();
  const open = vi.fn().mockResolvedValue(undefined);
  const props = {
    conversationId: 'parent-a',
    refreshKey: '',
    loadPage: async () => page,
    loadRun: () => pending.promise,
    openConversation: open,
  };
  const view = render(
    <OverlayProvider>
      <DelegatedActivity {...props} />
    </OverlayProvider>,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Research task' }));
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
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('Public result')).not.toBeInTheDocument();
  expect(open).not.toHaveBeenCalled();
});

it('loads full continuation and provides the actual parent link without manufacturing child history', async () => {
  const loadPage = vi
    .fn()
    .mockResolvedValueOnce({
      ...page,
      parent_conversation_id: 'root',
      items: [],
      has_more: true,
      next_cursor: 'next',
    })
    .mockResolvedValue({ ...page, parent_conversation_id: 'root' });
  const open = vi.fn().mockResolvedValue(undefined);
  render(
    <OverlayProvider>
      <DelegatedActivity
        conversationId="parent-a"
        refreshKey=""
        loadPage={loadPage}
        loadRun={async () => ({ ...run, child_conversation_id: null })}
        openConversation={open}
      />
    </OverlayProvider>,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'More delegated tasks' }),
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Research task' }));
  expect(
    await screen.findByText("Its thread isn't available."),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Open full thread' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Back to parent conversation' }),
  );
  expect(open).toHaveBeenCalledExactlyOnceWith('root');
  expect(loadPage).toHaveBeenLastCalledWith('next', expect.any(AbortSignal));
});

it('replaces each 50-row page and can return to the first page', async () => {
  const first = {
    ...page,
    items: Array.from({ length: 50 }, (_, index) => ({
      ...run,
      run_id: `run-${index}`,
      name: `Task ${index}`,
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
    })),
  };
  const load = vi
    .fn()
    .mockResolvedValueOnce(first)
    .mockResolvedValueOnce(last)
    .mockResolvedValue(first);
  render(
    <OverlayProvider>
      <DelegatedActivity
        conversationId="parent-a"
        refreshKey=""
        loadPage={load}
        loadRun={async () => run}
        openConversation={async () => {}}
      />
    </OverlayProvider>,
  );
  await screen.findByRole('button', { name: 'Task 0' });
  expect(screen.getAllByRole('listitem')).toHaveLength(50);
  fireEvent.click(screen.getByRole('button', { name: 'More delegated tasks' }));
  await screen.findByRole('button', { name: 'Task 52' });
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
  expect(
    screen.queryByRole('button', { name: 'Task 0' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Return to first delegated tasks' }),
  );
  await screen.findByRole('button', { name: 'Task 0' });
  expect(screen.getAllByRole('listitem')).toHaveLength(50);
});

it('aborts continuation on unmount and ignores its late response', async () => {
  const pending = deferred<DelegatedActivityView>();
  const load = vi
    .fn()
    .mockResolvedValueOnce({ ...page, has_more: true, next_cursor: 'next' })
    .mockReturnValue(pending.promise);
  const view = render(
    <OverlayProvider>
      <DelegatedActivity
        conversationId="parent-a"
        refreshKey=""
        loadPage={load}
        loadRun={async () => run}
        openConversation={async () => {}}
      />
    </OverlayProvider>,
  );
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

it('orders active agents before recent settled agents in the compact rail view', async () => {
  render(
    <OverlayProvider>
      <DelegatedActivity
        compact
        conversationId="parent-a"
        refreshKey=""
        loadPage={async () => ({
          ...page,
          items: [
            run,
            {
              ...run,
              run_id: 'run-active',
              name: 'Active task',
              status: 'waiting_approval',
            },
          ],
        })}
        loadRun={async () => run}
        openConversation={async () => {}}
      />
    </OverlayProvider>,
  );

  const names = (await screen.findAllByRole('listitem')).map(
    (item) => within(item).getByRole('button').textContent,
  );
  expect(names[0]).toContain('Active task');
  expect(names[1]).toContain('Research task');
  expect(
    screen.queryByRole('heading', { name: 'Delegated tasks' }),
  ).not.toBeInTheDocument();
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
  render(
    <OverlayProvider>
      <DelegatedActivity
        conversationId="parent-a"
        refreshKey=""
        loadPage={vi.fn().mockRejectedValue({ code: 'capability_unavailable' })}
        loadRun={async () => run}
        openConversation={vi.fn().mockResolvedValue(undefined)}
        onContentChange={onContentChange}
      />
    </OverlayProvider>,
  );
  expect(
    await screen.findByText('No delegated agents in this conversation.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(onContentChange).toHaveBeenLastCalledWith(false);
});

it('reuses a recent read when the section remounts with the same activity', async () => {
  const load = vi.fn().mockResolvedValue(page);
  let stored: DelegatedRead | null = null;
  const recentRead = {
    get: () => stored,
    set: (read: DelegatedRead) => {
      stored = read;
    },
  };
  const show = (refreshKey: string) =>
    render(
      <OverlayProvider>
        <DelegatedActivity
          conversationId="parent-a"
          refreshKey={refreshKey}
          loadPage={load}
          loadRun={async () => run}
          openConversation={vi.fn().mockResolvedValue(undefined)}
          recentRead={recentRead}
        />
      </OverlayProvider>,
    );
  const first = show('event-1');
  expect(await screen.findByText('Research task')).toBeVisible();
  first.unmount();
  // The context rail remounts when a panel closes; nothing new happened.
  const second = show('event-1');
  expect(await screen.findByText('Research task')).toBeVisible();
  expect(load).toHaveBeenCalledTimes(1);
  second.unmount();
  // New agent activity always reads again.
  show('event-2');
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(await screen.findByText('Research task')).toBeVisible();
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

it('reports how many delegated agents are live once each page settles', async () => {
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
  await waitFor(() => expect(onLiveChange).toHaveBeenLastCalledWith(2));
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
  await waitFor(() => expect(onLiveChange).toHaveBeenLastCalledWith(0));
});

it('stops and messages a running agent from its detail (parity row 9)', async () => {
  const working = { ...run, status: 'running' };
  const stopRun = vi.fn().mockResolvedValue(undefined);
  const messageRun = vi.fn().mockResolvedValue(undefined);
  render(
    <OverlayProvider>
      <DelegatedActivity
        conversationId="parent-a"
        refreshKey=""
        loadPage={async () => ({ ...page, items: [working] })}
        loadRun={async () => working}
        openConversation={async () => {}}
        stopRun={stopRun}
        messageRun={messageRun}
      />
    </OverlayProvider>,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Research task' }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByRole('status')).toHaveTextContent('Working');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Message' }));
  fireEvent.change(
    within(dialog).getByRole('textbox', { name: 'Message to Research task' }),
    { target: { value: 'Also cover tides.' } },
  );
  await act(async () => {
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Send to agent' }),
    );
  });
  expect(messageRun).toHaveBeenCalledWith('run-a', 'Also cover tides.');
  expect(
    await within(dialog).findByText(
      'Message sent. The agent reads it at its next step.',
    ),
  ).toBeVisible();
  await act(async () => {
    fireEvent.click(within(dialog).getByRole('button', { name: 'Stop' }));
  });
  expect(stopRun).toHaveBeenCalledWith('run-a');
});

it('offers Stop in place on a working row and none on a finished one', async () => {
  const stopRun = vi.fn().mockResolvedValue(undefined);
  render(
    <OverlayProvider>
      <DelegatedActivity
        compact
        conversationId="parent-a"
        refreshKey=""
        loadPage={async () => ({
          ...page,
          items: [
            run,
            {
              ...run,
              run_id: 'run-b',
              name: 'Working task',
              status: 'running',
            },
          ],
        })}
        loadRun={async () => run}
        openConversation={async () => {}}
        stopRun={stopRun}
      />
    </OverlayProvider>,
  );
  const stop = await screen.findByRole('button', {
    name: 'Stop Working task',
  });
  await act(async () => {
    fireEvent.click(stop);
  });
  expect(stopRun).toHaveBeenCalledWith('run-b');
  expect(
    screen.queryByRole('button', { name: 'Stop Research task' }),
  ).not.toBeInTheDocument();
  expect(screen.getByText('Done')).toBeVisible();
});

it('shows a child thread its own agent with Stop and Message', async () => {
  const stopRun = vi.fn().mockResolvedValue(undefined);
  render(
    <OverlayProvider>
      <DelegatedActivity
        conversationId="child-a"
        refreshKey=""
        loadPage={async () => ({
          ...page,
          conversation_id: 'child-a',
          parent_conversation_id: 'parent-a',
          own_run: { ...run, status: 'running' },
          items: [],
        })}
        loadRun={async () => run}
        openConversation={async () => {}}
        stopRun={stopRun}
        messageRun={vi.fn()}
      />
    </OverlayProvider>,
  );
  const own = await screen.findByRole('group', { name: 'This agent' });
  expect(own).toHaveTextContent('Research task');
  expect(own).toHaveTextContent('Working');
  expect(within(own).getByRole('button', { name: 'Message' })).toBeVisible();
  await act(async () => {
    fireEvent.click(within(own).getByRole('button', { name: 'Stop' }));
  });
  expect(stopRun).toHaveBeenCalledWith('run-a');
  expect(
    screen.getByRole('button', { name: 'Back to parent conversation' }),
  ).toBeVisible();
});

it('keeps "Message sent" in a child thread while Agents re-reads (B163)', async () => {
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
  render(
    <OverlayProvider>
      <DelegatedActivity
        conversationId="child-a"
        refreshKey=""
        loadPage={loadPage}
        loadRun={async () => run}
        openConversation={async () => {}}
        stopRun={vi.fn()}
        messageRun={messageRun}
      />
    </OverlayProvider>,
  );
  const own = await screen.findByRole('group', { name: 'This agent' });
  // Its own agent is what this thread has; no "No delegated agents" beside it.
  expect(
    screen.queryByText('No delegated agents in this conversation.'),
  ).toBeNull();
  fireEvent.click(within(own).getByRole('button', { name: 'Message' }));
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
  ).toBeVisible();
  await act(async () => release());
  expect(
    screen.getByText('Message sent. The agent reads it at its next step.'),
  ).toBeVisible();
});
