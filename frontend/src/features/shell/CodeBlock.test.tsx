import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ContextType } from 'react';
import { expect, it, vi } from 'vitest';
import { RuntimeContext } from '../../runtime';
import { CodeBlock } from './CodeBlock';

it('saves the code the one way every download saves, and says where', async () => {
  const saveOffer = vi.fn(async () => ({
    reference: `save:${'a'.repeat(32)}`,
    file_name: 'code.python',
  }));
  const save = vi.fn(async () => ({
    status: 'ok' as const,
    value: { kind: 'file' as const },
  }));
  const runtime = {
    controller: { saveOffer },
    platform: { save },
  } as unknown as NonNullable<ContextType<typeof RuntimeContext>>;
  render(
    <RuntimeContext.Provider value={runtime}>
      <CodeBlock language="python" text="print('tides')" />
    </RuntimeContext.Provider>,
  );

  await userEvent.click(screen.getByRole('button', { name: 'Download code' }));

  expect(saveOffer).toHaveBeenCalledWith({
    kind: 'content',
    name: 'code.python',
    content_base64: btoa("print('tides')"),
  });
  expect(save).toHaveBeenCalledWith(`save:${'a'.repeat(32)}`, 'code.python');
  expect(screen.queryByRole('status')).toBeNull();
});

it('offers no download outside the app, where there is nothing to save with', () => {
  render(<CodeBlock language="python" text="print('tides')" />);
  expect(screen.queryByRole('button', { name: 'Download code' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Copy code' })).toBeVisible();
});
render(<CodeBlock language="python" text="print('tides')" />);
