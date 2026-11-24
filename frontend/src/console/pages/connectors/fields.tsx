import { useRef, useState } from 'react';
import { ExternalLink, KeyRound, Upload, X } from 'lucide-react';
import { Button, Disclosure, IconButton } from '../../../ui/primitives';
import type { ConnectorKind } from './types';
import { SETUP_GUIDE } from './words';

/** A Google service account's JSON key file: sent once, never shown back. */
export type KeyFile = { text: string; email: string; from: string | null };

/** The key file's service account, or null when the text isn't one. */
export function parseKey(text: string, from: string | null): KeyFile | null {
  let found: unknown;
  try {
    found = JSON.parse(text);
  } catch {
    return null;
  }
  if (!found || typeof found !== 'object') return null;
  const fields = found as Record<string, unknown>;
  return fields.type === 'service_account' &&
    typeof fields.client_email === 'string'
    ? { text, email: fields.client_email, from }
    : null;
}

/**
 * The key file, pasted or chosen. Once it reads as a service account's key,
 * only that account's email shows: the key itself is never on screen again.
 */
export function KeyFileField({
  value,
  onChange,
  hint = 'Paste the JSON key file, or choose it. It is stored encrypted on the server and never shown again.',
}: {
  value: KeyFile | null;
  onChange: (key: KeyFile | null) => void;
  hint?: string;
}) {
  const [paste, setPaste] = useState('');
  const [refused, setRefused] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  if (value)
    return (
      <div className="field">
        <span className="field-label">Service account key</span>
        <div className="connector-key">
          <KeyRound size={16} aria-hidden />
          <span>
            {value.email}
            {` · ${value.from ?? 'pasted key'}`}
          </span>
          <IconButton
            size="sm"
            label="Remove the key file"
            onClick={() => onChange(null)}
          >
            <X size={16} aria-hidden />
          </IconButton>
        </div>
        <small>Key file ready. It is sent once, when you save.</small>
      </div>
    );

  async function chosen(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    const key = parseKey(await file.text(), file.name);
    setRefused(
      key ? null : `${file.name} isn’t a service account’s JSON key file.`,
    );
    if (key) onChange(key);
  }

  return (
    <div className="connector-key-field">
      <label className="field">
        <span className="field-label">Service account key</span>
        <textarea
          className="input connector-textarea"
          rows={4}
          spellCheck={false}
          autoComplete="off"
          placeholder="Paste the JSON key file here"
          value={paste}
          onChange={(event) => {
            const text = event.target.value;
            const key = parseKey(text, null);
            setPaste(key ? '' : text);
            setRefused(
              key || !text.trim()
                ? null
                : 'That isn’t a whole service account key file. Paste all of it, or choose the file.',
            );
            if (key) onChange(key);
          }}
        />
        <small>{hint}</small>
      </label>
      {refused && <small className="connector-refused">{refused}</small>}
      <Button onClick={() => picker.current?.click()}>
        <Upload size={16} aria-hidden />
        Choose key file
      </Button>
      <input
        ref={picker}
        type="file"
        accept=".json,application/json"
        hidden
        aria-label="Choose a key file"
        onChange={(event) => void chosen(event.currentTarget)}
      />
    </div>
  );
}

/** The setup each kind needs before it connects, in plain words, and the full guide. */
export function BeforeYouStart({ kind }: { kind: ConnectorKind }) {
  return (
    <Disclosure summary="Before you start" className="connector-setup">
      {kind === 'microsoft' ? (
        <ol>
          <li>
            In Microsoft Entra, register an app (App registrations, New
            registration, single tenant) and add a client secret to it.
          </li>
          <li>
            Under API permissions, add the Microsoft Graph application
            permission <strong>Sites.Selected</strong>, then give the app read
            access to each site you connect. Files.Read.All works too, but lets
            the app read every site and every OneDrive.
          </li>
          <li>Grant admin consent.</li>
          <li>
            Find the drive id in Graph Explorer:{' '}
            <code>GET /sites/{'{site-id}'}/drives</code>. A folder’s item id is
            optional.
          </li>
        </ol>
      ) : (
        <ol>
          <li>
            In a Google Cloud project with the Drive API turned on, create a
            service account and a JSON key for it.
          </li>
          <li>
            Add the service account’s email to the shared drive as a Viewer. It
            then reads that drive only.
          </li>
          <li>
            For a folder in someone’s My Drive, share the folder with the
            service account, or use domain-wide delegation (scope
            drive.readonly) and enter that person as the user it acts as.
          </li>
        </ol>
      )}
      <a
        className="connector-guide"
        href={SETUP_GUIDE}
        target="_blank"
        rel="noreferrer"
      >
        The full setup guide
        <ExternalLink size={14} aria-hidden />
      </a>
    </Disclosure>
  );
}
