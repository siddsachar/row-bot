/** The review queue's shapes, as the Company Server answers them (B4a). */

export type SubmissionStatus = 'pending' | 'approved' | 'rejected';
/** What the queue shows: one status, or every one. */
export type Shown = SubmissionStatus | 'all';

export type Submission = {
  id: string;
  kind: 'share' | 'skill';
  space_id: string;
  /** Null when the caller doesn't read the space. */
  space_name: string | null;
  /** The space it was sent to: not `space_id` once an approval moved it. */
  sent_space_id: string;
  title: string;
  text: string;
  source_note: string | null;
  submitted_by: string;
  submitted_at: string;
  status: SubmissionStatus;
  reviewed_by: string | null;
  decided_at: string | null;
  reason: string | null;
  /** The fact an approval made (null again if it was deleted). */
  fact_id: string | null;
};

/** A space the caller reviews. */
export type ReviewSpace = {
  id: string;
  name: string;
  kind: 'company' | 'group';
};

export type ReviewQueue = {
  submissions: Submission[];
  spaces: ReviewSpace[];
  pending_count: number;
  truncated: boolean;
};

export type Approved = {
  submission: Submission;
  fact: { id: string; space_id: string; text: string };
};
