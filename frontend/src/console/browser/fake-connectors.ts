/**
 * The connector routes for FakeApi, with made-up connectors: a SharePoint
 * library with people Row-Bot can't match and files failing, a paused Google
 * shared drive at its limit, and a SharePoint site stopped after refused
 * sign-ins. Secrets are taken and never answered back (`has_secret` only).
 * It also lists the knowledge spaces connectors sync into, with the ids the
 * Knowledge page's fake uses.
 */
import type { FakeAnswer } from './fake-api';

export const COMPANY_SPACE = 'a0000000-0000-4000-8000-000000000001';
export const ENGINEERING_SPACE = 'a0000000-0000-4000-8000-000000000002';
export const SALES_SPACE = 'a0000000-0000-4000-8000-000000000003';
export const HR_POLICIES = 'c0000000-0000-4000-8000-000000000001';
export const SALES_DECKS = 'c0000000-0000-4000-8000-000000000002';
export const ENGINEERING_WIKI = 'c0000000-0000-4000-8000-000000000003';
/** A drive id the fake's connection test can't find. */
export const MISSING_DRIVE = 'b!missing-drive';

const ME = '11111111-1111-4111-8111-111111111111';
const GRACE = '33333333-3333-4333-8333-333333333333';
const NOW = Date.now();
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

type Fields = Record<string, unknown>;

function space(id: string, name: string, kind: 'company' | 'group') {
  return {
    id,
    name,
    kind,
    reader_groups: kind === 'company' ? [] : [name.split(' ')[0]],
    owner_groups: ['it-admins'],
    owner_principals: [],
    created_by: kind === 'company' ? null : ME,
    created_at: ago(60 * 24 * 20),
    updated_at: ago(60 * 24 * 2),
  };
}

function connector(id: string, fields: Fields): Fields {
  return {
    id,
    kind: 'microsoft',
    space_id: COMPANY_SPACE,
    name: '',
    drive_id: null,
    folder_id: null,
    tenant_id: null,
    client_id: '',
    subject: null,
    has_secret: true,
    principal_map: {},
    state: 'active',
    state_reason: null,
    last_sync_at: null,
    next_sync_at: ago(-15),
    run_as: ME,
    created_by: ME,
    created_at: ago(60 * 24 * 7),
    updated_at: ago(60 * 24),
    permissions_checked_at: null,
    permissions_note: null,
    files: 0,
    documents: 0,
    skipped: 0,
    unmapped_files: 0,
    unmapped: [],
    failing: 0,
    limited: 0,
    warning: null,
    limit_warning: null,
    ...fields,
  };
}

const TENANT = '5f1c2a9e-3b4d-4e6f-8a7b-1c2d3e4f5a6b';
const CLIENT = '9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d';

function connectors(): Fields[] {
  return [
    connector(ENGINEERING_WIKI, {
      name: 'Engineering wiki',
      space_id: ENGINEERING_SPACE,
      drive_id: 'b!eng-wiki-drive',
      tenant_id: TENANT,
      client_id: CLIENT,
      state: 'error',
      state_reason:
        "Microsoft refused the connector's sign-in. Check its credentials and their consent. The connector stopped after 3 refusals in a row: put it right, then save or resume the connector.",
      last_sync_at: ago(60 * 26),
      permissions_checked_at: ago(60 * 27),
      permissions_note:
        'This connector stopped: its files keep their last permissions for 24 hours, then only admins can read them. Permissions last checked at 2026-11-23 08:00 UTC.',
      files: 61,
      documents: 58,
      skipped: 3,
      run_as: GRACE,
    }),
    connector(HR_POLICIES, {
      name: 'HR policies',
      drive_id: 'b!hr-policies-drive',
      folder_id: '01HRPOLICIESFOLDER',
      tenant_id: TENANT,
      client_id: CLIENT,
      last_sync_at: ago(12),
      permissions_checked_at: ago(50),
      principal_map: { 'sharepoint-group:7': 'it-admins' },
      files: 124,
      documents: 98,
      skipped: 22,
      unmapped_files: 4,
      unmapped: [
        { key: 'sharepoint-group:5', name: 'HR Members', files: 3 },
        {
          key: 'link:organization',
          name: 'People in the organisation with the link',
          files: 1,
        },
      ],
      failing: 2,
      warning:
        "4 files are shared with people or groups Row-Bot can't match, so only admins can read them. Map them to Row-Bot groups in the connector's principal map.",
    }),
    connector(SALES_DECKS, {
      kind: 'google',
      name: 'Sales decks',
      space_id: SALES_SPACE,
      drive_id: '0ASALESDECKSDRIVE',
      client_id: 'row-bot-sync@example-project.iam.gserviceaccount.com',
      state: 'paused',
      last_sync_at: ago(60 * 24 * 3),
      next_sync_at: ago(60 * 24 * 3 - 15),
      permissions_checked_at: ago(60 * 24 * 3),
      permissions_note:
        'Paused: its files keep their last permissions. Permissions last checked at 2026-11-21 09:00 UTC.',
      files: 40,
      documents: 31,
      skipped: 9,
      limited: 3,
      limit_warning:
        '3 files were left out: this connector reached its limit of files or space. Connect a narrower folder, or remove files at the source.',
    }),
  ];
}

/** The service account's email in a pasted key file (never kept). */
function serviceAccount(text: unknown) {
  try {
    const key = JSON.parse(String(text)) as Fields;
    return key.type === 'service_account' &&
      typeof key.client_email === 'string'
      ? key.client_email
      : null;
  } catch {
    return null;
  }
}

const invalid = (field: string): FakeAnswer => ({
  status: 422,
  body: {
    detail: [
      {
        loc: ['body', field],
        msg: 'Value error',
        type: 'value_error',
      },
    ],
  },
});

const BY_NAME = (a: Fields, b: Fields) =>
  String(a.name).toLowerCase().localeCompare(String(b.name).toLowerCase());

export class FakeConnectors {
  private spaces: ReturnType<typeof space>[];
  private connectors: Fields[];
  private made = 0;

  constructor(empty: boolean) {
    const company = space(COMPANY_SPACE, 'Company', 'company');
    this.spaces = empty
      ? [company]
      : [
          company,
          space(ENGINEERING_SPACE, 'Engineering', 'group'),
          space(SALES_SPACE, 'Sales team', 'group'),
        ];
    this.connectors = empty ? [] : connectors();
  }

  private shown(found: Fields): FakeAnswer {
    return { status: 200, body: found };
  }

  handle(method: string, url: URL, body: unknown): FakeAnswer | undefined {
    const path = url.pathname;
    if (path === '/api/admin/brain/spaces' && method === 'GET')
      return { status: 200, body: { spaces: this.spaces } };
    if (path === '/api/admin/connectors' && method === 'GET')
      return {
        status: 200,
        body: { connectors: [...this.connectors].sort(BY_NAME) },
      };
    if (path === '/api/admin/connectors' && method === 'POST')
      return this.create(body as Fields);
    const one = path.match(
      /^\/api\/admin\/connectors\/([^/]+)(?:\/(pause|resume|sync|test))?$/,
    );
    if (!one) return undefined;
    const found = this.connectors.find((item) => item.id === one[1]);
    if (!found) return { status: 404, body: { error: 'connector_not_found' } };
    const action = one[2];
    if (!action && method === 'GET') return this.shown(found);
    if (!action && method === 'DELETE') {
      this.connectors = this.connectors.filter((item) => item !== found);
      return { status: 204 };
    }
    if (!action && method === 'PATCH')
      return this.change(found, body as Fields);
    if (action === 'pause') {
      Object.assign(found, {
        state: 'paused',
        state_reason: null,
        permissions_note: `Paused: its files keep their last permissions. Permissions last checked at 2026-11-24 09:00 UTC.`,
      });
      return this.shown(found);
    }
    if (action === 'resume') {
      Object.assign(found, {
        state: 'active',
        state_reason: null,
        permissions_note: null,
        run_as: ME,
        next_sync_at: new Date().toISOString(),
      });
      return this.shown(found);
    }
    if (action === 'sync') {
      if (found.state !== 'active')
        return { status: 409, body: { error: 'connector_not_active' } };
      found.next_sync_at = new Date().toISOString();
      return { status: 202, body: found };
    }
    if (action === 'test')
      return {
        status: 200,
        body:
          found.state === 'error'
            ? {
                ok: false,
                problem:
                  "Microsoft refused the connector's sign-in. Check its credentials and their consent.",
              }
            : found.drive_id === MISSING_DRIVE
              ? {
                  ok: false,
                  problem:
                    "Microsoft can't find the drive or folder this connector reads.",
                }
              : { ok: true, problem: null },
      };
    return undefined;
  }

  private create(fields: Fields): FakeAnswer {
    if (!this.spaces.some((item) => item.id === fields.space_id))
      return { status: 404, body: { error: 'space_not_found' } };
    let clientId = fields.client_id;
    if (fields.kind === 'google') {
      clientId = serviceAccount(fields.service_account);
      if (!clientId) return invalid('service_account');
    }
    this.made += 1;
    const created = connector(
      `c1000000-0000-4000-8000-${String(this.made).padStart(12, '0')}`,
      {
        kind: fields.kind,
        space_id: fields.space_id,
        name: fields.name,
        drive_id: fields.drive_id ?? null,
        folder_id: fields.folder_id ?? null,
        tenant_id: fields.tenant_id ?? null,
        client_id: clientId,
        subject: fields.subject ?? null,
        principal_map: fields.principal_map ?? {},
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        next_sync_at: new Date().toISOString(),
      },
    );
    this.connectors.push(created);
    return { status: 201, body: created };
  }

  private change(found: Fields, fields: Fields): FakeAnswer {
    if (fields.service_account !== undefined) {
      const email = serviceAccount(fields.service_account);
      if (!email) return invalid('service_account');
      found.client_id = email;
    }
    for (const name of ['name', 'principal_map', 'tenant_id', 'client_id'])
      if (fields[name] !== undefined) found[name] = fields[name];
    if (
      fields.client_secret !== undefined ||
      fields.service_account !== undefined
    )
      Object.assign(found, {
        has_secret: true,
        ...(found.state === 'error'
          ? { state: 'active', state_reason: null, permissions_note: null }
          : {}),
      });
    Object.assign(found, { run_as: ME, updated_at: new Date().toISOString() });
    return this.shown(found);
  }
}
