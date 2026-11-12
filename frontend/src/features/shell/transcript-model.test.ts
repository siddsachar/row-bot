import { describe, expect, it } from 'vitest';
import type {
  EventRecord,
  TranscriptRow,
  TranscriptTraceGroup,
} from '../../api/types';
import {
  buildTranscript,
  keepUnchanged,
  liveMedia,
  liveTurn,
  type TranscriptItem,
} from './transcript-model';
import { cardKey } from './TranscriptCards';

/*
 * A turn of several model steps, each with a line of narration and a tool
 * step, some making videos (5.3.3): it reads in the order it happened, live
 * and after a reload.
 */

let revision = 0;
function record(type: string, payload: object): EventRecord {
  revision += 1;
  return {
    cursor: String(revision),
    event: {
      type,
      event_id: `event-${revision}`,
      projection_revision: String(revision),
      payload,
    },
  } as unknown as EventRecord;
}

const PASS = 'pass-1';
const user: TranscriptRow = {
  id: 'user:submission:u1',
  role: 'user',
  blocks: [{ type: 'text', text: 'Make the sign-up videos.' }],
};

/** What the live turn sends: a step's words, its tool step, what it made. */
class LiveRun {
  rows: TranscriptRow[] = [user];
  activity: EventRecord[] = [];

  say(segment: string, text: string) {
    revision += 1;
    const id = `assistant:live:${PASS}:${segment}`;
    const row: TranscriptRow = {
      id,
      role: 'assistant',
      blocks: [{ type: 'text', text }],
      render_revision: String(revision),
    };
    const index = this.rows.findIndex((value) => value.id === id);
    this.rows =
      index < 0
        ? [...this.rows, row]
        : this.rows.map((value, at) => (at === index ? row : value));
  }

  call(segment: string, call: string, tool: string, prompt = '') {
    this.activity.push(
      record('tool.activity', {
        state: 'tool_call',
        tool_call_id: call,
        tool_name: tool,
        pass_id: PASS,
        segment_id: segment,
        group_id: `${segment}:generic:${tool}`,
        status: 'pending',
        safe_input: prompt ? JSON.stringify({ prompt }) : '',
      }),
    );
  }

  done(
    segment: string,
    call: string,
    tool: string,
    {
      media = [] as string[],
      specialization,
    }: { media?: string[]; specialization?: object } = {},
  ) {
    this.activity.push(
      record('tool.activity', {
        state: 'tool_done',
        tool_call_id: call,
        tool_name: tool,
        pass_id: PASS,
        segment_id: segment,
        group_id: `${segment}:generic:${tool}`,
        status: 'succeeded',
        safe_summary: 'Done.',
        ...(specialization ? { specialization } : {}),
      }),
    );
    for (const reference of media)
      this.activity.push(
        record('media.available', {
          media_ref: reference,
          mime_type: reference.startsWith('video') ? 'video/mp4' : 'image/png',
          tool_call_id: call,
        }),
      );
  }

  items() {
    return buildTranscript(liveTurn(this.rows, this.activity).rows);
  }
}

/** The turn as a reader sees it: words, tool steps and media, in order. */
function reading(items: TranscriptItem[]) {
  return items.flatMap((item) => [
    ...item.row.blocks.flatMap((block) =>
      'text' in block && block.text ? [`text: ${block.text}`] : [],
    ),
    ...item.traces.flatMap((group) =>
      group.items.map((step) => `step: ${step.canonical_name}`),
    ),
    ...item.media.map((media) => `media: ${media.reference}`),
    ...item.cards.map((card) => `card: ${cardKey(card)}`),
    ...(item.earlier ?? []).map((card) => `earlier: ${cardKey(card)}`),
  ]);
}

function threeSteps() {
  const run = new LiveRun();
  run.say('s1', 'First, a video of the sign-up flow.');
  run.call('s1', 'c1', 'generate_video', 'The sign-up flow');
  run.done('s1', 'c1', 'generate_video', { media: ['video-a'] });
  run.say('s2', 'Now a still of the finished form.');
  run.call('s2', 'c2', 'generate_image');
  run.done('s2', 'c2', 'generate_image', { media: ['image-b'] });
  // A step that says nothing first.
  run.call('s3', 'c3', 'generate_video');
  run.done('s3', 'c3', 'generate_video', { media: ['video-c'] });
  run.say('s4', 'Next, a shorter cut with captions.');
  run.call('s4', 'c4', 'generate_video');
  return run;
}

describe('a turn of several steps', () => {
  it('reads live in the order it happened, the newest step at the end', () => {
    const run = threeSteps();
    const live = liveTurn(run.rows, run.activity);
    expect(reading(buildTranscript(live.rows))).toEqual([
      'text: Make the sign-up videos.',
      'text: First, a video of the sign-up flow.',
      'step: generate_video',
      'media: video-a',
      'text: Now a still of the finished form.',
      'step: generate_image',
      'media: image-b',
      'step: generate_video',
      'media: video-c',
      'text: Next, a shorter cut with captions.',
    ]);
    // The step in progress stays on the live line, after the newest words.
    expect(live.running.map((group) => group.items[0].call_id)).toEqual(['c4']);
    expect(live.all).toHaveLength(4);
  });

  it('shows each picture or video once, live and once the steps settle (B22)', () => {
    const run = threeSteps();
    const items = run.items();
    const placed = items.flatMap((item) =>
      item.media.map((media) => media.reference),
    );
    const pending = liveMedia(run.activity, new Set(placed)).map(
      (media) => media.reference,
    );
    expect([...placed, ...pending].sort()).toEqual([
      'image-b',
      'video-a',
      'video-c',
    ]);
    // Media a step made with no step to sit under stays on the live line.
    run.activity.push(
      record('media.available', {
        media_ref: 'loose',
        mime_type: 'image/png',
      }),
    );
    expect(
      liveMedia(
        run.activity,
        new Set(
          run.items().flatMap((item) => item.media.map((m) => m.reference)),
        ),
      ).map((media) => media.reference),
    ).toEqual(['loose']);
  });

  it('reads the same after a reload as it did live', () => {
    const run = threeSteps();
    run.done('s4', 'c4', 'generate_video', { media: ['video-d'] });
    run.say('s5', 'Here they are.');
    const live = reading(run.items());
    const media = (reference: string) => ({
      kind: 'media' as const,
      media_kind: 'attachment',
      media: [
        {
          media_ref: reference,
          mime_type: reference.startsWith('video') ? 'video/mp4' : 'image/png',
        },
      ],
    });
    const step = (
      id: string,
      text: string,
      tool: string,
      reference: string,
    ): TranscriptRow => ({
      id: `assistant:checkpoint:${id}`,
      message_id: id,
      role: 'assistant',
      blocks: text ? [{ id: `${id}-text`, type: 'markdown', text }] : [],
      traces: [savedTrace(`call-${id}`, tool, media(reference))],
    });
    const saved = buildTranscript([
      user,
      step(
        'a1',
        'First, a video of the sign-up flow.',
        'generate_video',
        'video-a',
      ),
      step(
        'a2',
        'Now a still of the finished form.',
        'generate_image',
        'image-b',
      ),
      step('a3', '', 'generate_video', 'video-c'),
      step(
        'a4',
        'Next, a shorter cut with captions.',
        'generate_video',
        'video-d',
      ),
      {
        id: 'assistant:checkpoint:a5',
        role: 'assistant',
        blocks: [{ id: 'a5-text', type: 'markdown', text: 'Here they are.' }],
      },
    ]);
    expect(reading(saved)).toEqual(live);
  });

  it('folds earlier videos under the newest, in the step that made it (#743)', () => {
    const run = new LiveRun();
    const edit = (id: string) => ({ kind: 'video_edit', edit_id: id });
    run.say('s1', 'Recording the page.');
    run.call('s1', 'c1', 'edit_video');
    run.done('s1', 'c1', 'edit_video', { specialization: edit('e1') });
    run.say('s2', 'Trimming it.');
    run.call('s2', 'c2', 'edit_video');
    run.done('s2', 'c2', 'edit_video', { specialization: edit('e2') });
    run.say('s3', 'And a slower cut.');
    run.call('s3', 'c3', 'edit_video');
    run.done('s3', 'c3', 'edit_video', { specialization: edit('e3') });
    expect(reading(run.items())).toEqual([
      'text: Make the sign-up videos.',
      'text: Recording the page.',
      'step: edit_video',
      'text: Trimming it.',
      'step: edit_video',
      'text: And a slower cut.',
      'step: edit_video',
      'card: video_edit:e3',
      'earlier: video_edit:e1',
      'earlier: video_edit:e2',
    ]);
  });

  it('shows a video under its step when the step was saved before the video came', () => {
    // The chat re-read the turn mid-step: its step is saved, its video only live.
    const run = threeSteps();
    run.rows = [
      user,
      {
        id: 'assistant:checkpoint:a1',
        message_id: 'a1',
        role: 'assistant',
        blocks: [
          {
            id: 'a1-text',
            type: 'markdown',
            text: 'First, a video of the sign-up flow.',
          },
        ],
        traces: [savedTrace('c1', 'generate_video', undefined)],
      },
      ...run.rows.filter((row) => !row.id.endsWith(':s1') && row !== user),
    ];
    const items = run.items();
    expect(reading(items).slice(0, 4)).toEqual([
      'text: Make the sign-up videos.',
      'text: First, a video of the sign-up flow.',
      'step: generate_video',
      'media: video-a',
    ]);
    const shown = new Set(
      items.flatMap((item) => item.media.map((media) => media.reference)),
    );
    expect(liveMedia(run.activity, shown)).toEqual([]);
  });

  it('keeps a step saved before it finished on the live line while it runs (I31)', () => {
    // The chat re-read the turn while its newest step ran: that step is saved, still pending.
    const run = threeSteps();
    const running = savedTrace('c4', 'generate_video', undefined);
    running.status = 'pending';
    running.counts = { pending: 1 };
    running.items[0].status = 'pending';
    run.rows = [
      ...run.rows.filter((row) => !row.id.endsWith(':s4')),
      {
        id: 'assistant:checkpoint:a4',
        message_id: 'a4',
        role: 'assistant',
        blocks: [
          {
            id: 'a4-text',
            type: 'markdown',
            text: 'Next, a shorter cut with captions.',
          },
        ],
        traces: [running],
      },
    ];
    const live = liveTurn(run.rows, run.activity);
    // Under its words it would read as done, and the live line, left empty, said "Thinking…".
    expect(reading(buildTranscript(live.rows)).at(-1)).toBe(
      'text: Next, a shorter cut with captions.',
    );
    expect(live.running.map((group) => group.items[0].call_id)).toEqual(['c4']);
  });

  it('keeps a saved running step on the live line when its live event is gone (R15)', () => {
    // The chat re-read the turn mid-step and its activity no longer holds that step's event.
    const run = threeSteps();
    run.activity.pop();
    const running = savedTrace('c4', 'generate_video', undefined);
    running.status = 'pending';
    running.counts = { pending: 1 };
    running.items[0].status = 'pending';
    run.rows = [
      ...run.rows.filter((row) => !row.id.endsWith(':s4')),
      {
        id: 'assistant:checkpoint:a4',
        message_id: 'a4',
        role: 'assistant',
        blocks: [
          {
            id: 'a4-text',
            type: 'markdown',
            text: 'Next, a shorter cut with captions.',
          },
        ],
        traces: [running],
      },
    ];
    const live = liveTurn(run.rows, run.activity, true);
    // Under its words it read as done, and the live line below said "Thinking…" too.
    expect(reading(buildTranscript(live.rows)).at(-1)).toBe(
      'text: Next, a shorter cut with captions.',
    );
    expect(live.running.map((group) => group.items[0].call_id)).toEqual(['c4']);
    // Once the turn has ended, a step saved as pending stays where it was saved.
    const ended = liveTurn(run.rows, run.activity, false);
    expect(reading(buildTranscript(ended.rows)).at(-1)).toBe(
      'step: generate_video',
    );
    expect(ended.running).toEqual([]);
  });

  it('keeps quiet tool steps at the end on the live line, with the step in progress', () => {
    const run = new LiveRun();
    run.say('s1', 'Filling in the claim.');
    run.call('s1', 'c1', 'generate_video');
    run.done('s1', 'c1', 'generate_video', { media: ['video-a'] });
    run.call('s2', 'c2', 'computer_activity');
    run.done('s2', 'c2', 'computer_activity');
    run.call('s3', 'c3', 'computer_activity');
    run.done('s3', 'c3', 'computer_activity');
    run.call('s4', 'c4', 'computer_activity');
    const live = liveTurn(run.rows, run.activity);
    expect(reading(buildTranscript(live.rows))).toEqual([
      'text: Make the sign-up videos.',
      'text: Filling in the claim.',
      'step: generate_video',
      'media: video-a',
    ]);
    expect(live.running.map((group) => group.items[0].call_id)).toEqual([
      'c2',
      'c3',
      'c4',
    ]);
    // Words that follow put them in their place.
    run.say('s5', 'Now the last page.');
    expect(reading(run.items()).slice(4)).toEqual([
      'step: computer_activity',
      'step: computer_activity',
      'text: Now the last page.',
    ]);
  });

  it('leaves a turn with no finished live step as it is', () => {
    const run = new LiveRun();
    run.say('s1', 'Just an answer.');
    expect(liveTurn(run.rows, run.activity).rows).toBe(run.rows);
    run.call('s1', 'c1', 'web_search');
    expect(liveTurn(run.rows, run.activity).rows).toBe(run.rows);
  });

  it('renders only the step a token streams into again', () => {
    const run = threeSteps();
    const before = run.items();
    run.say('s4', 'Next, a shorter cut with captions, at half speed.');
    const after = keepUnchanged(before, run.items());
    const changed = after.filter((item, index) => item !== before[index]);
    expect(changed.map((item) => item.row.id)).toEqual([
      `assistant:live:${PASS}:s4`,
    ]);
  });
});

function savedTrace(
  call: string,
  tool: string,
  specialization: TranscriptTraceGroup['items'][number]['specialization'],
): TranscriptTraceGroup {
  return {
    group_id: call,
    name: tool,
    kind: 'generic',
    group_order: 0,
    status: 'succeeded',
    counts: { succeeded: 1 },
    items: [
      {
        item_id: call,
        group_id: call,
        call_id: call,
        call_order: 0,
        group_order: 0,
        canonical_name: tool,
        group_name: tool,
        group_kind: 'generic',
        status: 'succeeded',
        safe_summary: '',
        summary_truncated: false,
        specialization,
      },
    ],
  };
}
