import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DraftsList } from './Drafts';
import { proactiveStore } from './proactive-state';
import { draft, fakeIO } from './test-data';

describe('Drafts', () => {
  it('lists drafts; one opens to edit, and sending goes to a chat, never straight out', async () => {
    const io = fakeIO({ drafts: [draft()] });
    const store = proactiveStore(io);
    const onSendFromChat = vi.fn();
    const onOpen = vi.fn();
    const { rerender } = render(
      <DraftsList
        store={store}
        openId={null}
        onOpen={onOpen}
        onSendFromChat={onSendFromChat}
      />,
    );
    await userEvent.click(
      await screen.findByRole('button', { name: /Re: Q3 numbers/ }),
    );
    expect(onOpen).toHaveBeenCalledWith('fedcba9876543210');
    rerender(
      <DraftsList
        store={store}
        openId="fedcba9876543210"
        onOpen={onOpen}
        onSendFromChat={onSendFromChat}
      />,
    );
    const text = await screen.findByRole('textbox', { name: 'Reply text' });
    await userEvent.clear(text);
    await userEvent.type(text, 'Hi Priya, any news on Q3?');
    expect(screen.getByText(/^Saved /)).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: /Send…/ }));
    // The edited text waits in a new chat; the person sends it under approval.
    expect(onSendFromChat).toHaveBeenCalledWith(
      expect.stringContaining('Hi Priya, any news on Q3?'),
    );
    // Nothing reported or sent on the server's side.
    expect(io.feedback).not.toHaveBeenCalled();
    expect(io.changeSettings).not.toHaveBeenCalled();
  });

  it('says so when there are none', async () => {
    render(
      <DraftsList
        store={proactiveStore(fakeIO({ drafts: [] }))}
        openId={null}
        onOpen={() => undefined}
      />,
    );
    expect(await screen.findByText(/No drafts\./)).toBeVisible();
  });
});
