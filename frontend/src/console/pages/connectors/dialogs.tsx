import { useId, useState } from 'react';
import { Field, Input } from '../../../ui/primitives';
import { request } from '../../api';
import { useDirectory } from '../../directory';
import { FormDialog } from '../../form-dialog';
import { KeyFileField, type KeyFile } from './fields';
import type { Connector } from './types';
import {
  GUID,
  SYNCS_AS_YOU,
  connectorProblem,
  keyKind,
  keyName,
  plural,
} from './words';

type Edit = {
  connector: Connector;
  onClose: () => void;
  onSaved: (connector: Connector) => void;
};

function patch(connector: Connector, body: Record<string, unknown>) {
  return request<Connector>(`/api/admin/connectors/${connector.id}`, {
    method: 'PATCH',
    body,
  });
}

export function RenameDialog({ connector, onClose, onSaved }: Edit) {
  const [name, setName] = useState(connector.name);
  return (
    <FormDialog
      title={`Rename ${connector.name}`}
      description={SYNCS_AS_YOU}
      explain={(error) => connectorProblem(error, connector.kind)}
      submitLabel="Save"
      onClose={onClose}
      onSubmit={async () => {
        if (name.trim() !== connector.name)
          onSaved(await patch(connector, { name: name.trim() }));
      }}
    >
      <Field label="Name">
        <Input
          required
          maxLength={160}
          value={name}
          onChange={(event) => setName(event.target.value)}
          data-initial-focus
        />
      </Field>
    </FormDialog>
  );
}

const NO_KEY = 'Add the new JSON key file for the service account.';

/** New credentials, write-only: the saved secret is never shown, only that one is saved. */
export function CredentialsDialog({ connector, onClose, onSaved }: Edit) {
  const [tenantId, setTenantId] = useState(connector.tenant_id ?? '');
  const [clientId, setClientId] = useState(connector.client_id);
  const [secret, setSecret] = useState('');
  const [key, setKey] = useState<KeyFile | null>(null);
  const microsoft = connector.kind === 'microsoft';

  async function save() {
    if (!microsoft) {
      if (!key) throw new Error(NO_KEY);
      onSaved(await patch(connector, { service_account: key.text }));
      return;
    }
    onSaved(
      await patch(connector, {
        client_secret: secret.trim(),
        ...(tenantId.trim() !== connector.tenant_id
          ? { tenant_id: tenantId.trim() }
          : {}),
        ...(clientId.trim() !== connector.client_id
          ? { client_id: clientId.trim() }
          : {}),
      }),
    );
  }

  return (
    <FormDialog
      title={`Replace ${connector.name}’s credentials`}
      description={SYNCS_AS_YOU}
      explain={(error) =>
        error instanceof Error && error.message === NO_KEY
          ? NO_KEY
          : connectorProblem(error, connector.kind)
      }
      submitLabel="Save credentials"
      onClose={onClose}
      onSubmit={save}
    >
      {microsoft ? (
        <>
          <div className="console-form-pair">
            <Field label="Tenant id">
              <Input
                required
                pattern={GUID}
                autoComplete="off"
                spellCheck={false}
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
              />
            </Field>
            <Field label="Application (client) id">
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
            hint={
              connector.has_secret
                ? 'Secret saved. Enter the new secret: it replaces the saved one and is never shown again.'
                : 'No secret is saved. Enter it: it is stored encrypted and never shown again.'
            }
          >
            <Input
              required
              type="password"
              autoComplete="new-password"
              maxLength={1024}
              placeholder={connector.has_secret ? 'Secret saved' : undefined}
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
              data-initial-focus
            />
          </Field>
        </>
      ) : (
        <KeyFileField
          value={key}
          onChange={setKey}
          hint={`Key file saved for ${connector.client_id}. Paste or choose the new key file to replace it.`}
        />
      )}
    </FormDialog>
  );
}

/**
 * The principal map: each upstream person, group or link Row-Bot can't match,
 * given the Row-Bot group whose people may read its files. An empty group
 * takes the key out of the map, and its files stay admins only.
 */
export function PrincipalMapDialog({
  connector,
  focus,
  onClose,
  onSaved,
}: Edit & { focus: string | null }) {
  const { groups } = useDirectory();
  const suggestions = useId();
  const known = new Set(connector.unmapped.map((item) => item.key));
  const rows = [
    ...connector.unmapped,
    ...Object.keys(connector.principal_map)
      .filter((key) => !known.has(key))
      .map((key) => ({ key, name: keyName(key), files: null })),
  ];
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      rows.map((row) => [row.key, connector.principal_map[row.key] ?? '']),
    ),
  );

  async function save() {
    const map = Object.fromEntries(
      Object.entries(values)
        .map(([key, group]): [string, string] => [key, group.trim()])
        .filter(([, group]) => group),
    );
    const before = connector.principal_map;
    const same =
      Object.keys(map).length === Object.keys(before).length &&
      Object.entries(map).every(([key, group]) => before[key] === group);
    if (!same) onSaved(await patch(connector, { principal_map: map }));
  }

  return (
    <FormDialog
      title={`Map ${connector.name}’s people and groups`}
      description="Row-Bot can’t tell who these are. Give each one the Row-Bot group whose people should read its files, or leave it empty to keep those files for admins only."
      explain={(error) => connectorProblem(error, connector.kind)}
      submitLabel="Save the map"
      onClose={onClose}
      onSubmit={save}
    >
      {rows.map((row) => (
        <Field
          key={row.key}
          label={row.name}
          hint={`${keyKind(row.key)} · ${
            row.files === null ? 'mapped before' : plural(row.files, 'file')
          }`}
        >
          <Input
            list={suggestions}
            maxLength={256}
            placeholder="Admins only"
            value={values[row.key] ?? ''}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                [row.key]: event.target.value,
              }))
            }
            data-initial-focus={row.key === focus || undefined}
          />
        </Field>
      ))}
      <datalist id={suggestions}>
        {groups.map((group) => (
          <option key={group} value={group} />
        ))}
      </datalist>
      <p className="console-footnote">
        Row-Bot reads every file’s permissions again after you save.{' '}
        {SYNCS_AS_YOU}
      </p>
    </FormDialog>
  );
}
