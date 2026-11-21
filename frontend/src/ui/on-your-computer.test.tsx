import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { OnYourComputer, usePhone } from './on-your-computer';
import { PHONE_LAYOUT } from './use-media';

it('stands in for the tool it names, says why, and goes back to Home', async () => {
  render(
    <MemoryRouter initialEntries={['/?tab=monitor']}>
      <Routes>
        <Route
          path="/"
          element={
            <OnYourComputer what="System Monitor">
              System Monitor’s checks and charts need a bigger screen.
            </OnYourComputer>
          }
        />
      </Routes>
      <Routes>
        <Route path="*" element={<LocationText />} />
      </Routes>
    </MemoryRouter>,
  );
  const notice = screen.getByRole('region', { name: 'System Monitor' });
  expect(notice).toHaveTextContent(
    'System Monitor’s checks and charts need a bigger screen.',
  );
  expect(screen.getByTestId('location')).toHaveTextContent('/?tab=monitor');

  await userEvent.click(screen.getByRole('link', { name: 'Go to Home' }));

  expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/);
});

it('follows the phone layout as the window narrows and widens', () => {
  const listeners = new Set<() => void>();
  let width = 1280;
  vi.mocked(window.matchMedia).mockImplementation(
    (query: string) =>
      ({
        get matches() {
          return query === PHONE_LAYOUT && width < 768;
        },
        media: query,
        addEventListener: (_: string, listener: () => void) =>
          listeners.add(listener),
        removeEventListener: (_: string, listener: () => void) =>
          listeners.delete(listener),
      }) as unknown as MediaQueryList,
  );
  render(<PhoneProbe />);
  expect(screen.getByTestId('phone')).toHaveTextContent('computer');

  act(() => {
    width = 390;
    listeners.forEach((listener) => listener());
  });
  expect(screen.getByTestId('phone')).toHaveTextContent('phone');

  act(() => {
    width = 768;
    listeners.forEach((listener) => listener());
  });
  expect(screen.getByTestId('phone')).toHaveTextContent('computer');
});

function PhoneProbe() {
  return (
    <output data-testid="phone">{usePhone() ? 'phone' : 'computer'}</output>
  );
}

function LocationText() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}
