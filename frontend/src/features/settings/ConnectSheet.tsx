import { useContext, useState, type ReactNode } from 'react';
import { Check, ExternalLink } from 'lucide-react';
import { RuntimeContext } from '../../runtime';
import { writeClipboardText } from '../../platform/clipboard';
import { Button } from '../../ui/primitives';

export type ConnectStep = {
  id: string;
  /** What to do, in one or two short sentences. */
  text: ReactNode;
  /** Where to do it (opens in the browser). */
  link?: { href: string; label: string };
  /** A value to copy into another service (a callback address, a URL). */
  copy?: { value: string; label: string };
  /** The step is already done (a saved field, a running channel). */
  done?: boolean;
  /** The control for this step: a field, Start, Authenticate… */
  children?: ReactNode;
};

/** A value to paste somewhere else, with Copy and a short "Copied". */
export function CopyValue({ value, label }: { value: string; label: string }) {
  const runtime = useContext(RuntimeContext);
  const [copied, setCopied] = useState('');
  return (
    <span className="connect-copy">
      <code className="settings-break-word">{value}</code>
      <Button
        className="small"
        aria-label={`Copy ${label}`}
        onClick={() =>
          void writeClipboardText(value, runtime?.platform.writeClipboard).then(
            (done) => setCopied(done ? 'Copied.' : 'Row-Bot couldn’t copy it.'),
          )
        }
      >
        Copy
      </Button>
      {copied && (
        <span role="status" className="home-caption">
          {copied}
        </span>
      )}
    </span>
  );
}

/**
 * One connect sheet for channels, accounts and plugins: numbered steps with
 * the link where each happens, values to copy, and the control for the step
 * in place (a field, Start, Authenticate). Done steps show a check. Nothing
 * here contacts a service by itself; every outward step is its own button.
 */
export function ConnectSheet({
  title,
  titleHidden = false,
  intro,
  steps,
  children,
}: {
  title: string;
  /** The surrounding disclosure already names the steps ("How to set up X"). */
  titleHidden?: boolean;
  intro?: ReactNode;
  steps: ConnectStep[];
  children?: ReactNode;
}) {
  return (
    <section className="connect-sheet" aria-label={title}>
      <h4 className={titleHidden ? 'visually-hidden' : undefined}>{title}</h4>
      {intro && <p className="settings-help">{intro}</p>}
      <ol className="connect-steps">
        {steps.map((step, index) => (
          <li key={step.id} data-done={step.done ? 'true' : undefined}>
            <span className="connect-step-number" aria-hidden>
              {step.done ? <Check size={13} /> : index + 1}
            </span>
            <div className="connect-step-body">
              <p>
                {step.text}
                {step.done && <span className="visually-hidden"> Done.</span>}
                {step.link && (
                  <>
                    {' '}
                    <a
                      className="settings-inline-action"
                      href={step.link.href}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {step.link.label}
                      <ExternalLink size={12} aria-hidden />
                    </a>
                  </>
                )}
              </p>
              {step.copy && (
                <CopyValue value={step.copy.value} label={step.copy.label} />
              )}
              {step.children}
            </div>
          </li>
        ))}
      </ol>
      {children}
    </section>
  );
}
