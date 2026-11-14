import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DraftsList, SAVE_AFTER_MS } from './Drafts';
import { proactiveStore } from './proactive-state';
import { draft, fakeIO } from './test-data';

function open(io: ReturnType<typeof fakeIO>) {
  const store = proactiveStore(io);
  return render(
    <DraftsList
      store={store}
      openId="fedcba9876543210"
      onOpen={() => undefined}
    />,
  );
}

describe('Drafts', () => {
  it('lists replies by subject and who they go to, and opens one', async () => {
    const io = fakeIO({ drafts: [draft()] });
    const onOpen = vi.fn();
    render(
      <DraftsList store={proactiveStore(io)} openId={null} onOpen={onOpen} />,
    );
    await userEvent.click(
      await screen.findByRole('button', {
        name: /Re: Q3 numbers — to priya@example\.com/,
      }),
    );
    expect(onOpen).toHaveBeenCalledWith('fedcba9876543210');
  });

  it('says who it replies to and saves edits to Row-Bot as you type', async () => {
    const io = fakeIO({ drafts: [draft()] });
    open(io);
    expect(
      await screen.findByRole('heading', { name: 'Re: Q3 numbers' }),
    ).toBeVisible();
    expect(screen.getByText(/Reply in “Q3 numbers” · Gmail/)).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'To' })).toHaveValue(
      'priya@example.com',
    );
    const text = screen.getByRole('textbox', { name: 'Reply text' });
    await userEvent.clear(text);
    await userEvent.type(text, 'Hi Priya, any news on Q3?');
    expect(screen.getByText('Saving…')).toBeVisible();
    await waitFor(
      () =>
        expect(io.saveDraft).toHaveBeenLastCalledWith('fedcba9876543210', {
          to: 'priya@example.com',
          subject: 'Re: Q3 numbers',
          body: 'Hi Priya, any news on Q3?',
        }),
      { timeout: SAVE_AFTER_MS + 1000 },
    );
    expect(await screen.findByText(/^Saved /)).toBeVisible();
    expect(io.askToSend).not.toHaveBeenCalled();
  });

  it('Send… shows the approval card and only its Send sends', async () => {
    const io = fakeIO({ drafts: [draft()] });
    open(io);
    await userEvent.click(await screen.findByRole('button', { name: /Send…/ }));
    const card = await screen.findByRole('complementary', {
      name: /Approval required: send this reply to priya@example\.com/,
    });
    expect(card).toHaveTextContent(
      'Hi Priya, just checking on the Q3 numbers.',
    );
    expect(card).toHaveTextContent(
      'Row-Bot wrote this reply on its own, so only you can send it.',
    );
    expect(io.answerSend).not.toHaveBeenCalled();
    // The text can't change under an open card.
    expect(screen.getByRole('textbox', { name: 'Reply text' })).toHaveAttribute(
      'readonly',
    );
    await userEvent.click(screen.getByRole('button', { name: /^Send/ }));
    expect(io.answerSend).toHaveBeenCalledWith('fedcba9876543210', {
      binding: 'b'.repeat(64),
      approved: true,
    });
    expect(await screen.findByText(/^Sent /)).toBeVisible();
    expect(screen.queryByRole('button', { name: /Send…/ })).toBeNull();
  });

  it("Don't send sends nothing and returns to the reply", async () => {
    const io = fakeIO({ drafts: [draft()] });
    open(io);
    await userEvent.click(await screen.findByRole('button', { name: /Send…/ }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Don’t send' }),
    );
    expect(io.answerSend).toHaveBeenCalledWith('fedcba9876543210', {
      binding: 'b'.repeat(64),
      approved: false,
    });
    expect(await screen.findByRole('button', { name: /Send…/ })).toBeVisible();
    expect(screen.queryByText(/^Sent /)).toBeNull();
  });

  it('says why a reply cannot be sent', async () => {
    const io = fakeIO({
      drafts: [draft({ send_blocked: 'Sign in to Gmail to send.' })],
    });
    open(io);
    expect(await screen.findByText('Sign in to Gmail to send.')).toBeVisible();
    expect(screen.getByRole('button', { name: /Send…/ })).toBeDisabled();
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
