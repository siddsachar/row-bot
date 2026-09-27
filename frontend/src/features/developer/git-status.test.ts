import { expect, it } from 'vitest';
import { parseTracking } from './git-status';

it('reads upstream, ahead and behind from the short status header', () => {
  expect(parseTracking('## main...origin/main [ahead 2, behind 1]')).toEqual({
    upstream: 'origin/main',
    ahead: 2,
    behind: 1,
    unborn: false,
  });
  expect(parseTracking('## feat/x...origin/feat/x')).toMatchObject({
    upstream: 'origin/feat/x',
    ahead: 0,
    behind: 0,
  });
  expect(parseTracking('## main')).toMatchObject({ upstream: '', ahead: 0 });
  expect(parseTracking('## No commits yet on main').unborn).toBe(true);
  expect(parseTracking('')).toMatchObject({ upstream: '', unborn: false });
});
