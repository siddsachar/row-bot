import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { AttentionSnapshot } from '../../api/types';
import AttentionIndicator, { remindLaterAbout } from './AttentionIndicator';

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
