import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { resetBuddyMoments } from './BuddyMoment';
import { usePetMoment } from './PetMoment';
import { fakeIO, moment } from './test-data';

beforeEach(() => resetBuddyMoments());

function Pet({
  controller,
  platform,
}: {
  controller: ClientController;
  platform: ClientPlatform;
}) {
  return <>{usePetMoment(controller, platform, true).moment}</>;
}

function setUp() {
  const io = fakeIO({
    moments: [
      moment({
        state: 'shown',
        surface: 'buddy',
        shown_at: new Date().toISOString(),
      }),
    ],
  });
  const controller = {
    proactiveMoments: io.moments,
    proactiveMoment: io.moment,
    proactiveFeedback: io.feedback,
    proactiveSettings: io.settings,
    proactiveDrafts: io.drafts,
    live: io.live,
  } as unknown as ClientController;
  const showMainWindow = vi.fn(async () => ({
    status: 'ok' as const,
    value: null,
  }));
  const platform = { showMainWindow } as unknown as ClientPlatform;
  render(<Pet controller={controller} platform={platform} />);
  return { io, showMainWindow };
}

it("the pet's bubble opens the draft itself in the main window", async () => {
  const { io, showMainWindow } = setUp();
  await userEvent.click(
    await screen.findByRole('button', { name: /Review draft/ }),
  );
  expect(showMainWindow).toHaveBeenCalledWith(null, 'draft:fedcba9876543210');
  await waitFor(() =>
    expect(io.feedback).toHaveBeenCalledWith('0123456789abcdef', {
      action: 'act',
      action_id: 'open_draft',
    }),
  );
});

it("the pet's Why this? opens that moment's Why this in the main window", async () => {
  const { showMainWindow } = setUp();
  await userEvent.click(
    await screen.findByRole('button', { name: /More for No reply/ }),
  );
  await userEvent.click(
    await screen.findByRole('menuitem', { name: /Why this/ }),
  );
  expect(showMainWindow).toHaveBeenCalledWith(null, 'why:0123456789abcdef');
});
