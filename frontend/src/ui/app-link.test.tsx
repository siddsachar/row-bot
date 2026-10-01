import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it } from 'vitest';
import { AppLink } from './app-link';

it('navigates in place inside the router and keeps the basename outside it', () => {
  const { unmount } = render(
    <MemoryRouter>
      <AppLink to="/settings/access#tunnel">Access</AppLink>
    </MemoryRouter>,
  );
  expect(screen.getByRole('link', { name: 'Access' })).toHaveAttribute(
    'href',
    '/settings/access#tunnel',
  );
  unmount();
  render(<AppLink to="/settings/knowledge">Knowledge</AppLink>);
  expect(screen.getByRole('link', { name: 'Knowledge' })).toHaveAttribute(
    'href',
    '/app-v2/settings/knowledge',
  );
});
