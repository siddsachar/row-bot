import { fireEvent, render, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ClientPlatform } from '../../platform';
import { BuddyDragHandle } from './BuddyGesture';

describe('Buddy drag-away', () => {
  it('keeps taps, threshold moves, dock drops, and cancellation local', async () => {
    const buddyPlacement = vi.fn().mockResolvedValue({
      status: 'ok',
      value: { placement: 'desktop', visible: true },
    });
    const onTornOff = vi.fn();
    const view = render(
      <BuddyDragHandle
        platform={{ buddyPlacement } as unknown as ClientPlatform}
        onTornOff={onTornOff}
      >
        <span>Buddy</span>
      </BuddyDragHandle>,
    );
    const handle = view.container.querySelector('.buddy-drag-handle')!;
    vi.spyOn(handle, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      right: 100,
      bottom: 100,
    } as DOMRect);
    Object.defineProperty(handle, 'setPointerCapture', { value: vi.fn() });
    fireEvent.pointerDown(handle, {
      button: 0,
      pointerId: 1,
      clientX: 20,
      clientY: 20,
    });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 25, clientY: 20 });
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 25, clientY: 20 });
    expect(buddyPlacement).not.toHaveBeenCalled();
    fireEvent.pointerDown(handle, {
      button: 0,
      pointerId: 2,
      clientX: 20,
      clientY: 20,
    });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 40, clientY: 20 });
    fireEvent.pointerUp(handle, { pointerId: 2, clientX: 40, clientY: 20 });
    expect(buddyPlacement).not.toHaveBeenCalled();
    fireEvent.pointerDown(handle, {
      button: 0,
      pointerId: 3,
      clientX: 20,
      clientY: 20,
    });
    fireEvent.pointerMove(handle, { pointerId: 3, clientX: 40, clientY: 20 });
    fireEvent.pointerCancel(handle, { pointerId: 3 });
    fireEvent.pointerUp(handle, { pointerId: 3, clientX: 140, clientY: 140 });
    expect(buddyPlacement).not.toHaveBeenCalled();
    fireEvent.pointerDown(handle, {
      button: 0,
      pointerId: 4,
      clientX: 20,
      clientY: 20,
    });
    fireEvent.pointerMove(handle, { pointerId: 4, clientX: 40, clientY: 20 });
    fireEvent.pointerUp(handle, {
      pointerId: 4,
      clientX: 140,
      clientY: 140,
      screenX: 500,
      screenY: 300,
    });
    await waitFor(() => expect(onTornOff).toHaveBeenCalledTimes(1));
    expect(buddyPlacement).toHaveBeenCalledWith('tear_off', { x: 500, y: 300 });
  });

  it('restores the docked visual when native placement is unavailable', async () => {
    const buddyPlacement = vi
      .fn()
      .mockResolvedValue({ status: 'unavailable', reason: 'browser' });
    const view = render(
      <BuddyDragHandle
        platform={{ buddyPlacement } as unknown as ClientPlatform}
        onTornOff={vi.fn()}
      >
        <span>Buddy</span>
      </BuddyDragHandle>,
    );
    const handle = view.container.querySelector('.buddy-drag-handle')!;
    vi.spyOn(handle, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      right: 100,
      bottom: 100,
    } as DOMRect);
    Object.defineProperty(handle, 'setPointerCapture', { value: vi.fn() });
    fireEvent.pointerDown(handle, {
      button: 0,
      pointerId: 1,
      clientX: 20,
      clientY: 20,
    });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 40, clientY: 20 });
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 140, clientY: 140 });
    await waitFor(() =>
      expect(view.getByRole('status')).toHaveTextContent('unavailable'),
    );
    expect(handle).toHaveAttribute('data-dragging', 'false');
  });
});
