import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  AttentionSnapshot,
  PendingApproval,
  PendingApprovalPage,
} from '../../api/types';
import AttentionIndicator, { remindLaterAbout } from './AttentionIndicator';

const approval = vi.fn();
const intent = vi.fn();
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: { approval, intent } }),
}));

function Where() {
  const location = useLocation();
  return (
    <output aria-label="Location">{location.pathname + location.search}</output>
  );
}

function show(snapshot: AttentionSnapshot) {
  const load = vi.fn(async () => snapshot);
  render(
    <MemoryRouter initialEntries={['/c/synthetic']}>
      <AttentionIndicator load={load} />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return load;
}

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

it('stays quiet while everything is healthy', async () => {
  const load = show({ schema_version: 1, problems: [], update: null });
  await act(async () => undefined);
  expect(load).toHaveBeenCalledOnce();
  expect(screen.queryByRole('link')).toBeNull();
});

it('names the problems and opens Monitor', async () => {
  show({
    schema_version: 1,
    problems: [
      {
        id: 'channel:telegram',
        title: 'Telegram stopped',
        detail: 'It is set to start with Row-Bot but isn’t running.',
        place: 'channels',
      },
      {
        id: 'plugin:rss-reader',
        title: 'The plugin rss-reader didn’t load',
        detail: 'Open it in Settings › Plugins.',
        place: 'plugins',
      },
    ],
    // A problem comes first; the update waits.
    update: { version: '9.1.0' },
  });
  const link = await screen.findByRole('link', {
    name: '2 things need attention. Open Monitor',
  });
  fireEvent.click(link);
  expect(screen.getByLabelText('Location')).toHaveTextContent('/?tab=monitor');
});

it('offers an update, opens Updates, and "Remind me later" hides it for a day', async () => {
  show({ schema_version: 1, problems: [], update: { version: '9.1.0' } });
  const link = await screen.findByRole('link', {
    name: 'Update to 9.1.0 available. Open Updates',
  });
  fireEvent.click(link);
  expect(screen.getByLabelText('Location')).toHaveTextContent(
    '/settings/updates',
  );
  act(() => remindLaterAbout('9.1.0'));
  expect(screen.queryByRole('link')).toBeNull();
  // A reminder about another version doesn't hide this one.
  act(() => remindLaterAbout('9.0.9'));
  expect(
    screen.getByRole('link', {
      name: 'Update to 9.1.0 available. Open Updates',
    }),
  ).toBeInTheDocument();
});

function waiting(id: string, title: string): PendingApproval {
  return {
    id,
    source: 'workflow',
    title,
    what: `${title}: continue?`,
    requested_at: '2026-09-30T09:00:00',
    expires_at: null,
    conversation_id: null,
    task_id: `task-${id}`,
  };
}

it('counts waiting approvals and answers them in place (B255)', async () => {
  const pages: PendingApprovalPage[] = [
    {
      schema_version: 1,
      items: [
        waiting('news', 'Daily News'),
        waiting('report', 'Weekly report'),
      ],
      total: 2,
    },
    {
      schema_version: 1,
      items: [waiting('report', 'Weekly report')],
      total: 1,
    },
  ];
  const loadApprovals = vi.fn(async () => pages[0]);
  approval.mockResolvedValue({ id: 'news', revision: '0', nonce: 'nonce' });
  intent.mockResolvedValue({ status: 'completed' });
  render(
    <MemoryRouter>
      <AttentionIndicator
        load={async () => ({
          schema_version: 1,
          problems: [
            {
              id: 'channel:telegram',
              title: 'Telegram stopped',
              detail: 'It isn’t running.',
              place: 'channels',
            },
          ],
          update: null,
        })}
        loadApprovals={loadApprovals}
      />
    </MemoryRouter>,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: '2 approvals are waiting. Review',
    }),
  );
  const list = screen.getByRole('dialog', {
    name: 'Waiting for your approval',
  });
  // The problems stay one step away.
  expect(
    within(list).getByRole('link', { name: /1 thing needs attention/ }),
  ).toHaveAttribute('href', '/?tab=monitor');
  const news = within(list).getByRole('listitem', {
    name: 'Daily News needs your approval',
  });
  loadApprovals.mockImplementation(async () => pages[1]);
  await act(async () =>
    fireEvent.click(within(news).getByRole('button', { name: 'Approve' })),
  );
  expect(intent).toHaveBeenCalledWith(
    'news',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce' },
    '0',
  );
  expect(
    await screen.findByRole('button', {
      name: '1 approval is waiting. Review',
    }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('listitem', { name: 'Daily News needs your approval' }),
  ).toBeNull();
});
