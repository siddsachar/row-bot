import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { DelegatedActivityView, DelegatedRun } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import DelegatedActivity from './DelegatedActivity';

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
  fireEvent.click(screen.getByRole('button', { name: 'Back to parent' }));
  await act(async () => {});
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await waitFor(() => expect(opener).toHaveFocus());
  fireEvent.click(opener);
  await screen.findByText('Public result');
  fireEvent.click(
    screen.getByRole('button', { name: 'Open child conversation' }),
  );
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
    await screen.findByText('Child conversation history is unavailable.'),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Open child conversation' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Back to parent' }));
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
