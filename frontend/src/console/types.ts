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

/** A company brain space, as the admin routes give it. */
export type Space = {
  id: string;
  name: string;
  kind: 'company' | 'group';
  reader_groups: string[];
  owner_groups: string[];
  owner_principals: string[];
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

export type SpaceList = { spaces: Space[]; max_upload_bytes: number };

export type BrainDocument = {
  id: string;
  space_id: string;
  title: string;
  file_name: string;
  media_type: string;
  byte_count: number;
  sha256: string;
  status: 'queued' | 'processing' | 'ready' | 'failed';
  /** Why it failed, or what it waits for while queued, in plain words. */
  status_reason: string | null;
  version: number;
  uploaded_by: string;
  uploaded_at: string;
  processed_at: string | null;
  chunk_count: number;
  graph_status: 'pending' | 'extracting' | 'done' | 'waiting' | 'failed';
  graph_reason: string | null;
};

export type Fact = {
  id: string;
  space_id: string;
  text: string;
  source_document_id: string | null;
  written_by: string;
  created_at: string;
  updated_at: string;
  retired_at: string | null;
};

export type EntityType =
  | 'person'
  | 'team'
  | 'organisation'
  | 'customer'
  | 'project'
  | 'product'
  | 'system'
  | 'policy'
  | 'term'
  | 'place';

/** An entity as the graph lists it. */
export type GraphNode = {
  id: string;
  space_id: string;
  subject: string;
  description: string;
  entity_type: EntityType;
  aliases: string[];
  status: 'active' | 'retired';
  updated_at: string;
  relation_count: number;
  orphan: boolean;
};

export type Graph = {
  nodes: GraphNode[];
  total_entities: number;
  shown_entities: number;
  truncated: boolean;
};

export type Entity = {
  id: string;
  space_id: string;
  subject: string;
  entity_type: EntityType;
  description: string;
  aliases: string[];
  status: 'active' | 'retired' | 'merged_into';
  merged_into: string | null;
  edited_by: string | null;
  edited_at: string | null;
  created_at: string;
  updated_at: string;
};

export type EntityRelation = {
  id: string;
  relation_type: string;
  direction: 'out' | 'in';
  peer: {
    id: string;
    subject: string;
    entity_type: EntityType;
    status: string;
  };
  status: 'active' | 'retired';
  origin: 'extracted' | 'manual';
  updated_at: string;
};

type CitedDocument = { id: string; title: string; file_name: string };

export type Citation =
  | {
      type: 'document';
      space: { id: string; name: string };
      document: CitedDocument;
      chunk: number;
      excerpt: string;
    }
  | {
      type: 'fact';
      space: { id: string; name: string };
      fact: { id: string; text: string };
      document: CitedDocument | null;
    };

export type EntityDetail = {
  entity: Entity;
  space: { id: string; name: string; kind: Space['kind'] };
  relations: EntityRelation[];
  mentions: Citation[];
  mention_count: number;
};

export type ChainReport = {
  ok: boolean;
  checked: number;
  head: string;
  broken_at: number | null;
  reason: 'invalid' | 'prev_hash' | 'seq' | 'hash' | 'head' | null;
};
