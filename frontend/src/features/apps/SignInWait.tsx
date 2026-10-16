import {
  useEffect,
  useId,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import { Check, Copy, ExternalLink, LoaderCircle } from 'lucide-react';
import { useRuntime } from '../../runtime';
import { clientError } from '../../api/errors';
import type { PlanSignIn } from '../../api/types';
import { writeClipboardText } from '../../platform/clipboard';
import { Button, Field, Input } from '../../ui/primitives';

/**
 * Seconds left until `expiresIn` (seconds, by Row-Bot's clock when it last
 * said) runs out, ticking every second. A later, smaller answer moves the end
 * earlier; it never moves later, so the count never jumps up.
 */
export function useTimeLeft(expiresIn: number | null | undefined) {
  const [end, setEnd] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (expiresIn == null) return;
    const next = Date.now() + expiresIn * 1000;
    setNow(Date.now());
    setEnd((current) => (current === null ? next : Math.min(current, next)));
  }, [expiresIn]);
  useEffect(() => {
    if (end === null) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [end]);
  if (expiresIn == null || end === null) return null;
  return Math.max(0, Math.ceil((end - now) / 1000));
}

function clock(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

/** How long the link or code still works, said once a second (not read aloud). */
function TimeLeft({
  seconds,
  what,
}: {
  seconds: number | null;
  what: 'link' | 'code';
}) {
  if (seconds === null) return null;
  return (
    <p className="sign-in-time" role="timer">
      {seconds > 0 ? (
        <>
          The sign-in {what} works for another <strong>{clock(seconds)}</strong>
          .
        </>
      ) : (
        `The sign-in ${what} has expired.`
      )}
    </p>
  );
}

/** "github.com/login/device": where to go, without the scheme. */
export function pageName(address: string) {
  try {
    const url = new URL(address);
    return (url.host + url.pathname).replace(/\/$/, '');
  } catch {
    return address;
  }
}

function DeviceCode({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const { platform } = useRuntime();
  const id = useId();
  return (
    <div className="subscription-device-code-row">
      <output
        className="subscription-device-code-value"
        aria-labelledby={`${id}-label`}
      >
        {code}
      </output>
      <span id={`${id}-label`} className="visually-hidden">
        Sign-in code
      </span>
      <Button
        variant="ghost"
        aria-label="Copy the code"
        onClick={() => {
          void writeClipboardText(code, platform.writeClipboard).then(
            setCopied,
          );
        }}
      >
        {copied ? (
          <Check size={14} aria-hidden />
        ) : (
          <Copy size={14} aria-hidden />
        )}
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  );
}

/**
 * What a sign-in waits for, by how its answer comes back (F27): this
 * computer's browser or Row-Bot's public address return by themselves;
 * another device without either pastes back the address its browser ends
 * on, or enters a code on the service's own page.
 */
export default function SignInWait({
  name,
  signIn,
  busy,
  stop,
  onFinish,
}: {
  name: string;
  signIn: PlanSignIn | null | undefined;
  busy: boolean;
  /** Stop, beside the primary action. */
  stop: ReactNode;
  /** Sends the pasted address; throws a problem the form words plainly. */
  onFinish: (address: string) => Promise<void>;
}) {
  const { platform } = useRuntime();
  const delivery = signIn?.delivery ?? 'this_computer';
  const url = signIn?.authorization_url ?? '';
  const seconds = useTimeLeft(signIn?.expires_in);
  const [opened, setOpened] = useState(false);
  const [address, setAddress] = useState('');
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const errorId = useId();
  const open = (page: string) => {
    setOpened(true);
    void platform.openExternal(page);
  };

  if (signIn?.received)
    return (
      <div className="plan-wait">
        <p className="sign-in-waiting" role="status">
          <LoaderCircle size={16} aria-hidden />
          Finishing signing in to {name}…
        </p>
      </div>
    );

  if (delivery === 'device' && !signIn?.device)
    return (
      <div className="plan-wait">
        <p className="sign-in-waiting" role="status">
          <LoaderCircle size={16} aria-hidden />
          Getting a sign-in code from {name}…
        </p>
        <div className="app-dialog-actions">{stop}</div>
      </div>
    );

  if (delivery === 'device' && signIn?.device) {
    const { user_code, verification_uri, verification_uri_complete } =
      signIn.device;
    const page = pageName(verification_uri);
    return (
      <div className="plan-wait sign-in-elsewhere">
        <p>
          Sign in to {name} with a code. You can do it on this device or any
          other.
        </p>
        <ol className="sign-in-steps">
          <li>
            <span>
              Go to <strong>{page}</strong> and sign in.
            </span>
            <Button
              variant="primary"
              onClick={() =>
                open(verification_uri_complete || verification_uri)
              }
            >
              Open {page}
              <ExternalLink size={14} aria-hidden />
            </Button>
          </li>
          <li>
            <span>Enter this code when it asks, then allow access.</span>
            <DeviceCode code={user_code} />
          </li>
        </ol>
        <p className="sign-in-waiting" role="status">
          <LoaderCircle size={16} aria-hidden />
          Waiting for you to allow access. This page updates by itself.
        </p>
        <TimeLeft seconds={seconds} what="code" />
        <div className="app-dialog-actions">{stop}</div>
      </div>
    );
  }

  if (delivery === 'paste') {
    const submit = async (event: FormEvent) => {
      event.preventDefault();
      if (!address.trim()) return;
      setSending(true);
      setError('');
      try {
        await onFinish(address.trim());
        setAddress('');
      } catch (cause) {
        setError(clientError(cause).message);
      } finally {
        setSending(false);
      }
    };
    return (
      <form
        className="plan-wait sign-in-elsewhere"
        noValidate
        onSubmit={(event) => void submit(event)}
      >
        <p>
          Row-Bot is running on another computer, so signing in to {name} from
          here takes one extra step.
        </p>
        <ol className="sign-in-steps">
          <li>
            <span>Open the sign-in page and allow access.</span>
            <Button
              variant={opened ? 'secondary' : 'primary'}
              disabled={!url}
              onClick={() => open(url)}
            >
              Open sign-in page
              <ExternalLink size={14} aria-hidden />
            </Button>
          </li>
          <li>
            <span>
              You&apos;ll end on a page that doesn&apos;t load. That&apos;s
              expected: copy its whole address from the address bar. It starts
              with <code>http://127.0.0.1</code>.
            </span>
          </li>
          <li>
            <Field label="Paste that address here">
              <Input
                type="url"
                inputMode="url"
                autoComplete="off"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                placeholder="http://127.0.0.1/…"
                value={address}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? errorId : undefined}
                onChange={(event) => {
                  setAddress(event.target.value);
                  setError('');
                }}
              />
            </Field>
            {error && (
              <small
                id={errorId}
                role="alert"
                className="settings-dialog-error"
              >
                {error}
              </small>
            )}
          </li>
        </ol>
        <TimeLeft seconds={seconds} what="link" />
        <div className="app-dialog-actions">
          {stop}
          <Button
            type="submit"
            variant="primary"
            disabled={busy || sending || !address.trim()}
          >
            {sending ? 'Finishing…' : 'Finish'}
          </Button>
        </div>
      </form>
    );
  }

  if (delivery === 'public')
    return (
      <div className="plan-wait">
        <p>
          Sign in to {name} on the page that opens, then come back here. This
          page updates by itself.
        </p>
        <TimeLeft seconds={seconds} what="link" />
        <div className="app-dialog-actions">
          {stop}
          {url && (
            <Button variant="primary" onClick={() => open(url)}>
              {opened ? 'Open the sign-in page again' : 'Open sign-in page'}
              <ExternalLink size={14} aria-hidden />
            </Button>
          )}
        </div>
      </div>
    );

  return (
    <div className="plan-wait">
      <p>
        Finish signing in to {name} in your browser. This page updates by
        itself.
      </p>
      <TimeLeft seconds={seconds} what="link" />
      <div className="app-dialog-actions">
        {stop}
        {url && (
          <Button onClick={() => open(url)}>Open the sign-in page again</Button>
        )}
      </div>
    </div>
  );
}
