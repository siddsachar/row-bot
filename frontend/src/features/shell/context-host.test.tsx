import { render, screen } from '@testing-library/react';
import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { expect, it } from 'vitest';
import {
  ContextHostContext,
  ContextSlot,
  useContextHost,
  useContextHostOwner,
} from './context-host';

let mounts = 0;
function Rail() {
  useEffect(() => {
    mounts += 1;
  }, []);
  return <aside aria-label="Conversation context">Context</aside>;
}
function Portal() {
  const host = useContextHost();
  return host ? createPortal(<Rail />, host.element) : null;
}
function Harness({ place }: { place: 'column' | 'region' | 'none' }) {
  const { host, parking } = useContextHostOwner();
  return (
    <ContextHostContext.Provider value={host}>
      <div data-testid="parking" ref={parking} hidden />
      <Portal />
      <section data-testid="column">
        <ContextSlot active={place === 'column'} />
      </section>
      <section data-testid="region">
        <ContextSlot active={place === 'region'} />
      </section>
    </ContextHostContext.Provider>
  );
}

it('moves one mounted Context between slots without remounting it (B18)', () => {
  mounts = 0;
  const view = render(<Harness place="column" />);
  const rail = () => screen.getByRole('complementary', { hidden: true });
  expect(screen.getByTestId('column')).toContainElement(rail());
  view.rerender(<Harness place="region" />);
  expect(screen.getByTestId('region')).toContainElement(rail());
  view.rerender(<Harness place="none" />);
  expect(screen.getByTestId('parking')).toContainElement(rail());
  view.rerender(<Harness place="column" />);
  expect(screen.getByTestId('column')).toContainElement(rail());
  expect(mounts).toBe(1);
});
