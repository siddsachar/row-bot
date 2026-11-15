import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { LoaderCircle, RotateCcw, TriangleAlert } from 'lucide-react';
import type { SkillOfferPage } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Drawer, useNotify } from '../../ui/overlays';
import {
  Button,
  CopyGlyph,
  Disclosure,
  Field,
  IconButton,
  Input,
  useCopyFeedback,
} from '../../ui/primitives';
import SafeMarkdown from '../shell/chat-parity-markdown';
import type { SkillOffer } from './skill-offers';
import './skills.css';

type Draft = NonNullable<SkillOffer['draft']>;
type Fields = Pick<
  Draft,
  'display_name' | 'description' | 'when_to_use' | 'inputs' | 'steps' | 'checks'
>;
type Phase =
  | { kind: 'drafting' }
  | { kind: 'failed'; message: string }
  | { kind: 'review' }
  | { kind: 'editing'; fields: Fields; problem: string }
  | { kind: 'rejecting' };

const REASONS = ['Not useful', 'Wrong steps', 'I’ll write it myself'];

/** A list field as lines: one input, step or check per line, each once. */
const lines = (value: string[]) => [
  ...new Set(value.map((line) => line.trim()).filter(Boolean)),
];
const cleaned = (fields: Fields): Fields => ({
  display_name: fields.display_name.trim(),
  description: fields.description.trim(),
  when_to_use: fields.when_to_use.trim(),
  inputs: lines(fields.inputs),
  steps: lines(fields.steps),
  checks: lines(fields.checks),
});

/** The SKILL.md the draft becomes, as Settings › Skills saves it. */
export function skillFile(draft: Draft): string {
  return [
    '---',
    `name: ${draft.name}`,
    `display_name: ${draft.display_name}`,
    `icon: ${draft.icon}`,
    `description: ${draft.description}`,
    '---',
    '',
    draft.instructions,
  ].join('\n');
}

function when(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

/**
 * The review sheet for a skill drafted from a chat (C1): what it will do, why
 * Row-Bot offered it and where it came from, with Approve, Edit and Reject.
 * Nothing is saved until Approve; the person's edit is final.
 */
export default function SkillDraftSheet({
  open,
  onOpenChange,
  conversationId,
  chatTitle,
  messageId,
  offer,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  conversationId: string;
  chatTitle: string;
  messageId: string;
  offer: SkillOffer | undefined;
  /** The chat's offers after a change here. */
  onChanged: (page: SkillOfferPage) => void;
}) {
  const { controller, platform } = useRuntime();
  const notify = useNotify();
  const [phase, setPhase] = useState<Phase>({ kind: 'review' });
  const [busy, setBusy] = useState(false);
  const [copied, markCopied] = useCopyFeedback();
  const drafting = useRef<AbortController | null>(null);
  const draft = offer?.draft ?? null;
  const pending = draft && offer?.status === 'drafted';

  async function writeDraft() {
    drafting.current?.abort();
    const abort = new AbortController();
    drafting.current = abort;
    setPhase({ kind: 'drafting' });
    try {
      const page = await controller.draftSkill(
        conversationId,
        messageId,
        offer?.status === 'offered' ? offer.id : undefined,
        abort.signal,
      );
      if (abort.signal.aborted) return;
      onChanged(page);
      setPhase({ kind: 'review' });
    } catch (cause) {
      if (abort.signal.aborted) return;
      setPhase({ kind: 'failed', message: clientError(cause).message });
    }
  }

  useEffect(() => {
    if (!open) {
      drafting.current?.abort();
      return;
    }
    if (offer?.status === 'drafted') setPhase({ kind: 'review' });
    else void writeDraft();
    // Opened for a reply: its draft, or a new one once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, messageId]);

  async function libraryRevision() {
    const page = await controller.skills('', undefined, undefined, 'all');
    return page.revision;
  }

  async function run(
    type:
      | 'skill.proposal.apply'
      | 'skill.proposal.edit'
      | 'skill.proposal.reject'
      | 'skill.delete',
    payload: Record<string, unknown>,
  ) {
    const review = await controller.reviewSkill(type, payload);
    const receipt = await controller.executeSkill({
      command_id: crypto.randomUUID(),
      type,
      payload: { ...payload, review_id: review.review_id },
    });
    if (receipt.status !== 'completed')
      throw clientError({ code: 'skill_outcome_uncertain' });
    return receipt;
  }

  async function refresh() {
    onChanged(await controller.skillOffers(conversationId));
  }

  async function step(work: () => Promise<void>) {
    setBusy(true);
    try {
      await work();
    } catch (cause) {
      const message = clientError(cause).message;
      if (phase.kind === 'editing') setPhase({ ...phase, problem: message });
      else notify(message, 'danger');
    } finally {
      setBusy(false);
    }
  }

  const approve = () =>
    step(async () => {
      if (!draft) return;
      const receipt = await run('skill.proposal.apply', {
        revision: await libraryRevision(),
        proposal_id: draft.proposal_id,
        reason: '',
      });
      await refresh();
      const name = receipt.skill_id ?? draft.name;
      // The reply now shows it saved; the notice can take it back.
      onOpenChange(false);
      notify(`Saved “${draft.display_name}” as a skill.`, undefined, {
        label: 'Undo',
        title: 'Undo: delete the skill',
        onAction: () => void undo(name, draft.display_name),
      });
    });

  async function undo(name: string, title: string) {
    try {
      const detail = await controller.skill(name);
      await run('skill.delete', {
        revision: detail.library_revision,
        name,
        skill_revision: detail.skill.revision,
      });
      await refresh();
      notify(`Removed the skill “${title}”.`);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    }
  }

  const saveEdit = (fields: Fields) =>
    step(async () => {
      if (!draft) return;
      await run('skill.proposal.edit', {
        revision: await libraryRevision(),
        proposal_id: draft.proposal_id,
        fields,
      });
      await refresh();
      setPhase({ kind: 'review' });
    });

  const reject = (reason: string) =>
    step(async () => {
      if (!draft) return;
      await run('skill.proposal.reject', {
        revision: await libraryRevision(),
        proposal_id: draft.proposal_id,
        reason,
      });
      await refresh();
      onOpenChange(false);
      notify(
        'Rejected. Row-Bot won’t offer this as a skill again for a while.',
      );
    });

  const title =
    draft && phase.kind !== 'drafting' && phase.kind !== 'failed'
      ? `${draft.icon} ${draft.display_name}`.trim()
      : 'Save as a skill';
  const source = draft?.source_title || chatTitle;
  const dated = offer ? when(offer.created_at) : '';
  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      modal
      title={title}
      description={
        source ? `From ${source}${dated ? ` · ${dated}` : ''}` : undefined
      }
      className="skill-draft-sheet"
      closeLabel="Close skill draft"
    >
      <div
        className="skill-draft-body"
        role="group"
        aria-label="Skill draft"
        tabIndex={0}
        aria-busy={phase.kind === 'drafting' || busy}
      >
        {phase.kind === 'drafting' && (
          <div className="skill-draft-state" role="status">
            <LoaderCircle className="spin" size={18} aria-hidden />
            <p>Drafting a skill from this chat…</p>
            <div className="skill-draft-skeleton" aria-hidden>
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
        {phase.kind === 'failed' && (
          <div className="skill-draft-state" role="alert">
            <TriangleAlert size={18} aria-hidden />
            <p>{phase.message}</p>
          </div>
        )}
        {draft && (phase.kind === 'review' || phase.kind === 'rejecting') && (
          <>
            {draft.why && (
              <section aria-label="Why Row-Bot offered this">
                <h3>Why Row-Bot offered this</h3>
                <p>{draft.why}</p>
              </section>
            )}
            <section aria-label="What it will do" className="skill-draft-what">
              <h3>What it will do</h3>
              <div className="skill-draft-markdown">
                <SafeMarkdown text={draft.instructions} />
              </div>
            </section>
            {draft.overlaps.length > 0 && (
              <p className="skill-draft-note" role="note">
                <TriangleAlert size={15} aria-hidden />
                <span>
                  Similar to{' '}
                  {draft.overlaps.map((item, index) => (
                    <span key={item.name}>
                      {index ? ', ' : ''}
                      <Link
                        to={`/settings/skills/${encodeURIComponent(item.name)}`}
                      >
                        {item.display_name || item.name}
                      </Link>
                    </span>
                  ))}
                </span>
              </p>
            )}
            {draft.errors.length > 0 && (
              <p className="skill-draft-note" role="alert">
                <TriangleAlert size={15} aria-hidden />
                <span>{draft.errors.join(' ')}</span>
              </p>
            )}
            <Disclosure summary="SKILL.md" className="skill-draft-file">
              <div className="skill-draft-file-body">
                <pre>{skillFile(draft)}</pre>
                <IconButton
                  size="sm"
                  className="skill-draft-copy"
                  label={copied ? 'Copied SKILL.md' : 'Copy SKILL.md'}
                  onClick={() =>
                    void platform.writeClipboard(skillFile(draft)).then(
                      (result) => markCopied(result.status === 'ok'),
                      () => markCopied(false),
                    )
                  }
                >
                  <CopyGlyph copied={copied} />
                </IconButton>
              </div>
            </Disclosure>
          </>
        )}
        {draft && phase.kind === 'editing' && (
          <form
            className="skill-draft-form"
            aria-label="Edit the skill"
            onSubmit={(event) => {
              event.preventDefault();
              void saveEdit(cleaned(phase.fields));
            }}
          >
            <Field label="Name">
              <Input
                value={phase.fields.display_name}
                maxLength={128}
                required
                data-initial-focus
                onChange={(event) =>
                  setPhase({
                    ...phase,
                    fields: {
                      ...phase.fields,
                      display_name: event.target.value,
                    },
                  })
                }
              />
            </Field>
            <Field label="What it does">
              <Input
                value={phase.fields.description}
                maxLength={300}
                onChange={(event) =>
                  setPhase({
                    ...phase,
                    fields: {
                      ...phase.fields,
                      description: event.target.value,
                    },
                  })
                }
              />
            </Field>
            <Field label="When to use">
              <textarea
                className="input"
                rows={2}
                value={phase.fields.when_to_use}
                maxLength={600}
                onChange={(event) =>
                  setPhase({
                    ...phase,
                    fields: {
                      ...phase.fields,
                      when_to_use: event.target.value,
                    },
                  })
                }
              />
            </Field>
            {(['inputs', 'steps', 'checks'] as const).map((key) => (
              <Field
                key={key}
                label={
                  key === 'inputs'
                    ? 'What changes each time'
                    : key === 'steps'
                      ? 'Steps'
                      : 'How to tell it worked'
                }
                hint="One per line."
              >
                <textarea
                  className="input"
                  rows={key === 'steps' ? 6 : 2}
                  value={phase.fields[key].join('\n')}
                  required={key === 'steps'}
                  onChange={(event) =>
                    setPhase({
                      ...phase,
                      fields: {
                        ...phase.fields,
                        [key]: event.target.value.split('\n'),
                      },
                    })
                  }
                />
              </Field>
            ))}
            {phase.problem && (
              <p className="skill-draft-note" role="alert">
                <TriangleAlert size={15} aria-hidden />
                <span>{phase.problem}</span>
              </p>
            )}
          </form>
        )}
        {phase.kind === 'rejecting' && (
          <section aria-label="Why reject it" className="skill-draft-reasons">
            <h3>Why reject it?</h3>
            <div className="skill-draft-reason-list">
              {REASONS.map((reason) => (
                <Button
                  key={reason}
                  variant="secondary"
                  disabled={busy}
                  onClick={() => void reject(reason)}
                >
                  {reason}
                </Button>
              ))}
            </div>
          </section>
        )}
      </div>
      <div className="skill-draft-actions">
        {phase.kind === 'drafting' && (
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        )}
        {phase.kind === 'failed' && (
          <>
            <Button variant="primary" onClick={() => void writeDraft()}>
              <RotateCcw size={15} aria-hidden />
              Try again
            </Button>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </>
        )}
        {phase.kind === 'review' && pending && (
          <>
            <Button
              variant="primary"
              disabled={busy || draft.proposal_status !== 'ready'}
              onClick={() => void approve()}
            >
              {busy ? 'Saving…' : 'Approve'}
            </Button>
            <Button
              disabled={busy}
              onClick={() =>
                setPhase({
                  kind: 'editing',
                  problem: '',
                  fields: {
                    display_name: draft.display_name,
                    description: draft.description,
                    when_to_use: draft.when_to_use,
                    inputs: draft.inputs,
                    steps: draft.steps,
                    checks: draft.checks,
                  },
                })
              }
            >
              Edit
            </Button>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => setPhase({ kind: 'rejecting' })}
            >
              Reject
            </Button>
          </>
        )}
        {phase.kind === 'editing' && (
          <>
            <Button
              variant="primary"
              disabled={busy || !lines(phase.fields.steps).length}
              onClick={() => void saveEdit(cleaned(phase.fields))}
            >
              {busy ? 'Saving…' : 'Save changes'}
            </Button>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => setPhase({ kind: 'review' })}
            >
              Cancel
            </Button>
          </>
        )}
        {phase.kind === 'rejecting' && (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => setPhase({ kind: 'review' })}
          >
            Back
          </Button>
        )}
      </div>
      {phase.kind === 'review' && pending && (
        <p className="skill-draft-footnote">
          Saved skills load by themselves when a request matches. You can turn
          one off or delete it in Settings › Skills.
        </p>
      )}
    </Drawer>
  );
}
