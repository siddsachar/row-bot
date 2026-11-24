import { useState, type FormEvent } from 'react';
import {
  Button,
  Field,
  Input,
  Segmented,
  Select,
} from '../../../ui/primitives';
import { ModalTask } from '../../../ui/overlays';
import { ApiError, request } from '../../api';
import { Notice } from '../../page';
import { problem } from '../../words';
import { BeforeYouStart, KeyFileField, type KeyFile } from './fields';
import type {
  ConnectionTest,
  Connector,
  ConnectorKind,
  ConnectorSpace,
} from './types';
import { GUID, KINDS, SOURCE, connectorProblem } from './words';

type Phase =
  | { step: 'form' }
  | { step: 'testing' }
  | { step: 'tested'; result: ConnectionTest };

/**
 * Add a connector: the kind first, then where it reads and how it signs in,
 * then the space and a name. Saving connects it and tests it at once (the
 * server tests saved connectors only). After a failed test the details can
 * be changed and saved again, which replaces the connector this dialog made.
 */
export function AddConnector({
  spaces,
  onClose,
  onSaved,
  onRemoved,
}: {
  spaces: ConnectorSpace[];
  onClose: () => void;
  onSaved: (connector: Connector) => void;
  onRemoved: (id: string) => void;
}) {
  const [kind, setKind] = useState<ConnectorKind>('microsoft');
  const [tenantId, setTenantId] = useState('');
  const [clientId, setClientId] = useState('');
  const [secret, setSecret] = useState('');
  const [driveId, setDriveId] = useState('');
  const [folderId, setFolderId] = useState('');
  const [key, setKey] = useState<KeyFile | null>(null);
  const [subject, setSubject] = useState('');
  const [spaceId, setSpaceId] = useState(
    () =>
      (spaces.find((space) => space.kind === 'company') ?? spaces[0])?.id ?? '',
  );
  const [name, setName] = useState('');
  const [saved, setSaved] = useState<Connector | null>(null);
  const [phase, setPhase] = useState<Phase>({ step: 'form' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const spaceName =
    spaces.find((space) => space.id === spaceId)?.name ?? 'the space';

  function body() {
    const optional = (value: string) => value.trim() || undefined;
    const common = {
      kind,
      space_id: spaceId,
      name: name.trim(),
      folder_id: optional(folderId),
    };
    return kind === 'microsoft'
      ? {
          ...common,
          drive_id: driveId.trim(),
          tenant_id: tenantId.trim(),
          client_id: clientId.trim(),
          client_secret: secret.trim(),
        }
      : {
          ...common,
          drive_id: optional(driveId),
          service_account: key?.text,
          subject: optional(subject),
        };
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (kind === 'google' && !key)
      return setError('Add the service account’s JSON key file.');
    if (kind === 'google' && !driveId.trim() && !folderId.trim())
      return setError('Enter the shared drive’s id, a folder’s id, or both.');
    setBusy(true);
    setError(null);
    let created: Connector;
    try {
      if (saved) {
        try {
          await request(`/api/admin/connectors/${saved.id}`, {
            method: 'DELETE',
          });
        } catch (failure) {
          if (!(failure instanceof ApiError && failure.status === 404))
            throw failure;
        }
        onRemoved(saved.id);
        setSaved(null);
      }
      created = await request<Connector>('/api/admin/connectors', {
        method: 'POST',
        body: body(),
      });
    } catch (failure) {
      setError(connectorProblem(failure, kind));
      setBusy(false);
      return;
    }
    onSaved(created);
    setSaved(created);
    setBusy(false);
    setPhase({ step: 'testing' });
    let result: ConnectionTest;
    try {
      result = await request<ConnectionTest>(
        `/api/admin/connectors/${created.id}/test`,
        { method: 'POST' },
      );
    } catch (failure) {
      result = { ok: false, problem: problem(failure) };
    }
    setPhase({ step: 'tested', result });
  }

  const close = (open: boolean) => {
    if (!open) onClose();
  };

  const result = phase.step === 'tested' ? phase.result : null;
  const shown =
    phase.step === 'testing'
      ? {
          title: 'Testing the connection',
          description: `Row-Bot is signing in to ${SOURCE[kind]} and looking at what ${saved?.name ?? 'it'} reads.`,
        }
      : result?.ok
        ? {
            title: 'Connected',
            description: `Row-Bot signed in and can read what ${saved?.name ?? 'it'} reads.`,
          }
        : result
          ? {
              title: 'Row-Bot couldn’t connect',
              description: result.problem ?? 'The connection didn’t work.',
            }
          : {
              title: 'Add a connector',
              description:
                'Sync a SharePoint, OneDrive or Google Drive folder into a knowledge space.',
            };

  return (
    <ModalTask
      open
      title={shown.title}
      description={shown.description}
      onOpenChange={close}
    >
      {phase.step === 'testing' ? (
        <p role="status">Testing…</p>
      ) : result ? (
        <div className="console-form">
          <p>
            {result.ok
              ? `The first sync starts now. Files appear in ${spaceName} as they are read, and only the people who can open a file in ${SOURCE[kind]} can find it in Row-Bot.`
              : `${saved?.name ?? 'The connector'} is saved, but it can’t sync until this is put right. Change the details and test again, or close this and fix it from the list.`}
          </p>
          <footer className="console-form-actions">
            {result.ok ? (
              <Button variant="primary" onClick={onClose}>
                Done
              </Button>
            ) : (
              <>
                <Button onClick={onClose}>Close</Button>
                <Button
                  variant="primary"
                  onClick={() => setPhase({ step: 'form' })}
                >
                  Change details
                </Button>
              </>
            )}
          </footer>
        </div>
      ) : (
        <form className="console-form" onSubmit={(event) => void submit(event)}>
          <div className="field">
            <span className="field-label">Where the files are</span>
            <Segmented<ConnectorKind>
              label="Where the files are"
              value={kind}
              onChange={(value) => {
                setKind(value);
                setError(null);
              }}
              options={[
                { value: 'microsoft', label: KINDS.microsoft },
                { value: 'google', label: KINDS.google },
              ]}
            />
          </div>
          <BeforeYouStart kind={kind} />
          {kind === 'microsoft' ? (
            <>
              <div className="console-form-pair">
                <Field
                  label="Tenant id"
                  hint="Directory (tenant) ID, on the app registration’s Overview."
                >
                  <Input
                    required
                    pattern={GUID}
                    autoComplete="off"
                    spellCheck={false}
                    value={tenantId}
                    onChange={(event) => setTenantId(event.target.value)}
                  />
                </Field>
                <Field
                  label="Application (client) id"
                  hint="Application (client) ID, on the same page."
                >
                  <Input
                    required
                    pattern={GUID}
                    autoComplete="off"
                    spellCheck={false}
                    value={clientId}
                    onChange={(event) => setClientId(event.target.value)}
                  />
                </Field>
              </div>
              <Field
                label="Client secret"
                hint="Its value, not its id. Stored encrypted on the server and never shown again."
              >
                <Input
                  required
                  type="password"
                  autoComplete="new-password"
                  maxLength={1024}
                  value={secret}
                  onChange={(event) => setSecret(event.target.value)}
                />
              </Field>
              <div className="console-form-pair">
                <Field
                  label="Drive id"
                  hint="The document library or OneDrive to sync."
                >
                  <Input
                    required
                    maxLength={512}
                    autoComplete="off"
                    spellCheck={false}
                    value={driveId}
                    onChange={(event) => setDriveId(event.target.value)}
                  />
                </Field>
                <Field
                  label="Folder id (optional)"
                  hint="Leave it empty to sync the whole drive."
                >
                  <Input
                    maxLength={512}
                    autoComplete="off"
                    spellCheck={false}
                    value={folderId}
                    onChange={(event) => setFolderId(event.target.value)}
                  />
                </Field>
              </div>
            </>
          ) : (
            <>
              <KeyFileField value={key} onChange={setKey} />
              <div className="console-form-pair">
                <Field
                  label="Shared drive id"
                  hint="Enter a shared drive’s id, a folder’s id, or both."
                >
                  <Input
                    maxLength={512}
                    autoComplete="off"
                    spellCheck={false}
                    value={driveId}
                    onChange={(event) => setDriveId(event.target.value)}
                  />
                </Field>
                <Field
                  label="Folder id"
                  hint="A folder in the shared drive, or in someone’s My Drive."
                >
                  <Input
                    maxLength={512}
                    autoComplete="off"
                    spellCheck={false}
                    value={folderId}
                    onChange={(event) => setFolderId(event.target.value)}
                  />
                </Field>
              </div>
              <Field
                label="User it acts as (optional)"
                hint="Only with domain-wide delegation: the person whose My Drive it reads."
              >
                <Input
                  type="email"
                  autoComplete="off"
                  value={subject}
                  onChange={(event) => setSubject(event.target.value)}
                />
              </Field>
            </>
          )}
          <Field
            label="Space"
            hint={`Synced files keep their permissions: only the people who can open a file in ${SOURCE[kind]} can find it in Row-Bot, whoever else reads the space.`}
          >
            <Select
              required
              value={spaceId}
              onChange={(event) => setSpaceId(event.target.value)}
            >
              {spaces.map((space) => (
                <option key={space.id} value={space.id}>
                  {space.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="Name"
            hint="What admins see it as, such as HR policies."
          >
            <Input
              required
              maxLength={160}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          {saved && (
            <Notice title="Saving again replaces the connector you just added" />
          )}
          {error !== null && (
            <p className="console-form-error" role="alert">
              {error}
            </p>
          )}
          <footer className="console-form-actions">
            <Button onClick={onClose}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={busy}>
              {busy
                ? 'Connecting…'
                : saved
                  ? 'Save and test again'
                  : 'Connect and test'}
            </Button>
          </footer>
        </form>
      )}
    </ModalTask>
  );
}
