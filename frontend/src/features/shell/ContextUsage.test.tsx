import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import ContextUsage from './ContextUsage';

it('labels absent and stale usage without claiming a current measurement', () => {
  const { rerender } = render(<ContextUsage />);
  expect(screen.getByText('Context ready')).toBeInTheDocument();
  rerender(
    <ContextUsage
      usage={{
        conversation_id: 'a',
        state: 'stale',
        freshness: 'stale',
        status: 'ready',
        estimated_input_tokens: 12345,
        usable_input_tokens: 100000,
        compact_at_tokens: 75000,
        native_window_tokens: 128000,
        effective_limit_tokens: 100000,
        model_ref: 'fake/model',
        last_confirmed_input_tokens: 0,
        scope: 'agent',
        capacity_state: 'ready',
      }}
    />,
  );
  expect(screen.getByText('Context ~12%')).toBeInTheDocument();
  expect(screen.getByRole('meter', { name: 'Context usage' })).toHaveAttribute(
    'aria-valuenow',
    '12345',
  );
  expect(document.querySelector('.context-meter-threshold')).toHaveStyle({
    insetInlineStart: '75%',
  });
  fireEvent.click(screen.getByText('Context ~12%'));
  expect(
    screen.getByText('Last provider-confirmed input: 0 tokens.'),
  ).toBeInTheDocument();
});

it('shows compaction and failure states without pretending capacity exists', () => {
  const usage = {
    conversation_id: 'a',
    state: 'live' as const,
    freshness: 'current' as const,
    status: 'compacting' as const,
    estimated_input_tokens: 80,
    usable_input_tokens: 100,
    compact_at_tokens: 75,
    native_window_tokens: 128,
    effective_limit_tokens: 100,
    model_ref: 'fake/model',
    last_confirmed_input_tokens: null,
    scope: 'chat_only' as const,
    capacity_state: 'ready',
  };
  const { rerender } = render(<ContextUsage usage={usage} />);
  expect(screen.getByText('Compacting context…')).toBeInTheDocument();
  rerender(<ContextUsage usage={{ ...usage, status: 'failed' as const }} />);
  expect(
    screen.getByText('Context 80% · compaction failed'),
  ).toBeInTheDocument();
});
