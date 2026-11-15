import type { ContextType } from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { GuidePageView } from '../../api/types';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import SafeMarkdown from './chat-parity-markdown';

function Where() {
  const location = useLocation();
  return (
    <output aria-label="location">{`${location.pathname}${location.search}${location.hash}`}</output>
  );
}

function inApp(text: string, guidePage = vi.fn()) {
  const runtime = { controller: { guidePage } } as unknown as NonNullable<
    ContextType<typeof RuntimeContext>
  >;
  return render(
    <RuntimeContext.Provider value={runtime}>
      <OverlayProvider>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route path="*" element={<SafeMarkdown text={text} />} />
          </Routes>
          <Where />
        </MemoryRouter>
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
}

it('shows a Settings link as a chip with the row’s own name and opens it in place', () => {
  inApp(
    'Set the hours in [the dream thing](/settings/preferences#dream-cycle).',
  );

  const chip = screen.getByRole('link', { name: 'Preferences › Dream Cycle' });
  expect(chip).not.toHaveAttribute('target');
  fireEvent.click(chip);
  expect(screen.getByLabelText('location')).toHaveTextContent(
    '/settings/preferences#dream-cycle',
  );
});

it('takes the docs site’s /app-v2 addresses and page links too', () => {
  inApp(
    '[Name](/app-v2/settings/preferences#identity.name) and [models](/settings/models)',
  );

  expect(
    screen.getByRole('link', { name: 'Preferences › Assistant name' }),
  ).toBeVisible();
  expect(screen.getByRole('link', { name: 'Models' })).toBeVisible();
});

it('reads a link to a row that isn’t there as plain words, never a dead link', () => {
  inApp('Open [Vision](/settings/models#vision) to fix it.');

  expect(screen.queryByRole('link')).toBeNull();
  expect(screen.getByText(/Open Vision to fix it\./)).toBeVisible();
});

it('leaves links to other sites as they were', () => {
  inApp('[Example](https://example.test/docs)');

  expect(screen.getByRole('link', { name: 'Example' })).toHaveAttribute(
    'target',
    '_blank',
  );
});

const PAGE: GuidePageView = {
  schema_version: 1,
  route: '/docs/settings/system',
  title: 'Settings: System',
  description: 'What Row-Bot may do on this computer.',
  markdown:
    'System settings describe where Row-Bot works.\n\n## Privacy\n\n1. Open **Settings › System**.\n2. Turn off **Share install stats**.',
  web_url: 'https://row-bot.ai/docs/settings/system',
  sections: [{ anchor: 'privacy', heading: 'Privacy' }],
};

it('opens a cited guide offline in a sheet, at its section, with the web page one click away', async () => {
  const guidePage = vi.fn(async () => PAGE);
  inApp(
    'See [Settings: System › Privacy](/docs/settings/system#privacy).',
    guidePage,
  );
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;

  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Settings: System › Privacy' }),
    );
  });

  const sheet = await screen.findByRole('dialog', {
    name: 'Settings: System › Privacy',
  });
  expect(guidePage).toHaveBeenCalledWith(
    '/docs/settings/system',
    expect.any(AbortSignal),
  );
  expect(await within(sheet).findByText('Share install stats')).toBeVisible();
  expect(
    within(sheet).getByRole('link', { name: 'Open on row-bot.ai' }),
  ).toHaveAttribute('href', 'https://row-bot.ai/docs/settings/system');
  const section = sheet.querySelector('[data-guide-section="privacy"]');
  expect(section).toHaveAttribute('data-highlighted', 'true');
  expect(scroll).toHaveBeenCalledWith({ block: 'start' });
});

it('says so in the sheet when a guide can’t be read', async () => {
  const guidePage = vi.fn(async () => {
    throw Object.assign(new Error('missing'), { code: 'not_found' });
  });
  inApp('[Guide](/docs/settings/nowhere)', guidePage);

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Guide' }));
  });

  const sheet = await screen.findByRole('dialog', { name: 'Guide' });
  expect(await within(sheet).findByRole('alert')).toBeVisible();
});

it('outside the app a guide is its page on the docs site', () => {
  render(<SafeMarkdown text="[Privacy](/docs/settings/system#privacy)" />);

  expect(screen.getByRole('link', { name: 'Privacy' })).toHaveAttribute(
    'href',
    'https://row-bot.ai/docs/settings/system#privacy',
  );
});
