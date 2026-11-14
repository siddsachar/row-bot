import { vi } from 'vitest';
import type {
  ProactiveDraftView,
  ProactiveMomentDetail,
  ProactiveMomentView,
  ProactiveSettingsView,
} from '../../api/types';
import type { ProactiveIO } from './proactive-state';

const iso = (date: Date) => date.toISOString();

export function moment(
  fields: Partial<ProactiveMomentView> = {},
): ProactiveMomentView {
  return {
    moment_id: '0123456789abcdef',
    kind: 'follow_up',
    title: 'No reply from Priya Shah · Q3 numbers',
    body: '',
    urgency: 'normal',
    state: 'pending',
    surface: '',
    created_at: iso(new Date(Date.now() - 3_600_000)),
    due_at: '',
    expires_at: '',
    shown_at: '',
    actions: [{ id: 'open_draft', label: 'Review draft' }],
    prepared_ref: 'fedcba9876543210',
    evidence_count: 1,
    ...fields,
  };
}

export function settings(
  fields: Partial<ProactiveSettingsView> = {},
): ProactiveSettingsView {
  return {
    schema_version: 1,
    level: 'quiet',
    levels: [
      { id: 'off', label: 'Off', example: 'Never.' },
      { id: 'quiet', label: 'Quiet', example: 'Up to 3 a day.' },
      { id: 'balanced', label: 'Balanced', example: 'Up to 6 a day.' },
      { id: 'eager', label: 'Eager', example: 'Up to 12 a day.' },
    ],
    quiet_start: '22:00',
    quiet_end: '07:30',
    day_end: '18:00',
    follow_up_days: 3,
    desktop_context: true,
    desktop_context_privacy: 'Never window titles, contents or the screen.',
    sources: [
      { id: 'mail', label: 'Mail', on: true, privacy: 'Reads who replied.' },
      { id: 'calendar', label: 'Calendar', on: true, privacy: 'Reads events.' },
    ],
    kinds: [
      {
        id: 'follow_up',
        label: 'Follow-ups',
        on: true,
        urgent: false,
        quieted: false,
      },
      {
        id: 'meeting_prep',
        label: 'Meeting prep',
        on: true,
        urgent: false,
        quieted: false,
      },
      {
        id: 'date',
        label: 'Dates that matter',
        on: true,
        urgent: false,
        quieted: true,
      },
    ],
    channel: '',
    rules: [],
    privacy: 'Short summaries go to your main model.',
    ...fields,
  } as ProactiveSettingsView;
}

export function detail(
  of: ProactiveMomentView,
  fields: Partial<ProactiveMomentDetail> = {},
): ProactiveMomentDetail {
  return {
    schema_version: 1,
    moment: of,
    conversation_id: '',
    summary: 'You asked Priya for the Q3 numbers on Monday; no reply since.',
    evidence: [
      {
        type: 'message',
        id: 'm1',
        label: 'Q3 numbers',
        source: 'gmail',
      },
    ],
    why: ['Quiet: 1 of 3 today.', 'It waited for a break: you came back.'],
    history: [],
    ...fields,
  };
}

export function draft(
  fields: Partial<ProactiveDraftView> = {},
): ProactiveDraftView {
  return {
    schema_version: 1,
    draft_id: 'fedcba9876543210',
    moment_id: '0123456789abcdef',
    kind: 'reply',
    title: 'Re: Q3 numbers',
    body: 'Hi Priya, just checking on the Q3 numbers.',
    created_at: iso(new Date()),
    ...fields,
  };
}

/** A fake of every Proactive call; `moments` and `settings` are what reads return. */
export function fakeIO(
  state: {
    moments?: ProactiveMomentView[];
    settings?: ProactiveSettingsView;
    drafts?: ProactiveDraftView[];
  } = {},
) {
  let current = settings(state.settings);
  const listeners = new Set<() => void>();
  const io = {
    moments: vi.fn(async () => ({
      schema_version: 1 as const,
      items: state.moments ?? [],
    })),
    moment: vi.fn(async (id: string) =>
      detail(
        (state.moments ?? []).find((item) => item.moment_id === id) ??
          moment({ moment_id: id }),
      ),
    ),
    feedback: vi.fn(async (id: string, feedback: { action: string }) => {
      const found =
        (state.moments ?? []).find((item) => item.moment_id === id) ??
        moment({ moment_id: id });
      const settled = feedback.action === 'not_now' ? 'pending' : 'dismissed';
      if (feedback.action === 'less_like_this')
        current = {
          ...current,
          rules: [
            ...current.rules,
            {
              rule_id: 'aaaaaaaaaaaa',
              type: 'fewer',
              kind: found.kind,
              topic: '',
              hour: -1,
              words: 'At most one follow-up a day',
            },
          ],
        };
      return detail({
        ...found,
        state: feedback.action === 'act' ? 'acted' : settled,
      });
    }),
    activity: vi.fn(async () => ({
      schema_version: 1 as const,
      items: [
        {
          entry_id: 2,
          at: iso(new Date()),
          kind: 'pass' as const,
          summary: 'Checked mail, calendar: nothing new.',
          moment_id: '',
        },
      ],
      next_before: null,
    })),
    settings: vi.fn(async () => current),
    changeSettings: vi.fn(async (change: Record<string, unknown>) => {
      const { sources, kinds, ...rest } = change as {
        sources?: Record<string, boolean>;
        kinds?: Record<string, boolean>;
      };
      current = {
        ...current,
        ...(rest as Partial<ProactiveSettingsView>),
        sources: current.sources.map((item) =>
          sources && item.id in sources
            ? { ...item, on: sources[item.id] }
            : item,
        ),
        kinds: current.kinds.map((item) =>
          kinds && item.id in kinds
            ? { ...item, on: kinds[item.id], quieted: false }
            : item,
        ),
      };
      return current;
    }),
    addRule: vi.fn(async () => current),
    changeRule: vi.fn(async () => current),
    deleteRule: vi.fn(async (id: string) => {
      current = {
        ...current,
        rules: current.rules.filter((rule) => rule.rule_id !== id),
      };
      return current;
    }),
    drafts: vi.fn(async () => ({
      schema_version: 1 as const,
      items: state.drafts ?? [],
    })),
    draft: vi.fn(async (id: string) => draft({ draft_id: id })),
    boundary: vi.fn(async () => ({
      schema_version: 1 as const,
      noted: 'app_open' as const,
    })),
    channels: vi.fn(async () => []),
    live: {
      connected: true,
      subscribe: (_topic: string, listener: () => void) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
    /** The server published a `proactive` change. */
    publish: () => listeners.forEach((listener) => listener()),
  };
  return io as typeof io & ProactiveIO;
}
