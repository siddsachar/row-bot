import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ClientPlatform } from '../platform';
import type { BuddyPetRequest } from '../platform/types';
import { BuddyPet } from './BuddyPet';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function platform() {
  const requests: BuddyPetRequest[] = [];
  const moves: [number, number][] = [];
  const fake = {
    buddyPet: vi.fn(async (request: BuddyPetRequest) => {
      requests.push(request);
      if (request.action === 'layout')
        return {
          status: 'ok' as const,
          value: request.expanded
            ? { side: 'left' as const, petX: 388, petY: 124, expanded: true }
            : { side: 'right' as const, petX: 0, petY: 0, expanded: false },
        };
      if (request.action === 'state')
        return {
          status: 'ok' as const,
          value: { cursor: { x: 10, y: 20, age: 0.1 }, landed: 0 },
        };
      return { status: 'ok' as const, value: { accepted: true } };
    }),
    moveWindow: vi.fn((x: number, y: number) => {
      moves.push([x, y]);
      return true;
    }),
  } as unknown as ClientPlatform;
  return { fake, requests, moves };
}

function Pet({ host }: { host: ClientPlatform }) {
  const [open, setOpen] = useState(false);
  return (
    <BuddyPet
      platform={host}
      pack={null}
      snapshot={null}
      loadMedia={vi.fn()}
      open={open}
      onOpenChange={setOpen}
      onOpenMain={vi.fn()}
      onDock={vi.fn()}
      onHide={vi.fn()}
    >
      <p>Card content</p>
    </BuddyPet>
  );
}

describe('BuddyPet', () => {
  it('reports its layout and click mask, and opens its bubble on a click', async () => {
    const { fake, requests } = platform();
    render(<Pet host={fake} />);
    await act(async () => {});
    expect(requests).toContainEqual({ action: 'layout', expanded: false });
    const mask = requests.find((request) => request.action === 'hit');
    expect(mask).toMatchObject({ pet: [0, 0, 168, 176], bubble: null });
    // Until Buddy's silhouette is read, the whole box takes clicks.
    expect(atob((mask as { cells: string }).cells)).toHaveLength(72);

    const pet = screen.getByRole('button', { name: 'Show Buddy’s bubble' });
    fireEvent.pointerDown(pet, {
      button: 0,
      pointerId: 1,
      screenX: 50,
      screenY: 50,
    });
    fireEvent.pointerUp(pet, { pointerId: 1, screenX: 50, screenY: 50 });
    await act(async () => {});
    expect(screen.getByText('Card content')).toBeInTheDocument();
    expect(requests).toContainEqual({ action: 'layout', expanded: true });
    // The bubble sits on the side the host chose, Buddy where the host put it.
    expect(
      screen.getByRole('region', { name: 'Buddy’s bubble' }),
    ).toHaveAttribute('data-side', 'left');
    expect(pet).toHaveStyle({ left: '388px', top: '124px' });

    fireEvent.keyDown(window, { key: 'Escape' });
    await act(async () => {});
    expect(screen.queryByText('Card content')).not.toBeInTheDocument();
  });

  it('turns a press that moves into a drag that ends in a landing, not a click', async () => {
    const { fake, requests, moves } = platform();
    const frame = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((callback) => {
        callback(0);
        return 1;
      });
    render(<Pet host={fake} />);
    await act(async () => {});
    const pet = screen.getByRole('button', { name: 'Show Buddy’s bubble' });
    fireEvent.pointerDown(pet, {
      button: 0,
      pointerId: 2,
      clientX: 80,
      clientY: 90,
      screenX: 500,
      screenY: 400,
    });
    fireEvent.pointerMove(pet, { pointerId: 2, screenX: 560, screenY: 380 });
    fireEvent.pointerUp(pet, { pointerId: 2, screenX: 560, screenY: 380 });
    await act(async () => {});
    expect(requests).toContainEqual({ action: 'drag', phase: 'start' });
    expect(requests).toContainEqual({ action: 'drag', phase: 'end' });
    expect(moves.at(-1)).toEqual([480, 290]);
    expect(screen.queryByText('Card content')).not.toBeInTheDocument();
    frame.mockRestore();
  });

  it('opens a small menu on right-click', async () => {
    const { fake } = platform();
    render(<Pet host={fake} />);
    await act(async () => {});
    fireEvent.contextMenu(
      screen.getByRole('button', { name: 'Show Buddy’s bubble' }),
    );
    expect(
      screen.getByRole('navigation', { name: 'Buddy menu' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Dock Buddy' }),
    ).toBeInTheDocument();
  });
});
