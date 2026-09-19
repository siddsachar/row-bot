import { useEffect, useRef, useState } from 'react';
import { Button, ErrorState, Field, Input, Select } from '../../ui/primitives';

export type ArtifactShareOptions = {
  action: 'publish' | 'channel' | 'x';
  channel_name?: string;
  target?: string;
  delivery: 'link' | 'slides' | 'pdf' | 'pptx' | 'html';
  pages: string;
  text: string;
  pptx_mode: 'screenshot' | 'structured';
  remote: boolean;
};
export type ArtifactShareReview = {
  review_id: string;
  resource_id: string;
  resource_revision: string;
  action: 'publish' | 'channel' | 'x';
  channel_name: string | null;
  recipient: string | null;
  delivery: string;
  pages: string;
  page_count: number;
  remote: boolean;
  requires_pairing: boolean;
};
export type ArtifactShareOutcome = {
  status: 'published' | 'submitted' | 'partial' | 'uncertain' | 'denied';
  code: string | null;
  resource_id: string;
  resource_revision: string;
  url: string | null;
  link_kind: 'local' | 'remote_access' | null;
  submitted_count: number;
  total_count: number;
};
export type ArtifactSharingProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  channels: { name: string; label: string; available: boolean }[];
  prepare: (options: ArtifactShareOptions) => Promise<ArtifactShareReview>;
  execute: (
    options: ArtifactShareOptions,
    reviewId: string,
    expectedRevision: string,
  ) => Promise<ArtifactShareOutcome>;
};

function safeUrl(value: string | null) {
  try {
    const url = new URL(value ?? '');
    return ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

export default function ArtifactSharing(props: ArtifactSharingProps) {
  const [options, setOptions] = useState<ArtifactShareOptions>({
    action: 'publish',
    delivery: 'link',
    pages: 'all',
    text: '',
    pptx_mode: 'screenshot',
    remote: false,
  });
  const [review, setReview] = useState<ArtifactShareReview | null>(null);
  const [outcome, setOutcome] = useState<ArtifactShareOutcome | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const operation = useRef<symbol | null>(null);
  const current = useRef(props);
  useEffect(() => {
    current.current = props;
  }, [props]);
  useEffect(() => {
    setReview(null);
    setError('');
  }, [props.resourceId, props.resourceRevision]);
  useEffect(() => {
    setOutcome(null);
  }, [props.resourceId]);

  function change(value: Partial<ArtifactShareOptions>) {
    setOptions((previous) => ({ ...previous, ...value }));
    setReview(null);
    setOutcome(null);
    setError('');
  }
  async function run(execute: boolean) {
    if (operation.current || !props.visible || (execute && !review)) return;
    const identity = Symbol('sharing');
    operation.current = identity;
    setBusy(true);
    setError('');
    const resourceId = props.resourceId;
    try {
      if (!execute) {
        const result = await props.prepare(options);
        if (current.current.resourceId !== resourceId) return;
        if (
          result.resource_id !== resourceId ||
          result.resource_revision !== current.current.resourceRevision
        )
          throw { code: 'share_review_changed' };
        setReview(result);
        setOutcome(null);
      } else if (review) {
        const result = await props.execute(
          options,
          review.review_id,
          review.resource_revision,
        );
        if (current.current.resourceId !== resourceId) return;
        if (result.resource_id !== resourceId)
          throw { code: 'share_review_changed' };
        setOutcome(result);
        setReview(null);
      }
    } catch (reason) {
      if (current.current.resourceId !== resourceId) return;
      const code =
        typeof reason === 'object' && reason !== null && 'code' in reason
          ? String(reason.code)
          : '';
      setReview(null);
      if (
        [
          'action_denied',
          'capability_revoked',
          'resource_binding_revoked',
        ].includes(code)
      ) {
        setOutcome(null);
        setError(
          'Access changed. Review this design and destination before continuing.',
        );
      } else if (code === 'sharing_media_limit')
        setError(
          'X supports up to four selected pages. Choose an explicit page range.',
        );
      else if (code === 'interactive_publish_requires_all_pages')
        setError(
          'This interactive design publishes all routes together. Select all pages.',
        );
      else if (
        code === 'share_review_changed' ||
        code === 'resource_revision_conflict'
      )
        setError(
          'The source or destination changed. Review the current details again.',
        );
      else
        setError(
          'The operation is unconfirmed. Check the destination before starting another attempt.',
        );
    } finally {
      if (operation.current === identity) {
        operation.current = null;
        setBusy(false);
      }
    }
  }
  if (!props.visible) return null;
  const url = safeUrl(outcome?.url ?? null);
  const actionLabel =
    options.action === 'publish'
      ? options.remote
        ? 'Publish remote access link'
        : 'Publish local link'
      : options.action === 'x'
        ? 'Post to X'
        : 'Send to channel';
  return (
    <section
      className="studio-section stack"
      aria-label="Design sharing"
      aria-busy={busy}
    >
      <Field label="Share action">
        <Select
          aria-label="Share action"
          value={options.action}
          disabled={busy}
          onChange={(event) =>
            change({
              action: event.target.value as ArtifactShareOptions['action'],
              channel_name: undefined,
              target: undefined,
              delivery: 'link',
            })
          }
        >
          <option value="publish">Publish link</option>
          <option value="channel">Send to channel</option>
          <option value="x">Post to X</option>
        </Select>
      </Field>
      {options.action === 'channel' && (
        <>
          <Field label="Channel">
            <Select
              aria-label="Channel"
              value={options.channel_name ?? ''}
              disabled={busy}
              onChange={(event) => change({ channel_name: event.target.value })}
            >
              <option value="">Choose channel</option>
              {options.channel_name &&
                !props.channels.some(
                  (channel) => channel.name === options.channel_name,
                ) && (
                  <option value={options.channel_name}>
                    {options.channel_name} (selected)
                  </option>
                )}
              {props.channels.map((channel) => (
                <option
                  key={channel.name}
                  value={channel.name}
                  disabled={!channel.available}
                >
                  {channel.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Recipient override">
            <Input
              aria-label="Recipient override"
              value={options.target ?? ''}
              maxLength={1024}
              disabled={busy}
              placeholder="Use configured destination"
              onChange={(event) =>
                change({ target: event.target.value || undefined })
              }
            />
          </Field>
          <Field label="Delivery">
            <Select
              aria-label="Delivery"
              value={options.delivery}
              disabled={busy}
              onChange={(event) =>
                change({
                  delivery: event.target
                    .value as ArtifactShareOptions['delivery'],
                })
              }
            >
              <option value="link">Link</option>
              <option value="slides">Slide PNGs</option>
              <option value="pdf">PDF</option>
              <option value="pptx">PPTX</option>
              <option value="html">HTML</option>
            </Select>
          </Field>
        </>
      )}
      {(options.action === 'publish' ||
        (options.action === 'channel' && options.delivery === 'link')) && (
        <>
          <Field label="Link access">
            <Select
              aria-label="Link access"
              value={options.remote ? 'remote' : 'local'}
              disabled={busy}
              onChange={(event) =>
                change({ remote: event.target.value === 'remote' })
              }
            >
              <option value="local">Local link</option>
              <option value="remote">Remote access link</option>
            </Select>
          </Field>
          <p>
            {options.remote
              ? 'A remote access link can start the configured app tunnel. Row-Bot sign-in or device pairing is still required.'
              : 'A local link opens on this computer.'}
          </p>
        </>
      )}
      <Field label="Sharing pages">
        <Input
          aria-label="Sharing pages"
          value={options.pages}
          maxLength={256}
          placeholder="all, 1-3 or 1,3,5"
          disabled={busy}
          onChange={(event) => change({ pages: event.target.value })}
        />
      </Field>
      {options.action !== 'publish' && (
        <Field label="Message or caption">
          <textarea
            aria-label="Message or caption"
            value={options.text}
            maxLength={10000}
            disabled={busy}
            onChange={(event) => change({ text: event.target.value })}
          />
        </Field>
      )}
      {options.delivery === 'pptx' && (
        <Field label="Sharing PPTX mode">
          <Select
            aria-label="Sharing PPTX mode"
            value={options.pptx_mode}
            disabled={busy}
            onChange={(event) =>
              change({
                pptx_mode: event.target
                  .value as ArtifactShareOptions['pptx_mode'],
              })
            }
          >
            <option value="screenshot">High fidelity</option>
            <option value="structured">Editable</option>
          </Select>
        </Field>
      )}
      <Button
        disabled={
          busy ||
          (options.action === 'channel' && !options.channel_name) ||
          !options.pages.trim()
        }
        onClick={() => void run(false)}
      >
        Review sharing
      </Button>
      {review && (
        <div role="group" aria-label="Sharing review">
          <p>
            {review.page_count} pages · {review.delivery}
          </p>
          {review.recipient && <p>Recipient: {review.recipient}</p>}
          <p>
            {options.action === 'publish'
              ? 'This will create or replace the saved published copy.'
              : 'This sends the reviewed content outside Row-Bot.'}
          </p>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => void run(true)}
          >
            {actionLabel}
          </Button>
        </div>
      )}
      {error && <ErrorState title="Sharing unavailable">{error}</ErrorState>}
      {outcome && (
        <div role="status">
          {outcome.status === 'published' ? (
            <p>
              {outcome.link_kind === 'remote_access'
                ? 'Remote access link ready. Sign-in or pairing is required.'
                : 'Local link ready.'}
            </p>
          ) : outcome.status === 'submitted' ? (
            <p>
              Submitted {outcome.submitted_count} of {outcome.total_count} items
              to the destination adapter.
            </p>
          ) : outcome.status === 'denied' ? (
            <p>
              The action was denied before delivery. Review the source, access
              and destination.
            </p>
          ) : (
            <p>
              Delivery or publication is {outcome.status}.{' '}
              {outcome.submitted_count} of {outcome.total_count} sends returned.
              Check the destination before another attempt; retained copies are
              preserved.
            </p>
          )}
          {outcome.code === 'remote_access_unavailable' && (
            <p>The tunnel was unavailable. Only the local link is ready.</p>
          )}
          {url && (
            <a href={url} target="_blank" rel="noopener noreferrer">
              Open{' '}
              {outcome.link_kind === 'remote_access'
                ? 'remote access'
                : 'local'}{' '}
              link
            </a>
          )}
        </div>
      )}
    </section>
  );
}
