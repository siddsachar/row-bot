import { useEffect, useRef, useState } from 'react';
import type { ArtifactPublication } from '../../api/types';
import { writeClipboardText } from '../../platform/clipboard';
import type { ClientPlatform } from '../../platform/types';
import { QrCode } from '../../ui/QrCode';
import { Button, ErrorState, Field, Input, Select } from '../../ui/primitives';

export type ArtifactShareOptions = {
  action: 'publish' | 'unpublish' | 'channel' | 'x';
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
  action: 'publish' | 'unpublish' | 'channel' | 'x';
  channel_name: string | null;
  recipient: string | null;
  delivery: string;
  pages: string;
  page_count: number;
  remote: boolean;
  requires_pairing: boolean;
};
export type ArtifactShareOutcome = {
  status:
    | 'published'
    | 'unpublished'
    | 'submitted'
    | 'partial'
    | 'uncertain'
    | 'denied';
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
  /** The design's published link, if any (Copy, QR, Unpublish). */
  loadPublication?: (signal: AbortSignal) => Promise<ArtifactPublication>;
  writeClipboard?: ClientPlatform['writeClipboard'];
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

const UNPUBLISH: ArtifactShareOptions = {
  action: 'unpublish',
  delivery: 'link',
  pages: 'all',
  text: '',
  pptx_mode: 'screenshot',
  remote: false,
};

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
  const [publication, setPublication] = useState<ArtifactPublication | null>(
    null,
  );
  const [reloadPublication, setReloadPublication] = useState(0);
  const [qr, setQr] = useState(false);
  const [notice, setNotice] = useState('');
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
    setPublication(null);
    setQr(false);
    setNotice('');
  }, [props.resourceId]);
  const { loadPublication, visible, resourceId, resourceRevision } = props;
  useEffect(() => {
    if (!visible || !loadPublication) return;
    const abort = new AbortController();
    loadPublication(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted && value.resource_id === resourceId)
          setPublication(value);
      },
      () => {
        // Without the saved link the panel still publishes; it just can't
        // show the current one.
      },
    );
    return () => abort.abort();
  }, [
    visible,
    loadPublication,
    resourceId,
    resourceRevision,
    reloadPublication,
  ]);

  function change(value: Partial<ArtifactShareOptions>) {
    setOptions((previous) => ({ ...previous, ...value }));
    setReview(null);
    setOutcome(null);
    setError('');
    setNotice('');
  }
  function failure(reason: unknown) {
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
  }
  // Every action asks once (review, then confirm); only Unpublish, which
  // takes a link back, runs straight away.
  async function run(confirm = false, selectedOptions = options) {
    if (operation.current || !props.visible || (confirm && !review)) return;
    const identity = Symbol('sharing');
    operation.current = identity;
    setBusy(true);
    setError('');
    setNotice('');
    const resourceId = props.resourceId;
    const selected = structuredClone(selectedOptions);
    try {
      const reviewed =
        confirm && review ? review : await props.prepare(selected);
      if (current.current.resourceId !== resourceId) return;
      if (
        reviewed.resource_id !== resourceId ||
        reviewed.resource_revision !== current.current.resourceRevision ||
        reviewed.action !== selected.action
      )
        throw { code: 'share_review_changed' };
      if (!confirm && selected.action !== 'unpublish') {
        setReview(reviewed);
        setOutcome(null);
        return;
      }
      const result = await props.execute(
        selected,
        reviewed.review_id,
        reviewed.resource_revision,
      );
      if (current.current.resourceId !== resourceId) return;
      if (result.resource_id !== resourceId)
        throw { code: 'share_review_changed' };
      setReview(null);
      if (result.status === 'unpublished') {
        setOutcome(null);
        setQr(false);
        setPublication((value) =>
          value ? { ...value, published: false, url: null } : value,
        );
        setNotice('Unpublished. The link no longer opens.');
      } else {
        setOutcome(result);
        if (result.status === 'published' && safeUrl(result.url))
          setPublication({
            resource_id: resourceId,
            resource_revision: result.resource_revision,
            published: true,
            url: result.url,
            link_kind: result.link_kind,
            published_at: null,
          });
      }
      setReloadPublication((value) => value + 1);
    } catch (reason) {
      if (current.current.resourceId !== resourceId) return;
      failure(reason);
    } finally {
      if (operation.current === identity) {
        operation.current = null;
        setBusy(false);
      }
    }
  }
  async function copyLink(url: string) {
    const copied = await writeClipboardText(url, props.writeClipboard);
    setNotice(
      copied
        ? 'Link copied.'
        : "The link couldn't be copied here. Select it and copy it yourself.",
    );
  }
  if (!props.visible) return null;
  const publishedUrl = publication?.published ? safeUrl(publication.url) : null;
  const remoteLink = publication?.link_kind === 'remote_access';
  const actionLabel =
    options.action === 'publish'
      ? publishedUrl
        ? 'Update the published copy'
        : options.remote
          ? 'Publish remote access link'
          : 'Publish local link'
      : options.action === 'x'
        ? 'Prepare X post'
        : 'Prepare channel send';
  return (
    <section
      className="studio-section stack"
      aria-label="Design sharing"
      aria-busy={busy}
    >
      {publishedUrl && (
        <section className="share-published" aria-label="Published link">
          <p className="share-published-state">
            <strong>Published</strong> ·{' '}
            {remoteLink
              ? 'opens with Row-Bot sign-in or a paired device'
              : 'opens on this computer'}
          </p>
          <code className="share-published-url">{publishedUrl}</code>
          <div className="action-cluster">
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => void copyLink(publishedUrl)}
            >
              Copy link
            </Button>
            <a
              className="button secondary"
              href={publishedUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open {remoteLink ? 'remote access' : 'local'} link
            </a>
            {remoteLink && (
              <Button
                aria-pressed={qr}
                onClick={() => setQr((value) => !value)}
              >
                QR code
              </Button>
            )}
            <Button disabled={busy} onClick={() => void run(false, UNPUBLISH)}>
              Unpublish
            </Button>
          </div>
          {remoteLink && qr && (
            <QrCode
              value={publishedUrl}
              label="QR code for the published link"
            />
          )}
        </section>
      )}
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
      {!review && (
        <Button
          disabled={
            busy ||
            (options.action === 'channel' && !options.channel_name) ||
            !options.pages.trim()
          }
          onClick={() => void run()}
        >
          {actionLabel}
        </Button>
      )}
      {review && (
        <div role="group" aria-label="Confirm sharing destination">
          {review.action === 'publish' ? (
            <p>
              {review.remote
                ? `Publish ${review.page_count === 1 ? 'this page' : `these ${review.page_count} pages`} at a remote access link? The configured tunnel can make it reachable from the internet; opening it still needs Row-Bot sign-in or a paired device.`
                : `Publish ${review.page_count === 1 ? 'this page' : `these ${review.page_count} pages`} at a local link? It opens on this computer only.`}
            </p>
          ) : (
            <>
              <p>
                {review.page_count} pages · {review.delivery}
              </p>
              {review.recipient && <p>Recipient: {review.recipient}</p>}
            </>
          )}
          <div className="action-cluster">
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => void run(true)}
            >
              {review.action === 'publish'
                ? 'Confirm publish'
                : options.action === 'x'
                  ? 'Confirm post to X'
                  : 'Confirm send to channel'}
            </Button>
            <Button disabled={busy} onClick={() => setReview(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      <p>
        {options.action === 'publish'
          ? 'Publishing creates or replaces the saved published copy.'
          : 'Sending shares the selected content outside Row-Bot.'}
      </p>
      {error && <ErrorState title="Sharing unavailable">{error}</ErrorState>}
      {notice && <p role="status">{notice}</p>}
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
        </div>
      )}
    </section>
  );
}
