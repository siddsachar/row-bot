import { expect, it } from 'vitest';
import { readableValue } from './tool-activity';

it('shows an empty nested value, which is still sent, and keeps list items apart', () => {
  expect(readableValue([{ title: 'Done', status: null }])).toBe(
    'title Done, status empty',
  );
  expect(readableValue(['a, b', 'c'])).toBe('"a, b", c');
  expect(readableValue(['a', 'b'])).toBe('a, b');
});
