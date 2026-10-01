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

  const drag = (buddyPlacement: ReturnType<typeof vi.fn>) => {
    const onTornOff = vi.fn();
    const view = render(
      <BuddyDragHandle
        platform={{ buddyPlacement } as unknown as ClientPlatform}
        onTornOff={onTornOff}
      >
        <span>Buddy</span>
      </BuddyDragHandle>,
    );
    const handle =
      view.container.querySelector<HTMLElement>('.buddy-drag-handle')!;
    vi.spyOn(handle, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      right: 100,
      bottom: 100,
      height: 100,
    } as DOMRect);
    Object.defineProperty(handle, 'setPointerCapture', { value: vi.fn() });
    const start = (pointerId: number) => {
      fireEvent.pointerDown(handle, {
        button: 0,
        pointerId,
        clientX: 20,
        clientY: 20,
      });
      fireEvent.pointerMove(handle, { pointerId, clientX: 40, clientY: 20 });
      expect(handle).toHaveAttribute('data-dragging', 'true');
    };
    return { view, handle, start, onTornOff };
  };

  it('says why a tear-off was refused beside the avatar, and clears it on the next drag (B224)', async () => {
    const buddyPlacement = vi.fn().mockResolvedValue({
      status: 'unavailable',
      reason: 'native_reconnecting',
    });
    const { view, handle, start } = drag(buddyPlacement);
    start(1);
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 140, clientY: 140 });
    expect(handle).toHaveAttribute('data-dragging', 'false');
    const notice = await view.findByRole('status');
    expect(notice).toHaveTextContent(
      'Desktop features are reconnecting. Try again in a moment.',
    );
    // A readable notice of its own, never squeezed inside the avatar.
    expect(handle).not.toContainElement(notice);
    expect(view.container).not.toContainElement(notice);
    fireEvent.pointerDown(handle, {
      button: 0,
      pointerId: 2,
      clientX: 20,
      clientY: 20,
    });
    expect(view.queryByRole('status')).toBeNull();
  });

  it('names a refusal from the desktop host in plain words', async () => {
    const buddyPlacement = vi.fn().mockResolvedValue({
      status: 'unavailable',
      reason: 'native_operation_unavailable',
    });
    const { view, handle, start } = drag(buddyPlacement);
    start(1);
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 140, clientY: 140 });
    expect(await view.findByRole('status')).toHaveTextContent(
      'Buddy couldn’t open its own window.',
    );
  });

  it('puts the avatar back when the drag loses its pointer capture', () => {
    const buddyPlacement = vi.fn();
    const { handle, start } = drag(buddyPlacement);
    start(1);
    fireEvent.lostPointerCapture(handle, { pointerId: 1 });
    expect(handle).toHaveAttribute('data-dragging', 'false');
    expect(handle.style.transform).toBe('');
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 140, clientY: 140 });
    expect(buddyPlacement).not.toHaveBeenCalled();
  });

  it('ends a drag let go outside the avatar and puts it back', async () => {
    const buddyPlacement = vi.fn().mockResolvedValue({
      status: 'ok',
      value: { placement: 'desktop', visible: true },
    });
    const { handle, start, onTornOff } = drag(buddyPlacement);
    start(1);
    // The pointer is released over another element: only the window hears it.
    fireEvent.pointerUp(document.body, {
      pointerId: 1,
      clientX: 400,
      clientY: 300,
      screenX: 900,
      screenY: 600,
    });
    expect(handle).toHaveAttribute('data-dragging', 'false');
    await waitFor(() => expect(onTornOff).toHaveBeenCalledTimes(1));
    expect(buddyPlacement).toHaveBeenCalledWith('tear_off', { x: 900, y: 600 });
    // A cancelled drag puts it back too.
    start(2);
    fireEvent.pointerCancel(window, { pointerId: 2 });
    expect(handle).toHaveAttribute('data-dragging', 'false');
  });
});

describe('native image drag (B101)', () => {
  it('never lets the avatar image start a browser drag that cancels the gesture', () => {
    const view = render(
      <BuddyDragHandle
        platform={{ buddyPlacement: vi.fn() } as unknown as ClientPlatform}
        onTornOff={vi.fn()}
      >
        <img alt="" src="data:image/png;base64,AA==" />
      </BuddyDragHandle>,
    );
    const image = view.container.querySelector('img')!;
    const drag = new Event('dragstart', { bubbles: true, cancelable: true });
    image.dispatchEvent(drag);
    expect(drag.defaultPrevented).toBe(true);
  });
});
