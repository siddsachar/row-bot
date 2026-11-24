import { useState } from 'react';
import { Field, Input, Select } from '../../../ui/primitives';
import { request } from '../../api';
import { useDirectory } from '../../directory';
import { FormDialog } from '../../form-dialog';
import type { Approved, ReviewSpace, Submission } from './types';
import {
  MAX_FACT,
  MAX_REASON,
  MAX_TITLE,
  becomes,
  characters,
  short,
  tooLong,
} from './words';

/**
 * Approve a share, as sent or edited, into a fact of its space or another
 * the reviewer manages. It says what approving does before it is done.
 */
export function ApproveDialog({
  submission,
  spaces,
  onClose,
  onApproved,
}: {
  submission: Submission;
  spaces: ReviewSpace[];
  onClose: () => void;
  onApproved: (approved: Approved, space: ReviewSpace | undefined) => void;
}) {
  const [title, setTitle] = useState(submission.title);
  const [text, setText] = useState(submission.text);
  const [spaceId, setSpaceId] = useState(submission.space_id);
  const space = spaces.find((item) => item.id === spaceId);
  const over = tooLong(text);

  async function approve() {
    const changes: { title?: string; text?: string; space_id?: string } = {};
    if (title.trim() !== submission.title) changes.title = title.trim();
    if (text.trim() !== submission.text) changes.text = text.trim();
    if (spaceId !== submission.space_id) changes.space_id = spaceId;
    onApproved(
      await request<Approved>(`/api/brain/review/${submission.id}/approve`, {
        method: 'POST',
        body: changes,
      }),
      space,
    );
  }

  return (
    <FormDialog
      title={`Approve “${short(submission.title)}”?`}
      description="Edit it first if it needs it: what you approve is what Row-Bot answers from."
      submitLabel="Approve"
      submitDisabled={over !== null}
      onClose={onClose}
      onSubmit={approve}
    >
      <Field label="Title">
        <Input
          required
          maxLength={MAX_TITLE}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </Field>
      <Field label="Fact" hint={characters(text, MAX_FACT)}>
        <textarea
          className="input review-textarea"
          required
          rows={6}
          value={text}
          onChange={(event) => setText(event.target.value)}
          data-initial-focus
        />
      </Field>
      {spaces.length > 1 && (
        <Field label="Space">
          <Select
            value={spaceId}
            onChange={(event) => setSpaceId(event.target.value)}
          >
            {spaces.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
      )}
      {over ? (
        <p className="console-form-error" role="alert">
          {over}
        </p>
      ) : (
        <p className="review-outcome">{becomes(space)}</p>
      )}
    </FormDialog>
  );
}

/** Reject a share with a reason its submitter reads. */
export function RejectDialog({
  submission,
  onClose,
  onRejected,
}: {
  submission: Submission;
  onClose: () => void;
  onRejected: (rejected: Submission) => void;
}) {
  const { nameOf } = useDirectory();
  const [reason, setReason] = useState('');
  const where = submission.space_name ?? 'its space';

  async function reject() {
    onRejected(
      await request<Submission>(`/api/brain/review/${submission.id}/reject`, {
        method: 'POST',
        body: { reason: reason.trim() },
      }),
    );
  }

  return (
    <FormDialog
      title={`Reject “${short(submission.title)}”?`}
      description={`Nothing is added to ${where}. ${nameOf(submission.submitted_by)} sees that it was rejected, with your reason.`}
      submitLabel="Reject"
      submitDisabled={!reason.trim()}
      onClose={onClose}
      onSubmit={reject}
    >
      <Field label="Reason" hint={characters(reason, MAX_REASON)}>
        <textarea
          className="input review-textarea"
          required
          maxLength={MAX_REASON}
          rows={3}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          data-initial-focus
        />
      </Field>
    </FormDialog>
  );
}
