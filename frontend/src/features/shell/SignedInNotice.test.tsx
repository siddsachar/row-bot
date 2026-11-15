import { render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import SignedInNotice, { SIGNED_IN_KEY } from './SignedInNotice';

afterEach(() => sessionStorage.clear());

it('says the browser is signed in once, after the claim page signed it in (U1)', async () => {
  sessionStorage.setItem(SIGNED_IN_KEY, '1');
  const { unmount } = render(
    <OverlayProvider>
      <SignedInNotice />
    </OverlayProvider>,
  );

  expect(
    await screen.findByText('This browser is signed in.'),
  ).toBeInTheDocument();
  expect(sessionStorage.getItem(SIGNED_IN_KEY)).toBeNull();

  unmount();
  render(
    <OverlayProvider>
      <SignedInNotice />
    </OverlayProvider>,
  );
  expect(screen.queryByText('This browser is signed in.')).toBeNull();
});

it('stays quiet when the browser was already signed in', () => {
  render(
    <OverlayProvider>
      <SignedInNotice />
    </OverlayProvider>,
  );

  expect(screen.queryByText('This browser is signed in.')).toBeNull();
});
