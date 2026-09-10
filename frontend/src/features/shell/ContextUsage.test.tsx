import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import ContextUsage from './ContextUsage';

it('labels absent and stale usage without claiming a current measurement', () => {
  const { rerender } = render(<ContextUsage />);
  expect(
    screen.getByText('Context usage is available after a response.'),
  ).toBeInTheDocument();
  rerender(
    <ContextUsage
      usage={{
        conversation_id: 'a',
        state: 'stale',
        estimated_input_tokens: 12345,
        usable_input_tokens: null,
        model_ref: 'fake/model',
        last_confirmed_input_tokens: 0,
      }}
    />,
  );
  expect(
    screen.getByText(
      'Last saved context: 12,345 estimated tokens · out of date',
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/next request may use a different amount/),
  ).toBeInTheDocument();
  expect(
    screen.getByText('Last provider-confirmed input: 0 tokens.'),
  ).toBeInTheDocument();
});
