import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { encode } from 'uqr';
import { QrCode } from './QrCode';

it('draws the encoded modules as one labelled path', () => {
  const value = 'https://example.invalid/published/deck.html';
  render(<QrCode value={value} label="QR code for the published link" />);
  const image = screen.getByRole('img', {
    name: 'QR code for the published link',
  });
  const expected = encode(value, { ecc: 'M', border: 2 });
  expect(image).toHaveAttribute(
    'viewBox',
    `0 0 ${expected.size} ${expected.size}`,
  );
  const dark = expected.data.flat().filter(Boolean).length;
  const path = image.querySelector('path')?.getAttribute('d') ?? '';
  expect(path.match(/M/g)).toHaveLength(dark);
  expect(image.querySelector('rect')).toHaveAttribute('fill', '#fff');
});

it('changes with the value and keeps the same drawing for the same value', () => {
  const view = render(<QrCode value="a" label="Code" />);
  const first = view.container.querySelector('path')?.getAttribute('d');
  view.rerender(<QrCode value="a" label="Code" />);
  expect(view.container.querySelector('path')?.getAttribute('d')).toBe(first);
  view.rerender(<QrCode value="b" label="Code" />);
  expect(view.container.querySelector('path')?.getAttribute('d')).not.toBe(
    first,
  );
});
