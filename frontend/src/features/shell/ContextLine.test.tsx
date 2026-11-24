import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import type { ContextUsageView } from '../../api/types';
import ContextLine from './ContextLine';

const measured: ContextUsageView = {
  conversation_id: 'a',
  state: 'live',
  freshness: 'current',
  status: 'ready',
  estimated_input_tokens: 43000,
  usable_input_tokens: 100000,
  compact_at_tokens: 80000,
  native_window_tokens: 128000,
  effective_limit_tokens: 100000,
  model_ref: 'fake/model',
  last_confirmed_input_tokens: null,
  scope: 'agent',
  capacity_state: 'ready',
};

it('says the context will appear before the first measurement, with nothing to open', () => {
  render(<ContextLine usage={null} />);
  const section = screen.getByRole('region', { name: 'Context' });
  expect(section).toHaveTextContent(
    'Context will appear after the next response',
  );
  expect(within(section).queryByRole('button')).toBeNull();
});

it('opens what fills the context from its line once measured', async () => {
  render(<ContextLine usage={measured} />);
  const line = screen.getByRole('button', {
    name: '43% of context · compacts at 80%',
  });
  await act(async () => fireEvent.click(line));
  expect(
    screen.getByRole('dialog', { name: 'Context usage details' }),
  ).toHaveTextContent('Approximately 43,000 of 100,000 usable tokens.');
});
