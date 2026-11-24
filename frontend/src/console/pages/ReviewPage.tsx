import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CircleCheck, CircleX, MessageSquare } from 'lucide-react';
import {
  EmptyState,
  EntityList,
  EntityRow,
  Field,
  IconButton,
  InlineEmpty,
  Select,
} from '../../ui/primitives';
import { When } from '../../ui/When';
import { useNotify } from '../../ui/overlays';
import { query, request } from '../api';
import { useDirectory } from '../directory';
import { Loaded, useLoad } from '../load';
import { Page } from '../page';
import { ApproveDialog, RejectDialog } from './review/dialogs';
import type {
  ReviewQueue,
  ReviewSpace,
  Shown,
  Submission,
} from './review/types';
import { SHOWN, STATUS, short, waiting } from './review/words';
import './review/review.css';

function shownFrom(value: string | null): Shown {
  return SHOWN.some((option) => option.value === value)
    ? (value as Shown)
    : 'pending';
}

/** What happened to a share, after who shared it and when. */
function Decision({ submission }: { submission: Submission }) {
  const { nameOf } = useDirectory();
  if (submission.status === 'pending') return null;
  const by = submission.reviewed_by
    ? ` by ${nameOf(submission.reviewed_by)}`
    : '';
  return (
    <>
      {` · ${STATUS[submission.status].label}${by} `}
      <When value={submission.decided_at} />
    </>
  );
}

function Details({
  submission,
  space,
}: {
  submission: Submission;
  space: string;
}) {
  return (
    <div className="review-details">
      <p className="review-text">{submission.text}</p>
      <dl className="console-details">
        {submission.source_note && (
          <div>
            <dt>Where it came from</dt>
            <dd>{submission.source_note}</dd>
          </div>
        )}
        {submission.reason && (
          <div>
            <dt>Why it was rejected</dt>
            <dd>{submission.reason}</dd>
          </div>
        )}
        {submission.status === 'approved' && (
          <div>
            <dt>Fact</dt>
            <dd>
              {submission.fact_id
                ? `A fact in ${space}, which everyone who reads ${space} can see.`
                : 'Its fact was deleted later.'}
            </dd>
          </div>
        )}
      </dl>
    </div>
  );
}

/**
 * What people shared from their chats to a company space, waiting for its
 * owners or an admin: approve one (as sent or edited) to make it a fact of
 * its space, or reject it with a reason.
 */
export default function ReviewPage() {
  const { nameOf } = useDirectory();
  const notify = useNotify();
  const [params, setParams] = useSearchParams();
  const space = params.get('space') ?? '';
  const shown = shownFrom(params.get('status'));
  const filters = query({
    space: space || undefined,
    status: shown,
  });
  // The spaces to filter by stay while the queue loads again.
  const [spaces, setSpaces] = useState<ReviewSpace[]>([]);
  const queue = useLoad(
    () =>
      request<ReviewQueue>(`/api/brain/review${filters}`).then((answer) => {
        setSpaces(answer.spaces);
        return answer;
      }),
    filters,
  );
  const [approving, setApproving] = useState<Submission | null>(null);
  const [rejecting, setRejecting] = useState<Submission | null>(null);

  function filter(name: 'space' | 'status', value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  }

  const spaceName = (submission: Submission) =>
    submission.space_name ??
    spaces.find((item) => item.id === submission.space_id)?.name ??
    'A space';

  /** The decided share in place of the waiting one (or gone, when only waiting ones show). */
  function decided(submission: Submission) {
    queue.update((data) => ({
      ...data,
      pending_count: Math.max(0, data.pending_count - 1),
      submissions:
        shown === 'pending'
          ? data.submissions.filter((item) => item.id !== submission.id)
          : data.submissions.map((item) =>
              item.id === submission.id ? submission : item,
            ),
    }));
  }

  return (
    <Page
      title="Review"
      description="What people shared from their chats to the company's knowledge. Approve a share to make it a fact of its space, or reject it with a reason they'll see."
    >
      <div className="console-filters">
        <Field label="Space">
          <Select
            value={space}
            onChange={(event) => filter('space', event.target.value)}
          >
            <option value="">Every space</option>
            {spaces.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Show">
          <Select
            value={shown}
            onChange={(event) =>
              filter(
                'status',
                event.target.value === 'pending' ? '' : event.target.value,
              )
            }
          >
            {SHOWN.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Loaded load={queue} label="Loading shares" what="the review queue">
        {(data) => (
          <section className="console-section" aria-label="Review queue">
            <p className="review-count">{waiting(data.pending_count)}</p>
            {data.submissions.length === 0 ? (
              shown === 'pending' && !space ? (
                <EmptyState title="Nothing to review">
                  When someone shares something from a chat to a company space,
                  it waits here until you approve or reject it.
                </EmptyState>
              ) : (
                <InlineEmpty icon={<MessageSquare size={16} />}>
                  No shares match these filters.
                </InlineEmpty>
              )
            ) : (
              <EntityList label="Shares">
                {data.submissions.map((submission) => (
                  <EntityRow
                    key={submission.id}
                    title={submission.title}
                    icon={<MessageSquare size={18} />}
                    status={STATUS[submission.status]}
                    meta={
                      <>
                        {`${spaceName(submission)} · Shared by ${nameOf(submission.submitted_by)} `}
                        <When value={submission.submitted_at} />
                        <Decision submission={submission} />
                      </>
                    }
                    action={
                      submission.status === 'pending' ? (
                        <>
                          <IconButton
                            size="sm"
                            label={`Approve “${short(submission.title)}”`}
                            onClick={() => setApproving(submission)}
                          >
                            <CircleCheck size={16} aria-hidden />
                          </IconButton>
                          <IconButton
                            size="sm"
                            label={`Reject “${short(submission.title)}”`}
                            onClick={() => setRejecting(submission)}
                          >
                            <CircleX size={16} aria-hidden />
                          </IconButton>
                        </>
                      ) : undefined
                    }
                    details={
                      <Details
                        submission={submission}
                        space={spaceName(submission)}
                      />
                    }
                  />
                ))}
              </EntityList>
            )}
            {data.truncated && (
              <p className="console-footnote">
                Only the first 200 are shown. Choose a space to see fewer.
              </p>
            )}
          </section>
        )}
      </Loaded>
      {approving && (
        <ApproveDialog
          submission={approving}
          spaces={spaces}
          onClose={() => setApproving(null)}
          onApproved={(approved, target) => {
            decided(approved.submission);
            notify(
              `“${short(approved.submission.title)}” is now a fact in ${target?.name ?? 'its space'}`,
            );
          }}
        />
      )}
      {rejecting && (
        <RejectDialog
          submission={rejecting}
          onClose={() => setRejecting(null)}
          onRejected={(rejected) => {
            decided(rejected);
            notify(`“${short(rejected.title)}” is rejected`);
          }}
        />
      )}
    </Page>
  );
}
