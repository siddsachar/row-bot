import { act, fireEvent, render, screen } from '@testing-library/react';
import { useEffect } from 'react';
import { expect, it, vi } from 'vitest';
import { OverlayProvider, useOverlay } from './overlays';

function Notify({ onUndo }: { onUndo: () => void }) {
  const overlay = useOverlay();
  useEffect(() => {
    overlay.notify('Removed Tides deck from this conversation.', undefined, {
      label: 'Undo',
      onAction: onUndo,
    });
    // Once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

it('shows an Undo toast for an easy-to-regret removal and runs it once (decision 19)', async () => {
  const onUndo = vi.fn();
  render(
    <OverlayProvider>
      <Notify onUndo={onUndo} />
    </OverlayProvider>,
  );
  expect(
    await screen.findByText('Removed Tides deck from this conversation.'),
  ).toBeInTheDocument();
  const undo = screen.getByRole('button', { name: 'Undo' });
  await act(async () => fireEvent.click(undo));
  expect(onUndo).toHaveBeenCalledTimes(1);
  expect(
    screen.queryByText('Removed Tides deck from this conversation.'),
  ).toBeNull();
});
