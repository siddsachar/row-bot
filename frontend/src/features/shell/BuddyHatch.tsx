import {
  ProviderSettingsSession,
  useProviderSettingsValue,
} from '../settings/provider-settings-sessions';
import { useEffect, useRef } from 'react';
import { Button, ErrorState, Field } from '../../ui/primitives';
import type { BuddyPack } from './BuddyControls';

export type HatchResult = {
  schema_version: 1;
  command_id: string;
  job_id: string;
  status: string;
  stage: string;
  pack_id: string | null;
  selected: boolean;
  completed_clips: number;
  total_clips: number;
  code: string;
  retained_copy: boolean;
  has_still: boolean;
};
export type HatchRequest = {
  action: 'full' | 'motion' | 'still' | 'remove';
  prompt: string;
  config_revision: string;
  source_pack_id?: string;
  source_pack_revision?: string;
  source_command_id?: string;
};
export type HatchReview = {
  nonce?: string;
  review_id: string;
  action: HatchRequest['action'];
  config_revision: string;
  image_model: string | null;
  video_model: string | null;
  provider_calls: number;
};
export type HatchRemoval = {
  status: 'removed';
  pack_id: string;
  retained_copy: true;
  config_changed: boolean;
};
export type BuddyHatchProps = {
  editor?: ProviderSettingsSession;
  scopeKey: string;
  configRevision: string | null;
  initialPrompt?: string;
  selectedPack: BuddyPack | null;
  personality: string;
  styleNotes: string;
  result: HatchResult | null;
  review(request: HatchRequest): Promise<HatchReview>;
  confirm(reviewId: string): Promise<HatchResult | HatchRemoval>;
  dismissReview?(): void;
  refresh(commandId: string): Promise<HatchResult>;
  cancel(jobId: string): Promise<void>;
};

const personalityNames: Record<string, string> = {
  warm_mystical: 'Warm mystical',
  calm_focus: 'Calm focus',
  playful_helper: 'Playful helper',
  quiet_guardian: 'Quiet guardian',
  curious_scholar: 'Curious scholar',
};
const personalityHints: Record<string, string> = {
  warm_mystical: 'gentle, luminous, encouraging, and a little mysterious',
  calm_focus: 'minimal, steady, precise, and designed for deep work',
  playful_helper:
    'bright, expressive, nimble, and visibly helpful without feeling noisy',
  quiet_guardian: 'protective, quiet, observant, and reassuring',
  curious_scholar: 'bookish, inquisitive, analytical, and warmly attentive',
};

export function composeHatchPrompt(
  concept: string,
  personality: string,
  styleNotes: string,
) {
  const selected = personalityNames[personality]
    ? personality
    : 'warm_mystical';
  return [
    concept.trim(),
    `Personality style: ${personalityNames[selected]} - ${personalityHints[selected]}.`,
    styleNotes.trim() ? `User style notes: ${styleNotes.trim()}` : '',
  ]
    .filter(Boolean)
    .join('\n\n');
}

const active = (result: HatchResult | null) =>
  result !== null &&
  ['queued', 'running', 'cancelling'].includes(result.status);

export default function BuddyHatch(props: BuddyHatchProps) {
  const localEditor = useRef<ProviderSettingsSession | null>(null);
  if (!localEditor.current)
    localEditor.current = new ProviderSettingsSession('buddy');
  const editor = props.editor ?? localEditor.current;

  const [prompt, setPrompt] = useProviderSettingsValue(
    editor,
    'prompt',
    props.initialPrompt ?? '',
  );
  const [review, setReview] = useProviderSettingsValue<HatchReview | null>(
    editor,
    'review',
    null,
  );
  const [result, setResult] = useProviderSettingsValue<HatchResult | null>(
    editor,
    'result',
    props.result,
  );
  const [busy, setBusy] = useProviderSettingsValue(editor, 'busy', false);
  const [stopping, setStopping] = useProviderSettingsValue(
    editor,
    'stopping',
    false,
  );
  const [notice, setNotice] = useProviderSettingsValue(editor, 'notice', '');
  const [error, setError] = useProviderSettingsValue(editor, 'error', '');
  const operation = useRef<symbol | null>(null);
  const current = useRef(props);
  useEffect(() => {
    current.current = props;
  }, [props]);
  useEffect(() => {
    if (props.editor) return;
    setPrompt('');
    setReview(null);
    setNotice('');
    setError('');
  }, [props.scopeKey, props.editor, setPrompt, setReview, setNotice, setError]);
  useEffect(() => {
    if (!props.initialPrompt || editor.get('promptSeeded', false)) return;
    if (!editor.get('prompt', '')) setPrompt(props.initialPrompt);
    editor.set('promptSeeded', true);
  }, [editor, props.initialPrompt, setPrompt]);
  useEffect(() => {
    setResult(props.result);
  }, [props.scopeKey, props.result, setResult]);
  useEffect(() => {
    const next = JSON.stringify([
      props.configRevision,
      props.selectedPack?.revision,
    ]);
    const prior = editor.get<string | null>('reviewContext', null);
    if (prior !== null && prior !== next) setReview(null);
    editor.set('reviewContext', next);
  }, [props.configRevision, props.selectedPack?.revision, editor, setReview]);

  async function stop() {
    if (!editor.active || editor.get('stopping', false) || !active(result))
      return;
    const scope = props.scopeKey;
    const jobId = result!.job_id;
    setStopping(true);
    try {
      await props.cancel(jobId);
      if (editor.active && current.current.scopeKey === scope)
        setNotice(
          'Stop requested. A provider request already in progress may finish; its result will not be selected.',
        );
    } catch {
      if (editor.active && current.current.scopeKey === scope)
        setError(
          'Hatch could not confirm Stop. Refresh the original command before another action.',
        );
    } finally {
      setStopping(false);
    }
  }

  async function run(
    action: HatchRequest['action'] | 'retained-motion' | 'confirm' | 'refresh',
  ) {
    if (
      operation.current ||
      editor.get('busy', false) ||
      !editor.active ||
      !props.configRevision
    )
      return;
    const id = Symbol('hatch');
    operation.current = id;
    const scope = props.scopeKey;
    const revision = props.configRevision;
    setBusy(true);
    setError('');
    setNotice('');
    const stillCurrent = () =>
      current.current.scopeKey === scope &&
      current.current.configRevision !== null;
    try {
      if (action === 'confirm') {
        if (!review || review.config_revision !== revision) return;
        // Consumed before admission; an uncertain response never exposes a retry
        // of this same review as a second provider operation.
        setReview(null);
        const outcome = await props.confirm(review.review_id);
        if (!stillCurrent()) return;
        if ('command_id' in outcome) setResult(outcome);
        else {
          setResult(null);
          setNotice(
            'Generated look removed. Its files are retained for recovery.',
          );
        }
      } else if (action === 'refresh' && result) {
        const outcome = await props.refresh(result.command_id);
        if (stillCurrent()) {
          if (outcome.command_id !== result.command_id)
            throw new Error('hatch_identity_changed');
          setResult(outcome);
        }
      } else if (
        ['full', 'motion', 'still', 'remove', 'retained-motion'].includes(
          action,
        )
      ) {
        const request: HatchRequest = {
          action:
            action === 'retained-motion'
              ? 'motion'
              : (action as HatchRequest['action']),
          prompt:
            action === 'full' ||
            action === 'motion' ||
            action === 'retained-motion'
              ? composeHatchPrompt(
                  prompt.trim() || 'Retained Buddy look',
                  props.personality,
                  props.styleNotes,
                )
              : prompt.trim() || 'Retained Buddy look',
          config_revision: revision,
        };
        if (
          (action === 'still' || action === 'retained-motion') &&
          result?.has_still
        )
          request.source_command_id = result.command_id;
        else if (action !== 'full' && props.selectedPack) {
          request.source_pack_id = props.selectedPack.id;
          request.source_pack_revision = props.selectedPack.revision;
        }
        const approved = await props.review(request);
        if (
          stillCurrent() &&
          current.current.configRevision === revision &&
          current.current.selectedPack?.revision ===
            props.selectedPack?.revision
        ) {
          if (
            approved.action !== request.action ||
            approved.config_revision !== revision
          )
            throw new Error('hatch_review_changed');
          setReview(approved);
        }
      }
    } catch {
      if (stillCurrent())
        setError(
          'Hatch could not confirm this action. Refresh its saved status and review retained results before starting another request.',
        );
    } finally {
      if (operation.current === id) {
        operation.current = null;
        setBusy(false);
      }
    }
  }

  if (!props.configRevision) return null;
  const running = active(result);
  return (
    <section
      className="buddy-hatch"
      aria-label="Hatch a Buddy"
      aria-busy={busy}
    >
      <div className="settings-buddy-section-heading buddy-hatch-heading">
        <div>
          <h3>Generate Look</h3>
          <p>
            Create a look and six motion clips with your configured image and
            video providers. Generation may incur provider charges. Your current
            look stays available until the new pack is ready.
          </p>
        </div>
      </div>
      <Field label="Describe your Buddy">
        <textarea
          aria-label="Describe your Buddy"
          maxLength={4000}
          value={prompt}
          disabled={busy || running}
          onChange={(event) => {
            editor.set('promptSeeded', true);
            setPrompt(event.target.value);
            setReview(null);
          }}
        />
      </Field>
      <div className="button-row buddy-hatch-actions">
        <Button
          disabled={busy || running || !prompt.trim()}
          onClick={() => void run('full')}
        >
          Review Generate full Buddy
        </Button>
        {props.selectedPack?.available &&
          props.selectedPack.assets.some((asset) => asset.id === 'preview') && (
            <Button
              disabled={busy || running}
              onClick={() => void run('motion')}
            >
              Review motion
            </Button>
          )}
        {result?.has_still && !running && (
          <>
            <Button disabled={busy} onClick={() => void run('still')}>
              Review still only
            </Button>
            <Button disabled={busy} onClick={() => void run('retained-motion')}>
              Review motion from still
            </Button>
          </>
        )}
        {props.selectedPack?.generated && (
          <Button disabled={busy || running} onClick={() => void run('remove')}>
            Review removal
          </Button>
        )}
      </div>
      {review && (
        <section
          className="buddy-hatch-review"
          aria-label="Review Hatch action"
        >
          <p>
            {review.action === 'remove'
              ? 'Remove this generated look from the catalog. Its files will be retained; the default look is selected if needed.'
              : review.action === 'still'
                ? 'Create a new look from the retained still without contacting a provider.'
                : `Generate ${review.action === 'full' ? 'a new look and six clips' : 'six new motion clips'}.`}
          </p>
          {review.image_model && <p>Image model: {review.image_model}</p>}
          {review.video_model && <p>Video model: {review.video_model}</p>}
          <p>Provider calls: {review.provider_calls}</p>
          <Button disabled={busy} onClick={() => void run('confirm')}>
            {review.action === 'remove'
              ? 'Confirm removal'
              : 'Confirm Hatch action'}
          </Button>
          <Button
            disabled={busy}
            variant="ghost"
            onClick={() => {
              props.dismissReview?.();
              setReview(null);
            }}
          >
            Dismiss review
          </Button>
        </section>
      )}
      {result && (
        <section
          className="buddy-hatch-progress"
          aria-label="Hatch progress"
          role="status"
        >
          <p>
            {result.status === 'completed'
              ? 'Buddy is ready.'
              : result.status === 'uncertain'
                ? 'Generation outcome needs review.'
                : result.status === 'partial'
                  ? 'Some results are retained; selection is incomplete.'
                  : `Hatch ${result.status}.`}
          </p>
          <p>
            {result.completed_clips} of {result.total_clips} motion clips
            retained.
          </p>
          {result.selected && <p>The new look was selected.</p>}
          {result.retained_copy && (
            <p>Generated files are retained for recovery.</p>
          )}
          {result.status === 'uncertain' && (
            <p>
              Refresh status before requesting new generation. Starting again is
              a separate provider request.
            </p>
          )}
          <Button disabled={busy} onClick={() => void run('refresh')}>
            Refresh Hatch status
          </Button>
          {running && (
            <Button
              disabled={stopping || result.status === 'cancelling'}
              onClick={() => void stop()}
            >
              Stop Hatch
            </Button>
          )}
        </section>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && <ErrorState title="Hatch needs attention">{error}</ErrorState>}
    </section>
  );
}
