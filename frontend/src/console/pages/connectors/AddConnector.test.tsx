import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import {
  COMPANY_SPACE,
  ENGINEERING_SPACE,
  MISSING_DRIVE,
} from '../../browser/fake-connectors';
import { renderConsole, sent } from '../../testing';
import { SETUP_GUIDE } from './words';

afterEach(() => vi.unstubAllGlobals());

const TENANT = '0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e';
const CLIENT = '1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d';
const KEY = JSON.stringify({
  type: 'service_account',
  client_email: 'finance-sync@example-project.iam.gserviceaccount.com',
  private_key: 'test-only-placeholder',
});

async function openAdd(state: 'populated' | 'empty' = 'populated') {
  const api = renderConsole('/connectors', state);
  await userEvent.click(
    await screen.findByRole('button', { name: 'Add connector' }),
  );
  return {
    api,
    dialog: screen.getByRole('dialog', { name: 'Add a connector' }),
  };
}

async function fillMicrosoft(dialog: HTMLElement, drive: string) {
  const field = (label: RegExp) => within(dialog).getByLabelText(label);
  await userEvent.type(field(/^Tenant id/), TENANT);
  await userEvent.type(field(/^Application \(client\) id/), CLIENT);
  await userEvent.type(field(/^Client secret/), 'test-client-secret');
  await userEvent.type(field(/^Drive id/), drive);
}

test('adding a SharePoint connector: setup steps, fields, then save and test', async () => {
  const { api, dialog } = await openAdd();
  expect(
    within(dialog).getByRole('radio', { name: 'SharePoint or OneDrive' }),
  ).toHaveAttribute('aria-checked', 'true');
  await userEvent.click(within(dialog).getByText('Before you start'));
  expect(dialog).toHaveTextContent('Sites.Selected');
  expect(dialog).toHaveTextContent('Grant admin consent.');
  expect(
    within(dialog).getByRole('link', { name: 'The full setup guide' }),
  ).toHaveAttribute('href', SETUP_GUIDE);
  expect(within(dialog).getByLabelText(/^Client secret/)).toHaveAttribute(
    'type',
    'password',
  );

  await fillMicrosoft(dialog, 'b!finance-drive');
  await userEvent.type(
    within(dialog).getByLabelText(/^Folder id/),
    '01FINANCEFOLDER',
  );
  await userEvent.selectOptions(
    within(dialog).getByLabelText(/^Space/),
    'Engineering',
  );
  await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Finance');
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Connect and test' }),
  );

  const done = await screen.findByRole('dialog', { name: 'Connected' });
  expect(done).toHaveTextContent(
    'Files appear in Engineering as they are read, and only the people who can open a file in SharePoint can find it in Row-Bot.',
  );
  expect(sent(api, 'POST', '/api/admin/connectors')).toEqual({
    kind: 'microsoft',
    space_id: ENGINEERING_SPACE,
    name: 'Finance',
    folder_id: '01FINANCEFOLDER',
    drive_id: 'b!finance-drive',
    tenant_id: TENANT,
    client_id: CLIENT,
    client_secret: 'test-client-secret',
  });
  expect(
    api.calls.some(
      (call) => call.method === 'POST' && call.path.endsWith('/test'),
    ),
  ).toBe(true);
  await userEvent.click(within(done).getByRole('button', { name: 'Done' }));

  const list = screen.getByRole('list', { name: 'Connectors' });
  expect(within(list).getByText('Finance')).toBeInTheDocument();
  // The secret went once, to the server; nothing shows it again.
  expect(
    screen.queryByDisplayValue('test-client-secret'),
  ).not.toBeInTheDocument();
  expect(document.body).not.toHaveTextContent('test-client-secret');
});

test('a failed test says why in plain words, and saving again replaces the connector', async () => {
  const { api, dialog } = await openAdd('empty');
  await fillMicrosoft(dialog, MISSING_DRIVE);
  await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Finance');
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Connect and test' }),
  );

  const failed = await screen.findByRole('dialog', {
    name: 'Row-Bot couldn’t connect',
  });
  expect(failed).toHaveTextContent(
    "Microsoft can't find the drive or folder this connector reads.",
  );
  expect(failed).toHaveTextContent(
    'Finance is saved, but it can’t sync until this is put right.',
  );
  const first = api.calls.filter(
    (call) => call.method === 'POST' && call.path === '/api/admin/connectors',
  ).length;
  expect(first).toBe(1);

  await userEvent.click(
    within(failed).getByRole('button', { name: 'Change details' }),
  );
  const again = screen.getByRole('dialog', { name: 'Add a connector' });
  expect(again).toHaveTextContent(
    'Saving again replaces the connector you just added',
  );
  const drive = within(again).getByLabelText(/^Drive id/);
  expect(drive).toHaveValue(MISSING_DRIVE);
  await userEvent.clear(drive);
  await userEvent.type(drive, 'b!finance-drive');
  await userEvent.click(
    within(again).getByRole('button', { name: 'Save and test again' }),
  );
  await userEvent.click(
    within(await screen.findByRole('dialog', { name: 'Connected' })).getByRole(
      'button',
      { name: 'Done' },
    ),
  );

  expect(
    api.calls.some(
      (call) =>
        call.method === 'DELETE' &&
        call.path.startsWith('/api/admin/connectors/'),
    ),
  ).toBe(true);
  const list = screen.getByRole('list', { name: 'Connectors' });
  expect(within(list).getAllByRole('listitem')).toHaveLength(1);
  expect(sent(api, 'POST', '/api/admin/connectors')).toMatchObject({
    drive_id: 'b!finance-drive',
  });
});

test('adding a Google Drive connector with a pasted key file that is never shown', async () => {
  const { api, dialog } = await openAdd();
  await userEvent.click(
    within(dialog).getByRole('radio', { name: 'Google Drive' }),
  );
  await userEvent.click(within(dialog).getByText('Before you start'));
  expect(dialog).toHaveTextContent(
    'Add the service account’s email to the shared drive as a Viewer.',
  );

  await userEvent.click(within(dialog).getByLabelText(/^Service account key/));
  await userEvent.paste(KEY);
  expect(dialog).toHaveTextContent(
    'finance-sync@example-project.iam.gserviceaccount.com · pasted key',
  );
  expect(dialog).not.toHaveTextContent('test-only-placeholder');
  expect(screen.queryByDisplayValue(/test-only-placeholder/)).toBeNull();

  await userEvent.type(
    within(dialog).getByLabelText(/^Shared drive id/),
    '0AFINANCEDRIVE',
  );
  await userEvent.type(
    within(dialog).getByLabelText(/^User it acts as/),
    'finance-lead@example.com',
  );
  await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Finance drive');
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Connect and test' }),
  );

  const done = await screen.findByRole('dialog', { name: 'Connected' });
  expect(done).toHaveTextContent(
    'only the people who can open a file in Google Drive can find it in Row-Bot.',
  );
  expect(sent(api, 'POST', '/api/admin/connectors')).toEqual({
    kind: 'google',
    space_id: COMPANY_SPACE,
    name: 'Finance drive',
    drive_id: '0AFINANCEDRIVE',
    service_account: KEY,
    subject: 'finance-lead@example.com',
  });
  await userEvent.click(within(done).getByRole('button', { name: 'Done' }));
  expect(document.body).not.toHaveTextContent('test-only-placeholder');
});

test('a Google key file can be chosen, and a file that is not one is refused', async () => {
  const { dialog } = await openAdd();
  await userEvent.click(
    within(dialog).getByRole('radio', { name: 'Google Drive' }),
  );
  const picker = within(dialog).getByLabelText('Choose a key file');
  await userEvent.upload(
    picker,
    new File(['{"type": "authorized_user"}'], 'notes.json', {
      type: 'application/json',
    }),
  );
  expect(
    await within(dialog).findByText(
      'notes.json isn’t a service account’s JSON key file.',
    ),
  ).toBeInTheDocument();

  await userEvent.upload(
    picker,
    new File([KEY], 'finance-key.json', { type: 'application/json' }),
  );
  expect(
    await within(dialog).findByText(
      'finance-sync@example-project.iam.gserviceaccount.com · finance-key.json',
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Remove the key file' }),
  ).toBeInTheDocument();
});

test('a Google connector without a key file or a place to read says so before sending', async () => {
  const { api, dialog } = await openAdd();
  await userEvent.click(
    within(dialog).getByRole('radio', { name: 'Google Drive' }),
  );
  await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Finance drive');
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Connect and test' }),
  );
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'Add the service account’s JSON key file.',
  );

  await userEvent.click(within(dialog).getByLabelText(/^Service account key/));
  await userEvent.paste(KEY);
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Connect and test' }),
  );
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'Enter the shared drive’s id, a folder’s id, or both.',
  );
  expect(
    api.calls.some(
      (call) => call.method === 'POST' && call.path === '/api/admin/connectors',
    ),
  ).toBe(false);
});

test('a field the server refuses is named in plain words', async () => {
  renderConsole('/connectors', 'populated', (url, method) =>
    method === 'POST' && url.pathname === '/api/admin/connectors'
      ? {
          status: 422,
          body: {
            detail: [
              {
                loc: ['body', 'tenant_id'],
                msg: 'Value error',
                type: 'value_error',
              },
            ],
          },
        }
      : undefined,
  );
  await userEvent.click(
    await screen.findByRole('button', { name: 'Add connector' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Add a connector' });
  await fillMicrosoft(dialog, 'b!finance-drive');
  await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Finance');
  await userEvent.click(
    within(dialog).getByRole('button', { name: 'Connect and test' }),
  );
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    'Check the tenant id: it is a GUID',
  );
});
