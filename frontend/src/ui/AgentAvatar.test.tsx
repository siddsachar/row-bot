import { render } from '@testing-library/react';
import { expect, it } from 'vitest';
import { AgentAvatar, agentSeed } from './AgentAvatar';

const avatar = (seed: string, size?: number) =>
  render(<AgentAvatar seed={seed} size={size} />).container.querySelector(
    '.agent-avatar',
  )!;

it('draws one agent the same way every time, at any size (B240)', () => {
  const small = avatar('profile-7', 16);
  const large = avatar('profile-7', 32);
  expect(small.getAttribute('data-avatar')).toBe(
    large.getAttribute('data-avatar'),
  );
  expect(small.innerHTML).toBe(large.innerHTML);
  expect(small).toHaveAttribute('aria-hidden', 'true');
  // Different agents spread across the set.
  const looks = new Set(
    Array.from({ length: 40 }, (_, index) =>
      avatar(`run-${index}`).getAttribute('data-avatar'),
    ),
  );
  expect(looks.size).toBeGreaterThan(6);
});

it('takes the icon from the agent’s profile when it has one, else its run', () => {
  expect(agentSeed('profile-7', 'run-1')).toBe('profile-7');
  expect(agentSeed('profile-7', 'run-2')).toBe('profile-7');
  expect(agentSeed('', 'run-1')).toBe('run-1');
  expect(agentSeed(null, 'run-1')).toBe('run-1');
});
