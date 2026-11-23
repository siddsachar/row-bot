/** The Company Server's admin API, as the console reads it. */

export type Me = {
  id: string;
  display_name: string;
  email: string | null;
  status: string;
  groups: string[];
  admin: boolean;
};

export type Person = {
  id: string;
  display_name: string;
  email: string | null;
  groups: string[];
  role: 'admin' | 'member';
  admin: boolean;
  last_sign_in_at: string;
  suspended: boolean;
};

export type Device = {
  id: string;
  principal_id: string;
  name: string;
  platform: 'windows' | 'macos' | 'linux';
  app_version: string;
  first_seen_at: string;
  last_seen_at: string;
  revoked_at: string | null;
};

export type Endpoint = {
  id: string;
  name: string;
  api: 'openai' | 'anthropic';
  base_url: string;
  has_key: boolean;
  timeout_seconds: number;
  enabled: boolean;
};

export type CatalogModel = {
  id: string;
  endpoint_id: string;
  name: string;
  upstream_model: string;
  display_name: string;
  kind: 'chat' | 'embeddings';
  context_window: number | null;
  input_price: number;
  output_price: number;
  is_default: boolean;
};

export type Budget = {
  id: string;
  scope: 'person' | 'group';
  subject: string;
  soft_limit: number | null;
  hard_limit: number | null;
};

export type UsageBy = 'person' | 'group' | 'model';
export type UsageRow = {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
} & Partial<Record<UsageBy, string>>;

/** A company policy or a group override as written: only what it sets. */
export type PolicyDocument = {
  version?: number;
} & Record<string, Record<string, unknown> | number | undefined>;

export type PolicyRevision = {
  version: number;
  group: string | null;
  changed_by: string;
  changed_at: string;
  policy: PolicyDocument;
};

export type PolicyWrite = {
  policy: PolicyDocument;
  unknown_models: string[];
  still_granted_by?: Record<string, string[]>;
};

export type AuditEvent = {
  id: string;
  occurred_at: string;
  recorded_at: string;
  actor: { kind: 'person' | 'admin' | 'service' | 'operator'; id: string };
  action: string;
  outcome: 'succeeded' | 'failed' | 'denied' | 'changed' | 'blocked';
  target: { kind: string; ref: string } | null;
  device_id: string | null;
  details: Record<string, unknown>;
};

export type AuditLink = { seq: number; event: AuditEvent };

export type ChainReport = {
  ok: boolean;
  checked: number;
  head: string;
  broken_at: number | null;
  reason: 'invalid' | 'prev_hash' | 'seq' | 'hash' | 'head' | null;
};
