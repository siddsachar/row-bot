import { describe, expect, it } from 'vitest';
import { relativeTime } from '../../ui/format';
import { moment } from './test-data';
import { forYouLead } from './words';

const now = new Date(2026, 10, 16, 9, 30);
const at = (hour: number, minute = 0) =>
  new Date(2026, 10, 16, hour, minute).toISOString();

describe("Home's lead line with Proactive on", () => {
  it('names what waits and the meeting about to start', () => {
    const lead = forYouLead(
      [
        moment({ moment_id: '0000000000000001' }),
        moment({
          moment_id: '0000000000000002',
          kind: 'meeting_prep',
          urgency: 'urgent',
          group: 'now',
          starts_at: at(9, 40),
        }),
      ],
      now,
      relativeTime,
    );
    expect(lead).toBe(
      `2 things for you · meeting ${relativeTime(at(9, 40), now)}.`,
    );
  });

  it('counts one thing plainly, says the brief is ready alone, and is empty with nothing', () => {
    expect(forYouLead([moment()], now, relativeTime)).toBe('1 thing for you.');
    expect(
      forYouLead(
        [
          moment({
            kind: 'brief',
            title: 'Your morning brief',
            state: 'shown',
          }),
        ],
        now,
        relativeTime,
      ),
    ).toBe('Your morning brief is ready.');
    expect(forYouLead([], now, relativeTime)).toBe('');
    expect(
      forYouLead([moment({ state: 'dismissed' })], now, relativeTime),
    ).toBe('');
  });
});
