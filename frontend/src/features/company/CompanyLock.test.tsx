import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import CompanyLock from './CompanyLock';

it('says “Set by your company”, with the setting in plain words on focus and for screen readers', async () => {
  const user = userEvent.setup();
  render(<CompanyLock detail="Allow personal API keys: Off" />);

  expect(screen.getByText('Set by your company')).toBeVisible();
  // Nothing to press: it is no control.
  expect(screen.queryByRole('button')).toBeNull();
  expect(screen.getByText(': Allow personal API keys: Off')).toHaveClass(
    'visually-hidden',
  );

  await user.tab();
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    'Allow personal API keys: Off',
  );
});
