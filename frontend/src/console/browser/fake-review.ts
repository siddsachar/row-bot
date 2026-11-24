/**
 * The review queue's routes for FakeApi: shares people offered to the
 * company's spaces, with made-up content, which the console approves (as
 * sent, edited or moved to another space) or rejects. The signed-in admin
 * reviews every space, as the server lets an admin's console session do.
 */
import type { FakeAnswer } from './fake-api';

// The same spaces and people as the rest of the fake server.
const COMPANY_SPACE = 'a0000000-0000-4000-8000-000000000001';
const ENGINEERING_SPACE = 'a0000000-0000-4000-8000-000000000002';
const SALES_SPACE = 'a0000000-0000-4000-8000-000000000003';
const ME = '11111111-1111-4111-8111-111111111111';
const ADA = '22222222-2222-4222-8222-222222222222';
const GRACE = '33333333-3333-4333-8333-333333333333';
const LIN = '44444444-4444-4444-8444-444444444444';
const MAX_FACT = 2000;
const NOW = Date.now();
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

type Space = { id: string; name: string; kind: 'company' | 'group' };
type Submission = {
  id: string;
  kind: 'share';
  space_id: string;
  space_name: string | null;
  sent_space_id: string;
  title: string;
  text: string;
  source_note: string | null;
  submitted_by: string;
  submitted_at: string;
  status: 'pending' | 'approved' | 'rejected';
  reviewed_by: string | null;
  decided_at: string | null;
  reason: string | null;
  fact_id: string | null;
};

const SPACES: Space[] = [
  { id: COMPANY_SPACE, name: 'Company', kind: 'company' },
  { id: ENGINEERING_SPACE, name: 'Engineering', kind: 'group' },
  { id: SALES_SPACE, name: 'Sales team', kind: 'group' },
];

function share(
  number: number,
  space: Space,
  fields: Partial<Submission> & Pick<Submission, 'title' | 'text'>,
): Submission {
  return {
    id: `c0000000-0000-4000-8000-00000000000${number}`,
    kind: 'share',
    space_id: space.id,
    space_name: space.name,
    sent_space_id: space.id,
    source_note: null,
    submitted_by: ADA,
    submitted_at: ago(60 * number),
    status: 'pending',
    reviewed_by: null,
    decided_at: null,
    reason: null,
    fact_id: null,
    ...fields,
  };
}

function shares(): Submission[] {
  const [company, engineering, sales] = SPACES;
  return [
    share(1, sales, {
      title: 'Commission timing',
      text: 'Commission is paid on the 5th working day of each month, for the month before.',
      source_note: 'From a chat on 12 Nov',
    }),
    share(2, company, {
      title: 'Parking at the harbour office',
      text: 'Visitors park in bay 4. Ask reception for a pass before 10:00.',
      submitted_by: LIN,
    }),
    share(3, engineering, {
      title: 'Release checklist notes',
      // Longer than a fact holds: approved only once shortened.
      text: `Before a release: ${'run the full test matrix, check the installers, read the release notes again. '.repeat(30)}`,
      submitted_by: GRACE,
    }),
    share(4, sales, {
      title: 'Discount approvals',
      text: 'Discounts above 15% need the sales director.',
      status: 'approved',
      reviewed_by: ME,
      decided_at: ago(30),
      fact_id: 'f0000000-0000-4000-8000-000000000004',
    }),
    share(5, company, {
      title: 'Lunch on Fridays',
      text: 'Lunch is free on Fridays.',
      submitted_by: LIN,
      status: 'rejected',
      reviewed_by: ME,
      decided_at: ago(90),
      reason: 'Only in the London office: we will add it to its own page.',
    }),
  ];
}

export class FakeReview {
  private submissions: Submission[];
  private facts = 0;

  constructor(empty: boolean) {
    this.submissions = empty ? [] : shares();
  }

  handle(method: string, url: URL, body: unknown): FakeAnswer | undefined {
    const path = url.pathname;
    if (path === '/api/brain/review' && method === 'GET') {
      const space = url.searchParams.get('space');
      const status = url.searchParams.get('status') ?? 'pending';
      if (space && !SPACES.some((item) => item.id === space))
        return { status: 404, body: { error: 'space_not_found' } };
      const inSpace = this.submissions.filter(
        (item) => !space || item.space_id === space,
      );
      const listed = inSpace.filter(
        (item) => status === 'all' || item.status === status,
      );
      return {
        status: 200,
        body: {
          submissions:
            status === 'pending'
              ? listed.sort((a, b) =>
                  a.submitted_at.localeCompare(b.submitted_at),
                )
              : listed,
          spaces: SPACES,
          pending_count: inSpace.filter((item) => item.status === 'pending')
            .length,
          truncated: false,
        },
      };
    }
    const step = path.match(
      /^\/api\/brain\/review\/([^/]+)\/(approve|reject)$/,
    );
    if (!step || method !== 'POST') return undefined;
    const found = this.submissions.find((item) => item.id === step[1]);
    if (!found) return { status: 404, body: { error: 'submission_not_found' } };
    if (found.status !== 'pending')
      return {
        status: 409,
        body: { error: 'submission_decided', status: found.status },
      };
    const decided = {
      reviewed_by: ME,
      decided_at: new Date().toISOString(),
    };
    if (step[2] === 'reject') {
      Object.assign(found, decided, {
        status: 'rejected',
        reason: (body as { reason: string }).reason,
      });
      return { status: 200, body: found };
    }
    const edits = body as { title?: string; text?: string; space_id?: string };
    const text = edits.text ?? found.text;
    if (text.length > MAX_FACT)
      return {
        status: 422,
        body: { error: 'text_too_long_for_a_fact', max_length: MAX_FACT },
      };
    const space =
      SPACES.find((item) => item.id === (edits.space_id ?? found.space_id)) ??
      SPACES[0];
    const factId = `f1000000-0000-4000-8000-00000000000${++this.facts}`;
    Object.assign(found, decided, {
      status: 'approved',
      title: edits.title ?? found.title,
      text,
      space_id: space.id,
      space_name: space.name,
      fact_id: factId,
    });
    return {
      status: 200,
      body: {
        submission: found,
        fact: { id: factId, space_id: space.id, text },
      },
    };
  }
}
