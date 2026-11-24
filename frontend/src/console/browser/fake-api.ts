/**
 * An in-memory Company Server API for the console's browser spec: the admin
 * routes the console calls, with made-up people and data. `state` picks what
 * the pages see: everything filled in, nothing yet, or failing loads.
 */
import { FakeBrain } from './fake-brain';

export type FakeState = 'populated' | 'empty' | 'error';
export type FakeAnswer = { status: number; body?: unknown };
export type FakeCall = { method: string; path: string; body: unknown };

const ME = '11111111-1111-4111-8111-111111111111';
const ADA = '22222222-2222-4222-8222-222222222222';
const GRACE = '33333333-3333-4333-8333-333333333333';
const LIN = '44444444-4444-4444-8444-444444444444';
const NOW = Date.now();
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

function people() {
  return [
    {
      id: ADA,
      display_name: 'Ada Lovelace',
      email: 'ada@example.com',
      groups: ['Engineering', 'Sales'],
      role: 'member',
      admin: false,
      last_sign_in_at: ago(42),
      suspended: false,
    },
    {
      id: GRACE,
      display_name: 'Grace Hopper',
      email: 'grace@example.com',
      groups: ['it-admins'],
      role: 'member',
      admin: true,
      last_sign_in_at: ago(60 * 26),
      suspended: false,
    },
    {
      id: LIN,
      display_name: 'Lin Okafor',
      email: 'lin@example.com',
      groups: ['Sales'],
      role: 'member',
      admin: false,
      last_sign_in_at: ago(60 * 24 * 9),
      suspended: true,
    },
    {
      id: ME,
      display_name: 'Sam Rivera',
      email: 'sam@example.com',
      groups: ['it-admins'],
      role: 'admin',
      admin: true,
      last_sign_in_at: ago(3),
      suspended: false,
    },
  ];
}

function devices() {
  return [
    {
      id: 'd1000000-0000-4000-8000-000000000001',
      principal_id: ADA,
      name: 'Ada’s ThinkPad',
      platform: 'windows',
      app_version: '5.4.0',
      first_seen_at: ago(60 * 24 * 30),
      last_seen_at: ago(12),
      revoked_at: null,
    },
    {
      id: 'd1000000-0000-4000-8000-000000000002',
      principal_id: ME,
      name: 'Sam’s MacBook Pro',
      platform: 'macos',
      app_version: '5.4.0',
      first_seen_at: ago(60 * 24 * 40),
      last_seen_at: ago(2),
      revoked_at: null,
    },
    {
      id: 'd1000000-0000-4000-8000-000000000003',
      principal_id: GRACE,
      name: 'Build box',
      platform: 'linux',
      app_version: '5.3.4',
      first_seen_at: ago(60 * 24 * 60),
      last_seen_at: ago(60 * 24 * 3),
      revoked_at: null,
    },
    {
      id: 'd1000000-0000-4000-8000-000000000004',
      principal_id: LIN,
      name: 'Old laptop',
      platform: 'windows',
      app_version: '5.3.1',
      first_seen_at: ago(60 * 24 * 90),
      last_seen_at: ago(60 * 24 * 20),
      revoked_at: ago(60 * 24 * 9),
    },
  ];
}

const ENDPOINT = 'e1000000-0000-4000-8000-000000000001';
function endpoints() {
  return [
    {
      id: 'e1000000-0000-4000-8000-000000000002',
      name: 'Anthropic',
      api: 'anthropic',
      base_url: 'https://api.anthropic.com',
      has_key: true,
      timeout_seconds: 120,
      enabled: true,
    },
    {
      id: ENDPOINT,
      name: 'GPU box',
      api: 'openai',
      base_url: 'http://gpu-box.internal:8000/v1',
      has_key: false,
      timeout_seconds: 60,
      enabled: true,
    },
  ];
}

function models() {
  return [
    {
      id: 'm1',
      endpoint_id: ENDPOINT,
      name: 'company-chat',
      upstream_model: 'qwen3.5-27b',
      display_name: 'company-chat',
      kind: 'chat',
      context_window: 32768,
      input_price: 0,
      output_price: 0,
      is_default: true,
    },
    {
      id: 'm2',
      endpoint_id: 'e1000000-0000-4000-8000-000000000002',
      name: 'claude-sonnet',
      upstream_model: 'claude-sonnet-4-5',
      display_name: 'claude-sonnet',
      kind: 'chat',
      context_window: 200000,
      input_price: 3,
      output_price: 15,
      is_default: false,
    },
    {
      id: 'm3',
      endpoint_id: ENDPOINT,
      name: 'company-embed',
      upstream_model: 'company-embed',
      display_name: 'company-embed',
      kind: 'embeddings',
      context_window: null,
      input_price: 0,
      output_price: 0,
      is_default: true,
    },
  ];
}

function audit() {
  const event = (
    seq: number,
    minutes: number,
    action: string,
    actor: [string, string],
    extra: Record<string, unknown> = {},
  ) => ({
    seq,
    prev_hash: 'a'.repeat(64),
    hash: 'b'.repeat(64),
    event: {
      id: `ev-${seq}`,
      occurred_at: ago(minutes),
      recorded_at: ago(minutes),
      org_id: 'company',
      actor: { kind: actor[0], id: actor[1] },
      action,
      category: action.split('.')[0],
      outcome: 'succeeded',
      layer: 'org',
      target: null,
      device_id: null,
      decided_by: null,
      turn_id: null,
      request_id: null,
      redaction: 'metadata',
      details: {},
      ...extra,
    },
  });
  return [
    event(60, 3, 'session.signed_in', ['person', ME]),
    event(59, 12, 'model.called', ['person', ADA], {
      details: {
        provider: 'gpu-box',
        model: 'company-chat',
        input_tokens: 1200,
        output_tokens: 340,
        duration_ms: 2100,
      },
    }),
    event(58, 40, 'policy.changed', ['admin', ME], {
      details: { digest: 'c'.repeat(64), schema_version: 1 },
    }),
    event(57, 42, 'session.signed_in', ['person', ADA]),
    event(56, 60 * 5, 'person.suspended', ['admin', ME], {
      target: { kind: 'person', ref: LIN },
    }),
    event(55, 60 * 5, 'session.revoked', ['admin', ME], {
      target: { kind: 'person', ref: LIN },
    }),
    event(54, 60 * 26, 'role.changed', ['operator', 'cli'], {
      target: { kind: 'person', ref: ME },
      details: { role: 'admin', change: 'granted' },
    }),
    event(53, 60 * 27, 'session.signed_in', ['person', LIN], {
      outcome: 'denied',
    }),
    event(52, 60 * 30, 'org_settings.changed', ['admin', ME], {
      target: { kind: 'setting', ref: `gateway.endpoint.${ENDPOINT}` },
      details: { change: 'created' },
    }),
    event(51, 60 * 31, 'device.joined', ['person', ADA], {
      target: { kind: 'device', ref: 'd1' },
      details: { platform: 'windows', app_version: '5.4.0' },
    }),
  ];
}

const COMPANY = {
  version: 3,
  models: { personal_api_keys: false, local_models: true },
  tools: { disabled_groups: ['computer_use', 'shell'] },
  approvals: { max_level: 'auto' },
  apps: { mcp_servers: 'allow-list', allowed_mcp_servers: ['jira'] },
};
const GROUPS = {
  Engineering: {
    version: 2,
    models: { personal_api_keys: true },
    tools: { enabled_groups: ['shell'] },
    offline: { max_days: 30 },
  },
};

export class FakeApi {
  calls: FakeCall[] = [];
  private people = people();
  private devices = devices();
  private company: Record<string, unknown> = COMPANY;
  private groups: Record<string, Record<string, unknown>> = GROUPS;
  private brain: FakeBrain;

  constructor(readonly state: FakeState = 'populated') {
    this.brain = new FakeBrain(state !== 'populated');
    if (state !== 'populated') {
      this.people = this.people.filter((person) => person.id === ME);
      this.devices = [];
      this.company = { version: 0 };
      this.groups = {};
    }
  }

  handle(method: string, url: URL, body: unknown): FakeAnswer {
    const path = url.pathname;
    this.calls.push({ method, path: path + url.search, body });
    if (path === '/api/me')
      return {
        status: 200,
        body: {
          id: ME,
          display_name: 'Sam Rivera',
          email: 'sam@example.com',
          status: 'active',
          groups: ['it-admins'],
          admin: true,
        },
      };
    if (path === '/api/admin/people')
      return { status: 200, body: { people: this.people } };
    if (path === '/auth/logout') return { status: 204 };
    if (this.state === 'error' && method === 'GET')
      return { status: 500, body: { error: 'internal' } };
    const empty = this.state === 'empty';
    const person = path.match(
      /^\/api\/admin\/people\/([^/]+)\/(suspend|resume|role)$/,
    );
    if (person) {
      const found = this.people.find((item) => item.id === person[1]);
      if (!found) return { status: 404, body: { error: 'person_not_found' } };
      if (person[2] === 'suspend') found.suspended = true;
      if (person[2] === 'resume') found.suspended = false;
      if (person[2] === 'role') {
        found.role = (body as { role: string }).role;
        found.admin =
          found.role === 'admin' || found.groups.includes('it-admins');
      }
      return { status: 200, body: found };
    }
    if (path === '/api/admin/devices') {
      const revoked = url.searchParams.get('revoked');
      const owner = url.searchParams.get('person');
      return {
        status: 200,
        body: {
          devices: this.devices.filter(
            (device) =>
              (revoked === null ||
                String(device.revoked_at !== null) === revoked) &&
              (!owner || device.principal_id === owner),
          ),
        },
      };
    }
    const revoke = path.match(/^\/api\/admin\/devices\/([^/]+)\/revoke$/);
    if (revoke) {
      const device = this.devices.find((item) => item.id === revoke[1]);
      if (!device) return { status: 404, body: { error: 'device_not_found' } };
      device.revoked_at = new Date().toISOString();
      return { status: 200, body: device };
    }
    if (path === '/api/admin/endpoints' && method === 'GET')
      return { status: 200, body: { endpoints: empty ? [] : endpoints() } };
    if (path === '/api/admin/endpoints' && method === 'POST') {
      const { api_key: key, ...fields } = body as Record<string, unknown>;
      return {
        status: 201,
        body: { id: 'e-new', ...fields, has_key: Boolean(key) },
      };
    }
    if (path === '/api/admin/models' && method === 'GET')
      return { status: 200, body: { models: empty ? [] : models() } };
    if (path === '/api/admin/budgets')
      return {
        status: 200,
        body: {
          budgets: empty
            ? []
            : [
                {
                  id: 'b1',
                  scope: 'group',
                  subject: 'Sales',
                  soft_limit: 200,
                  hard_limit: 300,
                },
                {
                  id: 'b2',
                  scope: 'person',
                  subject: ADA,
                  soft_limit: 50,
                  hard_limit: null,
                },
              ],
        },
      };
    if (path === '/api/admin/usage') {
      const by = url.searchParams.get('by') ?? 'person';
      const rows =
        {
          person: [ADA, ME, GRACE],
          group: ['Engineering', 'Sales', 'it-admins'],
          model: ['claude-sonnet', 'company-chat', 'company-embed'],
        }[by] ?? [];
      return {
        status: 200,
        body: {
          by,
          usage: empty
            ? []
            : rows.map((key, index) => ({
                [by]: key,
                calls: 412 - index * 120,
                input_tokens: 812_400 - index * 200_000,
                output_tokens: 140_220 - index * 40_000,
                cost: 18.42 - index * 6,
              })),
        },
      };
    }
    if (path === '/api/admin/policy' && method === 'GET')
      return {
        status: 200,
        body: { company: this.company, groups: this.groups },
      };
    if (path === '/api/admin/policy' && method === 'PUT') {
      const document = body as Record<string, unknown>;
      this.company = { ...document, version: Number(document.version) + 1 };
      const models = (document.models ?? {}) as Record<string, unknown>;
      return {
        status: 200,
        body: {
          policy: this.company,
          unknown_models: ((models.allowed ?? []) as string[]).filter(
            (name) =>
              name !== '*' &&
              !['company-chat', 'claude-sonnet', 'company-embed'].includes(
                name,
              ),
          ),
          still_granted_by:
            models.local_models === false
              ? { Engineering: ['models.local_models'] }
              : {},
        },
      };
    }
    const group = path.match(/^\/api\/admin\/policy\/groups\/(.+)$/);
    if (group && method === 'PUT') {
      const document = body as Record<string, unknown>;
      const saved = { ...document, version: Number(document.version) + 1 };
      this.groups = { ...this.groups, [decodeURIComponent(group[1])]: saved };
      return { status: 200, body: { policy: saved, unknown_models: [] } };
    }
    if (path === '/api/admin/policy/history')
      return {
        status: 200,
        body: {
          entries: empty
            ? []
            : [
                {
                  version: 3,
                  group: null,
                  changed_by: ME,
                  changed_at: ago(40),
                  policy: COMPANY,
                },
                {
                  version: 2,
                  group: 'Engineering',
                  changed_by: ME,
                  changed_at: ago(60 * 24),
                  policy: GROUPS.Engineering,
                },
                {
                  version: 1,
                  group: null,
                  changed_by: GRACE,
                  changed_at: ago(60 * 24 * 3),
                  policy: { version: 1, models: { personal_api_keys: false } },
                },
              ],
        },
      };
    if (path === '/api/admin/audit') {
      const all = empty ? [] : audit();
      const action = url.searchParams.get('action');
      const before = Number(url.searchParams.get('before') ?? Infinity);
      const matching = all.filter(
        (link) =>
          (!action || link.event.action === action) && link.seq < before,
      );
      const page = matching.slice(0, 6);
      return {
        status: 200,
        body: {
          events: page,
          next_before: matching.length > 6 ? page[page.length - 1].seq : null,
        },
      };
    }
    if (path === '/api/admin/audit/verify')
      return {
        status: 200,
        body: {
          ok: true,
          checked: 60,
          head: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
          broken_at: null,
          reason: null,
        },
      };
    const brain = this.brain.handle(method, url, body);
    return brain ?? { status: 404, body: { error: 'not_found' } };
  }
}
