import { useState } from 'react';
import { Link } from 'react-router-dom';
import { AudioLines, BookOpen, ExternalLink, X } from 'lucide-react';
import type { AttentionProblem } from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Hint, IconButton } from '../../ui/primitives';

const CLOUD_SPEECH = 'Use cloud speech-to-text';
const VOICE = '/settings/voice#talk';

/** Only a web page opens from a notice, whatever a link says. */
function webPage(url: string) {
  try {
    return ['https:', 'http:'].includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

/**
 * What to do after Row-Bot restarted because the server ran out of memory
 * (U8): how to resize (the guide, and the host's own page when a known host
 * runs it), cloud speech-to-text in Voice, and Dismiss. Once dismissed it
 * comes back only after another such restart. Home's Needs you shows icons
 * with their names as tooltips; Monitor shows the words.
 */
export default function MemoryNoticeActions({
  problem,
  look = 'text',
  onDismissed,
}: {
  problem: AttentionProblem;
  look?: 'icon' | 'text';
  /** It was dismissed: read what needs the person again. */
  onDismissed: () => void;
}) {
  const { controller } = useRuntime();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const links = (problem.links ?? []).filter((link) => webPage(link.url));
  async function dismiss() {
    setBusy(true);
    setError('');
    try {
      await controller.dismissMemoryNotice();
      onDismissed();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  const dismissButton = (
    <IconButton
      size="sm"
      label="Dismiss"
      disabled={busy}
      aria-busy={busy || undefined}
      onClick={() => void dismiss()}
    >
      <X size={15} aria-hidden />
    </IconButton>
  );
  const note = error && (
    <p role="alert" className="fix-action-note">
      {error}
    </p>
  );
  if (look === 'icon')
    return (
      <>
        {links.map((link, index) => (
          <Hint key={link.url} label={link.label}>
            <a
              className="button ghost icon-button icon-action icon-action-sm"
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={link.label}
            >
              {index === 0 ? (
                <BookOpen size={15} aria-hidden />
              ) : (
                <ExternalLink size={15} aria-hidden />
              )}
            </a>
          </Hint>
        ))}
        <Hint label={CLOUD_SPEECH}>
          <Link
            className="button ghost icon-button icon-action icon-action-sm"
            to={VOICE}
            aria-label={CLOUD_SPEECH}
          >
            <AudioLines size={15} aria-hidden />
          </Link>
        </Hint>
        {dismissButton}
        {note}
      </>
    );
  return (
    <>
      {links.map((link, index) => (
        <a
          key={link.url}
          className={`button small fix-action${index === 0 ? ' primary' : ''}`}
          href={link.url}
          target="_blank"
          rel="noopener noreferrer"
        >
          {index === 0 ? (
            <BookOpen size={14} aria-hidden />
          ) : (
            <ExternalLink size={14} aria-hidden />
          )}
          {link.label}
        </a>
      ))}
      <Link className="button small fix-action" to={VOICE}>
        <AudioLines size={14} aria-hidden />
        {CLOUD_SPEECH}
      </Link>
      {dismissButton}
      {note}
    </>
  );
}
